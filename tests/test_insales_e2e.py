# End-to-end тесты обработки заказа InSales: фикстура → process_insales_order → POST в МС.
# Использует реальные нормалайзер, OrderProcessor, CounterpartyHandler, ProductMatcher;
# моки только для MoySkladClient.

import json
import os
from unittest.mock import MagicMock

import pytest

from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.product_matcher import ProductMatcher

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
SAMPLE_ORDER = os.path.join(FIXTURES_DIR, "insales_sample_order.json")
COD_ORDER = os.path.join(FIXTURES_DIR, "insales_cod_order.json")
COURIER_ORDER = os.path.join(FIXTURES_DIR, "insales_courier_order.json")
FAMILYPACK_ORDER = os.path.join(FIXTURES_DIR, "insales_familypack_order.json")


def make_config():
    cfg = MagicMock()
    cfg.MS_ORGANIZATION_ID = "org-wc"
    cfg.MS_ORGANIZATION_INSALES_ID = "org-insales"
    cfg.MS_STATE_NEW_LEAD_ID = "state-default"
    cfg.MS_STATE_INSALES_NEW_ID = "state-insales-new"
    cfg.MS_PROJECT_INSALES_ID = "project-insales"
    cfg.MS_STORE_ID = "store-main"
    cfg.MS_STORE_OPENED_ID = "store-opened"
    cfg.MS_CURRENCY_RUB_ID = "cur-rub"
    cfg.MS_SALES_CHANNEL_ID = "sc-shop"
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
    cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID = "ce-pay-type"
    cfg.MS_CUSTOMENTITY_DELIVERY_SD_ID = "ce-del-sd"
    cfg.MS_PAYMENT_TYPE_PREPAID_ID = "pt-prepaid"
    cfg.MS_PAYMENT_TYPE_NONCASH_ID = "pt-noncash"
    cfg.MS_DELIVERY_SD_CDEK_ID = "sd-cdek"
    cfg.MS_DELIVERY_SD_DOSTAVISTA_ID = "sd-dostavista"
    cfg.MS_DELIVERY_SD_SHOWROOM_ID = "sd-showroom"
    return cfg


def make_ms_client(*, customerorder_existing=None):
    """Мок MoySkladClient.

    customerorder_existing: если задан — find_by_filter("customerorder", ...) вернёт его.
    Контрагенты, товары, услуги — всегда находятся (фейковая meta).
    """
    ms = MagicMock()

    def find_by_filter(entity, filter_str):
        if entity == "counterparty":
            return [{
                "id": "cp-found",
                "companyType": "individual",
                "name": "Существующий клиент",
                "meta": {"href": "https://api/cp", "type": "counterparty",
                         "mediaType": "application/json"},
            }]
        if entity == "customerorder":
            return [customerorder_existing] if customerorder_existing else []
        if entity == "product":
            sku = filter_str.rsplit("=", 1)[-1]
            return [{
                "id": f"p-{sku}",
                "meta": {"href": f"https://api/product/{sku}", "type": "product",
                         "mediaType": "application/json"},
            }]
        if entity == "service":
            name = filter_str.rsplit("=", 1)[-1]
            return [{
                "id": f"s-{name}",
                "meta": {"href": f"https://api/service/{name}", "type": "service",
                         "mediaType": "application/json"},
            }]
        return []

    ms.find_by_filter.side_effect = find_by_filter
    ms.post.return_value = {
        "id": "ms-order-new",
        "name": "TS-NEW",
        "meta": {"href": "https://api/customerorder/new", "type": "customerorder"},
    }

    def make_meta(t, uuid):
        return {"meta": {"href": f"https://api.moysklad.ru/api/remap/1.2/entity/{t}/{uuid}",
                         "type": t, "mediaType": "application/json"}}

    def make_state_meta(et, uuid):
        return {"meta": {"href": f"https://api.moysklad.ru/api/remap/1.2/entity/{et}/metadata/states/{uuid}",
                         "type": "state", "mediaType": "application/json"}}

    ms.make_meta.side_effect = make_meta
    ms.make_state_meta.side_effect = make_state_meta
    return ms


def build_processor(ms):
    cfg = make_config()
    cp_handler = CounterpartyHandler(ms)
    pm = ProductMatcher(ms)
    return OrderProcessor(cfg, ms, cp_handler, pm), cfg


def post_calls(ms, path: str) -> list:
    return [c for c in ms.post.call_args_list if c[0][0] == path]


def attrs_index(body) -> dict:
    """Атрибуты заказа индексируем по UUID (последний сегмент href)."""
    return {a["meta"]["href"].split("/")[-1]: a["value"] for a in body.get("attributes", [])}


# --- Тесты ---

def test_e2e_sample_order_creates_in_ms():
    """Нормальный prepaid-заказ → 1 POST customerorder с правильными полями."""
    with open(SAMPLE_ORDER, encoding="utf-8") as f:
        order = json.load(f)

    ms = make_ms_client()
    processor, cfg = build_processor(ms)

    results = processor.process_insales_order(order)
    assert len(results) == 1

    calls = post_calls(ms, "entity/customerorder")
    assert len(calls) == 1
    body = calls[0][0][1]

    # Организация — ИП Абовян (InSales-специфичная)
    assert "org-insales" in body["organization"]["meta"]["href"]
    # State — InSales новый
    assert "state-insales-new" in body["state"]["meta"]["href"]
    # Project — InSales
    assert "project-insales" in body["project"]["meta"]["href"]
    # Склад — основной (нет вскрытых)
    assert "store-main" in body["store"]["meta"]["href"]

    # Адрес ПВЗ из delivery_info.outlet.address
    assert body["shipmentAddress"] == \
        "628403, Россия, Ханты-Мансийский автономный округ - Югра, Сургут, ул. Юности, 8"

    # Позиции: 1 товар (TG130X3-B после маппинга TG-130X3-B) + 1 услуга доставки
    assert len(body["positions"]) == 2
    assert any("/product/TG130X3-B" in p["assortment"]["meta"]["href"]
               for p in body["positions"])
    assert any("/service/CDEK: Самовывоз (4 дней)" in p["assortment"]["meta"]["href"]
               for p in body["positions"])

    # Атрибуты
    attrs = attrs_index(body)
    assert attrs["attr-order-num"] == "17621 Tangemshop"
    assert attrs["attr-pay-method"] == "Оплата онлайн"
    assert attrs["attr-pvz"] == "SUR18"
    # delivery_sd → cdek (customentity)
    assert isinstance(attrs["attr-del-sd"], dict)
    assert "sd-cdek" in attrs["attr-del-sd"]["meta"]["href"]
    # delivery_type → pvz = long 1 (новое поле «Вид доставки», не справочник)
    assert attrs["attr-del-type"] == 1
    # payment_type → prepaid
    assert "pt-prepaid" in attrs["attr-pay-type"]["meta"]["href"]


def test_e2e_skips_when_order_already_in_ms():
    """Если заказ уже в МС — POST customerorder НЕ вызывается, возвращается existing."""
    with open(SAMPLE_ORDER, encoding="utf-8") as f:
        order = json.load(f)

    existing = {"id": "ms-existing", "name": "TS-OLD",
                "meta": {"href": "https://api/old", "type": "customerorder"}}
    ms = make_ms_client(customerorder_existing=existing)
    processor, _ = build_processor(ms)

    results = processor.process_insales_order(order)

    assert post_calls(ms, "entity/customerorder") == []
    assert results[0] == existing


def test_e2e_cod_order_adds_margin_service():
    """COD-заказ с margin_amount > 0 → услуга 'Наценка за наложенный платеж' в позициях."""
    with open(COD_ORDER, encoding="utf-8") as f:
        order = json.load(f)

    ms = make_ms_client()
    processor, _ = build_processor(ms)
    processor.process_insales_order(order)

    body = post_calls(ms, "entity/customerorder")[0][0][1]

    # Должна быть позиция-услуга на наценку (находится через find_by_filter("service", ...))
    margin_lookups = [c for c in ms.find_by_filter.call_args_list
                      if c[0][0] == "service" and "Наценка за наложенный платеж" in c[0][1]]
    assert len(margin_lookups) >= 1

    # Атрибут "сумма к оплате получателем" заполнен (это COD)
    attrs = attrs_index(body)
    assert attrs["attr-total"] not in (None, "0")
    # payment_type → noncash (при получении)
    assert "pt-noncash" in attrs["attr-pay-type"]["meta"]["href"]


def test_e2e_familypack_order_splits_into_two_positions():
    """Family Pack: одна позиция InSales → 2 позиции в МС с поделённой ценой."""
    with open(FAMILYPACK_ORDER, encoding="utf-8") as f:
        order = json.load(f)

    ms = make_ms_client()
    processor, _ = build_processor(ms)
    processor.process_insales_order(order)

    body = post_calls(ms, "entity/customerorder")[0][0][1]

    product_positions = [p for p in body["positions"]
                         if "product" in p["assortment"]["meta"]["href"]
                         or "/product/" in p["assortment"]["meta"]["href"]]
    # Family Pack даёт 2 товара
    assert len(product_positions) == 2

    # Сумма цен товаров = sale_price * 100 (без потерь при делении)
    total_cents = sum(p["price"] * p["quantity"] for p in product_positions)
    expected = round(float(order["order_lines"][0]["sale_price"]) * 100) * \
        order["order_lines"][0]["quantity"]
    assert total_cents == expected


def test_e2e_courier_order_uses_shipping_address():
    """Курьерская доставка → адрес из shipping_address (city + address)."""
    with open(COURIER_ORDER, encoding="utf-8") as f:
        order = json.load(f)

    ms = make_ms_client()
    processor, _ = build_processor(ms)
    processor.process_insales_order(order)

    body = post_calls(ms, "entity/customerorder")[0][0][1]

    # Должен быть какой-то адрес
    assert body.get("shipmentAddress")
    # И не равен outlet.address (т.к. курьер, не ПВЗ)
    delivery_info = order.get("delivery_info") or {}
    outlet_address = (delivery_info.get("outlet") or {}).get("address")
    if outlet_address:
        assert body["shipmentAddress"] != outlet_address
