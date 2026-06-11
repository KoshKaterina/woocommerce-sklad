# Разбор адреса доставки на компоненты для МС shipmentAddressFull.
#
# Источник (WooCommerce, после плагина DaData):
#   - shipping.postcode / city / country (ISO) — уже отдельные поля;
#   - shipping.address_1 — полная стандартизованная DaData-строка
#     ("г Москва, ул Бутлерова, д 17, офис 5126") — её и парсим на улицу/дом/квартиру.
# DaData-стандартизация на checkout обязательна → целимся на чистый формат,
# но оставляем graceful fallback (нераспознанное кладём целиком в street).

import re
from dataclasses import dataclass

# ISO-код страны (shipping.country) → название для справочника стран МС.
# НЕ хардкодим одну страну: бывают зарубежные заказы (KZ/BY/…).
ISO_TO_COUNTRY_NAME = {
    "RU": "Россия",
    "KZ": "Казахстан",
    "BY": "Беларусь",
    "UA": "Украина",
    "AM": "Армения",
    "AZ": "Азербайджан",
    "GE": "Грузия",
    "KG": "Киргизия",
    "UZ": "Узбекистан",
    "TJ": "Таджикистан",
    "MD": "Молдова",
    "TM": "Туркмения",
}

# Маркеры дома (DaData): "д 17", "дом 100", "двлд 9", "вл 5", "уч 3".
_HOUSE_RE = re.compile(
    r"^(д|дом|двлд|домовлад\w*|вл|владение|уч|участок|зу)[\.\s]+\S",
    re.IGNORECASE,
)
# Маркеры квартиры/офиса: "кв 12", "офис 5126", "оф. 3", "помещение 1".
_FLAT_RE = re.compile(
    r"^(кв|квартира|офис|оф|помещ\w*|пом|комн\w*|апарт\w*)[\.\s]+\S",
    re.IGNORECASE,
)
# Голый номер дома (fallback / ПВЗ): "35", "16с10", "4А", "2 корп.4 стр.1".
_BARE_HOUSE_RE = re.compile(
    r"^\d+[а-яёa-z]?(\s*(к|корп\w*|стр\w*|с|literа|лит)\.?\s*\d*[а-яёa-z]?)*$",
    re.IGNORECASE,
)

# Регион / район / страна — отбрасываем из street (город берём из shipping.city).
_REGION_RE = re.compile(
    r"(^|\s)(обл|область|край|респ|республика|округ|ао)(\s|\.|$)", re.IGNORECASE
)
_AREA_RE = re.compile(
    r"(^|\s)(р-н|район|улус|администрац\w*|г\.о)(\s|\.|$)", re.IGNORECASE
)
_CITY_PREFIX_RE = re.compile(r"^(г|город|гор)[\.\s]", re.IGNORECASE)
_COUNTRY_NAMES = {n.lower() for n in ISO_TO_COUNTRY_NAME.values()}


@dataclass
class ShipmentAddressParts:
    """Компоненты адреса для нативного объекта МС shipmentAddressFull.

    Страна/регион в МС — справочники; здесь храним их имена (country_name),
    резолв в meta — на стороне order_processor/ms_client. region в MVP не пишем.
    """
    postal_code: str = ""
    country_name: str = ""   # "Россия" / "Казахстан" / …
    region: str = ""         # пока не пишем в МС (справочник), кладём в addInfo
    city: str = ""
    street: str = ""
    house: str = ""
    apartment: str = ""
    add_info: str = ""       # «Другое»: регион и нераспознанные хвосты

    def is_empty(self) -> bool:
        return not any((self.postal_code, self.country_name, self.city,
                        self.street, self.house, self.apartment))


def _strip_marker(token: str, regex: re.Pattern) -> str:
    """Убрать ведущий маркер ("д 17" → "17", "офис 5126" → "5126")."""
    m = regex.match(token)
    if not m:
        return token.strip()
    return token[m.end(1):].lstrip(". ").strip()


def _is_locality(token: str, city: str = "") -> bool:
    """Токен — это страна/регион/район/город (не улица)?"""
    if token.lower() in _COUNTRY_NAMES:
        return True
    if city and token.strip().lower() == city.strip().lower():
        return True
    if _CITY_PREFIX_RE.match(token):
        return True
    if _REGION_RE.search(token):
        return True
    if _AREA_RE.search(token):
        return True
    return False


def _split_street_house_flat(address: str, city: str = "") -> tuple[str, str, str]:
    """Разобрать строку на (street, house, apartment).

    Работает справа налево: квартира → дом → остаток (улица, без локалити).
    """
    tokens = [t.strip() for t in address.split(",") if t.strip()]
    apartment = ""
    house = ""

    if tokens and _FLAT_RE.match(tokens[-1]):
        apartment = _strip_marker(tokens.pop(), _FLAT_RE)

    if tokens and _HOUSE_RE.match(tokens[-1]):
        house = _strip_marker(tokens.pop(), _HOUSE_RE)
    elif tokens and _BARE_HOUSE_RE.match(tokens[-1]):
        # fallback: голый номер дома без маркера ("Фрунзе 5" → дом "5")
        last = tokens[-1]
        if last[0].isdigit() or " " not in last:
            # токен начинается с цифры ("6 лит. А") или без пробела ("16с10") —
            # это целиком дом
            house = tokens.pop()
        else:
            # дом — последнее слово, остальное — улица ("Фрунзе 5")
            street_in_token, _, num = last.rpartition(" ")
            tokens[-1] = street_in_token
            house = num

    street_tokens = [t for t in tokens if not _is_locality(t, city)]
    street = ", ".join(street_tokens)
    return street, house, apartment


def parse_wc_address(shipping: dict, delivery_type: str,
                     cdek_city: str = "") -> ShipmentAddressParts:
    """Собрать ShipmentAddressParts из shipping{} заказа WooCommerce.

    delivery_type: "courier" / "pvz" / "postamat" (из detect_delivery_type).
    Курьер: компоненты из shipping + парсинг address_1.
    ПВЗ/постамат: address_2 — CDEK-строка с кодом ПВЗ; best-effort.
    cdek_city — город из меты CDEK-плагина (fallback при пустом shipping.city).

    addInfo («Другое») не заполняем: shipping.state либо дублирует город
    (Москва/СПб), либо грязный («ЮРЛОВО, Г.О ХИМКИ») — менеджерам мешает.
    """
    iso = (shipping.get("country") or "").upper()
    country_name = ISO_TO_COUNTRY_NAME.get(iso, "")
    city = (shipping.get("city") or "").strip()
    postal = (shipping.get("postcode") or "").strip()
    region = (shipping.get("state") or "").strip()

    if delivery_type in ("pvz", "postamat"):
        city = city or cdek_city.strip()
        # address_2: "MSK2469, Москва, ул. Твардовского, 2 корп.4, стр.1"
        raw = (shipping.get("address_2") or "").strip()
        # убираем ведущий код ПВЗ ("MSK2469")
        raw = re.sub(r"^[A-Z]+\d+\s*,?\s*", "", raw)
        street, house, apartment = _split_street_house_flat(raw, city)
    else:
        # курьер: полная DaData-строка в address_1
        address_1 = (shipping.get("address_1") or "").strip()
        street, house, apartment = _split_street_house_flat(address_1, city)

    return ShipmentAddressParts(
        postal_code=postal,
        country_name=country_name,
        region=region,
        city=city,
        street=street,
        house=house,
        apartment=apartment,
    )
