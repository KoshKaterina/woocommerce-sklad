"""Ручной тест: передать реальный заказ InSales в МойСклад."""
import json
import sys

from woo_moysklad.config import load_config
from woo_moysklad.counterparty_handler import CounterpartyHandler
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.order_processor import OrderProcessor
from woo_moysklad.product_matcher import ProductMatcher


def main():
    config = load_config()
    ms = MoySkladClient(config)
    cp = CounterpartyHandler(ms)
    pm = ProductMatcher(ms)
    processor = OrderProcessor(config, ms, cp, pm)

    path = "tests/fixtures/insales_sample_order.json"
    if len(sys.argv) > 1:
        path = sys.argv[1]

    with open(path, encoding="utf-8") as f:
        order_data = json.load(f)

    print(f"\nЗаказ InSales #{order_data.get('number', order_data.get('id'))}")
    print(f"Клиент: {order_data['client'].get('name')} {order_data['client'].get('surname')}")
    print(f"Оплата: {order_data.get('payment_title')}")
    print(f"Доставка: {order_data.get('delivery_title')}")
    print(f"Товаров: {len(order_data.get('order_lines', []))}")
    print()

    results = processor.process_insales_order(order_data)

    print(f"\nСоздано заказов в МС: {len(results)}")
    for r in results:
        print(f"  → {r.get('name')} (id: {r.get('id')})")


if __name__ == "__main__":
    main()
