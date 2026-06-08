# Тесты нормализатора uCoz

import json
import os
from unittest.mock import MagicMock

import pytest

from woo_moysklad.ucoz.normalizer import (
    _UCOZ_GOODS_ID_TO_MS_SKU,
    _UCOZ_ORDER_SUFFIX,
    build_ucoz_customer,
    build_ucoz_line_items,
    is_ucoz_paid,
    map_ucoz_delivery,
    map_ucoz_payment,
    normalize_ucoz_order,
    parse_ucoz_delivery_data,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE_PATH = os.path.join(FIXTURES_DIR, "ucoz_order_sample.json")


@pytest.fixture
def sample_ucoz_order():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def make_config():
    return MagicMock()


# --- _UCOZ_GOODS_ID_TO_MS_SKU dictionary ---

def test_dictionary_excludes_coinkite_brands():
    """Coinkite целиком исключён (Coldcard MK3/MK4/MK5/Q, SATSCARD, TAPSIGNER, ...)."""
    coinkite_ids = (
        [118] + list(range(119, 129)) + [153, 178, 192, 193, 201, 204]
        + list(range(249, 255)) + list(range(1106, 1115))
    )
    for cid in coinkite_ids:
        assert cid not in _UCOZ_GOODS_ID_TO_MS_SKU, f"Coinkite goods_id {cid} должен быть исключён"


def test_dictionary_excludes_secux_satochip_yubikey_garbage():
    """SecuX (137-140), Satochip Satodime (260), мусорный Yubikey 165."""
    for excluded in [137, 138, 139, 140, 260, 165]:
        assert excluded not in _UCOZ_GOODS_ID_TO_MS_SKU


def test_dictionary_includes_known_tangem_products():
    assert _UCOZ_GOODS_ID_TO_MS_SKU[108] == "TG128X3-B"   # Tangem 2.0 (3 карты)
    assert _UCOZ_GOODS_ID_TO_MS_SKU[109] == "TG130X3-B"   # Tangem 2.0 WHITE (3 карты)
    assert _UCOZ_GOODS_ID_TO_MS_SKU[270] == "TG-TRON"     # Tangem Tron
    assert _UCOZ_GOODS_ID_TO_MS_SKU[1115] == "TG-SAKURA-SL"


def test_dictionary_includes_fuzzy_matched_items():
    """20 нечётких matches (score<1.0) после ручной проверки тоже в словаре."""
    assert _UCOZ_GOODS_ID_TO_MS_SKU[96] == "HW-47"        # fuzzy 0.86
    assert _UCOZ_GOODS_ID_TO_MS_SKU[77] == "HW-84"        # fuzzy 0.96
    assert _UCOZ_GOODS_ID_TO_MS_SKU[258] == "TG-VIVID"    # fuzzy 0.96
    assert _UCOZ_GOODS_ID_TO_MS_SKU[273] == "TG-SKY"      # fuzzy 0.97


def test_dictionary_size_matches_csv_minus_exclusions():
    """242 позиции в CSV − 38 исключений = 204."""
    assert len(_UCOZ_GOODS_ID_TO_MS_SKU) == 204


# --- build_ucoz_customer ---

def test_customer_email_uses_telegram_username():
    """TODO Q6: tg-username (без @) → email = '@{user}'."""
    customer = build_ucoz_customer({
        "field_full_name": "Иван", "field_phone": "79991234567",
        "user": "ivanov", "field_email": "should_be_ignored@example.com",
    })
    assert customer.email == "@ivanov"


def test_customer_email_falls_back_to_field_email():
    """Если user пустой — fallback на field_email."""
    customer = build_ucoz_customer({
        "field_full_name": "Иван", "field_phone": "+7 999",
        "user": "", "field_email": "ivan@example.com",
    })
    assert customer.email == "ivan@example.com"


def test_customer_full_name_default():
    customer = build_ucoz_customer({})
    assert customer.full_name == "Без имени"
    assert customer.phone == ""
    assert customer.email == ""


# --- build_ucoz_line_items ---

def test_line_items_resolve_known_goods_id():
    items = build_ucoz_line_items([
        {"goods_id": 134, "name": "Blockstream Jade Orange", "price_raw": 8990, "count": 1},
    ])
    assert len(items) == 1
    assert items[0].sku == "HW-06"
    assert items[0].price_cents == 899000
    assert items[0].quantity == 1


def test_line_items_skip_unknown_goods_id():
    """Неизвестный goods_id (включая исключённые Coinkite/SecuX) → пропуск."""
    items = build_ucoz_line_items([
        {"goods_id": 118, "name": "Coldcard MK3", "price_raw": 21990, "count": 1},   # Coinkite
        {"goods_id": 137, "name": "SecuX v20", "price_raw": 9990, "count": 1},       # SecuX
        {"goods_id": 99999, "name": "noname", "price_raw": 100, "count": 1},
    ])
    assert items == []


def test_line_items_handle_string_price():
    """uCoz иногда передаёт price_raw как строку."""
    items = build_ucoz_line_items([
        {"goods_id": 108, "name": "Tangem", "price_raw": "7590", "count": 2},
    ])
    assert len(items) == 1
    assert items[0].price_cents == 759000
    assert items[0].quantity == 2


# --- map_ucoz_payment ---

def test_payment_1_at_pickup_is_cod():
    """Подтверждено заказом #392: payment_id=1 → 'При получении', noncash."""
    title, type_key = map_ucoz_payment(1)
    assert type_key == "noncash"
    assert "получении" in title.lower()


def test_payment_7_card_to_courier_is_prepaid():
    """Подтверждено заказом #393: payment_id=7 → 'На карту' (prepaid, не COD)."""
    title, type_key = map_ucoz_payment(7)
    assert type_key == "prepaid"
    assert title == "На карту"


def test_payment_3_yandexkassa():
    title, type_key = map_ucoz_payment(3)
    assert type_key == "prepaid"
    assert "Касса" in title


def test_payment_6_crypto_wallet():
    """TODO Q7: payment_id=6 → 'Wallet' (prepaid)."""
    title, type_key = map_ucoz_payment(6)
    assert title == "Wallet"
    assert type_key == "prepaid"


def test_payment_unknown_returns_placeholder():
    title, type_key = map_ucoz_payment(99)
    assert "99" in title
    assert type_key is None


# --- map_ucoz_delivery ---

def test_delivery_1_pickup_office():
    """Подтверждено заказом #394: delivery_id=1 → Самовывоз из офиса (sd_key=None, pvz)."""
    name, sd_key, type_key, fixed_price = map_ucoz_delivery(1)
    assert "Самовывоз" in name
    assert sd_key is None      # самовывоз — не СДЭК и не Яндекс
    assert type_key == "pvz"   # самовывоз классифицируется как ПВЗ (как в InSales)
    assert fixed_price is None


def test_delivery_3_cdek_pvz():
    """Подтверждено заказом #392: delivery_id=3 → СДЭК ПВЗ, цена из delivery_tax."""
    name, sd_key, type_key, fixed_price = map_ucoz_delivery(3)
    assert sd_key == "cdek"
    assert type_key == "pvz"
    assert "CDEK" in name or "СДЭК" in name
    assert fixed_price is None  # цену берём из uCoz delivery_tax


def test_delivery_4_courier_msk_has_fixed_1000_rub():
    """Подтверждено заказом #393: delivery_id=4 → курьер МСК, фикс. 1000 ₽
    (uCoz всегда передаёт delivery_tax=0, реальная цена в WC — 1000 ₽).
    """
    name, sd_key, type_key, fixed_price = map_ucoz_delivery(4)
    assert sd_key == "yandex"
    assert type_key == "courier"
    assert name == "Курьерская доставка по Москве"
    assert fixed_price == 100_000  # 1000 ₽ в копейках


def test_delivery_unknown_returns_defaults():
    """Неизвестные delivery_id возвращают дефолтные значения."""
    name, sd_key, type_key, fixed_price = map_ucoz_delivery(99)
    assert name == "Доставка"
    assert sd_key is None
    assert type_key is None
    assert fixed_price is None


# --- parse_ucoz_delivery_data ---

def test_parse_delivery_data_cdek_pvz():
    """СДЭК ПВЗ: delivery_pi (код) + delivery_pn (адрес) + delivery_cn/ci (город)."""
    raw = ('{"delivery_pn":"Москва, ул. Генерала Тюленева, 25А",'
           '"delivery_pi":"MSK2331","delivery_ci":"44","delivery_cn":"Москва"}')
    parsed = parse_ucoz_delivery_data(raw)
    assert parsed["delivery_pi"] == "MSK2331"
    assert "Тюленева" in parsed["delivery_pn"]


def test_parse_delivery_data_empty():
    assert parse_ucoz_delivery_data("{}") == {}
    assert parse_ucoz_delivery_data(None) == {}
    assert parse_ucoz_delivery_data("") == {}


def test_parse_delivery_data_invalid_json_returns_empty():
    assert parse_ucoz_delivery_data("not json") == {}


# --- is_ucoz_paid ---

def test_is_paid_when_inv_params_starts_with_payment_accepted():
    assert is_ucoz_paid("payment_accepted#yandexkassa_payment_id=...") is True


def test_is_not_paid_for_other_inv_params():
    assert is_ucoz_paid("payment_pending#...") is False
    assert is_ucoz_paid("") is False
    assert is_ucoz_paid(None) is False


# --- normalize_ucoz_order (full integration) ---

def test_normalize_sample_fixture(sample_ucoz_order):
    """Базовая фикстура: один товар, оплачено, есть комментарий."""
    cfg = make_config()
    normalized = normalize_ucoz_order(sample_ucoz_order, cfg)

    assert normalized.source == "ucoz"
    assert normalized.order_id == "391"
    assert normalized.order_number == f"391{_UCOZ_ORDER_SUFFIX}"
    assert normalized.is_paid is True
    assert normalized.is_cod is False
    assert normalized.organization_id is None  # дефолт = ИП Перфилов
    assert normalized.promo_code is None
    assert normalized.cod_margin_amount_cents == 0
    assert normalized.total_to_pay is None

    # Клиент: tg-username из user → email
    assert normalized.customer.full_name == "Тестовый Клиент"
    assert normalized.customer.email == "@testuser"
    assert normalized.customer.phone == "79999999999"

    # Товары: один Blockstream Jade Orange (HW-06), цена 8990
    assert len(normalized.line_items) == 1
    assert normalized.line_items[0].sku == "HW-06"
    assert normalized.line_items[0].price_cents == 899000

    # Платёж: payment_id=3 → "Яндекс Касса", prepaid
    assert "Касса" in normalized.payment_title
    assert normalized.payment_type_key == "prepaid"

    # delivery_id=4 → курьер по Москве (yandex). uCoz даёт delivery_tax=0, но
    # для этого типа задана фикс. цена 1000 ₽ → услуга всё равно создаётся.
    assert normalized.delivery_sd_key == "yandex"
    assert normalized.delivery_type_key == "courier"
    assert len(normalized.delivery_services) == 1
    assert normalized.delivery_services[0].price_cents == 100_000

    # Адрес: из field_delivery_address как есть
    assert normalized.shipment_address == "Москва, Тестовая улица 1"

    # Комментарий
    assert normalized.description == "Тестовый комментарий"

    # Стоимости: 1 × 8990 → estimated_cost = "8990", delivery = 1000 (фикс. для МСК-курьера)
    assert normalized.estimated_cost == "8990"
    assert normalized.delivery_cost_attr_value == "1000"


def test_normalize_with_paid_delivery_unknown_delivery_id():
    """Если delivery_tax > 0 и delivery_id неизвестен — услуга с дефолтным именем 'Доставка'."""
    raw = {
        "order": {
            "id": 500, "user": "u", "field_full_name": "Иван", "field_phone": "+7 999",
            "field_email": "i@e.ru", "field_delivery_address": "СПб, ул. Тест 1",
            "field_order_comment": "", "payment_id": 3, "delivery_id": 99,
            "delivery_tax": "490.00", "amount": "9480.00",
            "inv_params": "payment_accepted#yandexkassa",
        },
        "goods": [{"goods_id": 108, "name": "Tangem 2.0", "price_raw": 8990, "count": 1}],
    }
    normalized = normalize_ucoz_order(raw, make_config())
    assert len(normalized.delivery_services) == 1
    assert normalized.delivery_services[0].name == "Доставка"
    assert normalized.delivery_services[0].price_cents == 49000
    assert normalized.delivery_cost_attr_value == "490"


def test_normalize_marks_unpaid_when_inv_params_does_not_start_with_payment_accepted():
    raw = {
        "order": {
            "id": 501, "user": "u", "field_full_name": "Иван", "field_phone": "+7",
            "field_email": "", "field_delivery_address": "",
            "field_order_comment": "", "payment_id": 3, "delivery_id": 4,
            "delivery_tax": "0", "amount": "8990.00",
            "inv_params": "payment_pending",
        },
        "goods": [{"goods_id": 108, "name": "Tangem", "price_raw": 8990, "count": 1}],
    }
    assert normalize_ucoz_order(raw, make_config()).is_paid is False


def test_normalize_unknown_goods_does_not_crash():
    """Полностью неизвестные товары → line_items пуст, нормалайзер не падает.

    estimated_cost при этом всё равно считается из сырых goods (паритет с
    insales_normalizer). Это нормально: дальше OrderProcessor увидит пустой
    список позиций и выкинет OrderProcessingError 'Заказ без товаров'.
    """
    raw = {
        "order": {
            "id": 502, "user": "x", "field_full_name": "X", "field_phone": "1",
            "field_email": "", "field_delivery_address": "addr",
            "field_order_comment": "", "payment_id": 3, "delivery_id": 4,
            "delivery_tax": "0", "amount": "0",
            "inv_params": "payment_accepted",
        },
        "goods": [{"goods_id": 99999, "name": "????", "price_raw": 1, "count": 1}],
    }
    normalized = normalize_ucoz_order(raw, make_config())
    assert normalized.line_items == []
    assert normalized.order_id == "502"


def test_normalize_real_cod_cdek_pvz_fixture():
    """Реальный заказ #392: COD (payment_id=1) + СДЭК ПВЗ (delivery_id=3, delivery_pi=MSK2331)."""
    path = os.path.join(FIXTURES_DIR, "ucoz_cod_cdek_pvz.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    n = normalize_ucoz_order(raw, make_config())

    # Базовое
    assert n.order_id == "392"
    assert n.order_number == f"392{_UCOZ_ORDER_SUFFIX}"
    assert n.is_paid is False        # inv_params отсутствует — не оплачено
    assert n.is_cod is True          # payment_id=1 → noncash → COD

    # Платёж
    assert n.payment_type_key == "noncash"
    assert "получении" in n.payment_title.lower()

    # Доставка СДЭК ПВЗ
    assert n.delivery_sd_key == "cdek"
    assert n.delivery_type_key == "pvz"
    assert n.pvz_code == "MSK2331"
    assert "Тюленева" in (n.shipment_address or "")

    # Услуга доставки 195 ₽
    assert len(n.delivery_services) == 1
    assert n.delivery_services[0].price_cents == 19500

    # Товары: 14990 + 7590×2 = 30170
    assert len(n.line_items) == 2
    skus = {item.sku for item in n.line_items}
    assert skus == {"HW-26", "TG130X3-B"}
    assert n.estimated_cost == "30170"
    assert n.delivery_cost_attr_value == "195"

    # COD-наценка: 4% от 30170 = 1206.8 → 1207 ₽ (округление)
    assert n.cod_margin_amount_cents == 120700

    # Сумма к оплате получателю: товары + доставка + наценка = 30170 + 195 + 1207 = 31572
    assert n.total_to_pay == "31572"

    # Комментарий: текст покупателя + форматированная справка СДЭК
    assert "тест" in n.description
    assert "MSK2331" in n.description  # field_cdek_info

    # Контрагент: telegram-username → email
    assert n.customer.email == "@koshkatarina"
    assert n.customer.full_name == "Katerina Koshenkova"


def test_normalize_real_card_courier_msk_fixture():
    """Реальный заказ #393: На карту (prepaid) + курьер по Москве (бесплатно).

    Ключевые отличия от COD-кейса:
    - is_cod=False, наценка 4% НЕ добавляется (предоплата картой)
    - delivery_tax=0 → услуга доставки не создаётся
    - delivery_data="{}" → pvz_code не извлекается
    - адрес берётся из field_delivery_address
    """
    path = os.path.join(FIXTURES_DIR, "ucoz_card_courier_msk.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    n = normalize_ucoz_order(raw, make_config())

    assert n.order_id == "393"
    assert n.is_paid is False             # inv_params=None
    assert n.is_cod is False              # prepaid (не наложенный платёж)
    assert n.cod_margin_amount_cents == 0
    assert n.total_to_pay is None         # для prepaid не нужен

    # Платёж: На карту → prepaid
    assert n.payment_title == "На карту"
    assert n.payment_type_key == "prepaid"

    # Доставка: курьер по Москве, yandex
    assert n.delivery_sd_key == "yandex"
    assert n.delivery_type_key == "courier"
    assert n.pvz_code is None
    assert n.shipment_address == "бутлерова 17"

    # uCoz передаёт delivery_tax=0, но услуга всё равно добавляется фикс. ценой 1000 ₽
    assert len(n.delivery_services) == 1
    assert n.delivery_services[0].name == "Курьерская доставка по Москве"
    assert n.delivery_services[0].price_cents == 100_000
    assert n.delivery_cost_attr_value == "1000"

    # Товары: 6990 + 4990×3 = 21960. Каждый goods_id → одна позиция с quantity.
    assert n.estimated_cost == "21960"
    assert len(n.line_items) == 2
    by_sku = {item.sku: item for item in n.line_items}
    assert by_sku["U2F-06"].quantity == 1   # Yubikey 5C NFC
    assert by_sku["U2F-06"].price_cents == 699000
    assert by_sku["SEED-12"].quantity == 3  # Keystone Tablet × 3
    assert by_sku["SEED-12"].price_cents == 499000


def test_normalize_real_crypto_pickup_fixture():
    """Реальный заказ #394: крипта (Wallet) + самовывоз из офиса.

    Особенности:
    - payment_topay="535 USDT " — формат отличается от рублёвых заказов (не парсим)
    - delivery_id=1 (самовывоз) → услуга в МС с ценой 0 (как в InSales)
    - field_delivery_address=None, delivery_data={} → shipment_address=None
    - is_paid=False (inv_params=None) — менеджер пометит вручную после поступления крипты
    - Tangem Ring приходит как 3 ОТДЕЛЬНЫЕ позиции в goods (uCoz не группирует)
      — прокидываем в МС как 3 строки (менеджер при необходимости объединит)
    """
    path = os.path.join(FIXTURES_DIR, "ucoz_crypto_pickup.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    n = normalize_ucoz_order(raw, make_config())

    assert n.order_id == "394"
    assert n.is_paid is False
    assert n.is_cod is False
    assert n.cod_margin_amount_cents == 0

    # Платёж: крипта → Wallet, prepaid
    assert n.payment_title == "Wallet"
    assert n.payment_type_key == "prepaid"

    # Доставка: самовывоз
    assert n.delivery_sd_key is None
    assert n.delivery_type_key == "pvz"
    assert n.shipment_address is None
    assert n.pvz_code is None

    # Услуга самовывоза в позициях с ценой 0 (паритет с InSales)
    assert len(n.delivery_services) == 1
    assert n.delivery_services[0].name == "Самовывоз из офиса Sunscrypt"
    assert n.delivery_services[0].price_cents == 0
    assert n.delivery_cost_attr_value is None

    # 4 позиции в goods (Tangem Ring × 3 как 3 отдельных + Hyperblue × 1)
    assert len(n.line_items) == 4
    skus = sorted(item.sku for item in n.line_items)
    assert skus == ["TG-HYPERBLUE", "TG-RING", "TG-RING", "TG-RING"]
    assert n.estimated_cost == "53460"  # 14990×3 + 8490 = 53460


def test_cod_adds_4_percent_margin_from_items_total():
    """Для COD рассчитываем наценку 4% от items_total (доставка не учитывается)."""
    raw = {
        "order": {
            "id": 600, "user": "u", "field_full_name": "X", "field_phone": "1",
            "field_email": "", "field_delivery_address": "addr",
            "field_order_comment": "", "payment_id": 1, "delivery_id": 3,
            "delivery_tax": "300.00", "amount": "10000.00",
            "inv_params": None, "delivery_data": "{}",
        },
        "goods": [{"goods_id": 108, "name": "Tangem", "price_raw": 10000, "count": 1}],
    }
    n = normalize_ucoz_order(raw, make_config())
    assert n.is_cod is True
    assert n.cod_margin_amount_cents == 40000   # 10000 × 4% = 400 ₽ × 100
    assert n.total_to_pay == "10700"             # 10000 + 300 + 400


def test_non_cod_has_no_margin():
    """Для предоплаты (Яндекс Касса) наценки нет."""
    raw = {
        "order": {
            "id": 601, "user": "u", "field_full_name": "X", "field_phone": "1",
            "field_email": "", "field_delivery_address": "",
            "field_order_comment": "", "payment_id": 3, "delivery_id": 3,
            "delivery_tax": "0", "amount": "8990.00",
            "inv_params": "payment_accepted", "delivery_data": "{}",
        },
        "goods": [{"goods_id": 108, "name": "Tangem", "price_raw": 8990, "count": 1}],
    }
    n = normalize_ucoz_order(raw, make_config())
    assert n.is_cod is False
    assert n.cod_margin_amount_cents == 0
    assert n.total_to_pay is None


def test_normalize_handles_missing_payment_id():
    raw = {
        "order": {
            "id": 503, "user": "x", "field_full_name": "X", "field_phone": "1",
            "field_email": "", "field_delivery_address": "",
            "field_order_comment": "", "delivery_id": 4,
            "delivery_tax": "0", "amount": "0", "inv_params": "",
        },
        "goods": [],
    }
    normalized = normalize_ucoz_order(raw, make_config())
    assert normalized.payment_type_key is None
