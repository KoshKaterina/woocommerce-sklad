"""Ретро-фикс адресов InSales-заказов в МС: нативный shipmentAddressFull (разово, 2026-06).

До фикса InSales-заказы получали только плоскую строку адреса — в карточке МС
структурные поля пустые, всё в «Другом». Скрипт идёт по заказам InSales,
строит ShipmentAddressParts тем же кодом, что и интеграция
(build_insales_address_parts), находит заказ МС по доп.полю «Номер заказа
на сайте» (= "<номер> Tangemshop") и пишет shipmentAddressFull.
Плоский адрес МС перегенерирует из структуры сам. Самовывоз/«не требуется» —
пропуск. ВАЖНО: InSales API доступен только с российского IP.

  python scripts/fix_insales_addresses_retro.py --dry-run
  python scripts/fix_insales_addresses_retro.py
  python scripts/fix_insales_addresses_retro.py --pages 5   # глубина, по 100 заказов
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.insales.client import InSalesClient
from woo_moysklad.insales.normalizer import build_insales_address_parts
from woo_moysklad.ms_client import MS_BASE_URL, MoySkladClient


def build_full(ms: MoySkladClient, parts) -> dict | None:
    """Собрать объект shipmentAddressFull (зеркало OrderProcessor)."""
    if parts is None or parts.is_empty():
        return None
    full = {}
    if parts.postal_code:
        full["postalCode"] = parts.postal_code
    if parts.city:
        full["city"] = parts.city
    if parts.street:
        full["street"] = parts.street
    if parts.house:
        full["house"] = parts.house
    if parts.apartment:
        full["apartment"] = parts.apartment
    if parts.country_name:
        country_meta = ms.find_country_meta(parts.country_name)
        if country_meta:
            full["country"] = country_meta
    return full or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pages", type=int, default=10, help="страниц InSales по 100 заказов")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)
    insales = InSalesClient(cfg)

    ok, detail = insales.check_access()
    if not ok:
        raise SystemExit(f"InSales недоступен ({detail}) — нужен российский IP")

    orders = insales.get_orders(per_page=100)[: args.pages * 100]
    print(f"InSales-заказов получено: {len(orders)}\n")

    attr_base = (f"{MS_BASE_URL}/entity/customerorder/metadata/"
                 f"attributes/{cfg.MS_ATTR_ORDER_NUMBER_ID}")
    fixed = skipped = notfound = 0
    for o in orders:
        number = o.get("number")
        parts = build_insales_address_parts(
            o.get("delivery_info") or {}, o.get("shipping_address") or {},
            o.get("delivery_title", ""))
        full = build_full(ms, parts)
        if not full:
            skipped += 1
            continue

        rows = ms.find_by_filter("customerorder", f"{attr_base}={number} Tangemshop")
        if not rows:
            notfound += 1
            continue
        ms_order = rows[0]

        summary = ", ".join(f"{k}={v}" for k, v in full.items() if k != "country")
        print(f"  {ms_order['name']} (insales {number}): {summary}")
        if not args.dry_run:
            ms.put(f"entity/customerorder/{ms_order['id']}",
                   {"shipmentAddressFull": full})
        fixed += 1

    print(f"\nИтого: записано {fixed}, без адреса (самовывоз и пр.) {skipped}, "
          f"нет в МС {notfound}")
    if args.dry_run:
        print("(dry-run, ничего не записано)")


if __name__ == "__main__":
    main()
