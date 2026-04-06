# Периодическая сверка: проверка пропущенных заказов WC → МС

import threading
from datetime import datetime, timedelta, timezone

from .logger import get_logger
from .order_processor import _TEST_ORDER_SUFFIX

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

    def _find_order_in_ms(self, order_id: str) -> dict | None:
        """Найти заказ в МС по доп. полю 'Номер заказа на сайте'. Возвращает заказ или None."""
        if not self.config.MS_ATTR_ORDER_NUMBER_ID:
            return None

        filter_str = (
            f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/"
            f"attributes/{self.config.MS_ATTR_ORDER_NUMBER_ID}={order_id}"
        )
        rows = self.ms.find_by_filter("customerorder", filter_str)
        return rows[0] if rows else None

    @staticmethod
    def _should_mark_paid(wc_order: dict) -> bool:
        """Проверить, нужно ли проставить оплату (предоплата со статусом processing)."""
        if wc_order.get("status") not in ("processing", "completed"):
            return False
        payment_title = wc_order.get("payment_method_title", "").lower()
        return "при получении" not in payment_title and "на карту" not in payment_title

    def run(self):
        """Сверка заказов: два прохода.

        1) По date_created — проверяем, что все новые заказы создались в МС.
        2) По date_modified — проверяем, что оплаты проставлены.
        """
        log.info("Сверка: начало")
        now = datetime.now(timezone.utc)
        window_start = now - RECONCILIATION_WINDOW

        created = 0
        paid = 0
        errors = 0

        # --- Проход 1: новые заказы (по date_created) ---
        try:
            new_orders = self.woo.get_orders(
                after=window_start.isoformat(),
                before=now.isoformat(),
            )
        except Exception as e:
            log.error("Сверка: ошибка получения новых заказов из WC", error=str(e))
            new_orders = []

        for order in new_orders:
            order_id = str(order["id"])
            try:
                ms_order = self._find_order_in_ms(f"{order_id}{_TEST_ORDER_SUFFIX}")
                if ms_order:
                    continue
                self.processor.process_order(order)
                created += 1
            except Exception as e:
                errors += 1
                log.error("Сверка: ошибка создания заказа",
                          wc_order_id=order_id, error=str(e))

        # --- Проход 2: оплаты (по date_modified) ---
        try:
            modified_orders = self.woo.get_orders(
                modified_after=window_start.isoformat(),
                modified_before=now.isoformat(),
            )
        except Exception as e:
            log.error("Сверка: ошибка получения изменённых заказов из WC", error=str(e))
            modified_orders = []

        for order in modified_orders:
            order_id = str(order["id"])
            if not self._should_mark_paid(order):
                continue
            try:
                ms_order = self._find_order_in_ms(f"{order_id}{_TEST_ORDER_SUFFIX}")
                if not ms_order or ms_order.get("payedSum", 0) > 0:
                    continue
                self.processor.mark_paid(order)
                paid += 1
            except Exception as e:
                errors += 1
                log.error("Сверка: ошибка проставления оплаты",
                          wc_order_id=order_id, error=str(e))

        log.info("Сверка: итог",
                 новых_создано=created, оплачено=paid, ошибок=errors)

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
