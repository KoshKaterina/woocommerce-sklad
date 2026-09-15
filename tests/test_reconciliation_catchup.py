# Сверка догоняет окно, которое не смогла проверить из-за сбоя связи (15.09.2026, заказ сайта 19046)

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import requests

from tests.test_reconciliation import FakeAdapter, make_config, make_ms_client
from woo_moysklad.core import reconciliation as recon_module
from woo_moysklad.core.reconciliation import (
    RECONCILIATION_FRESH_SKEW,
    RECONCILIATION_INTERVAL,
    RECONCILIATION_MAX_CATCHUP,
    RECONCILIATION_STARTUP_LOOKBACK,
    RECONCILIATION_WINDOW,
    Reconciliation,
    is_connection_failure,
)
from woo_moysklad.exceptions import (
    CounterpartyError,
    MoySkladAPIError,
    MoySkladConnectionError,
    OrderProcessingError,
)

UTC = timezone.utc
STEP = timedelta(seconds=RECONCILIATION_INTERVAL)


class Clock:
    def __init__(self, now):
        self.now = now


@pytest.fixture
def clock(monkeypatch):
    """Управляемое «сейчас» сверки: сценарии с часовыми сбоями без ожидания."""
    c = Clock(datetime(2026, 9, 15, 8, 0, tzinfo=UTC))

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.now

    monkeypatch.setattr(recon_module, "datetime", FakeDateTime)
    return c


class ModifiedWindowAdapter(FakeAdapter):
    """Отдаёт только заказы, изменённые внутри окна, как WooCommerce с modified_after/before.
    Пока state["down"], получить заказы нельзя вовсе."""

    def __init__(self, orders, state=None):
        super().__init__(orders=orders)
        self.state = state if state is not None else {"down": False}
        self.windows = []

    def fetch_modified_in_window(self, window_start, window_end):
        self.windows.append((window_start, window_end))
        if self.state["down"]:
            raise requests.exceptions.ConnectionError("Unable to connect to proxy")
        return [o for o in self.orders if window_start <= o["modified"] <= window_end]


def make_flaky_ms_client(state: dict):
    """МС, до которого нет связи, пока state["down"] (как при лежащем прокси)."""
    ms = MagicMock()

    def find_by_filter(entity, filter_str):
        if state["down"]:
            raise MoySkladConnectionError("Ошибка соединения с МС: Unable to connect to proxy")
        return []

    ms.find_by_filter.side_effect = find_by_filter
    return ms


def connection_error_wrapped_like_order_processor():
    """Та же цепочка, что в логах 15.09: заказ не создан ← контрагент ← связь с МС."""
    try:
        try:
            try:
                raise MoySkladConnectionError("Ошибка соединения с МС: Unable to connect to proxy")
            except MoySkladConnectionError as e:
                raise CounterpartyError(f"Не удалось найти/создать контрагента: {e}") from e
        except CounterpartyError as e:
            raise OrderProcessingError(f"Ошибка контрагента: {e}") from e
    except OrderProcessingError as e:
        return e


def test_order_lost_during_outage_is_created_after_recovery(clock):
    """Сценарий 15.09.2026, заказ сайта 19046.

    Сервис работает штатно, в 11:31 МСК прокси до МС перестаёт отвечать. Заказ изменён в 11:35
    (оплата), все сверки до 12:00 падают на поиске в МС. В 12:02 связь есть, и сверка обязана
    проверить окно, которое не смогла проверить во время сбоя. До правки она смотрела только
    последние минуты, и 19046 не был создан никогда.
    """
    order = {"id": "19046", "modified": datetime(2026, 9, 15, 8, 35, 31, tzinfo=UTC)}
    adapter = ModifiedWindowAdapter([order])
    state = {"down": False}
    recon = Reconciliation(make_config(), make_flaky_ms_client(state), [adapter])

    moment = datetime(2026, 9, 15, 8, 22, tzinfo=UTC)
    while moment < datetime(2026, 9, 15, 9, 0, tzinfo=UTC):
        clock.now = moment
        state["down"] = moment >= datetime(2026, 9, 15, 8, 31, tzinfo=UTC)
        recon.run()
        moment += STEP
    assert adapter.processed == []

    state["down"] = False
    clock.now = datetime(2026, 9, 15, 9, 2, 52, tzinfo=UTC)  # 12:02:52 МСК
    recon.run()

    assert adapter.processed == ["19046"]


def test_window_held_while_source_unreachable(clock):
    """Источник не отдаёт заказы — окно держится до первого удачного прохода, потом снова обычное."""
    state = {"down": True}
    adapter = ModifiedWindowAdapter([], state)
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])
    first_start = clock.now - RECONCILIATION_WINDOW

    recon.run()
    clock.now += STEP
    recon.run()
    assert adapter.windows[1][0] == first_start

    state["down"] = False
    clock.now += STEP
    recon.run()
    assert adapter.windows[2][0] == first_start
    assert adapter.windows[2][1] == clock.now - RECONCILIATION_FRESH_SKEW  # верхняя граница не меняется

    clock.now += STEP
    recon.run()
    assert adapter.windows[3][0] == clock.now - RECONCILIATION_WINDOW


def test_connection_error_inside_order_holds_window(clock):
    """Заказ упал на связи (обёрнуто, как в OrderProcessor) — окно не закрыто."""
    class Adapter(ModifiedWindowAdapter):
        def process(self, raw):
            raise connection_error_wrapped_like_order_processor()

    adapter = Adapter([{"id": "1", "modified": clock.now - timedelta(minutes=5)}])
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])
    first_start = clock.now - RECONCILIATION_WINDOW

    recon.run()
    clock.now += STEP
    recon.run()

    assert adapter.windows[1][0] == first_start


def test_data_error_does_not_hold_window(clock):
    """Битый заказ (ошибка данных) окно не держит, иначе оно растягивалось бы навсегда."""
    class Adapter(ModifiedWindowAdapter):
        def process(self, raw):
            raise OrderProcessingError(f"Заказ #{raw['id']} не содержит товаров")

    adapter = Adapter([{"id": "bad", "modified": clock.now - timedelta(minutes=5)}])
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])

    recon.run()
    clock.now += STEP
    recon.run()

    assert adapter.windows[1][0] == clock.now - RECONCILIATION_WINDOW


def test_catchup_is_capped(clock):
    """Сбой дольше предела — после восстановления проверяем только последние RECONCILIATION_MAX_CATCHUP."""
    state = {"down": True}
    adapter = ModifiedWindowAdapter([], state)
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])

    recon.run()
    state["down"] = False
    clock.now += timedelta(hours=10)
    recon.run()
    assert adapter.windows[1][0] == clock.now - RECONCILIATION_MAX_CATCHUP

    clock.now += STEP
    recon.run()
    assert adapter.windows[2][0] == clock.now - RECONCILIATION_WINDOW


def test_startup_lookback_only_on_first_pass(clock):
    """После старта сервиса первая сверка смотрит на полчаса назад, дальше окно обычное."""
    adapter = ModifiedWindowAdapter([])
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])
    started = clock.now
    recon._remember_startup()

    clock.now += STEP
    recon.run()
    assert adapter.windows[0][0] == started - RECONCILIATION_STARTUP_LOOKBACK

    clock.now += STEP
    recon.run()
    assert adapter.windows[1][0] == clock.now - RECONCILIATION_WINDOW


def test_schedule_remembers_startup_once(clock):
    """schedule() зовут на старте и после каждого прохода — отметка старта ставится один раз."""
    adapter = ModifiedWindowAdapter([])
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter])
    try:
        recon.schedule(interval_seconds=3600)
        first = recon._unchecked_since["fake"]
        recon._unchecked_since.clear()
        clock.now += STEP
        recon.schedule(interval_seconds=3600)
        assert first == (datetime(2026, 9, 15, 8, 0, tzinfo=UTC) - RECONCILIATION_STARTUP_LOOKBACK, "старт сервиса")
        assert recon._unchecked_since == {}
    finally:
        recon.stop()


def test_field_resync_keeps_normal_window(clock):
    """Обратной синхронизации полей догонка не нужна — ей всегда обычное окно."""
    state = {"down": True}
    adapter = ModifiedWindowAdapter([], state)
    resync = MagicMock()
    recon = Reconciliation(make_config(), make_ms_client({}), [adapter], field_resync=resync)

    recon.run()
    state["down"] = False
    clock.now += STEP
    recon.run()

    assert resync.run.call_args_list[1].args == (clock.now - RECONCILIATION_WINDOW, clock.now)


def test_is_connection_failure_walks_cause_chain():
    assert is_connection_failure(connection_error_wrapped_like_order_processor())
    assert is_connection_failure(requests.exceptions.Timeout("read timed out"))
    assert is_connection_failure(requests.exceptions.ConnectionError("Unable to connect to proxy"))
    assert not is_connection_failure(OrderProcessingError("Заказ #1 не содержит товаров"))
    assert not is_connection_failure(MoySkladAPIError("MS API 400: bad", status_code=400))
    assert not is_connection_failure(RuntimeError("boom"))
