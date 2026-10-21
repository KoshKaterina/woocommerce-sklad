import copy
from pathlib import Path
import json
import os
from unittest.mock import MagicMock

import pytest

from woo_moysklad.woocommerce.consents import extract_checkout_consents, VERSION, CHANNELS
from woo_moysklad.woocommerce.normalizer import normalize_wc_order
from woo_moysklad.core.order_processor import OrderProcessor


def payload(choice="yes"):
    return {"id": 123, "meta_data": [{"key": "_sc_checkout_consent_snapshot", "value": {
        "schema": 1, "pdn": "yes", "marketing": choice,
        "recorded_at": "2026-10-19T18:00:00+03:00", "source": "checkout",
        "pdn_version": "policy-123456abcdef", "marketing_version": VERSION, "channels": CHANNELS[:],
    }}]}


@pytest.mark.parametrize("choice,expected", [("yes", "да"), ("no", "нет")])
def test_choices_and_one_order_field(choice, expected):
    order = normalize_wc_order(payload(choice))
    assert f"звонки): {expected}," in order.checkout_consents
    config = MagicMock(MS_ATTR_CHECKOUT_CONSENTS_ID="consents-field")
    processor = OrderProcessor(config, MagicMock(), MagicMock(), MagicMock())
    attrs = processor._build_attributes_from_normalized(order_number="123", order=order, source_lines=[])
    consent = [a for a in attrs if a["meta"]["href"].endswith("/consents-field")]
    assert len(consent) == 1
    assert consent[0]["value"] == order.checkout_consents


@pytest.mark.parametrize("change", [
    {"marketing_version": "checkout-email-v2-2026-10-08"},
    {"marketing": "true"}, {"pdn": "no"}, {"channels": ["email"]},
    {"recorded_at": "2026-10-19T18:00:00"}, {"recorded_at": "garbage"},
    {"pdn_version": "unknown"}, {"source": "insales"},
])
def test_incomplete_or_legacy_evidence_never_grants_consent(change):
    order = payload()
    order["meta_data"][0]["value"].update(change)
    assert extract_checkout_consents(order) is None


def test_email_only_and_absent_are_unknown():
    for order in ({"meta_data": [{"key": "_agree_marketing_email", "value": "yes"}]}, {}):
        assert extract_checkout_consents(order) is None


def test_ambiguous_duplicate_snapshot_ignored():
    order = payload()
    order["meta_data"] *= 2
    assert extract_checkout_consents(order) is None


def test_empty_config_disables_only_consent_field():
    config = MagicMock(MS_ATTR_CHECKOUT_CONSENTS_ID="")
    processor = OrderProcessor(config, MagicMock(), MagicMock(), MagicMock())
    attrs = processor._build_attributes_from_normalized(order_number="123", order=normalize_wc_order(payload()), source_lines=[])
    assert not any(a.get("value", "").startswith("ПДн:") for a in attrs if isinstance(a.get("value"), str))


def test_actual_php_producer_matches_python_consumer():
    fixture = os.environ.get("CHECKOUT_CONSENT_PHP_FIXTURE")
    if not fixture:
        pytest.skip("Run PHP producer fixture to verify cross-language contract")
    for order in json.loads(Path(fixture).read_text()):
        assert extract_checkout_consents(order) == order["expected"]
