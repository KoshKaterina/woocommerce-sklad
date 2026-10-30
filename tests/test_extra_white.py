"""Акция Tangemshop «дополнительный белый за 4 990 ₽»: проверка права в заказе InSales."""

from unittest.mock import MagicMock

from woo_moysklad.core.client_note import build_site_client_note
from woo_moysklad.core.normalized_order import NormalizedCustomer, NormalizedOrder
from woo_moysklad.core.order_processor import OrderProcessor
from woo_moysklad.insales.extra_white import extra_white_warning
from woo_moysklad.insales.normalizer import normalize_insales_order

PROMO = 9000000001      # вариант скрытого акционного товара (в бою - id после создания)
PROMO_PRODUCT = 9000000000
MAIN = 318114475        # «Tangem 2.0»
BLACK, WHITE, FAMILY, RING = 1353419777, 546677567, 1756444521, 1756930473
ENDS = "2026-11-30T23:59:59+03:00"


def line(variant_id, quantity=1, product_id=MAIN, **extra):
    return {"variant_id": variant_id, "product_id": product_id, "quantity": quantity, **extra}


def promo_line(quantity=1, **extra):
    return line(PROMO, quantity, product_id=PROMO_PRODUCT, **extra)


def order(*lines, created_at="2026-11-10T12:00:00.000+03:00"):
    return {"order_lines": list(lines), "created_at": created_at}


def warn(data, ends_at=""):
    return extra_white_warning(data, promo_variant_id=PROMO, main_product_id=MAIN, ends_at=ends_at)


def test_off_until_promo_product_exists():
    data = order(promo_line(3))
    assert extra_white_warning(data, promo_variant_id=0, main_product_id=MAIN) is None


def test_order_without_promo_is_not_checked():
    assert warn(order(line(BLACK, 2), line(WHITE))) is None


def test_one_promo_per_main_unit_is_fine():
    assert warn(order(line(BLACK), promo_line())) is None
    # Право дают и обычный белый, и Family Pack, и кольцо: по одному на единицу.
    assert warn(order(line(WHITE), line(FAMILY), line(RING), promo_line(3))) is None
    assert warn(order(line(BLACK, 3), promo_line())) is None


def test_more_promo_than_main_needs_manager():
    text = warn(order(line(BLACK), promo_line(2)))
    assert text.startswith("Проверить акцию 4 990 ₽: акционных наборов 2, основных кошельков 1.")


def test_promo_alone_needs_manager():
    assert "акционных наборов 1, основных кошельков 0" in warn(order(promo_line()))


def test_promo_set_does_not_qualify_for_the_next_one():
    # Даже если акционный вариант заведут внутри основного товара, он не считается основным.
    data = order(line(BLACK), line(PROMO, 2, product_id=MAIN))
    assert "акционных наборов 2, основных кошельков 1" in warn(data)


def test_order_after_deadline_needs_manager():
    data = order(line(BLACK), promo_line(), created_at="2026-12-01T00:00:05.000+03:00")
    assert "акция закончилась 30.11.2026 23:59" in warn(data, ENDS)
    assert warn(order(line(BLACK), promo_line()), ENDS) is None


def test_both_problems_in_one_text():
    data = order(promo_line(), created_at="2026-12-02T10:00:00.000+03:00")
    text = warn(data, ENDS)
    assert "основных кошельков 0; заказ оформлен 02.12.2026 10:00" in text


def test_unparsable_deadline_does_not_break_the_check():
    assert warn(order(line(BLACK), promo_line()), "30 ноября") is None
    assert warn(order(promo_line()), "30 ноября") is not None


def make_config(**overrides):
    cfg = MagicMock()
    cfg.MS_ORGANIZATION_INSALES_ID = "org-insales-uuid"
    cfg.MS_STATE_INSALES_NEW_ID = "state-insales-uuid"
    cfg.FAMILY_PACK_WHITE_FIRST_ENABLED = False
    cfg.INSALES_ORDER_TYPE_ENABLED = False
    cfg.INSALES_EXTRA_WHITE_VARIANT_ID = PROMO
    cfg.INSALES_MAIN_WALLET_PRODUCT_ID = MAIN
    cfg.INSALES_EXTRA_WHITE_ENDS_AT = ENDS
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def insales_order(*lines):
    return {
        "id": 1, "number": 19001, "created_at": "2026-11-10T12:00:00.000+03:00",
        "client": {"name": "Ирина", "surname": "Фролова", "phone": "+79161234567"},
        "order_lines": list(lines), "discounts": [], "comment": "Позвоните за час",
        "payment_title": "Оплата онлайн", "delivery_title": "Доставка в Пункты выдачи (СДЭК)",
        "delivery_price": 0, "total_price": 0,
    }


def test_normalizer_keeps_promo_price_and_ms_sku():
    # Купон 10% лёг только на обычный кошелёк: у акционной строки discounts_amount 0,
    # потому что у товара акции «Применять скидки: нет».
    data = insales_order(
        line(BLACK, sku="TG128X3-B", sale_price=7590, discounts_amount=759),
        promo_line(sku="TG130X3-B", sale_price=4990, discounts_amount=0))
    result = normalize_insales_order(data, make_config())
    assert [(li.sku, li.price_cents, li.quantity) for li in result.line_items] == [
        ("TG128X3-B", 683100, 1), ("TG130X3-B", 499000, 1)]
    assert result.staff_warning is None
    assert result.estimated_cost == "11821"


def test_normalizer_flags_promo_without_main_wallet():
    data = insales_order(promo_line(sku="TG130X3-B", sale_price=4990))
    result = normalize_insales_order(data, make_config())
    assert result.staff_warning.startswith("Проверить акцию 4 990 ₽")
    assert result.description == "Позвоните за час"


def test_mock_config_does_not_switch_the_check_on():
    cfg = make_config()
    del cfg.INSALES_EXTRA_WHITE_VARIANT_ID  # MagicMock снова отдаёт заглушку
    result = normalize_insales_order(insales_order(promo_line(sku="TG130X3-B", sale_price=4990)), cfg)
    assert result.staff_warning is None


def ms_order(**kwargs):
    customer = NormalizedCustomer(full_name="Ирина Фролова", phone="+79161234567")
    return NormalizedOrder(source="insales", order_id="1", order_number="19001 Tangemshop",
                           customer=customer, **kwargs)


def test_warning_goes_first_in_ms_comment():
    processor = OrderProcessor(MagicMock(), MagicMock(), MagicMock(), MagicMock())
    body = processor._build_body_from_normalized(
        agent_meta={}, store_id="store", positions=[], attributes=[],
        order=ms_order(staff_warning="Проверить акцию 4 990 ₽: …", description="Позвоните"))
    assert body["description"] == "Проверить акцию 4 990 ₽: …\n\nПозвоните"
    body = processor._build_body_from_normalized(
        agent_meta={}, store_id="store", positions=[], attributes=[],
        order=ms_order(description="Позвоните"))
    assert body["description"] == "Позвоните"


def test_warning_reaches_amo_note_as_its_own_line():
    note = build_site_client_note(ms_order(staff_warning="Проверить акцию 4 990 ₽: …",
                                           description="Позвоните"))
    assert note.splitlines()[-2:] == ["Комментарий покупателя: Позвоните",
                                      "Проверить акцию 4 990 ₽: …"]
