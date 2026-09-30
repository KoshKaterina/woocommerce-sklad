"""Признак предзаказа у заказов InSales. Синтетика, сети нет.

Образец взят с живого заказа 18627 (30.09.2026): одна строка `TG-FPSTEALTH`
с `quantity: 1, reserved_quantity: 0` разворачивается в две позиции МойСклада.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from woo_moysklad.config import Config
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.product_matcher import ProductMatcher
from woo_moysklad.insales.normalizer import normalize_insales_order
from woo_moysklad.insales.preorder import (
    line_preorder,
    order_age_minutes,
    read_order_preorders,
)

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 30, 18, 25, 0, tzinfo=MSK)


def line(line_id, sku, title, *, quantity=1, reserved=None, price=6990.0):
    item = {"id": line_id, "sku": sku, "title": title, "quantity": quantity,
            "sale_price": price, "discounts_amount": 0.0, "variant_id": None}
    if reserved is not None:
        item["reserved_quantity"] = reserved
    return item


def order(*lines, created_at="2026-09-30T18:18:23.000+03:00"):
    return {
        "id": 1634707921, "number": 18627, "created_at": created_at,
        "client": {"name": "Тиана", "surname": "Василькова", "phone": "+79956109902"},
        "delivery_title": "Самовывоз из Шоурума", "payment_title": "Оплата при получении",
        "order_lines": list(lines), "shipping_address": {}, "discounts": [],
    }


def make_config(*, enabled=True, max_age=20):
    cfg = MagicMock()
    cfg.MS_ORGANIZATION_INSALES_ID = "org-insales"
    cfg.MS_STATE_INSALES_NEW_ID = "state-tangemshop-new"
    cfg.MS_PROJECT_INSALES_ID = "project-insales"
    cfg.MS_SALES_CHANNEL_INSALES_ID = "channel-insales"
    cfg.FAMILY_PACK_WHITE_FIRST_ENABLED = False
    cfg.FAMILY_PACK_WHITE_BASE_PRICE_KOPECKS = 759000
    cfg.FAMILY_PACK_WHITE_DISCOUNT_PERCENT = 30
    cfg.INSALES_ORDER_TYPE_ENABLED = enabled
    cfg.INSALES_PREORDER_MAX_AGE_MINUTES = max_age
    return cfg


# --- line_preorder: что считаем предзаказом ---

@pytest.mark.parametrize("quantity, reserved, expected", [
    (1, 0, True),     # живой случай заказа 18627
    (1, 1, False),
    (2, 2, False),
    (2, 1, True),     # частичный резерв - часть заказа всё равно ждут
    (2, 0, True),
])
def test_rezerv_reshaet_predzakaz_li_eto(quantity, reserved, expected):
    assert line_preorder(line(1, "TG-SKY", "Blush Sky",
                              quantity=quantity, reserved=reserved)) is expected


@pytest.mark.parametrize("payload", [
    {"quantity": 1},                                   # резерва нет вовсе (отменённый заказ)
    {"quantity": 1, "reserved_quantity": None},
    {"quantity": 1, "reserved_quantity": "1"},         # строка вместо числа
    {"quantity": 1, "reserved_quantity": -1},
    {"quantity": 0, "reserved_quantity": 0},
    {"reserved_quantity": 0},                          # нет количества
    {"quantity": True, "reserved_quantity": 0},        # bool не число
    {"quantity": 1, "reserved_quantity": True},
])
def test_nechitaemyy_rezerv_daet_neizvestno(payload):
    assert line_preorder(payload) is None


# --- возраст заказа ---

def test_vozrast_zakaza_schitaetsya_po_created_at():
    age = order_age_minutes(order(), now=NOW)
    assert age == pytest.approx(6.62, abs=0.05)


@pytest.mark.parametrize("created", ["", None, "вчера", "2026-09-30 18:18:23", 12345])
def test_nerazbornaya_data_daet_neizvestnyy_vozrast(created):
    assert order_age_minutes({"created_at": created}, now=NOW) is None


# --- read_order_preorders: главный гейт ---

def test_svezhiy_zakaz_otdaet_kartu_priznakov():
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0),
                 line(2, "TG128X3-B", "Классический чёрный", reserved=1))
    assert read_order_preorders(data, max_age_minutes=20, now=NOW) == {1: True, 2: False}


def test_nesvezhiy_zakaz_priznak_ne_daet_vovse():
    """Резерв снимается при отмене и отгрузке - у старого заказа он уже соврёт."""
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0),
                 created_at="2026-09-30T17:00:00.000+03:00")
    assert read_order_preorders(data, max_age_minutes=20, now=NOW) is None


def test_zakaz_bez_razbornoy_daty_priznak_ne_daet():
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0),
                 created_at="позавчера")
    assert read_order_preorders(data, max_age_minutes=20, now=NOW) is None


def test_stroka_bez_rezerva_schitaetsya_obychnoy_a_ne_ronyaet_zakaz():
    data = order(line(1, "TG128X3-B", "Классический чёрный"),
                 line(2, "TG-SKY", "Blush Sky", reserved=0))
    assert read_order_preorders(data, max_age_minutes=20, now=NOW) == {1: False, 2: True}


# --- нормализатор: признак доезжает до позиций ---

def test_odna_stroka_insales_daet_odinakovyy_priznak_vsem_poziciyam_ms(monkeypatch):
    """Family Pack разворачивается в два SKU, предзаказ у них общий."""
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0))
    normalized = normalize_insales_order(data, make_config())
    assert [i.sku for i in normalized.line_items] == ["TG128X3-B", "TG-STEALTH"]
    assert [i.preorder for i in normalized.line_items] == [True, True]


def test_polnyy_rezerv_daet_obychnyy_zakaz(monkeypatch):
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    data = order(line(1, "TG128X3-B", "Классический чёрный", reserved=1))
    normalized = normalize_insales_order(data, make_config())
    assert [i.preorder for i in normalized.line_items] == [False]


def test_vyklyuchennyy_flag_ostavlyaet_priznak_neizvestnym(monkeypatch):
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0))
    normalized = normalize_insales_order(data, make_config(enabled=False))
    assert [i.preorder for i in normalized.line_items] == [None, None]


# --- заказ в МойСкладе: оба поля ---

def setup_processor():
    cfg = Config(MS_ATTR_PREORDER_SUMMARY_ID="preorder-test-field",
                 MS_ORGANIZATION_ID="org", MS_STORE_ID="regular-store",
                 MS_STORE_OPENED_ID="opened-store", MS_CURRENCY_RUB_ID="rub",
                 MS_ATTR_ORDER_NUMBER_ID="order-number-test-field",
                 MS_ATTR_ORDER_TYPE_ID="order-type-test-field",
                 MS_DICT_ORDER_TYPE_ID="order-type-test-dict",
                 MS_DICT_ORDER_TYPE_ORDER_ID="value-order",
                 MS_DICT_ORDER_TYPE_PREORDER_ID="value-preorder",
                 INSALES_ORDER_TYPE_ENABLED=True)
    ms = MagicMock()
    ms.find_by_filter.side_effect = lambda entity, query: (
        [{"id": "synthetic-product", "meta": {"href": "fake://product"}}]
        if entity == "product" else [])
    ms.make_meta.side_effect = lambda entity, identifier: {
        "meta": {"type": entity, "id": identifier}}
    ms.make_state_meta.side_effect = lambda entity, identifier: {
        "meta": {"type": "state", "id": identifier}}
    ms.post.return_value = {"id": "created"}
    cp = MagicMock()
    cp.find_or_create_from_normalized.return_value = {"meta": {"type": "counterparty"}}
    return OrderProcessor(cfg, ms, cp, ProductMatcher(ms)), ms


def order_type_values(body):
    return [attr["value"]["meta"]["href"].rsplit("/", 1)[-1]
            for attr in body.get("attributes", [])
            if "order-type-test-field" in attr["meta"]["href"]]


def preorder_summaries(body):
    return [attr["value"] for attr in body.get("attributes", [])
            if "preorder-test-field" in attr["meta"]["href"]]


def _frozen_datetime():
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)
    return _Frozen


def test_zakaz_insales_s_predzakazom_nesyot_oba_polya(monkeypatch):
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    processor, ms = setup_processor()
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0))
    processor.process_normalized_order(normalize_insales_order(data, make_config()))
    body = ms.post.call_args[0][1]
    assert order_type_values(body) == ["value-preorder"]
    assert preorder_summaries(body) and "Тип: Предзаказ" in preorder_summaries(body)[0]


def test_zakaz_insales_v_nalichii_nesyot_tip_zakaz(monkeypatch):
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    processor, ms = setup_processor()
    data = order(line(1, "TG128X3-B", "Классический чёрный", reserved=1))
    processor.process_normalized_order(normalize_insales_order(data, make_config()))
    body = ms.post.call_args[0][1]
    assert order_type_values(body) == ["value-order"]
    assert preorder_summaries(body) == ["Тип: Заказ\nОжидаемые позиции: нет"]


def test_nesvezhiy_zakaz_ostavlyaet_oba_polya_pustymi(monkeypatch):
    """Лучше пустое поле, чем выдуманный «Заказ» поверх настоящего предзаказа."""
    monkeypatch.setattr("woo_moysklad.insales.preorder.datetime", _frozen_datetime())
    processor, ms = setup_processor()
    data = order(line(1, "TG-FPSTEALTH", "Family Pack Stealth", reserved=0),
                 created_at="2026-09-30T17:00:00.000+03:00")
    processor.process_normalized_order(normalize_insales_order(data, make_config()))
    body = ms.post.call_args[0][1]
    assert order_type_values(body) == []
    assert preorder_summaries(body) == []
