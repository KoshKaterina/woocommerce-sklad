"""Тесты обратной синхронизации полей (FieldResync, TODO §4)."""

from woo_moysklad.core.field_resync import (
    FieldResync,
    categorize_payment,
    compute_desired,
)


class Cfg:
    MS_ATTR_PAYMENT_METHOD_ID = "a-paymethod"
    MS_ATTR_ESTIMATED_COST_ID = "a-est"
    MS_ATTR_DELIVERY_COST_ID = "a-del"
    MS_ATTR_TOTAL_TO_PAY_ID = "a-total"
    MS_ATTR_PAYMENT_TYPE_ID = "a-ptype"
    MS_CUSTOMENTITY_PAYMENT_TYPE_ID = "dict-pt"
    MS_PAYMENT_TYPE_PREPAID_ID = "elem-1"
    MS_PAYMENT_TYPE_NONCASH_ID = "elem-2"
    MS_SALES_CHANNEL_MARKETPLACE_ID = "mp-1"


# --- categorize_payment ---

def test_categorize_on_card_is_manual_prepaid():
    # WC-метод «На карту» → промо (обнуление СДЭК)
    assert categorize_payment("На карту") == "manual_prepaid"
    assert categorize_payment("оплата НА КАРТУ") == "manual_prepaid"

def test_categorize_bank_is_manual_prepaid():
    assert categorize_payment("Банковский перевод") == "manual_prepaid"
    assert categorize_payment("банковским переводом") == "manual_prepaid"

def test_categorize_insales_kartoy_is_prepaid_no_zero():
    # InSales «Оплата картой»/«Оплата онлайн» → предоплата БЕЗ обнуления
    assert categorize_payment("Оплата картой") == "prepaid"
    assert categorize_payment("Оплата онлайн") == "prepaid"

def test_categorize_cod():
    assert categorize_payment("При получении") == "cod"
    assert categorize_payment("наложенный платёж") == "cod"

def test_categorize_nalich_not_cod():
    # «налич» намеренно НЕ считается COD
    assert categorize_payment("Оплата наличными") is None

def test_categorize_unknown_and_empty():
    assert categorize_payment("Биткоин") is None
    assert categorize_payment("") is None
    assert categorize_payment(None) is None


# --- compute_desired ---

GOODS = {"id": "p1", "type": "goods", "name": "Tangem", "price": 760000, "quantity": 1}
CDEK = {"id": "p2", "type": "service", "name": "CDEK: Самовывоз", "price": 79900, "quantity": 1}
COURIER = {"id": "p3", "type": "service", "name": "Курьер по Москве", "price": 50000, "quantity": 1}


def test_desired_cod():
    d = compute_desired([GOODS, CDEK], "cod", Cfg)
    assert d["estimated"] == 7600
    assert d["delivery"] == 799
    assert d["total_to_pay"] == 8399          # всё включая доставку
    assert d["payment_element"] == "elem-2"   # noncash
    assert d["zero_position_ids"] == []


def test_desired_manual_prepaid_zeroes_cdek():
    d = compute_desired([GOODS, CDEK], "manual_prepaid", Cfg)
    assert d["zero_position_ids"] == ["p2"]   # СДЭК обнуляется (на карту/перевод)
    assert d["delivery"] == 0
    assert d["total_to_pay"] == 0             # предоплата
    assert d["payment_element"] == "elem-1"   # prepaid


def test_desired_manual_prepaid_does_not_zero_non_cdek():
    d = compute_desired([GOODS, COURIER], "manual_prepaid", Cfg)
    assert d["zero_position_ids"] == []       # курьер не обнуляем
    assert d["delivery"] == 500
    assert d["total_to_pay"] == 0


def test_desired_prepaid_no_zeroing():
    # InSales «онлайн»/«картой» → предоплата, доставка НЕ обнуляется
    d = compute_desired([GOODS, CDEK], "prepaid", Cfg)
    assert d["zero_position_ids"] == []
    assert d["delivery"] == 799
    assert d["total_to_pay"] == 0
    assert d["payment_element"] == "elem-1"


def test_desired_unknown_leaves_payment_fields():
    d = compute_desired([GOODS, CDEK], None, Cfg)
    assert d["estimated"] == 7600 and d["delivery"] == 799   # позиционные считаем
    assert d["total_to_pay"] is None                         # оплату не трогаем
    assert d["payment_element"] is None


# --- FieldResync.resync_order ---

class FakeMS:
    def __init__(self, positions):
        self._positions = positions
        self.puts = []

    def get(self, path, params=None):
        return {"rows": self._positions}

    def put(self, path, data):
        self.puts.append((path, data))
        return {}


def _ms_positions():
    return [
        {"id": "p1", "quantity": 1, "price": 760000,
         "assortment": {"meta": {"type": "product"}, "name": "Tangem"}},
        {"id": "p2", "quantity": 1, "price": 79900,
         "assortment": {"meta": {"type": "service"}, "name": "CDEK: Самовывоз"}},
    ]


def _order(payment, est, dele, total, ptype_elem, channel=None):
    attrs = [
        {"id": "a-paymethod", "value": payment},
        {"id": "a-est", "value": est},
        {"id": "a-del", "value": dele},
        {"id": "a-total", "value": total},
        {"id": "a-ptype", "value": {"meta": {"href": f"/customentity/dict-pt/{ptype_elem}"}}},
    ]
    o = {"id": "o1", "name": "00001", "attributes": attrs}
    if channel:
        o["salesChannel"] = {"meta": {"href": f"/entity/saleschannel/{channel}"}}
    return o


def test_resync_idempotent_no_writes():
    # Заказ уже корректен (онлайн-предоплата) → ничего не пишем
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    order = _order("Оплата онлайн", 7600, 799, 0, "elem-1")
    assert rs.resync_order(order) is None
    assert ms.puts == []


def test_resync_card_zeroes_and_recomputes():
    # Менеджер сменил оплату на «На карту» → обнулить СДЭК + Стоимость доставки=0
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    order = _order("На карту", 7600, 799, 0, "elem-1")  # был онлайн (elem-1), доставка 799
    res = rs.resync_order(order)
    assert res is not None
    paths = [p[0] for p in ms.puts]
    assert "entity/customerorder/o1/positions/p2" in paths   # обнулили СДЭК
    assert "entity/customerorder/o1" in paths                # обновили атрибуты
    assert "Стоимость доставки" in res["plan"]


def test_resync_insales_kartoy_not_zeroed():
    # InSales «Оплата картой» + СДЭК: предоплата, но доставку НЕ обнуляем → нет записи
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    order = _order("Оплата картой", 7600, 799, 0, "elem-1")
    assert rs.resync_order(order) is None
    assert ms.puts == []


def test_resync_marketplace_skipped():
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    order = _order("На карту", 0, 0, 0, "elem-2", channel="mp-1")
    assert rs.resync_order(order) is None
    assert ms.puts == []


def test_resync_counterparty_only_no_change():
    # «Изменён только контрагент»: поля уже корректны → пересчёт ничего не пишет
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    order = _order("При получении", 7600, 799, 8399, "elem-2")  # корректный COD
    assert rs.resync_order(order) is None
    assert ms.puts == []
