"""
Проверка совместимости товаров InSales с МойСклад:
1. Все ли варианты в InSales имеют заполненный SKU?
2. Совпадают ли SKU вариантов InSales с артикулами товаров в МС?
3. Есть ли товары только в одной из систем?
"""
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# --- InSales ---
SHOP_URL = os.getenv("INSALES_SHOP_URL", "").strip()
API_KEY = os.getenv("INSALES_API_KEY", "").strip()
PASSWORD = os.getenv("INSALES_PASSWORD", "").strip()

# --- МойСклад ---
MS_TOKEN = os.getenv("MS_TOKEN", "").strip()

if not all([SHOP_URL, API_KEY, PASSWORD]):
    print("Ошибка: заполните INSALES_* в .env")
    sys.exit(1)
if not MS_TOKEN:
    print("Ошибка: заполните MS_TOKEN в .env")
    sys.exit(1)

INSALES_BASE = f"https://{API_KEY}:{PASSWORD}@{SHOP_URL}/admin"
MS_BASE = "https://api.moysklad.ru/api/remap/1.2"

FIXTURES_DIR = Path("tests/fixtures")
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

insales_session = requests.Session()
insales_session.headers["Content-Type"] = "application/json"

ms_session = requests.Session()
ms_session.headers.update({
    "Authorization": f"Bearer {MS_TOKEN}",
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip",
})


def insales_get(endpoint, params=None):
    url = f"{INSALES_BASE}/{endpoint}.json"
    resp = insales_session.get(url, params=params, timeout=30)
    print(f"  InSales GET {endpoint} → {resp.status_code} ({resp.headers.get('API-Usage-Limit', '?')})")
    resp.raise_for_status()
    return resp.json()


def ms_get(endpoint, params=None):
    url = f"{MS_BASE}/{endpoint}"
    resp = ms_session.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_all_insales_products():
    """Получить все товары InSales с вариациями (с пагинацией)."""
    all_products = []
    page = 1
    while True:
        products = insales_get("products", {"per_page": 250, "page": page})
        if not products:
            break
        all_products.extend(products)
        print(f"    ... страница {page}, получено {len(products)} товаров")
        page += 1
    return all_products


def get_all_ms_products():
    """Получить все товары МС с артикулами (с пагинацией)."""
    all_products = []
    offset = 0
    limit = 1000
    while True:
        data = ms_get("entity/product", {"limit": limit, "offset": offset, "filter": "archived=false"})
        rows = data.get("rows", [])
        all_products.extend(rows)
        print(f"  МС products offset={offset}, получено {len(rows)}")
        if len(rows) < limit:
            break
        offset += limit
    return all_products


def main():
    print("\n=== Проверка совместимости SKU: InSales ↔ МойСклад ===\n")

    # 1. Получить товары InSales
    print("1. Загрузка товаров InSales...")
    insales_products = get_all_insales_products()
    print(f"   Всего товаров (product): {len(insales_products)}")

    # Собрать все варианты
    insales_variants = []
    empty_sku_variants = []
    for product in insales_products:
        for variant in product.get("variants", []):
            info = {
                "product_id": product["id"],
                "product_title": product.get("title", "?"),
                "variant_id": variant["id"],
                "variant_title": variant.get("title", ""),
                "sku": (variant.get("sku") or "").strip(),
                "barcode": (variant.get("barcode") or "").strip(),
                "available": variant.get("quantity", 0),
            }
            insales_variants.append(info)
            if not info["sku"]:
                empty_sku_variants.append(info)

    print(f"   Всего вариантов: {len(insales_variants)}")

    # 2. Проверка пустых SKU
    print(f"\n2. Варианты БЕЗ SKU: {len(empty_sku_variants)}")
    if empty_sku_variants:
        for v in empty_sku_variants:
            print(f"   ❌ [{v['variant_id']}] {v['product_title']} / {v['variant_title']} (barcode: {v['barcode'] or '-'})")
    else:
        print("   Все варианты имеют SKU")

    # 3. Получить товары МС
    print("\n3. Загрузка товаров МойСклад...")
    ms_products = get_all_ms_products()
    print(f"   Всего товаров: {len(ms_products)}")

    # Собрать артикулы МС
    ms_articles = {}
    ms_no_article = []
    for p in ms_products:
        article = (p.get("article") or "").strip()
        if article:
            ms_articles[article] = {
                "id": p["id"],
                "name": p.get("name", "?"),
                "article": article,
            }
        else:
            ms_no_article.append(p.get("name", "?"))

    print(f"   С артикулом: {len(ms_articles)}, без артикула: {len(ms_no_article)}")

    # 4. Сравнение
    insales_skus = {v["sku"] for v in insales_variants if v["sku"]}
    ms_article_set = set(ms_articles.keys())

    only_insales = insales_skus - ms_article_set
    only_ms = ms_article_set - insales_skus
    matched = insales_skus & ms_article_set

    print(f"\n4. Результаты сравнения SKU:")
    print(f"   Совпадают: {len(matched)}")
    print(f"   Только в InSales (нет в МС): {len(only_insales)}")
    print(f"   Только в МС (нет в InSales): {len(only_ms)}")

    if only_insales:
        print(f"\n   --- Только в InSales ({len(only_insales)}) ---")
        for sku in sorted(only_insales):
            variants = [v for v in insales_variants if v["sku"] == sku]
            for v in variants:
                print(f"   ⚠ {sku} → {v['product_title']} / {v['variant_title']}")

    if only_ms:
        print(f"\n   --- Только в МС ({len(only_ms)}) ---")
        for article in sorted(only_ms):
            info = ms_articles[article]
            print(f"   ⚠ {article} → {info['name']}")

    # 5. Проверка регистра (case-insensitive дубли)
    print("\n5. Проверка проблем с регистром SKU:")
    insales_lower = {}
    for sku in insales_skus:
        lower = sku.lower()
        insales_lower.setdefault(lower, []).append(sku)
    ms_lower = {}
    for art in ms_article_set:
        lower = art.lower()
        ms_lower.setdefault(lower, []).append(art)

    case_issues = []
    for lower_sku, is_variants in insales_lower.items():
        if lower_sku in ms_lower and is_variants[0] not in ms_article_set:
            case_issues.append((is_variants[0], ms_lower[lower_sku][0]))

    if case_issues:
        for is_sku, ms_art in case_issues:
            print(f"   ⚠ Регистр не совпадает: InSales '{is_sku}' ↔ МС '{ms_art}'")
    else:
        print("   Проблем с регистром нет")

    # Сохранить результаты
    report = {
        "insales_variants_total": len(insales_variants),
        "insales_empty_sku": [v for v in empty_sku_variants],
        "ms_products_total": len(ms_products),
        "matched_count": len(matched),
        "matched_skus": sorted(matched),
        "only_insales": sorted(only_insales),
        "only_ms": sorted(only_ms),
        "case_issues": case_issues,
    }
    report_path = FIXTURES_DIR / "sku_compatibility_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n   Полный отчёт: {report_path}")

    print("\n=== Готово ===\n")


if __name__ == "__main__":
    main()
