"""РАЗОВЫЙ ретро-фикс адресов заказов МС, созданных новым кодом адресов
(2026-06-08 12:50 … 2026-06-11), до фикса дублей/меты CDEK.

Что чинит:
1. «Другое» (addInfo) = дубль города / мусорный shipping.state → очищает.
   Исключение: addInfo с цифрами (похоже на реальный адрес) — не трогает.
2. Самовывоз из офиса: фиктивный адрес («Россия, Москва») → очищает целиком.
3. СДЭК-ПВЗ без кода ПВЗ (сбой чекаута, заказ 17130): проставляет код из
   меты CDEK WC-заказа; улицу пытается взять из другого заказа с тем же
   кодом ПВЗ (история WC), без CDEK API.

Запуск:  python scripts/fix_address_retro.py            # dry-run (по умолчанию)
         python scripts/fix_address_retro.py --apply    # применить
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.ms_client import MoySkladClient
from woo_moysklad.woocommerce.client import WooCommerceClient
from woo_moysklad.core.address_parser import parse_wc_address
from woo_moysklad.core.field_mappers import (
    build_attribute,
    detect_delivery_type,
    extract_cdek_meta,
    is_office_pickup,
)

SINCE = "2026-06-08 12:50:00"  # первый заказ нового кода адресов (МС 04165)

FULL_STR_FIELDS = ("postalCode", "city", "street", "house", "apartment", "addInfo")


def _attr_value(order: dict, attr_id: str):
    for a in order.get("attributes", []):
        if attr_id in a.get("meta", {}).get("href", ""):
            return a.get("value")
    return None


def _full_for_put(full: dict, **overrides) -> dict:
    """Полный объект shipmentAddressFull для PUT (строки + meta страны)."""
    body = {f: str(full.get(f) or "") for f in FULL_STR_FIELDS}
    if full.get("country"):
        body["country"] = {"meta": full["country"]["meta"]}
    body.update(overrides)
    return body


def main(apply: bool) -> None:
    cfg = load_config()
    ms = MoySkladClient(cfg)
    wc = WooCommerceClient(cfg)
    NUM, PVZ = cfg.MS_ATTR_ORDER_NUMBER_ID, cfg.MS_ATTR_PVZ_CODE_ID

    # --- заказы МС нового кода ---
    rows, offset = [], 0
    while True:
        r = ms.get("entity/customerorder", params={
            "filter": f"created>={SINCE}", "limit": 100, "offset": offset,
            "order": "created,asc"})
        batch = r.get("rows", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    targets = [o for o in rows
               if _attr_value(o, NUM) and (o.get("shipmentAddressFull") or {}).get("city")]
    print(f"Заказов нового кода в МС: {len(targets)} (с {SINCE})\n")

    # --- история WC: office_code -> address_2 (для восстановления улицы ПВЗ) ---
    pvz_addr_by_code: dict[str, str] = {}
    for page in (1, 2, 3):
        resp = wc.wcapi.get("orders", params={"per_page": 100, "page": page,
                                              "orderby": "date", "order": "desc"})
        resp.raise_for_status()
        page_orders = resp.json()
        for w in page_orders:
            cdek = extract_cdek_meta(w)
            a2 = (w.get("shipping", {}).get("address_2") or "").strip()
            if cdek["office_code"] and a2 and cdek["office_code"] not in pvz_addr_by_code:
                pvz_addr_by_code[cdek["office_code"]] = a2
        if len(page_orders) < 100:
            break

    stats = {"addinfo": 0, "office": 0, "pvz_code": 0, "skip": 0, "err": 0}

    for o in targets:
        num = str(_attr_value(o, NUM))
        name = o.get("name")
        oid = o.get("id")
        full = o.get("shipmentAddressFull") or {}
        try:
            w = wc.get_order(int(num))
        except Exception as e:
            print(f"[{name} / WC {num}] WC-заказ не получен ({e}) — пропуск")
            stats["err"] += 1
            continue

        body: dict = {}
        notes: list[str] = []

        if is_office_pickup(w):
            # офис: фиктивный адрес целиком в мусор
            body["shipmentAddress"] = ""
            body["shipmentAddressFull"] = {f: "" for f in FULL_STR_FIELDS}
            notes.append("офис: очистка фиктивного адреса")
            stats["office"] += 1
        else:
            addinfo = str(full.get("addInfo") or "").strip()
            city = str(full.get("city") or "").strip()
            state = (w.get("shipping", {}).get("state") or "").strip()
            if addinfo:
                if any(ch.isdigit() for ch in addinfo):
                    notes.append(f"addInfo оставлен (адресные данные): {addinfo!r}")
                elif addinfo.lower() in (city.lower(), state.lower()):
                    body["shipmentAddressFull"] = _full_for_put(full, addInfo="")
                    notes.append(f"очистка addInfo (был {addinfo!r})")
                    stats["addinfo"] += 1
                else:
                    notes.append(f"addInfo оставлен (не дубль): {addinfo!r}")

            # СДЭК-ПВЗ без кода ПВЗ → код из меты + улица из истории
            mt = (w.get("shipping_lines") or [{}])[0].get("method_title", "")
            if detect_delivery_type(mt) == "pvz" and not _attr_value(o, PVZ):
                cdek = extract_cdek_meta(w)
                code = cdek["office_code"]
                if code:
                    attr = build_attribute(PVZ, code)
                    if attr:
                        body["attributes"] = [attr]
                        notes.append(f"код ПВЗ ← {code}")
                        stats["pvz_code"] += 1
                    a2 = pvz_addr_by_code.get(code)
                    if a2 and not str(full.get("street") or "").strip():
                        shipping = dict(w.get("shipping", {}), address_2=a2)
                        parts = parse_wc_address(shipping, "pvz", cdek_city=cdek["city"])
                        cur = body.get("shipmentAddressFull") or _full_for_put(full)
                        cur.update({"street": parts.street, "house": parts.house,
                                    "apartment": parts.apartment,
                                    "city": parts.city or cur.get("city", "")})
                        body["shipmentAddressFull"] = cur
                        body["shipmentAddress"] = f"Россия, {a2}"
                        notes.append(f"улица ПВЗ из истории ({a2!r})")

        if not body:
            stats["skip"] += 1
            continue

        print(f"[{name} / WC {num}] " + "; ".join(notes))
        if apply:
            try:
                ms.put(f"entity/customerorder/{oid}", body)
            except Exception as e:
                print(f"   !! ошибка PUT: {e}")
                stats["err"] += 1

    mode = "ПРИМЕНЕНО" if apply else "DRY-RUN (ничего не изменено; --apply для записи)"
    print(f"\n{mode}: очисток addInfo={stats['addinfo']}, офисных адресов={stats['office']}, "
          f"кодов ПВЗ={stats['pvz_code']}, без изменений={stats['skip']}, ошибок={stats['err']}")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
