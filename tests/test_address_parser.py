# Тесты разбора адреса на компоненты (shipmentAddressFull).
# Образцы — реальные строки из заказов WooCommerce после плагина DaData.

from woo_moysklad.core.address_parser import (
    ISO_TO_COUNTRY_NAME,
    parse_wc_address,
)


def _shipping(country="RU", city="", postcode="", state="", address_1="", address_2=""):
    return {
        "country": country,
        "city": city,
        "postcode": postcode,
        "state": state,
        "address_1": address_1,
        "address_2": address_2,
    }


# --- Курьер: чистый DaData-формат ---------------------------------------

def test_courier_moscow_with_office():
    p = parse_wc_address(
        _shipping(city="Москва", postcode="117342",
                  address_1="г Москва, ул Бутлерова, д 17, офис 5126"),
        "courier",
    )
    assert p.country_name == "Россия"
    assert p.postal_code == "117342"
    assert p.city == "Москва"
    assert p.street == "ул Бутлерова"
    assert p.house == "17"
    assert p.apartment == "5126"


def test_courier_spb_with_flat():
    p = parse_wc_address(
        _shipping(city="Санкт-Петербург", postcode="190000",
                  address_1="г Санкт-Петербург, Невский пр-кт, д 70, кв 55"),
        "courier",
    )
    assert p.street == "Невский пр-кт"
    assert p.house == "70"
    assert p.apartment == "55"


def test_courier_korpus_stroenie_in_house():
    p = parse_wc_address(
        _shipping(city="Москва", postcode="107031",
                  address_1="г Москва, Малый Кисельный пер, д 3 стр 2, кв 12"),
        "courier",
    )
    assert p.street == "Малый Кисельный пер"
    assert p.house == "3 стр 2"
    assert p.apartment == "12"


def test_courier_dvld_no_flat_with_settlement():
    # «двлд 9» (домовладение), населённый пункт «деревня Юрлово» остаётся в street
    p = parse_wc_address(
        _shipping(city="Химки", postcode="141544",
                  state="ЮРЛОВО, Г.О ХИМКИ",
                  address_1="Московская обл, г Химки, деревня Юрлово, ул Венская, двлд 9"),
        "courier",
    )
    assert p.house == "9"
    assert p.apartment == ""
    assert p.street == "деревня Юрлово, ул Венская"
    # регион не пишем в справочник, но сохраняем в «Другое»
    assert p.add_info == "ЮРЛОВО, Г.О ХИМКИ"


def test_courier_kazakhstan_street_without_type():
    p = parse_wc_address(
        _shipping(country="KZ", city="Караганда", postcode="M01F4X5",
                  address_1="Казахстан, Карагандинская обл, "
                            "Карагандинская городская администрация, "
                            "г Караганда, Ондасынова, д 3"),
        "courier",
    )
    assert p.country_name == "Казахстан"
    assert p.city == "Караганда"
    assert p.street == "Ондасынова"
    assert p.house == "3"


def test_courier_belarus():
    p = parse_wc_address(
        _shipping(country="BY", city="Борисов", postcode="222514",
                  address_1="Беларусь, Минская обл, Борисовский р-н, "
                            "г Борисов, Минский пер, д 3"),
        "courier",
    )
    assert p.country_name == "Беларусь"
    assert p.street == "Минский пер"
    assert p.house == "3"


def test_courier_house_with_letter():
    p = parse_wc_address(
        _shipping(city="Красноярск", postcode="660133",
                  address_1="г Красноярск, ул Партизана Железняка, д 46а"),
        "courier",
    )
    assert p.street == "ул Партизана Железняка"
    assert p.house == "46а"


# --- ПВЗ: best-effort из address_2 (CDEK-строка с кодом) -----------------

def test_pvz_strips_code_and_city():
    p = parse_wc_address(
        _shipping(city="Москва",
                  address_2="MSK2469, Москва, ул. Твардовского, 2 корп.4, стр.1"),
        "pvz",
    )
    assert p.city == "Москва"
    assert "MSK2469" not in p.street
    assert "Твардовского" in p.street
    # дубль города не остаётся в street
    assert not p.street.startswith("Москва")


def test_pvz_simple():
    p = parse_wc_address(
        _shipping(city="Кингисепп", address_2="KNP16, Кингисепп, 1-я линия, 2Д"),
        "pvz",
    )
    assert "KNP16" not in p.street
    assert "линия" in p.street


# --- Прочее --------------------------------------------------------------

def test_office_pickup_empty():
    p = parse_wc_address(_shipping(city="Москва", address_2=""), "pvz")
    assert p.street == ""
    assert p.city == "Москва"


def test_unknown_country_iso_left_empty():
    p = parse_wc_address(_shipping(country="US", city="New York",
                                   address_1="5th Avenue, д 1"), "courier")
    assert p.country_name == ""  # нет в таблице ISO → пусто, остальное парсится


def test_iso_table_has_cis():
    assert ISO_TO_COUNTRY_NAME["RU"] == "Россия"
    assert ISO_TO_COUNTRY_NAME["KZ"] == "Казахстан"
    assert ISO_TO_COUNTRY_NAME["BY"] == "Беларусь"
