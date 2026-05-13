"""Vercel serverless function: /api/crypto-news

Aggregates crypto news from multiple RSS feeds (CoinDesk, Cointelegraph,
Decrypt, Bitcoin.com) plus CryptoPanic (if CRYPTOPANIC_API_KEY is set),
deduplicates by URL, then uses Claude Haiku 4.5 to:
  1. Translate title + summary to Khmer
  2. Classify sentiment (bullish / bearish / neutral) with a confidence score
  3. Extract relevant tags (BTC, ETH, REGULATION, ...)

In-memory cache with 10-minute TTL per article URL keeps API costs down.
Falls back to English-only output when ANTHROPIC_API_KEY isn't configured.
"""
import hashlib
import json
import os
import re
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler


# ── RSS sources (no API key needed) ─────────────────────────────────────────
_RSS_SOURCES = [
    ("CoinDesk",        "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph",   "https://cointelegraph.com/rss"),
    ("Decrypt",         "https://decrypt.co/feed"),
    ("Bitcoin.com",     "https://news.bitcoin.com/feed/"),
    ("CryptoSlate",     "https://cryptoslate.com/feed/"),
]

_MAX_PER_SOURCE       = 8
_MAX_TOTAL_ARTICLES   = 24
_AI_BATCH_SIZE        = 8
_CACHE_TTL_SECONDS    = 600
_HTTP_TIMEOUT         = 8

# Module-level cache survives across warm invocations on Vercel.
_analysis_cache: dict[str, tuple[float, dict]] = {}


# ── HTML stripping ──────────────────────────────────────────────────────────
_HTML_TAG  = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

def _strip_html(s: str) -> str:
    if not s:
        return ""
    s = _HTML_TAG.sub(" ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">")
           .replace("&quot;", '"').replace("&#039;", "'")
           .replace("&apos;", "'"))
    return _WHITESPACE.sub(" ", s).strip()


# ── RSS parser (stdlib only) ────────────────────────────────────────────────
def _parse_rss_feed(source_name: str, url: str) -> list[dict]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (CryptoNewsAggregator/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = resp.read()
    except Exception:
        return []

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    items = root.findall(".//item")[:_MAX_PER_SOURCE]
    articles = []
    for item in items:
        title = _strip_html(item.findtext("title", "") or "")
        link  = (item.findtext("link", "") or "").strip()
        if not title or not link:
            continue

        desc = _strip_html(item.findtext("description", "") or "")[:600]
        pub  = item.findtext("pubDate", "") or ""
        published_iso = ""
        try:
            published_iso = parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass

        # Try to find a thumbnail across common RSS extensions.
        image_url = ""
        for tag in (
            "{http://search.yahoo.com/mrss/}thumbnail",
            "{http://search.yahoo.com/mrss/}content",
            "enclosure",
        ):
            el = item.find(tag)
            if el is not None:
                image_url = el.get("url") or el.get("href") or ""
                if image_url:
                    break

        articles.append({
            "id":           hashlib.sha256(link.encode()).hexdigest()[:16],
            "title_en":     title,
            "summary_en":   desc,
            "url":          link,
            "source":       source_name,
            "published_at": published_iso,
            "image_url":    image_url,
        })
    return articles


def _fetch_cryptopanic(api_key: str) -> list[dict]:
    """Optional source; activated when CRYPTOPANIC_API_KEY env var is set."""
    try:
        url = (
            "https://cryptopanic.com/api/v1/posts/?"
            + urllib.parse.urlencode({
                "auth_token":   api_key,
                "kind":         "news",
                "public":       "true",
                "filter":       "hot",
            })
        )
        req = urllib.request.Request(url, headers={"User-Agent": "CryptoNewsAggregator/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except Exception:
        return []

    out = []
    for post in payload.get("results", [])[:_MAX_PER_SOURCE]:
        link = post.get("url", "") or post.get("source", {}).get("url", "")
        title = (post.get("title") or "").strip()
        if not link or not title:
            continue
        out.append({
            "id":           hashlib.sha256(link.encode()).hexdigest()[:16],
            "title_en":     title,
            "summary_en":   "",
            "url":          link,
            "source":       (post.get("source") or {}).get("title", "CryptoPanic"),
            "published_at": post.get("published_at", ""),
            "image_url":    "",
        })
    return out


def _fetch_all_articles() -> list[dict]:
    sources = list(_RSS_SOURCES)
    cp_key = os.getenv("CRYPTOPANIC_API_KEY", "").strip()

    all_articles: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_parse_rss_feed, n, u) for n, u in sources]
        if cp_key:
            futures.append(pool.submit(_fetch_cryptopanic, cp_key))

        for fut in as_completed(futures, timeout=_HTTP_TIMEOUT + 4):
            try:
                all_articles.extend(fut.result(timeout=1))
            except Exception:
                continue

    # Dedupe by id (URL hash), keep first.
    seen, deduped = set(), []
    for a in all_articles:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        deduped.append(a)

    deduped.sort(key=lambda a: a.get("published_at") or "", reverse=True)
    return deduped[:_MAX_TOTAL_ARTICLES]


# ── Claude analysis (translation + sentiment + tags) ────────────────────────
_SYSTEM_PROMPT = """You are a Cambodian financial news analyst translating crypto news for Khmer-speaking traders.

For each article, return:
1. title_km   - the headline translated to natural, professional Khmer (preserve crypto names like Bitcoin, BTC, XRP as-is)
2. summary_km - a clear 2-sentence summary in Khmer that captures the key market-moving information
3. sentiment  - one of: "bullish" (positive for prices), "bearish" (negative for prices), "neutral"
4. sentiment_score - integer 0-100 confidence in the sentiment direction (higher = more confident)
5. tags - 2-4 uppercase tags from: BTC, ETH, XRP, SOL, ADA, ALTCOIN, REGULATION, MARKET, ETF, DEFI, NFT, MINING, EXCHANGE, MACRO, ADOPTION, SECURITY

Important:
- Khmer translation must be natural, not literal/word-for-word
- Use common Khmer financial vocabulary (ការវិនិយោគ, តម្លៃ, ទីផ្សារ, ស្ទុះឡើង, ធ្លាក់ចុះ)
- Be objective; don't add hype the original article doesn't contain
- Return strictly valid JSON matching the requested schema"""


def _analyze_with_claude(articles: list[dict]) -> dict[str, dict]:
    """Returns map of article_id -> analysis dict. Empty dict on any failure."""
    if not articles:
        return {}
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {}

    try:
        import anthropic
    except ImportError:
        return {}

    client = anthropic.Anthropic()
    results: dict[str, dict] = {}

    # Batch articles to keep individual calls bounded.
    for i in range(0, len(articles), _AI_BATCH_SIZE):
        batch = articles[i:i + _AI_BATCH_SIZE]
        items_text = "\n\n".join(
            f"[{idx}] id={a['id']}\nTITLE: {a['title_en']}\nDESC: {a['summary_en'][:500]}"
            for idx, a in enumerate(batch)
        )

        schema = {
            "type": "object",
            "properties": {
                "articles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id":              {"type": "string"},
                            "title_km":        {"type": "string"},
                            "summary_km":      {"type": "string"},
                            "sentiment":       {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                            "sentiment_score": {"type": "integer", "minimum": 0, "maximum": 100},
                            "tags":            {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["id", "title_km", "summary_km", "sentiment", "sentiment_score", "tags"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["articles"],
            "additionalProperties": False,
        }

        try:
            response = client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=4096,
                system=[
                    {
                        "type":  "text",
                        "text":  _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{
                    "role": "user",
                    "content": f"Analyze these {len(batch)} crypto news items:\n\n{items_text}",
                }],
                output_config={
                    "format": {"type": "json_schema", "schema": schema}
                },
            )
            text = next((b.text for b in response.content if b.type == "text"), "")
            parsed = json.loads(text)
            for entry in parsed.get("articles", []):
                aid = entry.get("id")
                if aid:
                    results[aid] = entry
        except Exception:
            continue

    return results


# ── Cache helpers ───────────────────────────────────────────────────────────
def _cache_get(article_id: str) -> dict | None:
    entry = _analysis_cache.get(article_id)
    if not entry:
        return None
    ts, analysis = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    return analysis


def _cache_set(article_id: str, analysis: dict) -> None:
    _analysis_cache[article_id] = (time.time(), analysis)
    # Bound cache size.
    if len(_analysis_cache) > 200:
        oldest = sorted(_analysis_cache.items(), key=lambda kv: kv[1][0])[:50]
        for k, _ in oldest:
            _analysis_cache.pop(k, None)


# ── Main pipeline ───────────────────────────────────────────────────────────
def build_payload(limit: int = 20) -> dict:
    limit = max(1, min(limit, _MAX_TOTAL_ARTICLES))
    articles = _fetch_all_articles()[:limit]

    # Partition into cached vs needs-analysis.
    needs_analysis = []
    for a in articles:
        cached = _cache_get(a["id"])
        if cached:
            a.update(cached)
            a["ai_analyzed"] = True
        else:
            needs_analysis.append(a)

    if needs_analysis:
        analyses = _analyze_with_claude(needs_analysis)
        for a in needs_analysis:
            an = analyses.get(a["id"])
            if an:
                a.update({
                    "title_km":        an.get("title_km", ""),
                    "summary_km":      an.get("summary_km", ""),
                    "sentiment":       an.get("sentiment", "neutral"),
                    "sentiment_score": an.get("sentiment_score", 50),
                    "tags":            an.get("tags", []),
                    "ai_analyzed":     True,
                })
                _cache_set(a["id"], {
                    "title_km":        a["title_km"],
                    "summary_km":      a["summary_km"],
                    "sentiment":       a["sentiment"],
                    "sentiment_score": a["sentiment_score"],
                    "tags":            a["tags"],
                })
            else:
                a.setdefault("ai_analyzed", False)
                a.setdefault("sentiment", "neutral")
                a.setdefault("sentiment_score", 50)
                a.setdefault("tags", [])

    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in articles:
        counts[a.get("sentiment", "neutral")] = counts.get(a.get("sentiment", "neutral"), 0) + 1

    return {
        "fetched_at":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "count":       len(articles),
        "ai_enabled":  bool(os.getenv("ANTHROPIC_API_KEY")),
        "sentiment_counts": counts,
        "articles":    articles,
    }


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                limit = int(qs.get("limit", ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            payload = build_payload(limit)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=180")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            err = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self.end_headers()
            self.wfile.write(err)
