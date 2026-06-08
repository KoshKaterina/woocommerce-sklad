# HTTP-клиент uCoz: endpoint отдаёт только последний заказ.

import requests

from woo_moysklad.logger import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT = 30


class UcozClient:
    """Клиент uCoz uamo.php.

    Endpoint возвращает только последний заказ магазина. Параметры игнорируются
    (правка PHP на стороне uCoz недоступна). Авторизации нет — публичный URL.
    """

    def __init__(self, url: str, timeout: int = DEFAULT_TIMEOUT):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()

    def get_latest_order(self) -> dict | None:
        """Получить последний заказ. Возвращает None при сетевой ошибке или пустом ответе."""
        try:
            resp = self.session.get(self.url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("uCoz: ошибка запроса", url=self.url, error=str(e))
            return None

        try:
            data = resp.json()
        except ValueError as e:
            log.error("uCoz: невалидный JSON", error=str(e), body=resp.text[:200])
            return None

        if not isinstance(data, dict) or "order" not in data:
            log.error("uCoz: неожиданная структура ответа", keys=list(data) if isinstance(data, dict) else None)
            return None

        return data
