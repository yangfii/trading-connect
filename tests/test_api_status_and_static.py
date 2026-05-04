"""Tests for /api/status, /, and PWA static asset routes."""
from __future__ import annotations


def test_status_with_no_data(client, monkeypatch, server_module):
    monkeypatch.setattr(server_module, "_mt5_paths", lambda: [])
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["server"] == "running"
    assert body["ea_connected"] is False
    assert body["push_count"] == 0
    assert body["last_push"] is None
    assert body["file_found"] is False
    assert body["data_source"] == "none"
    assert body["sse_clients"] == 0


def test_status_after_push(client, monkeypatch, server_module, sample_push_payload):
    monkeypatch.setattr(server_module, "_mt5_paths", lambda: [])
    client.post("/api/push", json=sample_push_payload)

    resp = client.get("/api/status")
    body = resp.get_json()
    assert body["ea_connected"] is True
    assert body["push_count"] == 1
    assert body["last_push"] is not None
    assert body["data_source"] == "mt5_push"


def test_index_serves_dashboard_when_present(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<html" in resp.data.lower() or b"<!doctype" in resp.data.lower()


def test_manifest_served_with_correct_mimetype(client):
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    assert resp.mimetype == "application/manifest+json"


def test_pwa_icons_served(client):
    for path in ("/icon-192.png", "/icon-512.png", "/icon-512-maskable.png", "/apple-touch-icon.png"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        assert resp.mimetype == "image/png"
