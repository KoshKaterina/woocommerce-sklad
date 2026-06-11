"""Тесты нормализатора WooCommerce — поведение ручной предоплаты.

Ключевая гарантия: "Банковский перевод" ведёт себя ВЕЗДЕ так же, как "На карту":
- бесплатная доставка СДЭК (цена услуги обнуляется),
- заказ НЕ помечается оплаченным автоматически (is_paid=False),
- тип приёма платежа = prepaid.
"Онлайн оплата" — контрпример: оплачивается сразу, доставка не обнуляется.
"""

from woo_moysklad.woocommerce.normalizer import normalize_wc_order


def _wc_order(payment_title: str, delivery_title: str = "CDEK: Самовывоз"):
    return {
        "id": 12345,
        "status": "processing",
        "billing": {"first_name": "Иван", "phone": "+79990000000", "email": "i@example.com"},
        "line_items": [{"sku": "TG128X3-B", "name": "Tangem", "price": "8990", "quantity": 1}],
        "shipping_lines": [{"method_title": delivery_title, "total": "500"}],
        "payment_method_title": payment_title,
    }


def _delivery_price(order):
    return order.delivery_services[0].price_cents


def test_bank_transfer_cdek_free_delivery():
    """Банковский перевод + СДЭК → доставка бесплатна (как и На карту)."""
    n = normalize_wc_order(_wc_order("Банковский перевод"))
    assert _delivery_price(n) == 0
    assert n.payment_type_key == "prepaid"
    assert n.is_paid is False


def test_card_cdek_free_delivery():
    """Регрессия: На карту + СДЭК → доставка бесплатна."""
    n = normalize_wc_order(_wc_order("На карту"))
    assert _delivery_price(n) == 0
    assert n.is_paid is False


def test_online_cdek_delivery_charged_and_paid():
    """Контрпример: Онлайн оплата + СДЭК → доставка платная, заказ оплачен."""
    n = normalize_wc_order(_wc_order("Онлайн оплата"))
    assert _delivery_price(n) == 50000  # 500 руб * 100
    assert n.payment_type_key == "prepaid"
    assert n.is_paid is True


def test_bank_transfer_non_cdek_delivery_charged():
    """Банковский перевод, но не СДЭК → доставка платная (обнуление только для СДЭК)."""
    n = normalize_wc_order(_wc_order("Банковский перевод", delivery_title="Курьер по Москве"))
    assert _delivery_price(n) == 50000
    assert n.is_paid is False  # ручная предоплата — всё равно не помечаем оплаченным


def test_office_pickup_no_address():
    """Самовывоз из офиса: ни плоского адреса, ни shipmentAddressFull."""
    order = _wc_order("Онлайн оплата", delivery_title="Самовывоз из офиса Sunscrypt")
    order["shipping_lines"][0]["method_id"] = "local_pickup"
    order["shipping"] = {"city": "Москва", "state": "МОСКВА", "country": "RU",
                         "address_1": "", "address_2": "", "postcode": ""}
    n = normalize_wc_order(order)
    assert n.shipment_address is None
    assert n.shipment_address_parts is None


def test_pvz_empty_address2_fallback_to_cdek_meta():
    """Сбой чекаута (заказ 17130): address_2 пуст → код и город из меты CDEK."""
    order = _wc_order("При получении", delivery_title="CDEK: Самовывоз, (2-3 дней)")
    order["shipping_lines"][0]["method_id"] = "official_cdek"
    order["shipping_lines"][0]["meta_data"] = [
        {"key": "_official_cdek_office_code", "value": "SPB217"},
        {"key": "_official_cdek_city", "value": "Санкт-Петербург"},
    ]
    order["shipping"] = {"city": "Санкт-Петербург", "state": "САНКТ-ПЕТЕРБУРГ",
                         "country": "RU", "address_1": "", "address_2": "", "postcode": ""}
    n = normalize_wc_order(order)
    assert n.pvz_code == "SPB217"
    assert n.shipment_address == "Россия, SPB217, Санкт-Петербург"
    assert n.shipment_address_parts.city == "Санкт-Петербург"
    assert n.shipment_address_parts.add_info == ""  # дубля города в «Другое» нет
