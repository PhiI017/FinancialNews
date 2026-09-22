"""
pricefinder.py — which free, keyless price routes actually answer from THIS machine.

── WHY THIS EXISTS ───────────────────────────────────────────────────────────────────

Every price route in the alerter is dead on a GitHub-hosted runner, and has been since
the first real run. Measured 2026-09-22 (run 7):

    META  yahoo_http_429 + finnhub_no_key + stooq_unparsed
    CELH  yahoo_http_429_host_throttled + finnhub_no_key + stooq_unparsed
    ...and the same for TTWO, VOO, BTC-USD, SCHG, SPMO.

So the letter has never once carried a price for a holding. It reads well because FRED
still answers for the S&P, oil and the 10-year — which makes the hole easy to miss.

Yahoo rate-limits datacentre IP ranges as a block. Stooq answers 200 with a JavaScript
browser check. Both are deliberate fences around hosted machines, not outages, so waiting
does not fix either.

── WHY A PROBE AND NOT A GUESS ───────────────────────────────────────────────────────

THE CONTAINER THIS WAS WRITTEN IN CANNOT REACH ANY OF THESE HOSTS — its proxy answers 403
to the CONNECT, for finance.yahoo.com and coinbase.com alike. So "unreachable from here"
carries no information about the runner, and picking a replacement source by reasoning
about it would be a guess dressed as a fix. The same discipline as printing the Stooq body
we could not parse: find out where it stops, on the hardware it has to work on.

This module therefore adds NOTHING to the ladder. It reports. `python pricefinder.py`
prints one line per route per symbol, with the state and the number if there is one, and
whatever wins gets promoted into `sources.quote` in a separate commit with a test.

── WHAT IS BEING TESTED ──────────────────────────────────────────────────────────────

Only routes that need no key and no account, since the point is to work without waiting on
a form. A key-based source (Finnhub, free and already wired) remains the better answer if
one is ever set; this is what to do until then, and what to fall back to after.

Crypto is separated from equities on purpose: BTC has several excellent keyless sources
and US equities have almost none, so a single verdict over both would hide which half
works.
"""

import json
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 15
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

EQUITIES = ("META", "VOO", "CELH")
CRYPTO = ("BTC-USD",)


def _fetch(url, data=None, headers=None, method=None):
    """
    (body_bytes, state) — one attempt, no retry, no shared throttle state.

    DELIBERATELY NOT `sources._get`. That one backs off, counts refusals per host and
    short-circuits after two — correct for a production ladder and wrong for a survey,
    where one route's 429 would suppress the next route on the same host and the report
    would blame the wrong thing.
    """
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"),
        headers={"User-Agent": UA, "Accept": "*/*", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read(), "ok"
    except urllib.error.HTTPError as e:
        return None, f"http_{e.code}"
    except urllib.error.URLError as e:
        return None, "timeout" if "timed out" in str(e.reason).lower() else "unreachable"
    except Exception as e:
        return None, type(e).__name__


def _looks_like_a_browser_check(body):
    """Stooq's shape: HTTP 200, HTML, and a demand for JavaScript. Not our parse failure."""
    head = body[:400].lower()
    return b"<html" in head and (b"javascript" in head or b"noscript" in head)


# ── THE ROUTES ────────────────────────────────────────────────────────────────────────
#
# Each returns ({price, prev_close, change_pct, asof, source}, state) or (None, state).
# A route that cannot carry a symbol returns `not_covered`, which is a fact about the
# SOURCE and must never be confused with a fetch that failed.


def yahoo_query2(symbol):
    """The sibling host. query1 is what throttles; the limit may or may not be shared."""
    body, state = _fetch(
        f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?range=7d&interval=1d")
    if state != "ok":
        return None, state
    return _read_yahoo_chart(body, "yahoo_query2")


def yahoo_spark(symbol):
    """A different endpoint family on the throttling host — cheaper, sometimes exempt."""
    body, state = _fetch(
        f"https://query1.finance.yahoo.com/v7/finance/spark"
        f"?symbols={symbol}&range=7d&interval=1d")
    if state != "ok":
        return None, state
    try:
        blob = json.loads(body)
        row = (blob.get("spark", {}).get("result") or [{}])[0]
        closes = (row.get("response") or [{}])[0].get("close") or []
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return None, "empty"
        return _row(symbol, closes[-1], closes[-2], "yahoo_spark"), "ok"
    except Exception:
        return None, "unparsed"


def _read_yahoo_chart(body, label):
    try:
        blob = json.loads(body)
        result = (blob.get("chart", {}).get("result") or [{}])[0]
        closes = [c for c in (result["indicators"]["quote"][0]["close"] or [])
                  if c is not None]
        stamps = result.get("timestamp") or []
        if len(closes) < 2:
            return None, "empty"
        asof = time.strftime("%Y-%m-%d", time.gmtime(stamps[-1])) if stamps else ""
        return _row(result["meta"]["symbol"], closes[-1], closes[-2], label, asof), "ok"
    except Exception:
        return None, "unparsed"


def stooq_light(symbol):
    """
    Stooq's one-line quote path rather than the daily-history path.

    The history path returns a JavaScript browser check from a runner. This is a different
    URL on the same host, which is worth one request to distinguish "the host fences
    datacentres" from "that one path does".
    """
    sym = _stooq_symbol(symbol)
    if not sym:
        return None, "not_covered"
    body, state = _fetch(f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv")
    if state != "ok":
        return None, state
    if _looks_like_a_browser_check(body):
        return None, "browser_check"
    return _read_stooq_csv(body, symbol, "stooq_light")


def stooq_pl(symbol):
    """The Polish host. Same data, and possibly not behind the same fence."""
    sym = _stooq_symbol(symbol)
    if not sym:
        return None, "not_covered"
    body, state = _fetch(f"https://stooq.pl/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv")
    if state != "ok":
        return None, state
    if _looks_like_a_browser_check(body):
        return None, "browser_check"
    return _read_stooq_csv(body, symbol, "stooq_pl")


def _stooq_symbol(symbol):
    if symbol.endswith("-USD"):
        return symbol.replace("-USD", "").lower() + "usd"
    return symbol.lower() + ".us"


def _read_stooq_csv(body, symbol, label):
    try:
        rows = body.decode("utf-8", "replace").strip().splitlines()
        if len(rows) < 2:
            return None, "empty"
        cols = rows[0].lower().split(",")
        vals = rows[1].split(",")
        rec = dict(zip(cols, vals))
        close, open_ = float(rec["close"]), float(rec["open"])
        return _row(symbol, close, open_, label, rec.get("date", "")), "ok"
    except Exception:
        return None, "unparsed"


def stooq_history(symbol):
    """
    THE CONTROL, NOT A CANDIDATE. This is the path the live ladder already uses and which
    already answers 200 with a JavaScript browser check.

    It is probed anyway because the two new Stooq paths came back 404 on every symbol, and
    404 on all four — including a symbol the host certainly carries — reads like OUR wrong
    URL, not the host's answer about the data. Running the known-good path beside them
    settles which: if this one still returns its browser check while the others 404, the
    404s are a bad path of ours and Stooq is no worse than it was.
    """
    sym = _stooq_symbol(symbol)
    if not sym:
        return None, "not_covered"
    body, state = _fetch(f"https://stooq.com/q/d/l/?s={sym}&i=d")
    if state != "ok":
        return None, state
    if _looks_like_a_browser_check(body):
        return None, "browser_check"
    return None, "answered_something_else"


def tradingview(symbol):
    """
    TradingView's screener endpoint. Keyless, POSTs JSON, covers US equities and ETFs.

    IT IS ASKED FOR THREE EXCHANGES AND MUST BE TOLD WHICH ONE ANSWERED. The request names
    NASDAQ:X, NYSE:X and AMEX:X because the listing venue is not known here, and the reply
    is a list. Taking `rows[0]` without reading `rows[0]["s"]` back is how you end up
    pricing a different instrument that happens to share a ticker — the exact shape of
    every silent-wrongness bug in this project. The symbol is checked.

    `update_mode` is carried through because a quote can be delayed, end-of-day or
    streaming, and a stale number that looks live is worse than a missing one.
    """
    if symbol.endswith("-USD"):
        return None, "not_covered"
    payload = json.dumps({
        "symbols": {"tickers": [f"NASDAQ:{symbol}", f"NYSE:{symbol}", f"AMEX:{symbol}"]},
        "columns": ["close", "change", "update_mode", "description"],
    }).encode("utf-8")
    body, state = _fetch("https://scanner.tradingview.com/america/scan",
                         data=payload, headers={"Content-Type": "application/json"})
    if state != "ok":
        return None, state
    try:
        rows = json.loads(body).get("data", [])
    except Exception:
        return None, "unparsed"
    want = symbol.upper()
    for r in rows:
        got = (r.get("s") or "")
        if got.split(":")[-1].upper() != want:
            # NOT AN ERROR AND NOT A HIT. The scanner echoing a ticker we did not ask for
            # is worth knowing about, so it is skipped rather than silently accepted.
            continue
        d = r.get("d") or []
        if not d or d[0] in (None, ""):
            continue
        close, change_pct = float(d[0]), float(d[1] or 0.0)
        prev = close / (1 + change_pct / 100.0) if change_pct else close
        row = _row(symbol, close, prev, "tradingview")
        row["venue"] = got
        row["update_mode"] = d[2] if len(d) > 2 else ""
        return row, "ok"
    return None, "empty" if rows else "no_match"


def coinbase(symbol):
    """
    Crypto, from DAILY CANDLES rather than two spot readings.

    THE FIRST PROBE HAD BOTH EXCHANGES AGREEING ON THE PRICE TO SEVENTY CENTS AND
    DISAGREEING ON THE DAY'S MOVE BY SIX AND A HALF POINTS — Coinbase +5.72%, Kraken
    -0.92%, on the same asset at the same second. Neither was lying. Coinbase was asked
    for spot now against spot at a date, and Kraken reports a 24-HOUR ROLLING open; those
    are different questions and only one of them is "what did it do today".

    That is not a cosmetic difference. `triggers.movers` fires on `change_pct`, so the
    rolling reading would have pushed a 5.7% bitcoin alert to a phone on a day the asset
    had not moved. Both routes read a dated daily candle now, which makes them comparable
    and makes the cross-check mean something.
    """
    if not symbol.endswith("-USD"):
        return None, "not_covered"
    body, state = _fetch(
        f"https://api.exchange.coinbase.com/products/{symbol}/candles?granularity=86400")
    if state != "ok":
        return None, state
    try:
        candles = json.loads(body)          # [time, low, high, open, close, volume], newest first
        if len(candles) < 2:
            return None, "empty"
        close, prev = float(candles[0][4]), float(candles[1][4])
        asof = time.strftime("%Y-%m-%d", time.gmtime(candles[0][0]))
        return _row(symbol, close, prev, "coinbase", asof), "ok"
    except Exception:
        return None, "unparsed"


def kraken(symbol):
    """Crypto, daily candles. A second opinion — one source is not a check on itself."""
    if not symbol.endswith("-USD"):
        return None, "not_covered"
    pair = "XBTUSD" if symbol.startswith("BTC") else symbol.replace("-", "")
    body, state = _fetch(f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval=1440")
    if state != "ok":
        return None, state
    try:
        result = json.loads(body).get("result") or {}
        series = next((v for k, v in result.items() if k != "last"), None)
        if not series or len(series) < 2:
            return None, "empty"
        close, prev = float(series[-1][4]), float(series[-2][4])
        asof = time.strftime("%Y-%m-%d", time.gmtime(series[-1][0]))
        return _row(symbol, close, prev, "kraken", asof), "ok"
    except Exception:
        return None, "unparsed"


def _row(symbol, price, prev, source, asof=""):
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev,
        "change_pct": (price / prev - 1.0) * 100.0 if prev else 0.0,
        "asof": asof or time.strftime("%Y-%m-%d"),
        "source": source,
    }


ROUTES = (
    ("yahoo_query2", yahoo_query2),
    ("yahoo_spark", yahoo_spark),
    ("stooq_light", stooq_light),
    ("stooq_pl", stooq_pl),
    ("stooq_history", stooq_history),
    ("tradingview", tradingview),
    ("coinbase", coinbase),
    ("kraken", kraken),
)


def probe(symbols=None):
    """
    {route: {symbol: (row, state)}} — every route against every symbol, printed.

    NOTHING IS CACHED AND NOTHING IS SHORT-CIRCUITED. A survey that stops at the first
    success cannot tell you what your SECOND source would have been, which is the thing
    the last three days kept needing.
    """
    symbols = symbols or (EQUITIES + CRYPTO)
    out = {}
    print(f"{'route':<14} {'symbol':<9} {'state':<22} value")
    print("-" * 62)
    for name, fn in ROUTES:
        out[name] = {}
        for sym in symbols:
            try:
                row, state = fn(sym)
            except Exception as e:                       # a probe must never crash a run
                row, state = None, f"raised_{type(e).__name__}"
            out[name][sym] = (row, state)
            value = (f"{row['price']:,.2f} ({row['change_pct']:+.2f}%) "
                     f"{row.get('asof', '')} {row.get('venue', '')} "
                     f"{row.get('update_mode', '')}".strip()) if row else ""
            print(f"{name:<14} {sym:<9} {state:<22} {value}")
    print()
    for name, fn in ROUTES:
        got = [s for s, (r, st) in out[name].items() if st == "ok"]
        cov = [s for s, (r, st) in out[name].items() if st != "not_covered"]
        print(f"{name}: {len(got)}/{len(cov)} answered"
              + (f" — {', '.join(got)}" if got else ""))
    return out


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    probe(args or None)
