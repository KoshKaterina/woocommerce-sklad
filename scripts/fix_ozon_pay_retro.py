"""Ретро-фикс способа оплаты InSales-заказов в МС (разово, 2026-06).

InSales отдаёт способ оплаты в своих формулировках («Оплата онлайн»,
«Ozon Pay (неактивен)», «Оплата с OZON Pay») — в МС нужен канон WC
«Онлайн-оплата». Скрипт находит заказы МС с такими значениями доп.поля
«Способ оплаты», нормализует их той же функцией, что и интеграция
(normalize_insales_payment_title), и ставит «Прием платежа» = предоплата,
если он не заполнен.

  python scripts/fix_ozon_pay_retro.py --dry-run
  python scripts/fix_ozon_pay_retro.py
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.core.field_mappers import build_attribute
from woo_moysklad.insales.normalizer import normalize_insales_payment_title
from woo_moysklad.ms_client import MS_BASE_URL, MoySkladClient

# Точные известные значения InSales (поиск по `=`: подстрочный `~онлайн`
# зацепил бы и тысячи WC-заказов с каноном «Онлайн-оплата», а find_by_filter
# не пагинирует). Сами значения чинит normalize_insales_payment_title.
SEARCH_TERMS = (
    "Оплата онлайн",
    "Оплата онлайн (неактивен)",
    "Ozon Pay (неактивен)",
    "Оплата с OZON Pay",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)

    attr_base = (f"{MS_BASE_URL}/entity/customerorder/metadata/"
                 f"attributes/{cfg.MS_ATTR_PAYMENT_METHOD_ID}")
    orders: dict[str, dict] = {}
    for term in SEARCH_TERMS:
        for row in ms.find_by_filter("customerorder", f"{attr_base}={term}"):
            orders[row["id"]] = row
    print(f"Заказов-кандидатов: {len(orders)}\n")

    fixed = 0
    for order in orders.values():
        attrs = {a["id"]: a for a in order.get("attributes", [])}
        current = str(attrs.get(cfg.MS_ATTR_PAYMENT_METHOD_ID, {}).get("value", ""))
        canonical = normalize_insales_payment_title(current)
        if canonical == current:
            continue  # уже канон («Онлайн-оплата» тоже матчится по ~онлайн)

        new_attrs = [build_attribute(cfg.MS_ATTR_PAYMENT_METHOD_ID, canonical)]
        set_pt = cfg.MS_ATTR_PAYMENT_TYPE_ID not in attrs
        if set_pt:
            new_attrs.append(build_attribute(
                cfg.MS_ATTR_PAYMENT_TYPE_ID, "custom",
                is_custom_entity=True,
                dictionary_id=cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID,
                element_id=cfg.MS_PAYMENT_TYPE_PREPAID_ID,
            ))

        print(f"  {order['name']}: «{current}» → «{canonical}»"
              + (" + Прием платежа = предоплата" if set_pt else ""))
        if not args.dry_run:
            ms.put(f"entity/customerorder/{order['id']}", {"attributes": new_attrs})
        fixed += 1

    print(f"\nИтого: исправлено {fixed}")
    if args.dry_run:
        print("(dry-run, ничего не записано)")


if __name__ == "__main__":
    main()
