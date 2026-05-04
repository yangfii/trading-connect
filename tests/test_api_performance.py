"""Tests for /api/performance — REST snapshot endpoint."""
from __future__ import annotations


def test_performance_no_data_returns_helpful_error(client, monkeypatch, server_module):
    # Force file fallback to also return None
    monkeypatch.setattr(server_module, "_load_from_file", lambda: None)

    resp = client.get("/api/performance")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" in body
    assert "No data" in body["error"]


def test_performance_returns_latest_after_push(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/performance")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symbol"] == "XAUUSD"
    assert body["account"]["balance"] == 10000.00
    assert body["_source"] == "mt5_push"


def test_performance_specific_account_hit(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/performance?account=123456")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["meta"]["account_number"] == 123456
    assert body["_account_no"] == "123456"


def test_performance_specific_account_miss(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/performance?account=999999")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "error" in body
    assert "999999" in body["error"]


def test_performance_sets_cors_and_no_cache_headers(client, sample_push_payload):
    client.post("/api/push", json=sample_push_payload)
    resp = client.get("/api/performance")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert resp.headers["Cache-Control"] == "no-cache"


def test_performance_falls_back_to_file_when_memory_empty(client, monkeypatch, server_module):
    fake_file_data = {"symbol": "XAUUSD", "account": {"balance": 5000}}
    monkeypatch.setattr(server_module, "_load_from_file", lambda: fake_file_data)

    resp = client.get("/api/performance")
    body = resp.get_json()
    assert body["account"]["balance"] == 5000
