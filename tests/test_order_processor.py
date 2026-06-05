# Тесты обработчика заказов

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from woo_moysklad.exceptions import CounterpartyError, OrderProcessingError
from woo_moysklad.core.order_processor import OrderProcessor, _to_ms_moment


# --- _to_ms_moment (формат даты для платежа МС) ---

def test_ms_moment_insales_with_tz():
    # InSales paid_at с таймзоной → без таймзоны, формат МС
    assert _to_ms_moment("2026-06-05T10:27:00+03:00") == "2026-06-05 10:27:00"

def test_ms_moment_wc_no_tz():
    assert _to_ms_moment("2026-06-05T10:27:00") == "2026-06-05 10:27:00"

def test_ms_moment_empty():
    assert _to_ms_moment(None) is None
    assert _to_ms_moment("") is None

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_order.json")


@pytest.fixture
def sample_order():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def make_config():
    """Минимальная конфигурация для тестов."""
    cfg = MagicMock()
    cfg.MS_ORGANIZATION_ID = "org-uuid"
    cfg.MS_STORE_ID = "store-uuid"
    cfg.MS_STORE_OPENED_ID = "store-opened-uuid"
    cfg.MS_CURRENCY_RUB_ID = "rub-uuid"
    cfg.MS_SALES_CHANNEL_ID = "channel-uuid"
    cfg.MS_STATE_NEW_LEAD_ID = "state-uuid"
    cfg.MS_ATTR_ORDER_NUMBER_ID = "attr-order-num"
    cfg.MS_ATTR_PAYMENT_METHOD_ID = "attr-pay-method"
    cfg.MS_ATTR_PAYMENT_TYPE_ID = "attr-pay-type"
    cfg.MS_ATTR_PROMO_CODE_ID = "attr-promo"
    cfg.MS_ATTR_DELIVERY_SD_ID = "attr-del-sd"
    cfg.MS_ATTR_DELIVERY_TYPE_ID = "attr-del-type"
    cfg.MS_ATTR_PVZ_CODE_ID = "attr-pvz"
    cfg.MS_ATTR_DELIVERY_COST_ID = "attr-del-cost"
    cfg.MS_ATTR_ESTIMATED_COST_ID = "attr-est-cost"
    cfg.MS_ATTR_TOTAL_TO_PAY_ID = "attr-total"
    cfg.MS_ATTR_COURIER_COMMENT_ID = "attr-comment"
    cfg.MS_CUSTOMENTITY_DELIVERY_SD_ID = "ce-del-sd"
    cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID = "ce-pay-type"
    cfg.MS_PAYMENT_TYPE_PREPAID_ID = "pt-prepaid"
    cfg.MS_PAYMENT_TYPE_NONCASH_ID = "pt-noncash"
    cfg.MS_DELIVERY_SD_CDEK_ID = "sd-cdek"
    cfg.MS_DELIVERY_SD_YANDEX_ID = "sd-yandex"
    return cfg


def make_processor(find_existing=None, cp_meta=None, positions=None, ms_post_result=None):
    """Создать OrderProcessor с моками."""
    config = make_config()
    ms = MagicMock()
    ms.make_meta.return_value = {"meta": {"href": "...", "type": "test", "mediaType": "application/json"}}
    ms.make_state_meta.return_value = {"meta": {"href": "...", "type": "state", "mediaType": "application/json"}}
    ms.find_by_filter.return_value = [find_existing] if find_existing else []
    ms.post.return_value = ms_post_result or {"id": "new-order", "name": "00001", "meta": {}}

    cp = MagicMock()
    cp.find_or_create.return_value = cp_meta or {"meta": {"href": "...", "type": "counterparty"}}
    cp.find_or_create_from_normalized.return_value = cp_meta or {"meta": {"href": "...", "type": "counterparty"}}

    pm = MagicMock()
    default_positions = positions or {
        "regular": [{"quantity": 1, "price": 100000, "discount": 0, "vat": 0,
                     "assortment": {"meta": {"type": "product", "href": "..."}}}],
        "opened": [],
        "services": [],
    }
    pm.build_positions_from_normalized.return_value = default_positions

    return OrderProcessor(config, ms, cp, pm), ms, cp, pm


def test_create_new_order(sample_order):
    """Создание нового заказа — POST в МС."""
    processor, ms, cp, pm = make_processor()
    results = processor.process_order(sample_order)
    ms.post.assert_called_once()
    assert results[0]["name"] == "00001"


def test_skip_duplicate(sample_order):
    """Дубликат — пропускаем, никогда не обновляем."""
    existing = {"id": "existing-id", "name": "00099", "meta": {}}
    processor, ms, cp, pm = make_processor(find_existing=existing)
    results = processor.process_order(sample_order)
    ms.post.assert_not_called()
    ms.put.assert_not_called()
    assert results[0]["name"] == "00099"


def test_counterparty_error_raises(sample_order):
    """Ошибка контрагента → OrderProcessingError."""
    processor, ms, cp, pm = make_processor()
    cp.find_or_create_from_normalized.side_effect = CounterpartyError("test error")
    with pytest.raises(OrderProcessingError):
        processor.process_order(sample_order)


def test_positions_passed_to_body(sample_order):
    """Позиции передаются в тело запроса."""
    fake_positions = {
        "regular": [{"quantity": 1, "price": 683100, "assortment": {"meta": {"type": "product"}}}],
        "opened": [],
        "services": [],
    }
    processor, ms, cp, pm = make_processor(positions=fake_positions)
    processor.process_order(sample_order)

    call_data = ms.post.call_args[0][1]
    assert "positions" in call_data
    assert len(call_data["positions"]) == 1


def test_mixed_order_creates_two_orders(sample_order):
    """Смешанный заказ (обычные + из видеообзора) → 2 заказа в МС."""
    fake_positions = {
        "regular": [{"quantity": 1, "price": 100000, "assortment": {"meta": {"type": "product"}}}],
        "opened": [{"quantity": 1, "price": 50000, "assortment": {"meta": {"type": "product"}}}],
        "services": [{"quantity": 1, "price": 30000, "assortment": {"meta": {"type": "service"}}}],
    }
    processor, ms, cp, pm = make_processor(positions=fake_positions)
    ms.post.side_effect = [
        {"id": "order-1", "name": "00001", "meta": {}},
        {"id": "order-2", "name": "00002", "meta": {}},
    ]

    results = processor.process_order(sample_order)

    assert ms.post.call_count == 2
    assert len(results) == 2

    # Первый заказ: обычные товары + услуги
    body1 = ms.post.call_args_list[0][0][1]
    assert len(body1["positions"]) == 2  # 1 regular + 1 service

    # Второй заказ: только товары из видеообзора
    body2 = ms.post.call_args_list[1][0][1]
    assert len(body2["positions"]) == 1  # 1 opened, no services


def test_only_opened_creates_one_order(sample_order):
    """Только товары из видеообзора + услуги → 1 заказ, склад вскрытые."""
    fake_positions = {
        "regular": [],
        "opened": [{"quantity": 1, "price": 50000, "assortment": {"meta": {"type": "product"}}}],
        "services": [{"quantity": 1, "price": 30000, "assortment": {"meta": {"type": "service"}}}],
    }
    processor, ms, cp, pm = make_processor(positions=fake_positions)

    results = processor.process_order(sample_order)

    assert ms.post.call_count == 1
    assert len(results) == 1

    # Позиции: opened + services
    body = ms.post.call_args[0][1]
    assert len(body["positions"]) == 2


def test_mixed_order_number_suffix(sample_order):
    """Второй заказ (из видеообзора) получает суффикс _1 в номере."""
    from woo_moysklad.core.order_processor import _TEST_ORDER_SUFFIX

    fake_positions = {
        "regular": [{"quantity": 1, "price": 100000, "assortment": {"meta": {"type": "product"}}}],
        "opened": [{"quantity": 1, "price": 50000, "assortment": {"meta": {"type": "product"}}}],
        "services": [],
    }
    processor, ms, cp, pm = make_processor(positions=fake_positions)
    ms.post.side_effect = [
        {"id": "order-1", "name": "00001", "meta": {}},
        {"id": "order-2", "name": "00002", "meta": {}},
    ]

    results = processor.process_order(sample_order)

    # Проверяем атрибуты — номер заказа
    order_id = str(sample_order["id"])
    sfx = _TEST_ORDER_SUFFIX

    body1 = ms.post.call_args_list[0][0][1]
    attrs1 = body1["attributes"]
    order_num_attr1 = [a for a in attrs1
                       if "attr-order-num" in a["meta"]["href"]][0]
    assert order_num_attr1["value"] == f"{order_id}{sfx}"

    body2 = ms.post.call_args_list[1][0][1]
    attrs2 = body2["attributes"]
    order_num_attr2 = [a for a in attrs2
                       if "attr-order-num" in a["meta"]["href"]][0]
    assert order_num_attr2["value"] == f"{order_id}{sfx}_1"
