"""Check delivery method usage across last 300 InSales orders."""
import os
from collections import Counter

import requests
from dotenv import load_dotenv

load_dotenv()

SHOP_URL = os.getenv("INSALES_SHOP_URL")
API_KEY = os.getenv("INSALES_API_KEY")
PASSWORD = os.getenv("INSALES_PASSWORD")
BASE = f"https://{API_KEY}:{PASSWORD}@{SHOP_URL}/admin"


def main():
    all_orders = []
    page = 1
    while len(all_orders) < 300:
        resp = requests.get(
            f"{BASE}/orders.json",
            params={"per_page": 100, "page": page},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_orders.extend(batch)
        page += 1

    all_orders = all_orders[:300]
    print(f"Orders fetched: {len(all_orders)}\n")

    delivery_counter = Counter()
    tariff_counter = Counter()
    company_counter = Counter()

    for o in all_orders:
        delivery_counter[o.get("delivery_title", "—")] += 1
        di = o.get("delivery_info") or {}
        if di:
            tariff_counter[di.get("tariff_id", "—")] += 1
            company_counter[di.get("shipping_company", "—")] += 1

    print("=== By delivery_title ===")
    for name, cnt in delivery_counter.most_common():
        print(f"  {cnt:>4}  {name}")

    print("\n=== By tariff_id ===")
    for name, cnt in tariff_counter.most_common():
        print(f"  {cnt:>4}  {name}")

    print("\n=== By shipping_company ===")
    for name, cnt in company_counter.most_common():
        print(f"  {cnt:>4}  {name}")


if __name__ == "__main__":
    main()
