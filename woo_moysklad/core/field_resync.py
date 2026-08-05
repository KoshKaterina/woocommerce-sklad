"""Обратная синхронизация заказа покупателя (TODO §4).

Когда менеджер вручную правит заказ в МС (позиции и/или «Способ оплаты»),
приводим заказ в порядок по тем же правилам, что и при создании:
  - цена услуги доставки СДЭК → 0 при оплате «на карту»/«банковский перевод»

⚠️ 05.08.2026 отсюда убран пересчёт доп. полей «Оценочная стоимость»,
«Стоимость доставки», «Итого к оплате получателем», «Прием платежа» и «Вид
доставки»: поля заводились под фулфилмент (ЭРМС, май-июнь 2026), дублировали
суммы заказа и никем не читались. Решение Кати 05.08.2026, поля удалены из МС.

Работает по ВСЕМ заказам МС, изменённым в окне, КРОМЕ канала «Маркетплейс»
(в т.ч. по созданным менеджером вручную — у них нет «Номера заказа на сайте»).

Безопасность:
  - пишем ТОЛЬКО изменившееся (идемпотентно) → нет правок → нет записи →
    `updated` не дёргается → нет зацикливания;
  - НЕ трогаем: контрагента, статус, адрес, «Доставка (СД)», товары/услуги
    (кроме обнуления цены услуги доставки СДЭК для карты/перевода).
"""
from datetime import timedelta, timezone

from woo_moysklad.logger import get_logger

_MSK = timezone(timedelta(hours=3))  # МойСклад интерпретирует даты фильтров как МСК

log = get_logger(__name__)


def categorize_payment(payment_str: str | None) -> str | None:
    """Категория оплаты из строки «Способ оплаты» (case-insensitive, с допуском опечаток).

    'cod'            — при получении / наложенный
    'manual_prepaid' — «на карту» / «банковский перевод» / крипта / wallet / «нет»:
                       предоплата + обнуление СДЭК (доставка бесплатная)
    'prepaid'        — всё остальное (Эвотор, наличные, онлайн, картой, счёт и т.д.):
                       предоплата БЕЗ обнуления доставки
    None             — пустое значение / None
    """
    if not payment_str:
        return None
    s = payment_str.lower()
    if "получ" in s or "налож" in s:
        return "cod"
    if ("на карт" in s or "перевод" in s or "банк" in s
            or "wallet" in s or "нет" in s or "крипт" in s):
        return "manual_prepaid"
    return "prepaid"


def _is_cdek_service(name: str) -> bool:
    n = (name or "").lower()
    return "сдэк" in n or "cdek" in n


def compute_desired(positions: list, category: str | None, cfg=None) -> dict:
    """Что нужно поправить в заказе, исходя из позиций и категории оплаты.

    positions: [{id, type ('service'/прочее=товар), name, price(коп), quantity}]
    Возвращает dict: zero_position_ids — СДЭК-услуги, цену которых надо обнулить
    (оплата «на карту» / банковским переводом = доставка за наш счёт).
    """
    is_manual_prepaid = category == "manual_prepaid"

    zero_ids = []
    for p in positions:
        if p["type"] != "service":
            continue
        if is_manual_prepaid and _is_cdek_service(p["name"]) and p["price"] != 0:
            zero_ids.append(p["id"])

    return {"zero_position_ids": zero_ids}


class FieldResync:
    """Пересчёт зависимых доп. полей заказов МС, изменённых менеджером."""

    def __init__(self, config, ms_client):
        self.config = config
        self.ms = ms_client

    # ── чтение ──────────────────────────────────────────────
    def _fetch_orders(self, window_start, window_end) -> list:
        start = window_start.astimezone(_MSK).strftime("%Y-%m-%d %H:%M:%S")
        end = window_end.astimezone(_MSK).strftime("%Y-%m-%d %H:%M:%S")
        orders, offset = [], 0
        while True:
            resp = self.ms.get("entity/customerorder", params={
                "filter": f"updated>={start};updated<={end}",
                "limit": 100, "offset": offset,
            })
            rows = resp.get("rows", [])
            orders.extend(rows)
            if len(rows) < 100:
                break
            offset += 100
        return orders

    def _fetch_positions(self, order_id: str) -> list:
        resp = self.ms.get(f"entity/customerorder/{order_id}/positions",
                            params={"expand": "assortment", "limit": 1000})
        out = []
        for p in resp.get("rows", []):
            assortment = p.get("assortment") or {}
            a_type = assortment.get("meta", {}).get("type", "")
            out.append({
                "id": p["id"],
                "type": "service" if a_type == "service" else "goods",
                "name": assortment.get("name", ""),
                "price": p.get("price", 0),
                "quantity": p.get("quantity", 1),
                "discount": p.get("discount", 0),
            })
        return out

    def _is_marketplace(self, order: dict) -> bool:
        mp_id = self.config.MS_SALES_CHANNEL_MARKETPLACE_ID
        if not mp_id:
            return False
        href = (order.get("salesChannel") or {}).get("meta", {}).get("href", "")
        return href.rstrip("/").split("/")[-1] == mp_id

    def _attr_value(self, order: dict, attr_id: str):
        for a in order.get("attributes", []):
            if a.get("id") == attr_id:
                return a.get("value")
        return None  # поле отсутствует

    # ── один заказ ──────────────────────────────────────────
    def resync_order(self, order: dict, *, dry_run: bool = False) -> dict | None:
        """Пересчитать поля одного заказа. Возвращает план изменений или None, если изменений нет."""
        cfg = self.config
        order_id = order["id"]
        name = order.get("name", order_id)

        if self._is_marketplace(order):
            return None

        payment_str = self._attr_value(order, cfg.MS_ATTR_PAYMENT_METHOD_ID)
        category = categorize_payment(payment_str if isinstance(payment_str, str) else None)

        positions = self._fetch_positions(order_id)
        desired = compute_desired(positions, category, cfg)

        plan = {}
        zero_ids = desired["zero_position_ids"]
        if zero_ids:
            plan["Обнулить СДЭК-доставку"] = {"positions": zero_ids}

        if not zero_ids:
            return None

        if dry_run:
            return {"order": name, "id": order_id, "plan": plan}

        for pos_id in zero_ids:
            self.ms.put(f"entity/customerorder/{order_id}/positions/{pos_id}", {"price": 0})
        log.info("Resync: заказ поправлен", order=name, fields=list(plan.keys()))
        return {"order": name, "id": order_id, "plan": plan}

    # ── проход по окну ──────────────────────────────────────
    def run(self, window_start, window_end) -> None:
        try:
            orders = self._fetch_orders(window_start, window_end)
        except Exception as e:
            log.error("Resync: ошибка получения заказов", error=str(e))
            return
        changed = 0
        for order in orders:
            try:
                if self.resync_order(order):
                    changed += 1
            except Exception as e:
                log.error("Resync: ошибка обработки заказа",
                          order=order.get("name"), error=str(e))
        log.info("Resync: проход завершён", всего=len(orders), изменено=changed)
