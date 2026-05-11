"""Vercel serverless function: /api/news

Returns curated high-impact economic events that move XAUUSD (Gold).
This mirrors the same payload that dashboard_server.py serves on the
self-hosted Flask backend, so the dashboard works identically on both.
"""
import json
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


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


def _weekly_recurring(start_date, days_ahead):
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


def build_payload(days=30, impact_filter="all"):
    days = max(1, min(int(days), 90))
    if impact_filter not in ("all", "high", "medium", "low"):
        impact_filter = "all"

    today  = datetime.utcnow().date()
    cutoff = today + timedelta(days=days)
    merged = list(_ECONOMIC_EVENTS) + _weekly_recurring(today, days)

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

    next_high = next((e for e in upcoming if e.get("impact") == "high"), None)

    return {
        "today":        today.isoformat(),
        "now_utc":      datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "window":       days,
        "impact":       impact_filter,
        "count":        len(upcoming),
        "high_count":   sum(1 for e in upcoming if e.get("impact") == "high"),
        "medium_count": sum(1 for e in upcoming if e.get("impact") == "medium"),
        "events":       upcoming,
        "grouped":      grouped,
        "next_high":    next_high,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        try:
            days = int(qs.get("days", ["30"])[0])
        except (TypeError, ValueError):
            days = 30
        impact = (qs.get("impact", ["all"])[0] or "all").lower().strip()

        body = json.dumps(build_payload(days, impact)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
