#!/usr/bin/env python3
"""Скрипт для получения UUID сущностей из Мой Склад.

Выводит готовые строки для .env файла.
Запуск: python -m woo_moysklad.setup_ms_ids
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.moysklad.ru/api/remap/1.2"
TOKEN = os.getenv("MS_TOKEN", "")

if not TOKEN:
    print("ОШИБКА: MS_TOKEN не задан в .env")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}


def api_get(path: str, params: dict | None = None) -> dict:
    """GET-запрос к API МС."""
    url = f"{BASE_URL}/{path.lstrip('/')}"
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(0.1)  # Задержка между запросами
    return resp.json()


def print_section(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# --- 1. Организации ---
print_section("Организации (GET /entity/organization)")
try:
    data = api_get("entity/organization")
    for row in data.get("rows", []):
        print(f"  {row['name']:40s} → MS_ORGANIZATION_ID={row['id']}")
except Exception as e:
    print(f"  ОШИБКА: {e}")

# --- 2. Склады ---
print_section("Склады (GET /entity/store)")
try:
    data = api_get("entity/store")
    for row in data.get("rows", []):
        print(f"  {row['name']:40s} → MS_STORE_ID={row['id']}")
except Exception as e:
    print(f"  ��ШИБКА: {e}")

# --- 3. Валюты ---
print_section("Валюты (GET /entity/currency)")
try:
    data = api_get("entity/currency")
    for row in data.get("rows", []):
        code = row.get("code", row.get("name", ""))
        print(f"  {code:40s} → MS_CURRENCY_RUB_ID={row['id']}")
except Exception as e:
    print(f"  ��ШИБКА: {e}")

# --- 4. Каналы продаж ---
print_section("Каналы продаж (GET /entity/saleschannel)")
try:
    data = api_get("entity/saleschannel")
    for row in data.get("rows", []):
        print(f"  {row['name']:40s} → MS_SALES_CHANNEL_ID={row['id']}")
except Exception as e:
    print(f"  ��ШИБКА: {e}")

# --- 5. Метаданные заказа: статусы ---
print_section("Статусы заказа покупателя (GET /entity/customerorder/metadata)")
try:
    metadata = api_get("entity/customerorder/metadata")
    states = metadata.get("states", [])
    for s in states:
        print(f"  {s['name']:40s} [{s.get('stateType', '')}] → {s['id']}")
except Exception as e:
    print(f"  ОШИБКА: {e}")

# --- 6. Доп. поля заказа ---
print_section("Доп. поля заказа покупателя")
custom_entity_attrs = []
try:
    # attributes может быть dict с meta (нужен доп. запрос) или list
    attrs_raw = metadata.get("attributes", [])
    if isinstance(attrs_raw, dict) and "meta" in attrs_raw:
        attrs = api_get("entity/customerorder/metadata/attributes").get("rows", [])
    elif isinstance(attrs_raw, list):
        attrs = attrs_raw
    else:
        attrs = []
    for a in attrs:
        attr_type = a.get("type", "")
        print(f"  {a['name']:40s} [{attr_type}] → {a['id']}")
        if attr_type == "customentity" and "customEntityMeta" in a:
            custom_entity_attrs.append(a)
except Exception as e:
    print(f"  ОШИБКА: {e}")

# --- 7. Элементы справочников ---
for attr in custom_entity_attrs:
    ce_meta = attr["customEntityMeta"]
    ce_href = ce_meta["href"]
    # Извлекаем ID справочника из href
    ce_id = ce_href.rstrip("/").split("/")[-1]

    print_section(f"Справочник: {attr['name']} ({ce_id})")
    try:
        data = api_get(f"entity/customentity/{ce_id}")
        for row in data.get("rows", []):
            print(f"  {row['name']:40s} → {row['id']}")
    except Exception as e:
        print(f"  ОШИБКА: {e}")

print(f"\n{'=' * 60}")
print("  Готово! Скопируйте нужные UUID в .env файл.")
print(f"{'=' * 60}")
