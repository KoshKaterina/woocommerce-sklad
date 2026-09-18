"""Синтетические заказы: никакого доступа к сайтам или рабочему МС."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from woo_moysklad.config import Config
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.product_matcher import ProductMatcher
from woo_moysklad.woocommerce.normalizer import normalize_wc_order
from woo_moysklad.woocommerce.preorder import PREORDER_META_KEY


def line(product_id, sku, name, *, preorder, qty=1, variation_id=0):
    item = {"product_id": product_id, "variation_id": variation_id,
            "sku": sku, "name": name, "quantity": qty, "price": "1000"}
    item["meta_data"] = [{"key": PREORDER_META_KEY, "value": {
        "v": 1, "preorder": preorder, "product_id": product_id,
        "variation_id": variation_id, "sku": sku, "quantity": qty,
    }}]
    return item


def order(*items):
    return {"id": 77001, "billing": {"first_name": "Тест", "phone": "000"},
            "customer_note": "Комментарий покупателя", "status": "pending",
            "payment_method_title": "Другой способ", "line_items": list(items),
            "shipping_lines": []}


def setup_processor(*, enabled=True, existing=None):
    cfg = Config(MS_ATTR_PREORDER_SUMMARY_ID="preorder-test-field" if enabled else "",
                 MS_ORGANIZATION_ID="org", MS_STORE_ID="regular-store",
                 MS_STORE_OPENED_ID="opened-store", MS_CURRENCY_RUB_ID="rub",
                 MS_ATTR_ORDER_NUMBER_ID="order-number-test-field")
    ms = MagicMock()
    ms.find_by_filter.side_effect = lambda entity, query: (
        [existing] if entity == "customerorder" and existing else
        [{"id": "synthetic-product", "meta": {"href": "fake://product"}}] if entity == "product" else []
    )
    ms.make_meta.side_effect = lambda entity, identifier: {"meta": {"type": entity, "id": identifier}}
    ms.post.return_value = {"id": "created"}
    cp = MagicMock()
    cp.find_or_create_from_normalized.return_value = {"meta": {"type": "counterparty"}}
    return OrderProcessor(cfg, ms, cp, ProductMatcher(ms)), ms, cp


def preorder_attributes(body):
    return [attr["value"] for attr in body.get("attributes", [])
            if "preorder-test-field" in attr["meta"]["href"]]


@pytest.mark.parametrize("preorders, expected", [
    ([False], "Тип: Заказ\nОжидаемые позиции: нет"),
    ([True], "Тип: Предзаказ\nОжидаемые позиции:\n- Tangem Red [SKU: TR] × 2"),
    ([False, True], "Тип: Предзаказ\nОжидаемые позиции:\n- Tangem Blue [SKU: TB] × 1"),
])
def test_order_type_and_exact_waiting_lines(preorders, expected):
    products = [line(12, "TR", "Tangem Red", preorder=preorders[0], qty=2, variation_id=13)]
    if len(preorders) == 2:
        products.append(line(14, "TB", "Tangem Blue", preorder=preorders[1]))
    processor, ms, _ = setup_processor()
    processor.process_order(order(*products))
    ms.post.assert_called_once()
    body = ms.post.call_args.args[1]
    assert preorder_attributes(body) == [expected]
    assert "Woo товар #" not in preorder_attributes(body)[0]
    assert "вариация #" not in preorder_attributes(body)[0]
    assert body["description"] == "Комментарий покупателя"
    assert len(body["positions"]) == len(products)
    assert all("courier" not in attr["meta"]["href"] for attr in body["attributes"])


def test_multiple_preorder_lines_and_long_text_are_not_truncated():
    long_title = "Модель " + "К" * 310
    processor, ms, _ = setup_processor()
    processor.process_order(order(line(12, "TR", long_title, preorder=True),
                                  line(14, "TB", "Blue", preorder=True, qty=3)))
    value = preorder_attributes(ms.post.call_args.args[1])[0]
    assert long_title in value and "× 3" in value and len(value) > 255


def test_snapshot_sku_not_replaced_by_current_rest_sku():
    item = line(12, "FROZEN", "Frozen title", preorder=True)
    item["sku"] = "CURRENT"
    normalized = normalize_wc_order(order(item), preorder_enabled=True)
    assert normalized.line_items[0].sku == "FROZEN"
    assert normalized.line_items[0].title == "Frozen title"


def test_unknown_legacy_does_not_claim_ordinary_even_when_enabled():
    item = line(12, "TR", "Red", preorder=True)
    item["meta_data"] = [{"key": "Backordered", "value": "1"}]
    processor, ms, _ = setup_processor()
    processor.process_order(order(item))
    assert preorder_attributes(ms.post.call_args.args[1]) == []


def test_disabled_mode_ignores_even_invalid_snapshot_and_uses_old_sku():
    item = line(12, "FROZEN", "Red", preorder=True)
    item["meta_data"][0]["value"] = "invalid"
    item["sku"] = "CURRENT"
    processor, ms, _ = setup_processor(enabled=False)
    processor.process_order(order(item))
    assert preorder_attributes(ms.post.call_args.args[1]) == []
    assert ms.find_by_filter.call_args_list[0].args == ("product", "article=CURRENT")


def test_self_parent_snapshot_fails_before_external_calls():
    item = line(90, "SELF", "Impossible child", preorder=True, variation_id=90)
    processor, ms, cp = setup_processor()
    with pytest.raises(ValueError, match="своим родителем"):
        processor.process_order(order(item))
    ms.find_by_filter.assert_not_called()
    ms.post.assert_not_called()
    cp.find_or_create_from_normalized.assert_not_called()


@pytest.mark.parametrize("mutation", [
    lambda items: items[1].update(meta_data=[]),
    lambda items: items[0]["meta_data"].append(deepcopy(items[0]["meta_data"][0])),
    lambda items: items[0]["meta_data"][0]["value"].update(preorder="true"),
    lambda items: items[0]["meta_data"][0]["value"].update(v=2),
    lambda items: items[0]["meta_data"][0]["value"].update(quantity=99),
    lambda items: items[0]["meta_data"][0]["value"].update(variation_id=999),
    lambda items: items[0]["meta_data"][0].update(value="json string"),
])
def test_partial_duplicate_invalid_snapshot_fails_before_external_calls(mutation):
    items = [line(12, "TR", "Red", preorder=True), line(14, "TB", "Blue", preorder=False)]
    mutation(items)
    processor, ms, cp = setup_processor()
    with pytest.raises(ValueError):
        processor.process_order(order(*items))
    ms.find_by_filter.assert_not_called()
    ms.post.assert_not_called()
    cp.find_or_create_from_normalized.assert_not_called()


def test_existing_ms_order_never_receives_new_field_or_update():
    processor, ms, _ = setup_processor(existing={"id": "already-created"})
    assert processor.process_order(order(line(12, "TR", "Red", preorder=True))) == [{"id": "already-created"}]
    ms.post.assert_not_called()
    ms.put.assert_not_called()


def test_video_review_split_is_existing_exception_not_preorder_split():
    processor, ms, _ = setup_processor()
    ms.post.side_effect = [{"id": "regular"}, {"id": "opened"}]
    processor.process_order(order(
        line(12, "TR", "Red", preorder=False),
        line(14, "TB-1", "Blue из видеообзора", preorder=True)))
    assert ms.post.call_count == 2
    regular_body = ms.post.call_args_list[0].args[1]
    opened_body = ms.post.call_args_list[1].args[1]
    assert preorder_attributes(regular_body) == ["Тип: Заказ\nОжидаемые позиции: нет"]
    assert preorder_attributes(opened_body) == [
        "Тип: Предзаказ\nОжидаемые позиции:\n"
        "- Blue из видеообзора [SKU: TB-1] × 1"]
    assert regular_body["store"]["meta"]["id"] == "regular-store"
    assert opened_body["store"]["meta"]["id"] == "opened-store"
