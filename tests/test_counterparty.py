# Тесты обработчика контрагентов

from unittest.mock import MagicMock

import pytest

from woo_moysklad.core.counterparty_handler import (
    CounterpartyHandler,
    normalize_phone,
    split_full_name,
)
from woo_moysklad.exceptions import CounterpartyError


# --- split_full_name ---

def test_split_two_words():
    assert split_full_name("Екатерина Кошенкова") == ("Екатерина", "Кошенкова", "")

def test_split_three_words():
    assert split_full_name("Иванов Иван Иванович") == ("Иванов", "Иван", "Иванович")

def test_split_one_word():
    assert split_full_name("Мария") == ("Мария", "", "")

def test_split_empty():
    assert split_full_name("") == ("", "", "")

def test_split_extra_spaces():
    assert split_full_name("  Иван   Петров  ") == ("Иван", "Петров", "")


# --- normalize_phone ---

def test_normalize_plus7():
    assert normalize_phone("+79099371845") == "+79099371845"

def test_normalize_8_prefix():
    assert normalize_phone("89099371845") == "+79099371845"

def test_normalize_7_prefix():
    assert normalize_phone("79099371845") == "+79099371845"

def test_normalize_with_formatting():
    assert normalize_phone("+7 (909) 937-18-45") == "+79099371845"

def test_normalize_empty():
    assert normalize_phone("") == ""

def test_normalize_short():
    # Короткий номер — вернуть как есть
    result = normalize_phone("12345")
    assert result == "12345"


# --- CounterpartyHandler ---

def make_handler(find_result=None, post_result=None, put_result=None):
    ms = MagicMock()
    ms.find_by_filter.return_value = find_result or []
    ms.post.return_value = post_result or {"id": "new-id", "meta": {"href": "...", "type": "counterparty"}}
    ms.put.return_value = put_result or {}
    return CounterpartyHandler(ms), ms


# --- Телеграм в доп. поле контрагента ---

TG_ATTR = "tg-attr-uuid"
_EXISTING_REAL = {
    "id": "cp-77",
    "name": "Иван Петров",
    "email": "i@x.ru",
    "companyType": "individual",
    "meta": {"href": "...", "type": "counterparty"},
}


def make_tg_handler(find_result=None, attr_id=TG_ATTR):
    ms = MagicMock()
    ms.find_by_filter.return_value = find_result or []
    ms.post.return_value = {"id": "new-id", "meta": {"href": "...", "type": "counterparty"}}
    ms.put.return_value = {}
    return CounterpartyHandler(ms, telegram_attr_id=attr_id), ms


def _tg_values(data):
    return [a["value"] for a in data.get("attributes", [])
            if a["meta"]["href"].endswith("/entity/counterparty/metadata/attributes/" + TG_ATTR)]


def _billing(telegram="@durov_team"):
    return {"first_name": "Иван Петров", "phone": "+79099371845", "email": "i@x.ru", "telegram": telegram}


def test_telegram_written_to_new_counterparty():
    """Новый контрагент - ник сразу в доп. поле «Телеграм» (ссылка на поле контрагента, не заказа)."""
    handler, ms = make_tg_handler(find_result=[])
    handler.find_or_create(_billing())
    assert _tg_values(ms.post.call_args[0][1]) == ["@durov_team"]


def test_telegram_filled_into_existing_empty_field():
    """Существующий контрагент без ника - дописываем ник, остальное не трогаем."""
    handler, ms = make_tg_handler(find_result=[dict(_EXISTING_REAL, attributes=[])])
    handler.find_or_create(_billing())
    ms.put.assert_called_once()
    patch = ms.put.call_args[0][1]
    assert _tg_values(patch) == ["@durov_team"]
    assert set(patch) == {"attributes"}


def test_telegram_not_overwritten_when_filled():
    """У контрагента уже есть ник - не перезаписываем (мог поправить менеджер)."""
    existing = dict(_EXISTING_REAL, attributes=[{"id": TG_ATTR, "value": "@old_nick"}])
    handler, ms = make_tg_handler(find_result=[existing])
    handler.find_or_create(_billing())
    ms.put.assert_not_called()


def test_telegram_skipped_without_attr_id_or_value():
    """UUID поля не задан или ника нет - доп. поле не пишем."""
    handler, ms = make_tg_handler(find_result=[], attr_id="")
    handler.find_or_create(_billing())
    assert "attributes" not in ms.post.call_args[0][1]

    handler, ms = make_tg_handler(find_result=[])
    handler.find_or_create(_billing(telegram=""))
    assert "attributes" not in ms.post.call_args[0][1]


def test_telegram_from_normalized_customer():
    """find_or_create_from_normalized несёт customer.telegram."""
    from woo_moysklad.core.normalized_order import NormalizedCustomer
    handler, ms = make_tg_handler(find_result=[])
    handler.find_or_create_from_normalized(
        NormalizedCustomer(full_name="Иван Петров", phone="+79099371845", email="", telegram="@durov_team"))
    assert _tg_values(ms.post.call_args[0][1]) == ["@durov_team"]


def test_find_existing_counterparty():
    """Контрагент найден по телефону — возвращаем его meta."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-123",
        "name": "Екатерина",
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"},
    }])
    billing = {"first_name": "Екатерина", "phone": "+79099371845", "email": "test@test.com"}
    result = handler.find_or_create(billing)
    assert "meta" in result
    ms.post.assert_not_called()


def test_create_new_counterparty():
    """Контрагент не найден — создаём нового."""
    handler, ms = make_handler(find_result=[])
    billing = {"first_name": "Екатерина Кошенкова", "phone": "+79099371845", "email": "test@test.com"}
    result = handler.find_or_create(billing)
    assert "meta" in result
    ms.post.assert_called_once()

    # Проверяем данные для создания
    call_data = ms.post.call_args[0][1]
    assert call_data["name"] == "Екатерина Кошенкова"
    assert call_data["firstName"] == "Екатерина"
    assert call_data["lastName"] == "Кошенкова"
    assert call_data["companyType"] == "individual"


def test_update_company_type():
    """Контрагент найден, но companyType != individual — обновляем."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-123",
        "name": "ООО Тест",
        "companyType": "legal",
        "meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"},
    }])
    billing = {"first_name": "Тест", "phone": "+79099371845", "email": ""}
    handler.find_or_create(billing)
    ms.put.assert_called_once()


def test_enrich_placeholder_name_and_email():
    """Найден контрагент с именем-телефоном и без почты → дозаполняем из заказа."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-123",
        "name": "79778271097",        # заглушка-телефон
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty", "mediaType": "application/json"},
    }])
    billing = {"first_name": "Альберт Капитулов", "phone": "+79778271097",
               "email": "kapitulov27@gmail.com"}
    handler.find_or_create(billing)
    ms.put.assert_called_once()
    patch = ms.put.call_args[0][1]
    assert patch["name"] == "Альберт Капитулов"
    assert patch["firstName"] == "Альберт"
    assert patch["lastName"] == "Капитулов"
    assert patch["email"] == "kapitulov27@gmail.com"


def test_enrich_does_not_overwrite_real_name():
    """У контрагента уже реальное имя → имя не трогаем, но пустой email дозаполняем."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-9",
        "name": "Мария Иванова",       # реальное имя — не перезаписываем
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty"},
    }])
    billing = {"first_name": "Другое Имя", "phone": "+79099371845", "email": "m@x.ru"}
    handler.find_or_create(billing)
    patch = ms.put.call_args[0][1]
    assert "name" not in patch          # реальное имя сохранено
    assert patch["email"] == "m@x.ru"   # email дозаполнен


def test_enrich_extends_incomplete_name():
    """Имя из заказа дополняет существующее («Александр» + фамилия) → обновляем (заказ 17146)."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-17146",
        "name": "Александр",            # неполное имя (напр. создан amoCRM)
        "email": "alexlazarev656@gmail.com",
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty"},
    }])
    billing = {"first_name": "Александр Лазарев", "phone": "+79149080820",
               "email": "alexlazarev656@gmail.com"}
    handler.find_or_create(billing)
    patch = ms.put.call_args[0][1]
    assert patch["name"] == "Александр Лазарев"
    assert patch["firstName"] == "Александр"
    assert patch["lastName"] == "Лазарев"


def test_enrich_extends_partial_word():
    """«Алекс» → «Александр Иванов»: имя уточнили и дописали — обновляем."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-8",
        "name": "Алекс",
        "email": "a@x.ru",
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty"},
    }])
    billing = {"first_name": "Александр Иванов", "phone": "+79099371845", "email": "a@x.ru"}
    handler.find_or_create(billing)
    patch = ms.put.call_args[0][1]
    assert patch["name"] == "Александр Иванов"


def test_enrich_no_extension_for_different_name():
    """«Александра» vs «Александр Иванов» — не префикс, имя не трогаем."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-10",
        "name": "Александра",
        "email": "a@x.ru",
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty"},
    }])
    billing = {"first_name": "Александр Иванов", "phone": "+79099371845", "email": "a@x.ru"}
    handler.find_or_create(billing)
    ms.put.assert_not_called()


def test_enrich_skips_when_nothing_to_add():
    """Реальное имя + есть email → PUT не нужен."""
    handler, ms = make_handler(find_result=[{
        "id": "cp-7",
        "name": "Мария Иванова",
        "email": "old@x.ru",
        "companyType": "individual",
        "meta": {"href": "...", "type": "counterparty"},
    }])
    billing = {"first_name": "Мария Иванова", "phone": "+79099371845", "email": "new@x.ru"}
    handler.find_or_create(billing)
    ms.put.assert_not_called()           # ничего не дозаполняем, email не затираем


def test_multiple_counterparties():
    """Найдено несколько контрагентов — берём первого."""
    handler, ms = make_handler(find_result=[
        {"id": "cp-1", "name": "Первый", "companyType": "individual",
         "meta": {"href": "...", "type": "counterparty"}},
        {"id": "cp-2", "name": "Второй", "companyType": "individual",
         "meta": {"href": "...", "type": "counterparty"}},
    ])
    billing = {"first_name": "Тест", "phone": "+79099371845", "email": ""}
    result = handler.find_or_create(billing)
    assert "meta" in result


def test_no_phone_raises():
    """Телефон не указан — CounterpartyError."""
    handler, _ = make_handler()
    with pytest.raises(CounterpartyError):
        handler.find_or_create({"first_name": "Тест", "phone": "", "email": ""})
