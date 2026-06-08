# Тесты uCoz-поллера, state-файла и HTTP-клиента.

import json
from unittest.mock import MagicMock

import pytest
import requests

from woo_moysklad.ucoz.client import UcozClient
from woo_moysklad.ucoz.poller import UcozPoller
from woo_moysklad.ucoz.state import UcozState


# --- UcozState ---

def test_state_returns_none_when_file_missing(tmp_path):
    state = UcozState(tmp_path / "missing.json")
    assert state.load() is None


def test_state_roundtrip(tmp_path):
    state = UcozState(tmp_path / "state.json")
    state.save(391)
    assert state.load() == 391


def test_state_save_overwrites(tmp_path):
    state = UcozState(tmp_path / "state.json")
    state.save(100)
    state.save(200)
    assert state.load() == 200


def test_state_creates_parent_dir(tmp_path):
    state = UcozState(tmp_path / "nested" / "dir" / "state.json")
    state.save(1)
    assert state.load() == 1


def test_state_returns_none_on_corrupted_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not valid", encoding="utf-8")
    state = UcozState(path)
    assert state.load() is None


def test_state_returns_none_when_value_not_int(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_processed_ucoz_order_id": "abc"}), encoding="utf-8")
    state = UcozState(path)
    assert state.load() is None


# --- UcozClient ---

def _mock_response(json_data=None, status=200, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        resp.json = MagicMock(side_effect=ValueError("no json"))
    resp.text = text
    return resp


def test_client_returns_parsed_order():
    client = UcozClient("http://example.com/uamo.php")
    client.session = MagicMock()
    client.session.get.return_value = _mock_response(
        json_data={"order": {"id": 391}, "goods": []})
    result = client.get_latest_order()
    assert result == {"order": {"id": 391}, "goods": []}


def test_client_returns_none_on_request_error():
    client = UcozClient("http://example.com/uamo.php")
    client.session = MagicMock()
    client.session.get.side_effect = requests.ConnectionError("boom")
    assert client.get_latest_order() is None


def test_client_returns_none_on_invalid_json():
    client = UcozClient("http://example.com/uamo.php")
    client.session = MagicMock()
    client.session.get.return_value = _mock_response(json_data=None, text="<html>error</html>")
    assert client.get_latest_order() is None


def test_client_returns_none_on_missing_order_key():
    client = UcozClient("http://example.com/uamo.php")
    client.session = MagicMock()
    client.session.get.return_value = _mock_response(json_data={"goods": []})
    assert client.get_latest_order() is None


# --- UcozPoller ---

def _make_poller(state_value=None, api_response=None):
    client = MagicMock()
    client.get_latest_order.return_value = api_response

    state = MagicMock()
    state.load.return_value = state_value

    processor = MagicMock()
    processor.process_ucoz_order.return_value = []

    poller = UcozPoller(client, state, processor, interval_seconds=300)
    return poller, client, state, processor


def _api(order_id: int):
    return {"order": {"id": order_id}, "goods": []}


def test_poller_cold_start_saves_without_processing():
    poller, client, state, processor = _make_poller(
        state_value=None, api_response=_api(391))
    poller.run_once()

    state.save.assert_called_once_with(391)
    processor.process_ucoz_order.assert_not_called()


def test_poller_same_id_does_nothing():
    poller, client, state, processor = _make_poller(
        state_value=391, api_response=_api(391))
    poller.run_once()

    state.save.assert_not_called()
    processor.process_ucoz_order.assert_not_called()


def test_poller_id_decreased_warns_only():
    """Защита от ротации/сброса id на стороне uCoz."""
    poller, client, state, processor = _make_poller(
        state_value=500, api_response=_api(100))
    poller.run_once()

    state.save.assert_not_called()
    processor.process_ucoz_order.assert_not_called()


def test_poller_next_id_processes_and_saves():
    raw = _api(392)
    poller, client, state, processor = _make_poller(
        state_value=391, api_response=raw)
    poller.run_once()

    processor.process_ucoz_order.assert_called_once_with(raw)
    state.save.assert_called_once_with(392)


def test_poller_gap_still_processes_current():
    """Gap = пропуск заказов. Их не восстановить (uamo.php отдаёт только последний),
    но текущий надо обработать."""
    raw = _api(395)
    poller, client, state, processor = _make_poller(
        state_value=391, api_response=raw)
    poller.run_once()

    # Обрабатываем последний, state двигаем на него.
    processor.process_ucoz_order.assert_called_once_with(raw)
    state.save.assert_called_once_with(395)


def test_poller_does_not_save_on_processor_error():
    """Если обработка упала — state не двигаем, чтобы повторить на следующем тике."""
    raw = _api(392)
    poller, client, state, processor = _make_poller(
        state_value=391, api_response=raw)
    processor.process_ucoz_order.side_effect = RuntimeError("ms down")

    poller.run_once()  # не должен выкинуть исключение

    processor.process_ucoz_order.assert_called_once_with(raw)
    state.save.assert_not_called()


def test_poller_does_nothing_on_client_error():
    poller, client, state, processor = _make_poller(
        state_value=391, api_response=None)
    poller.run_once()

    state.save.assert_not_called()
    processor.process_ucoz_order.assert_not_called()


def test_poller_skips_when_order_id_is_not_int():
    poller, client, state, processor = _make_poller(
        state_value=391, api_response={"order": {"id": "abc"}})
    poller.run_once()

    state.save.assert_not_called()
    processor.process_ucoz_order.assert_not_called()
