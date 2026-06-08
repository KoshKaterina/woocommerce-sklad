# Отдельный поллер uCoz: раз в 5 минут дёргает uamo.php, детектит новые заказы и gap'ы.
#
# Не входит в общий Reconciliation, т.к. uamo.php отдаёт только ПОСЛЕДНИЙ заказ —
# по id запрашивать нельзя, пагинации нет. Поэтому своя цепочка:
#   last_id из state → current_id из API → обработать, если id вырос.

import threading

from woo_moysklad.logger import get_logger
from woo_moysklad.ucoz.client import UcozClient
from woo_moysklad.ucoz.state import UcozState

log = get_logger(__name__)

DEFAULT_INTERVAL_SECONDS = 60  # 1 минута: эндпоинт лёгкий (1 заказ в ответе),
                                #          быстрее реагируем на новый заказ.


class UcozPoller:
    """Планировщик uCoz: периодически читает последний заказ и обрабатывает новые."""

    def __init__(self, client: UcozClient, state: UcozState, order_processor,
                 interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
        self.client = client
        self.state = state
        self.processor = order_processor
        self.interval = interval_seconds
        self._timer: threading.Timer | None = None

    def _extract_current_id(self, raw: dict) -> int | None:
        order = raw.get("order") or {}
        current_id = order.get("id")
        if not isinstance(current_id, int):
            log.error("uCoz: в ответе нет order.id (или не int)", order_id=current_id)
            return None
        return current_id

    def run_once(self) -> None:
        """Один проход: получить последний заказ и обработать, если новый."""
        raw = self.client.get_latest_order()
        if not raw:
            return

        current_id = self._extract_current_id(raw)
        if current_id is None:
            return

        last_id = self.state.load()

        if last_id is None:
            # Холодный старт: запоминаем, ничего не обрабатываем.
            self.state.save(current_id)
            log.info("uCoz: холодный старт", current_id=current_id)
            return

        if current_id == last_id:
            return

        if current_id < last_id:
            log.warning("uCoz: id в ответе меньше сохранённого",
                        current_id=current_id, last_id=last_id)
            return

        skipped = list(range(last_id + 1, current_id))
        if skipped:
            # Мы не можем запросить их по id — uamo.php отдаёт только последний.
            # Когда появится общий алертинг — перенести в telegram-уведомление.
            log.error("uCoz: пропущены заказы между проходами",
                      last_id=last_id, current_id=current_id,
                      skipped_ids=skipped, skipped_count=len(skipped))

        try:
            self.processor.process_ucoz_order(raw)
        except Exception as e:
            log.error("uCoz: ошибка обработки, повтор на следующем тике",
                      order_id=current_id, error=str(e))
            return  # state не сохраняем → retry на следующем тике

        self.state.save(current_id)

    def schedule(self, interval_seconds: int | None = None) -> None:
        """Запланировать периодический опрос через threading.Timer."""
        interval = interval_seconds if interval_seconds is not None else self.interval

        def _run_and_reschedule():
            try:
                self.run_once()
            except Exception as e:
                log.error("uCoz: необработанная ошибка в run_once", error=str(e))
            self.schedule(interval)

        self._timer = threading.Timer(interval, _run_and_reschedule)
        self._timer.daemon = True
        self._timer.start()
        log.info("uCoz поллер запланирован", interval_seconds=interval)

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
            self._timer = None
