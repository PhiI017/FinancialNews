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
                 "fred_series", "headlines", "feed"):
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
