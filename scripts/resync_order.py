"""Ручной тест обратной синхронизации полей одного заказа МС (TODO §4).

  python scripts/resync_order.py --order 04078 --dry-run   # показать план, не писать
  python scripts/resync_order.py --order 04078             # применить

Принимает имя (name) или id заказа покупателя.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.field_resync import FieldResync
from woo_moysklad.ms_client import MoySkladClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True, help="имя (name) или id заказа покупателя МС")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)
    rs = FieldResync(cfg, ms)

    if re.fullmatch(r"[0-9a-f-]{36}", args.order):
        order = ms.get(f"entity/customerorder/{args.order}")
    else:
        rows = ms.get("entity/customerorder", params={"filter": f"name={args.order}"}).get("rows", [])
        if not rows:
            raise SystemExit(f"Заказ {args.order!r} не найден")
        order = rows[0]

    print(f"Заказ {order.get('name')} (id {order['id']})")
    res = rs.resync_order(order, dry_run=args.dry_run)
    if res is None:
        print("  изменений нет (поля уже корректны / неизвестная оплата / Маркетплейс)")
    else:
        print("  план изменений:\n" + json.dumps(res["plan"], ensure_ascii=False, indent=2))
        print("  (dry-run, ничего не записано)" if args.dry_run else "  ✓ записано в МС")


if __name__ == "__main__":
    main()
