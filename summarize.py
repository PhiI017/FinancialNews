"""
summarize.py — the only part that costs money, and it is optional.

WHAT IT COSTS, MEASURED RATHER THAN WAVED AT. A daily digest sends roughly 3,000 input
tokens (about sixty headlines plus the prompt and your positions) and gets back about 700.
On Claude Haiku 4.5 at $1.00 per million input and $5.00 per million output that is about
$0.0065 a run — call it twenty cents a month for daily, under thirty with the Sunday
recap. Sonnet 5 is roughly four times that and Opus 5 about fifteen times; both are still
under $5 a month. `estimate_cost` prints the arithmetic for whatever MODEL is set to.

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
- At most 250 words for a daily note, 450 for a weekly or week-ahead one.

The letter you are writing is one of three, and they have different jobs:

DAILY — what moved today and why, tied to their positions. If a catalyst lands tomorrow,
one closing line flagging it. Nothing else forward-looking.

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
                  f"= ${cost:.4f} this run, about ${cost * 30:.2f} a month daily")


def available():
    """(bool, reason) — whether a summary can be produced at all."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False, "no ANTHROPIC_API_KEY set — digest sent with numbers only"
    try:
        import anthropic                                   # noqa: F401
    except ImportError:
        return False, "the anthropic package is not installed — digest sent with numbers only"
    return True, ""


def summarize(facts, kind="daily", model=None):
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
        # THEIR OWN PLAN, IN THE PROMPT. Without it the letter talks about the market;
        # with it, the letter can say what the market means FOR THEM — which is the only
        # reason to read a personal newsletter rather than the news.
        if idx.get("next_trigger"):
            lines.append(f"  Their plan buys in stages at 10, 15, 20 and 25 percent below "
                         f"the record. The next one is {idx['next_trigger']}%, which needs "
                         f"a further {idx['to_next_trigger']:.1f}% fall from here. Mention "
                         f"this only if it is close or if something moved it.")
        if idx.get("fired"):
            lines.append(f"  IT CROSSED THEIR {idx['fired']} percent DIP TRIGGER TODAY. "
                         f"Lead with this.")
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

    news = facts.get("headlines", [])
    if news:
        lines.append(f"Headlines ({len(news)}). Treat these as raw, unverified, and "
                     f"possibly about a different company than the symbol they are filed "
                     f"under — that happens about half the time:")
        for h in news:
            lines.append(f"  [{h.get('symbol', '?')}] {h['title']}")
    else:
        lines.append("No headlines were retrieved.")
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
