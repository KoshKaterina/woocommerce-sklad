"""Ретро-фикс способа оплаты Ozon Pay в заказах МС (разово, 2026-06).

InSales отдаёт способ оплаты как «Ozon Pay (неактивен)» / «Оплата с OZON Pay» —
это онлайн-оплата. Скрипт находит заказы МС с «Ozon Pay» в доп.поле
«Способ оплаты» и ставит: «Способ оплаты» = «Онлайн-оплата»,
«Прием платежа» = предоплата (только если не заполнен).

  python scripts/fix_ozon_pay_retro.py --dry-run
  python scripts/fix_ozon_pay_retro.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.field_mappers import build_attribute
from woo_moysklad.ms_client import MS_BASE_URL, MoySkladClient

CANONICAL_TITLE = "Онлайн-оплата"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)

    attr_filter = (f"{MS_BASE_URL}/entity/customerorder/metadata/"
                   f"attributes/{cfg.MS_ATTR_PAYMENT_METHOD_ID}~Ozon")
    rows = ms.find_by_filter("customerorder", attr_filter)
    print(f"Заказов с «Ozon Pay» в способе оплаты: {len(rows)}\n")

    fixed = 0
    for order in rows:
        attrs = {a["id"]: a for a in order.get("attributes", [])}
        current = attrs.get(cfg.MS_ATTR_PAYMENT_METHOD_ID, {}).get("value", "")
        if "ozon pay" not in str(current).lower():
            continue

        new_attrs = [build_attribute(cfg.MS_ATTR_PAYMENT_METHOD_ID, CANONICAL_TITLE)]
        set_pt = cfg.MS_ATTR_PAYMENT_TYPE_ID not in attrs
        if set_pt:
            new_attrs.append(build_attribute(
                cfg.MS_ATTR_PAYMENT_TYPE_ID, "custom",
                is_custom_entity=True,
                dictionary_id=cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID,
                element_id=cfg.MS_PAYMENT_TYPE_PREPAID_ID,
            ))

        print(f"  {order['name']}: «{current}» → «{CANONICAL_TITLE}»"
              + (" + Прием платежа = предоплата" if set_pt else ""))
        if not args.dry_run:
            ms.put(f"entity/customerorder/{order['id']}", {"attributes": new_attrs})
        fixed += 1

    print(f"\nИтого: исправлено {fixed}")
    if args.dry_run:
        print("(dry-run, ничего не записано)")


if __name__ == "__main__":
    main()
