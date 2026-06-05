"""Ручной тест: передать заказ InSales в МойСклад.

Использование:
    python process_insales_order.py 17663 17662 17661   # получить по ID из InSales API
    python process_insales_order.py path/to/order.json  # взять из файла
    python process_insales_order.py                     # fixture по умолчанию
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.insales.client import InSalesClient
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.core.product_matcher import ProductMatcher


def load_order(arg: str, insales_client: InSalesClient | None) -> dict:
    """Если arg — число, ищем заказ по ВИТРИННОМУ номеру (number) — это то, что
    видит пользователь. Внутренний id (get_order) пробуем только как запасной
    вариант: в InSales number и id — разные пространства и могут совпасть, поэтому
    id-first утаскивал чужой заказ. Не-число → читаем файл."""
    if arg.isdigit():
        if insales_client is None:
            raise RuntimeError("InSales не сконфигурирован — заполни INSALES_* в .env")
        n = int(arg)
        found = insales_client.find_order_by_number(n)
        if found is not None:
            return found
        # запасной вариант — вдруг передали именно внутренний id
        print(f"  ! №{n} не найден среди последних 100 по номеру — пробую как внутренний id")
        try:
            return insales_client.get_order(n)
        except Exception:
            pass
        raise RuntimeError(
            f"Заказ #{n} не найден ни по номеру (последние 100), ни по внутреннему id"
        )
    with open(arg, encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()
    ms = MoySkladClient(config)
    cp = CounterpartyHandler(ms)
    pm = ProductMatcher(ms)
    processor = OrderProcessor(config, ms, cp, pm)

    insales = None
    if config.INSALES_SHOP_URL and config.INSALES_API_KEY:
        insales = InSalesClient(config)

    args = sys.argv[1:] or ["tests/fixtures/insales_sample_order.json"]

    for arg in args:
        print(f"\n========== {arg} ==========")
        try:
            order_data = load_order(arg, insales)
        except Exception as e:
            print(f"Не удалось загрузить заказ: {e}")
            continue

        print(f"InSales #{order_data.get('number', order_data.get('id'))}")
        client = order_data.get("client") or {}
        print(f"  Клиент:   {client.get('name', '')} {client.get('surname', '')}")
        print(f"  Оплата:   {order_data.get('payment_title')}")
        print(f"  Доставка: {order_data.get('delivery_title')}")
        lines = order_data.get("order_lines") or []
        print(f"  Позиций:  {len(lines)}")
        for ol in lines:
            print(f"    - sku={ol.get('sku')!r} variant_id={ol.get('variant_id')} "
                  f"qty={ol.get('quantity')} price={ol.get('sale_price')} "
                  f"{ol.get('title')}")

        try:
            results = processor.process_insales_order(order_data)
        except Exception as e:
            print(f"  ОШИБКА: {e}")
            continue

        print(f"  Создано заказов в МС: {len(results)}")
        for r in results:
            print(f"    → {r.get('name')} (id: {r.get('id')})")

        # Второй проход — отметка оплаты (как в reconciliation): paid_at + paid.
        # process только создаёт заказ; оплата ставится отдельным шагом.
        paid_at = order_data.get("paid_at")
        if paid_at and order_data.get("financial_status") == "paid":
            try:
                marked = processor.mark_paid_insales(order_data)
                names = [m.get("name") for m in marked if m]
                print(f"  Оплата отмечена: {names or 'нет (уже оплачен или заказ не найден)'}")
            except Exception as e:
                print(f"  Оплата НЕ отмечена: {e}")
        else:
            print("  Оплата в InSales не подтверждена (paid_at/financial_status) — "
                  "оплату не ставим")


if __name__ == "__main__":
    main()
