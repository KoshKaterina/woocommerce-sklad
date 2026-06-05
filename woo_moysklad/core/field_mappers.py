# Маппинг полей WooCommerce → Мой Склад: все трансформации данных

import re

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def detect_delivery_type(method_title: str) -> str:
    """Определить тип доставки по method_title.

    "pvz" — если содержит "Самовывоз" или "постамат" (case-insensitive)
    "courier" — всё остальное
    """
    lower = method_title.lower()
    if "самовывоз" in lower or "постамат" in lower:
        return "pvz"
    return "courier"


def build_shipment_address(order_data: dict) -> str:
    """Сформировать строку адреса доставки.

    pvz:     "Россия, " + shipping.address_2 + ", " + shipping.postcode
    courier: "Россия, " + shipping.city + ", " + shipping.address_1 + ", " + shipping.postcode
    """
    shipping = order_data.get("shipping", {})
    shipping_lines = order_data.get("shipping_lines", [])

    method_title = shipping_lines[0].get("method_title", "") if shipping_lines else ""
    delivery_type = detect_delivery_type(method_title)

    postcode = shipping.get("postcode", "")

    if delivery_type == "pvz":
        address_2 = shipping.get("address_2", "")
        parts = ["Россия", address_2, postcode]
    else:
        city = shipping.get("city", "")
        address_1 = shipping.get("address_1", "")
        parts = ["Россия", city, address_1, postcode]

    # Убираем пустые части
    parts = [p for p in parts if p]
    return ", ".join(parts)


def extract_pvz_code(order_data: dict) -> str | None:
    """Извлечь код ПВЗ из shipping.address_2 (только при типе pvz).

    Код ПВЗ — одна или более заглавных латинских букв + одна или более цифр.
    "MSK2425, Москва, ул. Садовая-Кудринская, 20" → "MSK2425"
    "SBP892 какой-то текст" → "SBP892"
    """
    shipping_lines = order_data.get("shipping_lines", [])
    method_title = shipping_lines[0].get("method_title", "") if shipping_lines else ""

    if detect_delivery_type(method_title) != "pvz":
        return None

    address_2 = order_data.get("shipping", {}).get("address_2", "")
    if not address_2:
        return None

    match = re.search(r'\b([A-Z]+\d+)\b', address_2)
    return match.group(1) if match else None


def map_payment_type(payment_method_title: str) -> str | None:
    """Определить тип приёма платежа для справочника МС.

    "на карту" / "онлайн" / "банковский перевод" → "prepaid" (1. Заказ предоплачен)
    "при получении"                              → "noncash" (2. Безналичная оплата)
    иначе                                        → None (WARNING)
    """
    lower = payment_method_title.lower()
    if "на карту" in lower or "онлайн" in lower or "банковский перевод" in lower:
        return "prepaid"
    if "при получении" in lower:
        return "noncash"

    log.warning("Неизвестный способ оплаты, тип приёма платежа не определён",
                payment_method_title=payment_method_title)
    return None


def is_manual_prepayment(payment_method_title: str) -> bool:
    """Предоплата, которую менеджер подтверждает вручную (деньги приходят вне интеграции).

    "На карту", "Банковский перевод" → True
    "Онлайн оплата" (онлайн-касса, оплачивается сразу), "При получении" и прочее → False

    Влияет одинаково на все источники-следствия: бесплатная доставка СДЭК,
    is_paid=False (не помечаем оплаченным автоматически), пропуск заказа в
    reconciliation и webhook mark_paid (ждём ручной отметки менеджера).
    """
    lower = payment_method_title.lower()
    return "на карту" in lower or "банковский перевод" in lower


def extract_courier_comment(order_data: dict) -> str | None:
    """Извлечь комментарий покупателя (customer_note). None если пустая строка."""
    note = order_data.get("customer_note", "").strip()
    return note if note else None


def extract_promo_code(order_data: dict) -> str | None:
    """Извлечь промокод. Реализовано, но пока НЕ используется в обработке."""
    codes = order_data.get("coupon_codes", [])
    return codes[0] if codes else None


def map_delivery_sd(method_title: str) -> str | None:
    """Определить элемент справочника 'Доставка (СД)' по method_title.

    Возвращает ключ для конфига: "cdek", "yandex" или None.
    Для самовывоза из офиса атрибут не заполняется — достаточно услуги в заказе.
    """
    lower = method_title.lower()
    if "cdek" in lower or "сдэк" in lower:
        return "cdek"
    if "курьерская по" in lower or "доставка курьером по москве" in lower.replace("  ", " "):
        return "yandex"
    if "самовывоз из офиса" in lower or "самовывоз офис" in lower:
        return None  # атрибут "Доставка (СД)" для самовывоза из офиса не ставим

    log.warning("Неизвестная служба доставки, поле 'Доставка (СД)' не заполнено",
                method_title=method_title)
    return None


def map_delivery_type(method_title: str) -> str | None:
    """Определить элемент справочника 'Вид доставки' по method_title.

    Возвращает ключ для конфига: "pvz", "postamat", "courier" или None.
    """
    lower = method_title.lower()
    if "постамат" in lower:
        return "postamat"
    if "самовывоз" in lower or "пвз" in lower:
        return "pvz"
    # Курьерская — всё что не pvz/постамат и не самовывоз
    delivery_type = detect_delivery_type(method_title)
    if delivery_type == "courier":
        return "courier"

    log.warning("Неизвестный вид доставки", method_title=method_title)
    return None


def build_attribute(attr_uuid: str, value, is_custom_entity: bool = False,
                    dictionary_id: str | None = None, element_id: str | None = None,
                    ms_base_url: str = "https://api.moysklad.ru/api/remap/1.2") -> dict | None:
    """Сформировать структуру доп. поля (attribute) для МС.

    Для обычных полей (string/text): {"meta": ..., "value": str(value)}
    Для справочников (customentity): {"meta": ..., "value": {"meta": ...}}
    None если value или attr_uuid пустые.
    """
    if not attr_uuid or value is None:
        return None

    attr = {
        "meta": {
            "href": f"{ms_base_url}/entity/customerorder/metadata/attributes/{attr_uuid}",
            "type": "attributemetadata",
            "mediaType": "application/json",
        }
    }

    if is_custom_entity and dictionary_id and element_id:
        # Справочник: value — meta-ссылка на элемент
        attr["value"] = {
            "meta": {
                "href": f"{ms_base_url}/entity/customentity/{dictionary_id}/{element_id}",
                "type": "customentity",
                "mediaType": "application/json",
            }
        }
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # Числовые поля МС (long / double) — передаём числом, не строкой
        attr["value"] = value
    else:
        # Строковое значение (string / text)
        attr["value"] = str(value)

    return attr
