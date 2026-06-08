"""Обратная синхронизация доп. полей заказа покупателя (TODO §4).

Когда менеджер вручную правит заказ в МС (позиции и/или «Способ оплаты»),
пересчитываем зависимые доп. поля по тем же правилам, что и при создании:
  - Оценочная стоимость   = Σ товарных позиций (целые рубли)
  - Стоимость доставки     = Σ позиций-услуг (целые рубли)
  - Итого к оплате получателем = Σ всех позиций при COD, иначе 0
  - Прием платежа (1/2/3)  = из «Способ оплаты» (строка)
  - цена услуги доставки СДЭК → 0 при оплате «на карту»/«банковский перевод»

Работает по ВСЕМ заказам МС, изменённым в окне, КРОМЕ канала «Маркетплейс»
(в т.ч. по созданным менеджером вручную — у них нет «Номера заказа на сайте»).

Безопасность:
  - пишем ТОЛЬКО изменившиеся поля (идемпотентно) → нет правок → нет записи →
    `updated` не дёргается → нет зацикливания;
  - НЕ трогаем: контрагента, статус, адрес, вид доставки/СД, товары/услуги
    (кроме обнуления цены услуги доставки СДЭК для карты/перевода);
  - если изменён только контрагент — пересчёт даёт те же значения → записи нет.
"""
from woo_moysklad.core.field_mappers import build_attribute
from woo_moysklad.core.packaging import PACKAGING_SERVICE_NAMES, compute_packaging
from woo_moysklad.core.product_matcher import ProductMatcher
from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def categorize_payment(payment_str: str | None) -> str | None:
    """Категория оплаты из строки «Способ оплаты» (case-insensitive, с допуском опечаток).

    'cod'            — при получении / наложенный (НЕ «налич»)
    'manual_prepaid' — «на карту» / «банковский перевод»: предоплата + обнуление СДЭК
                       (совпадает с WC is_manual_prepayment)
    'prepaid'        — «онлайн» / «картой» (InSales): предоплата БЕЗ обнуления
    None             — не распознано (поля оплаты не трогаем)

    Важно: «на карт(у)» ≠ «картой». InSales шлёт «Оплата онлайн»/«Оплата картой»
    (онлайн-предоплата, доставку НЕ обнуляем); промо «бесплатная СДЭК» — только для
    WC-метода «На карту» и «Банковский перевод».
    """
    if not payment_str:
        return None
    s = payment_str.lower()
    if "получ" in s or "налож" in s:
        return "cod"
    if "на карт" in s or "перевод" in s or "банк" in s:
        return "manual_prepaid"
    if "онлайн" in s or "картой" in s:
        return "prepaid"
    return None


def _is_cdek_service(name: str) -> bool:
    n = (name or "").lower()
    return "сдэк" in n or "cdek" in n


def compute_desired(positions: list, category: str | None, cfg) -> dict:
    """Посчитать желаемые значения зависимых полей из позиций и категории оплаты.

    positions: [{id, type ('service'/прочее=товар), name, price(коп), quantity}]
    Возвращает dict: estimated, delivery, total_to_pay (None=не трогать),
    payment_element (None=не трогать), zero_position_ids (СДЭК-услуги к обнулению).
    """
    is_manual_prepaid = category == "manual_prepaid"

    goods_kop = 0
    services_kop = 0
    zero_ids = []

    for p in positions:
        line = p["price"] * p["quantity"]
        if p["type"] == "service":
            if is_manual_prepaid and _is_cdek_service(p["name"]) and p["price"] != 0:
                zero_ids.append(p["id"])  # обнуляем СДЭК-доставку, в сумму не идёт
            else:
                services_kop += line
        else:
            goods_kop += line

    all_kop = goods_kop + services_kop
    desired = {
        "estimated": goods_kop // 100,
        "delivery": services_kop // 100,
        "zero_position_ids": zero_ids,
        "total_to_pay": None,
        "payment_element": None,
    }
    if category == "cod":
        desired["total_to_pay"] = all_kop // 100
        desired["payment_element"] = cfg.MS_PAYMENT_TYPE_NONCASH_ID
    elif category in ("manual_prepaid", "prepaid"):
        desired["total_to_pay"] = 0
        desired["payment_element"] = cfg.MS_PAYMENT_TYPE_PREPAID_ID
    return desired


class FieldResync:
    """Пересчёт зависимых доп. полей заказов МС, изменённых менеджером."""

    def __init__(self, config, ms_client):
        self.config = config
        self.ms = ms_client
        self.pm = ProductMatcher(ms_client)  # для find_or_create_service (упаковка)

    # ── чтение ──────────────────────────────────────────────
    def _fetch_orders(self, window_start, window_end) -> list:
        start = window_start.strftime("%Y-%m-%d %H:%M:%S")
        end = window_end.strftime("%Y-%m-%d %H:%M:%S")
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
            try:
                volume = float(assortment.get("volume") or 0)
            except (TypeError, ValueError):
                volume = 0.0
            out.append({
                "id": p["id"],
                "type": "service" if a_type == "service" else "goods",
                "name": assortment.get("name", ""),
                "price": p.get("price", 0),
                "quantity": p.get("quantity", 1),
                "volume": volume,
            })
        return out

    def _channel_id(self, order: dict) -> str:
        href = (order.get("salesChannel") or {}).get("meta", {}).get("href", "")
        return href.rstrip("/").split("/")[-1]

    def _is_marketplace(self, order: dict) -> bool:
        mp_id = self.config.MS_SALES_CHANNEL_MARKETPLACE_ID
        if not mp_id:
            return False
        return self._channel_id(order) == mp_id

    def _is_tangemshop(self, order: dict) -> bool:
        ts_id = getattr(self.config, "MS_SALES_CHANNEL_INSALES_ID", "")
        if not ts_id:
            return False
        return self._channel_id(order) == ts_id

    def _attr_value(self, order: dict, attr_id: str):
        for a in order.get("attributes", []):
            if a.get("id") == attr_id:
                return a.get("value")
        return None  # поле отсутствует

    def _compute_packaging_changes(self, order: dict, positions: list) -> tuple[dict, list, list]:
        """Сверить упаковку заказа с желаемой по объёму товаров.

        Возвращает (plan, delete_ids, add_specs):
          plan       — {имя: {old, new}} для отчёта/dry-run,
          delete_ids — id позиций-упаковки на удаление,
          add_specs  — [(имя_услуги, количество)] на добавление.
        Tangemshop пропускаем (упаковку не ведём).
        """
        if self._is_tangemshop(order):
            return {}, [], []

        goods = [(p["volume"], p["quantity"]) for p in positions if p["type"] == "goods"]
        desired = dict(compute_packaging(goods))  # имя → количество

        current: dict[str, list] = {}  # имя → [(id, quantity), ...]
        for p in positions:
            if p["type"] == "service" and p["name"] in PACKAGING_SERVICE_NAMES:
                current.setdefault(p["name"], []).append((p["id"], p["quantity"]))

        plan, delete_ids, add_specs = {}, [], []
        for name in set(desired) | set(current):
            want = desired.get(name, 0)
            cur_list = current.get(name, [])
            cur_total = sum(q for _, q in cur_list)
            # уже корректно (ровно одна позиция нужного количества) → пропускаем
            if want == cur_total and len(cur_list) <= 1:
                continue
            delete_ids.extend(pid for pid, _ in cur_list)
            if want > 0:
                add_specs.append((name, want))
            plan[name] = {"old": cur_total, "new": want}
        return plan, delete_ids, add_specs

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
        if payment_str and category is None:
            log.warning("Resync: не распознан способ оплаты — поля оплаты не трогаем",
                        order=name, payment=payment_str)

        positions = self._fetch_positions(order_id)
        desired = compute_desired(positions, category, cfg)

        # --- сборка диффа атрибутов ---
        attr_patch = []
        plan = {}

        def num_attr(attr_id, value, key):
            cur = self._attr_value(order, attr_id)
            if cur is None or float(cur) != float(value):
                a = build_attribute(attr_id, value)
                if a:
                    attr_patch.append(a)
                    plan[key] = {"old": cur, "new": value}

        num_attr(cfg.MS_ATTR_ESTIMATED_COST_ID, desired["estimated"], "Оценочная стоимость")
        num_attr(cfg.MS_ATTR_DELIVERY_COST_ID, desired["delivery"], "Стоимость доставки")
        if desired["total_to_pay"] is not None:
            num_attr(cfg.MS_ATTR_TOTAL_TO_PAY_ID, desired["total_to_pay"], "Итого к оплате")

        # Прием платежа (customentity)
        if desired["payment_element"]:
            cur = self._attr_value(order, cfg.MS_ATTR_PAYMENT_TYPE_ID)
            cur_elem = (cur.get("meta", {}).get("href", "").rstrip("/").split("/")[-1]
                        if isinstance(cur, dict) else None)
            if cur_elem != desired["payment_element"]:
                a = build_attribute(
                    cfg.MS_ATTR_PAYMENT_TYPE_ID, "x", is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID,
                    element_id=desired["payment_element"],
                )
                if a:
                    attr_patch.append(a)
                    plan["Прием платежа"] = {"old": cur_elem, "new": desired["payment_element"]}

        zero_ids = desired["zero_position_ids"]
        if zero_ids:
            plan["Обнулить СДЭК-доставку"] = {"positions": zero_ids}

        # --- упаковка: подстройка под текущие товары ---
        pkg_plan, pkg_deletes, pkg_adds = self._compute_packaging_changes(order, positions)
        if pkg_plan:
            plan["Упаковка"] = pkg_plan

        if not attr_patch and not zero_ids and not pkg_deletes and not pkg_adds:
            return None

        if dry_run:
            return {"order": name, "id": order_id, "plan": plan}

        # --- запись: позиции (цена/упаковка), затем атрибуты ---
        for pos_id in zero_ids:
            self.ms.put(f"entity/customerorder/{order_id}/positions/{pos_id}", {"price": 0})
        for pos_id in pkg_deletes:
            self.ms.delete(f"entity/customerorder/{order_id}/positions/{pos_id}")
        add_body = []
        for svc_name, count in pkg_adds:
            meta = self.pm.find_or_create_service(svc_name)
            if meta:
                add_body.append({"quantity": count, "price": 0, "discount": 0,
                                 "vat": 0, "assortment": meta})
        if add_body:
            self.ms.post(f"entity/customerorder/{order_id}/positions", add_body)
        if attr_patch:
            self.ms.put(f"entity/customerorder/{order_id}", {"attributes": attr_patch})
        log.info("Resync: поля пересчитаны", order=name, fields=list(plan.keys()))
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
