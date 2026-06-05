"""Миграция доп. полей заказа покупателя: СТАРОЕ → новые поля.

Переносит значения из 6 переименованных полей (получивших суффикс "СТАРОЕ")
в новые одноимённые поля и очищает старые. Работает ТОЛЬКО с attributes заказа:
в PUT отправляется лишь {"attributes": [...]}, поэтому позиции, отгрузки и услуги
не затрагиваются (это отдельные коллекции MoySklad).

Безопасность:
  * перед каждой записью полное состояние заказа (old+new значения + кол-во позиций)
    логируется в data/ms_attr_migration_backup.jsonl — миграция полностью обратима;
  * если НОВОЕ поле уже заполнено — поле пропускается (старое не трогаем);
  * после PUT заказ перечитывается и сверяется кол-во позиций (защита от потери товаров);
  * backup-файл служит и журналом сделанного: при --all уже обработанные заказы пропускаются.

Запуск:
  python scripts/migrate_ms_attrs.py --order 03909 --dry-run   # показать план по 1 заказу
  python scripts/migrate_ms_attrs.py --order 03909             # применить к 1 заказу
  python scripts/migrate_ms_attrs.py --all --dry-run           # план по всем (только чтение)
  python scripts/migrate_ms_attrs.py --all                     # применить ко всем (с 2026-03-01)
"""
import argparse
import json
import os
import re
import sys
import time

import requests
from dotenv import load_dotenv

BASE = "https://api.moysklad.ru/api/remap/1.2"
DATE_FROM = "2026-03-01 00:00:00"
BACKUP_PATH = "data/ms_attr_migration_backup.jsonl"
SLEEP = 0.35  # пауза между запросами (rate limit MS)

# Справочник "Прием платежа" — новый: элементы с именами "1"/"2"/"3"
PAYMENT_NEW_DICT = "00a648ac-60ac-11f1-0a80-1cc60006b0c8"
PAYMENT_NEW_ELEM = {
    1: "0db95b3b-60ac-11f1-0a80-1b9f0005d237",  # "1"
    2: "16bb90ce-60ac-11f1-0a80-11190005b58b",  # "2"
    3: "1ed04af8-60ac-11f1-0a80-1ef30005d75c",  # "3"
}

# Поля: ключ → (OLD id, OLD тип, NEW id, NEW тип)
FIELDS = {
    "delivery_type": ("10e587ca-2aa3-11f1-0a80-0f48002ee65f", "customentity",
                      "8c337f77-5d2b-11f1-0a80-1cae0026fe2e", "long"),
    "delivery_cost": ("04fee4e9-2aa5-11f1-0a80-0d860032db59", "string",
                      "6197cf57-5d04-11f1-0a80-0e1800256067", "double"),
    "estimated_cost": ("04fee891-2aa5-11f1-0a80-0d860032db5a", "string",
                       "6197d336-5d04-11f1-0a80-0e1800256068", "double"),
    "total_to_pay": ("c5f954c4-2aa3-11f1-0a80-138c003062fd", "string",
                     "80814b14-5d04-11f1-0a80-1d5a00242f6e", "double"),
    "courier_comment": ("d787efae-2aa4-11f1-0a80-145f0031ba34", "text",
                        "ed537fe2-5d04-11f1-0a80-0e18002576eb", "text"),
    "payment_type": ("cbe57ab4-2aa4-11f1-0a80-1a29003029a1", "customentity",
                     "574102c9-60ac-11f1-0a80-0e5500051d84", "customentity"),
}


def attr_meta(attr_id):
    return {"href": f"{BASE}/entity/customerorder/metadata/attributes/{attr_id}",
            "type": "attributemetadata", "mediaType": "application/json"}


def ce_value(dict_id, elem_id):
    return {"meta": {"href": f"{BASE}/entity/customentity/{dict_id}/{elem_id}",
                     "type": "customentity", "mediaType": "application/json"}}


def _leading_int(s):
    m = re.match(r"\s*(\d+)", str(s))
    return int(m.group(1)) if m else None


def raw_value(attr):
    """Извлечь "сырое" значение attribute: для customentity — name элемента."""
    if attr is None:
        return None
    v = attr.get("value")
    if isinstance(v, dict):
        return v.get("name")
    return v


def is_empty(attr):
    v = raw_value(attr)
    return v is None or (isinstance(v, str) and v.strip() == "")


def convert(key, old_attr):
    """Вернуть value для нового поля по правилам конвертации. None → не переносим."""
    _, _, new_id, new_type = FIELDS[key]
    val = raw_value(old_attr)
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return None

    if key == "delivery_type":
        n = _leading_int(val)
        return n  # long
    if key == "payment_type":
        n = _leading_int(val)
        elem = PAYMENT_NEW_ELEM.get(n)
        if elem is None:
            raise ValueError(f"payment_type: не распознан элемент {val!r}")
        return ce_value(PAYMENT_NEW_DICT, elem)
    if new_type == "double":
        return float(str(val).replace(",", ".").strip())
    if new_type == "text":
        return str(val)
    raise ValueError(f"Неизвестное поле {key}")


def build_set_attr(key, value):
    old_id, _, new_id, new_type = FIELDS[key]
    if new_type == "customentity":
        return {"meta": attr_meta(new_id), "value": value}
    return {"meta": attr_meta(new_id), "value": value}


def plan_order(order):
    """Вернуть (changes, set_attrs, clear_attrs, notes) для заказа."""
    amap = {a["id"]: a for a in order.get("attributes", [])}
    set_attrs, clear_attrs, changes, notes = [], [], {}, []
    for key, (old_id, _, new_id, new_type) in FIELDS.items():
        old_attr = amap.get(old_id)
        new_attr = amap.get(new_id)
        if is_empty(old_attr):
            continue  # нечего переносить
        if not is_empty(new_attr):
            notes.append(f"{key}: НОВОЕ уже заполнено ({raw_value(new_attr)!r}) — пропуск")
            continue
        try:
            value = convert(key, old_attr)
        except Exception as e:
            notes.append(f"{key}: ОШИБКА конвертации {raw_value(old_attr)!r}: {e} — пропуск")
            continue
        if value is None:
            continue
        set_attrs.append(build_set_attr(key, value))
        clear_attrs.append({"meta": attr_meta(old_id), "value": None})  # очистить старое
        changes[key] = {"old": raw_value(old_attr), "new": value}
    return changes, set_attrs, clear_attrs, notes


def positions_size(order):
    return order.get("positions", {}).get("meta", {}).get("size")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--order", help="имя (name) или id одного заказа")
    g.add_argument("--all", action="store_true", help="все заказы с 2026-03-01")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_dotenv(".env")
    tok = os.environ["MS_TOKEN"]
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip",
                      "Content-Type": "application/json"})

    os.makedirs("data", exist_ok=True)
    done = set()
    if args.all and os.path.exists(BACKUP_PATH):
        with open(BACKUP_PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["order_id"])
                except Exception:
                    pass

    def fetch_one(ident):
        # по id
        if re.fullmatch(r"[0-9a-f-]{36}", ident):
            r = s.get(f"{BASE}/entity/customerorder/{ident}", timeout=30)
            r.raise_for_status()
            return r.json()
        # по name
        r = s.get(f"{BASE}/entity/customerorder", params={"filter": f"name={ident}"}, timeout=30)
        r.raise_for_status()
        rows = r.json()["rows"]
        if not rows:
            sys.exit(f"Заказ {ident!r} не найден")
        return rows[0]

    def process(order, log_f):
        oid, name = order["id"], order["name"]
        changes, set_attrs, clear_attrs, notes = plan_order(order)
        pos_before = positions_size(order)
        for n in notes:
            print(f"   ⚠ {name}: {n}")
        if not changes:
            return "skip"
        summary = ", ".join(f"{k}: {v['old']!r}→{v['new']!r}" for k, v in changes.items())
        print(f"   {name}: {summary}")
        if args.dry_run:
            return "dry"
        # backup ДО записи
        backup = {"order_id": oid, "name": name, "positions_size": pos_before,
                  "changes": {k: {"old": v["old"]} for k, v in changes.items()},
                  "old_attrs_full": [a for a in order.get("attributes", [])
                                     if a["id"] in [FIELDS[k][0] for k in changes]
                                     or a["id"] in [FIELDS[k][2] for k in changes]]}
        log_f.write(json.dumps(backup, ensure_ascii=False) + "\n")
        log_f.flush()
        # PUT только attributes
        body = {"attributes": set_attrs + clear_attrs}
        r = s.put(f"{BASE}/entity/customerorder/{oid}", data=json.dumps(body), timeout=30)
        if r.status_code != 200:
            print(f"   ❌ {name}: PUT {r.status_code}: {r.text[:300]}")
            return "error"
        time.sleep(SLEEP)
        # верификация: позиции не изменились
        updated = r.json()
        pos_after = positions_size(updated)
        if pos_before != pos_after:
            print(f"   ❌❌ {name}: ПОЗИЦИИ ИЗМЕНИЛИСЬ {pos_before}→{pos_after}! Стоп.")
            sys.exit(1)
        return "ok"

    counts = {"ok": 0, "skip": 0, "error": 0, "dry": 0}
    with open(BACKUP_PATH, "a", encoding="utf-8") as log_f:
        if args.order:
            order = fetch_one(args.order)
            print(f"Заказ {order['name']} (позиций: {positions_size(order)})")
            res = process(order, log_f)
            counts[res] = counts.get(res, 0) + 1
        else:
            offset = 0
            while True:
                r = s.get(f"{BASE}/entity/customerorder",
                          params={"filter": f"created>={DATE_FROM}", "limit": 100,
                                  "offset": offset, "order": "created,asc"}, timeout=30)
                r.raise_for_status()
                rows = r.json()["rows"]
                if not rows:
                    break
                for order in rows:
                    if order["id"] in done:
                        counts["skip"] += 1
                        continue
                    res = process(order, log_f)
                    counts[res] = counts.get(res, 0) + 1
                offset += 100
                print(f"  ...обработано до offset={offset}, итог: {counts}")
                time.sleep(SLEEP)
    print(f"\nГОТОВО: {counts}")


if __name__ == "__main__":
    main()
