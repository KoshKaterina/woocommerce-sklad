# Нормализатор WooCommerce: order_data → NormalizedOrder

from .field_mappers import (
    build_shipment_address,
    extract_courier_comment,
    extract_promo_code,
    extract_pvz_code,
    is_card_payment,
    map_delivery_sd,
    map_delivery_type,
    map_payment_type,
)
from .logger import get_logger
from .normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)

log = get_logger(__name__)


def _safe(name: str, func):
    """Обёртка для безопасного маппинга — WARNING при ошибке, возврат None."""
    try:
        return func()
    except Exception as e:
        log.warning("WC normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def normalize_wc_order(order_data: dict) -> NormalizedOrder:
    """Преобразовать сырой заказ WooCommerce в NormalizedOrder."""
    billing = order_data.get("billing", {})
    payment_title = order_data.get("payment_method_title", "")
    shipping_lines = order_data.get("shipping_lines", [])
    method_title = shipping_lines[0].get("method_title", "") if shipping_lines else ""
    card_payment = is_card_payment(payment_title)

    # Клиент
    customer = NormalizedCustomer(
        full_name=billing.get("first_name", ""),
        phone=billing.get("phone", ""),
        email=billing.get("email", ""),
    )

    # Товарные позиции
    line_items = []
    for item in order_data.get("line_items", []):
        line_items.append(NormalizedLineItem(
            sku=item.get("sku", ""),
            title=item.get("name", ""),
            price_cents=round(float(item.get("price", 0)) * 100),
            quantity=int(item.get("quantity", 1)),
        ))

    # Услуги доставки
    delivery_services = []
    for sl in shipping_lines:
        name = sl.get("method_title", "")
        if card_payment:
            price_cents = 0
        else:
            price_cents = round(float(sl.get("total", 0)) * 100)
        delivery_services.append(NormalizedDeliveryService(
            name=name,
            price_cents=price_cents,
        ))

    # Маппинг атрибутов
    payment_type_key = _safe("map_payment_type", lambda: map_payment_type(payment_title))
    delivery_sd_key = _safe("map_delivery_sd", lambda: map_delivery_sd(method_title))
    delivery_type_key = _safe("map_delivery_type", lambda: map_delivery_type(method_title))
    pvz_code = _safe("extract_pvz_code", lambda: extract_pvz_code(order_data))
    shipment_address = _safe("build_shipment_address", lambda: build_shipment_address(order_data))
    description = _safe("extract_courier_comment", lambda: extract_courier_comment(order_data))
    promo_code = _safe("extract_promo_code", lambda: extract_promo_code(order_data))

    # Состояние оплаты
    is_cod = "при получении" in payment_title.lower()
    is_paid = order_data.get("status") == "processing" and not is_cod and not card_payment

    order_id = str(order_data["id"])

    return NormalizedOrder(
        source="woocommerce",
        order_id=order_id,
        order_number=order_id,
        customer=customer,
        line_items=line_items,
        delivery_services=delivery_services,
        payment_title=payment_title,
        payment_type_key=payment_type_key,
        delivery_sd_key=delivery_sd_key,
        delivery_type_key=delivery_type_key,
        pvz_code=pvz_code,
        shipment_address=shipment_address,
        description=description,
        promo_code=promo_code,
        is_paid=is_paid,
        is_cod=is_cod,
    )
