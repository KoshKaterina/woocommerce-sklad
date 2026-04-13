# Тесты нормализатора InSales

import json
import os
from unittest.mock import MagicMock

import pytest

from woo_moysklad.insales_normalizer import (
    build_customer_name,
    build_delivery_service_name,
    expand_family_pack,
    extract_insales_promo_code,
    extract_insales_pvz_code,
    map_insales_delivery_sd,
    map_insales_delivery_type,
    map_insales_payment_type,
    normalize_insales_order,
)

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "insales_sample_order.json")


@pytest.fixture
def sample_insales_order():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def make_config():
    cfg = MagicMock()
    cfg.MS_ORGANIZATION_INSALES_ID = "org-insales-uuid"
    cfg.MS_STATE_INSALES_NEW_ID = "state-insales-uuid"
    return cfg


# --- build_customer_name ---

def test_customer_name_basic():
    assert build_customer_name({"name": "Ирина", "surname": "Фролова"}) == "Ирина Фролова"


def test_customer_name_with_middlename():
    assert build_customer_name({"name": "Ирина", "middlename": "Ивановна", "surname": "Фролова"}) == "Ирина Ивановна Фролова"


def test_customer_name_only_name():
    assert build_customer_name({"name": "Ирина"}) == "Ирина"


def test_customer_name_empty():
    assert build_customer_name({}) == "Без имени"


# --- expand_family_pack ---

def test_family_pack_black():
    items = expand_family_pack("TG-FPB", 10000.0, 1)
    assert len(items) == 2
    assert items[0].sku == "TG128X3-B"
    assert items[1].sku == "TG128X3-B"
    assert items[0].price_cents + items[1].price_cents == 1000000
    assert items[0].quantity == 1
    assert items[1].quantity == 1


def test_family_pack_color():
    items = expand_family_pack("TG-FPSEA", 15000.0, 1)
    assert len(items) == 2
    assert items[0].sku == "TG128X3-B"
    assert items[1].sku == "TG-SEA"
    assert items[0].price_cents + items[1].price_cents == 1500000


def test_family_pack_quantity():
    """qty=2 → каждая позиция с qty=2."""
    items = expand_family_pack("TG-FPSTEALTH", 12000.0, 2)
    assert len(items) == 2
    assert items[0].quantity == 2
    assert items[1].quantity == 2
    assert items[1].sku == "TG-STEALTH"


def test_family_pack_odd_price():
    """Нечётная цена: сумма двух половин = оригинал."""
    items = expand_family_pack("TG-FPSEA", 7601.01, 1)
    total = items[0].price_cents + items[1].price_cents
    assert total == round(7601.01 * 100)


# --- build_delivery_service_name ---

def test_delivery_name_pvz_single_day():
    di = {"tariff_id": "cdek_parcel_delivery_point", "delivery_interval": {"min_days": 5, "max_days": 5}}
    assert build_delivery_service_name(di, "Доставка в Пункты выдачи (СДЭК)") == "CDEK: Самовывоз (5 дней)"


def test_delivery_name_pvz_range():
    di = {"tariff_id": "cdek_parcel_delivery_point", "delivery_interval": {"min_days": 3, "max_days": 5}}
    assert build_delivery_service_name(di, "Доставка в Пункты выдачи (СДЭК)") == "CDEK: Самовывоз, (3-5 дней)"


def test_delivery_name_courier():
    di = {"tariff_id": "cdek_parcel_courier", "delivery_interval": {"min_days": 2, "max_days": 3}}
    assert build_delivery_service_name(di, "Курьерская доставка") == "CDEK: Посылка склад-дверь, (2-3 дней)"


def test_delivery_name_showroom():
    assert build_delivery_service_name({}, "Самовывоз из Шоурума") == "Самовывоз из офиса Sunscrypt"


def test_delivery_name_no_delivery():
    assert build_delivery_service_name({}, "Доставка не требуется") is None


# --- map_insales_payment_type ---

def test_payment_online():
    assert map_insales_payment_type("Оплата онлайн") == "prepaid"


def test_payment_card():
    assert map_insales_payment_type("Оплата картой") == "prepaid"


def test_payment_cod():
    assert map_insales_payment_type("Оплата при получении") == "noncash"


# --- map_insales_delivery_sd ---

def test_delivery_sd_cdek():
    assert map_insales_delivery_sd({"shipping_company": "СДЭК"}) == "cdek"


def test_delivery_sd_none():
    assert map_insales_delivery_sd({}) is None


# --- map_insales_delivery_type ---

def test_delivery_type_pvz():
    di = {"outlet": {"type": "pvz"}}
    assert map_insales_delivery_type(di, "Доставка в ПВЗ") == "pvz"


def test_delivery_type_courier():
    di = {"tariff_id": "cdek_parcel_courier"}
    assert map_insales_delivery_type(di, "Курьерская доставка") == "courier"


def test_delivery_type_showroom():
    assert map_insales_delivery_type({}, "Самовывоз из Шоурума") == "pvz"


# --- extract_insales_pvz_code ---

def test_pvz_code_cdek():
    di = {"outlet": {"external_id": "cdek#SVK27"}}
    assert extract_insales_pvz_code(di) == "SVK27"


def test_pvz_code_no_hash():
    di = {"outlet": {"external_id": "SVK27"}}
    assert extract_insales_pvz_code(di) == "SVK27"


def test_pvz_code_none():
    assert extract_insales_pvz_code({}) is None


# --- extract_insales_promo_code ---

def test_promo_code_coupon():
    assert extract_insales_promo_code([{"coupon": "SALE10"}]) == "SALE10"


def test_promo_code_description():
    assert extract_insales_promo_code([{"description": "PROMO"}]) == "PROMO"


def test_promo_code_empty():
    assert extract_insales_promo_code([]) is None


# --- normalize_insales_order (интеграционный) ---

def test_normalize_real_order(sample_insales_order):
    """Нормализация реального заказа из фикстуры."""
    config = make_config()
    order = normalize_insales_order(sample_insales_order, config)

    assert order.source == "insales"
    assert order.order_number == "17621 Tangemshop"
    assert order.customer.full_name == "Татьяна Азимова"
    assert order.customer.phone == "+79222546325"
    assert order.customer.email == "tocik_20@mail.ru"
    assert len(order.line_items) == 1
    assert order.line_items[0].sku == "TG130X3-B"  # маппинг TG-130X3-B → TG130X3-B
    assert order.line_items[0].price_cents == 760000
    assert order.payment_title == "Оплата онлайн"
    assert order.payment_type_key == "prepaid"
    assert order.delivery_sd_key == "cdek"
    assert order.delivery_type_key == "pvz"
    assert order.pvz_code == "SUR18"
    assert order.is_cod is False
    assert order.is_paid is False  # paid_at is null
    assert order.organization_id == "org-insales-uuid"
    assert order.state_id == "state-insales-uuid"


def test_normalize_cod_margins():
    """COD заказ с наценкой."""
    order_data = {
        "id": 999,
        "number": 99,
        "client": {"name": "Тест", "surname": "Тестов", "phone": "+79001234567", "email": ""},
        "order_lines": [{"sku": "TG128X2-B", "title": "Test", "sale_price": 5800.0, "quantity": 1}],
        "delivery_title": "Доставка в Пункты выдачи (СДЭК)",
        "delivery_price": 531.0,
        "payment_title": "Оплата при получении",
        "margin": "5.0",
        "margin_amount": "316.55",
        "total_price": 6647.55,
        "financial_status": "pending",
        "paid_at": None,
        "comment": None,
        "discounts": [],
        "delivery_info": {
            "tariff_id": "cdek_parcel_delivery_point",
            "shipping_company": "СДЭК",
            "delivery_interval": {"min_days": 4, "max_days": 4},
            "outlet": {"type": "pvz", "external_id": "cdek#SVK27",
                       "address": "164500, Россия, Архангельская область, Северодвинск"},
        },
        "shipping_address": {"city": "Северодвинск", "address": None},
    }
    config = make_config()
    order = normalize_insales_order(order_data, config)

    assert order.is_cod is True
    assert order.cod_margin_amount_cents == 31655
    assert order.delivery_cost_attr_value == "847.55"
    assert order.total_to_pay == "6647.55"
    assert order.payment_type_key == "noncash"
    # Услуга доставки: всегда полная цена
    assert len(order.delivery_services) == 1
    assert order.delivery_services[0].price_cents == 53100
    assert order.delivery_services[0].name == "CDEK: Самовывоз (4 дней)"


def test_normalize_prepaid_delivery_full_price():
    """Предоплата: доставка всегда полная стоимость."""
    order_data = {
        "id": 888,
        "number": 88,
        "client": {"name": "Тест", "phone": "+79001234567", "email": ""},
        "order_lines": [{"sku": "TG128X2-B", "title": "Test", "sale_price": 5800.0, "quantity": 1}],
        "delivery_title": "Доставка в Пункты выдачи (СДЭК)",
        "delivery_price": 1153.0,
        "payment_title": "Оплата онлайн",
        "margin_amount": "0",
        "total_price": 6953.0,
        "financial_status": "pending",
        "paid_at": None,
        "comment": None,
        "discounts": [],
        "delivery_info": {
            "tariff_id": "cdek_parcel_delivery_point",
            "shipping_company": "СДЭК",
            "delivery_interval": {"min_days": 4, "max_days": 4},
            "outlet": {"type": "pvz", "external_id": "cdek#SUR18",
                       "address": "Сургут, ул. Юности, 8"},
        },
        "shipping_address": {},
    }
    config = make_config()
    order = normalize_insales_order(order_data, config)

    assert order.delivery_services[0].price_cents == 115300
    assert order.is_cod is False
