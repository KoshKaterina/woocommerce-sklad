"""Тесты подбора упаковки по объёму товаров (core/packaging.py)."""

from woo_moysklad.core.packaging import (
    BIG_BAG,
    BIG_BAG_CAP,
    BOX,
    SMALL_BAG,
    SMALL_BAG_CAP,
    compute_packaging,
)


def test_no_volume_data_no_packaging():
    # Объём не задан (0) → не подбираем
    assert compute_packaging([(0, 1), (0, 3)]) == []
    assert compute_packaging([]) == []


def test_small_bag_under_threshold():
    # V ≤ 0.00065 → маленький пакет
    assert compute_packaging([(0.0003, 1)]) == [(SMALL_BAG, 1)]
    assert compute_packaging([(SMALL_BAG_CAP, 1)]) == [(SMALL_BAG, 1)]


def test_big_bag_between_thresholds():
    # 0.00065 < V ≤ 0.015 → большой пакет
    assert compute_packaging([(0.001, 1)]) == [(BIG_BAG, 1)]
    assert compute_packaging([(0.005, 2)]) == [(BIG_BAG, 1)]  # 0.01
    assert compute_packaging([(BIG_BAG_CAP, 1)]) == [(BIG_BAG, 1)]


def test_single_item_over_big_cap_goes_to_box():
    # Один товар поштучно > 0.015 → сразу коробка
    assert compute_packaging([(0.02, 1)]) == [(BOX, 1)]
    # даже если есть мелочь рядом
    assert compute_packaging([(0.02, 1), (0.0001, 5)]) == [(BOX, 1)]


def test_two_big_bags():
    # V=0.016 (unit ≤ cap): большой (0.015) + остаток 0.001 (>small cap) → ещё большой
    assert compute_packaging([(0.008, 2)]) == [(BIG_BAG, 2)]


def test_big_plus_small_mix():
    # V=0.0156 (unit ≤ cap): большой (0.015) + остаток 0.0006 (≤ small cap) → маленький
    res = compute_packaging([(0.0078, 2)])
    assert res == [(BIG_BAG, 1), (SMALL_BAG, 1)]


def test_three_big_bags_max():
    # V=0.045 ровно 3 больших пакета
    assert compute_packaging([(0.009, 5)]) == [(BIG_BAG, 3)]


def test_over_three_bags_goes_to_box():
    # V > 3 больших пакетов (0.045), unit ≤ cap → коробка
    assert compute_packaging([(0.01, 5)]) == [(BOX, 1)]      # 0.05
    assert compute_packaging([(0.015, 4)]) == [(BOX, 1)]     # 0.06


def test_quantity_multiplies_volume():
    # объём × количество
    assert compute_packaging([(0.0004, 1)]) == [(SMALL_BAG, 1)]      # 0.0004
    assert compute_packaging([(0.0004, 2)]) == [(BIG_BAG, 1)]        # 0.0008 > small cap
