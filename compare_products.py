"""
Сравнение товаров InSales и МойСклад для ручной проверки.
InSales: все товары с вариантами.
МС: только товары с "Tangem" в названии.
Вывод: таблица совпадений по SKU/артикулу + несовпавшие.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

SHOP_URL = os.getenv("INSALES_SHOP_URL", "").strip()
API_KEY = os.getenv("INSALES_API_KEY", "").strip()
PASSWORD = os.getenv("INSALES_PASSWORD", "").strip()
MS_TOKEN = os.getenv("MS_TOKEN", "").strip()

if not all([SHOP_URL, API_KEY, PASSWORD, MS_TOKEN]):
    print("Ошибка: заполните INSALES_* и MS_TOKEN в .env")
    sys.exit(1)

INSALES_BASE = f"https://{API_KEY}:{PASSWORD}@{SHOP_URL}/admin"
MS_BASE = "https://api.moysklad.ru/api/remap/1.2"

ms_session = requests.Session()
ms_session.headers.update({
    "Authorization": f"Bearer {MS_TOKEN}",
    "Content-Type": "application/json",
})

FIXTURES_DIR = Path("tests/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)


def insales_get(endpoint, params=None):
    url = f"{INSALES_BASE}/{endpoint}.json"
    resp = requests.get(url, params=params, timeout=30,
                        headers={"Content-Type": "application/json"})
    print(f"  InSales GET {endpoint} → {resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def ms_get_all_tangem():
    """Получить все товары МС с 'Tangem' в названии."""
    all_products = []
    offset = 0
    limit = 1000
    while True:
        resp = ms_session.get(
            f"{MS_BASE}/entity/product",
            params={"limit": limit, "offset": offset, "filter": "archived=false"},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json().get("rows", [])
        tangem = [r for r in rows if "tangem" in r.get("name", "").lower()]
        all_products.extend(tangem)
        print(f"  МС offset={offset}: {len(rows)} товаров, из них Tangem: {len(tangem)}")
        if len(rows) < limit:
            break
        offset += limit
        time.sleep(0.35)
    return all_products


def main():
    print("\n=== Сравнение товаров InSales ↔ МойСклад ===\n")

    # --- InSales ---
    print("1. Загрузка товаров InSales...")
    all_products = []
    page = 1
    while True:
        products = insales_get("products", {"per_page": 250, "page": page})
        if not products:
            break
        all_products.extend(products)
        page += 1
        time.sleep(0.3)

    insales_map = {}  # sku → {product_title, variant_title, sku, price}
    empty_sku = []
    for p in all_products:
        for v in p.get("variants", []):
            sku = (v.get("sku") or "").strip()
            info = {
                "product_title": p.get("title", "?"),
                "variant_title": v.get("title", ""),
                "sku": sku,
                "price": v.get("price"),
            }
            if sku:
                insales_map[sku] = info
            else:
                empty_sku.append(info)

    print(f"   Вариантов с SKU: {len(insales_map)}, без SKU: {len(empty_sku)}")

    # --- МойСклад ---
    print("\n2. Загрузка товаров МойСклад (Tangem)...")
    ms_products = ms_get_all_tangem()

    ms_map = {}  # article → {name, article}
    for p in ms_products:
        article = (p.get("article") or "").strip()
        if article:
            ms_map[article] = {
                "name": p.get("name", "?"),
                "article": article,
            }

    print(f"   Товаров Tangem с артикулом: {len(ms_map)}")

    # --- Сравнение ---
    insales_skus = set(insales_map.keys())
    ms_articles = set(ms_map.keys())

    matched = sorted(insales_skus & ms_articles)
    only_insales = sorted(insales_skus - ms_articles)
    only_ms = sorted(ms_articles - insales_skus)

    print(f"\n{'='*100}")
    print(f"  СОВПАВШИЕ: {len(matched)}")
    print(f"{'='*100}")
    print(f"  {'SKU':<20} {'InSales название':<45} {'МС название'}")
    print(f"  {'-'*20} {'-'*45} {'-'*40}")
    for sku in matched:
        is_info = insales_map[sku]
        ms_info = ms_map[sku]
        is_name = f"{is_info['product_title']}"
        if is_info["variant_title"]:
            is_name += f" / {is_info['variant_title']}"
        print(f"  {sku:<20} {is_name:<45} {ms_info['name']}")

    if only_insales:
        print(f"\n{'='*100}")
        print(f"  ТОЛЬКО В INSALES ({len(only_insales)}) — нет в МС")
        print(f"{'='*100}")
        for sku in only_insales:
            info = insales_map[sku]
            name = info["product_title"]
            if info["variant_title"]:
                name += f" / {info['variant_title']}"
            print(f"  {sku:<20} {name}")

    if only_ms:
        print(f"\n{'='*100}")
        print(f"  ТОЛЬКО В МС ({len(only_ms)}) — нет в InSales")
        print(f"{'='*100}")
        for article in only_ms:
            info = ms_map[article]
            print(f"  {article:<20} {info['name']}")

    if empty_sku:
        print(f"\n{'='*100}")
        print(f"  INSALES ВАРИАНТЫ БЕЗ SKU ({len(empty_sku)})")
        print(f"{'='*100}")
        for info in empty_sku:
            name = info["product_title"]
            if info["variant_title"]:
                name += f" / {info['variant_title']}"
            print(f"  (пусто)  {name}")

    # Сохранить результат
    report = {
        "matched": [{
            "sku": sku,
            "insales": insales_map[sku]["product_title"],
            "ms": ms_map[sku]["name"],
        } for sku in matched],
        "only_insales": [{
            "sku": sku,
            "name": insales_map[sku]["product_title"],
        } for sku in only_insales],
        "only_ms": [{
            "article": a,
            "name": ms_map[a]["name"],
        } for a in only_ms],
        "empty_sku_insales": empty_sku,
    }
    path = FIXTURES_DIR / "product_comparison.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nОтчёт сохранён: {path}")
    print()


if __name__ == "__main__":
    main()
