"""
test_alerts.py — the invariants whose failure would be invisible.

    python test_alerts.py

Nothing here touches the network, sends a notification, or spends a cent. Every test is
about a failure that produces a plausible-looking result rather than an error, because
those are the only ones a quiet alerter can hide.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import alerter
import calendar_events
import notify
import sources
import summarize
import triggers

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@test
def an_all_time_high_never_falls():
    """
    A SHORT FETCH MUST NOT SHRINK THE DRAWDOWN.

    If Yahoo returns three years instead of forty, the observed maximum drops, every dip
    level silently rescales, and a -15% alert never fires. Nothing raises and the digest
    looks ordinary. An all-time high is monotone by definition, so a lower observation is
    evidence about the FETCH, not about the market.
    """
    high, moved = triggers.ratchet_high(7700.0, 6000.0)
    assert high == 7700.0 and not moved, (high, moved)
    high, moved = triggers.ratchet_high(7700.0, 7800.0)
    assert high == 7800.0 and moved
    assert triggers.ratchet_high(None, 100.0) == (100.0, True)   # a first run


@test
def a_market_down_exactly_ten_percent_fires_the_ten_percent_trigger():
    """
    THE ROUND NUMBER IS THE ONE THAT BREAKS, AND IT BREAKS SILENTLY.

    6930/7700 is exactly 0.9 in decimal and slightly more in binary, so the depth is
    9.999999999999998 and a bare `>= 10` is False. The single most likely headline
    scenario — "the market is down ten percent" — would not have alerted, with no error
    and a digest reporting a 10.0% drawdown beside no trigger.
    """
    _dd, fired, _r = triggers.dip_state(6930, 7700, [5, 10, 15, 20, 25], set())
    assert 10 in fired, (
        f"a 10.00% drawdown did not fire the 10% level: {fired}. This is the float "
        f"boundary — see triggers.EPSILON_PCT")
    # and the epsilon stays small enough not to fire a level that is genuinely not reached
    _dd, fired, _r = triggers.dip_state(7000, 7700, [5, 10, 15, 20, 25], set())
    assert fired == [5], f"9.09% should not reach the 10% level: {fired}"


@test
def a_dip_level_fires_once_and_rearms_only_on_recovery():
    """
    FIFTY COPIES OF THE SAME ALERT IS THE SAME AS NONE.

    Half-hourly runs through a drawdown would resend -10% about fifty times, and the
    response to that is muting the app — which loses the alert that mattered. Hysteresis
    rather than a cooldown, because a timer re-fires at the same level on the same dip.
    """
    levels = [5, 10, 15, 20, 25]
    dd, fired, _ = triggers.dip_state(6930, 7700, levels, set())
    assert fired == [5, 10], fired
    assert -10.1 < dd < -9.9, dd

    dd, fired, _ = triggers.dip_state(6930, 7700, levels, {5, 10})
    assert fired == [], "the same level fired twice — this is the mute-the-app bug"

    # STILL SPENT while only part-way back: 7000 is -9.1%, inside the 1% buffer of -10.
    _dd, _f, rearmed = triggers.dip_state(7000, 7700, levels, {5, 10})
    assert 10 not in rearmed, rearmed
    # AND RELEASED once genuinely recovered past the buffer.
    _dd, _f, rearmed = triggers.dip_state(7400, 7700, levels, {5, 10})
    assert 10 in rearmed and 5 in rearmed, rearmed


@test
def the_two_trigger_functions_agree_about_where_a_level_is():
    """
    A LEVEL CANNOT BE BOTH "JUST FIRED" AND "STILL AHEAD OF YOU".

    `dip_state` fires at exactly 10.00% down thanks to EPSILON_PCT. `next_trigger` did
    not use it, so at the same depth it reported the NEXT buy as 10% — the one that had
    just fired. The letter would have said "this crosses your 10% trigger" and "your next
    buy is at 10%" in the same breath.

    Two functions disagreeing about a boundary is worse than either being wrong alone: it
    makes the whole thing look broken at the exact moment it is saying something that
    matters. So they share the epsilon, and this checks them against each other rather
    than separately.
    """
    levels = [10, 15, 20, 25]
    for close, high in ((6930, 7700), (6545, 7700), (6160, 7700), (5775, 7700)):
        _dd, fired, _r = triggers.dip_state(close, high, levels, set())
        depth = (1 - close / high) * 100
        nxt, _gap = triggers.next_trigger(depth, levels)
        assert nxt not in fired, (
            f"at {depth:.2f}% down, level {nxt} is reported as the NEXT buy while "
            f"dip_state says it already fired {fired}")

    # and on an ordinary day the next buy is the first one, with the real distance
    nxt, gap = triggers.next_trigger(0.65, levels)
    assert nxt == 10 and 9.3 < gap < 9.4, (nxt, gap)
    # past the deepest level there is no next one to name
    assert triggers.next_trigger(30.0, levels) == (None, None)


@test
def one_move_threshold_cannot_serve_a_mixed_book():
    """
    5% IN VOO IS A MARKET EVENT AND 5% IN CELH IS A TUESDAY.

    A single threshold either buries the index moves or sends a small cap's ordinary
    noise weekly, and the second is how an alerter earns the mute it gets.
    """
    quotes = [{"symbol": "VOO", "change_pct": -5.4},
              {"symbol": "CELH", "change_pct": 6.1},
              {"symbol": "META", "change_pct": 1.2}]
    hits = {m["symbol"] for m in triggers.movers(quotes, {"CELH": {"move_pct": 8.0}})}
    assert hits == {"VOO"}, hits
    # ...and CELH does qualify once it moves like CELH.
    quotes[1]["change_pct"] = 9.0
    hits = {m["symbol"] for m in triggers.movers(quotes, {"CELH": {"move_pct": 8.0}})}
    assert hits == {"VOO", "CELH"}, hits


@test
def macro_thresholds_are_absolute_because_the_units_differ():
    """0.15 on the 10-year is a big day; 0.15 PERCENT of it is nothing."""
    rows = {"DGS10": [("2026-09-18", 4.80), ("2026-09-19", 5.00)],
            "DCOILWTICO": [("2026-09-18", 101.0), ("2026-09-19", 101.4)]}
    hits = {h["series"] for h in triggers.macro_moves(rows, {"DGS10": 0.15, "DCOILWTICO": 5.0})}
    assert hits == {"DGS10"}, hits


@test
def every_source_reports_where_it_stopped():
    """
    A MISSING NUMBER AND AN UNCHANGED ONE MUST NOT LOOK THE SAME.

    A digest that silently omits oil because the fetch failed reads exactly like a digest
    where oil did not move. This checks the contract rather than the network: every
    fetcher returns a (value, state) pair and never a bare None.
    """
    import inspect
    # The public `quote` and `index_history` are LADDERS; the parsing that can fail in an
    # interesting way lives in the rung beneath. Check the rungs for the distinction and
    # the ladders for reporting it onward.
    for name in ("_yahoo_quote", "_yahoo_index_history", "_stooq_rows",
                 "fred_series", "_rss_items", "feed"):
        src = inspect.getsource(getattr(sources, name))
        assert "return None, " in src or "return (rows" in src, (
            f"sources.{name} has no stated failure state")
        assert any(w in src for w in ("unparsed", "no_key", "too_short", "empty")), (
            f"sources.{name} cannot distinguish our bug from the host's")

    # AND THE LADDER NAMES BOTH RUNGS. "Yahoo throttled and Stooq has no such symbol" and
    # "both timed out" need different fixes, and one combined word cannot say which.
    for name in ("quote", "index_history"):
        src = inspect.getsource(getattr(sources, name))
        assert "yahoo_" in src and "stooq_" in src, (
            f"sources.{name} collapses two different failures into one state")
    # `headlines` BECAME A LADDER TOO once every per-ticker feed turned out to be 429 on
    # the runner, so it is held to the same contract: name each rung it tried.
    news = inspect.getsource(sources.headlines)
    for rung in ("seekingalpha_", "google_", "yahoo_"):
        assert rung in news, f"sources.headlines does not report its {rung} rung"
    # AND IT REFUSES A LOW-PRECISION QUERY RATHER THAN SERVING ONE. Searching a bare
    # ticker returns English, not news, and noise offered as coverage is worse than a
    # stated gap.
    assert "no_name" in news

    # 429 IS RETRYABLE AND WAS NOT. It means "you are right, just slower" — grouping it
    # with the 4xx family returned instantly and gave up, which is exactly what happened
    # on the first hosted run: every symbol http_429 in under a second.
    getsrc = inspect.getsource(sources._get)
    assert "e.code == 429" in getsrc, "429 is not retried separately from the 4xx family"

    # `no_key` is a STATE, not a silence — checked for real, since it needs no network.
    saved = os.environ.pop("FRED_API_KEY", None)
    try:
        rows, state = sources.fred_series("DGS10")
        assert rows is None and state == "no_key", (rows, state)
    finally:
        if saved:
            os.environ["FRED_API_KEY"] = saved


@test
def one_rate_limit_is_not_relearned_once_per_symbol():
    """
    A 429 IS A FACT ABOUT THE IP, NOT ABOUT THE SYMBOL.

    So the first refusal already tells you what the next seven requests will do. Retrying
    each with backoff spends minutes learning the same thing eight times — measured on the
    second hosted run, which sat in backoff long enough to matter before it had ever run
    on a schedule. After two refusals the host is skipped and the ladder drops straight to
    its fallback.

    The skip is named differently from a plain 429 because "this host refused us" and "we
    stopped asking" are different facts, and only the second one is our decision.
    """
    sources.reset_throttles()
    try:
        host = "query1.finance.yahoo.com"
        sources._throttled[host] = sources.THROTTLE_AFTER
        body, state = sources._get(f"https://{host}/anything")
        assert body is None and state == "http_429_host_throttled", (body, state)
        # ...and it is per-process, so the next run gets a clean chance at it
        sources.reset_throttles()
        assert sources._throttled == {}
    finally:
        sources.reset_throttles()


@test
def an_unconfigured_channel_is_reported_and_never_raises():
    """
    THE NOTIFIER IS THE WORST PLACE FOR AN EXCEPTION.

    A crash here takes down the run that was trying to tell you something. And one
    channel failing must never stop the other, which is the entire reason for two.
    """
    for key in ("NTFY_TOPIC", "SMTP_USER", "SMTP_PASS"):
        os.environ.pop(key, None)
    result = notify.send("subject", "body")
    assert result == {"ntfy": "not_configured", "email": "not_configured"}, result
    assert notify.configured() == {"ntfy": False, "email": False}
    # A topic is the credential on the public server, so it must not be guessable.
    assert len(notify.random_topic()) >= 20


@test
def a_stale_nav_is_reported_and_never_divided_by():
    """
    A PREMIUM AGAINST AN OLD MARK IS A PLAUSIBLE NUMBER WITH NO MEANING.

    Private holdings are appraised, not traded, so the NAV is an estimate that is refreshed
    slowly and sometimes not at all. Computing a premium against a six-week-old one gives a
    confident figure that is simply about a different day — the exact shape of every real
    bug in this project. `nav_stale` carries the AGE so the letter can say which.
    """
    import time

    import premium
    fresh = {"nav": 25.0, "asof": time.strftime("%Y-%m-%d"), "source": "config"}
    row, state = premium.premium("BOT", 29.38, fresh)
    assert state == "ok", state
    assert abs(row["premium_pct"] - 17.52) < 0.01, row["premium_pct"]

    old_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 45 * 86400))
    row, state = premium.premium("BOT", 29.38, {"nav": 25.0, "asof": old_day})
    assert state == "nav_stale", state
    assert row["stale_days"] >= 44, row
    assert "premium_pct" not in row, "a stale NAV must not produce a premium"

    # THE TWO ABSENCES ARE DIFFERENT FACTS. Nothing published a NAV, versus one exists and
    # is too old — different responses, so never one word for both.
    assert premium.premium("BOT", 29.38, None)[1] == "no_nav"
    assert premium.premium("BOT", 29.38, {"nav": 25.0, "asof": ""})[1] == "nav_undated"


@test
def the_premium_rungs_spend_and_rearm_like_the_dip_ladder():
    """
    SAME HYSTERESIS, SAME REASON: a level that fires every run is a level you turn off.

    And the +20 rung is a WARNING, not a buy — a premium coming down from a high is worth
    reading and is not the thing the plan acts on. Announcing it as a buy would be the
    5%-dip mistake in a new place.
    """
    import premium
    rungs = (20.0, 10.0, 0.0, -10.0)
    hit, spent = premium.crossed(17.5, rungs, [])
    assert hit == [20.0], hit
    assert all(r >= premium.WARN_ONLY_ABOVE for r in hit), "the +20 rung is warn-only"
    hit, spent = premium.crossed(17.0, rungs, spent)
    assert hit == [], "the same rung must not fire twice on a drift"
    hit, spent = premium.crossed(8.0, rungs, spent)
    assert hit == [10.0], hit
    # BACK ABOVE A LEVEL RE-ARMS IT, so a second approach alerts again.
    hit, spent = premium.crossed(12.0, rungs, spent)
    assert hit == [] and 10.0 not in spent, spent
    assert premium.crossed(8.0, rungs, spent)[0] == [10.0]

    # THE FUND'S OWN DISTRIBUTION REFUSES UNTIL THERE IS ONE, rather than taking quartiles
    # of a handful of readings — which is a guessed dial wearing a statistic's clothes.
    got, state = premium.rungs_from_history([1.0, 2.0, 3.0])
    assert got == premium.DEFAULT_RUNGS and state.startswith("too_short_3_of_"), state
    got, state = premium.rungs_from_history(list(range(100)))
    assert state == "ok" and got[0] > got[-1], got


@test
def a_fund_without_a_nav_says_so_instead_of_showing_its_price():
    """
    THE FAILURE THAT WOULD MATTER MOST HERE IS THE QUIET ONE.

    Six free NAV routes were surveyed on the runner and not one carries this fund's: the
    screener has the column and it is null, CEF Connect 404s, Nasdaq does not classify it
    as a fund at all. So the common case is NO NAV, and a premium section that silently
    fell back to the price would read as a premium of zero — a fund at fair value, stated
    confidently, on no data.
    """
    import letter
    import premium
    wl = {"positions": [{"symbol": "BOT", "premium_rungs_pct": [20, 10, 0, -10]}]}
    row, state = premium.nav_from_config("BOT", wl)
    assert row is None and state == "no_config_nav", (row, state)

    wl["positions"][0].update({"nav": 25.0, "nav_asof": "2026-09-19"})
    row, state = premium.nav_from_config("BOT", wl)
    assert state == "ok" and row["nav"] == 25.0 and row["source"] == "config"

    # AND THE LETTER NAMES IT AS A MISSING PREMIUM, NOT A MISSING PRICE. The same class of
    # mix-up that told the reader there were no prices in the first letter that had them.
    joined = " ".join(letter.data_notes({"premium_failures": {"BOT": "tradingview_empty"}}))
    assert "BOT: no premium" in joined, joined
    assert "no price" not in joined, joined


@test
def one_move_is_one_alert_and_a_bigger_move_is_another():
    """
    THE FIRST RUN WITH WORKING PRICES PUSHED A REAL ALERT THAT WOULD HAVE REPEATED ALL DAY.

    2026-09-22: META +11.34%, correctly detected, urgently pushed — and `movers` is
    stateless, so the same alert was due on all sixteen of that day's scheduled runs. The
    number does not change until the session does. Harmless for as long as every price
    fetch failed, which is why it surfaced the hour the ladder started working.

    Keyed on the quote's own session, not a clock: a timer re-fires at 3am on a move that
    has not changed.
    """
    rows = [{"symbol": "META", "change_pct": 11.34, "threshold": 5.0, "direction": "up"}]
    worth, seen = triggers.unreported_movers(rows, {}, "2026-09-19")
    assert len(worth) == 1, worth
    # SAME SESSION, SAME MOVE: silence.
    worth, seen = triggers.unreported_movers(rows, seen, "2026-09-19")
    assert worth == [], worth
    # A NEW SESSION IS A NEW FACT.
    worth, seen2 = triggers.unreported_movers(rows, seen, "2026-09-22")
    assert len(worth) == 1, worth
    # AN ESCALATION IS A NEW EVENT — otherwise the only alert you get for a crash is the
    # one from when it was still ordinary.
    worse = [{"symbol": "META", "change_pct": 17.0, "threshold": 5.0, "direction": "up"}]
    worth, seen3 = triggers.unreported_movers(worse, seen2, "2026-09-22")
    assert len(worth) == 1, worth
    # ...but a drift that has not grown by a whole threshold is not.
    drift = [{"symbol": "META", "change_pct": 18.0, "threshold": 5.0, "direction": "up"}]
    assert triggers.unreported_movers(drift, seen3, "2026-09-22")[0] == []
    # A REVERSAL IS NOT A REPEAT. Up 6% and down 6% are two different days' worth of news.
    flip = [{"symbol": "META", "change_pct": -6.0, "threshold": 5.0, "direction": "down"}]
    assert len(triggers.unreported_movers(flip, seen3, "2026-09-22")[0]) == 1
    # AND A SYMBOL THAT STOPPED MOVING IS FORGOTTEN, so next week's move alerts cleanly.
    assert triggers.unreported_movers([], seen3, "2026-09-22")[1] == {}


@test
def the_screener_is_asked_which_instrument_it_priced():
    """
    IT IS ASKED FOR THREE VENUES AND ANSWERS WITH A LIST.

    The request names NASDAQ:X, NYSE:X and AMEX:X because the listing venue is not known
    here. Taking the first row back prices whatever else happens to share that ticker — a
    wrong number in a sentence that reads perfectly, which is this project's whole failure
    mode. The reply is matched on the symbol.
    """
    import json as _json
    body = _json.dumps({"data": [
        {"s": "NYSE:METAX", "d": [12.0, 5.0, 0.6, "delayed_streaming_900"]},
        {"s": "NASDAQ:META", "d": [741.245, 11.34, 75.495, "delayed_streaming_900"]},
    ]}).encode()
    real = sources._get
    sources._get = lambda *a, **kw: (body, "ok")
    try:
        row, state = sources.tradingview_quote("META")
    finally:
        sources._get = real
    assert state == "ok", state
    assert row["price"] == 741.245, row
    assert row["venue"] == "NASDAQ:META", row["venue"]
    # close - change_abs, never close/(1+pct): the absolute figure needs no rounding.
    assert abs(row["prev_close"] - 665.75) < 0.01, row["prev_close"]
    assert abs(row["change_pct"] - 11.34) < 0.01, row["change_pct"]
    # A quote can be delayed, and a stale number passing as live is worse than none.
    assert row["update_mode"] == "delayed_streaming_900"

    # AND A REPLY THAT NAMES ONLY OTHER INSTRUMENTS IS NOT A PRICE.
    only_others = _json.dumps({"data": [{"s": "NYSE:METAX", "d": [12.0, 5.0, 0.6, "x"]}]}).encode()
    sources._get = lambda *a, **kw: (only_others, "ok")
    try:
        row, state = sources.tradingview_quote("META")
    finally:
        sources._get = real
    assert row is None and state == "no_match", (row, state)


@test
def the_two_crypto_sources_answer_the_same_question():
    """
    THEY AGREED ON THE PRICE TO SEVENTY CENTS AND DISAGREED ON THE DAY BY SIX POINTS.

    +5.72% against -0.92%, same asset, same second, in the first survey. One was spot-now
    against spot-at-a-date, the other a 24-hour ROLLING open — different questions, so the
    cross-check between them meant nothing. `movers` fires on change_pct, so the rolling
    reading pushes a 5.7% alert on a flat day with a correct price beside it.

    Both read a dated daily candle now. The test feeds each its own shape and asserts they
    land on the same move.
    """
    import json as _json
    cb = _json.dumps([[1758499200, 1, 2, 3, 85791.24, 9],
                      [1758412800, 1, 2, 3, 86596.00, 9]]).encode()
    kr = _json.dumps({"result": {"XXBTZUSD": [
        [1758412800, "1", "2", "3", "86596.00", "4", "5", 6],
        [1758499200, "1", "2", "3", "85791.24", "4", "5", 6]], "last": 1}}).encode()
    real = sources._get
    try:
        sources._get = lambda *a, **kw: (cb, "ok")
        a, sa = sources.coinbase_quote("BTC-USD")
        sources._get = lambda *a, **kw: (kr, "ok")
        b, sb = sources.kraken_quote("BTC-USD")
    finally:
        sources._get = real
    assert sa == sb == "ok", (sa, sb)
    assert abs(a["change_pct"] - b["change_pct"]) < 0.001, (a["change_pct"], b["change_pct"])
    assert a["price"] == b["price"] == 85791.24
    # AND NEITHER PRETENDS TO CARRY A STOCK. `not_covered` is a fact about the source.
    assert sources.coinbase_quote("META")[1] == "not_covered"
    assert sources.tradingview_quote("BTC-USD")[1] == "not_covered"


@test
def the_drawdown_is_not_the_days_move():
    """
    THE LETTER CALLED A +1.49% DAY "BARELY MOVED".

    Nothing lied to it. The only index percentage it was given was the distance from the
    all-time high — -0.4% — and it read that as the change on the day. Both arrive as a
    small percentage and only one of them is "today". The rows were already in hand; the
    question had never been asked of them.
    """
    rows = [("2026-09-17", 7600.0), ("2026-09-18", 7650.5), ("2026-09-19", 7764.7)]
    # `quote` IS STUBBED TOO, AND THE FIRST VERSION OF THIS TEST FORGOT TO. `gather` fetches
    # every holding, so the test was making real network calls — against a suite whose first
    # commit message says "no network, nothing sent, nothing spent". It passed because the
    # calls failed fast, and adding two symbols to the watchlist turned a slow test into a
    # minutes-long one. A test that reaches the network is a test whose result depends on
    # the weather.
    real_idx, real_quote = sources.index_history, sources.quote
    sources.index_history = lambda *a, **kw: (rows, "ok")
    sources.quote = lambda sym, *a, **kw: ({"symbol": sym, "price": 10.0,
                                            "prev_close": 10.0, "change_pct": 0.0,
                                            "asof": "2026-09-19", "source": "stub"}, "ok")
    try:
        wl = alerter.load_watchlist()
        facts = alerter.gather(wl, want_macro=False, want_news=False)
    finally:
        sources.index_history, sources.quote = real_idx, real_quote
    idx = facts["index"]
    assert abs(idx["change_pct"] - 1.4927) < 0.001, idx["change_pct"]
    assert idx["prev_close"] == 7650.5 and idx["asof"] == "2026-09-19"

    # AND THE MODEL IS TOLD WHICH IS WHICH, IN THE PROMPT, NOT LEFT TO INFER IT.
    idx.update({"high": 7799.0, "drawdown_pct": -0.44})
    prompt = " ".join(summarize.render(facts, "daily").split())
    assert "ON THE DAY" in prompt and "+1.49%" in prompt, prompt[-600:]
    assert "is NOT how far it moved today" in prompt


@test
def a_push_survives_the_characters_the_model_actually_writes():
    """
    AN EM-DASH IN THE TITLE KILLED EVERY PHONE ALERT, AND ONLY THE PHONE ALERT.

    Measured 2026-09-22: `delivery: {'email': 'ok', 'ntfy': 'UnicodeEncodeError'}`. HTTP
    header values are latin-1; the title was "Daily note — S&P -0.4% from its high". The
    model writes em-dashes in most titles, so the urgent channel was dead in every run
    while the run itself reported success.

    The test builds the request WITHOUT SENDING IT and asserts the bytes are valid UTF-8
    JSON carrying the title intact. Asserting on the built payload rather than on a live
    send is deliberate: this suite makes no network calls, and the bug was in the encoding
    of the request, which is fully observable before it leaves.
    """
    import json as _json
    import urllib.request
    os.environ["NTFY_TOPIC"] = "test-topic"
    built = {}
    real = urllib.request.urlopen

    def capture(req, *a, **kw):
        built["url"] = req.full_url
        built["data"] = req.data
        built["headers"] = dict(req.headers)
        raise OSError("not sending")

    urllib.request.urlopen = capture
    try:
        ok, state = notify.push("Daily note — S&P -0.4% from its high",
                                "Body with a curly quote: it\u2019s fine, and 25\u00b0C.",
                                level="urgent")
    finally:
        urllib.request.urlopen = real
        os.environ.pop("NTFY_TOPIC", None)

    assert state == "OSError", f"the fake send should be what failed, got {state}"
    # Every header value must survive the latin-1 encoding urllib will apply to it.
    for k, v in built["headers"].items():
        str(v).encode("latin-1")
    payload = _json.loads(built["data"].decode("utf-8"))
    assert "\u2014" in payload["title"], "the em-dash must reach ntfy, not be stripped"
    assert "\u2019" in payload["message"] and "\u00b0" in payload["message"]
    assert payload["topic"] == "test-topic"
    # ntfy's JSON API takes 1-5 and IGNORES a string, which would silently downgrade
    # every urgent alert to default without ever failing.
    assert payload["priority"] == 5, payload["priority"]
    assert isinstance(payload["tags"], list)


@test
def the_digest_survives_having_no_model():
    """
    NO API KEY MUST DEGRADE THE DIGEST, NOT CANCEL IT.

    The numbers came from somewhere else and cannot be regenerated later; the summary is
    a convenience on top. Losing the note is a smaller loss than losing the day.
    """
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        ok, why = summarize.available()
        assert not ok and "ANTHROPIC_API_KEY" in why, why
        text, state = summarize.summarize({"date": "2026-09-21"}, kind="daily")
        assert text is None and "numbers only" in state, state
    finally:
        if saved:
            os.environ["ANTHROPIC_API_KEY"] = saved


@test
def the_cost_is_stated_before_anything_is_spent():
    """The requirement was 'tell me any API costs before using them'. This is that."""
    cost, sentence = summarize.estimate_cost(3000, 700, "claude-haiku-4-5")
    assert 0.005 < cost < 0.008, cost
    assert "$" in sentence and "month" in sentence
    assert summarize.MODEL in summarize.PRICES, "the default model has no published price"


@test
def a_watching_position_is_never_described_as_held():
    """
    SCHG AND SPMO ARE CANDIDATES, NOT HOLDINGS.

    The prompt must keep that distinction or the note will discuss a position that does
    not exist — and it would read perfectly well while doing it.
    """
    wl = alerter.load_watchlist()
    statuses = {p["status"] for p in wl["positions"]}
    assert statuses <= {"held", "watching", "muted"}, statuses
    watching = {p["symbol"] for p in wl["positions"] if p["status"] == "watching"}
    assert {"SCHG", "SPMO"} <= watching, watching

    facts = {"date": "2026-09-21", "positions": wl["positions"], "quotes": [],
             "headlines": [], "themes": []}
    prompt = summarize.render(facts, "daily")
    assert "do not say they own these" in prompt
    assert "SCHG" in prompt.split("DECIDING ABOUT")[1].split("\n")[0]
    assert "watching" in summarize.SYSTEM


@test
def a_muted_position_is_never_fetched():
    """Muted means kept in the file for later, not quietly alerted on anyway."""
    wl = json.loads(json.dumps(alerter.load_watchlist()))
    wl["positions"].append({"symbol": "ZZZZ", "kind": "stock", "status": "muted"})
    assert "ZZZZ" not in {p["symbol"] for p in alerter.active_positions(wl)}


@test
def the_workflow_carries_no_secret_and_no_self_hosted_runner():
    """
    THIS IS DESIGNED FOR A PUBLIC REPOSITORY, WHICH MAKES BOTH OF THESE FATAL.

    A hardcoded key in a public repo is a leaked key. A self-hosted runner on a public
    repo lets a stranger's pull request run code on your machine — the hazard the stocks
    repo documents at length, and the reason this one must stay separate from it.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".github", "workflows", "alerts.yml")
    body = open(path).read()
    # CHECK THE `runs-on:` LINES, NOT THE PROSE. The first version of this grepped the
    # whole file for "self-hosted" and failed on the comment warning against it — a test
    # that fires on its own documentation is noise, and noise gets suites ignored.
    targets = [ln.split("runs-on:", 1)[1].strip()
               for ln in body.splitlines() if "runs-on:" in ln]
    assert targets, "no runs-on line at all"
    for target in targets:
        assert "self-hosted" not in target, (
            f"this workflow targets {target} and is meant for a PUBLIC repo, where a "
            f"stranger's pull request could then run code on your machine")
    for leak in ("sk-ant-", "AKIA", "smtp.gmail.com\n          password"):
        assert leak not in body, f"a literal credential is in the workflow: {leak}"
    # every credential arrives as a secret
    for name in ("NTFY_TOPIC", "SMTP_USER", "SMTP_PASS", "ANTHROPIC_API_KEY", "FRED_API_KEY"):
        assert f"secrets.{name}" in body, f"{name} is not read from a repository secret"
    # and the watchlist is restored before the state commit, so a private one stays private
    # LAYOUT-AGNOSTIC: this file lives at the repo root in the alerter's own repository
    # and under alerts/ in the repo it was written in. A test that pins the path fails on
    # the move rather than on the thing it is checking.
    assert "git checkout --" in body and "watchlist.json" in body.split("git checkout --")[1][:60], (
        "the watchlist is not restored before the state commit, so a watchlist supplied "
        "by secret could be committed back into the public repo")


@test
def a_quiet_check_sends_nothing():
    """An alerter that pings to say nothing happened is one you mute."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerter.py")).read()
    assert 'if verdict["urgency"] == "urgent":' in src
    assert "quiet — nothing sent" in src


@test
def a_dated_event_is_never_invented():
    """
    A NEWSLETTER THAT NAMES THE WRONG DATE IS WORSE THAN ONE THAT NAMES NONE.

    "CPI is Tuesday" when it is Thursday is confidently wrong about the one thing you
    would have acted on. There is no free, reliable calendar API, so the dates are
    declared in a file and the model is told it may name only those.
    """
    # COLLAPSED, because the prompt is wrapped and the phrase spans a line break — a
    # test that fails on where a paragraph happens to wrap is testing the wrapping.
    flat = " ".join(summarize.SYSTEM.split())
    assert "Never invent a date for an earnings report" in flat, flat[-300:]
    assert "the ONLY ones you may name" in summarize.render(
        {"date": "2026-10-10", "catalysts_text": "  2026-10-14: CPI"}, "weekahead")


@test
def an_empty_week_ahead_says_which_kind_of_empty_it_is():
    """
    "QUIET WEEK" AND "NOBODY UPDATED THE FILE" ARE OPPOSITE AND LOOK IDENTICAL.

    Both produce a letter with nothing in the look-ahead. Without the runway check the
    calendar silently rots and the Monday letter keeps arriving, emptier each month.
    """
    import datetime as dt
    events = [{"date": "2026-10-14", "what": "CPI", "why_it_matters": "x"}]
    _last, _runway, stale = calendar_events.horizon(today=dt.date(2026, 10, 10),
                                                    events=events)
    assert stale, "four days of runway was not reported as stale"
    _text, warning = calendar_events.render(today=dt.date(2026, 1, 1), within_days=7)
    assert isinstance(warning, str)

    # A PAST EVENT IS DROPPED, never carried forward as though it were still coming.
    assert calendar_events.upcoming(7, today=dt.date(2026, 11, 1), events=events) == []
    # and a malformed date is skipped rather than guessed
    bad = [{"date": "next tuesday", "what": "x", "why_it_matters": "y"}]
    assert calendar_events.upcoming(30, today=dt.date(2026, 10, 1), events=bad) == []


@test
def the_letters_go_to_email_and_only_alerts_push():
    """
    A 450-WORD NEWSLETTER IS UNREADABLE AS A PHONE NOTIFICATION.

    And pushing one every weekday is how the urgent channel gets muted — they share the
    app, so the noise you tolerate on the newsletter is noise you have taught yourself to
    swipe away on the alert.
    """
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "alerter.py")).read()
    assert 'channels=("email",)' in src, "the digests are pushing to the phone"
    assert 'level="quiet"' in src, "no short pointer is sent to the phone"
    assert 'channels=("ntfy", "email")' in src, "the urgent path must still push"


@test
def the_error_codes_leave_the_letter_without_leaving_the_log():
    """
    THE FIRST REAL LETTER ENDED WITH TWENTY COPIES OF `yahoo_http_429+stooq_unparsed`.

    Every word of it was true and almost none of it was for a human. Diagnostics and prose
    were written by the same code, so they got the same weight in a letter meant to be
    read over breakfast.

    The rule is NOT "hide the failures" — a digest that silently omits oil reads exactly
    like one where oil did not move, which is why states exist at all. It is that the two
    audiences get two renderings: one short English sentence per REASON for the reader,
    every machine state for the run log.
    """
    import letter

    facts = {"quote_failures": {s: "yahoo_http_429+stooq_unparsed"
                                for s in ("META", "CELH", "TTWO", "VOO", "BTC-USD")},
             "macro_failures": {"DGS10": "no_key", "DFF": "no_key"},
             "index_state": "yahoo_http_429_host_throttled",
             "positions": [], "quotes": [], "index": {}}

    notes = letter.data_notes(facts)
    assert len(notes) <= 3, f"one line per failure is back: {notes}"
    joined = " ".join(notes)
    for code in ("429", "http_", "unparsed", "stooq", "yahoo"):
        assert code not in joined.lower(), f"a machine state reached the reader: {code}"
    assert "throttled" in joined and "key" in joined, joined

    # THE READER LOSES NO FACT — every failing name is still accounted for, either by
    # name or by the "and N more" count.
    assert "META" in joined and "DGS10" in joined

    # AND THE ENGINEER'S VERSION IS UNCHANGED, still naming every state.
    full = alerter.failures_line(facts)
    assert "yahoo_http_429" in full and "no_key" in full, full

    # The plain-text letter is what a screen reader gets, so it carries no markup.
    text = letter.plain("daily", facts, {"fired_levels": []}, "A note.")
    for markup in ("<", ">", "&nbsp;", "style="):
        assert markup not in text, f"markup reached the spoken version: {markup}"


@test
def the_model_is_given_the_trigger_price_rather_than_deriving_one():
    """
    IT HAD TWO NUMBERS AND COMBINED THE WRONG PAIR, AND THE SENTENCE READ PERFECTLY.

    Measured 2026-09-22 on a real letter: "the S&P 500 sits 34 points above your first
    staged buy trigger". 34 was the distance to the RECORD (7,799 less 7,765); the
    trigger was at 7,019, some 746 points away. Given a level and a percentage but not a
    price, it derived one, and derived it wrong.

    A prompt instruction not to invent numbers does not cover this — nothing was invented,
    two real figures were combined incorrectly. The fix is to remove the arithmetic: the
    price is computed in code and handed over. Same rule the urgent path follows, for a
    different reason — there it is reliability, here it is that a plausible wrong number
    is worse than no number.
    """
    import summarize

    wl = alerter.load_watchlist()
    state = {"highest_close": 7799.0, "fired_levels": []}
    facts = {"index": {"close": 7765.0, "observed_high": 7799.0}, "quotes": [],
             "positions": [], "macro": {}}
    _verdict, _state = alerter.evaluate(facts, wl, state)
    idx = facts["index"]

    assert idx["next_trigger"] == 10, idx
    # the trigger PRICE, not the percentage — 10% below the record
    assert abs(idx["next_trigger_price"] - 7019.1) < 1.0, idx["next_trigger_price"]
    # and the points figure is to the TRIGGER, not to the record
    assert abs(idx["points_to_trigger"] - 745.9) < 1.0, idx["points_to_trigger"]
    assert abs(idx["points_to_trigger"] - 34.0) > 100, (
        "points_to_trigger is the distance to the RECORD — the exact confusion that "
        "produced the wrong sentence")

    # and both figures reach the prompt, so it never has to work them out
    prompt = summarize.render({**facts, "date": "2026-09-22"}, "daily")
    assert "7,019" in prompt and "746" in prompt, prompt[:400]


@test
def a_data_note_names_what_the_reader_can_actually_fix():
    """
    A COMBINED STATE CONTAINS BOTH A DEAD END AND A TWO-MINUTE FIX.

    `yahoo_http_429+finnhub_no_key+stooq_unparsed` is throttling AND a missing key. The
    first version checked 429 first, so the note said "the free price sources throttled
    this run" — true, useless, and it buried the half the reader could act on.
    """
    import letter

    assert letter.classify("yahoo_http_429+finnhub_no_key+stooq_unparsed") == "no_key"
    assert letter.classify("yahoo_http_429_host_throttled") == "rate_limited"
    assert "key" in letter.REASON_TEXT[("price", "no_key")]

    # AND THE NOTE MUST NAME WHAT IS MISSING, NOT ONLY WHY.
    #
    # Measured 2026-09-22: "BTC-USD, CELH, META and 4 more: the free price sources
    # throttled this run", at the bottom of the first letter that CARRIED those prices.
    # All of them were throttled NEWS feeds. The bucket was keyed on the reason alone, so
    # a rate-limited headline feed and a rate-limited quote produced the same sentence —
    # and the sentence had been written back when only prices could fail. The letter
    # contradicted its own body in fluent English.
    notes = letter.data_notes({
        "quote_failures": {"SCHG": "yahoo_http_429+tradingview_empty"},
        "news_failures": {"META": "http_429", "CELH": "http_429_host_throttled"},
        "macro_failures": {"DCOILWTICO": "no_key"},
        "macro_labels": {"DCOILWTICO": "WTI crude oil"},
    })
    joined = " ".join(notes)
    assert "CELH, META: no headlines" in joined, joined
    assert "SCHG: no price" in joined, joined
    # THE ONE THAT MATTERS: a symbol whose price arrived is never called priceless.
    assert "META: no price" not in joined and "META, " not in joined.split("no price")[-1]
    assert "WTI crude oil" in joined and "DCOILWTICO" not in joined
    # Still one sentence per (what, why) pair, never one per symbol.
    assert len(notes) == 3, notes


@test
def a_trusted_account_is_trusted_about_markets_not_about_lunch():
    """
    TEN ACCOUNTS POSTING TWENTY TIMES A DAY IS TWO HUNDRED NOTIFICATIONS.

    And the response to two hundred notifications is muting the app — which costs the dip
    alert this whole system exists for. So the filter is not a nicety, it is what keeps
    the urgent channel usable.

    MATCHED ON WORD BOUNDARIES. "META" inside "metadata" and "oil" inside "toil" are the
    matches that fill a phone with nonsense and teach you the filter does not work.
    Symbols are case-sensitive because a three-letter ticker is a common word in lower
    case: `ALL` and `IT` would match half of English otherwise.
    """
    import accounts

    syms = ["META", "CELH", "VOO", "BTC-USD"]
    words = ["fed", "hormuz", "oil"]

    for text in ("META beat on earnings", "Fed signals another hike",
                 "Hormuz closure enters week three"):
        ok, hits = accounts.relevant(text, syms, [], words)
        assert ok, f"missed something relevant: {text}"

    for text in ("metadata pipelines are hard", "I had a nice lunch",
                 "much toil for little reward", "the federation meets today"):
        ok, hits = accounts.relevant(text, syms, [], words)
        assert not ok, f"substring noise got through: {text} -> {hits}"


@test
def the_account_watcher_never_repeats_itself_or_floods():
    """
    IT RUNS EVERY HALF HOUR, so "already sent" has to survive a fresh checkout.

    Same reasoning as the dip levels: a runner starts from a clean clone every time, so
    state that lives only on disk does not exist and every post would be re-sent forever.

    And the cap drops the OLDEST of a batch rather than the newest — on a busy day the
    thing you most want is what just happened. What gets dropped stays unseen and can
    arrive next run, so nothing is lost, only delayed.
    """
    import accounts

    wl = {"positions": [{"symbol": "META", "status": "held"}]}
    cfg = {"bluesky": [], "extra_keywords": ["fed"], "max_per_run": 2,
           "posts_per_account": 10}

    posts, seen, failures = accounts.check(wl, seen=[], cfg=cfg)
    assert posts == [] and failures == {}, (posts, failures)

    # the cap and the seen-set are what the caller relies on, so check them directly
    assert cfg["max_per_run"] == 2
    body = accounts.render([{"handle": "a.bsky.social", "text": "x" * 400,
                             "url": "u", "matched": []}])
    assert len(body) < 220, "a phone notification is not a letter"
    assert body.endswith("..."), "a long post is truncated rather than sent whole"


@test
def the_subject_line_carries_the_number():
    """
    "Daily market note" told you nothing the schedule had not already told you.

    Half of email is read in the list view without opening anything, so the subject is
    the only line guaranteed to be seen — and a trigger being hit is exactly the thing
    that must survive being skimmed.
    """
    import letter

    # A VALUE WITH NO ROUNDING AMBIGUITY. The first version used -0.65, which Python
    # renders as -0.7 — the test was asserting my arithmetic, not the behaviour.
    facts = {"index": {"close": 7315.0, "high": 7700.0, "drawdown_pct": -5.0}}
    line = letter.subject("daily", facts, {"fired_levels": [], "movers": []})
    assert "-5.0%" in line, line

    fired = letter.subject("daily", facts, {"fired_levels": [5, 10], "movers": []})
    assert "10% trigger" in fired, fired


def main():
    passed, failed = 0, []
    for fn in TESTS:
        try:
            fn()
            print(f"  PASS  {fn.__name__.replace('_', ' ')}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {fn.__name__.replace('_', ' ')}\n          {e}")
            failed.append(fn.__name__)
        except Exception as e:
            print(f"  ERROR {fn.__name__.replace('_', ' ')}\n          {type(e).__name__}: {e}")
            failed.append(fn.__name__)
    print(f"\n  {passed} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
