"""Tests for /api/stream — Server-Sent Events broadcast.

We test the broadcast helper directly rather than driving the streaming
generator end-to-end, since pytest's request context tears down before
the long-lived generator can be drained.
"""
from __future__ import annotations

import json


def test_broadcast_appends_to_each_client(server_module):
    q1, q2 = [], []
    with server_module._sse_lock:
        server_module._sse_clients.extend([q1, q2])

    server_module._broadcast({"hello": "world"})

    assert len(q1) == 1
    assert len(q2) == 1
    assert q1[0].startswith("data: ")
    assert json.loads(q1[0][len("data: "):].strip()) == {"hello": "world"}


def test_broadcast_is_jsonifiable_payload(server_module, sample_push_payload):
    q = []
    with server_module._sse_lock:
        server_module._sse_clients.append(q)

    server_module._broadcast(sample_push_payload)
    assert q
    parsed = json.loads(q[0][len("data: "):].strip())
    assert parsed["symbol"] == "XAUUSD"


def test_push_triggers_broadcast(client, server_module, sample_push_payload):
    q = []
    with server_module._sse_lock:
        server_module._sse_clients.append(q)

    client.post("/api/push", json=sample_push_payload)
    assert len(q) == 1
    assert "XAUUSD" in q[0]


def test_stream_endpoint_registers_client_and_emits_snapshot(client, server_module, sample_push_payload):
    """Open the SSE stream, read the initial snapshot, then close.

    werkzeug's test client closes the response when the `with` block exits,
    which fires the generator's `finally` so the client is unregistered.
    """
    client.post("/api/push", json=sample_push_payload)

    with client.get("/api/stream", buffered=False) as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        # Iterate just enough to get the initial snapshot frame.
        it = resp.response
        first = next(iter(it))
        assert b"XAUUSD" in first
