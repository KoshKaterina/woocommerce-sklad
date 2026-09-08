"""Тесты обратной синхронизации заказа (FieldResync, TODO §4).

05.08.2026: пересчёт доп. полей «Оценочная стоимость», «Стоимость доставки»,
«Итого к оплате получателем», «Прием платежа» и «Вид доставки» убран вместе с
самими полями (наследие фулфилмента). Осталось обнуление цены СДЭК-доставки.
"""

from woo_moysklad.core.field_resync import (
    FieldResync,
    categorize_payment,
    compute_desired,
)


class Cfg:
    MS_ATTR_PAYMENT_METHOD_ID = "a-paymethod"
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
    # «налич» — не COD, не карта → prepaid по умолчанию
    assert categorize_payment("Оплата наличными") == "prepaid"
    assert categorize_payment("Наличные менеджер") == "prepaid"

def test_categorize_unknown_and_empty():
    assert categorize_payment("Эвотор") == "prepaid"   # терминал — платная доставка
    assert categorize_payment("Биткоин") == "prepaid"  # крипта — платная доставка
    assert categorize_payment("") is None
    assert categorize_payment(None) is None

def test_categorize_wallet_is_prepaid():
    # 08.09.2026: wallet — это оплата криптой, бесплатной доставки больше не даёт
    assert categorize_payment("wallet") == "prepaid"
    assert categorize_payment("Wallet") == "prepaid"

def test_categorize_net_is_manual_prepaid():
    assert categorize_payment("нет") == "manual_prepaid"

def test_categorize_kripta_is_prepaid():
    # 08.09.2026: крипта больше не даёт бесплатную доставку СДЭК (как и wallet)
    assert categorize_payment("Крипта") == "prepaid"
    assert categorize_payment("криптовалюта") == "prepaid"


# --- compute_desired: осталось только обнуление СДЭК ---

GOODS = {"id": "p1", "type": "goods", "name": "Tangem", "price": 760000, "quantity": 1}
CDEK = {"id": "p2", "type": "service", "name": "CDEK: Самовывоз", "price": 79900, "quantity": 1}
COURIER = {"id": "p3", "type": "service", "name": "Курьер по Москве", "price": 50000, "quantity": 1}


def test_desired_manual_prepaid_zeroes_cdek():
    d = compute_desired([GOODS, CDEK], "manual_prepaid", Cfg)
    assert d["zero_position_ids"] == ["p2"]   # СДЭК обнуляется (на карту/перевод)


def test_desired_manual_prepaid_does_not_zero_non_cdek():
    d = compute_desired([GOODS, COURIER], "manual_prepaid", Cfg)
    assert d["zero_position_ids"] == []       # курьер не обнуляем


def test_desired_prepaid_no_zeroing():
    # InSales «онлайн»/«картой» → предоплата, доставка НЕ обнуляется
    assert compute_desired([GOODS, CDEK], "prepaid", Cfg)["zero_position_ids"] == []


def test_desired_cod_no_zeroing():
    assert compute_desired([GOODS, CDEK], "cod", Cfg)["zero_position_ids"] == []


def test_desired_unknown_payment_no_zeroing():
    assert compute_desired([GOODS, CDEK], None, Cfg)["zero_position_ids"] == []


def test_desired_zero_priced_cdek_not_touched():
    # Доставка уже бесплатная — второй раз не обнуляем (иначе лишняя запись в МС)
    free_cdek = {"id": "p2", "type": "service", "name": "CDEK: Самовывоз",
                 "price": 0, "quantity": 1}
    assert compute_desired([GOODS, free_cdek], "manual_prepaid", Cfg)["zero_position_ids"] == []


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


def _order(payment, channel=None):
    o = {"id": "o1", "name": "00001",
         "attributes": [{"id": "a-paymethod", "value": payment}]}
    if channel:
        o["salesChannel"] = {"meta": {"href": f"/entity/saleschannel/{channel}"}}
    return o


def test_resync_card_zeroes_cdek():
    # Менеджер сменил оплату на «На карту» → обнуляем цену СДЭК-доставки
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    res = rs.resync_order(_order("На карту"))
    assert res is not None
    assert [p[0] for p in ms.puts] == ["entity/customerorder/o1/positions/p2"]
    assert ms.puts[0][1] == {"price": 0}
    assert "Обнулить СДЭК-доставку" in res["plan"]


def test_resync_online_payment_no_writes():
    # Онлайн-оплата: доставка платная, трогать нечего
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    assert rs.resync_order(_order("Оплата онлайн")) is None
    assert ms.puts == []


def test_resync_cod_no_writes():
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    assert rs.resync_order(_order("При получении")) is None
    assert ms.puts == []


def test_resync_marketplace_skipped():
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    assert rs.resync_order(_order("На карту", channel="mp-1")) is None
    assert ms.puts == []


def test_resync_dry_run_writes_nothing():
    ms = FakeMS(_ms_positions())
    rs = FieldResync(Cfg, ms)
    res = rs.resync_order(_order("На карту"), dry_run=True)
    assert res is not None and ms.puts == []
