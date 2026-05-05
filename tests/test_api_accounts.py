"""Tests for /api/accounts — multi-account list endpoint."""
from __future__ import annotations

import json
from datetime import datetime, timedelta


def test_accounts_empty_returns_zero(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 0
    assert body["accounts"] == []


def test_accounts_lists_after_push(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/accounts")
    body = resp.get_json()

    assert body["count"] == 1
    a = body["accounts"][0]
    assert a["account_number"] == "123456"
    assert a["account_name"] == "Test Trader"
    assert a["balance"] == 10000.00
    assert a["equity"] == 10250.50
    assert a["total_pnl"] == 1500.00
    assert a["win_rate"] == 60.00
    assert a["symbol"] == "XAUUSD"
    assert a["stale"] is False


def test_accounts_multiple_pushed_accounts_listed(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    other = json.loads(json.dumps(sample_push_payload))
    other["account_number"] = 999
    other["meta"]["account_number"] = 999
    client.post("/api/push", json=other)

    resp = client.get("/api/accounts")
    body = resp.get_json()
    assert body["count"] == 2
    nos = {a["account_number"] for a in body["accounts"]}
    assert nos == {"123456", "999"}


def test_accounts_stale_flag_below_60s(client, server_module, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    # Backdate "last seen" to 59 seconds ago — should NOT be stale
    with server_module._lock:
        server_module._account_seen["123456"] = datetime.now() - timedelta(seconds=59)

    resp = client.get("/api/accounts")
    a = resp.get_json()["accounts"][0]
    assert a["stale"] is False
    assert 58 <= a["last_seen_sec"] <= 61


def test_accounts_stale_flag_above_60s(client, server_module, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    with server_module._lock:
        server_module._account_seen["123456"] = datetime.now() - timedelta(seconds=61)

    resp = client.get("/api/accounts")
    a = resp.get_json()["accounts"][0]
    assert a["stale"] is True


def test_accounts_sort_live_before_stale(client, server_module, sample_push_payload):
    """Stale accounts should sort after fresh ones regardless of last_seen_sec."""
    client.post("/api/push", json=sample_push_payload)
    other = json.loads(json.dumps(sample_push_payload))
    other["account_number"] = 999
    other["meta"]["account_number"] = 999
    client.post("/api/push", json=other)

    # Make 123456 stale, 999 fresh
    with server_module._lock:
        server_module._account_seen["123456"] = datetime.now() - timedelta(seconds=120)
        server_module._account_seen["999"] = datetime.now()

    resp = client.get("/api/accounts")
    accts = resp.get_json()["accounts"]
    assert accts[0]["account_number"] == "999"
    assert accts[1]["account_number"] == "123456"
    assert accts[1]["stale"] is True


def test_accounts_handles_missing_meta_fields_gracefully(client):
    """Minimal payload — endpoint should not crash on missing fields."""
    minimal = {"account_number": 1, "meta": {}, "account": {}, "performance": {}}
    client.post("/api/push", json=minimal)

    resp = client.get("/api/accounts")
    a = resp.get_json()["accounts"][0]
    assert a["account_number"] == "1"
    assert a["account_name"] == "—"
    assert a["server"] == "—"
    assert a["currency"] == "USD"
    assert a["balance"] == 0
    assert a["symbol"] == "XAUUSD"


def test_accounts_sets_cors_and_no_cache_headers(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/accounts")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert resp.headers["Cache-Control"] == "no-cache"
