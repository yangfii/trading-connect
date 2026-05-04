"""Shared fixtures for dashboard_server tests.

Each test gets a fresh import of dashboard_server so the module-level
in-memory state (_latest, _accounts, _push_count, etc.) does not leak
between tests.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def server_module(monkeypatch):
    """Reload dashboard_server with a clean state and no auth token."""
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    if "dashboard_server" in sys.modules:
        del sys.modules["dashboard_server"]
    module = importlib.import_module("dashboard_server")
    yield module
    # Clear SSE clients so background generators don't linger across tests.
    with module._sse_lock:
        module._sse_clients.clear()


@pytest.fixture
def client(server_module):
    server_module.app.config["TESTING"] = True
    with server_module.app.test_client() as c:
        yield c


@pytest.fixture
def auth_server(monkeypatch):
    """Reload dashboard_server with AUTH_TOKEN set."""
    monkeypatch.setenv("AUTH_TOKEN", "secret-token")
    if "dashboard_server" in sys.modules:
        del sys.modules["dashboard_server"]
    module = importlib.import_module("dashboard_server")
    module.app.config["TESTING"] = True
    yield module
    with module._sse_lock:
        module._sse_clients.clear()


@pytest.fixture
def auth_client(auth_server):
    with auth_server.app.test_client() as c:
        yield c


@pytest.fixture
def sample_push_payload():
    """Realistic payload matching the EA's BuildJSON output shape."""
    return {
        "source": "MT5_EA",
        "updated": "2026-05-04 10:30:00",
        "symbol": "XAUUSD",
        "account_number": 123456,
        "meta": {
            "account_number": 123456,
            "account_name": "Test Trader",
            "server": "MetaQuotes-Demo",
            "currency": "USD",
            "leverage": 100,
            "account_type": "Demo",
            "group": "Personal",
        },
        "account": {
            "balance": 10000.00,
            "equity": 10250.50,
            "open_pnl": 250.50,
            "open_pos": 2,
        },
        "performance": {
            "total_trades": 50,
            "win_trades": 30,
            "loss_trades": 20,
            "win_rate": 60.00,
            "total_pnl": 1500.00,
            "profit_factor": 1.85,
            "avg_win": 75.00,
            "avg_loss": -40.50,
            "best_trade": 350.00,
            "worst_trade": -180.00,
        },
        "risk": {
            "max_drawdown": -250.00,
            "max_drawdown_pct": -2.50,
            "daily_dd_pct": -0.85,
            "avg_rr": 1.85,
        },
    }
