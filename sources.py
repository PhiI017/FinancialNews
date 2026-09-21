"""
sources.py — every number this alerter sends, from free sources only.

WHAT EACH FUNCTION RETURNS IS A (value, state) PAIR, NEVER A BARE VALUE OR None.

That is the rule the rest of this project learned the expensive way, and an alerter is
where it matters most: a digest that silently omits oil because the fetch failed reads
exactly like a digest where oil did not move. One of those is information and the other
is a lie, and the reader cannot tell them apart. So a failure carries WHERE IT STOPPED —
`http_404`, `timeout`, `empty`, `no_key`, `unparsed` — and the notifier prints it.

`unparsed` specifically means WE could not read a 200 response: our bug, not the host's.

── COSTS ────────────────────────────────────────────────────────────────────────────
Nothing here costs money. Yahoo's chart endpoint needs no key. FRED needs a free key
(fred.stlouisfed.org/docs/api/api_key.html) and is skipped with the state `no_key` when
one is absent, rather than pretending those series did not move.
"""

import json
import os
import time
import urllib.parse
import urllib.request

TIMEOUT = 20
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_NEWS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
FRED_OBS = "https://api.stlouisfed.org/fred/series/observations"


def _get(url, headers=None, retries=2):
    """(body_bytes, state). Retries only what is worth retrying."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    last = "unknown"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read(), "ok"
        except urllib.error.HTTPError as e:
            # A 4xx IS OURS AND A 5xx IS THEIRS, so only one of them is worth a retry.
            if 400 <= e.code < 500:
                return None, f"http_{e.code}"
            last = f"http_{e.code}"
        except urllib.error.URLError as e:
            last = "timeout" if "timed out" in str(e.reason).lower() else "unreachable"
        except Exception as e:
            last = type(e).__name__
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None, last


def quote(symbol, lookback_days=7):
    """
    ({symbol, price, prev_close, change_pct, asof}, state) for one symbol.

    THE PREVIOUS CLOSE COMES FROM THE SERIES, NOT FROM THE `previousClose` FIELD.
    Yahoo's meta block carries `chartPreviousClose`, and for a symbol that trades
    continuously — crypto — it does not mean what it means for a stock. Taking the last
    two distinct daily closes gives one definition that holds for both, and the day's
    move is then the same arithmetic everywhere.
    """
    url = YAHOO_CHART.format(symbol=urllib.parse.quote(symbol))
    url += f"?interval=1d&range={max(lookback_days, 5)}d"
    body, state = _get(url)
    if state != "ok":
        return None, state
    try:
        chart = json.loads(body)["chart"]
        if chart.get("error"):
            return None, "http_error_body"
        result = chart["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        stamps = result["timestamps"] if "timestamps" in result else result["timestamp"]
    except Exception:
        # A 200 WE COULD NOT READ IS OUR BUG. Never `empty`, which would read as
        # "the symbol had no prices" and quietly drop a holding from the digest.
        return None, "unparsed"
    if len(closes) < 2:
        return None, "empty"
    price, prev = float(closes[-1]), float(closes[-2])
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev,
        "change_pct": (price / prev - 1.0) * 100.0 if prev else 0.0,
        "asof": time.strftime("%Y-%m-%d", time.gmtime(stamps[-1])) if stamps else "",
    }, "ok"


def index_history(symbol="^GSPC", years=40):
    """
    ([(date, close)], state) — long daily history, for a REAL all-time high.

    `range=max` is asked for rather than a window, because "the high in the last two
    years" is not an all-time high and the difference is the entire alert. `caller.py`
    also keeps the highest value it has ever seen and never lets a fetch lower it — see
    `state.highest_close`. A truncated response would otherwise quietly shrink the
    drawdown and silence a trigger.
    """
    url = YAHOO_CHART.format(symbol=urllib.parse.quote(symbol))
    url += "?interval=1d&range=max"
    body, state = _get(url)
    if state != "ok":
        return None, state
    try:
        result = json.loads(body)["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception:
        return None, "unparsed"
    rows = [(time.strftime("%Y-%m-%d", time.gmtime(t)), float(c))
            for t, c in zip(stamps, closes) if c is not None]
    if len(rows) < 250:
        # A YEAR OF DAILY CLOSES IS THE FLOOR FOR CALLING ANYTHING AN ALL-TIME HIGH.
        return None, "too_short"
    return rows, "ok"


def fred_series(series_id, api_key=None, last_n=10):
    """([(date, value)], state) — a FRED series. `no_key` is a state, not a silence."""
    api_key = api_key or os.getenv("FRED_API_KEY")
    if not api_key:
        return None, "no_key"
    q = urllib.parse.urlencode({
        "series_id": series_id, "api_key": api_key, "file_type": "json",
        "sort_order": "desc", "limit": last_n,
    })
    body, state = _get(f"{FRED_OBS}?{q}")
    if state != "ok":
        return None, state
    try:
        obs = json.loads(body)["observations"]
    except Exception:
        return None, "unparsed"
    # FRED writes "." for a day a series has no value — a holiday, say. It is not zero,
    # and treating it as one would print a 100% collapse in the oil price.
    rows = [(o["date"], float(o["value"])) for o in obs if o.get("value") not in (".", "", None)]
    rows.reverse()
    return (rows, "ok") if rows else (None, "empty")


def headlines(symbol, limit=12):
    """([{title, link, published}], state) — Yahoo's RSS feed for one symbol."""
    body, state = _get(YAHOO_NEWS.format(symbol=urllib.parse.quote(symbol)))
    if state != "ok":
        return None, state
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)
    except Exception:
        return None, "unparsed"
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title:
            items.append({"title": title,
                          "link": (item.findtext("link") or "").strip(),
                          "published": (item.findtext("pubDate") or "").strip(),
                          "symbol": symbol})
        if len(items) >= limit:
            break
    # EMPTY IS A REAL ANSWER HERE, unlike everywhere else in this file: a quiet ticker
    # genuinely has no headlines today. It is distinguished from `unparsed` above.
    return items, "ok"


def feed(url, label="", limit=8):
    """
    ([{title, link, published, source}], state) — any RSS or Atom feed.

    WHY THIS EXISTS SEPARATELY FROM `headlines`. A per-symbol feed can only ever mention
    a company already on your list, so it is structurally incapable of warning you about
    something arriving from outside it — a Fed decision, an oil shock, a China headline.
    Those move your holdings without ever naming them.

    Atom as well as RSS, because the Federal Reserve publishes Atom and a parser that
    silently returned nothing for it would drop the single most authoritative source
    here while looking like a quiet news day.
    """
    body, state = _get(url)
    if state != "ok":
        return None, state
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(body)
    except Exception:
        return None, "unparsed"

    items, seen = [], set()
    # RSS puts entries in <item>; Atom in <entry> under a namespace. Handle both by tag
    # name rather than by namespace, so a feed changing its namespace does not go quiet.
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title = link = published = ""
        for child in node:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag == "link":
                link = (child.text or child.get("href") or "").strip()
            elif ctag in ("pubDate", "updated", "published") and child.text:
                published = child.text.strip()
        if title and title.lower() not in seen:
            seen.add(title.lower())
            items.append({"title": title, "link": link, "published": published,
                          "symbol": label or "market"})
        if len(items) >= limit:
            break
    return items, "ok"


def dedupe(items):
    """
    One story carried by three outlets is one story.

    NOT A TIDY-UP — IT IS A COST AND A WEIGHTING PROBLEM. Duplicates are paid for twice in
    the prompt, and more importantly they make a single story look like three independent
    ones, which is exactly the signal a reader uses to judge that something is big.
    """
    out, seen = [], set()
    for it in items:
        key = "".join(ch for ch in it["title"].lower() if ch.isalnum())[:70]
        if key and key not in seen:
            seen.add(key)
            out.append(it)
    return out
