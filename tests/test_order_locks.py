# Тесты замка по номеру заказа (защита от гонки вебхука и сверки)

import json
import os
import threading
import time
from unittest.mock import MagicMock

import pytest

from woo_moysklad.core.order_locks import busy_orders, claim_order
from woo_moysklad.core.order_processor import OrderProcessor

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample_order.json")


@pytest.fixture
def sample_order():
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- сам замок ---

def test_claim_free_number():
    with claim_order("18643") as acquired:
        assert acquired is True
        assert "18643" in busy_orders()
    assert "18643" not in busy_orders()


def test_second_claim_of_same_number_fails():
    with claim_order("18643") as first:
        assert first is True
        with claim_order("18643") as second:
            assert second is False
    assert busy_orders() == set()


def test_different_numbers_do_not_block_each_other():
    with claim_order("18643") as a, claim_order("18644") as b:
        assert a is True
        assert b is True


def test_number_released_after_exception():
    with pytest.raises(RuntimeError):
        with claim_order("18643") as acquired:
            assert acquired is True
            raise RuntimeError("падение внутри обработки")
    assert "18643" not in busy_orders()


def test_only_one_thread_gets_the_number():
    """Десять потоков рвутся к одному номеру — занять его должен ровно один."""
    got = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        with claim_order("18643") as acquired:
            if acquired:
                got.append(threading.current_thread().name)
                time.sleep(0.02)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(got) == 1
    assert busy_orders() == set()


# --- замок в работе обработчика ---

def _make_slow_processor():
    """Процессор, у которого POST в МС длится 100 мс — чтобы поймать гонку."""
    config = MagicMock()
    for attr in ("MS_ORGANIZATION_ID", "MS_STORE_ID", "MS_STORE_OPENED_ID",
                 "MS_CURRENCY_RUB_ID", "MS_SALES_CHANNEL_ID", "MS_STATE_NEW_LEAD_ID",
                 "MS_ATTR_ORDER_NUMBER_ID"):
        setattr(config, attr, f"{attr.lower()}-uuid")

    ms = MagicMock()
    ms.make_meta.return_value = {"meta": {"href": "...", "type": "test"}}
    ms.make_state_meta.return_value = {"meta": {"href": "...", "type": "state"}}
    ms.find_by_filter.return_value = []  # МС всегда отвечает «такого заказа нет»

    def slow_post(path, body):
        time.sleep(0.1)
        return {"id": "new-order", "name": "06784", "meta": {}}

    ms.post.side_effect = slow_post

    cp = MagicMock()
    cp.find_or_create_from_normalized.return_value = {"meta": {"type": "counterparty"}}

    pm = MagicMock()
    pm.build_positions_from_normalized.return_value = {
        "regular": [{"quantity": 1, "price": 100000, "discount": 0, "vat": 0,
                     "assortment": {"meta": {"type": "product", "href": "..."}}}],
        "opened": [],
        "services": [],
    }
    return OrderProcessor(config, ms, cp, pm), ms


def test_webhook_and_reconciliation_create_only_one_order(sample_order):
    """Два потока (вебхук и сверка) берутся за один заказ — POST в МС ровно один.

    Воспроизводит 27.08.2026: МС в обоих потоках отвечает «заказа нет», обработка
    идёт секунды. Без замка получалась пара 06784/06785.
    """
    processor, ms = _make_slow_processor()
    results = {}
    barrier = threading.Barrier(2)

    def run(tag):
        barrier.wait()
        results[tag] = processor.process_order(sample_order)

    threads = [threading.Thread(target=run, args=(tag,))
               for tag in ("вебхук", "сверка")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ms.post.call_count == 1
    created = [tag for tag, res in results.items() if res]
    skipped = [tag for tag, res in results.items() if res == []]
    assert len(created) == 1
    assert len(skipped) == 1
    assert busy_orders() == set()
