# Нормализатор uCoz: ответ uamo.php → NormalizedOrder

import json

from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)

log = get_logger(__name__)

# Постоянный суффикс номера заказа uCoz: предохраняет от коллизии с WC
# (оба источника используют числовые id) и группирует TG-shop заказы в МС.
_UCOZ_ORDER_SUFFIX = " TGShop"

# COD-наценка для uCoz: API uamo.php не передаёт поле margin (в отличие от
# InSales) — значит, рассчитываем сами от стоимости товаров. 4% покрывают
# риск невыкупа и комиссию транспортной компании за наложенный платёж.
# В МС уходит отдельной услугой "Наценка за наложенный платеж" (то же имя,
# что в WC/InSales — тот же сервис в МС, без дубликатов).
_UCOZ_COD_MARGIN_RATE = 0.04

# goods_id uCoz → артикул МС (article).
# Источник — _ucoz_ms_match.csv (242 позиции, 222 точных + 20 fuzzy после
# ручной проверки). Исключены: Coinkite целиком (Coldcard MK3/MK4/MK5/Q все
# цвета, SATSCARD, TAPSIGNER, HARDCASE, кабели, адаптер питания, кости энтропии,
# Security Bag Kit), SecuX (v20/w20/w10/Nifty), Satochip (Satodime), Yubikey #165
# (Security Key без уточнения, цена 4750 не совпадает ни с USB-A, ни с USB-C).
# При появлении новых товаров в uCoz — обновлять словарь.
_UCOZ_GOODS_ID_TO_MS_SKU: dict[int, str] = {
    7: "HW-26",
    8: "SEED-12",
    73: "HW-83",
    76: "HW-82",
    77: "HW-84",          # fuzzy 0.96
    78: "HW-85",
    79: "HW-78",
    80: "HW-79",
    81: "HW-80",
    82: "HW-81",
    83: "HW-77",
    84: "HW-75",
    85: "HW-76",
    86: "HW-34",
    87: "HW-35",
    88: "HW-46",
    89: "HW-37",
    90: "HW-51",
    91: "HW-50",
    92: "HW-44",
    93: "HW-43",
    94: "HW-52",
    95: "HW-45",
    96: "HW-47",          # fuzzy 0.86
    97: "HW-38",
    98: "HW-31",
    99: "HW-32",
    100: "HW-33",
    101: "HW-53",
    102: "HW-21",
    103: "HW-22",
    104: "HW-02",
    105: "HW-01",
    106: "TG128X2-B",
    107: "TG130X2-B",
    108: "TG128X3-B",
    109: "TG130X3-B",
    110: "HW-28",
    111: "HW-27",
    112: "HW-62",
    113: "HW-60",
    114: "HW-61",
    115: "HW-105",
    116: "HW-104",
    117: "HW-17",
    129: "HW-18",
    130: "HW-19",
    131: "HW-23",
    132: "HW-24",
    133: "HW-03",
    134: "HW-06",
    135: "HW-04",
    136: "HW-05",
    141: "HW-59",
    142: "HW-56",
    143: "HW-55",
    144: "HW-57",
    145: "HW-58",
    146: "SEED-17",
    147: "SEED-20",
    148: "SEED-16",
    149: "SEED-15",
    150: "SEED-19",
    151: "SEED-21",
    152: "SEED-25",
    154: "SEED-03",
    155: "SEED-02",
    156: "SEED-01",
    157: "SEED-08",
    158: "SEED-07",
    159: "SEED-05",
    160: "SEED-06",
    161: "SEED-04",
    162: "SEED-11",
    163: "SEED-14",
    164: "SEED-13",
    166: "U2F-07",
    167: "U2F-08",
    168: "U2F-05",
    169: "U2F-09",
    170: "U2F-04",
    171: "U2F-01",
    172: "U2F-03",
    173: "U2F-06",
    174: "U2F-02",
    175: "U2F-11",
    176: "U2F-12",
    177: "HW-74",
    179: "CASE-05",
    180: "CASE-09",
    181: "CASE-06",
    182: "CASE-02",
    183: "CASE-01",
    184: "CASE-08",
    185: "CASE-07",
    187: "CABLE-03",
    188: "CABLE-02",
    189: "CABLE-08",
    190: "CABLE-07",
    191: "CABLE-01",
    194: "CABLE-06",
    195: "CABLE-04",
    196: "CABLE-05",
    197: "ACC-03",
    198: "ACC-01",
    199: "ACC-02",
    200: "ACC-06",
    202: "ACC-09",
    203: "ACC-05",
    205: "ACC-10",
    206: "ACC-11",
    207: "ACC-07",
    209: "HW-92",
    210: "HW-91",
    211: "HW-90",
    213: "HW-93",
    214: "HW-95",
    215: "HW-88",
    216: "HW-87",
    217: "HW-86",
    218: "HW-89",
    219: "HW-94",
    220: "TG-RING",
    221: "SEED-24",
    222: "AI-01",
    225: "SERT-3",
    226: "SERT-2",
    228: "SERT-1",
    229: "AI-03",
    230: "AI-02",
    231: "AI-04",
    232: "AI-05",
    233: "AI-06",
    234: "CASE-14",       # fuzzy 0.94
    235: "CASE-15",       # fuzzy 0.94
    236: "CASE-16",       # fuzzy 0.94
    237: "CASE-17",
    238: "CASE-18",
    239: "CASE-19",
    240: "CASE-20",
    241: "U2F-13",
    242: "CASE-22",       # fuzzy 0.94
    243: "CASE-23",       # fuzzy 0.94
    244: "CASE-24",
    245: "CASE-25",
    246: "CASE-26",       # fuzzy 0.94
    247: "HW-97",
    248: "HW-96",
    256: "TG-KASPA3",
    257: "TG-TRENDY",
    258: "TG-VIVID",      # fuzzy 0.96
    259: "TG-STEALTH",
    261: "HW-70",
    262: "HW-71",
    263: "HW-72",
    264: "TG-115",
    265: "TG-114",
    266: "TG-111",
    267: "TG-110",
    268: "TG-109",
    269: "TG-113",
    270: "TG-TRON",
    273: "TG-SKY",        # fuzzy 0.97
    274: "TG-SEA",
    275: "TG-HYPERBLUE",
    276: "HW-113",
    277: "HW-112",
    278: "HW-116",
    279: "HW-117",
    280: "HW-114",
    281: "HW-115",
    282: "SEED-26",
    283: "SEED-27",
    284: "HW-120",
    289: "HW-119",
    290: "HW-118",
    291: "HW-123",
    292: "HW-124",
    293: "HW-121",
    294: "HW-122",
    295: "HW-125",
    296: "TG-BLOOM",
    297: "TG-SAKURA2",
    298: "TG-SUN",
    299: "TG-HODL",
    300: "SEED-28",
    301: "ACC-17",
    302: "CASE-33",
    303: "CASE-34",
    304: "CASE-35",
    305: "CASE-36",
    306: "CASE-37",
    307: "CASE-38",
    308: "CASE-39",
    309: "CASE-40",
    310: "CASE-41",
    311: "CASE-42",
    312: "ACC-18",
    313: "ACC-19",
    314: "ACC-20",
    315: "ACC-21",
    316: "ACC-22",
    1105: "HW-231",
    1115: "TG-SAKURA-SL",
}

# payment_id uCoz → (название способа оплаты, ключ типа платежа).
# payment_title идёт в MS_ATTR_PAYMENT_METHOD_ID (строка),
# type_key — в справочник "Приём платежа" (prepaid/noncash).
# TODO: дополнить остальные id после получения справочника от пользователя.
_UCOZ_PAYMENT_ID_TO_INFO: dict[int, tuple[str, str | None]] = {
    1: ("При получении", "noncash"),  # подтверждено заказом #392 (COD, inv_params=None)
    3: ("Яндекс Касса", "prepaid"),   # yandexkassa
    6: ("Wallet", "prepaid"),          # крипта (TODO Q7)
    7: ("На карту", "prepaid"),        # подтверждено заказом #393 (по аналогии с WC:
                                       # перевод на карту до отправки — это предоплата,
                                       # не наложенный платёж, COD-наценка не добавляется)
}

# delivery_id uCoz → (имя услуги в МС, sd_key, type_key, фикс. цена в копейках или None).
# Имя услуги должно совпадать с уже существующей в МС, иначе ProductMatcher
# создаст дубликат. Сейчас в МС используются названия в стиле "CDEK: Самовывоз"
# и "Курьерская доставка по Москве" (тот же формат, что в WC-услугах).
#
# Фикс. цена нужна, если uCoz передаёт `delivery_tax=0`, но магазин на самом деле
# берёт фиксированную сумму (например, курьер по Москве — всегда 1000 ₽). Если
# None — используется реальный `delivery_tax` из ответа uCoz.
#
# TODO: дополнить остальные id после получения справочника от пользователя.
_UCOZ_DELIVERY_ID_TO_INFO: dict[int, tuple[str, str | None, str | None, int | None]] = {
    1: ("Самовывоз из офиса Sunscrypt", None, "pvz", None),                  # заказ #394 (delivery_data={}, delivery_tax=0; sd_key=None по аналогии с InSales)
    3: ("CDEK: Самовывоз", "cdek", "pvz", None),                            # заказ #392 (delivery_pi из delivery_data)
    4: ("Курьерская доставка по Москве", "yandex", "courier", 100_000),     # заказ #393 (uCoz всегда даёт 0; в WC = 1000 ₽)
}


def parse_ucoz_delivery_data(raw: str | None) -> dict:
    """Безопасно распарсить delivery_data (JSON-строку).

    СДЭК ПВЗ передаёт: delivery_pn (адрес ПВЗ), delivery_pi (код ПВЗ),
    delivery_ci (id города), delivery_cn (название города). Для других типов
    формат пока неизвестен.
    """
    if not raw or raw == "{}":
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError) as e:
        log.warning("uCoz: невалидный delivery_data", raw=raw[:200], error=str(e))
        return {}


def _safe(name: str, func):
    try:
        return func()
    except Exception as e:
        log.warning("uCoz normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def _to_float(value, default: float = 0.0) -> float:
    """Привести строку/число к float. uCoz передаёт суммы как строки '8990.00'."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (ValueError, TypeError):
        return default


def map_ucoz_payment(payment_id: int) -> tuple[str, str | None]:
    """payment_id → (payment_title, payment_type_key)."""
    if payment_id in _UCOZ_PAYMENT_ID_TO_INFO:
        return _UCOZ_PAYMENT_ID_TO_INFO[payment_id]
    log.warning("uCoz: неизвестный payment_id, способ оплаты не маппится",
                payment_id=payment_id)
    return (f"uCoz payment_id={payment_id}", None)


def map_ucoz_delivery(
    delivery_id: int,
) -> tuple[str, str | None, str | None, int | None]:
    """delivery_id → (service_name, delivery_sd_key, delivery_type_key, fixed_price_cents)."""
    if delivery_id in _UCOZ_DELIVERY_ID_TO_INFO:
        return _UCOZ_DELIVERY_ID_TO_INFO[delivery_id]
    log.warning("uCoz: неизвестный delivery_id, атрибуты СД/Вид доставки не маппятся",
                delivery_id=delivery_id)
    return ("Доставка", None, None, None)


def build_ucoz_customer(order: dict) -> NormalizedCustomer:
    """Контрагент uCoz.

    TODO Q6: tg-username (поле user, без @) идёт в email контрагента в формате
    @{user}. id_tg игнорируем. field_email используем как fallback, если user пуст.
    """
    full_name = (order.get("field_full_name") or "").strip() or "Без имени"
    phone = order.get("field_phone") or ""
    user = (order.get("user") or "").strip()
    field_email = (order.get("field_email") or "").strip()
    email = f"@{user}" if user else field_email
    return NormalizedCustomer(full_name=full_name, phone=phone, email=email)


def build_ucoz_line_items(goods: list) -> list[NormalizedLineItem]:
    """Разрешить goods_id → SKU МС. Неизвестные позиции → log.error и пропуск."""
    items = []
    for g in goods:
        goods_id = g.get("goods_id")
        if not isinstance(goods_id, int):
            log.error("uCoz: позиция без goods_id, пропускаем", item=g)
            continue
        sku = _UCOZ_GOODS_ID_TO_MS_SKU.get(goods_id)
        if not sku:
            log.error("uCoz: неизвестный goods_id, пропускаем",
                      goods_id=goods_id, name=g.get("name"))
            continue
        try:
            price_cents = int(round(_to_float(g.get("price_raw")) * 100))
        except (TypeError, ValueError):
            log.error("uCoz: невалидная цена, пропускаем",
                      goods_id=goods_id, price_raw=g.get("price_raw"))
            continue
        try:
            quantity = int(g.get("count") or 1)
        except (TypeError, ValueError):
            quantity = 1
        items.append(NormalizedLineItem(
            sku=sku,
            title=g.get("name", ""),
            price_cents=price_cents,
            quantity=quantity,
        ))
    return items


def is_ucoz_paid(inv_params: str) -> bool:
    """Признак оплаты: inv_params начинается с 'payment_accepted'."""
    return (inv_params or "").startswith("payment_accepted")


def normalize_ucoz_order(raw_data: dict, config) -> NormalizedOrder:
    """Преобразовать сырой ответ uamo.php в NormalizedOrder.

    Структура raw_data: {"order": {...}, "goods": [{...}, ...]}.
    """
    order = raw_data.get("order") or {}
    goods = raw_data.get("goods") or []

    customer = build_ucoz_customer(order)
    line_items = build_ucoz_line_items(goods)

    # Платёж
    payment_id = order.get("payment_id")
    if isinstance(payment_id, int):
        payment_title, payment_type_key = map_ucoz_payment(payment_id)
    else:
        log.warning("uCoz: payment_id отсутствует или не int", payment_id=payment_id)
        payment_title, payment_type_key = ("", None)

    # Доставка: имя услуги, sd, type, фикс. цена (если переопределена для типа)
    delivery_id = order.get("delivery_id")
    if isinstance(delivery_id, int):
        (delivery_service_name, delivery_sd_key, delivery_type_key,
         fixed_price_cents) = map_ucoz_delivery(delivery_id)
    else:
        log.warning("uCoz: delivery_id отсутствует или не int", delivery_id=delivery_id)
        delivery_service_name = "Доставка"
        delivery_sd_key = delivery_type_key = fixed_price_cents = None

    # Цена доставки: фикс. для типа (если задана) или реальный delivery_tax из uCoz.
    if fixed_price_cents is not None:
        delivery_price_cents = fixed_price_cents
        delivery_price = fixed_price_cents / 100
    else:
        delivery_price = _to_float(order.get("delivery_tax"))
        delivery_price_cents = int(round(delivery_price * 100))

    # Услуга доставки идёт в позиции заказа МС в двух случаях:
    #   1) ненулевая стоимость (курьер, СДЭК с реальным тарифом)
    #   2) самовывоз — даже с ценой 0, чтобы менеджер видел сам факт самовывоза
    #      в позициях заказа (по аналогии с InSales-нормалайзером).
    is_pickup = "самовывоз" in delivery_service_name.lower()
    delivery_services: list[NormalizedDeliveryService] = []
    if delivery_price_cents > 0 or is_pickup:
        delivery_services.append(NormalizedDeliveryService(
            name=delivery_service_name,
            price_cents=delivery_price_cents,
        ))

    # delivery_data: JSON-строка с деталями доставки (зависит от типа).
    delivery_extra = parse_ucoz_delivery_data(order.get("delivery_data"))

    # Код ПВЗ — из delivery_pi (СДЭК ПВЗ). Для других типов поле может отсутствовать.
    pvz_code = (delivery_extra.get("delivery_pi") or "").strip() or None

    # Адрес: для ПВЗ берём delivery_pn (адрес пункта выдачи), для курьера —
    # field_delivery_address. Что заполнено — то и используем.
    shipment_address = (
        (order.get("field_delivery_address") or "").strip()
        or (delivery_extra.get("delivery_pn") or "").strip()
        or None
    )

    # Стоимости для атрибутов МС (целые рубли, формат как в WC/InSales).
    items_total = sum(
        _to_float(g.get("price_raw")) * int(g.get("count") or 1) for g in goods
    )
    estimated_cost = str(int(round(items_total))) if items_total > 0 else None
    delivery_cost_attr = (
        str(int(round(delivery_price))) if delivery_price > 0 else None
    )

    # Статус оплаты и COD
    is_paid = is_ucoz_paid(order.get("inv_params"))
    is_cod = payment_type_key == "noncash"

    # COD-наценка 4% от стоимости товаров: рассчитываем сами, в API uCoz
    # такого поля нет. Считаем в целых рублях (как в InSales: margin_amount
    # обычно целое), затем умножаем на 100 для копеек.
    cod_margin_rub = (
        int(round(items_total * _UCOZ_COD_MARGIN_RATE)) if is_cod else 0
    )
    cod_margin_cents = cod_margin_rub * 100

    # Сумма к оплате получателю при COD — товары + доставка + наценка.
    total_to_pay = (
        str(int(round(items_total + delivery_price)) + cod_margin_rub)
        if is_cod else None
    )

    # Комментарий: основной комментарий покупателя + (если есть) форматированная
    # справка СДЭК (адрес, код, режим работы) для удобства менеджера.
    parts = []
    base_comment = (order.get("field_order_comment") or "").strip()
    if base_comment:
        parts.append(base_comment)
    cdek_info = (order.get("field_cdek_info") or "").strip()
    if cdek_info:
        parts.append(cdek_info)
    comment = "\n\n".join(parts) or None

    order_id = str(order.get("id", ""))
    order_number = order_id  # uCoz: id и порядковый номер совпадают

    return NormalizedOrder(
        source="ucoz",
        order_id=order_id,
        order_number=f"{order_number}{_UCOZ_ORDER_SUFFIX}",
        customer=customer,
        line_items=line_items,
        delivery_services=delivery_services,
        payment_title=payment_title,
        payment_type_key=payment_type_key,
        delivery_sd_key=delivery_sd_key,
        delivery_type_key=delivery_type_key,
        pvz_code=pvz_code,
        shipment_address=shipment_address,
        description=comment,
        promo_code=None,       # uCoz не передаёт код купона в ответе uamo.php
        organization_id=None,  # ИП Перфилов = MS_ORGANIZATION_ID (TODO Q5)
        state_id=None,
        project_id=None,
        is_paid=is_paid,
        is_cod=is_cod,
        cod_margin_amount_cents=cod_margin_cents,
        delivery_cost_attr_value=delivery_cost_attr,
        estimated_cost=estimated_cost,
        total_to_pay=total_to_pay,
    )
