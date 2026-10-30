# Акция Tangemshop «дополнительный белый Tangem 2.0 (3 карты) за 4 990 ₽» (ТЗ 30.10.2026)

"""Серверная проверка права на акционный набор в заказе InSales.

Акционный набор - отдельный скрытый товар InSales: цена 4 990, старая 7 590, «Применять
скидки: нет» (купоны его не трогают), артикул TG130X3-B - в МС это тот же белый набор из
трёх карт, отдельного маппинга не нужно. Право на набор даёт любой вариант основного
товара «Tangem 2.0» (наборы, Family Pack, кольцо): один акционный набор на одну единицу
основного. Сам акционный набор права на следующий не даёт.

Тема продаёт набор только вместе с основным кошельком и сама убирает лишние из корзины,
но корзину InSales можно собрать и в обход темы, а штатного способа запретить такой заказ
у платформы нет. Поэтому здесь последняя линия: заказ, где акционных наборов больше, чем
основных кошельков, или оформленный после окончания акции, получает предупреждение для
менеджера. Цену и состав не меняем: покупатель мог уже оплатить онлайн, решение за
менеджером.
"""

from datetime import datetime

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def _parse_moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None
    # Без пояса сравнивать нельзя: наивное время с осведомлённым даёт TypeError.
    return moment if moment.tzinfo is not None else None


def extra_white_warning(order_data: dict, *, promo_variant_id: int,
                        main_product_id: int, ends_at: str = "") -> str | None:
    """Текст предупреждения для менеджера или None, если с акцией всё в порядке.

    promo_variant_id=0 - товар акции ещё не заведён, проверка выключена.
    ends_at - окончание акции в ISO с поясом; пусто - срок не проверяем.
    """
    if not promo_variant_id:
        return None
    lines = order_data.get("order_lines") or []
    promo = sum(int(ol.get("quantity") or 0) for ol in lines
                if ol.get("variant_id") == promo_variant_id)
    if not promo:
        return None
    main = sum(int(ol.get("quantity") or 0) for ol in lines
               if ol.get("product_id") == main_product_id
               and ol.get("variant_id") != promo_variant_id)

    problems = []
    if promo > main:
        problems.append(f"акционных наборов {promo}, основных кошельков {main}")
    if ends_at:
        end = _parse_moment(ends_at)
        created = _parse_moment(order_data.get("created_at") or "")
        if end is None:
            log.warning("Акция 4 990: срок окончания не разобран, срок не проверяем",
                        ends_at=ends_at)
        elif created is not None and created > end:
            problems.append(f"заказ оформлен {created:%d.%m.%Y %H:%M}, акция закончилась "
                            f"{end:%d.%m.%Y %H:%M}")
    if not problems:
        return None
    return ("Проверить акцию 4 990 ₽: " + "; ".join(problems)
            + ". Цена 4 990 ₽ положена только к основному кошельку и только до конца акции.")
