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
        """Сверка заказов за последние 40 минут."""
        log.info("Сверка: начало")
        now = datetime.now(timezone.utc)
        window_start = now - RECONCILIATION_WINDOW

        checked = 0
        found = 0
        created = 0
        paid = 0
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
                ms_order = self._find_order_in_ms(f"{order_id}{_TEST_ORDER_SUFFIX}")
                if ms_order:
                    found += 1
                    # Проверяем, нужна ли оплата (предоплата без платежа в МС)
                    if self._should_mark_paid(order) and ms_order.get("payedSum", 0) == 0:
                        try:
                            self.processor.mark_paid(order)
                            paid += 1
                        except Exception as e:
                            log.error("Сверка: ошибка проставления оплаты",
                                      wc_order_id=order_id, error=str(e))
                            errors += 1
                    continue

                # Не найден — создаём (дубликат-чек внутри process_order пропустит,
                # если заказ появится между проверкой и созданием)
                self.processor.process_order(order)
                created += 1

            except Exception as e:
                errors += 1
                log.error("Сверка: ошибка обработки заказа",
                          wc_order_id=order_id, error=str(e))

        log.info("Сверка: итог",
                 проверено=checked, в_МС=found, создано=created,
                 оплачено=paid, ошибок=errors)

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
