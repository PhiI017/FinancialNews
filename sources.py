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


# ── ONCE A HOST HAS THROTTLED US, IT HAS THROTTLED US ───────────────────────────────
#
# A 429 is a fact about the CLIENT IP, not about the symbol. So the first 429 already
# tells you what the next seven requests will do, and retrying each of them with backoff
# spends two minutes learning the same thing eight times. Measured on the first hosted
# run: every symbol and the index history, all 429, and the second run then sat in
# backoff long enough to be worth fixing before it ever ran on a schedule.
#
# After `THROTTLE_AFTER` refusals from one host, every later call to that host in the same
# process returns `http_429_host_throttled` immediately and the ladder drops to its
# fallback. Per process, not persisted: each run gets a fresh chance, because the limit is
# a rolling window and the next run is minutes later on possibly another machine.
THROTTLE_AFTER = 2
_throttled = {}


def _host_of(url):
    return urllib.parse.urlparse(url).netloc.lower()


def reset_throttles():
    """Forget which hosts are throttled. For tests, and for a long-lived process."""
    _throttled.clear()


def _get(url, headers=None, retries=2):
    """(body_bytes, state). Retries only what is worth retrying."""
    host = _host_of(url)
    if _throttled.get(host, 0) >= THROTTLE_AFTER:
        # NAMED DIFFERENTLY FROM A PLAIN 429 ON PURPOSE. "This host refused us" and "we
        # stopped asking this host" are different facts, and the second one is ours.
        return None, "http_429_host_throttled"
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    last = "unknown"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read(), "ok"
        except urllib.error.HTTPError as e:
            # A 4xx IS OURS AND A 5xx IS THEIRS, so only one of them is worth a retry —
            # WITH ONE EXCEPTION THAT COST A WHOLE RUN. 429 is neither: it means "you are
            # right, just slower". Classifying it with the 4xx family returned instantly
            # and gave up, and on 2026-09-21 that turned every symbol into `http_429` on
            # the first hosted run. Retried with real backoff it usually clears.
            if e.code == 429:
                last = "http_429"
                _throttled[host] = _throttled.get(host, 0) + 1
                if _throttled[host] >= THROTTLE_AFTER:
                    return None, "http_429"
                if attempt < retries:
                    time.sleep(4.0 * (attempt + 1))
                    continue
            elif 400 <= e.code < 500:
                return None, f"http_{e.code}"
            else:
                last = f"http_{e.code}"
        except urllib.error.URLError as e:
            last = "timeout" if "timed out" in str(e.reason).lower() else "unreachable"
        except Exception as e:
            last = type(e).__name__
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    return None, last


# ── A SECOND PRICE SOURCE, BECAUSE THE FIRST ONE THROTTLES DATACENTRES ─────────────
#
# MEASURED ON THE FIRST HOSTED RUN: Yahoo answered 429 to all seven symbols and to the
# index history, from a GitHub runner, in under a second. Hosted runners share well-known
# IP ranges and Yahoo rate-limits them as a block — so this is not a bad minute, it is the
# normal condition of the machine this is designed to run on.
#
# Stooq serves the same daily closes as plain CSV with no key and no session, and it does
# not appear to treat datacentres differently. It is the fallback rather than the primary
# only because Yahoo carries more symbols; when Yahoo answers, nothing changes.
#
# THE LADDER REPORTS WHICH RUNG ANSWERED. "Where did this number come from" is the first
# question about a disagreement, and a silent fallback makes it unanswerable.
STOOQ = "https://stooq.com/q/d/l/?s={symbol}&i=d"

# ── THE THIRD AND FOURTH RUNGS, AFTER BOTH FREE NO-KEY SOURCES REFUSED ─────────────
#
# MEASURED ON A HOSTED RUNNER, 2026-09-21. Yahoo answers 429 to the whole IP range.
# Stooq answers 200 with `<!DOCTYPE html>...This site requires JavaScript to verify your
# browser` — an anti-bot challenge wearing a success code, which is why it arrived as
# `unparsed` rather than as an error. Two different walls, same cause: this runs from a
# datacentre, and free unkeyed price data is exactly what gets fenced off from those.
#
# So the working sources need a key. Both below are free, instant, and take no card.
#
#   FRED      the S&P 500 itself, as series SP500. THIS IS THE IMPORTANT ONE — the dip
#             triggers are the whole point of the system and this alone restores them.
#   FINNHUB   individual equities and ETFs, 60 calls a minute free, which is ten times
#             what seven holdings need.
FINNHUB_QUOTE = "https://finnhub.io/api/v1/quote?symbol={symbol}&token={key}"


def finnhub_quote(symbol, key=None):
    """
    ({...}, state) — one symbol from Finnhub. `no_key` is a state, not a silence.

    NOT USED FOR CRYPTO. Finnhub spells bitcoin as an exchange pair rather than BTC-USD,
    and guessing which exchange would invent a price difference nobody chose. Crypto falls
    through to the rung below rather than being quietly mapped.
    """
    key = key or os.getenv("FINNHUB_API_KEY")
    if not key:
        return None, "no_key"
    if symbol.endswith("-USD"):
        return None, "not_covered"
    body, state = _get(FINNHUB_QUOTE.format(symbol=urllib.parse.quote(symbol), key=key))
    if state != "ok":
        return None, state
    try:
        d = json.loads(body)
        price, prev = float(d["c"]), float(d["pc"])
    except Exception:
        return None, "unparsed"
    if not price or not prev:
        # FINNHUB ANSWERS 200 WITH ZEROES for a symbol it does not carry. A zero price
        # would read as a 100% collapse and fire every alert at once.
        return None, "empty"
    return {"symbol": symbol, "price": price, "prev_close": prev,
            "change_pct": (price / prev - 1.0) * 100.0,
            "asof": time.strftime("%Y-%m-%d"), "source": "finnhub"}, "ok"

STOOQ_SYMBOLS = {
    # Stooq spells US equities with a .us suffix, indices with a caret, crypto plainly.
    "^GSPC": "^spx",
    "BTC-USD": "btcusd",
}


def _stooq_symbol(symbol):
    if symbol in STOOQ_SYMBOLS:
        return STOOQ_SYMBOLS[symbol]
    return symbol.lower() + ".us"


def _stooq_rows(symbol):
    """([(date, close)], state) — daily closes from Stooq's CSV."""
    body, state = _get(STOOQ.format(symbol=urllib.parse.quote(_stooq_symbol(symbol))))
    if state != "ok":
        return None, state
    try:
        lines = body.decode("utf-8", "replace").strip().splitlines()
        header = lines[0].lower().split(",")
        di, ci = header.index("date"), header.index("close")
    except Exception:
        # PRINT WHAT WE COULD NOT READ. `unparsed` means the failure is OURS, and on a
        # hosted runner there is no way to reproduce it by hand — this container cannot
        # reach stooq.com either. Without the body, the next fix is a guess; with it, the
        # log says whether we got an error page, a hit limit, or a shape we misread.
        # Same reason probe.py in the sibling project keeps a body it could not parse.
        head = body[:200].decode("utf-8", "replace").replace("\n", " ")
        print(f"    stooq {symbol}: 200 but unreadable. First 200 bytes: {head!r}")
        return None, "unparsed"
    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        try:
            rows.append((parts[di], float(parts[ci])))
        except (ValueError, IndexError):
            continue          # Stooq writes "N/D" for a day with no print
    if len(rows) < 2:
        # AN EMPTY CSV WITH A HEADER IS HOW STOOQ SAYS "NO SUCH SYMBOL", with a 200.
        return None, "empty"
    return rows, "ok"


def _yahoo_quote(symbol, lookback_days=7):
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
        "source": "yahoo",
    }, "ok"


def _yahoo_index_history(symbol="^GSPC", years=40):
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


def quote(symbol, lookback_days=7):
    """
    ({...,'source'}, state) — Yahoo, then Stooq. The winning rung is in `source`.

    THE FALLBACK IS NOT A NICETY HERE. On a GitHub runner Yahoo answered 429 to every
    symbol at once, which is the normal condition of a shared datacentre IP rather than
    an outage. A single-source alerter on this hardware simply does not work.

    BOTH FAILURE STATES ARE REPORTED, not just the last one, because "Yahoo throttled and
    Stooq does not carry this symbol" and "both timed out" call for different fixes.
    """
    row, state = _yahoo_quote(symbol, lookback_days)
    if state == "ok":
        return row, state

    row, fh_state = finnhub_quote(symbol)
    if fh_state == "ok":
        return row, "ok"

    rows, alt_state = _stooq_rows(symbol)
    if alt_state != "ok":
        return None, f"yahoo_{state}+finnhub_{fh_state}+stooq_{alt_state}"
    price, prev = rows[-1][1], rows[-2][1]
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev,
        "change_pct": (price / prev - 1.0) * 100.0 if prev else 0.0,
        "asof": rows[-1][0],
        "source": "stooq",
    }, "ok"


def index_history(symbol="^GSPC", years=40):
    """
    ([(date, close)], state) — the long history, Yahoo then Stooq.

    Stooq's daily index file runs back decades, which is what the all-time high needs.
    `triggers.ratchet_high` still refuses to lower a stored high, so even if a fallback
    returns a shorter history than the primary once did, the record cannot shrink.
    """
    rows, state = _yahoo_index_history(symbol, years)
    if state == "ok":
        return rows, state

    # FRED BEFORE STOOQ, because it is the one that answers from a datacentre. Its SP500
    # series is the index's own daily close, roughly ten years of it — shorter than
    # Yahoo's forty, which `ratchet_high` makes safe: a stored all-time high can never be
    # lowered by a source that sees less history than the one before it.
    if symbol in ("^GSPC", "^SPX", "SPX"):
        fred_rows, fred_state = fred_series("SP500", last_n=4000)
        if fred_state == "ok" and len(fred_rows) >= 250:
            return fred_rows, "ok"

    alt, alt_state = _stooq_rows(symbol)
    if alt_state != "ok":
        return None, f"yahoo_{state}+stooq_{alt_state}"
    if len(alt) < 250:
        return None, f"yahoo_{state}+stooq_too_short"
    return alt, "ok"
