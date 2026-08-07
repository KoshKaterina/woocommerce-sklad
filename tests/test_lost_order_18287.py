"""Тесты на разбор потери заказа №18287 (07.08.2026).

Заказ оформили на сайте, а в МойСклад и в amoCRM он не доехал. Сложились две
поломки, и каждая закрыта тестом ниже:

  1. Сверка Woo девять дней возвращала ноль заказов. Даты фильтра уезжали в
     WooCommerce со смещением зоны («+00:00»), а он в паре с dates_are_gmt=true
     на такую строку молча отдаёт пустой список. Проверено на живом API.
  2. Вебхук «заказ создан» до сервиса не дошёл, пришёл только «заказ изменён» -
     а обработчик по нему выходил в ignored, не проверив, есть ли заказ в МС.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from woo_moysklad.core.source_adapter import WooSourceAdapter, _utc_naive_iso


# ── 1. Формат дат в окне сверки ──────────────────────────────────────────────

def test_okno_sverki_bez_smescheniya_zony():
    """Главная поломка: со смещением «+00:00» WooCommerce отдаёт пустой список."""
    woo = MagicMock()
    woo.get_orders.return_value = []
    adapter = WooSourceAdapter(woo, MagicMock())

    window_end = datetime(2026, 8, 7, 13, 15, 0, 123456, tzinfo=timezone.utc)
    window_start = window_end - timedelta(minutes=9)
    adapter.fetch_modified_in_window(window_start, window_end)

    kwargs = woo.get_orders.call_args.kwargs
    assert kwargs["modified_after"] == "2026-08-07T13:06:00"
    assert kwargs["modified_before"] == "2026-08-07T13:15:00"
    assert "+00:00" not in kwargs["modified_after"]
    assert "+00:00" not in kwargs["modified_before"]
    # Флаг остаётся: без него WooCommerce читает даты как МСК и окно уедет на 3 часа.
    assert kwargs["dates_are_gmt"] is True


def test_mikrosekundy_v_filtr_ne_popadayut():
    moment = datetime(2026, 8, 7, 13, 6, 0, 987654, tzinfo=timezone.utc)
    assert _utc_naive_iso(moment) == "2026-08-07T13:06:00"


def test_naive_vremya_ne_lomaetsya():
    """Окно приходит из reconciliation.run в UTC, но подстраховываемся."""
    moment = datetime(2026, 8, 7, 13, 6, 0)
    assert _utc_naive_iso(moment) == "2026-08-07T13:06:00"


# ── 2. Вебхук «изменён» по заказу, которого нет в МойСкладе ──────────────────

def _webhook_module(monkeypatch, ms_order):
    """Модуль main с подменённой сверкой: ms_order=None → заказа в МС нет."""
    from woo_moysklad import main

    recon = MagicMock()
    recon._find_order_in_ms.return_value = ms_order
    monkeypatch.setattr(main, "reconciliation", recon, raising=False)
    return main


def test_zakaza_net_v_ms_znachit_sozdayom(monkeypatch):
    main = _webhook_module(monkeypatch, ms_order=None)
    assert main._missing_in_ms("18287") is True


def test_zakaz_est_v_ms_nichego_ne_sozdayom(monkeypatch):
    main = _webhook_module(monkeypatch, ms_order={"id": "abc", "name": "06052"})
    assert main._missing_in_ms("18286") is False


def test_oshibka_poiska_schitaetsya_kak_est(monkeypatch):
    """МойСклад отвалился → не создаём вслепую, иначе задвоим заказ."""
    from woo_moysklad import main

    recon = MagicMock()
    recon._find_order_in_ms.side_effect = RuntimeError("МС 500")
    monkeypatch.setattr(main, "reconciliation", recon, raising=False)

    assert main._missing_in_ms("18287") is False


def test_bez_nomera_zakaza_ne_lezem_v_ms(monkeypatch):
    main = _webhook_module(monkeypatch, ms_order=None)
    assert main._missing_in_ms("") is False


def test_sverka_ne_podnyalas_ne_padaem(monkeypatch):
    """Вебхук пришёл раньше, чем сервис поднял сверку - молча пропускаем."""
    from woo_moysklad import main

    monkeypatch.setattr(main, "reconciliation", None, raising=False)
    assert main._missing_in_ms("18287") is False
