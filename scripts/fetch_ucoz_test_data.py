"""
Скрипт для получения тестовых данных из uCoz: дёргает uamo.php и сохраняет
ответ как фикстуру с заданным именем для анализа маппинга.

uamo.php отдаёт ТОЛЬКО последний заказ (параметры игнорируются), поэтому
работаем так:
    1. Делаем тестовый заказ в магазине uCoz (нужный тип доставки/оплаты).
    2. Запускаем этот скрипт с понятным именем:
           python fetch_ucoz_test_data.py cdek_pvz_cod
    3. Сохраняется tests/fixtures/ucoz_<name>.json (и копия в _last.json).
    4. Перед следующим тестовым заказом — снова запускаем с другим именем.
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("UCOZ_POLL_URL", "https://sunscrypt.usite.pro/php/uamo.php").strip()
FIXTURES_DIR = Path("tests/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def fetch() -> dict:
    resp = requests.get(URL, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "last"
    safe_name = name.replace("/", "_").replace(" ", "_")

    print(f"\n=== uCoz: GET {URL} ===\n")
    data = fetch()

    order = data.get("order") or {}
    goods = data.get("goods") or []

    print(f"order.id              = {order.get('id')}")
    print(f"order.add_date        = {order.get('add_date')}")
    print(f"order.payment_id      = {order.get('payment_id')}")
    print(f"order.delivery_id     = {order.get('delivery_id')}")
    print(f"order.delivery_tax    = {order.get('delivery_tax')}")
    print(f"order.amount          = {order.get('amount')}")
    print(f"order.payment_topay   = {order.get('payment_topay')!r}")
    print(f"order.inv_params      = {order.get('inv_params')!r}")
    print(f"order.delivery_data   = {order.get('delivery_data')!r}")
    print(f"order.field_delivery_address = {order.get('field_delivery_address')!r}")
    print(f"goods (n={len(goods)}):")
    for g in goods:
        print(f"  - id={g.get('goods_id')} count={g.get('count')} price={g.get('price_raw')} name={g.get('name')!r}")

    out = FIXTURES_DIR / f"ucoz_{safe_name}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ Сохранено: {out}")

    # Дублируем в _last.json — удобно перезапускать без аргументов
    last = FIXTURES_DIR / "ucoz__last.json"
    last.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"→ Копия: {last}")


if __name__ == "__main__":
    main()
