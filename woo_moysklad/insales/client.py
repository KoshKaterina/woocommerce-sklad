# HTTP-клиент InSales API

import time

import requests

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


class InSalesClient:
    """Клиент InSales REST API.

    Авторизация: HTTP Basic Auth (API_KEY:PASSWORD в URL).
    Rate limit: 500 запросов / 5 минут (заголовок API-Usage-Limit).
    """

    def __init__(self, config):
        self.base_url = f"https://{config.INSALES_SHOP_URL}/admin"
        self.session = requests.Session()
        # Креды через Basic Auth, НЕ в URL: иначе пароль утекает в логи
        # с текстом HTTPError, а спецсимволы ломают URL
        self.session.auth = (config.INSALES_API_KEY, config.INSALES_PASSWORD)
        self.session.headers.update({
            "Content-Type": "application/json",
            # Дефолтный UA "python-requests/x.y" часто режется WAF'ами
            "User-Agent": "woo-moysklad-integration/2.0",
        })

    def _request(self, endpoint: str, params: dict | None = None) -> dict | list:
        """GET-запрос с отслеживанием rate limit."""
        url = f"{self.base_url}/{endpoint}.json"
        resp = self.session.get(url, params=params, timeout=30)

        # Отслеживание лимита
        usage = resp.headers.get("API-Usage-Limit", "")
        if usage:
            try:
                current = int(usage.split("/")[0])
                if current > 450:
                    log.warning("InSales API: приближение к лимиту", usage=usage)
                    time.sleep(5)
            except (ValueError, IndexError):
                pass

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            log.warning("InSales API: rate limit, ожидание", retry_after=retry_after)
            time.sleep(retry_after)
            return self._request(endpoint, params)

        resp.raise_for_status()
        return resp.json()

    def check_access(self) -> tuple[bool, str]:
        """Лёгкая проверка доступности API (1 заказ), без raise.

        InSales отвечает только с российских IP (иначе 403 «Доступ ограничен»),
        поэтому при деплое важно сразу увидеть, что API вообще достижим.
        """
        try:
            resp = self.session.get(
                f"{self.base_url}/orders.json", params={"per_page": 1}, timeout=15
            )
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        if resp.status_code == 200:
            return True, "200 OK"
        hint = ""
        if resp.status_code == 403:
            hint = " — IP заблокирован InSales (гео/подсеть/WAF)"
        elif resp.status_code == 401:
            hint = " — неверные INSALES_API_KEY/INSALES_PASSWORD"
        # Заголовок страницы блокировки — для диагностики
        import re
        m = re.search(r"<title>([^<]*)</title>", resp.text or "", re.IGNORECASE)
        title = f", страница: «{m.group(1).strip()}»" if m else ""
        return False, f"HTTP {resp.status_code}{hint}{title}"

    def get_order(self, order_id: int) -> dict:
        """Получить заказ по внутреннему ID (не number!)."""
        return self._request(f"orders/{order_id}")

    def find_order_by_number(self, number: int, per_page: int = 100) -> dict | None:
        """Найти заказ по отображаемому номеру (`number`) среди последних `per_page` заказов."""
        orders = self._request("orders", {"per_page": per_page})
        for o in orders:
            if o.get("number") == number:
                return o
        return None

    def get_orders(self, updated_since: str | None = None,
                   from_id: int | None = None,
                   per_page: int = 50) -> list[dict]:
        """Получить заказы с пагинацией updated_since + from_id.

        Возвращает ВСЕ заказы, соответствующие фильтру (итерирует страницы).
        """
        all_orders = []

        params = {"per_page": per_page}
        if updated_since:
            params["updated_since"] = updated_since
        if from_id:
            params["from_id"] = from_id

        while True:
            batch = self._request("orders", params)
            if not batch:
                break

            all_orders.extend(batch)
            log.info("InSales: загружены заказы", count=len(batch), total=len(all_orders))

            # Курсорная пагинация: сдвигаем маркер
            last = batch[-1]
            params["updated_since"] = last["updated_at"]
            params["from_id"] = last["id"]

        return all_orders
