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
import uuid
import threading
from pathlib import Path
from datetime import datetime, date, timedelta
from collections import deque
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

# ─── Command queue (web → EA) ────────────────────────────────────────────────
# {account_number: deque([{id, action, ticket, queued_at}, ...])}
_commands      = {}
# {command_id: {id, account, action, ticket, ok, message, queued_at, reported_at, status}}
_cmd_results   = {}
# Bound result history per account so memory doesn't grow forever
_RESULT_LIMIT  = 50

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


# ─── PWA assets ──────────────────────────────────────────────────────────────
def _send_static(filename, mimetype):
    p = Path(__file__).parent / filename
    if not p.exists():
        abort(404)
    return send_file(p, mimetype=mimetype)

@app.route("/manifest.json")
def pwa_manifest():
    return _send_static("manifest.json", "application/manifest+json")

@app.route("/icon-192.png")
def pwa_icon_192():
    return _send_static("icon-192.png", "image/png")

@app.route("/icon-512.png")
def pwa_icon_512():
    return _send_static("icon-512.png", "image/png")

@app.route("/icon-512-maskable.png")
def pwa_icon_maskable():
    return _send_static("icon-512-maskable.png", "image/png")

@app.route("/apple-touch-icon.png")
def pwa_apple_icon():
    return _send_static("apple-touch-icon.png", "image/png")


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
                "group":          (meta.get("group") or "").strip(),
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


def _check_auth():
    """Auth check shared by command endpoints. Returns None on OK, or error response."""
    if AUTH_TOKEN and request.headers.get("X-Auth-Token", "") != AUTH_TOKEN:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


@app.route("/api/command", methods=["POST"])
def api_command():
    """Web dashboard queues a trade command for the EA.

    Body: {"account": 12345, "action": "close" | "close_all", "ticket": 67890}
    `ticket` required for "close", ignored for "close_all".
    """
    err = _check_auth()
    if err: return err

    try:
        body = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        return jsonify({"ok": False, "error": f"bad json: {e}"}), 400

    acct   = str(body.get("account") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    ticket = body.get("ticket")

    if not acct:
        return jsonify({"ok": False, "error": "missing 'account'"}), 400
    if action not in ("close", "close_all"):
        return jsonify({"ok": False, "error": f"unsupported action: {action}"}), 400
    if action == "close":
        if ticket is None:
            return jsonify({"ok": False, "error": "missing 'ticket'"}), 400
        try:
            ticket = int(ticket)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "ticket must be integer"}), 400

    cmd_id = uuid.uuid4().hex[:12]
    now    = datetime.now()
    cmd = {
        "id":        cmd_id,
        "action":    action,
        "ticket":    ticket if action == "close" else 0,
        "queued_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    with _lock:
        if acct not in _commands:
            _commands[acct] = deque()
        _commands[acct].append(cmd)
        _cmd_results[cmd_id] = {
            **cmd,
            "account":  acct,
            "status":   "queued",
            "ok":       None,
            "message":  "",
        }

    return jsonify({"ok": True, "id": cmd_id, "queued_at": cmd["queued_at"]}), 200


@app.route("/api/commands")
def api_commands_list():
    """EA polls this endpoint to receive (and drain) pending commands.

    Query: ?account=12345
    Returns: {"commands": [{"id": "...", "action": "close", "ticket": 123}, ...]}
    Commands are removed from the queue once returned.
    """
    err = _check_auth()
    if err: return err

    acct = str(request.args.get("account") or "").strip()
    if not acct:
        return jsonify({"commands": []}), 200

    drained = []
    with _lock:
        q = _commands.get(acct)
        if q:
            while q:
                cmd = q.popleft()
                drained.append(cmd)
                if cmd["id"] in _cmd_results:
                    _cmd_results[cmd["id"]]["status"] = "sent"

    return jsonify({"commands": drained}), 200


@app.route("/api/command_result", methods=["POST"])
def api_command_result():
    """EA reports the outcome of an executed command."""
    err = _check_auth()
    if err: return err

    try:
        body = request.get_json(force=True, silent=False) or {}
    except Exception as e:
        return jsonify({"ok": False, "error": f"bad json: {e}"}), 400

    cmd_id  = str(body.get("id") or "").strip()
    if not cmd_id:
        return jsonify({"ok": False, "error": "missing 'id'"}), 400

    now = datetime.now()
    with _lock:
        rec = _cmd_results.get(cmd_id)
        if rec is None:
            # Unknown id — still record it so the dashboard can see something
            rec = {
                "id":      cmd_id,
                "account": str(body.get("account") or ""),
                "action":  str(body.get("action") or ""),
                "ticket":  body.get("ticket") or 0,
                "queued_at": "",
            }
            _cmd_results[cmd_id] = rec
        rec["ok"]          = bool(body.get("ok"))
        rec["message"]     = str(body.get("message") or "")
        rec["status"]      = "ok" if rec["ok"] else "failed"
        rec["reported_at"] = now.strftime("%Y-%m-%d %H:%M:%S")

        # Trim result history per account to keep memory bounded
        acct = rec.get("account", "")
        if acct:
            ids = [k for k, v in _cmd_results.items() if v.get("account") == acct]
            ids.sort(key=lambda k: _cmd_results[k].get("reported_at") or _cmd_results[k].get("queued_at") or "")
            while len(ids) > _RESULT_LIMIT:
                _cmd_results.pop(ids.pop(0), None)

    # Push to SSE clients so dashboard sees toast immediately
    _broadcast({"_event": "command_result", "result": dict(rec)})

    return jsonify({"ok": True}), 200


@app.route("/api/command_status")
def api_command_status():
    """Web dashboard polls this for outcome of a queued command.

    Query: ?id=<cmd_id>   → single result
           ?account=12345 → recent results for that account
    """
    cmd_id = request.args.get("id")
    acct   = request.args.get("account")

    with _lock:
        if cmd_id:
            rec = _cmd_results.get(cmd_id)
            if rec is None:
                return jsonify({"error": "unknown id"}), 404
            return jsonify(dict(rec)), 200

        if acct:
            recs = [dict(v) for v in _cmd_results.values() if v.get("account") == str(acct)]
            recs.sort(key=lambda r: r.get("queued_at") or "", reverse=True)
            return jsonify({"results": recs[:_RESULT_LIMIT]}), 200

        return jsonify({"error": "specify id= or account="}), 400


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


# ─── Economic Calendar (High-Impact News for XAUUSD) ─────────────────────────
# Curated USD events that historically move Gold. Times are UTC.
# Schedule sourced from BLS, BEA, Federal Reserve, and ISM release calendars.
_ECONOMIC_EVENTS = [
    # ── May 2026 ──
    {"date": "2026-05-01", "time": "12:30", "country": "USD", "event": "Non-Farm Payrolls (NFP)",    "impact": "high",   "category": "Employment"},
    {"date": "2026-05-01", "time": "12:30", "country": "USD", "event": "Unemployment Rate",          "impact": "high",   "category": "Employment"},
    {"date": "2026-05-01", "time": "12:30", "country": "USD", "event": "Average Hourly Earnings m/m","impact": "medium", "category": "Employment"},
    {"date": "2026-05-01", "time": "14:00", "country": "USD", "event": "ISM Manufacturing PMI",      "impact": "high",   "category": "Business"},
    {"date": "2026-05-12", "time": "12:30", "country": "USD", "event": "CPI m/m",                    "impact": "high",   "category": "Inflation"},
    {"date": "2026-05-12", "time": "12:30", "country": "USD", "event": "Core CPI m/m",               "impact": "high",   "category": "Inflation"},
    {"date": "2026-05-13", "time": "12:30", "country": "USD", "event": "PPI m/m",                    "impact": "medium", "category": "Inflation"},
    {"date": "2026-05-15", "time": "12:30", "country": "USD", "event": "Retail Sales m/m",           "impact": "high",   "category": "Consumer"},
    {"date": "2026-05-15", "time": "12:30", "country": "USD", "event": "Core Retail Sales m/m",      "impact": "high",   "category": "Consumer"},
    {"date": "2026-05-21", "time": "14:00", "country": "USD", "event": "Existing Home Sales",        "impact": "medium", "category": "Housing"},
    {"date": "2026-05-28", "time": "12:30", "country": "USD", "event": "Prelim GDP q/q",             "impact": "high",   "category": "Growth"},
    {"date": "2026-05-29", "time": "12:30", "country": "USD", "event": "Core PCE Price Index m/m",   "impact": "high",   "category": "Inflation"},
    {"date": "2026-05-29", "time": "12:30", "country": "USD", "event": "Personal Income m/m",        "impact": "medium", "category": "Consumer"},

    # ── June 2026 ──
    {"date": "2026-06-01", "time": "14:00", "country": "USD", "event": "ISM Manufacturing PMI",      "impact": "high",   "category": "Business"},
    {"date": "2026-06-03", "time": "14:00", "country": "USD", "event": "ISM Services PMI",           "impact": "high",   "category": "Business"},
    {"date": "2026-06-03", "time": "12:15", "country": "USD", "event": "ADP Non-Farm Employment",    "impact": "medium", "category": "Employment"},
    {"date": "2026-06-05", "time": "12:30", "country": "USD", "event": "Non-Farm Payrolls (NFP)",    "impact": "high",   "category": "Employment"},
    {"date": "2026-06-05", "time": "12:30", "country": "USD", "event": "Unemployment Rate",          "impact": "high",   "category": "Employment"},
    {"date": "2026-06-10", "time": "12:30", "country": "USD", "event": "CPI m/m",                    "impact": "high",   "category": "Inflation"},
    {"date": "2026-06-10", "time": "12:30", "country": "USD", "event": "Core CPI m/m",               "impact": "high",   "category": "Inflation"},
    {"date": "2026-06-11", "time": "12:30", "country": "USD", "event": "PPI m/m",                    "impact": "medium", "category": "Inflation"},
    {"date": "2026-06-16", "time": "12:30", "country": "USD", "event": "Retail Sales m/m",           "impact": "high",   "category": "Consumer"},
    {"date": "2026-06-17", "time": "18:00", "country": "USD", "event": "FOMC Rate Decision",         "impact": "high",   "category": "Central Bank"},
    {"date": "2026-06-17", "time": "18:00", "country": "USD", "event": "FOMC Economic Projections",  "impact": "high",   "category": "Central Bank"},
    {"date": "2026-06-17", "time": "18:30", "country": "USD", "event": "FOMC Press Conference",      "impact": "high",   "category": "Central Bank"},
    {"date": "2026-06-26", "time": "12:30", "country": "USD", "event": "Core PCE Price Index m/m",   "impact": "high",   "category": "Inflation"},

    # ── July 2026 ──
    {"date": "2026-07-01", "time": "14:00", "country": "USD", "event": "ISM Manufacturing PMI",      "impact": "high",   "category": "Business"},
    {"date": "2026-07-03", "time": "12:30", "country": "USD", "event": "Non-Farm Payrolls (NFP)",    "impact": "high",   "category": "Employment"},
    {"date": "2026-07-03", "time": "12:30", "country": "USD", "event": "Unemployment Rate",          "impact": "high",   "category": "Employment"},
    {"date": "2026-07-14", "time": "12:30", "country": "USD", "event": "CPI m/m",                    "impact": "high",   "category": "Inflation"},
    {"date": "2026-07-15", "time": "12:30", "country": "USD", "event": "PPI m/m",                    "impact": "medium", "category": "Inflation"},
    {"date": "2026-07-16", "time": "12:30", "country": "USD", "event": "Retail Sales m/m",           "impact": "high",   "category": "Consumer"},
    {"date": "2026-07-29", "time": "18:00", "country": "USD", "event": "FOMC Rate Decision",         "impact": "high",   "category": "Central Bank"},
    {"date": "2026-07-29", "time": "18:30", "country": "USD", "event": "FOMC Press Conference",      "impact": "high",   "category": "Central Bank"},
    {"date": "2026-07-30", "time": "12:30", "country": "USD", "event": "Advance GDP q/q",            "impact": "high",   "category": "Growth"},
    {"date": "2026-07-31", "time": "12:30", "country": "USD", "event": "Core PCE Price Index m/m",   "impact": "high",   "category": "Inflation"},

    # ── August 2026 ──
    {"date": "2026-08-03", "time": "14:00", "country": "USD", "event": "ISM Manufacturing PMI",      "impact": "high",   "category": "Business"},
    {"date": "2026-08-07", "time": "12:30", "country": "USD", "event": "Non-Farm Payrolls (NFP)",    "impact": "high",   "category": "Employment"},
    {"date": "2026-08-07", "time": "12:30", "country": "USD", "event": "Unemployment Rate",          "impact": "high",   "category": "Employment"},
    {"date": "2026-08-12", "time": "12:30", "country": "USD", "event": "CPI m/m",                    "impact": "high",   "category": "Inflation"},
    {"date": "2026-08-13", "time": "12:30", "country": "USD", "event": "PPI m/m",                    "impact": "medium", "category": "Inflation"},
    {"date": "2026-08-14", "time": "12:30", "country": "USD", "event": "Retail Sales m/m",           "impact": "high",   "category": "Consumer"},
    {"date": "2026-08-21", "time": "13:00", "country": "USD", "event": "Jackson Hole Symposium",     "impact": "high",   "category": "Central Bank"},
    {"date": "2026-08-28", "time": "12:30", "country": "USD", "event": "Core PCE Price Index m/m",   "impact": "high",   "category": "Inflation"},
]


def _weekly_recurring_events(start_date, days_ahead):
    """Weekly Initial Jobless Claims (Thursday 12:30 UTC) — moderate Gold mover."""
    events = []
    for i in range(days_ahead + 1):
        d = start_date + timedelta(days=i)
        if d.weekday() == 3:  # Thursday
            events.append({
                "date":     d.isoformat(),
                "time":     "12:30",
                "country":  "USD",
                "event":    "Initial Jobless Claims",
                "impact":   "medium",
                "category": "Employment",
            })
    return events


@app.route("/api/news")
def api_news():
    """Economic calendar — high-impact news affecting Gold (XAUUSD).

    Query params:
      ?days=N      window in days (default 30, max 90)
      ?impact=X    filter: high|medium|low|all (default all)
    """
    try:
        days = int(request.args.get("days", "30"))
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 90))

    impact_filter = (request.args.get("impact") or "all").lower().strip()
    if impact_filter not in ("all", "high", "medium", "low"):
        impact_filter = "all"

    today  = datetime.utcnow().date()
    cutoff = today + timedelta(days=days)

    merged = list(_ECONOMIC_EVENTS) + _weekly_recurring_events(today, days)

    upcoming = []
    for ev in merged:
        try:
            ev_date = date.fromisoformat(ev["date"])
        except (KeyError, ValueError):
            continue
        if not (today <= ev_date <= cutoff):
            continue
        if impact_filter != "all" and ev.get("impact") != impact_filter:
            continue
        ev = dict(ev)
        ev["days_until"] = (ev_date - today).days
        upcoming.append(ev)

    upcoming.sort(key=lambda e: (e["date"], e.get("time", "00:00")))

    grouped = {}
    for ev in upcoming:
        grouped.setdefault(ev["date"], []).append(ev)

    # Find next high-impact event for "trade with caution" alert
    next_high = next((e for e in upcoming if e.get("impact") == "high"), None)

    resp = jsonify({
        "today":     today.isoformat(),
        "now_utc":   datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "window":    days,
        "impact":    impact_filter,
        "count":     len(upcoming),
        "high_count":   sum(1 for e in upcoming if e.get("impact") == "high"),
        "medium_count": sum(1 for e in upcoming if e.get("impact") == "medium"),
        "events":    upcoming,
        "grouped":   grouped,
        "next_high": next_high,
    })
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "public, max-age=300"
    return resp


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
