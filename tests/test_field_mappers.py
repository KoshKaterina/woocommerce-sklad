# Тесты маппинга полей WC → МС

import json
import os

import pytest

from woo_moysklad.field_mappers import (
    build_attribute,
    build_shipment_address,
    detect_delivery_type,
    extract_courier_comment,
    extract_promo_code,
    extract_pvz_code,
    is_card_payment,
    map_delivery_sd,
    map_delivery_type,
    map_payment_type,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_order.json")


@pytest.fixture
def sample_order():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- detect_delivery_type ---

def test_detect_delivery_type_pvz():
    assert detect_delivery_type("CDEK: Самовывоз (1 дней)") == "pvz"

def test_detect_delivery_type_postamat():
    assert detect_delivery_type("CDEK: постамат") == "pvz"

def test_detect_delivery_type_courier():
    assert detect_delivery_type("Курьерская по Москве") == "courier"

def test_detect_delivery_type_nacenka():
    # Наценка — не содержит "самовывоз", по умолчанию courier
    assert detect_delivery_type("Наценка за наложенный платеж") == "courier"


# --- build_shipment_address ---

def test_build_shipment_address_pvz(sample_order):
    # method_title "CDEK: Самовывоз" → pvz → address_2 + postcode
    result = build_shipment_address(sample_order)
    assert result == "Россия, MSK2425, Москва, ул. Садовая-Кудринская, 20, 125464"

def test_build_shipment_address_courier():
    order = {
        "shipping": {"city": "Москва", "address_1": "ул. Ленина, 1", "postcode": "101000"},
        "shipping_lines": [{"method_title": "Курьерская по Москве"}],
    }
    result = build_shipment_address(order)
    assert result == "Россия, Москва, ул. Ленина, 1, 101000"


# --- extract_pvz_code ---

def test_extract_pvz_code(sample_order):
    assert extract_pvz_code(sample_order) == "MSK2425"

def test_extract_pvz_code_courier():
    order = {
        "shipping": {"address_2": ""},
        "shipping_lines": [{"method_title": "Курьерская по Москве"}],
    }
    assert extract_pvz_code(order) is None

def test_extract_pvz_code_various_formats():
    """Код ПВЗ: заглавные латинские буквы + цифры."""
    base = {"shipping_lines": [{"method_title": "CDEK: Самовывоз"}]}

    for address_2, expected in [
        ("SBP892, Санкт-Петербург, ул. Ленина, 5", "SBP892"),
        ("SO78 какой-то текст", "SO78"),
        ("Москва, MSK1, ул. Ленина", "MSK1"),
        ("Москва, ул. Ленина, 5", None),         # нет кода
        ("msk123, Москва", None),                 # строчные буквы
        ("", None),
    ]:
        order = {**base, "shipping": {"address_2": address_2}}
        assert extract_pvz_code(order) == expected, f"Failed for: {address_2!r}"


# --- map_payment_type ---

def test_map_payment_type_card():
    assert map_payment_type("На карту") == "prepaid"

def test_map_payment_type_online():
    assert map_payment_type("Онлайн оплата") == "prepaid"

def test_map_payment_type_cod():
    assert map_payment_type("При получении") == "noncash"

def test_map_payment_type_unknown():
    assert map_payment_type("Биткоин") is None


# --- is_card_payment ---

def test_is_card_payment_true():
    assert is_card_payment("На карту") is True

def test_is_card_payment_false():
    assert is_card_payment("При получении") is False

def test_is_card_payment_online():
    # "Онлайн оплата" — не "на карту"
    assert is_card_payment("Онлайн оплата") is False


# --- extract_courier_comment ---

def test_courier_comment(sample_order):
    assert extract_courier_comment(sample_order) == "Promo:coinmetrica тест ТЕСТ"

def test_courier_comment_empty():
    assert extract_courier_comment({"customer_note": ""}) is None


# --- extract_promo_code ---

def test_promo_code(sample_order):
    assert extract_promo_code(sample_order) == "coinmetrica"

def test_promo_code_empty():
    assert extract_promo_code({"coupon_codes": []}) is None


# --- map_delivery_sd ---

def test_delivery_sd_cdek():
    assert map_delivery_sd("CDEK: Самовывоз (1 дней)") == "cdek"

def test_delivery_sd_yandex():
    assert map_delivery_sd("Курьерская по Москве и МО") == "yandex"

def test_delivery_sd_pickup():
    assert map_delivery_sd("Самовывоз из офиса Sunscrypt") == "pickup"

def test_delivery_sd_unknown():
    assert map_delivery_sd("Наценка за наложенный платеж") is None


# --- map_delivery_type ---

def test_delivery_type_pvz():
    assert map_delivery_type("CDEK: Самовывоз (1 дней)") == "pvz"

def test_delivery_type_postamat():
    assert map_delivery_type("CDEK: постамат (2 дня)") == "postamat"

def test_delivery_type_courier():
    assert map_delivery_type("Курьерская по Москве") == "courier"


# --- build_attribute ---

def test_build_attribute_string():
    attr = build_attribute("test-uuid", "hello")
    assert attr is not None
    assert attr["value"] == "hello"
    assert "attributemetadata" in attr["meta"]["type"]

def test_build_attribute_number_as_string():
    attr = build_attribute("test-uuid", 1375)
    assert attr["value"] == "1375"

def test_build_attribute_none():
    assert build_attribute("test-uuid", None) is None
    assert build_attribute("", "value") is None

def test_build_attribute_custom_entity():
    attr = build_attribute(
        "attr-uuid", "custom",
        is_custom_entity=True,
        dictionary_id="dict-uuid",
        element_id="elem-uuid",
    )
    assert attr is not None
    assert attr["value"]["meta"]["type"] == "customentity"
    assert "dict-uuid" in attr["value"]["meta"]["href"]
    assert "elem-uuid" in attr["value"]["meta"]["href"]
