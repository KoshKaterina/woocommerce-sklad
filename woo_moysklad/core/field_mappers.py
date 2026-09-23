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


def _first_shipping_line(order_data: dict) -> dict:
    lines = order_data.get("shipping_lines", [])
    return lines[0] if lines else {}


def is_office_pickup(order_data: dict) -> bool:
    """Наш самовывоз (local_pickup) — заказ без адреса доставки.

    ⚠️ 23.09.2026 способ доставки на сайте переименовали: «Самовывоз из офиса
    Sunscrypt» → «Самовывоз из шоурума Sunscrypt». Слова «офиса» в новом имени нет,
    поэтому проверяем оба слова. Заказ, не опознанный как наш самовывоз, получил бы
    адрес доставки и уехал мимо статуса «Шоурум» в МС.
    Старое имя оставляем: в живых заказах на 23.09 встречаются оба.
    """
    line = _first_shipping_line(order_data)
    if line.get("method_id") == "local_pickup":
        return True
    title = (line.get("method_title") or "").lower()
    return "офиса" in title or "шоурум" in title


def extract_cdek_meta(order_data: dict) -> dict:
    """Код ПВЗ и город из меты официального CDEK-плагина (shipping_lines[0].meta_data).

    Плагин official_cdek пишет `_official_cdek_office_code` / `_official_cdek_city`
    во ВСЕ свои заказы — это надёжнее, чем address_2, который чекаут иногда
    не заполняет (сбой фронта, напр. заказ 17130).
    """
    meta = {m.get("key"): m.get("value")
            for m in (_first_shipping_line(order_data).get("meta_data") or [])}
    return {
        "office_code": str(meta.get("_official_cdek_office_code") or "").strip(),
        "city": str(meta.get("_official_cdek_city") or "").strip(),
    }


def build_shipment_address(order_data: dict) -> str | None:
    """Сформировать строку адреса доставки (человекочитаемый fallback).

    pvz:     "<Страна>, " + shipping.address_2 + ", " + shipping.postcode
             (address_2 пуст — сбой чекаута → код ПВЗ + город из меты CDEK)
    courier: "<Страна>, " + shipping.city + ", " + shipping.address_1 + ", " + shipping.postcode
    самовывоз из офиса: None — адреса доставки нет.

    Страна берётся по ISO-коду shipping.country (не хардкод — бывают KZ/BY/…),
    с дефолтом «Россия», если код неизвестен/пуст.
    """
    from woo_moysklad.core.address_parser import ISO_TO_COUNTRY_NAME

    if is_office_pickup(order_data):
        return None

    shipping = order_data.get("shipping", {})
    method_title = _first_shipping_line(order_data).get("method_title", "")
    delivery_type = detect_delivery_type(method_title)

    postcode = shipping.get("postcode", "")
    city = shipping.get("city", "")
    iso = (shipping.get("country") or "").upper()
    country = ISO_TO_COUNTRY_NAME.get(iso, "Россия")

    if delivery_type == "pvz":
        address_2 = shipping.get("address_2", "")
        if address_2:
            parts = [country, address_2, postcode]
        else:
            cdek = extract_cdek_meta(order_data)
            parts = [country, cdek["office_code"], city or cdek["city"], postcode]
    else:
        address_1 = shipping.get("address_1", "")
        parts = [country, city, address_1, postcode]

    # Убираем пустые части
    parts = [p for p in parts if p]
    return ", ".join(parts)


def extract_pvz_code(order_data: dict) -> str | None:
    """Извлечь код ПВЗ (только при типе pvz).

    Первичный источник — мета CDEK-плагина `_official_cdek_office_code`
    (есть всегда, даже когда чекаут не заполнил address_2).
    Fallback — regex по shipping.address_2: одна или более заглавных
    латинских букв + одна или более цифр.
    "MSK2425, Москва, ул. Садовая-Кудринская, 20" → "MSK2425"
    "SBP892 какой-то текст" → "SBP892"
    """
    method_title = _first_shipping_line(order_data).get("method_title", "")

    if detect_delivery_type(method_title) != "pvz":
        return None

    code = extract_cdek_meta(order_data)["office_code"]
    if code:
        return code

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
    if is_other_payment(payment_method_title):
        # Способ согласует менеджер - типа приёма платежа нет, и это не ошибка
        return None

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
    return "на карт" in lower or "банковский перевод" in lower


def is_other_payment(payment_method_title: str) -> bool:
    """«Другой способ» (способ оплаты сайта, заведён 15.09.2026).

    Как клиент заплатит, менеджер согласует с ним сам: перевод, криптовалюта, счёт.
    Заказ не оплачен, пока менеджер не отметит оплату; доставку СДЭК не обнуляем.
    """
    return "другой способ" in (payment_method_title or "").lower()


def is_manual_payment_mark(payment_method_title: str) -> bool:
    """Оплату такого заказа отмечает менеджер, интеграция автоматом не отмечает.

    Одно правило на все входы: создание заказа (is_paid), вебхук «заказ изменён»
    и сверка (should_mark_paid). До 15.09.2026 условие жило тремя копиями, и новый
    способ оплаты попал бы в «оплачен» в любой из них.

    "При получении"                    → True (деньги возьмут при вручении)
    "На карту", "Банковский перевод"   → True (ручная предоплата)
    "Другой способ"                    → True (способ согласует менеджер)
    "Онлайн-оплата" и прочее           → False (оплачено на сайте)
    """
    title = payment_method_title or ""
    return ("при получении" in title.lower()
            or is_manual_prepayment(title)
            or is_other_payment(title))


# Ник Телеграма: 5-32 символа, латиница, цифры и «_», первый символ - буква
_TELEGRAM_USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
_TELEGRAM_LINK_PREFIX = re.compile(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/", re.IGNORECASE)
# Ключи меты заказа WC: поле оформления billing_telegram Woo сохраняет как «_billing_telegram»
_TELEGRAM_META_KEYS = ("_billing_telegram", "billing_telegram")


def normalize_telegram(raw) -> str | None:
    """Привести ник к виду «@username». Не ник (телефон, мусор, пусто) → None.

    «t.me/name», «https://t.me/name/», «name», « @name » → «@name».
    В таком же виде ник пишет в amo Wazzup (поле контакта TelegramUsername_WZ).
    """
    value = _TELEGRAM_LINK_PREFIX.sub("", str(raw or "").strip())
    value = value.strip().strip("/").lstrip("@").strip()
    return f"@{value}" if _TELEGRAM_USERNAME.fullmatch(value) else None


def extract_telegram(order_data: dict) -> str | None:
    """Ник Телеграма покупателя из meta_data заказа WC (необязательное поле оформления).

    Невалидное значение в МС не передаём: дальше оно уедет в карточку контакта amo,
    а по нику там склеиваются контакты, и чужой или кривой ник склеит разных людей.
    """
    for m in order_data.get("meta_data") or []:
        if m.get("key") in _TELEGRAM_META_KEYS:
            return normalize_telegram(m.get("value"))
    return None


def extract_courier_comment(order_data: dict) -> str | None:
    """Извлечь комментарий покупателя (customer_note). None если пустая строка."""
    note = order_data.get("customer_note", "").strip()
    return note if note else None


def extract_promo_code(order_data: dict) -> str | None:
    """Извлечь промокод. Реализовано, но пока НЕ используется в обработке."""
    codes = order_data.get("coupon_codes", [])
    return codes[0] if codes else None


def extract_ym_client_id(order_data: dict) -> str | None:
    """ClientID Яндекс.Метрики из meta_data заказа WC (ключ "ym_client_id").

    Сквозная аналитика: значение ловится на сайте и кладётся в заказ Woo.
    Нет ключа / пустое значение → None (поле в МС просто не заполняем).
    Возвращаем строкой — это 19-значное число, точность важна.
    """
    for m in order_data.get("meta_data") or []:
        if m.get("key") == "ym_client_id":
            value = str(m.get("value") or "").strip()
            return value or None
    return None


def map_delivery_sd(method_title: str) -> str | None:
    """Определить элемент справочника 'Доставка (СД)' по method_title.

    Возвращает ключ для конфига: "cdek", "dostavista", "showroom" или None.

    WC-интеграция ставит автоматически только три значения (2026-06):
    - любой СДЭК → "СДЭК" (обычный; ускоренный тариф менеджер ставит вручную)
    - курьерская доставка Достависта (курьерка по Москве) → "Достависта (стандартная)"
    - самовывоз из офиса Sunscrypt → "Самовывоз из шоурума Sunscrypt"
    Остальные значения справочника (экспрессы, Яндекс Доставка, Почта России) —
    только ручной выбор менеджера, из заказа не передаются.
    InSales-аналог — map_insales_delivery_sd (СДЭК + ExpressRMS(Самовывоз)).
    """
    lower = method_title.lower()
    if "cdek" in lower or "сдэк" in lower:
        return "cdek"
    if ("достависта" in lower or "курьерская по" in lower
            or "доставка курьером по москве" in lower.replace("  ", " ")):
        return "dostavista"
    if "самовывоз из офиса" in lower or "самовывоз офис" in lower:
        return "showroom"

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
