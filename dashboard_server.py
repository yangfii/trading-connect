"""
Gold Performance Dashboard Server  v2.0
=========================================
Yang Fi - Gold Trader | Real-time MT5 Data
------------------------------------------
Receives LIVE data pushed directly from MT5 EA via HTTP POST.
Serves real-time dashboard at http://localhost:5000

DATA FLOW:
  MT5 EA  →  POST /api/push  →  Server memory  →  SSE /api/stream  →  Dashboard

HOW TO START:
  1.  pip install flask
  2.  python dashboard_server.py
  3.  Open browser: http://localhost:5000

MT5 EA SETUP (required once):
  Tools > Options > Expert Advisors
  ✅  Allow WebRequest for listed URL
  ➕  Add: http://127.0.0.1:5000
"""

import os
import json
import time
import glob
import threading
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, send_file, Response, request, abort

app   = Flask(__name__)
PORT  = int(os.environ.get("PORT", 5000))
HOST  = "0.0.0.0"
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")  # if empty, /api/push is open (local dev)

# ─── In-memory data store ────────────────────────────────────────────────────
_lock          = threading.Lock()
_latest        = {}       # most-recent push (any account)
_accounts      = {}       # {account_number: {...snapshot...}}
_account_seen  = {}       # {account_number: datetime of last push}
_push_count    = 0
_last_push     = None     # datetime of last push

# ─── Fallback: scan MT5 file folders ─────────────────────────────────────────
JSON_FILE = "gold_performance.json"

def _mt5_paths():
    appdata = os.environ.get("APPDATA", "")
    base    = Path(appdata) / "MetaQuotes" / "Terminal"
    paths   = [base / "Common" / "Files" / JSON_FILE]
    if base.exists():
        for p in base.glob("*"):
            paths.append(p / "MQL5" / "Files" / JSON_FILE)
    paths.append(Path(__file__).parent / JSON_FILE)
    return paths

def _load_from_file():
    for p in _mt5_paths():
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                d["_source"]   = "file"
                d["_file_path"]= str(p)
                age = round(time.time() - p.stat().st_mtime, 1)
                d["_file_age"] = age
                return d
            except Exception:
                pass
    return None

def _current_data():
    """Return latest pushed data, or fall back to file."""
    with _lock:
        if _latest:
            return dict(_latest)
    return _load_from_file()

# ─── SSE helpers ─────────────────────────────────────────────────────────────
_sse_clients = []
_sse_lock    = threading.Lock()

def _broadcast(data: dict):
    """Push SSE event to all connected dashboard clients."""
    payload = "data: " + json.dumps(data) + "\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.append(payload)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

# ─── Routes ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    p = Path(__file__).parent / "gold_dashboard.html"
    if p.exists():
        return send_file(p)
    abort(404, "gold_dashboard.html not found")


@app.route("/api/push", methods=["POST"])
def api_push():
    """EA sends real MT5 data here every N seconds."""
    global _push_count, _last_push

    if AUTH_TOKEN and request.headers.get("X-Auth-Token", "") != AUTH_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    raw = request.get_data(as_text=True)
    if not raw:
        return jsonify({"ok": False, "error": "empty body"}), 400

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    now = datetime.now()
    data["_source"]     = "mt5_push"
    data["_received_at"]= now.strftime("%Y-%m-%d %H:%M:%S")

    # identify which account this push is from
    acct_no = str(data.get("account_number") or
                  data.get("meta", {}).get("account_number") or
                  "unknown")

    with _lock:
        _latest.update(data)
        # store per-account snapshot
        _accounts[acct_no] = dict(data)
        _accounts[acct_no]["_account_no"] = acct_no
        _account_seen[acct_no] = now
        _push_count += 1
        _last_push   = now

    _broadcast(data)

    return jsonify({"ok": True, "received": now.isoformat()}), 200


@app.route("/api/performance")
def api_performance():
    """REST endpoint — specific account or latest snapshot."""
    acct = request.args.get("account")   # ?account=123456

    if acct:
        with _lock:
            d = _accounts.get(str(acct))
        if d is None:
            d = {"error": f"Account #{acct} not connected. Is that EA running?"}
    else:
        d = _current_data()
        if d is None:
            d = {
                "error": (
                    "No data yet. Make sure:\n"
                    "1. MT5 EA (GoldPerformanceTracker) is attached to XAUUSD chart\n"
                    "2. WebRequest enabled: Tools > Options > Expert Advisors\n"
                    "3. URL added: http://127.0.0.1:5000"
                )
            }

    resp = jsonify(d)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/accounts")
def api_accounts():
    """List all connected MT5 accounts with summary info."""
    now = datetime.now()
    with _lock:
        accts = {}
        for no, snap in _accounts.items():
            meta = snap.get("meta", {})
            acct_data = snap.get("account", {})
            perf = snap.get("performance", {})
            seen = _account_seen.get(no)
            age = (now - seen).total_seconds() if seen else None
            accts[no] = {
                "account_number": no,
                "account_name":   meta.get("account_name", "—"),
                "server":         meta.get("server",       "—"),
                "currency":       meta.get("currency",     "USD"),
                "leverage":       meta.get("leverage",     0),
                "account_type":   meta.get("account_type", "—"),
                "balance":        acct_data.get("balance", 0),
                "equity":         acct_data.get("equity",  0),
                "open_pnl":       acct_data.get("open_pnl",0),
                "open_pos":       acct_data.get("open_pos", 0),
                "total_pnl":      perf.get("total_pnl",    0),
                "win_rate":       perf.get("win_rate",     0),
                "total_trades":   perf.get("total_trades", 0),
                "symbol":         snap.get("symbol",       "XAUUSD"),
                "updated":        snap.get("updated",      "—"),
                "last_seen_sec":  age,
                "last_seen_at":   seen.strftime("%Y-%m-%d %H:%M:%S") if seen else None,
                "stale":          (age is not None and age > 60),
            }

    # Sort: live (recently seen) first, then by last_seen ascending (most recent first)
    sorted_accts = sorted(
        accts.values(),
        key=lambda a: (a["stale"], a["last_seen_sec"] if a["last_seen_sec"] is not None else 1e9)
    )

    resp = jsonify({
        "count":    len(accts),
        "accounts": sorted_accts,
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events — dashboard connects here for instant updates."""
    buf = []
    with _sse_lock:
        _sse_clients.append(buf)

    # Send current snapshot immediately on connect
    snap = _current_data()
    if snap:
        buf.append("data: " + json.dumps(snap) + "\n\n")

    def generate():
        try:
            while True:
                while buf:
                    yield buf.pop(0)
                # heartbeat every 3 s to keep connection alive
                yield ": heartbeat\n\n"
                time.sleep(3)
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if buf in _sse_clients:
                    _sse_clients.remove(buf)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":  "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.route("/api/status")
def api_status():
    """Health-check + diagnostics."""
    with _lock:
        pushes = _push_count
        last   = _last_push.strftime("%Y-%m-%d %H:%M:%S") if _last_push else None
        has_mem= bool(_latest)

    file_found = any(p.exists() for p in _mt5_paths())
    age = None
    for p in _mt5_paths():
        if p.exists():
            age = round(time.time() - p.stat().st_mtime, 1)
            break

    return jsonify({
        "server":       "running",
        "server_time":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ea_connected": has_mem,
        "push_count":   pushes,
        "last_push":    last,
        "file_found":   file_found,
        "file_age_sec": age,
        "sse_clients":  len(_sse_clients),
        "data_source":  "mt5_push" if has_mem else ("file" if file_found else "none"),
    })


# ─── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "="*58)
    print("  GOLD PERFORMANCE DASHBOARD SERVER  v2.0")
    print("  Yang Fi · MT5 Real-time Data")
    print("="*58)
    print(f"\n  Dashboard : http://localhost:{PORT}")
    print(f"  EA push   : http://localhost:{PORT}/api/push")
    print(f"  Status    : http://localhost:{PORT}/api/status")
    print("\n  ─── MT5 Setup (one-time) ───────────────────────")
    print("  Tools > Options > Expert Advisors")
    print("  ✅  Allow WebRequest for listed URL")
    print(f"  ➕  Add:  http://127.0.0.1:{PORT}")
    print("  ────────────────────────────────────────────────")
    print("\n  Waiting for EA data...  Press Ctrl+C to stop.\n")

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
