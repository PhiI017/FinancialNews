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
import os
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
# ── AND THE SURVEY ANSWERED THE STRUCTURAL QUESTION, WHICH SHARPENS ALL OF THIS ──────
#
# Measured on the runner 2026-09-22. TradingView, asked what BOT IS rather than what it
# costs:
#
#     {'symbol': 'NASDAQ:BOT', 'description': 'RoboStrategy, Inc.',
#      'type': 'fund', 'typespecs': ['closedend'], 'close': 29.38}
#
# IT IS A CLOSED-END FUND, NOT AN ETF. Nasdaq's own API agrees by omission — it answers
# "Symbol not exists" for assetclass=etf and answers for assetclass=stocks. So the premium
# is not an anomaly to be arbitraged away; it is a permanent feature of the structure,
# because there is no creation and redemption at NAV to close it. Nothing pulls the price
# back to the assets in either direction, ever.
#
# THAT MOVES THE BASE RATE FROM ANALOGY TO DIRECT EVIDENCE, and it points down. Closed-end
# funds as a class have traded at a discount for most of their recorded history, and the
# pattern for a NEWLY LISTED one is sharper still and very well documented: they list at a
# premium, because the offering price carries the underwriting costs and the NAV starts
# below what buyers paid, and they have historically drifted to a discount within months.
# A new closed-end fund trading above NAV is the normal starting condition, not evidence
# of anything, and it is the condition that historically resolves downward.
#
# A premium also carries its own ceiling: issuing shares above NAV is accretive to existing
# holders, so a fund trading at a large premium is paid to create the supply that removes
# it.

# ── AND THEN THE DATA ARRIVED AND THE RUNGS WERE CALIBRATED FOR THE WRONG UNIVERSE ──
#
# Measured from CEF Connect's own history on 2026-09-22:
#
#     2026-06-30   NAV 10.51   price 39.75   premium +278.21%
#     2026-07-31   NAV 11.32   price 25.98   premium +129.51%
#
# Our arithmetic and the source's `DiscountData` agree to the hundredth on both, so the
# reading is not in doubt. THE PREMIUM HALVED IN ONE MONTH and the fund still trades at
# roughly two and a half times the value of what it owns.
#
# +20 / +10 / 0 / -10 would never have fired. They were written from the closed-end base
# rate, which is sound and describes where these things END UP, not where this one is.
# A ladder whose top rung is seven times below the current level is not cautious, it is
# silent, and it would have sat there saying nothing while the premium fell 150 points.
#
# THE RUNGS ARE NOW SPACED WHERE THE DECISION ACTUALLY CHANGES, which is the loss you
# take if the premium converges: p/(1+p).
#
#   +100   still paying twice what the assets are worth; a 50% fall to reach NAV. WARN.
#    +50   a 33% fall to NAV. The first level worth a notification.
#    +25   a 20% fall to NAV.
#      0   at NAV. The base rate says this is where such vehicles rest, and it is the
#          first level that is not paying for optimism at all.
#
# NONE OF THE POSITIVE RUNGS IS AN ENDORSEMENT. They are compression milestones on
# something that began at +278%, and the honest reading of the closed-end literature is
# that the only defensible entry is at or below NAV. The intermediate rungs exist so the
# system speaks on the way down rather than staying mute until a level that may take
# years to arrive.
DEFAULT_RUNGS = (100.0, 50.0, 25.0, 0.0)
WARN_ONLY_ABOVE = 100.0         # the +100 rung warns; it is never a buy signal
MIN_HISTORY_POINTS = 60         # before the fund's own distribution may set the rungs

# ── HOW OLD IS TOO OLD DEPENDS ON HOW OFTEN THE NUMBER IS PUBLISHED ─────────────────
#
# A FLAT SEVEN-DAY LIMIT WAS WRONG AND WOULD HAVE MADE THIS NEVER WORK. It was written for
# a daily-published NAV, and then applied to a fund whose holdings are private companies
# marked by appraisal — those are re-struck quarterly, not daily. Under a seven-day rule a
# quarterly NAV is stale eight days out of nine and the premium is simply never computed:
# a tracker that refuses almost always is the same as no tracker, and it would have looked
# like caution.
#
# THE CADENCE IS DECLARED AND THE LIMIT FOLLOWS FROM IT. Same shape as `macro.cadence_ok`
# in the sibling project, which states each series' cadence and refuses a mismatch rather
# than inferring one from the data and being confidently wrong about a quarterly series
# that happens to have two rows close together.
#
# AND THE ASSUMPTION IS STATED EVERY TIME, because the whole method rests on it: between
# marks the NAV is treated as UNCHANGED, so the premium moves with the price. That holds
# well for illiquid private holdings and not at all for liquid ones — which is why the
# cadence has to be declared per position rather than assumed once for everything.
NAV_CADENCE_DAYS = {
    "daily": 7,        # a real daily NAV feed; a week old means something broke
    "weekly": 16,
    "monthly": 45,
    "quarterly": 120,  # an appraisal cycle plus the lag before it is published
}
DEFAULT_NAV_CADENCE = "quarterly"
MAX_NAV_AGE_DAYS = NAV_CADENCE_DAYS["daily"]     # kept for callers that state no cadence


def stale_after(cadence):
    """Days before a NAV of this cadence stops being usable. Unknown cadence is strict."""
    return NAV_CADENCE_DAYS.get(cadence or "", NAV_CADENCE_DAYS["daily"])            # a NAV older than this is reported, never divided by


def premium_pct(price, nav):
    """Plain arithmetic, kept out of the model's hands for the same reason as the dips."""
    if not nav:
        return None
    return (price / nav - 1.0) * 100.0


def premium(symbol, price, nav_row, cadence=None):
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
    limit = stale_after(cadence or nav_row.get("cadence"))
    if age > limit:
        return {"symbol": symbol, "nav": nav_row["nav"], "nav_asof": nav_row.get("asof"),
                "stale_days": age, "stale_after": limit,
                "cadence": cadence or nav_row.get("cadence")}, "nav_stale"
    if not price:
        return None, "no_price"
    return {
        "symbol": symbol,
        "price": price,
        "nav": nav_row["nav"],
        "nav_asof": nav_row.get("asof"),
        "stale_days": age,
        "stale_after": limit,
        "cadence": cadence or nav_row.get("cadence"),
        "premium_pct": premium_pct(price, nav_row["nav"]),
        # THE NUMBER THAT ACTUALLY DECIDES ANYTHING. A premium of +155% sounds like a
        # percentage to shrug at; "a 61% loss if it converges to NAV, with the companies
        # unchanged" is the same fact in the units of the decision. p/(1+p), and it is
        # computed here because it is arithmetic and the model must never derive it.
        "loss_to_nav_pct": (premium_pct(price, nav_row["nav"]) /
                            (100.0 + premium_pct(price, nav_row["nav"])) * 100.0
                            if premium_pct(price, nav_row["nav"]) > -100 else None),
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

# ── ROUND TWO, AFTER TREATING MY OWN 404s AS MINE ───────────────────────────────────
#
# The first survey called four routes dead on an http_404 and concluded no free source
# carries this NAV. THAT WAS THE WRONG READING OF THE WRONG EVIDENCE, and this project has
# a rule about it in capital letters: an HTTP 404 is a path that does not exist, which
# makes it MY malformed URL far more often than the host's verdict about its data. The
# same mistake was made about Stooq earlier the same day and caught by a control.
#
# So: the guessed API paths are replaced with their documented shapes, the ones that took
# a wrong asset class are re-asked with the right one, and three genuinely new families
# are added — the fund's own site, which is where a closed-end fund is REQUIRED to publish
# its NAV, and the SEC, which is the only durable source in this whole file.
#
# NOTHING IS CONCLUDED FROM THIS UNTIL IT RUNS ON THE RUNNER.

NAV_ROUTES = {
    # CEF Connect — the first attempt guessed one path out of several.
    "navticker_tv": ("navticker", None),
    "cefconnect_3m": ("url", "https://www.cefconnect.com/api/v3/pricinghistory/{sym}/3M"),
    "cefconnect_1y": ("url", "https://www.cefconnect.com/api/v3/pricinghistory/{sym}/1Y"),
    "cefconnect_hist": ("url", "https://www.cefconnect.com/api/v3/pricinghistory/{sym}/1M"),
    "cefconnect_basic": ("url",
                         "https://www.cefconnect.com/api/v3/FundBasicInformation/{sym}"),
    "cefconnect_search": ("url",
                          "https://www.cefconnect.com/api/v3/FundSearch?ticker={sym}"),
    # stockanalysis — /e/ is their ETF namespace and this is not an ETF.
    "stockanalysis_s": ("url", "https://api.stockanalysis.com/api/symbol/s/{sym}/overview"),
    "stockanalysis_html": ("url", "https://stockanalysis.com/stocks/{sym_lower}/"),
    # Nasdaq — the first round asked for two asset classes out of five.
    "nasdaq_mutual": ("url",
                      "https://api.nasdaq.com/api/quote/{sym}/info?assetclass=mutualfunds"),
    "nasdaq_summary": ("url",
                       "https://api.nasdaq.com/api/quote/{sym}/summary?assetclass=stocks"),
    "nasdaq_profile": ("url", "https://api.nasdaq.com/api/company/{sym}/company-profile"),
    # THE FUND'S OWN SITE IS GONE FROM THIS LIST. Three spellings of robostrategy.com were
    # probed on 2026-09-22: two timed out and one 404'd. A guessed domain is a guess, the
    # timeouts cost seventy-five seconds of every survey, and CEF Connect already answers
    # with the fund's NAV ticker — so there is nothing left for it to add. Put it back if
    # the real domain is ever known, which is a fact nobody here has.
    # THE SEC, WHICH IS THE ONLY DURABLE ONE HERE. Everything else is a company's goodwill.
    "sec_lookup": ("sec",
                   "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                   "&company=RoboStrategy&type=&dateb=&owner=include&count=10&output=atom"),
    "sec_fts": ("sec", "https://efts.sec.gov/LATEST/search-index?q=%22RoboStrategy%22"),
    # THE PRESS RELEASE ROUTE, which the news layer can already read. Closed-end funds
    # announce NAV in releases; if one is carried, the number is in the text.
    "news_nav": ("url",
                 "https://news.google.com/rss/search?q=%22RoboStrategy%22+%22net+asset+value%22"
                 "&hl=en-US&gl=US&ceid=US:en"),
}


def nav_from_config(symbol, wl):
    """
    ({nav, asof, source}, state) — a NAV the user typed in, age-checked like any other.

    THE BACKSTOP, NOT THE PLAN. The automated routes are surveyed first and this exists so
    that a fund nobody publishes freely is still trackable — the arithmetic was never the
    hard part, only the input.

    WHAT MAKES IT SAFE IS THAT IT IS DATED AND THE DATE IS ENFORCED. A hand-entered NAV is
    a fact about one day and goes stale exactly like a fetched one, faster in fact because
    nobody is refreshing it. `premium()` applies MAX_NAV_AGE_DAYS to it identically, so a
    number typed in six weeks ago produces `nav_stale` with its age rather than a confident
    premium against an old mark.
    """
    for pos in wl.get("positions", []):
        if pos.get("symbol") == symbol and pos.get("nav"):
            return {"nav": float(pos["nav"]), "asof": pos.get("nav_asof", ""),
                    "cadence": pos.get("nav_cadence", DEFAULT_NAV_CADENCE),
                    "source": "config"}, "ok"
    return None, "no_config_nav"


CEFCONNECT_HIST = "https://www.cefconnect.com/api/v3/pricinghistory/{sym}/{period}"


def nav_ticker(symbol):
    """
    (ticker, state) — the separate symbol a fund's NAV series trades under.

    I GUESSED THIS AND THE GUESS WAS WRONG, WHICH IS THE WHOLE LESSON. The first survey
    tried "{sym}X" — BOTX — got `no_match`, and I read that as "no NAV series exists".
    The convention is X{sym}X, and I did not have to know that: CEF Connect states it.

        {"NAVTicker":"XBOTX","Cusip":"77106T107","Ticker":"BOT","Name":"RoboStrategy Inc"}

    ASKING THE SOURCE BEATS KNOWING THE CONVENTION. A guessed ticker that misses is
    indistinguishable from a fund that has no NAV, and that is exactly the wrong
    conclusion I drew and published. This reads the name out of the reply instead.
    """
    body, state = sources._get(CEFCONNECT_HIST.format(sym=symbol, period="1M"))
    if state != "ok":
        return None, state
    try:
        data = (json.loads(body) or {}).get("Data") or {}
    except Exception:
        return None, "unparsed"
    ticker = data.get("NAVTicker")
    return (ticker, "ok") if ticker else (None, "no_nav_ticker")


def cefconnect_series(symbol, period="1Y"):
    """
    ([{date, nav, price, premium_pct, source_premium}], state) — the published history.

    THE FIELD NAMES ARE THE SOURCE'S AND I HAD GUESSED THEM WRONG. The first version
    looked for "NAV"/"Nav"/"nav" and "Date"/"date"; the reply uses `NAVData`, `Data` for
    the PRICE, and `DataDate`. It would have parsed nothing and reported `empty_all_periods`
    — "this fund has no NAV history" — from a reply containing exactly that history. The
    same shape as every other mistake in this file today: a parser that cannot tell a
    format it does not recognise from data that is not there.

    AND THE SOURCE COMPUTES THE PREMIUM ITSELF, so it is read back and checked against
    ours rather than ignored. `DiscountData` is positive for a premium, which is worth
    knowing before reading a 278 as a discount. Two routes to one number is the same
    discipline the price ladder uses, and here it confirmed the reading exactly: 39.75
    over a NAV of 10.51 is +278.21%, which is what the field says to the hundredth.
    """
    body, state = sources._get(CEFCONNECT_HIST.format(sym=symbol, period=period))
    if state != "ok":
        return None, state
    try:
        data = (json.loads(body) or {}).get("Data") or {}
    except Exception:
        return None, "unparsed"
    out = []
    for r in data.get("PriceHistory") or []:
        nav_v, price_v = r.get("NAVData"), r.get("Data")
        when = (r.get("DataDate") or "")[:10]
        if not nav_v or not price_v or not when:
            continue
        mine = premium_pct(float(price_v), float(nav_v))
        theirs = r.get("DiscountData")
        row = {"date": when, "nav": float(nav_v), "price": float(price_v),
               "premium_pct": mine, "source_premium": theirs}
        # A DISAGREEMENT HERE IS A BUG, NOT A ROUNDING QUESTION. If the source's own
        # figure and ours part company the field means something else than assumed, and
        # that is worth stopping on rather than quietly preferring one.
        if theirs is not None and abs(float(theirs) - mine) > 0.5:
            row["disagrees_by"] = round(float(theirs) - mine, 2)
        out.append(row)
    if not out:
        return None, "empty"
    return sorted(out, key=lambda r: r["date"]), "ok"


def nav_from_cefconnect(symbol):
    """
    ({nav, asof, source}, state) — the newest published NAV point.

    THE SHORT WINDOWS ARE GENUINELY EMPTY FOR THIS FUND and the long ones are not, so an
    empty reply is not an answer and the next window is tried. Reading the first empty
    PriceHistory as "no history exists" is the mistake this function was rewritten for.
    """
    last_state = "empty"
    for period in ("3M", "6M", "1Y"):
        rows, state = cefconnect_series(symbol, period)
        if state == "ok" and rows:
            newest = rows[-1]
            return {"nav": newest["nav"], "asof": newest["date"],
                    "cadence": DEFAULT_NAV_CADENCE,
                    "source": f"cefconnect_{period}"}, "ok"
        last_state = state
    return None, f"all_periods_{last_state}"


def nav(symbol, wl=None):
    """
    ({nav, asof, source}, state) — the ladder. Automated first, the typed one as backup.

    AUTOMATED ROUTES ARE TRIED FIRST EVEN WHEN NONE OF THEM ANSWERS TODAY, because a newly
    listed fund is exactly the case that starts being covered later: the screener already
    has the `nav` column and only lacks the value. When it fills, this uses it with no
    change, and `source` says which rung answered — the same reason the price ladder
    reports its own.
    """
    tried = []
    rows, state = _tv_columns(symbol, ["close", "nav", "nav_discount_premium"])
    if state == "ok" and rows and rows[0].get("nav"):
        return {"nav": float(rows[0]["nav"]), "asof": time.strftime("%Y-%m-%d"),
                "cadence": "daily", "source": "tradingview"}, "ok"
    tried.append(f"tradingview_{'empty' if state == 'ok' else state}")

    # THE NAV'S OWN TICKER, ASKED FOR RATHER THAN GUESSED. If a NAV series is quoted, it
    # is quoted under a symbol of its own, and the source names it.
    xsym, xstate = nav_ticker(symbol)
    if xsym:
        rows, state = _tv_columns(xsym, ["close"])
        if state == "ok" and rows and rows[0].get("close"):
            return {"nav": float(rows[0]["close"]), "asof": time.strftime("%Y-%m-%d"),
                    "cadence": "daily", "source": f"tradingview:{xsym}"}, "ok"
        tried.append(f"navticker_{xsym}_{'empty' if state == 'ok' else state}")
    else:
        tried.append(f"navticker_{xstate}")

    row, state = nav_from_cefconnect(symbol)
    if state == "ok":
        return row, "ok"
    tried.append(f"cefconnect_{state}")

    row, state = nav_from_config(symbol, wl or {})
    if state == "ok":
        return row, "ok"
    tried.append(f"config_{state}")
    return None, "+".join(tried)


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
            elif kind == "navticker":
                xsym, xstate = nav_ticker(symbol)
                if not xsym:
                    print(f"  {name:<22} {xstate}")
                    continue
                rows, state = _tv_columns(xsym, ["close", "description"])
                print(f"  {name:<22} {state:<12} {xsym} -> {rows if rows else ''}")
            elif kind == "sec":
                # SEC ASKS FOR A DECLARED CONTACT AND WE DO NOT INVENT ONE. `no_contact`
                # is a state about US — a one-line repo variable away from working — and
                # must never read as "the SEC does not carry this".
                contact = os.getenv("SEC_CONTACT", "")
                if not contact:
                    print(f"  {name:<20} no_contact   (set SEC_CONTACT to use this route)")
                    continue
                body, state = sources._get(template.format(sym=symbol),
                                           headers={"User-Agent": contact})
                head = (body[:300].decode("utf-8", "replace") if body else "")
                print(f"  {name:<22} {state:<12} {head}")
            else:
                url = template.format(sym=symbol, sym_lower=symbol.lower())
                body, state = sources._get(url)
                text = body.decode("utf-8", "replace") if body else ""
                # WHAT IS BEING LOOKED FOR IS THE WORD, not the whole page: a 200 that
                # never says "net asset value" does not carry one, and that is a different
                # fact from the fetch failing.
                mark = ""
                for needle in ("net asset value", "netAssetValue", '"nav"', "NAV"):
                    i = text.find(needle)
                    if i >= 0:
                        mark = f"  <<{needle}>> {text[max(0, i - 40):i + 120]}"
                        break
                print(f"  {name:<22} {state:<12} {len(text)}B{mark or '  (no nav text)'}")
        except Exception as e:
            print(f"  {name:<20} raised_{type(e).__name__}: {e}")
    print()


def set_nav(symbol, value, asof, path="watchlist.json", cadence=None):
    """
    Write one NAV into the config. `python alerter.py --set-nav BOT 25.00 2026-09-19`.

    THE POINT IS THAT NOBODY SHOULD HAVE TO EDIT JSON BY HAND to answer a question this
    small. A mistyped brace breaks the whole run, the error arrives hours later in a cron
    log, and the cost of that is out of all proportion to typing a number.

    THE DATE IS REQUIRED AND NOT DEFAULTED TO TODAY. A NAV is a fact about the day it was
    struck, and quietly stamping it with today's date would turn a three-week-old quarterly
    mark into a fresh one — defeating the staleness check that is the only thing making a
    hand-entered number safe to use at all. Refusing is the whole feature.
    """
    import datetime
    import json as _json
    try:
        datetime.date.fromisoformat(asof[:10])
    except Exception:
        return None, "bad_date_use_YYYY-MM-DD"
    try:
        value = float(value)
    except Exception:
        return None, "bad_nav"
    if value <= 0:
        return None, "nav_must_be_positive"

    blob = _json.load(open(path))
    for pos in blob.get("positions", []):
        if pos.get("symbol") == symbol:
            pos["nav"], pos["nav_asof"] = value, asof[:10]
            pos["nav_cadence"] = cadence or pos.get("nav_cadence", DEFAULT_NAV_CADENCE)
            _json.dump(blob, open(path, "w"), indent=2, ensure_ascii=False)
            age = _age_days(asof[:10])
            return {"symbol": symbol, "nav": value, "asof": asof[:10],
                    "cadence": pos["nav_cadence"], "age_days": age,
                    "usable_for_days": stale_after(pos["nav_cadence"]) - (age or 0)}, "ok"
    return None, "symbol_not_in_watchlist"
