"""Ретро-бэкфилл ym_client_id (ClientID Яндекс.Метрики) в заказы МС из WC (разово, 2026-06).

Идёт по WC-заказам с указанной даты создания, берёт у тех, где в meta_data есть
ym_client_id, находит соответствующий «Заказ покупателя» в МС по доп.полю
«Номер заказа на сайте» (для WC это id заказа) и проставляет «id (для метрики)»,
если оно пустое или отличается. Прочие атрибуты не затрагиваются — МС мержит
attributes по id, поэтому шлём только наш один атрибут.

Только WC: Метрика про сайт sunscrypt.ru; InSales ym_client_id не передаёт.

  python scripts/backfill_ym_client_id.py --after 2026-03-01 --dry-run
  python scripts/backfill_ym_client_id.py --after 2026-03-01
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.field_mappers import build_attribute, extract_ym_client_id
from woo_moysklad.ms_client import MS_BASE_URL, MoySkladClient
from woo_moysklad.woocommerce.client import WooCommerceClient


def _retry(fn, attempts: int = 8, pause: int = 30):
    """Длинный ретрай поверх коротких ретраев ms_client (DNS-сбои на не-РФ IP)."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            print(f"\n  сбой запроса: {e}; повтор через {pause}с [{i + 1}/{attempts}]")
            time.sleep(pause)


def find_ms_order(ms: MoySkladClient, attr_order_number_id: str, order_number: str):
    """Найти заказ МС по доп.полю «Номер заказа на сайте» (как OrderProcessor)."""
    filter_str = (f"{MS_BASE_URL}/entity/customerorder/metadata/"
                  f"attributes/{attr_order_number_id}={order_number}")
    rows = _retry(lambda: ms.find_by_filter("customerorder", filter_str))
    return rows[0] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--after", default="2026-03-01", help="дата создания WC, ISO (вкл.)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)
    wc = WooCommerceClient(cfg)

    after = f"{args.after}T00:00:00"
    orders = wc.get_orders(after=after)
    # Только заказы, где WC реально проставил ym_client_id
    candidates = []
    for o in orders:
        ym = extract_ym_client_id(o)
        if ym:
            candidates.append((str(o["id"]), ym))
    print(f"WC-заказов с {after}: {len(orders)}; из них с ym_client_id: {len(candidates)}\n")

    written = already = not_found = 0
    for order_number, ym in candidates:
        ms_order = find_ms_order(ms, cfg.MS_ATTR_ORDER_NUMBER_ID, order_number)
        if not ms_order:
            print(f"  WC {order_number}: заказ в МС не найден — пропуск")
            not_found += 1
            continue

        current = next((a.get("value") for a in ms_order.get("attributes", [])
                        if a["id"] == cfg.MS_ATTR_YM_CLIENT_ID), None)
        if str(current or "") == ym:
            already += 1
            continue

        attr = build_attribute(cfg.MS_ATTR_YM_CLIENT_ID, ym)
        action = "запишем" if not current else f"перезапишем («{current}» →)"
        print(f"  WC {order_number} → МС {ms_order['name']}: {action} «{ym}»")
        if not args.dry_run:
            _retry(lambda: ms.put(f"entity/customerorder/{ms_order['id']}",
                                  {"attributes": [attr]}))
        written += 1

    print(f"\nИтого: записано {written}, уже стояло {already}, "
          f"не найдено в МС {not_found}")
    if args.dry_run:
        print("(dry-run, ничего не записано)")


if __name__ == "__main__":
    main()
