# FastAPI приложение: вебхук (плейсхолдер), health, инициализация компонентов

import asyncio
import base64
import hashlib
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from .config import load_config
from .counterparty_handler import CounterpartyHandler
from .exceptions import OrderProcessingError
from .logger import get_logger
from .ms_client import MoySkladClient
from .order_processor import OrderProcessor
from .product_matcher import ProductMatcher
from .reconciliation import Reconciliation
from .woo_client import WooCommerceClient

log = get_logger(__name__)

# Глобальные компоненты (инициализируются в lifespan)
config = None
order_processor = None
reconciliation = None

# Дедупликация вебхуков: {(order_id, action): timestamp}
_webhook_dedup: dict[tuple[str, str], float] = {}
DEDUP_TTL_SECONDS = 300  # 5 минут

# Последовательная обработка вебхуков
_webhook_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация всех компонентов при старте."""
    global config, order_processor, reconciliation

    config = load_config()
    ms_client = MoySkladClient(config)
    woo_client = WooCommerceClient(config)
    cp_handler = CounterpartyHandler(ms_client)
    pm = ProductMatcher(ms_client)
    order_processor = OrderProcessor(config, ms_client, cp_handler, pm)
    reconciliation = Reconciliation(config, woo_client, ms_client, order_processor)

    # Запуск периодической сверки (раз в 20 минут)
    reconciliation.schedule()
    log.info("Приложение запущено")

    yield

    # Остановка при завершении
    reconciliation.stop()
    log.info("Приложение остановлено")


app = FastAPI(title="WooCommerce → Мой Склад", version="2.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0"}


@app.post("/webhook/order")
async def webhook_order(
    request: Request,
    x_wc_webhook_signature: str | None = Header(None),
    x_wc_webhook_topic: str | None = Header(None),
):
    """Приём вебхуков WooCommerce (плейсхолдер — логика верификации готова)."""
    body = await request.body()

    # Отладка: гарантированный вывод в stdout
    import sys
    print(f"[WEBHOOK] topic={x_wc_webhook_topic}, body_len={len(body)}", flush=True)
    print(f"[WEBHOOK] body_preview={body[:500]}", flush=True, file=sys.stderr)

    # --- Верификация подписи ---
    if config and config.WC_WEBHOOK_SECRET and x_wc_webhook_signature:
        expected = base64.b64encode(
            hmac.new(
                config.WC_WEBHOOK_SECRET.encode("utf-8"),
                body,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        if not hmac.compare_digest(expected, x_wc_webhook_signature):
            log.warning("Невалидная подпись вебхука")
            return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    # --- Определение топика ---
    topic = x_wc_webhook_topic or ""
    print(f"[WEBHOOK] resolved topic={topic!r}", flush=True)
    if topic not in ("order.created", "order.updated"):
        print(f"[WEBHOOK] IGNORING unknown topic={topic!r}", flush=True)
        return {"status": "ignored", "topic": topic}

    # --- Парсинг тела ---
    import json
    try:
        order_data = json.loads(body)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    # --- Определение действия ---
    order_id = str(order_data.get("id", ""))
    if topic == "order.updated":
        if order_data.get("status") == "processing":
            # Оплату при получении (COD) и "На карту" не помечаем — деньги ещё не получены
            payment_title = order_data.get("payment_method_title", "").lower()
            if "при получении" in payment_title or "на карту" in payment_title:
                log.info("Игнорируем mark_paid для ручной оплаты",
                         order_id=order_id, payment_method=payment_title)
                return {"status": "ignored", "reason": "manual_payment_not_prepaid"}
            action = "mark_paid"
        else:
            # order.updated без статуса "processing" — игнорируем
            # (принцип "только создание": интеграция не обновляет заказы в МС)
            log.info("Игнорируем order.updated (не mark_paid)",
                     order_id=order_id, status=order_data.get("status"))
            return {"status": "ignored", "reason": "update_not_supported"}
    else:
        action = topic

    dedup_key = (order_id, action)
    now = time.monotonic()

    # Очистка устаревших записей
    expired = [k for k, t in _webhook_dedup.items() if now - t > DEDUP_TTL_SECONDS]
    for k in expired:
        del _webhook_dedup[k]

    if dedup_key in _webhook_dedup:
        log.info("Дубликат вебхука, игнорируем",
                 order_id=order_id, action=action,
                 seconds_ago=round(now - _webhook_dedup[dedup_key], 1))
        return {"status": "ignored", "reason": "duplicate"}

    _webhook_dedup[dedup_key] = now

    # --- Последовательная обработка ---
    print(f"[WEBHOOK] processing order_id={order_id}, action={action}", flush=True)
    async with _webhook_lock:
        try:
            if action == "mark_paid":
                results = await asyncio.to_thread(order_processor.mark_paid, order_data)
                ms_names = [r.get("name", "") for r in results]
                return {"status": "ok", "action": "mark_paid", "ms_orders": ms_names}

            results = await asyncio.to_thread(order_processor.process_order, order_data)
            ms_names = [r.get("name", "") for r in results]
            return {"status": "ok", "ms_orders": ms_names}
        except OrderProcessingError as e:
            log.critical("Вебхук: заказ не создан", error=str(e))
            # Убираем из дедупа при ошибке, чтобы можно было повторить
            _webhook_dedup.pop(dedup_key, None)
            return JSONResponse(status_code=500, content={"error": str(e)})
        except Exception as e:
            log.error("Вебхук: неожиданная ошибка", error=str(e))
            _webhook_dedup.pop(dedup_key, None)
            return JSONResponse(status_code=500, content={"error": "Internal error"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("woo_moysklad.main:app", host="0.0.0.0", port=8000, reload=True)
