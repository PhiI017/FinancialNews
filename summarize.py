"""
summarize.py — the only part that costs money, and it is optional.

WHAT IT COSTS, MEASURED RATHER THAN WAVED AT. One letter sends roughly 3,000 input tokens
(about sixty headlines plus the prompt and your positions) and gets back about 700. On
Claude Haiku 4.5 at $1.00 per million input and $5.00 per million output that is about
$0.0065 a run. TWO LETTERS A WEEKDAY plus the Sunday recap is about fifty runs a month, so
call it thirty-five cents. Sonnet 5 is roughly four times that and Opus 5 about fifteen
times; both are still under $5 a month. `estimate_cost` prints the arithmetic for whatever
MODEL is set to.

THE DEFAULT IS HAIKU BECAUSE "KEEP COSTS NEAR ZERO" WAS A REQUIREMENT, not because it is
the better model. Change one line below if a richer summary is worth the difference —
this is a judgement about your money, so it is a visible constant and not a hidden one.

AND IT IS OPTIONAL IN THE STRONG SENSE. With no ANTHROPIC_API_KEY the digest still goes
out carrying every number and headline, with one line saying the summary was skipped and
why. A missing key must never silently produce an empty-looking quiet day.
"""

import os

MODEL = "claude-haiku-4-5"

PRICES = {                       # $ per million tokens, (input, output)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
}

SYSTEM = """You write a short market note for one private investor. Plain English, no
jargon, no hype, no advice to buy or sell.

Rules you must follow:
- Lead with what actually changed today. If little changed, say so in one line.
- NEVER OPEN WITH WHAT YOU COULD NOT GET. The letter already carries a short data-notes
  section listing anything missing, so an opening paragraph about absent prices is both
  duplicated and the weakest possible use of the first thing they read. Open with the
  most important thing you DO know. If prices are missing, mention it once, briefly, and
  near the end — or not at all when the data notes already cover it.
- Tie news to THEIR positions by name where it touches them.
- THE HEADLINES INCLUDE BROAD MARKET NEWS, not just their companies. A story about the
  Fed, oil, China, rates or the wider market belongs in the note even when it names none
  of their holdings — say which way it would push them and why. Only genuinely unrelated
  stories get dropped.
- Separate what happened from what it might mean, and mark speculation as speculation.
- Never invent a number. If you do not have a figure, describe the direction instead.
- A position marked "watching" is one they do not own yet and are deciding about. Do not
  write about it as though they hold it.
- No tables, no bullet characters, no markdown headings. Short paragraphs only, because
  this is read aloud by a screen reader.
- At most 250 words for a pre-open or pre-close note, 450 for a weekly or week-ahead one.

The letter you are writing is one of four, and they have different jobs. Two of them
arrive on the same day and MUST NOT read like the same letter twice:

PREOPEN (weekday, 8:45am New York, before the bell) — entirely about the day that has not
happened yet. Open with what is due TODAY: anything dated on the calendar, and anything
overnight that changes how their positions start. Then, in a line or two, where things
stand going in. Write it for somebody deciding what to do before the market opens, so no
recap of yesterday beyond what still matters this morning.

PRECLOSE (weekday, 3:40pm New York, twenty minutes before the bell) — about the day that
has just happened, while there is still time to act on it. Lead with what actually moved
and why, tied to their positions. If something needs a decision before the close, say so
plainly in one line near the top. The prices you are given are twenty minutes from the
close, so describe them as where things stand now and never as the closing price.

WEEKAHEAD (Monday) — almost entirely forward. Open with the two or three dated events in
the week and what each one would mean for their specific holdings. Say plainly which
day. Then, briefly, where things stand entering the week. Do not recap last week.

WEEKLY (Sunday) — two halves, in this order. First what actually happened over the week
and what it changed. Then what is coming next week and which of it could move their
positions. Name the dates.

On catalysts: only discuss events that appear in the dated list given to you. Never
invent a date for an earnings report, a Fed meeting or an economic release. If the list
is empty, say the calendar has nothing dated rather than filling the gap.

── EXPLAIN, DO NOT ASSUME ───────────────────────────────────────────────────────────

The reader is an intelligent private investor, not a professional. They are learning.
Assume no jargon is known and no mechanism is obvious.

EVERY TERM A NON-PROFESSIONAL MIGHT NOT KNOW GETS A SHORT EXPLANATION THE FIRST TIME IT
APPEARS, in the same sentence, in brackets or after a dash. "The 10-year yield rose to
5.1% — that is what the US government pays to borrow for ten years, and it sets the floor
for what every other loan and investment has to beat." Do this for FOMC, yields, basis
points, multiples, guidance, and anything similar. Never write a term and move on.

FOR EACH STORY, ANSWER THREE THINGS IN THIS ORDER, in plain prose rather than as labels:

  WHAT HAPPENED — the fact, briefly.
  WHY IT MATTERS — the mechanism. Not "this is bullish" but the actual chain: higher
  yields make safe bonds pay more, which makes expensive growth stocks less attractive
  by comparison, which pushes their prices down. Walk the steps.
  WHAT IT COULD HIT — name their specific positions and say WHICH DIRECTION and roughly
  how much it would take to matter. If a story would not move anything they hold, say so
  in one clause and stop; do not manufacture a connection.

SEPARATE WHAT IS KNOWN FROM WHAT IS GUESSED, in the words themselves. "Rates rose" is a
fact. "That usually pressures growth stocks" is a pattern. "This could mean META falls
next week" is a guess. Mark the third kind as a guess every time.

END WITH ONE LINE ON WHAT TO WATCH NEXT — the nearest thing that would change the
picture, and what it would tell them. If nothing is pending, say the next scheduled item
and when.

NEVER TELL THEM TO BUY OR SELL. Explain the mechanism and let them decide. They have
their own written plan with staged buy triggers; your job is to make them understand what
is happening, not to second-guess it."""


def estimate_cost(in_tokens, out_tokens, model=None):
    """($ for this call, human sentence) — printed before anything is spent."""
    model = model or MODEL
    rate_in, rate_out = PRICES.get(model, PRICES["claude-haiku-4-5"])
    cost = (in_tokens / 1e6) * rate_in + (out_tokens / 1e6) * rate_out
    return cost, (f"{model}: ~{in_tokens:,} in + {out_tokens:,} out "
                  f"= ${cost:.4f} this run, about ${cost * 50:.2f} a month "
                  f"at two letters a weekday plus the Sunday recap")


def available():
    """(bool, reason) — whether a summary can be produced at all."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "no ANTHROPIC_API_KEY set — digest sent with numbers only"
    try:
        import anthropic                                   # noqa: F401
    except ImportError:
        return False, "the anthropic package is not installed — digest sent with numbers only"
    return True, ""


def summarize(facts, kind="preclose", model=None):
    """
    (text, state) — the note, or a stated reason there is none.

    `facts` is the assembled dict from `alerter.gather`; it is rendered to text here
    rather than passed as JSON because the model reads prose better and because it keeps
    the prompt readable when something in it looks wrong.
    """
    ok, reason = available()
    if not ok:
        return None, reason
    import anthropic

    model = model or MODEL
    prompt = render(facts, kind)
    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=1200,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        # THE DIGEST STILL GOES OUT. A summariser outage must not cost you the numbers,
        # which are the part that came from somewhere else and cannot be regenerated.
        return None, f"summary failed ({type(e).__name__}) — digest sent with numbers only"

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    usage = response.usage
    cost, _ = estimate_cost(usage.input_tokens, usage.output_tokens, model)

    # WHO WROTE THIS, ON THE LETTER ITSELF. `response.model` is what the API reports it
    # actually served, not what was asked for — those can differ, and the served one is
    # the honest answer. Reading it off the response rather than echoing the request is
    # the difference between a label and a fact.
    #
    # It carries the cost too, so the running total is visible in your inbox rather than
    # only in a console you would have to go and look at.
    served = getattr(response, "model", model)
    text += (f"\n\n—\nWritten by {served}. This letter cost ${cost:.4f} "
             f"({usage.input_tokens} in, {usage.output_tokens} out). "
             f"Not financial advice.")
    return text, f"ok ({served}, ${cost:.4f}, {usage.input_tokens}+{usage.output_tokens} tokens)"


def render(facts, kind):
    """The prompt. Plain text, deterministic order, so a bad output can be reproduced."""
    lines = [f"Write the {kind} note. Today is {facts.get('date', 'unknown')}.", ""]

    held = [p for p in facts.get("positions", []) if p.get("status") == "held"]
    watch = [p for p in facts.get("positions", []) if p.get("status") == "watching"]
    if held:
        lines.append("They HOLD: " + ", ".join(p["symbol"] for p in held))
    if watch:
        lines.append("They are DECIDING ABOUT (do not say they own these): "
                     + ", ".join(p["symbol"] for p in watch))
    lines.append("")

    lines.append("Prices today:")
    for q in facts.get("quotes", []):
        lines.append(f"  {q['symbol']}: {q['price']:.2f}, {q['change_pct']:+.2f}% on the day")
    for sym, state in (facts.get("quote_failures") or {}).items():
        lines.append(f"  {sym}: NO PRICE — {state}. Say this is missing; do not guess it.")
    lines.append("")

    idx = facts.get("index") or {}
    if idx.get("close"):
        lines.append(f"S&P 500 at {idx['close']:.2f}, which is {idx['drawdown_pct']:+.2f}% "
                     f"from its all-time closing high of {idx['high']:.2f}.")
        # THESE TWO PERCENTAGES ARE DIFFERENT QUESTIONS AND THE LETTER ONCE MERGED THEM.
        if idx.get("change_pct") is not None:
            lines.append(
                f"  ON THE DAY ({idx.get('asof', 'the latest session')}) it moved "
                f"{idx['change_pct']:+.2f}%, from {idx['prev_close']:,.2f}. THIS is the "
                f"day's move; the figure above is the distance from the record and is NOT "
                f"how far it moved today. A letter once called a +1.49% day 'barely moved' "
                f"by reading the -0.4% drawdown as the change.")
        # THEIR OWN PLAN, IN THE PROMPT. Without it the letter talks about the market;
        # with it, the letter can say what the market means FOR THEM — which is the only
        # reason to read a personal newsletter rather than the news.
        if idx.get("next_trigger"):
            lines.append(
                f"  Their plan buys in stages at 10, 15, 20 and 25 percent below the "
                f"record. The next is {idx['next_trigger']}%, at an index level of "
                f"{idx.get('next_trigger_price', 0):,.0f} — that is "
                f"{idx.get('points_to_trigger', 0):,.0f} points below today's close, a "
                f"further {idx['to_next_trigger']:.1f}% fall. USE THESE FIGURES AS GIVEN "
                f"AND DO NOT COMPUTE YOUR OWN: a previous letter said the index sat '34 "
                f"points above the trigger' when 34 was the distance to the RECORD and "
                f"the trigger was 746 points away. Mention this only if it is close or "
                f"if something moved it.")
        if idx.get("fired"):
            lines.append(f"  IT CROSSED THEIR {idx['fired']} percent DIP TRIGGER TODAY. "
                         f"Lead with this.")
        lines.append("")

    # ── THE PREMIUM, WHICH IS A DIFFERENT RISK FROM THE PRICE AND MUST READ AS ONE ────
    for sym, row in (facts.get("premiums") or {}).items():
        if row.get("premium_pct") is None:
            lines.append(
                f"{sym}: its NAV is {row.get('stale_days')} days old "
                f"({row.get('nav_asof')}), so NO PREMIUM CAN BE STATED. Say the figure is "
                f"stale; do not compute one from it and do not guess.")
            continue
        lines.append(
            f"{sym} trades at {row['price']:.2f} against a net asset value of "
            f"{row['nav']:.2f} ({row['nav_asof']}), a premium of "
            f"{row['premium_pct']:+.1f}%.")
        lines.append(
            f"  EXPLAIN WHAT THAT MEANS, because it is the point of watching this one: "
            f"the premium is what the market pays ABOVE the value of what the fund owns, "
            f"and it can fall on its own while every company the fund holds does fine. "
            f"This is a CLOSED-END fund, so unlike an ordinary ETF there is no mechanism "
            f"creating and redeeming shares at NAV to close that gap. Their plan buys at "
            f"{', '.join(f'{r:+.0f}%' for r in row['rungs'])}. Do not tell them to buy "
            f"or sell; say where the premium is and what moved it.")
        if row.get("loss_to_nav_pct") is not None:
            lines.append(
                f"  GIVE THE PREMIUM IN THE UNITS OF THE DECISION, using this figure and "
                f"not one you derive: if the premium went to zero from here the shares "
                f"fall {row['loss_to_nav_pct']:.0f}%, with the companies the fund owns "
                f"completely unchanged. That is the risk being carried, and a percentage "
                f"premium on its own does not convey it.")
        for r in row.get("rung_prices") or []:
            lines.append(
                f"  Their {r['rung']:+.0f}% level is a share price of {r['price']:,.2f}, "
                f"{r['fall_pct']:+.1f}% from here. USE THESE FIGURES AS GIVEN AND DERIVE "
                f"NOTHING: a previous letter computed a trigger distance itself and was "
                f"wrong by a factor of twenty, in a sentence that read perfectly.")
        # THE ASSUMPTION THE WHOLE FIGURE RESTS ON, SAID OUT LOUD EVERY TIME.
        lines.append(
            f"  STATE THIS PLAINLY, in one clause: the NAV is from {row['nav_asof']}, "
            f"{row['stale_days']} days ago, and the premium assumes it has not changed "
            f"since. It is a {row.get('cadence')} figure, so between publications the "
            f"premium moves because the PRICE moved, not because the fund re-valued its "
            f"assets. A reader who does not know that will think a falling premium means "
            f"the holdings were marked down.")
    for sym, state in (facts.get("premium_failures") or {}).items():
        lines.append(f"{sym}: NO NAV — {state}. Say the premium cannot be computed and "
                     f"why; never let the price stand in for it.")
    if facts.get("premiums") or facts.get("premium_failures"):
        lines.append("")

    macro = facts.get("macro") or {}
    if macro:
        lines.append("Macro:")
        for sid, row in macro.items():
            label = facts.get("macro_labels", {}).get(sid, sid)
            lines.append(f"  {label}: {row['latest']} (previous {row['previous']})")
        lines.append("")
    for sid, state in (facts.get("macro_failures") or {}).items():
        lines.append(f"  {sid}: UNAVAILABLE — {state}")

    # ── HEADLINES GROUPED BY COMPANY, NOT POURED INTO ONE LIST ──────────────────────
    #
    # MEASURED 2026-09-22: CELH rose 5.6%, the biggest move of the day, and the letter gave
    # it one subordinate clause inside a paragraph about oil — while TTWO, down 2.1%, got a
    # paragraph of its own. Its news HAD been fetched; the run reported no failures at all.
    #
    # The cause was the shape of this block. Sixty headlines arrived as one flat list of
    # "[SYMBOL] title" lines mixed in with the market feeds, so nothing connected a holding
    # to its own news and nothing obliged the letter to cover the thing that moved. The
    # model wrote about whatever read most interestingly, which is what a flat list asks
    # for.
    #
    # Grouped, a company with no headlines is VISIBLE as an empty block rather than being
    # indistinguishable from one the model chose not to mention.
    news = facts.get("headlines", [])
    held_syms = [p["symbol"] for p in (facts.get("positions") or [])]
    by_symbol, market = {}, []
    for h in news:
        sym = h.get("symbol", "?")
        (by_symbol.setdefault(sym, []) if sym in held_syms else market).append(h["title"])
    if news:
        lines.append(f"Headlines ({len(news)}). Raw and unverified, and a feed filed under "
                     f"a symbol is only about that company around half the time — check "
                     f"the words before attributing one:")
        for sym in held_syms:
            titles = by_symbol.get(sym) or []
            if titles:
                lines.append(f"  {sym}:")
                for t in titles:
                    lines.append(f"    - {t}")
            else:
                lines.append(f"  {sym}: NO HEADLINES RETRIEVED for this company today.")
        if market:
            lines.append("  Market-wide:")
            for t in market:
                lines.append(f"    - {t}")
    else:
        lines.append("No headlines were retrieved.")
    lines.append("")

    # ── ANYTHING THAT MOVED MUST BE ACCOUNTED FOR, OR SAID TO BE UNACCOUNTED FOR ─────
    #
    # The other half of the same bug: covering the day's largest move in a subordinate
    # clause is a choice the letter should not have. And the alternative failure is worse —
    # inventing a reason — so the instruction names both and gives the out.
    moved = [q for q in (facts.get("quotes") or [])
             if q and abs(q.get("change_pct") or 0) >= 2.0]
    if moved:
        lines.append("MOVED TODAY AND MUST EACH BE ADDRESSED, largest first:")
        for q in sorted(moved, key=lambda r: -abs(r["change_pct"])):
            titles = by_symbol.get(q["symbol"]) or []
            lines.append(
                f"  {q['symbol']} {q['change_pct']:+.2f}% — "
                + (f"{len(titles)} headline(s) above." if titles
                   else "NO HEADLINES. Say the move is unexplained by anything retrieved; "
                        "do NOT supply a reason from memory."))
        lines.append("  Give the biggest mover its own paragraph. A cause may be stated "
                     "ONLY if a headline above supports it; otherwise say what moved and "
                     "that the reason is not in today's news. An invented cause is the one "
                     "error here that cannot be spotted by reading.")
        lines.append("")

    cal = facts.get("catalysts_text")
    if cal:
        lines.append("")
        lines.append(f"Dated events ahead (the ONLY ones you may name):")
        lines.append(cal)
        if facts.get("catalysts_warning"):
            lines.append(f"  NOTE: {facts['catalysts_warning']}")

    themes = facts.get("themes", [])
    if themes:
        lines.append("They also care about: " + "; ".join(themes)
                     + ". Mention these only if a headline above actually touches one.")
    return "\n".join(lines)
