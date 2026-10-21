"""Exercise the actual ASGI handler without lifespan, schedulers or external clients."""
import asyncio
import base64
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from woo_moysklad import main

SECRET = 'unit-test-only'
BODY = b'{"id":123}'

def sign(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()

@pytest.mark.parametrize('body,signature,secret,topic,status,calls', [
    (BODY, None, SECRET, 'order.created', 401, 0),
    (BODY, '', SECRET, 'order.created', 401, 0),
    (BODY, 'wrong', SECRET, 'order.created', 401, 0),
    (BODY, sign(BODY), '', 'order.created', 503, 0),
    (BODY, sign(BODY), None, 'order.created', 503, 0),
    (BODY, sign(BODY), SECRET, 'order.created', 200, 1),
    (BODY+b' ', sign(BODY), SECRET, 'order.created', 401, 0),
    (b'{', sign(b'{'), SECRET, 'order.created', 400, 0),
    (b'webhook_id=0', sign(b'webhook_id=0'), SECRET, '', 200, 0),
    (BODY, None, SECRET, '', 401, 0),
])
def test_signature_gate(monkeypatch, body, signature, secret, topic, status, calls):
    processor = Mock()
    processor.process_order.return_value = []
    monkeypatch.setattr(main, 'config', SimpleNamespace(WC_WEBHOOK_SECRET=secret) if secret is not None else None)
    monkeypatch.setattr(main, 'order_processor', processor)
    monkeypatch.setattr(main, '_webhook_dedup', {})
    monkeypatch.setattr(main, '_webhook_lock', asyncio.Lock())
    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            headers = {'x-wc-webhook-topic': topic}
            if signature is not None:
                headers['x-wc-webhook-signature'] = signature
            return await client.post('/webhook/order', content=body, headers=headers)
    response = asyncio.run(request())
    assert response.status_code == status
    assert processor.process_order.call_count == calls
    processor.mark_paid.assert_not_called()
