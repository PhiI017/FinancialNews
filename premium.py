"""
premium.py — what a share of a closed-ended vehicle costs against what it holds.

── THE THING BEING MEASURED ──────────────────────────────────────────────────────────

A fund whose shares trade on an exchange has two prices: what the market pays for a share,
and the value of its assets per share (NAV). The gap between them is the premium, or the
discount when it is negative.

    premium % = (price / nav - 1) * 100

FOR MOST ETFs THIS NUMBER IS BORING AND NEAR ZERO, and that is not luck. An open-end ETF
lets authorised participants create and redeem shares at NAV all day, so any gap is an
arbitrage that gets closed within pennies. A fund that can SUSTAIN a double-digit premium
is structurally not doing that — it is closed-end, or an interval fund, or otherwise has no
continuous redemption at NAV. Nothing forces its price back to its assets.

That structural fact is the whole risk, and it is worth being sure of before trusting any
number here: `structure_note()` reports what the sources actually say the vehicle is,
rather than taking the word "ETF" from anywhere including the user.

── WHY THE PREMIUM IS A SEPARATE RISK FROM THE COMPANIES ─────────────────────────────

You can lose money on this while every underlying company does fine. If a vehicle bought
at a 40% premium drifts back to NAV, that is a 29% loss with nothing whatsoever having
happened to the assets. MicroStrategy is the worked example everybody has: its premium to
the bitcoin it held ranged from roughly parity to well over 2x and compressed hard, and
holders who bought the premium lost money the bitcoin never lost.

So the premium is a SENTIMENT variable riding on top of a value variable, and it is the one
of the two that can be bought cheaply or expensively by choosing when to buy.

── AND THE NAV ITSELF IS AN ESTIMATE, WHICH CUTS THE SAME WAY ────────────────────────

Private holdings are not marked by a market. They are appraised, usually quarterly, often
smoothed, and the appraisal is the fund's own. So the premium computed here is the stated
premium plus an unknown mark error, and the error is not symmetric in consequence: a NAV
that is too high makes a bad premium look reasonable.

A STALE NAV MUST NEVER BE SILENTLY USED. That is this project's oldest rule in a new place
— a premium computed against a NAV from six weeks ago is a plausible-looking number with
no meaning, which is worse than no number. `premium()` refuses beyond `MAX_NAV_AGE_DAYS`
and says which it refused on.
"""

import json
import time

import sources

# ── WHAT COUNTS AS A HEALTHY PREMIUM, AND WHERE THE NUMBERS COME FROM ───────────────
#
# THE RULE THAT SHOULD DECIDE THIS CANNOT RUN YET, AND THAT IS THE FIRST FINDING.
# The only non-arbitrary anchor for a fund's premium is the fund's OWN history: buy when
# the premium sits in the bottom quartile of its own observed range. A recently formed
# fund with a limited operating history has no such range, so the honest statement is that
# the good rule is unavailable and a weaker one is standing in for it. `rungs_from_history`
# exists and REFUSES until there are enough observations; this file starts collecting them
# on the first run so the rule becomes available rather than staying a plan.
#
# UNTIL THEN, THE STRUCTURAL BASE RATE. Listed vehicles holding illiquid private assets
# have historically resolved to a DISCOUNT rather than a premium, as a class and over
# decades — closed-end funds have averaged single-digit-negative, and listed private-equity
# and business-development vehicles mostly trade below book. The mechanism is not mysterious:
# with no redemption at NAV, nothing pulls price up to assets, while fees, leverage cost,
# stale marks and illiquidity all pull a rational buyer's bid down. A premium is what
# enthusiasm pays on top of that, and enthusiasm is the part that leaves.
#
# A PREMIUM ALSO CARRIES ITS OWN CEILING. A fund trading above NAV is paid to issue new
# shares — issuance at a premium is accretive to existing holders — so a persistent large
# premium invites the supply that removes it. That is a reason to expect compression even
# with sentiment unchanged.
#
# SO THE RUNGS BELOW ARE DELIBERATELY UNAMBITIOUS, and they are shaped like the S&P dip
# ladder already in this system so they read the same way: one early warning that is not a
# buy, then levels that are.
#
#   +20   NOT A BUY. The premium is coming down from a high; say so and say nothing else.
#   +10   First rung. Paying a tenth over assets for access to something not otherwise
#         buyable is defensible. Paying more is paying for the mood.
#     0   At NAV. The structural base rate says this is roughly where such vehicles rest,
#         so this is the first level that is not paying for optimism at all.
#   -10   A discount. What this class has historically traded at when nobody is excited.
#
# THESE ARE A STARTING POSITION, NOT A MEASUREMENT, and they are in the config so they can
# be changed without touching code. The number that would replace them is the fund's own
# quartile, once there is enough history to have one.
DEFAULT_RUNGS = (20.0, 10.0, 0.0, -10.0)
WARN_ONLY_ABOVE = 20.0          # the +20 rung is an early warning, never a buy signal
MIN_HISTORY_POINTS = 60         # before the fund's own distribution may set the rungs
MAX_NAV_AGE_DAYS = 7            # a NAV older than this is reported, never divided by


def premium_pct(price, nav):
    """Plain arithmetic, kept out of the model's hands for the same reason as the dips."""
    if not nav:
        return None
    return (price / nav - 1.0) * 100.0


def premium(symbol, price, nav_row):
    """
    ({premium_pct, nav, nav_asof, stale_days, ...}, state) — or a state saying why not.

    THE REFUSALS ARE THE POINT. `no_nav` means nothing published one; `nav_stale` means one
    exists and is too old to divide by. Those need different responses and a single "no"
    would hide which.
    """
    if not nav_row or not nav_row.get("nav"):
        return None, "no_nav"
    age = _age_days(nav_row.get("asof"))
    if age is None:
        return None, "nav_undated"
    if age > MAX_NAV_AGE_DAYS:
        return {"symbol": symbol, "nav": nav_row["nav"], "nav_asof": nav_row.get("asof"),
                "stale_days": age}, "nav_stale"
    if not price:
        return None, "no_price"
    return {
        "symbol": symbol,
        "price": price,
        "nav": nav_row["nav"],
        "nav_asof": nav_row.get("asof"),
        "stale_days": age,
        "premium_pct": premium_pct(price, nav_row["nav"]),
        "source": nav_row.get("source", ""),
    }, "ok"


def _age_days(asof):
    if not asof:
        return None
    try:
        then = time.mktime(time.strptime(asof[:10], "%Y-%m-%d"))
    except Exception:
        return None
    return max(0, int((time.time() - then) // 86400))


def rungs_from_history(history, rungs=DEFAULT_RUNGS):
    """
    (rungs, state) — the fund's OWN distribution once there is one, else the defaults.

    THE GOOD RULE, WHICH REFUSES RATHER THAN APPROXIMATING. Below MIN_HISTORY_POINTS the
    quartiles of a handful of readings are noise wearing a statistic's clothes, and a
    threshold nobody can see being guessed is exactly the dial this project does not build.
    """
    points = sorted(p for p in (history or []) if p is not None)
    if len(points) < MIN_HISTORY_POINTS:
        return tuple(rungs), f"too_short_{len(points)}_of_{MIN_HISTORY_POINTS}"
    def pct(q):
        return points[min(len(points) - 1, int(q * len(points)))]
    # Bottom quartile is the buy, the median is the fair reading, the top quartile warns.
    return (round(pct(0.75), 1), round(pct(0.50), 1), round(pct(0.25), 1),
            round(pct(0.10), 1)), "ok"


def crossed(premium_now, rungs, already):
    """
    ([rungs newly reached], state-ish) — the dip ladder's logic, on the premium axis.

    SAME HYSTERESIS AS THE INDEX DIPS, for the same reason written above `dip_state`: a
    level that fires every run is a level you turn off. A rung is spent once reached and
    re-arms only when the premium climbs back above it.
    """
    if premium_now is None:
        return [], set(already or ())
    spent = set(already or ())
    hit = []
    for rung in sorted(rungs, reverse=True):
        if premium_now <= rung and rung not in spent:
            hit.append(rung)
            spent.add(rung)
        elif premium_now > rung:
            spent.discard(rung)          # back above it: this rung is live again
    return hit, spent


# ── FINDING THE NAV AT ALL ──────────────────────────────────────────────────────────
#
# EVERYTHING ABOVE IS WORTHLESS WITHOUT ONE, so the routes are surveyed on the runner the
# same way the price routes were, and nothing here is wired into a letter until one of
# them answers. A premium tracker that cannot read a NAV must say `no_nav` in plain words,
# not quietly report the price and let it read as a premium of zero.

NAV_ROUTES = {
    # The provider convention for a closed-end fund's NAV series: an X-wrapped ticker.
    "tradingview_xnav": ("tv", "{sym}X"),
    "tradingview_nav_col": ("tv_col", None),
    "cefconnect": ("url", "https://www.cefconnect.com/api/v3/DailyPricing/{sym}/1M"),
    "nasdaq_etf": ("url", "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=etf"),
    "nasdaq_stock": ("url", "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=stocks"),
    "stockanalysis": ("url", "https://stockanalysis.com/api/symbol/e/{sym}/overview"),
}


def structure_note(symbol):
    """What the sources SAY the vehicle is. The word 'ETF' is checked, never assumed."""
    rows, state = _tv_columns(symbol, ["description", "type", "typespecs", "close"])
    if state != "ok":
        return None, state
    return rows, "ok"


def _tv_columns(symbol, columns):
    payload = json.dumps({
        "symbols": {"tickers": [f"NASDAQ:{symbol}", f"NYSE:{symbol}", f"AMEX:{symbol}",
                                f"BATS:{symbol}"]},
        "columns": columns,
    }).encode("utf-8")
    body, state = sources._get(sources.TRADINGVIEW_SCAN, data=payload,
                               headers={"Content-Type": "application/json"})
    if state != "ok":
        return None, state
    try:
        data = json.loads(body).get("data", [])
    except Exception:
        return None, "unparsed"
    if not data:
        return None, "no_match"
    return [{"symbol": r.get("s"), **dict(zip(columns, r.get("d") or []))} for r in data], "ok"


def probe_nav(symbol="BOT"):
    """Which NAV routes answer for this symbol, and what each one says. Reports only."""
    print(f"what the sources say {symbol} IS:")
    rows, state = structure_note(symbol)
    if state != "ok":
        print(f"  {state}")
    else:
        for r in rows:
            print(f"  {r}")
    print()

    print(f"nav routes for {symbol}:")
    for name, (kind, template) in NAV_ROUTES.items():
        try:
            if kind == "tv":
                rows, state = _tv_columns(template.format(sym=symbol), ["close", "description"])
                print(f"  {name:<20} {state:<12} {rows if rows else ''}")
            elif kind == "tv_col":
                rows, state = _tv_columns(symbol, ["close", "nav", "nav_discount_premium"])
                print(f"  {name:<20} {state:<12} {rows if rows else ''}")
            else:
                body, state = sources._get(template.format(sym=symbol))
                head = (body[:220].decode("utf-8", "replace") if body else "")
                print(f"  {name:<20} {state:<12} {head}")
        except Exception as e:
            print(f"  {name:<20} raised_{type(e).__name__}: {e}")
    print()
