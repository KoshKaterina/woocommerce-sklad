# Нормализатор InSales: order_data → NormalizedOrder

from .logger import get_logger
from .normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)

log = get_logger(__name__)

# Тестовый суффикс номера заказа (убрать после тестирования)
_INSALES_ORDER_SUFFIX = " Tangemshop"

# Family Pack: первая позиция всегда чёрный Tangem 3 карты
_FAMILY_PACK_BLACK_SKU = "TG128X3-B"


def _safe(name: str, func):
    try:
        return func()
    except Exception as e:
        log.warning("InSales normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def build_customer_name(client: dict) -> str:
    """Собрать 'Имя Фамилия' из клиента InSales."""
    parts = []
    name = (client.get("name") or "").strip()
    middlename = (client.get("middlename") or "").strip()
    surname = (client.get("surname") or "").strip()

    if name:
        parts.append(name)
    if middlename:
        parts.append(middlename)
    if surname:
        parts.append(surname)

    return " ".join(parts) or "Без имени"


def expand_family_pack(sku: str, sale_price: float, quantity: int) -> list[NormalizedLineItem]:
    """Разбить Family Pack на 2 позиции.

    TG-FPB      → 2 × TG128X3-B
    TG-FP{COLOR} → TG128X3-B + TG-{COLOR}
    Цена: первый = floor(total_cents / 2), второй = total_cents - first.
    Количество каждой позиции = quantity оригинала.
    """
    total_cents = round(sale_price * 100)
    first_price = total_cents // 2
    second_price = total_cents - first_price

    # Определяем SKU цветного
    color = sku[5:]  # "TG-FP" = 5 символов, остаток = код цвета
    if color == "B":
        # Чёрный + чёрный
        second_sku = _FAMILY_PACK_BLACK_SKU
    else:
        second_sku = f"TG-{color}"

    return [
        NormalizedLineItem(
            sku=_FAMILY_PACK_BLACK_SKU,
            title=f"Family Pack: Tangem 2.0 (3 карты) чёрный",
            price_cents=first_price,
            quantity=quantity,
        ),
        NormalizedLineItem(
            sku=second_sku,
            title=f"Family Pack: Tangem 2.0 (3 карты) {color}",
            price_cents=second_price,
            quantity=quantity,
        ),
    ]


def build_delivery_service_name(delivery_info: dict, delivery_title: str) -> str | None:
    """Построить имя услуги доставки для МС в формате WC.

    Формат WC (анализ 100 услуг в МС):
      Один день, без запятой: "CDEK: Самовывоз (5 дней)"
      Диапазон, с запятой:   "CDEK: Самовывоз, (5-6 дней)"

    Особые случаи:
      "Самовывоз из Шоурума"   → "Самовывоз из офиса Sunscrypt"
      "Доставка не требуется"  → None
    """
    lower_title = delivery_title.lower()

    if "самовывоз из шоурума" in lower_title:
        return "Самовывоз из офиса Sunscrypt"

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


def map_insales_payment_type(payment_title: str) -> str | None:
    """Определить тип приёма платежа."""
    lower = payment_title.lower()
    if "онлайн" in lower or "картой" in lower:
        return "prepaid"
    if "при получении" in lower:
        return "noncash"
    log.warning("InSales: неизвестный способ оплаты", payment_title=payment_title)
    return None


def map_insales_delivery_sd(delivery_info: dict) -> str | None:
    """Определить службу доставки из delivery_info."""
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
    """Извлечь промокод из массива скидок InSales."""
    if not discounts:
        return None
    for d in discounts:
        code = d.get("coupon") or d.get("code") or d.get("description")
        if code:
            return str(code).strip()
    return None


def normalize_insales_order(order_data: dict, config) -> NormalizedOrder:
    """Преобразовать сырой заказ InSales в NormalizedOrder."""
    client = order_data.get("client", {})
    delivery_info = order_data.get("delivery_info") or {}
    shipping_address = order_data.get("shipping_address") or {}
    delivery_title = order_data.get("delivery_title", "")
    payment_title = order_data.get("payment_title", "")
    discounts = order_data.get("discounts", [])

    # Клиент
    customer = NormalizedCustomer(
        full_name=build_customer_name(client),
        phone=client.get("phone", ""),
        email=client.get("email", ""),
    )

    # Товарные позиции (с разбивкой Family Pack)
    line_items = []
    for ol in order_data.get("order_lines", []):
        sku = (ol.get("sku") or "").strip()
        sale_price = float(ol.get("sale_price", 0))
        quantity = int(ol.get("quantity", 1))
        title = ol.get("title", "")

        if sku.upper().startswith("TG-FP"):
            expanded = expand_family_pack(sku, sale_price, quantity)
            line_items.extend(expanded)
        elif sku == "TG-130X3-B":
            # Исправление расхождения SKU: InSales "TG-130X3-B" → МС "TG130X3-B"
            sku = "TG130X3-B"
            line_items.append(NormalizedLineItem(
                sku=sku, title=title,
                price_cents=round(sale_price * 100), quantity=quantity,
            ))
        else:
            line_items.append(NormalizedLineItem(
                sku=sku,
                title=title,
                price_cents=round(sale_price * 100),
                quantity=quantity,
            ))

    # Маппинг
    payment_type_key = _safe("payment_type", lambda: map_insales_payment_type(payment_title))
    delivery_sd_key = _safe("delivery_sd", lambda: map_insales_delivery_sd(delivery_info))
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

    # Стоимость доставки для атрибута = доставка + наценка
    delivery_price_raw = float(order_data.get("delivery_price", 0))
    delivery_cost_total = delivery_price_raw + margin_amount
    delivery_cost_attr = str(delivery_cost_total) if delivery_cost_total > 0 else None

    # Предварительная стоимость (сумма sale_price товаров)
    items_total = sum(round(float(ol.get("sale_price", 0)) * 100)
                      * int(ol.get("quantity", 1))
                      for ol in order_data.get("order_lines", []))
    estimated_cost = str(items_total // 100)

    # Сумма к оплате получателю (только COD)
    total_price = order_data.get("total_price", 0)
    total_to_pay = str(total_price) if is_cod else None

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
        is_paid=is_paid,
        is_cod=is_cod,
        cod_margin_amount_cents=cod_margin_cents,
        delivery_cost_attr_value=delivery_cost_attr,
        estimated_cost=estimated_cost,
        total_to_pay=total_to_pay,
    )
