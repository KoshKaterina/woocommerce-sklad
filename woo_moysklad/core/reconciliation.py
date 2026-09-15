# Периодическая сверка заказов: проверка наличия в МС и маркировка оплаты.
# Работает с любым количеством SourceAdapter'ов (WC, InSales, ...).

import threading
from datetime import datetime, timedelta, timezone

import requests

from woo_moysklad.exceptions import MoySkladConnectionError
from woo_moysklad.logger import get_logger
from woo_moysklad.core.source_adapter import SourceAdapter

log = get_logger(__name__)

RECONCILIATION_INTERVAL = 180  # 3 минуты
RECONCILIATION_WINDOW = timedelta(minutes=9)  # окно проверки (3× интервала, с запасом)
# Свежие заказы отдаём вебхуку: он приходит сразу после оформления, а сверка —
# страховка и подождать может. Без этого отступа оба входа брались за один заказ
# одновременно и создавали пару дублей в МС (27.08.2026, заказ сайта 18643).
# Замок в core/order_locks.py ловит гонку и без отступа; отступ убирает саму
# встречу двух входов на одном заказе. Окно при этом остаётся 7 минут — заказ
# успевает попасть в две-три сверки подряд.
RECONCILIATION_FRESH_SKEW = timedelta(minutes=2)
# Догонка после сбоя связи (15.09.2026, заказ сайта 19046). Пока МС или источник недоступны,
# каждый проход падает, а окно «последние 9 минут» уезжает вперёд вместе с часами. Заказ,
# изменённый в начале сбоя, выпадал из окна раньше, чем возвращалась связь, и не создавался
# никогда. Теперь источник, у которого проход не прошёл из-за связи, следующий проход начинает
# с начала непроверенного окна. Предел — чтобы многочасовой сбой не превратился в сверку за сутки.
RECONCILIATION_MAX_CATCHUP = timedelta(hours=6)
# Память о непроверенном окне живёт в процессе, а томов у контейнера нет. Поэтому первая сверка
# после старта сервиса смотрит на полчаса назад: заказы, пришедшие, пока сервис лежал или
# перезапускался, иначе остались бы только на совести вебхука.
RECONCILIATION_STARTUP_LOOKBACK = timedelta(minutes=30)


def is_connection_failure(exc: BaseException) -> bool:
    """Сбой связи, а не ошибка данных: МС или источник были недоступны, заказ стоит проверить снова.

    Смотрим всю цепочку причин: `OrderProcessingError` оборачивает `CounterpartyError`,
    а тот — `MoySkladConnectionError` из клиента МС.
    """
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, (MoySkladConnectionError,
                                requests.exceptions.ConnectionError,
                                requests.exceptions.Timeout)):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


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
        self._schedule_lock = threading.Lock()  # гарантирует не более одного активного таймера
        # Источник → (с какого момента окно не проверено, причина). Нет ключа — окно обычное.
        self._unchecked_since: dict[str, tuple[datetime, str]] = {}
        self._startup_remembered = False

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

    def _window_start(self, source: str, normal_start: datetime, now: datetime) -> datetime:
        """Начало окна источника: обычное или раньше, если прошлые проходы окно не закрыли."""
        pending = self._unchecked_since.get(source)
        if pending is None or pending[0] >= normal_start:
            return normal_start

        since, reason = pending
        floor = now - RECONCILIATION_MAX_CATCHUP
        if since < floor:
            log.warning("Сверка: непроверенное окно длиннее предела догонки, берём только последние часы",
                        source=source, с=since.isoformat(), предел=floor.isoformat(), причина=reason)
            return floor
        log.info("Сверка: окно шире обычного", source=source, с=since.isoformat(), причина=reason)
        return since

    def _run_adapter(self, adapter: SourceAdapter,
                     window_start: datetime, window_end: datetime) -> bool:
        """Прогнать один источник через сверку.

        Возвращает True, если окно проверено целиком: заказы получены и ни один не упал
        на связи. Ошибка данных в отдельном заказе окно не держит — иначе один битый заказ
        растягивал бы окно до предела догонки навсегда.
        """
        log.info("Сверка: старт источника", source=adapter.name)

        try:
            orders = adapter.fetch_modified_in_window(window_start, window_end)
        except Exception as e:
            log.error("Сверка: ошибка получения заказов",
                      source=adapter.name, error=str(e))
            return False

        created = 0
        paid = 0
        errors = 0
        connection_failures = 0

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
                if is_connection_failure(e):
                    connection_failures += 1
                log.error("Сверка: ошибка обработки заказа",
                          source=adapter.name, order_id=order_id, error=str(e))

        log.info("Сверка: итог источника",
                 source=adapter.name, создано=created, оплачено=paid, ошибок=errors,
                 сбоев_связи=connection_failures)
        return connection_failures == 0

    def run(self):
        """Прогнать сверку по всем зарегистрированным источникам."""
        log.info("Сверка: начало", sources=[a.name for a in self.adapters])
        now = datetime.now(timezone.utc)
        normal_start = now - RECONCILIATION_WINDOW
        # Верхняя граница отстаёт от «сейчас»: самые свежие заказы — забота вебхука
        window_end = now - RECONCILIATION_FRESH_SKEW

        for adapter in self.adapters:
            window_start = self._window_start(adapter.name, normal_start, now)
            try:
                complete = self._run_adapter(adapter, window_start, window_end)
            except Exception as e:
                complete = False
                log.error("Сверка: необработанная ошибка источника",
                          source=adapter.name, error=str(e))

            if complete:
                self._unchecked_since.pop(adapter.name, None)
                continue
            previous = self._unchecked_since.get(adapter.name)
            since = min(previous[0], window_start) if previous else window_start
            self._unchecked_since[adapter.name] = (since, "сбой связи")
            log.warning("Сверка: окно не закрыто, следующий проход проверит его заново",
                        source=adapter.name, с=since.isoformat())

        # Обратная синхронизация полей (в том же потоке-таймере — без гонки с созданием).
        # Ей догонка не нужна: она правит заказы, которые уже есть в МС, окно у неё обычное.
        if self.field_resync:
            try:
                self.field_resync.run(normal_start, now)
            except Exception as e:
                log.error("Сверка: необработанная ошибка resync", error=str(e))

    def _remember_startup(self, lookback: timedelta = RECONCILIATION_STARTUP_LOOKBACK) -> None:
        """Первая сверка после старта сервиса смотрит на lookback назад (см. RECONCILIATION_STARTUP_LOOKBACK)."""
        self._startup_remembered = True
        since = datetime.now(timezone.utc) - lookback
        for adapter in self.adapters:
            self._unchecked_since.setdefault(adapter.name, (since, "старт сервиса"))

    def schedule(self, interval_seconds: int = RECONCILIATION_INTERVAL):
        """Запланировать периодическую сверку через threading.Timer.

        Защита от двойного запуска: отменяет предыдущий таймер перед созданием нового.
        Если два timer-chain'а оказались живы (напр. после некорректного рестарта),
        следующий вызов schedule() от любого из них схлопнет их в один.
        """
        def _run_and_reschedule():
            try:
                self.run()
            except Exception as e:
                log.error("Сверка: необработанная ошибка", error=str(e))
            self.schedule(interval_seconds)

        with self._schedule_lock:
            if not self._startup_remembered:
                self._remember_startup()
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(interval_seconds, _run_and_reschedule)
            self._timer.daemon = True
            self._timer.start()
        log.info("Сверка запланирована", interval_seconds=interval_seconds)

    def stop(self):
        """Остановить планировщик."""
        with self._schedule_lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
