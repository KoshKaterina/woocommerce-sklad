# Признак товара со склада "Вскрытые" по названию позиции заказа

import pytest

from woo_moysklad.core.product_matcher import ProductMatcher


@pytest.mark.parametrize("name", [
    "Ledger Nano X (из видеообзора)",
    "Ledger Nano X (витринный образец)",
    "Tangem 2.0 (3 карты) (Витринный образец)",
])
def test_opened_markers_strip_sku_suffix(name):
    assert ProductMatcher._parse_video_review_sku("HW-46-1", name) == ("HW-46", True)


def test_regular_product_keeps_sku():
    assert ProductMatcher._parse_video_review_sku("HW-46-1", "Ledger Nano X") == ("HW-46-1", False)


def test_find_product_uses_clean_sku_for_showcase_sample():
    class FakeMs:
        def __init__(self):
            self.filters = []

        def find_by_filter(self, entity, flt):
            self.filters.append(flt)
            return [{"meta": {"href": "x"}, "id": "p1"}]

    ms = FakeMs()
    meta, is_opened = ProductMatcher(ms).find_product("HW-46-1", "Ledger Nano X (витринный образец)")
    assert is_opened is True
    assert meta == {"meta": {"href": "x"}}
    assert ms.filters == ["article=HW-46"]
