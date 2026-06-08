"""Расчёт упаковки заказа как услуг-позиций МС.

По суммарному объёму товаров заказа подбираем упаковку: фирменные пакеты двух
размеров или картонную коробку. Услуги добавляются в заказ с ценой 0 (маркер
для склада/сборки) — для всех каналов, КРОМЕ Tangemshop.

Объём — нативное поле `volume` товара МС (м³). Если объём товаров не задан
(суммарно 0), упаковку НЕ подбираем (нет данных для решения).

Алгоритм (compute_packaging):
  1. Если хотя бы один товар поштучно крупнее большого пакета (> BIG_BAG_CAP) —
     сразу одна «Картонная коробка».
  2. Иначе по суммарному объёму V = Σ(объём × кол-во):
       V ≤ SMALL_BAG_CAP                  → 1× пакет 170*300
       SMALL_BAG_CAP < V ≤ BIG_BAG_CAP    → 1× пакет 240*330
       V > BIG_BAG_CAP                    → жадно набиваем пакетами (см. ниже)
  3. Жадная набивка при V > BIG_BAG_CAP: заполняем большими пакетами 240*330,
     остаток ≤ SMALL_BAG_CAP кладём в маленький 170*300. Не более MAX_BAGS
     пакетов суммарно; если не помещается — одна «Картонная коробка».
"""

SMALL_BAG = "Фирменный пакет Sunscrypt 170*300"
BIG_BAG = "Фирменный пакет Sunscrypt 240*330"
BOX = "Картонная коробка"

# Имена всех упаковочных услуг — для распознавания позиций в reverse-sync.
PACKAGING_SERVICE_NAMES = frozenset({SMALL_BAG, BIG_BAG, BOX})

# Вместимости (м³) и лимит количества пакетов.
SMALL_BAG_CAP = 0.00065
BIG_BAG_CAP = 0.015
MAX_BAGS = 3

_EPS = 1e-9


def compute_packaging(items) -> list[tuple[str, int]]:
    """Подобрать упаковку для товаров заказа.

    items: итерируемое из (unit_volume_m3: float, quantity: int|float) —
           объём ОДНОЙ единицы товара и количество.
    Возвращает список (имя_услуги, количество). Пустой список, если объём
    товаров не задан (суммарно 0) — упаковку не подбираем.
    """
    items = [(float(v or 0), q) for v, q in items if q and q > 0]
    if not items:
        return []

    # 1. Один товар крупнее большого пакета → сразу коробка.
    if any(v > BIG_BAG_CAP + _EPS for v, _ in items):
        return [(BOX, 1)]

    total = sum(v * q for v, q in items)
    if total <= _EPS:
        return []  # нет данных об объёме — не решаем

    if total <= SMALL_BAG_CAP + _EPS:
        return [(SMALL_BAG, 1)]
    if total <= BIG_BAG_CAP + _EPS:
        return [(BIG_BAG, 1)]

    # 3. Жадная набивка пакетами разного размера, не более MAX_BAGS.
    bags: list[str] = []
    remaining = total
    while remaining > _EPS and len(bags) < MAX_BAGS:
        if remaining <= SMALL_BAG_CAP + _EPS:
            bags.append(SMALL_BAG)
            remaining = 0.0
        elif remaining <= BIG_BAG_CAP + _EPS:
            bags.append(BIG_BAG)
            remaining = 0.0
        else:
            bags.append(BIG_BAG)
            remaining -= BIG_BAG_CAP

    if remaining > _EPS:
        return [(BOX, 1)]  # не уместилось в MAX_BAGS пакетов → коробка

    counts: dict[str, int] = {}
    for b in bags:
        counts[b] = counts.get(b, 0) + 1
    return [(name, counts[name]) for name in (BIG_BAG, SMALL_BAG) if name in counts]
