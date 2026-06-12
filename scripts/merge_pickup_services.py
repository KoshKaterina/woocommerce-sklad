"""Слить дубли услуги «Самовывоз из офиса Sunscrypt» в МС (разовая чистка, 2026-06).

В МС две услуги с одинаковым именем. Скрипт:
1. Находит обе, считает использование в заказах покупателя и отгрузках
   (скан всех документов с expand=positions.assortment).
2. У менее используемой заменяет её позиции на более используемую
   (PUT позиции с новым assortment).
3. Удаляет менее используемую услугу.

  python scripts/merge_pickup_services.py --dry-run   # только посчитать и показать план
  python scripts/merge_pickup_services.py             # применить
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from woo_moysklad.config import load_config
from woo_moysklad.ms_client import MoySkladClient

SERVICE_NAME = "Самовывоз из офиса Sunscrypt"
DOC_TYPES = ("customerorder", "demand")
PAGE = 100


def _retry(fn, attempts: int = 8, pause: int = 30):
    """Длинный ретрай поверх коротких ретраев ms_client.

    Скан идёт >10 минут; минутный сбой DNS (известная особенность не-РФ IP)
    не должен убивать весь прогон.
    """
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1:
                raise
            print(f"\n  сбой запроса: {e}; повтор через {pause}с [{i + 1}/{attempts}]")
            time.sleep(pause)


def find_duplicate_services(ms: MoySkladClient) -> list[dict]:
    rows = ms.get("entity/service", params={"filter": f"name={SERVICE_NAME}"}).get("rows", [])
    rows = [r for r in rows if r["name"] == SERVICE_NAME]
    if len(rows) != 2:
        raise SystemExit(f"Ожидались ровно 2 услуги «{SERVICE_NAME}», найдено {len(rows)}: "
                         f"{[r['id'] for r in rows]}")
    return rows


def scan_usages(ms: MoySkladClient, service_ids: set[str]) -> dict[str, list[dict]]:
    """Все позиции документов с указанными услугами.

    Возвращает {service_id: [{"doc_type", "doc_id", "doc_name", "position_id"}, ...]}.
    """
    usages: dict[str, list[dict]] = {sid: [] for sid in service_ids}
    for doc_type in DOC_TYPES:
        offset = 0
        total = None
        while total is None or offset < total:
            resp = _retry(lambda: ms.get(f"entity/{doc_type}", params={
                "limit": PAGE, "offset": offset, "expand": "positions",
            }))
            total = resp["meta"]["size"]
            for doc in resp.get("rows", []):
                positions = doc.get("positions", {})
                pos_rows = positions.get("rows", [])
                # expand отдаёт максимум 100 вложенных позиций — добираем хвост
                if positions.get("meta", {}).get("size", 0) > len(pos_rows):
                    pos_rows = _retry(lambda: ms.get(
                        f"entity/{doc_type}/{doc['id']}/positions",
                        params={"limit": 1000})).get("rows", [])
                for pos in pos_rows:
                    href = pos.get("assortment", {}).get("meta", {}).get("href", "")
                    a_id = href.rstrip("/").split("/")[-1].split("?")[0]
                    if a_id in service_ids:
                        usages[a_id].append({
                            "doc_type": doc_type, "doc_id": doc["id"],
                            "doc_name": doc.get("name", "?"), "position_id": pos["id"],
                        })
            offset += PAGE
            print(f"  {doc_type}: просмотрено {min(offset, total)}/{total}", end="\r")
        print()
    return usages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    ms = MoySkladClient(cfg)

    a, b = find_duplicate_services(ms)
    print(f"Дубли «{SERVICE_NAME}»:\n  A: {a['id']}\n  B: {b['id']}\n")

    print("Скан документов…")
    usages = scan_usages(ms, {a["id"], b["id"]})
    for svc in (a, b):
        print(f"  {svc['id']}: {len(usages[svc['id']])} позиций")

    keeper, loser = (a, b) if len(usages[a["id"]]) >= len(usages[b["id"]]) else (b, a)
    loser_usages = usages[loser["id"]]
    print(f"\nОставляем:  {keeper['id']} ({len(usages[keeper['id']])} позиций)")
    print(f"Заменяем и удаляем: {loser['id']} ({len(loser_usages)} позиций)")
    for u in loser_usages:
        print(f"  {u['doc_type']} {u['doc_name']} → позиция {u['position_id']}")

    if args.dry_run:
        print("\n(dry-run, ничего не записано)")
        return

    keeper_meta = {"meta": keeper["meta"]}
    for u in loser_usages:
        _retry(lambda u=u: ms.put(
            f"entity/{u['doc_type']}/{u['doc_id']}/positions/{u['position_id']}",
            {"assortment": keeper_meta}))
        print(f"  ✓ заменено: {u['doc_type']} {u['doc_name']}")

    ms.delete(f"entity/service/{loser['id']}")
    print(f"\n✓ услуга-дубль {loser['id']} удалена, осталась {keeper['id']}")


if __name__ == "__main__":
    main()
