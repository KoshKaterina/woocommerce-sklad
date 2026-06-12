"""Ретро-фикс имён контрагентов: дописать фамилию из WC-заказов (разово, 2026-06).

До 2026-06 дозаполнение контрагента чинило только заглушки-телефоны: если
контрагент существовал с неполным именем («Александр», напр. из amoCRM),
фамилия из заказа не дописывалась. Скрипт идёт по WC-заказам за N дней
и применяет ту же логику, что и новое дозаполнение: имя обновляется, если
существующее — заглушка без букв ИЛИ префикс имени из заказа («Александр» →
«Александр Лазарев»). Прочие имена (правки менеджера) НЕ трогает.

  python scripts/fix_counterparty_names_retro.py --days 5 --dry-run
  python scripts/fix_counterparty_names_retro.py --days 5
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.counterparty_handler import (
    _has_letters,
    _is_name_extension,
    normalize_phone,
    split_full_name,
)
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.woocommerce.client import WooCommerceClient


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)
    wc = WooCommerceClient(cfg)

    after = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%S")
    orders = wc.get_orders(after=after)
    print(f"WC-заказов с {after}: {len(orders)}\n")

    seen_phones: set[str] = set()
    fixed = skipped = 0
    for order in orders:
        billing = order.get("billing", {})
        # На чекауте ФИО целиком пишется в first_name; last_name обычно пуст
        full_name = " ".join(filter(None, [(billing.get("first_name") or "").strip(),
                                           (billing.get("last_name") or "").strip()]))
        phone = (billing.get("phone") or "").strip()
        if not phone or not _has_letters(full_name):
            continue
        normalized = normalize_phone(phone)
        if normalized in seen_phones:
            continue
        seen_phones.add(normalized)

        rows = ms.find_by_filter("counterparty", f"phone={normalized}")
        if not rows:
            print(f"  заказ {order['id']}: контрагент по {normalized} не найден — пропуск")
            continue
        cp = rows[0]
        existing = (cp.get("name") or "").strip()

        # Та же логика, что в _build_enrichment_patch: заглушка или расширение имени
        if _has_letters(existing) and not _is_name_extension(existing, full_name):
            skipped += 1
            continue
        f, l, m = split_full_name(full_name)
        patch = {"name": full_name, "firstName": f, "lastName": l, "middleName": m}
        print(f"  заказ {order['id']}: «{existing}» → «{full_name}» (id {cp['id']})")
        if not args.dry_run:
            ms.put(f"entity/counterparty/{cp['id']}", patch)
        fixed += 1

    print(f"\nИтого: исправлено {fixed}, пропущено (имя уже отличается) {skipped}")
    if args.dry_run:
        print("(dry-run, ничего не записано)")


if __name__ == "__main__":
    main()
