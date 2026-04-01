"""Скрипт для ручного запуска обработки заказа из WooCommerce."""

import sys
from woo_moysklad.config import load_config
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.woo_client import WooCommerceClient
from woo_moysklad.counterparty_handler import CounterpartyHandler
from woo_moysklad.product_matcher import ProductMatcher
from woo_moysklad.order_processor import OrderProcessor


def main(order_id: int):
    print(f"Загружаю конфиг...")
    config = load_config()

    print(f"Инициализирую клиентов...")
    ms_client = MoySkladClient(config)
    woo_client = WooCommerceClient(config)
    cp_handler = CounterpartyHandler(ms_client)
    pm = ProductMatcher(ms_client)
    order_processor = OrderProcessor(config, ms_client, cp_handler, pm)

    print(f"Получаю заказ #{order_id} из WooCommerce...")
    order_data = woo_client.get_order(order_id)
    print(f"  Статус: {order_data.get('status')}")
    print(f"  Покупатель: {order_data.get('billing', {}).get('first_name')} {order_data.get('billing', {}).get('last_name')}")
    print(f"  Сумма: {order_data.get('total')} {order_data.get('currency')}")
    print(f"  Товаров: {len(order_data.get('line_items', []))}")

    print(f"\nОбрабатываю заказ...")
    results = order_processor.process_order(order_data)
    print(f"\nГотово! Создано заказов в МС: {len(results)}")
    for r in results:
        print(f"  - {r.get('name')} (id: {r.get('id')})")


if __name__ == "__main__":
    order_id = int(sys.argv[1]) if len(sys.argv) > 1 else 15780
    main(order_id)
