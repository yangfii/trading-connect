"""Tests for trade-control command endpoints.

Covers the full Dashboard -> Server -> EA -> Server lifecycle:
  POST /api/command           (dashboard enqueues)
  GET  /api/commands          (EA polls)
  POST /api/command/ack       (EA acknowledges)
  GET  /api/command/history   (dashboard shows history)

All four endpoints require AUTH_TOKEN when set.
"""
from __future__ import annotations


def test_enqueue_command_succeeds(client):
    resp = client.post("/api/command", json={"account": "111", "type": "close_all"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    cmd = body["command"]
    assert cmd["account"] == "111"
    assert cmd["type"] == "close_all"
    assert cmd["status"] == "pending"
    assert cmd["id"]
    assert "issued_at" in cmd


def test_enqueue_rejects_unknown_type(client):
    resp = client.post("/api/command", json={"account": "1", "type": "yolo"})
    assert resp.status_code == 400
    assert "invalid type" in resp.get_json()["error"]


def test_enqueue_rejects_missing_account(client):
    resp = client.post("/api/command", json={"type": "close_all"})
    assert resp.status_code == 400
    assert "account required" in resp.get_json()["error"]


def test_enqueue_rejects_missing_type(client):
    resp = client.post("/api/command", json={"account": "1"})
    assert resp.status_code == 400


def test_enqueue_rejects_empty_body(client):
    resp = client.post("/api/command", data="", content_type="application/json")
    assert resp.status_code == 400
    assert "empty" in resp.get_json()["error"].lower()


def test_enqueue_rejects_malformed_json(client):
    resp = client.post("/api/command", data="not json", content_type="application/json")
    assert resp.status_code == 400


def test_ea_poll_returns_pending_commands(client):
    client.post("/api/command", json={"account": "111", "type": "close_buys"})
    client.post("/api/command", json={"account": "111", "type": "close_sells"})

    resp = client.get("/api/commands?account=111")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 2
    types = [c["type"] for c in body["commands"]]
    assert types == ["close_buys", "close_sells"]


def test_ea_poll_isolates_accounts(client):
    client.post("/api/command", json={"account": "111", "type": "close_all"})
    client.post("/api/command", json={"account": "222", "type": "close_buys"})

    body111 = client.get("/api/commands?account=111").get_json()
    body222 = client.get("/api/commands?account=222").get_json()
    assert body111["count"] == 1
    assert body222["count"] == 1
    assert body111["commands"][0]["type"] == "close_all"
    assert body222["commands"][0]["type"] == "close_buys"


def test_ea_poll_empty_when_no_commands(client):
    resp = client.get("/api/commands?account=999")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 0
    assert body["commands"] == []


def test_ea_poll_requires_account_arg(client):
    resp = client.get("/api/commands")
    assert resp.status_code == 400


def test_ack_marks_done_and_removes_from_pending(client):
    enq = client.post("/api/command", json={"account": "111", "type": "close_all"}).get_json()
    cmd_id = enq["command"]["id"]

    ack = client.post("/api/command/ack", json={"id": cmd_id, "status": "done", "result": "Closed 3 positions"})
    assert ack.status_code == 200
    assert ack.get_json()["command"]["status"] == "done"
    assert ack.get_json()["command"]["result"] == "Closed 3 positions"
    assert "acked_at" in ack.get_json()["command"]

    # Pending should be empty now
    poll = client.get("/api/commands?account=111").get_json()
    assert poll["count"] == 0

    # History should contain the acked command
    hist = client.get("/api/command/history?account=111").get_json()
    assert len(hist["history"]) == 1
    assert hist["history"][0]["id"] == cmd_id
    assert hist["history"][0]["status"] == "done"


def test_ack_failed_status_preserves_message(client):
    enq = client.post("/api/command", json={"account": "111", "type": "close_all"}).get_json()
    cmd_id = enq["command"]["id"]

    ack = client.post("/api/command/ack", json={
        "id": cmd_id, "status": "failed", "result": "no positions to close",
    })
    assert ack.status_code == 200
    assert ack.get_json()["command"]["status"] == "failed"
    assert ack.get_json()["command"]["result"] == "no positions to close"


def test_ack_unknown_id_returns_404(client):
    resp = client.post("/api/command/ack", json={"id": "nope", "status": "done"})
    assert resp.status_code == 404


def test_ack_rejects_invalid_status(client):
    enq = client.post("/api/command", json={"account": "111", "type": "close_all"}).get_json()
    resp = client.post("/api/command/ack", json={"id": enq["command"]["id"], "status": "bogus"})
    assert resp.status_code == 400


def test_history_returns_pending_and_done_separately(client):
    a = client.post("/api/command", json={"account": "111", "type": "close_all"}).get_json()
    b = client.post("/api/command", json={"account": "111", "type": "close_buys"}).get_json()
    client.post("/api/command/ack", json={"id": a["command"]["id"], "status": "done"})

    body = client.get("/api/command/history?account=111").get_json()
    assert len(body["pending"]) == 1
    assert body["pending"][0]["id"] == b["command"]["id"]
    assert len(body["history"]) == 1
    assert body["history"][0]["id"] == a["command"]["id"]


def test_command_endpoint_requires_auth_when_token_set(auth_client):
    resp = auth_client.post("/api/command", json={"account": "111", "type": "close_all"})
    assert resp.status_code == 401


def test_command_endpoint_accepts_correct_auth(auth_client):
    resp = auth_client.post(
        "/api/command",
        json={"account": "111", "type": "close_all"},
        headers={"X-Auth-Token": "secret-token"},
    )
    assert resp.status_code == 200


def test_commands_poll_requires_auth_when_token_set(auth_client):
    resp = auth_client.get("/api/commands?account=111")
    assert resp.status_code == 401


def test_ack_requires_auth_when_token_set(auth_client):
    resp = auth_client.post("/api/command/ack", json={"id": "x", "status": "done"})
    assert resp.status_code == 401


def test_history_requires_auth_when_token_set(auth_client):
    resp = auth_client.get("/api/command/history?account=111")
    assert resp.status_code == 401


def test_full_lifecycle_dashboard_to_ea_to_dashboard(client):
    """End-to-end: dashboard enqueues -> EA polls -> EA acks -> dashboard sees history."""
    # 1. Dashboard issues command
    enq = client.post("/api/command", json={"account": "555", "type": "close_buys"}).get_json()
    cmd_id = enq["command"]["id"]

    # 2. EA polls and sees it
    poll1 = client.get("/api/commands?account=555").get_json()
    assert poll1["count"] == 1
    assert poll1["commands"][0]["id"] == cmd_id

    # 3. EA executes (simulated) and acks
    client.post("/api/command/ack", json={
        "id": cmd_id, "status": "done", "result": "Closed 2 buy positions",
    })

    # 4. EA polls again — queue is empty
    poll2 = client.get("/api/commands?account=555").get_json()
    assert poll2["count"] == 0

    # 5. Dashboard sees the history
    hist = client.get("/api/command/history?account=555").get_json()
    assert hist["history"][0]["status"] == "done"
    assert hist["history"][0]["result"] == "Closed 2 buy positions"


def test_command_state_isolated_between_test_module_reloads(server_module):
    """Sanity: conftest reload should clear the command queues."""
    assert server_module._cmd_pending == {}
    assert server_module._cmd_history == {}
    assert server_module._cmd_by_id == {}
