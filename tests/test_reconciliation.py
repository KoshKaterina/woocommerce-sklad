# Тесты унифицированной сверки через SourceAdapter

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from woo_moysklad.core.reconciliation import Reconciliation
from woo_moysklad.core.source_adapter import (
    InSalesSourceAdapter,
    SourceAdapter,
    WooSourceAdapter,
)


# --- Фиктивный адаптер ---

class FakeAdapter(SourceAdapter):
    name = "fake"

    def __init__(self, orders, should_pay_ids=None):
        self.orders = orders
        self.should_pay_ids = should_pay_ids or set()
        self.processed = []
        self.marked = []

    def fetch_modified_in_window(self, window_start, window_end):
        return self.orders

    def order_id(self, raw):
        return str(raw["id"])

    def ms_order_number(self, raw):
        return str(raw["id"])

    def should_mark_paid(self, raw):
        return raw["id"] in self.should_pay_ids

    def process(self, raw):
        self.processed.append(raw["id"])
        return [{"id": "ms-created"}]

    def mark_paid(self, raw):
        self.marked.append(raw["id"])
        return [{"id": "ms-paid"}]


def make_config():
    cfg = MagicMock()
    cfg.MS_ATTR_ORDER_NUMBER_ID = "attr-order-num"
    return cfg


def make_ms_client(existing_by_number: dict):
    """existing_by_number = {"15674": {...existing order...}, ...}"""
    ms = MagicMock()

    def find_by_filter(entity, filter_str):
        # вычленяем номер из конца filter_str: "...={number}"
        number = filter_str.rsplit("=", 1)[-1]
        found = existing_by_number.get(number)
        return [found] if found else []

    ms.find_by_filter.side_effect = find_by_filter
    return ms


# --- Тесты ---

def test_creates_missing_orders():
    """Если заказа нет в МС — вызывается process."""
    adapter = FakeAdapter(orders=[{"id": "1"}, {"id": "2"}])
    ms = make_ms_client(existing_by_number={})
    recon = Reconciliation(make_config(), ms, [adapter])

    recon.run()

    assert adapter.processed == ["1", "2"]
    assert adapter.marked == []


def test_skips_existing_without_payment_flag():
    """Заказ есть в МС, should_mark_paid=False — ничего не делаем."""
    adapter = FakeAdapter(orders=[{"id": "1"}], should_pay_ids=set())
    ms = make_ms_client(existing_by_number={"1": {"id": "ms-1", "payedSum": 0}})
    recon = Reconciliation(make_config(), ms, [adapter])

    recon.run()

    assert adapter.processed == []
    assert adapter.marked == []


def test_marks_paid_when_flag_true_and_not_paid():
    """Заказ есть в МС, should_mark_paid=True, payedSum=0 — mark_paid."""
    adapter = FakeAdapter(orders=[{"id": "1"}], should_pay_ids={"1"})
    ms = make_ms_client(existing_by_number={"1": {"id": "ms-1", "payedSum": 0}})
    recon = Reconciliation(make_config(), ms, [adapter])

    recon.run()

    assert adapter.processed == []
    assert adapter.marked == ["1"]


def test_skips_already_paid():
    """Заказ уже оплачен (payedSum > 0) — mark_paid не вызывается."""
    adapter = FakeAdapter(orders=[{"id": "1"}], should_pay_ids={"1"})
    ms = make_ms_client(existing_by_number={"1": {"id": "ms-1", "payedSum": 100}})
    recon = Reconciliation(make_config(), ms, [adapter])

    recon.run()

    assert adapter.processed == []
    assert adapter.marked == []


def test_multiple_adapters_independent():
    """Reconciliation обходит каждый адаптер независимо."""
    a = FakeAdapter(orders=[{"id": "A1"}])
    b = FakeAdapter(orders=[{"id": "B1"}])
    a.name = "A"
    b.name = "B"
    ms = make_ms_client(existing_by_number={})
    recon = Reconciliation(make_config(), ms, [a, b])

    recon.run()

    assert a.processed == ["A1"]
    assert b.processed == ["B1"]


def test_error_in_one_order_does_not_stop_others():
    """Исключение на одном заказе не должно остановить обработку остальных."""
    class ExplodingAdapter(FakeAdapter):
        def process(self, raw):
            if raw["id"] == "bad":
                raise RuntimeError("boom")
            return super().process(raw)

    adapter = ExplodingAdapter(orders=[{"id": "1"}, {"id": "bad"}, {"id": "3"}])
    ms = make_ms_client(existing_by_number={})
    recon = Reconciliation(make_config(), ms, [adapter])

    recon.run()  # не должен упасть

    assert adapter.processed == ["1", "3"]


# --- WooSourceAdapter ---

def test_woo_should_mark_paid_processing_online():
    """WC: processing + онлайн-оплата → маркируем."""
    adapter = WooSourceAdapter(MagicMock(), MagicMock())
    assert adapter.should_mark_paid({
        "status": "processing",
        "payment_method_title": "Онлайн оплата",
    }) is True


def test_woo_should_mark_paid_skips_manual():
    """WC: 'На карту', 'Банковский перевод' и 'При получении' — ручная маркировка."""
    adapter = WooSourceAdapter(MagicMock(), MagicMock())
    assert adapter.should_mark_paid({
        "status": "processing",
        "payment_method_title": "На карту",
    }) is False
    assert adapter.should_mark_paid({
        "status": "processing",
        "payment_method_title": "Банковский перевод",
    }) is False
    assert adapter.should_mark_paid({
        "status": "processing",
        "payment_method_title": "При получении",
    }) is False


def test_woo_should_mark_paid_skips_non_processing():
    """WC: любой статус кроме processing/completed — не маркируем."""
    adapter = WooSourceAdapter(MagicMock(), MagicMock())
    assert adapter.should_mark_paid({
        "status": "on-hold",
        "payment_method_title": "Онлайн",
    }) is False


# --- InSalesSourceAdapter ---

def test_insales_should_mark_paid_paid_at_and_status():
    """InSales: paid_at + financial_status=paid → маркируем."""
    adapter = InSalesSourceAdapter(MagicMock(), MagicMock())
    assert adapter.should_mark_paid({
        "paid_at": "2026-04-22T10:00:00+03:00",
        "financial_status": "paid",
    }) is True


def test_insales_should_mark_paid_pending_not_marked():
    """InSales: financial_status=pending — не маркируем."""
    adapter = InSalesSourceAdapter(MagicMock(), MagicMock())
    assert adapter.should_mark_paid({
        "paid_at": None,
        "financial_status": "pending",
    }) is False


def test_insales_ms_order_number_has_suffix():
    """InSales: номер в МС = number + ' Tangemshop' (пока тестовый суффикс)."""
    from woo_moysklad.insales.normalizer import _INSALES_ORDER_SUFFIX
    adapter = InSalesSourceAdapter(MagicMock(), MagicMock())
    assert adapter.ms_order_number({"number": 17620}) == f"17620{_INSALES_ORDER_SUFFIX}"


def test_insales_fetch_filters_by_updated_at_upper_bound():
    """InSales API не умеет upper bound — фильтруется на клиенте по updated_at <= window_end.

    updated_at приходит в зоне магазина (+03:00) — сравнение должно быть
    по времени, а не по ISO-строке (строки с разными зонами несравнимы).
    """
    insales = MagicMock()
    insales.get_orders.return_value = [
        # 12:55 МСК = 09:55 UTC — внутри окна, хотя строка "больше" UTC-строки конца окна
        {"id": 1, "updated_at": "2026-04-22T12:55:00.123+03:00"},
        # 13:05 МСК = 10:05 UTC — вне окна
        {"id": 2, "updated_at": "2026-04-22T13:05:00.456+03:00"},
        # UTC-офсет тоже поддерживаем
        {"id": 3, "updated_at": "2026-04-22T09:00:00+00:00"},
    ]
    adapter = InSalesSourceAdapter(insales, MagicMock())
    window_end = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)
    window_start = window_end - timedelta(minutes=40)

    result = adapter.fetch_modified_in_window(window_start, window_end)

    assert [o["id"] for o in result] == [1, 3]


def test_insales_fetch_keeps_order_with_unparseable_updated_at():
    """Кривой/пустой updated_at не должен терять заказ — создание идемпотентно."""
    insales = MagicMock()
    insales.get_orders.return_value = [
        {"id": 1, "updated_at": ""},
        {"id": 2},  # поля нет вовсе
        {"id": 3, "updated_at": "2026-04-22 12:55"},  # naive — несравнимо с aware
    ]
    adapter = InSalesSourceAdapter(insales, MagicMock())
    window_end = datetime(2026, 4, 22, 10, 0, tzinfo=timezone.utc)

    result = adapter.fetch_modified_in_window(window_end - timedelta(minutes=9), window_end)

    assert [o["id"] for o in result] == [1, 2, 3]
