# Тесты нормализатора InSales

import json
import os
from unittest.mock import MagicMock

import pytest

from woo_moysklad.insales.normalizer import (
    build_customer_name,
    build_delivery_service_name,
    extract_insales_promo_code,
    extract_insales_pvz_code,
    map_insales_delivery_sd,
    map_insales_delivery_type,
    map_insales_payment_type,
    normalize_insales_order,
    resolve_to_ms_skus,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE_PATH = os.path.join(FIXTURES_DIR, "insales_sample_order.json")


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


def test_customer_name_phone_in_client_falls_back_to_shipping():
    # InSales положил телефон в client.name → берём получателя из shipping_address
    client = {"name": "79778271097", "phone": "+79778271097"}
    shipping = {"name": "Андрей", "surname": "Петров"}
    assert build_customer_name(client, shipping) == "Андрей Петров"


def test_customer_name_phone_in_client_no_shipping_name():
    # Имени нет нигде → 'Без имени', а не телефон
    assert build_customer_name({"name": "79778271097"}, {"phone": "+79778271097"}) == "Без имени"


def test_customer_name_real_name_ignores_shipping():
    # Если у client нормальное имя — shipping не нужен
    assert build_customer_name({"name": "Ирина", "surname": "Фролова"},
                               {"name": "Кто-то"}) == "Ирина Фролова"


# --- resolve_to_ms_skus ---

def test_resolve_regular_product_passthrough():
    """Обычный товар: SKU возвращается как есть (passthrough → ProductMatcher найдёт в МС)."""
    assert resolve_to_ms_skus("TG-SEA", None) == ["TG-SEA"]
    assert resolve_to_ms_skus("TG128X2-B", 1234) == ["TG128X2-B"]
    # Новые товары в будущем — тоже passthrough, автоматически пойдут в API-поиск
    assert resolve_to_ms_skus("TG-BRAND-NEW-2027", None) == ["TG-BRAND-NEW-2027"]


def test_resolve_family_pack_black_by_sku():
    """Family Pack чёрный → 2 × TG128X3-B."""
    assert resolve_to_ms_skus("TG-FPB", None) == ["TG128X3-B", "TG128X3-B"]


def test_resolve_family_pack_white_by_sku():
    """Family Pack белый → TG128X3-B + TG130X3-B."""
    assert resolve_to_ms_skus("TG-FPW", None) == ["TG128X3-B", "TG130X3-B"]


def test_resolve_family_pack_color_by_sku():
    """Family Pack цветной → TG128X3-B + цветной 3-карточный."""
    assert resolve_to_ms_skus("TG-FPSEA", None) == ["TG128X3-B", "TG-SEA"]
    assert resolve_to_ms_skus("TG-FPHYPERBLUE", None) == ["TG128X3-B", "TG-HYPERBLUE"]


def test_resolve_sku_with_extra_dash():
    """SKU с лишним дефисом → правильный SKU МС."""
    assert resolve_to_ms_skus("TG-130X3-B", None) == ["TG130X3-B"]


def test_resolve_empty_sku_by_variant_id():
    """Пустой SKU + variant_id → маппинг по variant_id (список)."""
    assert resolve_to_ms_skus("", 2053393393) == ["TG128X2-B"]
    assert resolve_to_ms_skus("", 2053395137) == ["TG128X3-B", "TG130X3-B"]  # FP белый
    assert resolve_to_ms_skus("", 2053412817) == ["TG128X3-B", "TG-HYPERBLUE"]


def test_resolve_unknown_returns_empty_list():
    """Неизвестная позиция (нет SKU и неизвестный variant_id) → пустой список."""
    assert resolve_to_ms_skus("", 999999999) == []
    assert resolve_to_ms_skus("", None) == []


def test_resolve_unknown_sku_passthrough():
    """Неизвестный SKU без маппинга — возвращается как есть (единственная позиция)."""
    assert resolve_to_ms_skus("TG-NEW-2026", None) == ["TG-NEW-2026"]


def test_resolve_strips_whitespace():
    assert resolve_to_ms_skus("  TG-SEA  ", None) == ["TG-SEA"]


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


def test_delivery_sd_showroom_returns_none():
    # Самовывоз из Шоурума — атрибут "Доставка (СД)" не ставим
    assert map_insales_delivery_sd({}, "Самовывоз из Шоурума") is None


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

def test_promo_code_real_coupon_format():
    """Реальный формат из InSales API (заказ #17665): description='Скидка по купону <CODE>'."""
    discounts = [{
        "description": "Скидка по купону tangem2026",
        "discount_code_id": 39516505,
    }]
    assert extract_insales_promo_code(discounts) == "tangem2026"


def test_promo_code_empty():
    assert extract_insales_promo_code([]) is None


def test_promo_code_unparseable_description_returns_none():
    """Description в неожиданном формате — None (с warning в лог)."""
    discounts = [{
        "description": "Какой-то нестандартный текст",
        "discount_code_id": 123,
    }]
    assert extract_insales_promo_code(discounts) is None


def test_promo_code_from_real_fixture():
    """Полный заказ из InSales API (#17665) — промокод tangem2026 и цена со скидкой."""
    fixture = os.path.join(FIXTURES_DIR, "insales_order_with_promo.json")
    if not os.path.exists(fixture):
        pytest.skip("Нет фикстуры заказа с промокодом")
    with open(fixture, encoding="utf-8") as f:
        order_data = json.load(f)
    cfg = make_config()
    order = normalize_insales_order(order_data, cfg)

    assert order.promo_code == "tangem2026"
    # Family Pack TG-FPSKY → 2 позиции (TG128X3-B + TG-SKY) на 1 товар InSales.
    # sale_price=13690, quantity=3, discounts_amount=4107 (10% на строку).
    # Цена со скидкой за Family Pack: (13690*3 - 4107)/3 = 12321 за штуку.
    # В МС делим на 2 позиции: 12321/2 = 6160.50 → 6160 + 6161 копеек при per-cent split.
    # Проверяем суммарную выручку (qty × price) по товарным позициям:
    total_items_cents = sum(li.price_cents * li.quantity for li in order.line_items)
    assert total_items_cents == 3696300  # 36 963 ₽ — совпадает с total_price InSales

    # estimated_cost = сумма товаров со скидкой, целые рубли
    assert order.estimated_cost == "36963"


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
    # Доставка + наценка = 531 + 316.55 = 847.55 → округляем до целых рублей
    assert order.delivery_cost_attr_value == "848"
    # total_price=6647.55 → округляем до целых рублей
    assert order.total_to_pay == "6648"
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
