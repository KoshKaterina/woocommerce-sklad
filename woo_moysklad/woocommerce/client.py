# Клиент WooCommerce API для получения заказов (сверка)

from woocommerce import API

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


class WooCommerceClient:
    """Клиент WooCommerce REST API v3 для получения заказов."""

    def __init__(self, config):
        self.wcapi = API(
            url=config.WC_URL,
            consumer_key=config.WC_CONSUMER_KEY,
            consumer_secret=config.WC_CONSUMER_SECRET,
            wp_api=True,
            version="wc/v3",
            timeout=30,
        )

    def get_order(self, order_id: int) -> dict:
        """Получить заказ по ID."""
        response = self.wcapi.get(f"orders/{order_id}")
        response.raise_for_status()
        return response.json()

    def get_orders(self, after: str | None = None, before: str | None = None,
                   modified_after: str | None = None, modified_before: str | None = None,
                   per_page: int = 100, dates_are_gmt: bool = False) -> list[dict]:
        """Получить список заказов с пагинацией.

        after/before — фильтр по date_created (ISO 8601).
        modified_after/modified_before — фильтр по date_modified (ISO 8601).

        dates_are_gmt=True — даты переданы в UTC. Без этого флага WooCommerce
        читает их как время сайта (МСК), и окно уезжает на 3 часа: сверка
        смотрела в прошлое и не добирала свежие заказы (инцидент 29.07.2026,
        заказ #18145 после 500-х от МС остался незамеченным).
        """
        all_orders = []
        page = 1

        while True:
            params = {"per_page": per_page, "page": page}
            if after:
                params["after"] = after
            if before:
                params["before"] = before
            if modified_after:
                params["modified_after"] = modified_after
            if modified_before:
                params["modified_before"] = modified_before
            if dates_are_gmt and (after or before or modified_after or modified_before):
                params["dates_are_gmt"] = "true"

            response = self.wcapi.get("orders", params=params)
            response.raise_for_status()
            orders = response.json()

            if not orders:
                break

            all_orders.extend(orders)

            # Проверяем, есть ли ещё страницы
            total_pages = int(response.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages:
                break
            page += 1

        log.info("WC: получены заказы", count=len(all_orders), after=after, before=before)
        return all_orders
