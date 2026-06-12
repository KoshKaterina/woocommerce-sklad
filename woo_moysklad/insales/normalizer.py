# Нормализатор InSales: order_data → NormalizedOrder

import re

from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)

log = get_logger(__name__)

# Постоянный суффикс номера заказа InSales: предохраняет от коллизии с WC
# (оба источника используют числовые id) и группирует Tangemshop-заказы в МС.
_INSALES_ORDER_SUFFIX = " Tangemshop"

# InSales не кладёт сам код купона в заказ — только в description скидки
# в формате "Скидка по купону <CODE>" (+ discount_code_id для дозапроса).
# Парсим description, чтобы не делать доп. запрос к /discount_codes/{id}.
_INSALES_COUPON_DESC_RE = re.compile(r"по\s+купону\s+(\S+)", re.IGNORECASE)

# Маппинг InSales → МС только для случаев, которые нельзя резолвить автопоиском:
#   - Family Pack (одна позиция InSales разбивается на 2 товара МС)
#   - SKU-костыли (InSales SKU отличается от МС, пока не поправили в InSales)
# Для всех остальных SKU: InSales SKU == МС SKU → passthrough, далее ProductMatcher
# ищет товар в МС через API (find_by_filter по article/externalCode).

_INSALES_SKU_TO_MS_SKUS: dict[str, list[str]] = {
    # Family Pack: всегда 1 чёрный 3-карточный + 1 второй по цвету
    "TG-FPB":           ["TG128X3-B", "TG128X3-B"],
    "TG-FPW":           ["TG128X3-B", "TG130X3-B"],
    "TG-FPSEA":         ["TG128X3-B", "TG-SEA"],
    "TG-FPSTEALTH":     ["TG128X3-B", "TG-STEALTH"],
    "TG-FPSKY":         ["TG128X3-B", "TG-SKY"],
    "TG-FPHYPERBLUE":   ["TG128X3-B", "TG-HYPERBLUE"],
    # SKU-костыль: в InSales лишний дефис, в МС — без него
    "TG-130X3-B":       ["TG130X3-B"],
}

# Для позиций InSales с пустым SKU — резолв по variant_id.
# По мере проставления SKU в InSales записи отсюда можно будет удалять.
_INSALES_VARIANT_ID_TO_MS_SKUS: dict[int, list[str]] = {
    2053393393: ["TG128X2-B"],                     # 2 карты / Классический чёрный
    2053393409: ["TG128X3-B", "TG128X3-B"],        # Family Pack / Классический чёрный
    2053395121: ["TG130X2-B"],                     # 2 карты / Классический белый
    2053395129: ["TG130X3-B"],                     # 3 карты / Классический белый
    2053395137: ["TG128X3-B", "TG130X3-B"],        # Family Pack / Классический белый
    2053396585: ["TG-STEALTH"],                    # 3 карты / Stealth
    2053396601: ["TG128X3-B", "TG-STEALTH"],       # Family Pack / Stealth
    2053401321: ["TG128X3-B", "TG-SKY"],           # Family Pack / Blush Sky
    2053411473: ["TG-SEA"],                        # 3 карты / Electra Sea
    2053411481: ["TG128X3-B", "TG-SEA"],           # Family Pack / Electra Sea
    2053412809: ["TG-HYPERBLUE"],                  # 3 карты / Hyperblue
    2053412817: ["TG128X3-B", "TG-HYPERBLUE"],     # Family Pack / Hyperblue
    # 2-карточные цветные (Stealth/Sky/Sea/Hyperblue) не продаются — не маппим.
}


def resolve_to_ms_skus(raw_sku: str, variant_id: int | None) -> list[str]:
    """Вернуть список SKU МС для одной позиции InSales.

    Порядок:
    1. Если SKU заполнен и есть в _INSALES_SKU_TO_MS_SKUS (Family Pack / костыли) → маппинг.
    2. Если SKU заполнен, но не в словаре → passthrough (InSales SKU = МС SKU).
       ProductMatcher найдёт товар в МС через API по этому SKU.
       Если не найдёт — залогирует ошибку и позиция будет пропущена.
    3. Если SKU пустой, но есть variant_id в _INSALES_VARIANT_ID_TO_MS_SKUS → маппинг.
    4. Если SKU пустой и variant_id неизвестен → [] (позиция пропускается).

    Возвращает 1 элемент для обычного товара, 2+ для Family Pack (цена делится).
    """
    sku = (raw_sku or "").strip()
    if sku:
        if sku in _INSALES_SKU_TO_MS_SKUS:
            return _INSALES_SKU_TO_MS_SKUS[sku]
        return [sku]  # passthrough → автопоиск в МС через ProductMatcher
    if variant_id is not None and variant_id in _INSALES_VARIANT_ID_TO_MS_SKUS:
        return _INSALES_VARIANT_ID_TO_MS_SKUS[variant_id]
    return []


def _split_price_evenly(total_cents: int, parts: int) -> list[int]:
    """Разделить сумму в копейках на parts без потерь: [total//parts, ..., остаток]."""
    if parts <= 0:
        return []
    base = total_cents // parts
    result = [base] * parts
    result[-1] = total_cents - base * (parts - 1)
    return result


def _safe(name: str, func):
    try:
        return func()
    except Exception as e:
        log.warning("InSales normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def _join_name_parts(src: dict) -> str:
    """Собрать 'Имя [Отчество] Фамилия' из полей name/middlename/surname."""
    parts = []
    for key in ("name", "middlename", "surname"):
        v = (src.get(key) or "").strip()
        if v:
            parts.append(v)
    return " ".join(parts)


def _has_letters(s: str) -> bool:
    return any(ch.isalpha() for ch in s)


def build_customer_name(client: dict, shipping_address: dict | None = None) -> str:
    """Собрать имя контрагента 'Имя [Отчество] Фамилия'.

    У client.name при быстром/гостевом оформлении InSales иногда лежит телефон,
    а не имя. Если в client нет «буквенного» имени — берём получателя из
    shipping_address. Если и там нет — 'Без имени' (телефон в имя не пишем,
    он и так попадает в поле телефона контрагента).
    """
    name = _join_name_parts(client)
    if _has_letters(name):
        return name

    if shipping_address:
        sa_name = _join_name_parts(shipping_address)
        if _has_letters(sa_name):
            return sa_name

    return "Без имени"


def build_delivery_service_name(delivery_info: dict, delivery_title: str) -> str | None:
    """Построить имя услуги доставки для МС в формате WC.

    Формат WC (анализ 100 услуг в МС):
      Один день, без запятой: "CDEK: Самовывоз (5 дней)"
      Диапазон, с запятой:   "CDEK: Самовывоз, (5-6 дней)"

    Особые случаи:
      "Самовывоз из Шоурума"   → "Самовывоз со склада" (склад ExpressRMS,
                                  НЕ шоурум/офис Sunscrypt — это WC-самовывоз)
      "Доставка не требуется"  → None
    """
    lower_title = delivery_title.lower()

    if "самовывоз из шоурума" in lower_title:
        return "Самовывоз со склада"

    if "не требуется" in lower_title:
        return None

    if not delivery_info:
        return delivery_title  # fallback: как есть

    tariff_id = delivery_info.get("tariff_id") or ""
    interval = delivery_info.get("delivery_interval") or {}
    min_days = interval.get("min_days")
    max_days = interval.get("max_days")

    # Определяем тип СДЭК
    tariff_map = {
        "cdek_parcel_delivery_point": "Самовывоз",
        "cdek_parcel_courier": "Посылка склад-дверь",
    }

    delivery_type = None
    for key, value in tariff_map.items():
        if key in tariff_id:
            delivery_type = value
            break

    if not delivery_type:
        log.warning("Неизвестный tariff_id InSales", tariff_id=tariff_id)
        return delivery_title  # fallback

    # Строим название
    if min_days is not None and max_days is not None:
        if min_days == max_days:
            return f"CDEK: {delivery_type} ({min_days} дней)"
        else:
            return f"CDEK: {delivery_type}, ({min_days}-{max_days} дней)"

    return f"CDEK: {delivery_type}"


def normalize_insales_payment_title(payment_title: str) -> str:
    """Привести название способа оплаты InSales к каноническому.

    «Ozon Pay» (в т.ч. с суффиксом «(неактивен)») — это онлайн-оплата:
    в доп.поле «Способ оплаты» МС пишем «Онлайн-оплата», как в WC.
    """
    if "ozon pay" in payment_title.lower():
        return "Онлайн-оплата"
    return payment_title


def map_insales_payment_type(payment_title: str) -> str | None:
    """Определить тип приёма платежа."""
    lower = payment_title.lower()
    if "онлайн" in lower or "картой" in lower:
        return "prepaid"
    if "при получении" in lower:
        return "noncash"
    log.warning("InSales: неизвестный способ оплаты", payment_title=payment_title)
    return None


def map_insales_delivery_sd(delivery_info: dict, delivery_title: str = "") -> str | None:
    """Определить службу доставки для атрибута "Доставка (СД)" МС.

    СДЭК (любой тариф) → 'cdek' по shipping_company.
    "Самовывоз из Шоурума" → 'rms_pickup' («ExpressRMS(Самовывоз)»).
    Прочее ("Доставка не требуется", неизвестное) → None — менеджер ставит вручную.
    """
    if "самовывоз из шоурума" in delivery_title.lower():
        return "rms_pickup"
    if not delivery_info:
        return None
    company = (delivery_info.get("shipping_company") or "").lower()
    if "сдэк" in company or "cdek" in company:
        return "cdek"
    return None


def map_insales_delivery_type(delivery_info: dict, delivery_title: str) -> str | None:
    """Определить вид доставки."""
    lower_title = delivery_title.lower()

    if "самовывоз из шоурума" in lower_title:
        return "pvz"  # самовывоз = ПВЗ в МС

    if "не требуется" in lower_title:
        return None

    if delivery_info:
        outlet = delivery_info.get("outlet") or {}
        if outlet.get("type") == "pvz":
            return "pvz"

        tariff = delivery_info.get("tariff_id") or ""
        if "courier" in tariff:
            return "courier"

    return None


def extract_insales_pvz_code(delivery_info: dict) -> str | None:
    """Извлечь код ПВЗ: 'cdek#SVK27' → 'SVK27'."""
    if not delivery_info:
        return None
    outlet = delivery_info.get("outlet") or {}
    external_id = outlet.get("external_id") or ""
    if "#" in external_id:
        return external_id.split("#", 1)[1]
    return external_id or None


def build_insales_shipment_address(delivery_info: dict, shipping_address: dict,
                                   delivery_title: str) -> str | None:
    """Построить адрес доставки.

    ПВЗ:       delivery_info.outlet.address (готовый)
    Курьер:    shipping_address.city + ", " + shipping_address.address
    Самовывоз: None
    """
    lower_title = delivery_title.lower()

    if "самовывоз" in lower_title or "не требуется" in lower_title:
        return None

    if delivery_info:
        outlet = delivery_info.get("outlet") or {}
        if outlet.get("address"):
            return outlet["address"]

    # Курьер — собираем из shipping_address
    parts = []
    city = (shipping_address.get("city") or "").strip()
    address = (shipping_address.get("address") or "").strip()
    if city:
        parts.append(city)
    if address:
        parts.append(address)
    return ", ".join(parts) if parts else None


def extract_insales_promo_code(discounts: list) -> str | None:
    """Извлечь код купона из массива скидок InSales.

    InSales не кладёт сам код купона в заказ — только в description
    вида "Скидка по купону <CODE>" (и discount_code_id для дозапроса).
    У нас все скидки — от купонов (автоскидок в магазине нет),
    поэтому парсим description первого элемента без фильтров.
    """
    if not discounts:
        return None
    for d in discounts:
        description = d.get("description") or ""
        m = _INSALES_COUPON_DESC_RE.search(description)
        if m:
            return m.group(1).strip()
        log.warning("InSales: промокод не распарсен из description",
                    description=description,
                    discount_code_id=d.get("discount_code_id"))
    return None


def normalize_insales_order(order_data: dict, config) -> NormalizedOrder:
    """Преобразовать сырой заказ InSales в NormalizedOrder."""
    client = order_data.get("client", {})
    delivery_info = order_data.get("delivery_info") or {}
    shipping_address = order_data.get("shipping_address") or {}
    delivery_title = order_data.get("delivery_title", "")
    payment_title = normalize_insales_payment_title(order_data.get("payment_title", ""))
    discounts = order_data.get("discounts", [])

    # Клиент (имя — из client, при отсутствии буквенного имени берём получателя
    # из shipping_address; телефон/email — из аккаунта client)
    customer = NormalizedCustomer(
        full_name=build_customer_name(client, shipping_address),
        phone=client.get("phone", ""),
        email=client.get("email", ""),
    )

    # Товарные позиции — резолвим через маппинг, цена делится по числу МС-позиций.
    # Цена за штуку = sale_price − скидка_на_строку/quantity.
    # full_sale_price НЕ подходит: оно включает COD-наценку, а у нас наценка
    # идёт отдельной услугой. discounts_amount — это скидка по купону, размазанная
    # на строку заказа (InSales).
    line_items = []
    for ol in order_data.get("order_lines", []):
        raw_sku = ol.get("sku") or ""
        variant_id = ol.get("variant_id")
        ms_skus = resolve_to_ms_skus(raw_sku, variant_id)
        sale_price = float(ol.get("sale_price", 0))
        discounts_amount = float(ol.get("discounts_amount") or 0)
        quantity = int(ol.get("quantity", 1))
        title = ol.get("title", "")

        if not ms_skus:
            log.error("InSales: неизвестная позиция, пропускаем",
                      variant_id=variant_id, raw_sku=raw_sku, title=title)
            continue

        # Цена за штуку с учётом купонной скидки (в копейках).
        line_total_cents = round((sale_price * quantity - discounts_amount) * 100)
        per_unit_cents = line_total_cents // quantity if quantity else 0
        prices = _split_price_evenly(per_unit_cents, len(ms_skus))
        for ms_sku, price in zip(ms_skus, prices):
            line_items.append(NormalizedLineItem(
                sku=ms_sku,
                title=title,
                price_cents=price,
                quantity=quantity,
            ))

    # Маппинг
    payment_type_key = _safe("payment_type", lambda: map_insales_payment_type(payment_title))
    delivery_sd_key = _safe("delivery_sd",
                             lambda: map_insales_delivery_sd(delivery_info, delivery_title))
    delivery_type_key = _safe("delivery_type",
                               lambda: map_insales_delivery_type(delivery_info, delivery_title))
    pvz_code = _safe("pvz_code", lambda: extract_insales_pvz_code(delivery_info))
    shipment_address = _safe("shipment_address",
                              lambda: build_insales_shipment_address(
                                  delivery_info, shipping_address, delivery_title))
    promo_code = _safe("promo_code", lambda: extract_insales_promo_code(discounts))

    # Услуга доставки
    is_cod = "при получении" in payment_title.lower()
    is_pickup = "самовывоз" in delivery_title.lower()
    delivery_services = []

    svc_name = _safe("delivery_service_name",
                      lambda: build_delivery_service_name(delivery_info, delivery_title))
    if svc_name:
        delivery_price = float(order_data.get("delivery_price", 0))
        if is_pickup:
            price_cents = 0
        else:
            # Всегда полная стоимость доставки (и предоплата, и COD)
            price_cents = round(delivery_price * 100)
        delivery_services.append(NormalizedDeliveryService(
            name=svc_name, price_cents=price_cents))

    # COD наценка
    margin_amount = float(order_data.get("margin_amount") or "0")
    cod_margin_cents = round(margin_amount * 100) if margin_amount > 0 else 0

    # Стоимость доставки для атрибута = доставка + наценка (целые рубли, как в WC)
    delivery_price_raw = float(order_data.get("delivery_price", 0))
    delivery_cost_total = delivery_price_raw + margin_amount
    delivery_cost_attr = str(int(round(delivery_cost_total))) if delivery_cost_total > 0 else None

    # Оценочная стоимость (товары со скидкой, без COD-наценки, целые рубли)
    items_total_cents = 0
    for ol in order_data.get("order_lines", []):
        sp = float(ol.get("sale_price", 0))
        q = int(ol.get("quantity", 1))
        disc = float(ol.get("discounts_amount") or 0)
        items_total_cents += round((sp * q - disc) * 100)
    estimated_cost = str(items_total_cents // 100)

    # Сумма к оплате получателю (только COD, целые рубли)
    total_price = float(order_data.get("total_price") or 0)
    total_to_pay = str(int(round(total_price))) if is_cod else None

    # Состояние оплаты
    paid_at = order_data.get("paid_at")
    financial_status = order_data.get("financial_status", "")
    is_paid = paid_at is not None and financial_status == "paid"

    # Комментарий
    comment = (order_data.get("comment") or "").strip() or None

    order_number = str(order_data.get("number", order_data.get("id", "")))

    return NormalizedOrder(
        source="insales",
        order_id=str(order_data.get("id", "")),
        order_number=f"{order_number}{_INSALES_ORDER_SUFFIX}",
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
        promo_code=promo_code,
        organization_id=config.MS_ORGANIZATION_INSALES_ID or None,
        state_id=config.MS_STATE_INSALES_NEW_ID or None,
        project_id=config.MS_PROJECT_INSALES_ID or None,
        sales_channel_id=config.MS_SALES_CHANNEL_INSALES_ID or None,
        is_paid=is_paid,
        is_cod=is_cod,
        cod_margin_amount_cents=cod_margin_cents,
        delivery_cost_attr_value=delivery_cost_attr,
        estimated_cost=estimated_cost,
        total_to_pay=total_to_pay,
    )
