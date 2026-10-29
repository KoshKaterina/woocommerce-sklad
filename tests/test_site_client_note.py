from unittest.mock import MagicMock

from woo_moysklad.core.client_note import build_site_client_note
from woo_moysklad.core.normalized_order import NormalizedCustomer, NormalizedOrder
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.woocommerce.normalizer import normalize_wc_order


def make_order(**kwargs) -> NormalizedOrder:
    customer = kwargs.pop("customer", NormalizedCustomer(
        full_name="Ирина Фролова", phone="+7 (916) 123-45-67", email="irina@example.com"))
    return NormalizedOrder(source="insales", order_id="1", order_number="1 Tangemshop",
                           customer=customer, **kwargs)


def build_attrs(config, order):
    processor = OrderProcessor(config, MagicMock(), MagicMock(), MagicMock())
    return processor._build_attributes_from_normalized(order_number="1", order=order, source_lines=[])


def test_note_has_everything_the_client_typed():
    order = make_order(shipment_address="Москва, Тверская, 1", description="Позвоните\n\nза час")
    assert build_site_client_note(order) == (
        "Имя: Ирина Фролова\n"
        "Телефон: +7 (916) 123-45-67\n"
        "Почта: irina@example.com\n"
        "Адрес доставки: Москва, Тверская, 1\n"
        "Комментарий покупателя: Позвоните\nза час"
    )


def test_empty_parts_are_skipped_and_telegram_is_shown():
    order = make_order(customer=NormalizedCustomer(
        full_name="", phone="89161234567", telegram="@irina"))
    assert build_site_client_note(order) == "Телефон: 89161234567\nТелеграм: @irina"


def test_nothing_to_show_without_name_and_phone():
    order = make_order(customer=NormalizedCustomer(full_name=" ", phone="", email="a@b.ru"))
    assert build_site_client_note(order) is None


def test_long_comment_is_cut_under_ms_limit():
    note = build_site_client_note(make_order(description="я" * 6000))
    assert len(note) <= 3500


def test_woocommerce_order_gets_billing_name_and_phone():
    order = normalize_wc_order({
        "id": 5,
        "billing": {"first_name": "Олег", "last_name": "Петров", "phone": "+79990001122",
                    "email": "oleg@example.com"},
    })
    note = build_site_client_note(order)
    assert note.startswith("Имя: Олег Петров\nТелефон: +79990001122\nПочта: oleg@example.com")


def test_field_written_once_when_configured():
    attrs = build_attrs(MagicMock(MS_ATTR_SITE_CLIENT_ID="site-client"), make_order())
    rows = [a for a in attrs if a["meta"]["href"].endswith("/site-client")]
    assert len(rows) == 1
    assert rows[0]["value"].startswith("Имя: Ирина Фролова\n")


def test_empty_config_disables_only_this_field():
    attrs = build_attrs(MagicMock(MS_ATTR_SITE_CLIENT_ID=""), make_order())
    assert not any("Имя: " in a.get("value", "") for a in attrs if isinstance(a.get("value"), str))


def test_order_without_data_gets_no_field():
    order = make_order(customer=NormalizedCustomer(full_name="", phone=""))
    attrs = build_attrs(MagicMock(MS_ATTR_SITE_CLIENT_ID="site-client"), order)
    assert not any(a["meta"]["href"].endswith("/site-client") for a in attrs)
