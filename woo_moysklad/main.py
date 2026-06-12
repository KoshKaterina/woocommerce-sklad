# FastAPI приложение: вебхук (плейсхолдер), health, инициализация компонентов

import asyncio
import base64
import hashlib
import hmac
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from woo_moysklad.config import load_config
from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.core.field_mappers import is_manual_prepayment
from woo_moysklad.exceptions import OrderProcessingError
from woo_moysklad.insales.client import InSalesClient
from woo_moysklad.logger import get_logger
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.product_matcher import ProductMatcher
from woo_moysklad.core.field_resync import FieldResync
from woo_moysklad.core.reconciliation import Reconciliation
from woo_moysklad.core.source_adapter import InSalesSourceAdapter, WooSourceAdapter
from woo_moysklad.woocommerce.client import WooCommerceClient
# uCoz импортируется лениво в lifespan (пакет ucoz/ — WIP, может отсутствовать)

log = get_logger(__name__)

# Глобальные компоненты (инициализируются в lifespan)
config = None
order_processor = None
reconciliation = None
ucoz_poller = None  # тип UcozPoller | None; импорт ленивый (uCoz — WIP)

# Дедупликация вебхуков: {(order_id, action): timestamp}
_webhook_dedup: dict[tuple[str, str], float] = {}
DEDUP_TTL_SECONDS = 300  # 5 минут

# Последовательная обработка вебхуков
_webhook_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация всех компонентов при старте."""
    global config, order_processor, reconciliation, ucoz_poller

    config = load_config()
    ms_client = MoySkladClient(config)
    woo_client = WooCommerceClient(config)
    cp_handler = CounterpartyHandler(ms_client)
    pm = ProductMatcher(ms_client)
    order_processor = OrderProcessor(config, ms_client, cp_handler, pm)

    # Собираем адаптеры источников
    adapters = [WooSourceAdapter(woo_client, order_processor)]
    if config.INSALES_SHOP_URL and config.INSALES_API_KEY and config.INSALES_PASSWORD:
        insales_client = InSalesClient(config)
        adapters.append(InSalesSourceAdapter(insales_client, order_processor))
        log.info("InSales adapter включён", shop=config.INSALES_SHOP_URL)
        # Самопроверка доступа: гео-блок/креды видны сразу при старте,
        # а не как error-строки сверки (их часто нет в экспортах логов)
        ok, detail = insales_client.check_access()
        if ok:
            log.info("InSales API: доступ проверен", detail=detail)
        else:
            log.info("InSales API НЕДОСТУПЕН — заказы InSales не будут передаваться",
                     detail=detail)
    else:
        log.info("InSales adapter выключен — нет переменных INSALES_*")

    # Обратная синхронизация полей (TODO §4) — только если включена флагом
    field_resync = None
    if config.FIELD_RESYNC_ENABLED:
        field_resync = FieldResync(config, ms_client)
        log.info("Reverse-sync полей включён")
    else:
        log.info("Reverse-sync полей выключен (FIELD_RESYNC_ENABLED=false)")

    reconciliation = Reconciliation(config, ms_client, adapters, field_resync=field_resync)

    # Запуск периодической сверки (раз в 3 минуты)
    reconciliation.schedule()

    # uCoz поллер (опционально, собственный таймер). Импорт ленивый — пакет ucoz/
    # пока WIP и может отсутствовать в коммите; без UCOZ_POLL_URL не требуется.
    if config.UCOZ_POLL_URL:
        from woo_moysklad.ucoz.client import UcozClient
        from woo_moysklad.ucoz.poller import UcozPoller
        from woo_moysklad.ucoz.state import UcozState
        ucoz_poller = UcozPoller(
            client=UcozClient(config.UCOZ_POLL_URL),
            state=UcozState(config.UCOZ_STATE_PATH),
            order_processor=order_processor,
            interval_seconds=config.UCOZ_POLL_INTERVAL_SECONDS,
        )
        ucoz_poller.schedule()
        log.info("uCoz поллер включён", url=config.UCOZ_POLL_URL,
                 interval_seconds=config.UCOZ_POLL_INTERVAL_SECONDS)
    else:
        log.info("uCoz поллер выключен — нет UCOZ_POLL_URL")

    log.info("Приложение запущено", sources=[a.name for a in adapters])

    yield

    # Остановка при завершении
    reconciliation.stop()
    if ucoz_poller:
        ucoz_poller.stop()
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
    if topic not in ("order.created", "order.updated"):
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
            # COD ("при получении") и ручную предоплату ("На карту", "Банковский
            # перевод") не помечаем — деньги ещё не получены, ждём отметки менеджера
            payment_title = order_data.get("payment_method_title", "")
            if "при получении" in payment_title.lower() or is_manual_prepayment(payment_title):
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
