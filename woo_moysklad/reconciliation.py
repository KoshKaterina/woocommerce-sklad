# Периодическая сверка: проверка пропущенных заказов WC → МС

import threading
from datetime import datetime, timedelta, timezone

from .logger import get_logger
from .order_processor import _TEST_ORDER_SUFFIX  # ВРЕМЕННО: убрать после тестирования

log = get_logger(__name__)

RECONCILIATION_INTERVAL = 1200  # 20 минут
RECONCILIATION_WINDOW = timedelta(minutes=40)  # Окно проверки (с запасом)


class Reconciliation:
    """Периодическая сверка заказов WC и МС.

    Только проверяет наличие заказа в МС по полю 'Номер заказа на сайте'.
    Если заказ не найден — создаёт его. Никогда не обновляет существующие заказы.
    """

    def __init__(self, config, woo_client, ms_client, order_processor):
        self.config = config
        self.woo = woo_client
        self.ms = ms_client
        self.processor = order_processor
        self._timer = None

    def _order_exists_in_ms(self, order_id: str) -> bool:
        """Проверить, существует ли заказ в МС по доп. полю 'Номер заказа на сайте'."""
        if not self.config.MS_ATTR_ORDER_NUMBER_ID:
            return False

        filter_str = (
            f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/"
            f"attributes/{self.config.MS_ATTR_ORDER_NUMBER_ID}={order_id}"
        )
        rows = self.ms.find_by_filter("customerorder", filter_str)
        return bool(rows)

    def run(self):
        """Сверка заказов за последние 40 минут."""
        log.info("Сверка: начало")
        now = datetime.now(timezone.utc)
        window_start = now - RECONCILIATION_WINDOW

        checked = 0
        found = 0
        created = 0
        errors = 0

        try:
            orders = self.woo.get_orders(
                after=window_start.isoformat(),
                before=now.isoformat(),
            )
        except Exception as e:
            log.error("Сверка: ошибка получения заказов из WC", error=str(e))
            return

        for order in orders:
            checked += 1
            order_id = str(order["id"])

            try:
                # ВРЕМЕННО: проверяем с тестовым суффиксом (убрать после тестирования)
                if self._order_exists_in_ms(f"{order_id}{_TEST_ORDER_SUFFIX}"):
                    found += 1
                    continue

                # Не найден — создаём (topic="order.created" гарантирует,
                # что даже если заказ появится между проверкой и созданием,
                # дубликат-чек внутри process_order его пропустит)
                self.processor.process_order(order, topic="order.created")
                created += 1

            except Exception as e:
                errors += 1
                log.error("Сверка: ошибка обработки заказа",
                          wc_order_id=order_id, error=str(e))

        log.info("Сверка: итог",
                 проверено=checked, в_МС=found, создано=created, ошибок=errors)

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
