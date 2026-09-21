"""
triggers.py — what counts as urgent. Arithmetic only, and deliberately so.

NOTHING IN THIS FILE CALLS AN LLM. "The S&P is 10% below its high" and "META moved 6%
today" are subtraction. Routing them through a model would add a cost, a rate limit and
an outage to the one path that must work on the worst day of the year — which is exactly
the day an API is most likely to be slow and the alert most likely to matter.

The model's job starts after this one has already decided something happened.

── THE TWO THINGS THAT MAKE THIS HARDER THAN SUBTRACTION ────────────────────────────

FIRST, A TRIGGER THAT FIRES EVERY RUN IS A TRIGGER YOU TURN OFF. A run every 30 minutes
through a 12% drawdown would send the -10% alert around fifty times, and the fifty-first
is the one that gets muted along with everything else. So a level fires ONCE and re-arms
only after recovering above it by `REARM_BUFFER_PCT` — hysteresis, not a cooldown, because
a clock-based mute re-fires at the same level on the same drawdown a day later.

SECOND, AN ALL-TIME HIGH CAN ONLY GO UP, AND A BAD FETCH CAN SILENTLY LOWER IT. If a
truncated response returns three years instead of forty, the computed "high" falls, the
measured drawdown shrinks, and a -15% alert simply never fires — nothing errors and the
digest looks normal. `ratchet_high` refuses to lower a stored high, so the failure mode
becomes a stale high rather than a missed alarm.
"""

DEFAULT_MOVE_PCT = 5.0      # one-day move, in percent, that makes a holding urgent
REARM_BUFFER_PCT = 1.0      # recover this far above a level before it can fire again

# ── A MARKET DOWN EXACTLY 10.00% DID NOT FIRE THE 10% TRIGGER ───────────────────────
#
# Found by a test on the boundary, 2026-09-21. 6930/7700 is exactly 0.9 in decimal and
# 0.9000000000000000222 in binary, so the depth comes out as 9.999999999999998 and
# `depth >= 10` is False. The alert this system exists to send would have been skipped on
# the one round number a human would describe as "the market is down ten percent".
#
# Nothing would have looked wrong. No error, no log line — the digest would simply have
# reported a 10.0% drawdown (rounded for display) beside no trigger, and the next run at
# 9.98% would have looked like the level had already been handled.
#
# An epsilon of a hundredth of a percent is far below anything that could matter at these
# levels and far above float noise. Comparing rounded values instead would create a
# second, coarser bug at the re-arm boundary.
EPSILON_PCT = 0.01
WEEKEND_CRYPTO_NOTE = ("crypto trades at the weekend and equities do not, so a Saturday "
                       "move is compared against Friday's close for stocks")


def ratchet_high(stored_high, observed_high):
    """
    The all-time high never falls. Returns (high, moved_up).

    THE GUARD IS THE POINT, NOT THE MAXIMUM. Taking a max of two numbers is trivial; what
    this encodes is that a LOWER observation is evidence of a bad fetch rather than of a
    lower record, because an all-time high is monotone by definition. Without it, one
    short response quietly rescales every dip level at once.
    """
    stored = float(stored_high or 0.0)
    observed = float(observed_high or 0.0)
    if observed > stored:
        return observed, True
    return stored, False


def dip_state(close, high, levels, already_fired):
    """
    (drawdown_pct, [levels to fire now], [levels re-armed]) for the index.

    `already_fired` is the set of levels currently considered spent. A level fires when
    the drawdown reaches it and it is not spent; it un-spends when the market recovers to
    better than (level - buffer), so a genuine second trip to -10% alerts again.
    """
    if not high:
        return 0.0, [], []
    drawdown = (close / float(high) - 1.0) * 100.0        # negative when below the high
    depth = -drawdown                                      # positive percent below high
    fired, rearmed = [], []
    spent = {int(x) for x in already_fired}
    for level in sorted(int(x) for x in levels):
        if depth >= level - EPSILON_PCT and level not in spent:
            fired.append(level)
        elif level in spent and depth < level - REARM_BUFFER_PCT:
            rearmed.append(level)
    return drawdown, fired, rearmed


def movers(quotes, overrides=None, default_pct=DEFAULT_MOVE_PCT):
    """
    [{symbol, change_pct, threshold, direction}] for holdings that moved enough today.

    PER-SYMBOL THRESHOLDS EXIST BECAUSE ONE NUMBER IS WRONG FOR A MIXED BOOK. A 5% day in
    VOO is a market event; a 5% day in CELH is a Tuesday. A single threshold either buries
    the index moves or sends a small cap's ordinary noise every week, and the second is
    how an alerter gets ignored.
    """
    overrides = overrides or {}
    out = []
    for q in quotes:
        if not q:
            continue
        limit = float(overrides.get(q["symbol"], {}).get("move_pct", default_pct))
        if abs(q["change_pct"]) >= limit:
            out.append({"symbol": q["symbol"],
                        "change_pct": q["change_pct"],
                        "threshold": limit,
                        "direction": "up" if q["change_pct"] > 0 else "down"})
    return sorted(out, key=lambda r: -abs(r["change_pct"]))


def macro_moves(series_rows, thresholds):
    """
    [{series, latest, previous, change, threshold}] for macro series that jumped.

    ABSOLUTE, NOT PERCENT, and the units differ per series on purpose. A 0.15 move in the
    10-year yield is a large day; 0.15% of it is nothing. Percent would make one threshold
    mean four different things across four series.
    """
    out = []
    for series_id, rows in (series_rows or {}).items():
        if not rows or len(rows) < 2:
            continue
        latest, previous = rows[-1][1], rows[-2][1]
        limit = thresholds.get(series_id)
        if limit is None:
            continue
        if abs(latest - previous) >= float(limit):
            out.append({"series": series_id, "latest": latest, "previous": previous,
                        "change": latest - previous, "threshold": float(limit),
                        "asof": rows[-1][0]})
    return out


def urgency(fired_levels, big_movers, macro):
    """
    "urgent" | "important" | "quiet" — one word the caller routes on.

    A DIP LEVEL IS ALWAYS URGENT because it is the one thing the whole system exists to
    catch and it is the trigger tied to an actual plan. Everything else is important, and
    important means the digest rather than a push at 3am.
    """
    if fired_levels:
        return "urgent"
    if big_movers:
        return "urgent"
    if macro:
        return "important"
    return "quiet"
