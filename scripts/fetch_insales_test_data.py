"""
Скрипт для получения тестовых данных из InSales.
Запрашивает последние заказы, способы оплаты и доставки,
сохраняет в tests/fixtures/ для анализа маппинга.
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SHOP_URL = os.getenv("INSALES_SHOP_URL", "").strip().rstrip("/")
API_KEY = os.getenv("INSALES_API_KEY", "").strip()
PASSWORD = os.getenv("INSALES_PASSWORD", "").strip()

if not all([SHOP_URL, API_KEY, PASSWORD]):
    print("Ошибка: заполните INSALES_SHOP_URL, INSALES_API_KEY и INSALES_PASSWORD в .env")
    sys.exit(1)

BASE_URL = f"https://{API_KEY}:{PASSWORD}@{SHOP_URL}/admin"
FIXTURES_DIR = Path("tests/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})


def api_get(endpoint: str, params: dict | None = None) -> dict | list:
    url = f"{BASE_URL}/{endpoint}.json"
    resp = session.get(url, params=params, timeout=30)
    print(f"  GET {endpoint}.json → {resp.status_code} ({resp.headers.get('API-Usage-Limit', '?')})")
    resp.raise_for_status()
    return resp.json()


def save_fixture(name: str, data):
    path = FIXTURES_DIR / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  → Сохранено: {path}")


def main():
    print("\n=== InSales: получение тестовых данных ===\n")

    # 1. Последние заказы (до 5 штук)
    print("1. Заказы (последние 5):")
    orders = api_get("orders", {"per_page": 5})
    save_fixture("insales_orders.json", orders)
    if orders:
        print(f"   Получено заказов: {len(orders)}")
        # Сохраним первый заказ отдельно для детального анализа
        first_order = api_get(f"orders/{orders[0]['id']}")
        save_fixture("insales_sample_order.json", first_order)
        print(f"   Детали заказа #{first_order.get('number', first_order.get('id'))}")
    else:
        print("   Заказов не найдено. Создайте тестовый заказ в InSales.")

    # 2. Способы оплаты
    print("\n2. Способы оплаты:")
    payments = api_get("payment_gateways")
    save_fixture("insales_payment_gateways.json", payments)
    for pg in payments:
        print(f"   - [{pg.get('id')}] {pg.get('title', '?')} (тип: {pg.get('type', '?')})")

    # 3. Способы доставки
    print("\n3. Способы доставки:")
    deliveries = api_get("delivery_variants")
    save_fixture("insales_delivery_variants.json", deliveries)
    for dv in deliveries:
        print(f"   - [{dv.get('id')}] {dv.get('title', '?')} (тип: {dv.get('type', '?')})")

    # 4. Статусы заказов (кастомные)
    print("\n4. Кастомные статусы заказов:")
    try:
        statuses = api_get("custom_statuses")
        save_fixture("insales_custom_statuses.json", statuses)
        for s in statuses:
            print(f"   - [{s.get('permalink')}] {s.get('title', '?')} → {s.get('system_status', '?')}")
    except requests.HTTPError as e:
        print(f"   Не удалось получить: {e}")

    # 5. Вебхуки (текущие)
    print("\n5. Текущие вебхуки:")
    try:
        webhooks = api_get("webhooks")
        save_fixture("insales_webhooks.json", webhooks)
        if webhooks:
            for wh in webhooks:
                print(f"   - [{wh.get('id')}] {wh.get('topic')} → {wh.get('address')}")
        else:
            print("   Вебхуков нет (это нормально для нового подключения)")
    except requests.HTTPError as e:
        print(f"   Не удалось получить: {e}")

    # 6. Дополнительные поля заказов
    print("\n6. Дополнительные поля заказов:")
    try:
        fields = api_get("fields")
        save_fixture("insales_fields.json", fields)
        for f_item in fields:
            print(f"   - [{f_item.get('id')}] {f_item.get('title', '?')} ({f_item.get('type', '?')})")
    except requests.HTTPError as e:
        print(f"   Не удалось получить: {e}")

    print("\n=== Готово! Файлы в tests/fixtures/insales_*.json ===")
    print("Передайте эти файлы для анализа маппинга полей.\n")


if __name__ == "__main__":
    main()
