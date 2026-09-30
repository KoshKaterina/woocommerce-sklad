"""Признак предзаказа заказа InSales: резерв, который платформа ставит при оформлении.

Плашка «Предзаказ» на витрине tangemshop.ru рисуется в сниппете `product_card.liquid`
как `{% unless variant.available %}Предзаказ{% endunless %}` — то есть по нулевому
остатку варианта. Тот же факт платформа фиксирует в самом заказе: `reserved_quantity`
строки — это резерв, который InSales ставит в момент оформления. Резерв меньше
количества значит, что товара не было.

⚠️ Резерв живёт не вечно: у отменённого заказа он обнуляется, у отгруженного
снимается (проверено на заказе 18625 — при создании 1, после отмены `None`).
Поэтому признак читается только со СВЕЖЕГО заказа. Догонка сверки после сбоя связи
берёт заказы возрастом до шести часов, и там резерв уже соврал бы.

В рознице ту же задачу решает замороженный снимок в мете строки WooCommerce
(`woocommerce/preorder.py`): там признак пишет сайт в момент оформления, и стареть
ему нечем. У InSales своей меты строки нет, поэтому опираемся на резерв платформы.
"""

from datetime import datetime, timezone

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def _parse_created_at(value) -> datetime | None:
    """`created_at` InSales вида "2026-09-30T18:18:23.000+03:00" → datetime с зоной."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def order_age_minutes(order_data: dict, *, now: datetime | None = None) -> float | None:
    """Возраст заказа в минутах. None — дату разобрать не удалось (возраст неизвестен)."""
    created = _parse_created_at(order_data.get("created_at"))
    if created is None:
        return None
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return None
    return (moment - created).total_seconds() / 60


def line_preorder(order_line: dict) -> bool | None:
    """Предзаказна ли строка. None — резерва в строке нет, судить не по чему.

    Резерв строго меньше количества считаем предзаказом целиком: частичный резерв
    («две штуки, зарезервирована одна») означает, что часть заказа всё равно ждут
    с поставки, а поле «Тип заявки» у заказа одно.
    """
    quantity = order_line.get("quantity")
    reserved = order_line.get("reserved_quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return None
    if not isinstance(reserved, int) or isinstance(reserved, bool) or reserved < 0:
        return None
    return reserved < quantity


def read_order_preorders(order_data: dict, *, max_age_minutes: int,
                         now: datetime | None = None) -> dict | None:
    """Карта «id строки заказа → предзаказна ли». None — судить нельзя.

    None возвращается, когда заказ старше `max_age_minutes` или его дату не разобрать:
    резерв к этому моменту мог быть снят, и любое значение было бы выдумкой.
    """
    age = order_age_minutes(order_data, now=now)
    if age is None:
        log.warning("InSales: возраст заказа неизвестен, признак предзаказа не читаем",
                    order_id=order_data.get("id"), created_at=order_data.get("created_at"))
        return None
    if age > max_age_minutes:
        log.warning("InSales: заказ несвежий, резерв уже мог быть снят — "
                    "признак предзаказа не читаем",
                    order_id=order_data.get("id"), возраст_минут=round(age, 1),
                    предел=max_age_minutes)
        return None

    result: dict = {}
    for line in order_data.get("order_lines") or []:
        if not isinstance(line, dict):
            continue
        value = line_preorder(line)
        if value is None:
            log.warning("InSales: в строке заказа нет резерва, считаем позицию обычной",
                        order_id=order_data.get("id"), sku=line.get("sku"),
                        reserved_quantity=line.get("reserved_quantity"),
                        quantity=line.get("quantity"))
            value = False
        result[line.get("id")] = value
    return result
