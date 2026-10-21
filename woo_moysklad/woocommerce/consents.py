"""Versioned checkout evidence -> one MS text field. No contact subscription writes."""

from datetime import datetime
import re

VERSION = "checkout-marketing-v1-2026-10-19"
CHANNELS = ["email", "sms", "messengers", "calls"]


def extract_checkout_consents(order_data: dict) -> str | None:
    entries = [m for m in order_data.get("meta_data", [])
               if isinstance(m, dict) and m.get("key") == "_sc_checkout_consent_snapshot"]
    # Missing legacy metadata is unknown, never a broad yes/no inferred from email.
    if len(entries) != 1:
        return None
    snapshot = entries[0].get("value")
    if not isinstance(snapshot, dict):
        return None
    if (type(snapshot.get("schema")) is not int or snapshot.get("schema") != 1 or snapshot.get("pdn") != "yes"
            or snapshot.get("marketing") not in ("yes", "no")
            or snapshot.get("marketing_version") != VERSION
            or snapshot.get("channels") != CHANNELS
            or snapshot.get("source") != "checkout"):
        return None
    pdn_version = snapshot.get("pdn_version", "")
    if not isinstance(pdn_version, str) or not re.fullmatch(r"policy-[0-9a-f]{12}", pdn_version):
        return None
    timestamp = snapshot.get("recorded_at", "")
    if not isinstance(timestamp, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", timestamp):
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    choice = "да" if snapshot["marketing"] == "yes" else "нет"
    return (f"ПДн: да, реклама (email/SMS/мессенджеры/звонки): {choice}, "
            f"дата: {timestamp}, версия ПДн: {pdn_version}, версия рекламы: {VERSION}")
