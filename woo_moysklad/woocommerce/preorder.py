"""Снимок предзаказа в метаданных строки Woo, сохранённый при оформлении.

Не выводим предзаказ из нынешнего остатка или Woo ``Backordered``: оба могут
расходиться со статусом позиции в момент создания заказа.
"""

PREORDER_META_KEY = "_sc_preorder_snapshot"


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value) -> bool:
    return type(value) is int and value >= 0


def read_preorder_snapshot(item: dict) -> dict | None:
    """Вернуть проверенный снимок либо None при отсутствии (legacy).

    Любая неоднозначная мета — ошибка данных, а не обычный товар.
    """
    metadata = item.get("meta_data", [])
    if not isinstance(metadata, list):
        raise ValueError("Woo line meta_data должен быть списком")
    snapshots = [meta.get("value") for meta in metadata
                 if isinstance(meta, dict) and meta.get("key") == PREORDER_META_KEY]
    if not snapshots:
        return None
    if len(snapshots) != 1:
        raise ValueError(f"Woo line содержит несколько {PREORDER_META_KEY}")

    value = snapshots[0]
    if not isinstance(value, dict) or set(value) != {
        "v", "preorder", "product_id", "variation_id", "sku", "quantity"
    }:
        raise ValueError(f"Woo line {PREORDER_META_KEY}: неверная структура v1")
    if type(value["v"]) is not int or value["v"] != 1:
        raise ValueError(f"Woo line {PREORDER_META_KEY}: неизвестная версия")
    if type(value["preorder"]) is not bool:
        raise ValueError(f"Woo line {PREORDER_META_KEY}: preorder должен быть bool")
    if not _positive_int(value["product_id"]) or not _nonnegative_int(value["variation_id"]):
        raise ValueError(f"Woo line {PREORDER_META_KEY}: неверный product/variation ID")
    if not _positive_int(value["quantity"]):
        raise ValueError(f"Woo line {PREORDER_META_KEY}: неверное количество")
    if not isinstance(value["sku"], str):
        raise ValueError(f"Woo line {PREORDER_META_KEY}: sku должен быть строкой")

    # REST WC line_items — целочисленные id/quantity. Не принимаем совпадение
    # 1 == True и не привязываем снимок к другой вариации или строке заказа.
    if (type(item.get("product_id")) is not int or
            type(item.get("variation_id")) is not int or
            type(item.get("quantity")) is not int or
            value["product_id"] != item["product_id"] or
            value["variation_id"] != item["variation_id"] or
            value["quantity"] != item["quantity"]):
        raise ValueError(f"Woo line {PREORDER_META_KEY}: не совпадает с позицией заказа")
    # REST ``sku`` может отражать нынешнюю карточку. Источник артикула для
    # нового заказа — только замороженный value["sku"].
    return value


def read_order_preorders(items: list[dict]) -> list[dict | None]:
    """Отсутствуют все снимки — legacy/unknown; частичное покрытие — ошибка."""
    snapshots = [read_preorder_snapshot(item) for item in items]
    if any(value is None for value in snapshots) and any(value is not None for value in snapshots):
        raise ValueError("Woo order: снимок предзаказа есть не у всех позиций")
    return snapshots
