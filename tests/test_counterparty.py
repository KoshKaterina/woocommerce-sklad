# Тесты обработчика контрагентов

from unittest.mock import MagicMock

import pytest

from woo_moysklad.counterparty_handler import (
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
