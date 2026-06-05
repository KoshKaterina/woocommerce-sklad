# Периодическая сверка заказов: проверка наличия в МС и маркировка оплаты.
# Работает с любым количеством SourceAdapter'ов (WC, InSales, ...).

import threading
from datetime import datetime, timedelta, timezone

from woo_moysklad.logger import get_logger
from woo_moysklad.core.source_adapter import SourceAdapter

log = get_logger(__name__)

RECONCILIATION_INTERVAL = 180  # 3 минуты
RECONCILIATION_WINDOW = timedelta(minutes=9)  # окно проверки (3× интервала, с запасом)


class Reconciliation:
    """Периодическая сверка: для каждого источника проверяет все заказы,
    обновлённые в окне, и либо создаёт в МС (если нет), либо маркирует оплату.

    Проверяется только факт создания/оплаты. Поля заказа не сравниваются —
    менеджеры правят их вручную в МС, и интеграция не должна их затирать.
    """

    def __init__(self, config, ms_client, adapters: list[SourceAdapter],
                 field_resync=None):
        self.config = config
        self.ms = ms_client
        self.adapters = adapters
        self.field_resync = field_resync  # обратная синхронизация полей (опц., TODO §4)
        self._timer = None

    def _find_order_in_ms(self, order_number: str) -> dict | None:
        """Найти заказ в МС по доп. полю 'Номер заказа на сайте'."""
        if not self.config.MS_ATTR_ORDER_NUMBER_ID:
            return None

        filter_str = (
            f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/"
            f"attributes/{self.config.MS_ATTR_ORDER_NUMBER_ID}={order_number}"
        )
        rows = self.ms.find_by_filter("customerorder", filter_str)
        return rows[0] if rows else None

    def _run_adapter(self, adapter: SourceAdapter,
                     window_start: datetime, window_end: datetime) -> None:
        """Прогнать один источник через сверку."""
        log.info("Сверка: старт источника", source=adapter.name)

        try:
            orders = adapter.fetch_modified_in_window(window_start, window_end)
        except Exception as e:
            log.error("Сверка: ошибка получения заказов",
                      source=adapter.name, error=str(e))
            return

        created = 0
        paid = 0
        errors = 0

        for raw_order in orders:
            order_id = adapter.order_id(raw_order)
            try:
                ms_order_number = adapter.ms_order_number(raw_order)
                ms_order = self._find_order_in_ms(ms_order_number)

                if not ms_order:
                    adapter.process(raw_order)
                    created += 1
                    continue

                if adapter.should_mark_paid(raw_order) and ms_order.get("payedSum", 0) == 0:
                    adapter.mark_paid(raw_order)
                    paid += 1
            except Exception as e:
                errors += 1
                log.error("Сверка: ошибка обработки заказа",
                          source=adapter.name, order_id=order_id, error=str(e))

        log.info("Сверка: итог источника",
                 source=adapter.name, создано=created, оплачено=paid, ошибок=errors)

    def run(self):
        """Прогнать сверку по всем зарегистрированным источникам."""
        log.info("Сверка: начало", sources=[a.name for a in self.adapters])
        now = datetime.now(timezone.utc)
        window_start = now - RECONCILIATION_WINDOW

        for adapter in self.adapters:
            try:
                self._run_adapter(adapter, window_start, now)
            except Exception as e:
                log.error("Сверка: необработанная ошибка источника",
                          source=adapter.name, error=str(e))

        # Обратная синхронизация полей (в том же потоке-таймере — без гонки с созданием)
        if self.field_resync:
            try:
                self.field_resync.run(window_start, now)
            except Exception as e:
                log.error("Сверка: необработанная ошибка resync", error=str(e))

    def schedule(self, interval_seconds: int = RECONCILIATION_INTERVAL):
        """Запланировать периодическую сверку через threading.Timer."""
        def _run_and_reschedule():
            try:
                self.run()
            except Exception as e:
                log.error("Сверка: необработанная ошибка", error=str(e))
            self.schedule(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _run_and_reschedule)
        self._timer.daemon = True
        self._timer.start()
        log.info("Сверка запланирована", interval_seconds=interval_seconds)

    def stop(self):
        """Остановить планировщик."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
