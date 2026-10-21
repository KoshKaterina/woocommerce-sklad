# Нормализатор WooCommerce: order_data → NormalizedOrder

from woo_moysklad.core.address_parser import parse_wc_address
from woo_moysklad.core.field_mappers import (
    build_shipment_address,
    detect_delivery_type,
    extract_cdek_meta,
    extract_courier_comment,
    extract_promo_code,
    extract_pvz_code,
    extract_telegram,
    extract_ym_client_id,
    is_manual_payment_mark,
    is_manual_prepayment,
    is_office_pickup,
    map_delivery_sd,
    map_delivery_type,
    map_payment_type,
)
from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)
from woo_moysklad.woocommerce.preorder import read_order_preorders
from woo_moysklad.woocommerce.consents import extract_checkout_consents

log = get_logger(__name__)


def _safe(name: str, func):
    """Обёртка для безопасного маппинга — WARNING при ошибке, возврат None."""
    try:
        return func()
    except Exception as e:
        log.warning("WC normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def normalize_wc_order(order_data: dict, *, preorder_enabled: bool = False) -> NormalizedOrder:
    """Преобразовать сырой заказ WooCommerce в NormalizedOrder."""
    billing = order_data.get("billing", {})
    payment_title = order_data.get("payment_method_title", "")
    shipping_lines = order_data.get("shipping_lines", [])
    method_title = shipping_lines[0].get("method_title", "") if shipping_lines else ""
    manual_prepayment = is_manual_prepayment(payment_title)

    # Клиент: имя + фамилия (billing.last_name — отдельное поле WC)
    customer = NormalizedCustomer(
        full_name=" ".join(filter(None, [billing.get("first_name", "").strip(),
                                         billing.get("last_name", "").strip()])),
        phone=billing.get("phone", ""),
        email=billing.get("email", ""),
        # Ник Телеграма живёт у человека: пишется в доп.поле контрагента, не заказа
        telegram=_safe("extract_telegram", lambda: extract_telegram(order_data)),
    )

    # Товарные позиции
    line_items = []
    raw_items = order_data.get("line_items", [])
    # Выключенный режим полностью игнорирует служебную мету, включая старые
    # и некорректные значения: старый поток не зависит от нового producer.
    snapshots = read_order_preorders(raw_items) if preorder_enabled else [None] * len(raw_items)
    for item, snapshot in zip(raw_items, snapshots):
        line_items.append(NormalizedLineItem(
            sku=snapshot["sku"] if snapshot is not None else item.get("sku", ""),
            title=item.get("name", ""),
            price_cents=round(float(item.get("price", 0)) * 100),
            quantity=int(item.get("quantity", 1)),
            preorder=snapshot["preorder"] if snapshot is not None else None,
            product_id=snapshot["product_id"] if snapshot is not None else None,
            variation_id=snapshot["variation_id"] if snapshot is not None else None,
        ))

    # Услуги доставки: обнуляем цену только при ручной предоплате (на карту /
    # банковский перевод) И доставке СДЭК — это промо «бесплатная доставка».
    # Остальные услуги — реальная стоимость.
    delivery_services = []
    for sl in shipping_lines:
        name = sl.get("method_title", "")
        lower_name = name.lower()
        is_cdek = "cdek" in lower_name or "сдэк" in lower_name
        if manual_prepayment and is_cdek:
            price_cents = 0
        else:
            price_cents = round(float(sl.get("total", 0)) * 100)
        delivery_services.append(NormalizedDeliveryService(
            name=name,
            price_cents=price_cents,
        ))

    # Маппинг атрибутов
    payment_type_key = _safe("map_payment_type", lambda: map_payment_type(payment_title))
    if is_office_pickup(order_data):
        # local_pickup ловим по method_id, не полагаясь на название метода
        delivery_sd_key = "showroom"
    else:
        delivery_sd_key = _safe("map_delivery_sd", lambda: map_delivery_sd(method_title))
    delivery_type_key = _safe("map_delivery_type", lambda: map_delivery_type(method_title))
    pvz_code = _safe("extract_pvz_code", lambda: extract_pvz_code(order_data))
    # Самовывоз из офиса — адреса доставки нет, ни плоского, ни структурного
    if is_office_pickup(order_data):
        shipment_address = None
        shipment_address_parts = None
    else:
        shipment_address = _safe("build_shipment_address",
                                 lambda: build_shipment_address(order_data))
        shipment_address_parts = _safe(
            "parse_wc_address",
            lambda: parse_wc_address(order_data.get("shipping", {}),
                                     detect_delivery_type(method_title),
                                     cdek_city=extract_cdek_meta(order_data)["city"]),
        )
    description = _safe("extract_courier_comment", lambda: extract_courier_comment(order_data))
    promo_code = _safe("extract_promo_code", lambda: extract_promo_code(order_data))
    ym_client_id = _safe("extract_ym_client_id", lambda: extract_ym_client_id(order_data))

    # Состояние оплаты: при получении, ручная предоплата и «Другой способ» - отмечает менеджер
    is_cod = "при получении" in payment_title.lower()
    is_paid = order_data.get("status") == "processing" and not is_manual_payment_mark(payment_title)

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
        shipment_address_parts=shipment_address_parts,
        description=description,
        promo_code=promo_code,
        ym_client_id=ym_client_id,
        checkout_consents=_safe("checkout_consents", lambda: extract_checkout_consents(order_data)),
        is_paid=is_paid,
        is_cod=is_cod,
    )
