"""Tests for /api/push — the only write endpoint."""
from __future__ import annotations

import json


def test_push_accepts_valid_payload(client, server_module, sample_push_payload):
    resp = client.post("/api/push", json=sample_push_payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert "received" in body

    with server_module._lock:
        assert server_module._push_count == 1
        assert server_module._latest["symbol"] == "XAUUSD"
        assert "123456" in server_module._accounts


def test_push_stamps_source_and_received_at(client, server_module, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    with server_module._lock:
        assert server_module._latest["_source"] == "mt5_push"
        assert "_received_at" in server_module._latest


def test_push_rejects_empty_body(client):
    resp = client.post("/api/push", data="", content_type="application/json")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "empty" in body["error"].lower()


def test_push_rejects_malformed_json(client):
    resp = client.post(
        "/api/push", data="{not valid json", content_type="application/json"
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert body["error"]


def test_push_without_auth_token_rejected_when_required(auth_client, sample_push_payload):
    resp = auth_client.post("/api/push", json=sample_push_payload)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"


def test_push_with_wrong_auth_token_rejected(auth_client, sample_push_payload):
    resp = auth_client.post(
        "/api/push",
        json=sample_push_payload,
        headers={"X-Auth-Token": "wrong"},
    )
    assert resp.status_code == 401


def test_push_with_correct_auth_token_accepted(auth_client, sample_push_payload):
    resp = auth_client.post(
        "/api/push",
        json=sample_push_payload,
        headers={"X-Auth-Token": "secret-token"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_push_uses_root_account_number_when_present(client, server_module, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    with server_module._lock:
        assert "123456" in server_module._accounts


def test_push_falls_back_to_meta_account_number(client, server_module):
    payload = {"meta": {"account_number": 999}, "symbol": "XAUUSD"}
    client.post("/api/push", json=payload)
    with server_module._lock:
        assert "999" in server_module._accounts


def test_push_uses_unknown_when_no_account_number(client, server_module):
    payload = {"symbol": "XAUUSD"}
    client.post("/api/push", json=payload)
    with server_module._lock:
        assert "unknown" in server_module._accounts


def test_push_routes_separate_accounts_independently(client, server_module, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    other = dict(sample_push_payload)
    other["account_number"] = 222
    other["meta"] = dict(sample_push_payload["meta"], account_number=222)
    other["account"] = dict(sample_push_payload["account"], balance=20000.00)
    client.post("/api/push", json=other)

    with server_module._lock:
        assert server_module._push_count == 2
        assert "123456" in server_module._accounts
        assert "222" in server_module._accounts
        assert server_module._accounts["123456"]["account"]["balance"] == 10000.00
        assert server_module._accounts["222"]["account"]["balance"] == 20000.00


def test_push_increments_counter_per_call(client, server_module, sample_push_payload):
    for _ in range(3):
        client.post("/api/push", json=sample_push_payload)
    with server_module._lock:
        assert server_module._push_count == 3
        assert server_module._last_push is not None


def test_push_concurrent_requests_do_not_corrupt_state(server_module, sample_push_payload):
    """Hammer /api/push from threads with two accounts; verify counts add up.

    Flask's test_client is not thread-safe, so each thread gets its own client.
    """
    from concurrent.futures import ThreadPoolExecutor

    def post_for(account_no):
        payload = json.loads(json.dumps(sample_push_payload))
        payload["account_number"] = account_no
        payload["meta"]["account_number"] = account_no
        with server_module.app.test_client() as c:
            return c.post("/api/push", json=payload).status_code

    accounts = [111, 222] * 25  # 50 requests, 25 each
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(post_for, accounts))

    assert all(s == 200 for s in statuses)
    with server_module._lock:
        assert server_module._push_count == 50
        assert {"111", "222"} <= set(server_module._accounts.keys())
