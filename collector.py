"""
collector.py — market data for the stocks race, collected where the minutes are free.

── WHY THIS LIVES IN A PUBLIC REPO ───────────────────────────────────────────────────

The race's collector runs in a PRIVATE repository, where every Actions minute is metered.
That quota ran out on 2026-09-17 and every scheduled run since has failed in two to four
seconds with no runner assigned — verified again on 2026-09-22. The paper trading race has
not walked in five days.

Public repositories get unlimited free Actions minutes. That is the whole reason the
alerter lives here, and it is the same reason this does.

WHAT IS PUBLISHED HERE AND WHAT IS NOT. This file fetches PRICES and HEADLINES — public
market data about public companies, from public endpoints, for a list of names anyone can
derive from an index. It carries no model, no strategy, no arm, no position, no holding
and no result. The thing being protected is how those numbers are USED, and none of that
is here or should ever be.

AND IT NEEDS NO CREDENTIALS AT ALL, which is not an accident. Fundamentals would need an
SEC contact address, and a secret in a public repository is a thing to think hard about —
so fundamentals are deliberately NOT collected here. They are quarterly, durable and
backfillable at any time from EDGAR; prices and headlines are the forward-only half, and a
day of headlines nobody collected is gone. The urgent half turns out to be the half that
needs nothing.

── THE OUTPUT IS THE PRIVATE REPO'S OWN CSV SCHEMA, EXACTLY ──────────────────────────

    prices-<year>.csv   ticker,date,open,close,volume,source
    news.csv            published_at,ticker,headline,source,score,scored_at,fetched_at

so `archive.py --load` reads them with no translation step. A translation step is a second
place for a schema to drift, and the drift would be silent: a column in the wrong order
still loads and still looks like data.

MERGED, NEVER OVERWRITTEN, AND SORTED BY THE UNIQUE KEY. `(ticker, date)` for prices and
`(published_at, ticker, headline)` for news — the same keys the private store enforces, so
re-running collects nothing twice and a day's work is a day's worth of diff. Four export
sorts in the sibling project were once not total, which churned thousands of lines of
diff for no change; the sort here is the key and nothing else.
"""

import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import sources

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "marketdata")
UNIVERSE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe.txt")

PRICE_HEADER = ["ticker", "date", "open", "close", "volume", "source"]
DIVIDEND_HEADER = ["ticker", "ex_date", "amount", "source"]
NEWS_HEADER = ["published_at", "ticker", "headline", "source",
               "score", "scored_at", "fetched_at"]

# THE SAME WINDOW THE PRIVATE COLLECTOR USES, AND FOR THE SAME REASON: the source lags
# variably, so yesterday's close is often not there when a run fires. Re-asking for ten
# days costs nothing (one request per ticker either way) and lets a late close land on the
# next run instead of never.
DAILY_WINDOW_DAYS = 10

NASDAQ_HISTORICAL = ("https://api.nasdaq.com/api/quote/{ticker}/historical"
                     "?assetclass={klass}&fromdate={frm}&todate={to}&limit=9999")
NASDAQ_DIVIDENDS = "https://api.nasdaq.com/api/quote/{ticker}/dividends?assetclass={klass}"
# A ticker can be a stock or an ETF and the endpoint needs to be told which. Asking the
# wrong one returns a clean 200 with no rows, which reads exactly like a delisted name —
# so both are tried before a ticker is called empty.
ASSET_CLASSES = ("stocks", "etf")

# Twenty years, asked for in full on every run. Distributions are DURABLE — the 2019 one
# is at the same address today — so there is no window to chase and no run that can lose
# anything. Contrast the news layer, which is forward-only and where a missed day is gone.
DIVIDEND_YEARS = 20


def universe():
    """The names to collect. A plain text file so it is diffable and obviously public."""
    with open(UNIVERSE) as fh:
        return [ln.strip().upper() for ln in fh if ln.strip() and not ln.startswith("#")]


def _f(v):
    if v is None:
        return None
    s = str(v).replace("$", "").replace(",", "").strip()
    if s in ("", "--", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_nasdaq(body):
    """
    [(date, open, close, volume)] — or [] when the reply carries no rows.

    RETURNING [] MUST MEAN "THIS REPLY HAS NO ROWS", never "I did not recognise the
    shape". The caller distinguishes the two by asking the second asset class before
    concluding anything, and by reporting the state rather than the count.
    """
    try:
        rows = json.loads(body)["data"]["tradesTable"]["rows"]
    except (KeyError, TypeError, ValueError):
        return []
    out = []
    for r in rows:
        try:
            day = datetime.strptime(r["date"].strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        close = _f(r.get("close") or r.get("last"))
        if close is None:
            continue
        out.append((day, _f(r.get("open")), close, _f(r.get("volume"))))
    return sorted(out)


def fetch_prices(ticker, days=DAILY_WINDOW_DAYS):
    """([(date, open, close, volume)], state) — both asset classes before giving up."""
    to = datetime.now(timezone.utc).date()
    frm = to - timedelta(days=days)
    states = []
    for klass in ASSET_CLASSES:
        body, state = sources._get(NASDAQ_HISTORICAL.format(
            ticker=ticker, klass=klass, frm=frm, to=to))
        if state != "ok":
            states.append(f"{klass}_{state}")
            continue
        rows = parse_nasdaq(body)
        if rows:
            return rows, "ok"
        states.append(f"{klass}_empty")
    return [], "+".join(states)


# ── WRITING, WHICH IS WHERE A COLLECTOR USUALLY LOSES DATA ──────────────────────────


def _read_csv(path, header):
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as fh:
        rdr = csv.reader(fh)
        got = next(rdr, None)
        if got != header:
            # A HEADER THAT DOES NOT MATCH IS A REFUSAL, NOT SOMETHING TO WORK AROUND.
            # Appending rows under a different column order writes a file that loads
            # cleanly and means something else — the exact failure this project is
            # arranged against.
            raise SystemExit(f"{path}: header is {got}, expected {header}. Refusing to "
                             f"append to a file whose columns are not what this writes.")
        return {tuple(r[:_key_len(header)]): r for r in rdr if r}


# ── THE UNIQUE KEY PER FILE, DECLARED BESIDE THE HEADER RATHER THAN DEFAULTED ──────
#
# `_key_len` used to be `2 if header is PRICE_HEADER else 3`, which is correct for the two
# files that existed and wrong for the first new one that wants a two-column key. Adding
# `dividends.csv` was that file: keyed on three columns it would have deduped on the
# AMOUNT, so a corrected distribution would arrive as a SECOND row for the same ex-date
# and the holder would be credited twice. Nothing would have raised.
#
# Ninth instance of this repo's hand-written-list shape, and the cheapest one to close: an
# unknown header is refused rather than given a plausible default.
KEY_LEN = {
    tuple(PRICE_HEADER): 2,        # ticker, date
    tuple(NEWS_HEADER): 3,         # published_at, ticker, headline
    tuple(DIVIDEND_HEADER): 2,     # ticker, ex_date — NOT the amount
}


def _key_len(header):
    try:
        return KEY_LEN[tuple(header)]
    except KeyError:
        raise SystemExit(
            f"no unique key declared for a file with columns {list(header)}. Add one to "
            f"KEY_LEN — a guessed key silently duplicates rows or silently drops them.")


def _merge(path, header, rows):
    """
    (added, unchanged) — union with whatever is already on disk, sorted by the key.

    FIRST WRITE WINS, which is what makes a re-run free. The private store's `INSERT OR
    IGNORE` behaves the same way, so two machines collecting the same day converge on one
    answer instead of fighting over it.
    """
    existing = _read_csv(path, header)
    added = 0
    for row in rows:
        key = tuple(str(v) for v in row[:_key_len(header)])
        if key not in existing:
            existing[key] = [("" if v is None else str(v)) for v in row]
            added += 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for key in sorted(existing, key=_sort_key(header)):
            w.writerow(existing[key])
    return added, len(existing) - added


def _sort_key(header):
    """
    THE DEDUPE KEY AND THE SORT ORDER ARE NOT THE SAME THING HERE, on purpose.

    Rows are deduped on (ticker, date) because that is what makes a row unique. They are
    WRITTEN in the private repo's own order — `date, ticker` for prices — because these
    files are read there, and a file that arrives in a different order than that repo
    writes churns its entire history on the first export. Matching it now costs nothing;
    matching it after the file has a year in it rewrites the year.

    News already dedupes and sorts on the same tuple, so it needs no swap, and neither
    does `dividends.csv` — (ticker, ex_date) is both its key and the order a reader wants.
    """
    if header is PRICE_HEADER:
        return lambda k: (k[1], k[0])
    return lambda k: k


# ── THE CIRCUIT BREAKER WAS BUILT FOR SEVEN SYMBOLS AND THIS ASKS FOR FIVE HUNDRED ──
#
# `sources._get` stops asking a host after two refusals and returns
# `http_429_host_throttled` immediately from then on. That is exactly right for the
# alerter, which fetches a handful of symbols sixteen times a day: the first 429 already
# tells you what the next six will say.
#
# IT IS EXACTLY WRONG FOR A SWEEP OF 508. Two unlucky refusals early and every remaining
# ticker returns instantly without being asked — a run that finishes fast, reports a
# single repeated state and collects NOTHING, on a day whose prices cannot be re-collected
# later. The breaker would be doing its job and the day would still be gone.
#
# So a throttle here is a reason to WAIT, not to stop: back off, clear the breaker, and
# carry on down the list. `THROTTLE_PAUSES` caps how many times that can happen so a host
# that is genuinely refusing all day cannot turn this into an infinite loop — at which
# point the run gives up and SAYS it gave up, with the count.
THROTTLE_PAUSES = 6
THROTTLE_SLEEP = 30


def _wait_out_throttle(state, pauses):
    """(should_retry, pauses) — a host-level refusal is a pause, not the end of the run."""
    if "throttled" not in (state or "") or pauses >= THROTTLE_PAUSES:
        return False, pauses
    print(f"    host throttled; pausing {THROTTLE_SLEEP}s and continuing "
          f"({pauses + 1}/{THROTTLE_PAUSES})")
    time.sleep(THROTTLE_SLEEP)
    sources.reset_throttles()
    return True, pauses + 1


def collect_prices(tickers=None, days=DAILY_WINDOW_DAYS):
    """Fetch and merge. Returns {state: count} so a bad run says WHERE it stopped."""
    tickers = tickers or universe()
    by_year, states = {}, {}
    pauses = total_added = 0
    for i, t in enumerate(tickers, 1):
        rows, state = fetch_prices(t, days)
        retry, pauses = _wait_out_throttle(state, pauses)
        if retry:
            rows, state = fetch_prices(t, days)
        states[state] = states.get(state, 0) + 1
        if state != "ok":
            print(f"  {t}: {state}")
        else:
            for day, o, c, v in rows:
                by_year.setdefault(day[:4], []).append(
                    [t, day, o, c, int(v) if v is not None else "", "nasdaq"])
        # ── FLUSHED AS IT GOES, BECAUSE A TIMEOUT KILLS THE JOB WHERE IT STANDS ──────
        #
        # The first version held every row in memory and wrote once at the end. A sweep of
        # 508 names takes tens of minutes, `timeout-minutes` terminates the job outright
        # when it expires, and the commit step after it never runs — so a run that had
        # fetched 400 tickers would have committed NONE of them. On prices that is merely
        # annoying, since they backfill; on the news layer beside it, a lost run is a day
        # of headlines that cannot be collected again.
        #
        # Writing every FLUSH_EVERY tickers turns a timeout from total loss into partial
        # progress, and the merge is first-write-wins so the next run simply continues.
        if i % FLUSH_EVERY == 0 or i == len(tickers):
            total_added += _flush_prices(by_year)
            by_year = {}
            print(f"  ...{i}/{len(tickers)}, {total_added} new rows so far")
    print(f"prices: {total_added} new rows; states {states}")
    return states


FLUSH_EVERY = 50


def _flush_prices(by_year):
    added = 0
    for year, rows in sorted(by_year.items()):
        got, _kept = _merge(os.path.join(DATA, f"prices-{year}.csv"), PRICE_HEADER, rows)
        added += got
    return added


def collect_news(tickers=None, limit=6):
    """
    Headlines per company, through the ladder that already works from a hosted runner.

    SEEKING ALPHA FIRST BECAUSE IT IS SCOPED BY THE SYMBOL. Measured on this runner
    2026-09-22: Yahoo's per-symbol feed answers http_429 to a datacentre every time, which
    is what the private collector has been using. So this is not merely a new home for the
    news layer, it is the first one that can actually fetch it from the cloud.
    """
    tickers = tickers or universe()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows, states = [], {}
    pauses = total = 0
    for i, t in enumerate(tickers, 1):
        items, state = sources.headlines(t, limit=limit)
        retry, pauses = _wait_out_throttle(state, pauses)
        if retry:
            items, state = sources.headlines(t, limit=limit)
        states[state if state == "ok" else "failed"] = \
            states.get(state if state == "ok" else "failed", 0) + 1
        if state != "ok":
            print(f"  {t}: {state}")
            continue
        for it in items:
            day = _published_day(it.get("published"))
            if not day:
                continue
            rows.append([day, t, it["title"], f"seekingalpha/{t}", "", "", fetched_at])
        # THE SAME FLUSH, AND IT MATTERS MORE HERE. Prices backfill; a day of headlines
        # nobody wrote down is gone for good.
        if i % FLUSH_EVERY == 0 or i == len(tickers):
            added, _kept = _merge(os.path.join(DATA, "news.csv"), NEWS_HEADER, rows)
            total += added
            rows = []
            print(f"  ...{i}/{len(tickers)}, {total} new headlines so far")
    print(f"news.csv: +{total} new; states {states}")
    return states


def probe_dividends(tickers=None):
    """
    PRINT THE BODY. Fetch nothing else, parse nothing, decide nothing.

    ── WHY A COMMAND EXISTS WHOSE ONLY JOB IS TO SHOW BYTES ─────────────────────────

    The private repo's probe has recorded this route as `unparsed` — 200 OK, nothing
    recognised — since 2026-09-08, and nobody could fix it because nobody could SEE what
    it was being handed: the host is unreachable from the machine the code is written on
    and the body was thrown away the instant the state was printed.

    "Writing a parser for a document nobody has looked at is how this repo produces
    plausible numbers that are wrong." So this looks first. A parser written from a guess
    about this shape would return [] for the real one, and [] here means "this fund paid
    nothing", which is the bias the whole exercise exists to remove.

    BOTH ASSET CLASSES, because asking the wrong one returns a clean 200 with no rows and
    that reads exactly like a fund that has never paid — the same trap `fetch_prices`
    already walks around.
    """
    for t in (tickers or ["VOO", "SPY"]):
        for klass in ASSET_CLASSES:
            url = NASDAQ_DIVIDENDS.format(ticker=t, klass=klass)
            body, state = sources._get(url)
            print(f"\n===== {t} / {klass} -> {state} =====")
            if state != "ok":
                continue
            print(f"  {len(body)} bytes")
            print(body[:2400])
    return {}


def collect_dividends(tickers=None, years=DIVIDEND_YEARS):
    """
    Cash distributions per share, the half of total return an ETF's filings never carry.

    ── THIS IS A BIAS INSIDE A LIVE RACE, NOT A MISSING NICETY ──────────────────────

    The stocks race credits income from EDGAR `us-gaap` facts. A fund files none, so two
    of its arms have been walked on PRICE RETURN while the rest get TOTAL RETURN —
    $2,300,855 of income to the others and zero to those two, measured 2026-09-24.

    ── BACKFILLABLE, SO IT IS COLLECTED DIFFERENTLY TO THE NEWS LAYER ──────────────

    A distribution announced in 2019 is still at the same address today, which is the same
    property that let the congressional fetcher discard its PDFs. So this asks for TWENTY
    YEARS every time and lets the merge drop what it already holds, rather than chasing a
    window like `collect_prices` does. It is also why it does NOT need to run nightly: a
    missed day costs nothing and a missed HEADLINE cannot be recovered at all.

    THE STATES ARE REPORTED PER TICKER AND `no_events` IS NOT ZERO. A fund that pays
    nothing and a response with no dividend block are different claims; collapsing them
    re-creates the exact bias this function removes, one level further down.
    """
    tickers = tickers or universe()
    rows, states = [], {}
    pauses = total = 0
    for i, t in enumerate(tickers, 1):
        got, state = sources.dividend_history(t, years=years)
        retry, pauses = _wait_out_throttle(state, pauses)
        if retry:
            got, state = sources.dividend_history(t, years=years)
        states[state] = states.get(state, 0) + 1
        if state != "ok":
            print(f"  {t}: {state}")
        else:
            for when, amount in got:
                rows.append([t, when, f"{amount:.6f}", "yahoo"])
        if i % FLUSH_EVERY == 0 or i == len(tickers):
            added, _kept = _merge(os.path.join(DATA, "dividends.csv"),
                                  DIVIDEND_HEADER, rows)
            total += added
            rows = []
            print(f"  ...{i}/{len(tickers)}, {total} new payments so far")
    print(f"dividends.csv: +{total} new; states {states}")
    return states


def _published_day(raw):
    """
    'Mon, 22 Sep 2026 14:03:00 GMT' -> '2026-09-22'. None when it cannot be read.

    A HEADLINE WITH NO USABLE DATE IS DROPPED RATHER THAN STAMPED WITH TODAY. Stamping it
    would file an old story as news, and the news layer's whole value is that it is
    forward-only and dated — a wrong date is worse than a missing row.
    """
    if not raw:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return None


if __name__ == "__main__":
    argv = sys.argv[1:]
    names = [a.upper() for a in argv if not a.startswith("--")] or None
    flags = [a for a in argv if a.startswith("--")]
    # NAMES ALONE MEAN BOTH, and a flag means only that half. The first version read
    # "names present" as "do prices", so `--news AAPL` silently collected prices too.
    want_prices = "--prices" in flags or not flags
    want_news = "--news" in flags or not flags
    # DIVIDENDS ARE OPT-IN AND NOT PART OF "no flags". They are backfillable and slow —
    # twenty years for every name — while prices and headlines are the forward-only half
    # that has to run every night. Folding them into the default would turn a 20-minute
    # nightly job into an hour for data that does not expire.
    want_dividends = "--dividends" in flags
    if "--probe-dividends" in flags:
        probe_dividends(names)
        raise SystemExit(0)
    if want_prices:
        collect_prices(names)
    if want_news:
        collect_news(names)
    if want_dividends:
        collect_dividends(names)
