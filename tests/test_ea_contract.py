"""Contract / golden-file test between the MT5 EA and the dashboard server.

Loads a captured EA push payload (matching the JSON shape produced by
GoldPerformanceTracker.mq5's BuildJSON) and asserts the server normalises it
into the exact shape /api/accounts is expected to return. Locks the
EA -> server contract so a change on either side breaks loudly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "sample_ea_push.json"


@pytest.fixture
def ea_payload():
    return json.loads(FIXTURE.read_text())


def test_fixture_has_expected_top_level_keys(ea_payload):
    """The EA contract: every push has these top-level sections."""
    assert set(ea_payload.keys()) >= {
        "source", "updated", "symbol", "account_number",
        "meta", "account", "performance", "risk",
    }


def test_fixture_meta_shape(ea_payload):
    meta = ea_payload["meta"]
    assert set(meta.keys()) >= {
        "account_number", "account_name", "server",
        "currency", "leverage", "account_type", "group",
    }


def test_fixture_performance_shape(ea_payload):
    perf = ea_payload["performance"]
    assert set(perf.keys()) >= {
        "total_trades", "win_trades", "loss_trades",
        "win_rate", "total_pnl", "profit_factor",
        "avg_win", "avg_loss", "best_trade", "worst_trade",
    }


def test_server_accepts_real_ea_payload(client, ea_payload):
    resp = client.post("/api/push", json=ea_payload)
    assert resp.status_code == 200


def test_accounts_endpoint_normalises_ea_payload(client, ea_payload):
    """Golden-file: server output for a real EA push is stable."""
    client.post("/api/push", json=ea_payload)
    resp = client.get("/api/accounts")
    body = resp.get_json()

    assert body["count"] == 1
    a = body["accounts"][0]

    # Identity
    assert a["account_number"] == "87654321"
    assert a["account_name"] == "Yang Fi - Prop"
    assert a["server"] == "FTMO-Demo"
    assert a["account_type"] == "Demo"
    assert a["group"] == "Prop"

    # Account/financial fields flow through unchanged
    assert a["balance"] == 100000.00
    assert a["equity"] == 102345.67
    assert a["open_pnl"] == 2345.67
    assert a["open_pos"] == 3

    # Performance section
    assert a["total_pnl"] == 8923.45
    assert a["win_rate"] == 62.68
    assert a["total_trades"] == 142

    assert a["symbol"] == "XAUUSD"
    assert a["stale"] is False


def test_performance_endpoint_round_trips_full_payload(client, ea_payload):
    client.post("/api/push", json=ea_payload)
    resp = client.get(f"/api/performance?account={ea_payload['account_number']}")
    body = resp.get_json()

    # Every original section survives intact.
    assert body["meta"] == ea_payload["meta"]
    assert body["account"] == ea_payload["account"]
    assert body["performance"] == ea_payload["performance"]
    assert body["risk"] == ea_payload["risk"]
