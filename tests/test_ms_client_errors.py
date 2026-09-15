# Клиент МС отличает обрыв связи от ошибки API: по этому признаку сверка решает, держать ли окно

from unittest.mock import MagicMock

import pytest
import requests

from woo_moysklad import ms_client as ms_module
from woo_moysklad.exceptions import MoySkladAPIError, MoySkladConnectionError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ms_module.time, "sleep", lambda seconds: None)
    cfg = MagicMock()
    cfg.MS_TOKEN = "token"
    cfg.MS_MAX_REQUESTS_PER_SECOND = 1000
    c = ms_module.MoySkladClient(cfg)
    c.session = MagicMock()
    return c


def test_connection_error_after_retries_is_connection_error(client):
    """Прокси не отвечает на все попытки GET — MoySkladConnectionError, он же MoySkladAPIError."""
    client.session.request.side_effect = requests.exceptions.ConnectionError("Unable to connect to proxy")

    with pytest.raises(MoySkladConnectionError) as info:
        client.get("entity/customerorder")

    assert isinstance(info.value, MoySkladAPIError)  # старые except MoySkladAPIError его ловят
    assert "Ошибка соединения с МС" in str(info.value)
    assert client.session.request.call_count == 3


def test_post_timeout_is_connection_error_without_retry(client):
    """Таймаут POST не ретраим (сервер мог создать документ), но это всё равно сбой связи."""
    client.session.request.side_effect = requests.exceptions.ReadTimeout("Read timed out")

    with pytest.raises(MoySkladConnectionError):
        client.post("entity/customerorder", {"name": "x"})

    assert client.session.request.call_count == 1


def test_http_400_is_not_connection_error(client):
    """Ответ МС с ошибкой — это ошибка данных, а не связи."""
    client.session.request.return_value = MagicMock(status_code=400, text="bad request", headers={})

    with pytest.raises(MoySkladAPIError) as info:
        client.get("entity/customerorder")

    assert not isinstance(info.value, MoySkladConnectionError)
