# Нормализатор InSales: order_data → NormalizedOrder

import re

from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import (
    NormalizedCustomer,
    NormalizedDeliveryService,
    NormalizedLineItem,
    NormalizedOrder,
)
from woo_moysklad.insales.preorder import read_order_preorders

log = get_logger(__name__)

# Постоянный суффикс номера заказа InSales: предохраняет от коллизии с WC
# (оба источника используют числовые id) и группирует Tangemshop-заказы в МС.
_INSALES_ORDER_SUFFIX = " Tangemshop"

# InSales не кладёт сам код купона в заказ — только в description скидки
# в формате "Скидка по купону <CODE>" (+ discount_code_id для дозапроса).
# Парсим description, чтобы не делать доп. запрос к /discount_codes/{id}.
_INSALES_COUPON_DESC_RE = re.compile(r"по\s+купону\s+(\S+)", re.IGNORECASE)

# Маппинг InSales → МС только для случаев, которые нельзя резолвить автопоиском:
#   - Family Pack (одна позиция InSales разбивается на 2 товара МС)
#   - SKU-костыли (InSales SKU отличается от МС, пока не поправили в InSales)
# Для всех остальных SKU: InSales SKU == МС SKU → passthrough, далее ProductMatcher
# ищет товар в МС через API (find_by_filter по article/externalCode).

_INSALES_SKU_TO_MS_SKUS: dict[str, list[str]] = {
    # Family Pack: всегда 1 чёрный 3-карточный + 1 второй по цвету
    "TG-FPB":           ["TG128X3-B", "TG128X3-B"],
    "TG-FPW":           ["TG128X3-B", "TG130X3-B"],  # чёрный + белый
    "TG-FPSEA":         ["TG128X3-B", "TG-SEA"],
    "TG-FPSTEALTH":     ["TG128X3-B", "TG-STEALTH"],
    "TG-FPSKY":         ["TG128X3-B", "TG-SKY"],
    "TG-FPHYPERBLUE":   ["TG128X3-B", "TG-HYPERBLUE"],
    # SKU-костыль: в InSales лишний дефис, в МС — без него
    "TG-130X3-B":       ["TG130X3-B"],
}

# Для позиций InSales с пустым SKU — резолв по variant_id.
# По мере проставления SKU в InSales записи отсюда можно будет удалять.
_INSALES_VARIANT_ID_TO_MS_SKUS: dict[int, list[str]] = {
    2053393393: ["TG128X2-B"],                     # 2 карты / Классический чёрный
    2053393409: ["TG128X3-B", "TG128X3-B"],        # Family Pack / Классический чёрный
    2053395121: ["TG130X2-B"],                     # 2 карты / Классический белый
    2053395129: ["TG130X3-B"],                     # 3 карты / Классический белый
    2053395137: ["TG128X3-B", "TG130X3-B"],        # Family Pack / Классический белый
    2053396585: ["TG-STEALTH"],                    # 3 карты / Stealth
    2053396601: ["TG128X3-B", "TG-STEALTH"],       # Family Pack / Stealth
    2053401321: ["TG128X3-B", "TG-SKY"],           # Family Pack / Blush Sky
    2053411473: ["TG-SEA"],                        # 3 карты / Electra Sea
    2053411481: ["TG128X3-B", "TG-SEA"],           # Family Pack / Electra Sea
    2053412809: ["TG-HYPERBLUE"],                  # 3 карты / Hyperblue
    2053412817: ["TG128X3-B", "TG-HYPERBLUE"],     # Family Pack / Hyperblue
    # 2-карточные цветные (Stealth/Sky/Sea/Hyperblue) не продаются — не маппим.
}

# --- Промо Tangemshop 01.09.2026: «первый кошелёк Family Pack всегда белый» ---
# Включается флагом FAMILY_PACK_WHITE_FIRST_ENABLED (config.py), по умолчанию
# выключен — Family Pack раскладывается как раньше (1 чёрный + 1 по цвету).
#
# Артикулы Family Pack те же самые (TG-FPB, TG-FPSTEALTH, ...) — меняется только
# то, во что они раскладываются в МС. Альтернативные словари ВЫВОДЯТСЯ из основных
# автоматически (см. _white_first ниже), а не дублируются руками: у записи Family
# Pack (2 позиции, первая — чёрный 3-карточный TG128X3-B) первая позиция
# заменяется на белый TG130X3-B, вторая остаётся как была. Обычные товары и
# SKU-костыли (1 позиция) не трогаются. Так два словаря не могут разойтись
# по ошибке при правке одного из них.
_FAMILY_PACK_FIRST_BLACK_SKU = "TG128X3-B"
_FAMILY_PACK_FIRST_WHITE_SKU = "TG130X3-B"


def _white_first(mapping: dict) -> dict:
    """Вывести промо-вариант маппинга: у Family Pack (первая позиция — чёрный
    3-карточный) первая позиция заменяется на белый. Остальные записи — без изменений.
    """
    result = {}
    for key, ms_skus in mapping.items():
        if len(ms_skus) == 2 and ms_skus[0] == _FAMILY_PACK_FIRST_BLACK_SKU:
            result[key] = [_FAMILY_PACK_FIRST_WHITE_SKU, ms_skus[1]]
        else:
            result[key] = ms_skus
    return result


_INSALES_SKU_TO_MS_SKUS_WHITE_FIRST = _white_first(_INSALES_SKU_TO_MS_SKUS)
_INSALES_VARIANT_ID_TO_MS_SKUS_WHITE_FIRST = _white_first(_INSALES_VARIANT_ID_TO_MS_SKUS)


def resolve_to_ms_skus(raw_sku: str, variant_id: int | None, white_first: bool = False) -> list[str]:
    """Вернуть список SKU МС для одной позиции InSales.

    Порядок:
    1. Если SKU заполнен и есть в словаре (Family Pack / костыли) → маппинг.
    2. Если SKU заполнен, но не в словаре → passthrough (InSales SKU = МС SKU).
       ProductMatcher найдёт товар в МС через API по этому SKU.
       Если не найдёт — залогирует ошибку и позиция будет пропущена.
    3. Если SKU пустой, но есть variant_id в словаре по variant_id → маппинг.
    4. Если SKU пустой и variant_id неизвестен → [] (позиция пропускается).

    Возвращает 1 элемент для обычного товара, 2+ для Family Pack (цена делится).

    white_first: промо Tangemshop 01.09.2026 — первая позиция Family Pack белая
    вместо чёрной (см. FAMILY_PACK_WHITE_FIRST_ENABLED в конфиге). По умолчанию
    выключено — поведение не меняется.
    """
    sku_map = _INSALES_SKU_TO_MS_SKUS_WHITE_FIRST if white_first else _INSALES_SKU_TO_MS_SKUS
    variant_map = _INSALES_VARIANT_ID_TO_MS_SKUS_WHITE_FIRST if white_first else _INSALES_VARIANT_ID_TO_MS_SKUS

    sku = (raw_sku or "").strip()
    if sku:
        if sku in sku_map:
            return sku_map[sku]
        return [sku]  # passthrough → автопоиск в МС через ProductMatcher
    if variant_id is not None and variant_id in variant_map:
        return variant_map[variant_id]
    return []


def _split_price_evenly(total_cents: int, parts: int) -> list[int]:
    """Разделить сумму в копейках на parts без потерь: [total//parts, ..., остаток]."""
    if parts <= 0:
        return []
    base = total_cents // parts
    result = [base] * parts
    result[-1] = total_cents - base * (parts - 1)
    return result


def _split_family_pack_price(
    total_cents: int,
    ms_skus: list[str],
    white_first: bool,
    white_base_price_kopecks: int,
    white_discount_percent: int,
) -> list[int]:
    """Разделить цену Family Pack между позициями МС.

    Без промо (white_first=False) или не Family Pack (не ровно 2 позиции, или
    первая — не белый) — поровну, как раньше (_split_price_evenly).

    С промо для Family Pack (первая позиция после _white_first() — белый
    TG130X3-B) — по реальной экономике акции: первая позиция получает
    ФИКСИРОВАННУЮ цену со скидкой (white_base_price_kopecks с вычетом
    white_discount_percent), вторая — остаток (total_cents − белый). Так сумма
    позиций всегда сходится с ценой заказа (сколько бы ни стоил второй
    кошелёк на самом деле — его цена явно нигде не хардкожена), а белый
    кошелёк в МойСкладе записывается ровно по цене акции, а не «поровну».

    Если цена акции меньше расчётной скидки на белый (заказ дешевле, чем
    должен быть в принципе) — откатываемся на равное деление и логируем:
    отрицательная цена второй позиции в МС недопустима.
    """
    if not white_first or len(ms_skus) != 2 or ms_skus[0] != _FAMILY_PACK_FIRST_WHITE_SKU:
        return _split_price_evenly(total_cents, len(ms_skus))

    white_cents = round(white_base_price_kopecks * (100 - white_discount_percent) / 100)
    second_cents = total_cents - white_cents

    if second_cents < 0:
        log.warning("Family Pack white_first: цена акции меньше расчётной скидки на белый "
                    "— делим поровну вместо отрицательной цены второй позиции",
                    total_cents=total_cents, white_cents=white_cents)
        return _split_price_evenly(total_cents, len(ms_skus))

    return [white_cents, second_cents]


def _safe(name: str, func):
    try:
        return func()
    except Exception as e:
        log.warning("InSales normalizer: ошибка маппинга", field=name, error=str(e))
        return None


def _join_name_parts(src: dict) -> str:
    """Собрать 'Имя [Отчество] Фамилия' из полей name/middlename/surname."""
    parts = []
    for key in ("name", "middlename", "surname"):
        v = (src.get(key) or "").strip()
        if v:
            parts.append(v)
    return " ".join(parts)


def _has_letters(s: str) -> bool:
    return any(ch.isalpha() for ch in s)


def build_customer_name(client: dict, shipping_address: dict | None = None) -> str:
    """Собрать имя контрагента 'Имя [Отчество] Фамилия'.

    У client.name при быстром/гостевом оформлении InSales иногда лежит телефон,
    а не имя. Если в client нет «буквенного» имени — берём получателя из
    shipping_address. Если и там нет — 'Без имени' (телефон в имя не пишем,
    он и так попадает в поле телефона контрагента).
    """
    name = _join_name_parts(client)
    if _has_letters(name):
        return name

    if shipping_address:
        sa_name = _join_name_parts(shipping_address)
        if _has_letters(sa_name):
            return sa_name

    return "Без имени"


def build_delivery_service_name(delivery_info: dict, delivery_title: str) -> str | None:
    """Построить имя услуги доставки для МС в формате WC.

    Формат WC (анализ 100 услуг в МС):
      Один день, без запятой: "CDEK: Самовывоз (5 дней)"
      Диапазон, с запятой:   "CDEK: Самовывоз, (5-6 дней)"

    Особые случаи:
      "Самовывоз из Шоурума"   → "Самовывоз из шоурума Sunscrypt" (как в ОП розницы)
      "Доставка не требуется"  → None

    ⚠️ 06.10.2026: было "Самовывоз со склада" — имя из времён фулфилмента ЭРМС,
    откуда InSales-самовывоз выдавали до 21.07.2026. ЭРМС свёрнут, выдача идёт
    из того же шоурума, что и в рознице, а имя осталось и ломало сразу четыре
    места в amo_fix_fields: перенос в Офис и скип распределения не узнавали
    самовывоз (маркеры «самовывоз из офиса»/«самовывоз из шоурума»), в воронке
    TangemShop пришлось держать вторую пару правил «Тип доставки содержит склад»,
    а ШОУРУМ-бот писал клиенту «Самовывоз из шоурума» при другом значении в
    заказе. Теперь имя одно на оба магазина. Разбор —
    knowledge/samovyvoz-insales-nazyvaetsya-so-sklada.md в папке.
    """
    lower_title = delivery_title.lower()

    if "самовывоз из шоурума" in lower_title:
        return "Самовывоз из шоурума Sunscrypt"

    if "не требуется" in lower_title:
        return None

    if not delivery_info:
        return delivery_title  # fallback: как есть

    tariff_id = delivery_info.get("tariff_id") or ""
    interval = delivery_info.get("delivery_interval") or {}
    min_days = interval.get("min_days")
    max_days = interval.get("max_days")

    # Определяем тип СДЭК
    tariff_map = {
        "cdek_parcel_delivery_point": "Самовывоз",
        "cdek_parcel_courier": "Посылка склад-дверь",
    }

    delivery_type = None
    for key, value in tariff_map.items():
        if key in tariff_id:
            delivery_type = value
            break

    if not delivery_type:
        log.warning("Неизвестный tariff_id InSales", tariff_id=tariff_id)
        return delivery_title  # fallback

    # Строим название
    if min_days is not None and max_days is not None:
        if min_days == max_days:
            return f"CDEK: {delivery_type} ({min_days} дней)"
        else:
            return f"CDEK: {delivery_type}, ({min_days}-{max_days} дней)"

    return f"CDEK: {delivery_type}"


def normalize_insales_payment_title(payment_title: str) -> str:
    """Привести название способа оплаты InSales к формулировке WC.

    «Оплата онлайн», «Ozon Pay» (в т.ч. «(неактивен)»), «Оплата с OZON Pay» —
    всё это онлайн-оплата: в доп.поле «Способ оплаты» МС пишем «Онлайн-оплата»,
    как пишет WC. Остальные названия не трогаем.
    """
    lower = payment_title.lower()
    if "ozon pay" in lower or "онлайн" in lower:
        return "Онлайн-оплата"
    return payment_title


def map_insales_payment_type(payment_title: str) -> str | None:
    """Определить тип приёма платежа."""
    lower = payment_title.lower()
    if "онлайн" in lower or "картой" in lower:
        return "prepaid"
    if "при получении" in lower:
        return "noncash"
    log.warning("InSales: неизвестный способ оплаты", payment_title=payment_title)
    return None


def map_insales_delivery_sd(delivery_info: dict, delivery_title: str = "") -> str | None:
    """Определить службу доставки для атрибута "Доставка (СД)" МС.

    СДЭК (любой тариф) → 'cdek' по shipping_company.
    "Самовывоз из Шоурума" → 'rms_pickup' («ExpressRMS(Самовывоз)»).
    Прочее ("Доставка не требуется", неизвестное) → None — менеджер ставит вручную.

    ⚠️ Ключ 'rms_pickup' НЕ менять на 'showroom', хотя имя услуги с 06.10.2026 общее
    с розницей. По 'showroom' `_build_body_from_normalized` подменяет статус заказа МС
    на MS_STATE_SHOWROOM_PICKUP_ID, а заказы InSales рождаются в своём статусе
    «TangemShop Новый заказ» (MS_STATE_INSALES_NEW_ID) — подмена сломала бы интейк
    воронки TangemShop. Само доп.поле «Доставка (СД)» удалено 06.08.2026, ключ живёт
    только ради этой развилки по статусу.
    """
    if "самовывоз из шоурума" in delivery_title.lower():
        return "rms_pickup"
    if not delivery_info:
        return None
    company = (delivery_info.get("shipping_company") or "").lower()
    if "сдэк" in company or "cdek" in company:
        return "cdek"
    return None


def map_insales_delivery_type(delivery_info: dict, delivery_title: str) -> str | None:
    """Определить вид доставки."""
    lower_title = delivery_title.lower()

    if "самовывоз из шоурума" in lower_title:
        return "pvz"  # самовывоз = ПВЗ в МС

    if "не требуется" in lower_title:
        return None

    if delivery_info:
        outlet = delivery_info.get("outlet") or {}
        if outlet.get("type") == "pvz":
            return "pvz"

        tariff = delivery_info.get("tariff_id") or ""
        if "courier" in tariff:
            return "courier"

    return None


def extract_insales_pvz_code(delivery_info: dict) -> str | None:
    """Извлечь код ПВЗ: 'cdek#SVK27' → 'SVK27'."""
    if not delivery_info:
        return None
    outlet = delivery_info.get("outlet") or {}
    external_id = outlet.get("external_id") or ""
    if "#" in external_id:
        return external_id.split("#", 1)[1]
    return external_id or None


def _parse_outlet_address(address: str, city: str = "",
                          fallback_country: str = "") -> "ShipmentAddressParts":
    """Разобрать структурированную строку адреса ПВЗ СДЭК.

    "628403, Россия, Ханты-Мансийский автономный округ - Югра, Сургут, ул. Юности, 8"
    → индекс / страна / (регион пропускаем) / город / улица / дом.
    Первый токен бывает с текстовым префиксом («ПВЗ СДЭК 166000») — индекс
    вытаскиваем, префикс отбрасываем (код ПВЗ и так в отдельном поле).
    """
    from woo_moysklad.core.address_parser import (
        ISO_TO_COUNTRY_NAME,
        ShipmentAddressParts,
        _split_street_house_flat,
    )

    tokens = [t.strip() for t in address.split(",") if t.strip()]

    postal = ""
    if tokens:
        m = re.fullmatch(r"\D*?(\d{5,6})", tokens[0])
        if m:
            postal = m.group(1)
            tokens.pop(0)

    country = fallback_country
    country_names = {n.lower() for n in ISO_TO_COUNTRY_NAME.values()}
    if tokens and tokens[0].lower() in country_names:
        country = tokens.pop(0)

    # город: если не передан из КЛАДР — берём токен перед улицей не выйдет надёжно,
    # полагаемся на kladr_json.city (есть во всех заказах с локацией)
    street, house, apartment = _split_street_house_flat(", ".join(tokens), city)
    return ShipmentAddressParts(
        postal_code=postal, country_name=country, city=city,
        street=street, house=house, apartment=apartment,
    )


def build_insales_address_parts(delivery_info: dict, shipping_address: dict,
                                delivery_title: str):
    """ShipmentAddressParts для нативного shipmentAddressFull МС.

    ПВЗ:    outlet.address — структурированная строка СДЭК, парсим уверенно.
    Курьер: индекс/страна/город — из КЛАДР-данных InSales (валидированы чекаутом),
            улица/дом/квартира — парсинг DaData-строки shipping_address.address
            (тот же парсер, что и для WC).
    Самовывоз из Шоурума / «не требуется»: None — адрес не пишем.
    """
    from woo_moysklad.core.address_parser import (
        ISO_TO_COUNTRY_NAME,
        ShipmentAddressParts,
        _split_street_house_flat,
    )

    lower_title = delivery_title.lower()
    if "самовывоз" in lower_title or "не требуется" in lower_title:
        return None

    shipping_address = shipping_address or {}
    kladr = shipping_address.get("kladr_json") or {}
    location = shipping_address.get("location") or {}
    city = (shipping_address.get("city") or kladr.get("city") or "").strip()
    iso = (kladr.get("country") or location.get("country") or "").upper()
    country_name = ISO_TO_COUNTRY_NAME.get(iso, "")

    outlet = (delivery_info or {}).get("outlet") or {}
    if outlet.get("address"):
        return _parse_outlet_address(outlet["address"], city, country_name)

    # Курьер: точного подомового индекса у InSales нет — берём КЛАДР-индекс города
    postal = str(shipping_address.get("zip") or location.get("kladr_zip")
                 or kladr.get("zip") or "").strip()
    raw = (shipping_address.get("address") or "").strip()
    street, house, apartment = _split_street_house_flat(raw, city)

    # Свободный ввод без DaData: «д.23к1 квартира 242» целиком уходит в дом —
    # отрезаем квартиру/офис из хвоста
    if house and not apartment:
        m = re.search(r"[\s,]+(?:квартира|кв\.?|офис|оф\.?)\s*(\S+)$", house,
                      re.IGNORECASE)
        if m:
            apartment = m.group(1)
            house = house[:m.start()].strip()

    parts = ShipmentAddressParts(
        postal_code=postal, country_name=country_name, city=city,
        street=street, house=house, apartment=apartment,
    )
    return None if parts.is_empty() else parts


def build_insales_shipment_address(delivery_info: dict, shipping_address: dict,
                                   delivery_title: str) -> str | None:
    """Построить адрес доставки.

    ПВЗ:       delivery_info.outlet.address (готовый)
    Курьер:    shipping_address.city + ", " + shipping_address.address
    Самовывоз: None
    """
    lower_title = delivery_title.lower()

    if "самовывоз" in lower_title or "не требуется" in lower_title:
        return None

    if delivery_info:
        outlet = delivery_info.get("outlet") or {}
        if outlet.get("address"):
            return outlet["address"]

    # Курьер — собираем из shipping_address
    parts = []
    city = (shipping_address.get("city") or "").strip()
    address = (shipping_address.get("address") or "").strip()
    if city:
        parts.append(city)
    if address:
        parts.append(address)
    return ", ".join(parts) if parts else None


def extract_insales_promo_code(discounts: list) -> str | None:
    """Извлечь код купона из массива скидок InSales.

    InSales не кладёт сам код купона в заказ — только в description
    вида "Скидка по купону <CODE>" (и discount_code_id для дозапроса).
    У нас все скидки — от купонов (автоскидок в магазине нет),
    поэтому парсим description первого элемента без фильтров.
    """
    if not discounts:
        return None
    for d in discounts:
        description = d.get("description") or ""
        m = _INSALES_COUPON_DESC_RE.search(description)
        if m:
            return m.group(1).strip()
        log.warning("InSales: промокод не распарсен из description",
                    description=description,
                    discount_code_id=d.get("discount_code_id"))
    return None


def normalize_insales_order(order_data: dict, config) -> NormalizedOrder:
    """Преобразовать сырой заказ InSales в NormalizedOrder."""
    client = order_data.get("client", {})
    delivery_info = order_data.get("delivery_info") or {}
    shipping_address = order_data.get("shipping_address") or {}
    delivery_title = order_data.get("delivery_title", "")
    payment_title = normalize_insales_payment_title(order_data.get("payment_title", ""))
    discounts = order_data.get("discounts", [])

    # Клиент (имя — из client, при отсутствии буквенного имени берём получателя
    # из shipping_address; телефон/email — из аккаунта client)
    customer = NormalizedCustomer(
        full_name=build_customer_name(client, shipping_address),
        phone=client.get("phone", ""),
        email=client.get("email", ""),
    )

    # Товарные позиции — резолвим через маппинг, цена делится по числу МС-позиций.
    # Цена за штуку = sale_price − скидка_на_строку/quantity.
    # full_sale_price НЕ подходит: оно включает COD-наценку, а у нас наценка
    # идёт отдельной услугой. discounts_amount — это скидка по купону, размазанная
    # на строку заказа (InSales).
    white_first = bool(getattr(config, "FAMILY_PACK_WHITE_FIRST_ENABLED", False))
    # Читаем цену/скидку белого из конфига только если промо реально включено —
    # чтобы не трогать эти поля конфига (и не падать на моках в тестах), когда
    # флаг выключен и они не нужны.
    white_base_price_kopecks = int(getattr(config, "FAMILY_PACK_WHITE_BASE_PRICE_KOPECKS", 759000)) \
        if white_first else 0
    white_discount_percent = int(getattr(config, "FAMILY_PACK_WHITE_DISCOUNT_PERCENT", 30)) \
        if white_first else 0
    # Признак предзаказа: резерв, который InSales ставит на строку при оформлении.
    # Карта None — флаг выключен или заказ несвежий; тогда у позиций preorder=None,
    # и «Тип заявки» с «Предзаказ: ожидаемые позиции» не заполняются вовсе.
    preorder_map = None
    if getattr(config, "INSALES_ORDER_TYPE_ENABLED", False):
        preorder_map = read_order_preorders(
            order_data,
            max_age_minutes=int(getattr(config, "INSALES_PREORDER_MAX_AGE_MINUTES", 20)))

    line_items = []
    for ol in order_data.get("order_lines", []):
        raw_sku = ol.get("sku") or ""
        variant_id = ol.get("variant_id")
        ms_skus = resolve_to_ms_skus(raw_sku, variant_id, white_first=white_first)
        sale_price = float(ol.get("sale_price", 0))
        discounts_amount = float(ol.get("discounts_amount") or 0)
        quantity = int(ol.get("quantity", 1))
        title = ol.get("title", "")

        if not ms_skus:
            log.error("InSales: неизвестная позиция, пропускаем",
                      variant_id=variant_id, raw_sku=raw_sku, title=title)
            continue

        # Цена за штуку с учётом купонной скидки (в копейках).
        line_total_cents = round((sale_price * quantity - discounts_amount) * 100)
        per_unit_cents = line_total_cents // quantity if quantity else 0
        prices = _split_family_pack_price(
            per_unit_cents, ms_skus, white_first,
            white_base_price_kopecks=white_base_price_kopecks,
            white_discount_percent=white_discount_percent,
        )
        # Одна строка InSales может разворачиваться в несколько позиций МС
        # (Family Pack = два кошелька), признак предзаказа у них общий — он свойство
        # строки заказа, а не разложенной позиции.
        line_is_preorder = (None if preorder_map is None
                            else bool(preorder_map.get(ol.get("id"), False)))
        for ms_sku, price in zip(ms_skus, prices):
            line_items.append(NormalizedLineItem(
                sku=ms_sku,
                title=title,
                price_cents=price,
                quantity=quantity,
                preorder=line_is_preorder,
            ))

    # Маппинг
    payment_type_key = _safe("payment_type", lambda: map_insales_payment_type(payment_title))
    delivery_sd_key = _safe("delivery_sd",
                             lambda: map_insales_delivery_sd(delivery_info, delivery_title))
    delivery_type_key = _safe("delivery_type",
                               lambda: map_insales_delivery_type(delivery_info, delivery_title))
    pvz_code = _safe("pvz_code", lambda: extract_insales_pvz_code(delivery_info))
    shipment_address = _safe("shipment_address",
                              lambda: build_insales_shipment_address(
                                  delivery_info, shipping_address, delivery_title))
    shipment_address_parts = _safe("shipment_address_parts",
                                    lambda: build_insales_address_parts(
                                        delivery_info, shipping_address, delivery_title))
    # Есть структура — плоскую строку НЕ шлём: МС сгенерит её из full сам,
    # а несовпадающую переданную строку он складывает дублем в addInfo («Другое»).
    # Плоский остаётся только как fallback при ошибке разбора.
    if shipment_address_parts is not None:
        shipment_address = None
    promo_code = _safe("promo_code", lambda: extract_insales_promo_code(discounts))

    # Услуга доставки
    is_cod = "при получении" in payment_title.lower()
    is_pickup = "самовывоз" in delivery_title.lower()
    delivery_services = []

    svc_name = _safe("delivery_service_name",
                      lambda: build_delivery_service_name(delivery_info, delivery_title))
    if svc_name:
        delivery_price = float(order_data.get("delivery_price", 0))
        if is_pickup:
            price_cents = 0
        else:
            # Всегда полная стоимость доставки (и предоплата, и COD)
            price_cents = round(delivery_price * 100)
        delivery_services.append(NormalizedDeliveryService(
            name=svc_name, price_cents=price_cents))

    # COD наценка
    margin_amount = float(order_data.get("margin_amount") or "0")
    cod_margin_cents = round(margin_amount * 100) if margin_amount > 0 else 0

    # Стоимость доставки для атрибута = доставка + наценка (целые рубли, как в WC)
    delivery_price_raw = float(order_data.get("delivery_price", 0))
    delivery_cost_total = delivery_price_raw + margin_amount
    delivery_cost_attr = str(int(round(delivery_cost_total))) if delivery_cost_total > 0 else None

    # Оценочная стоимость (товары со скидкой, без COD-наценки, целые рубли)
    items_total_cents = 0
    for ol in order_data.get("order_lines", []):
        sp = float(ol.get("sale_price", 0))
        q = int(ol.get("quantity", 1))
        disc = float(ol.get("discounts_amount") or 0)
        items_total_cents += round((sp * q - disc) * 100)
    estimated_cost = str(items_total_cents // 100)

    # Сумма к оплате получателю (только COD, целые рубли)
    total_price = float(order_data.get("total_price") or 0)
    total_to_pay = str(int(round(total_price))) if is_cod else None

    # Состояние оплаты
    paid_at = order_data.get("paid_at")
    financial_status = order_data.get("financial_status", "")
    is_paid = paid_at is not None and financial_status == "paid"

    # Комментарий
    comment = (order_data.get("comment") or "").strip() or None

    order_number = str(order_data.get("number", order_data.get("id", "")))

    return NormalizedOrder(
        source="insales",
        order_id=str(order_data.get("id", "")),
        order_number=f"{order_number}{_INSALES_ORDER_SUFFIX}",
        customer=customer,
        line_items=line_items,
        delivery_services=delivery_services,
        payment_title=payment_title,
        payment_type_key=payment_type_key,
        delivery_sd_key=delivery_sd_key,
        delivery_type_key=delivery_type_key,
        pvz_code=pvz_code,
        shipment_address=shipment_address,
        shipment_address_parts=shipment_address_parts,
        description=comment,
        promo_code=promo_code,
        organization_id=config.MS_ORGANIZATION_INSALES_ID or None,
        state_id=config.MS_STATE_INSALES_NEW_ID or None,
        project_id=config.MS_PROJECT_INSALES_ID or None,
        sales_channel_id=config.MS_SALES_CHANNEL_INSALES_ID or None,
        is_paid=is_paid,
        is_cod=is_cod,
        cod_margin_amount_cents=cod_margin_cents,
        delivery_cost_attr_value=delivery_cost_attr,
        estimated_cost=estimated_cost,
        total_to_pay=total_to_pay,
    )
