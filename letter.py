"""
letter.py — how a letter LOOKS. Nothing here fetches or decides anything.

WHY THIS IS ITS OWN FILE. The first real letter was accurate and unreadable: it opened
with an apology about missing data and closed with twenty repetitions of
`yahoo_http_429+stooq_unparsed`. Every word of that was true and almost none of it was for
a human. Diagnostics and prose had been written by the same code, so they got the same
weight — and once they are separated, the fix is obvious in both directions.

THE FAILURE LINE IS THE MAIN OFFENDER AND THE RULE IS NOT "HIDE IT". A digest that
silently omits oil reads exactly like one where oil did not move — that has not changed
and is why `sources` reports states at all. But a READER needs "prices were unavailable
this run", once, in English. The engineer's version keeps every code and lives in the run
log, which is where somebody debugging actually looks. Same fact, two audiences, two
places.

PLAIN TEXT IS GENERATED ALONGSIDE THE HTML AND IS NOT AN AFTERTHOUGHT. It is what a
screen reader gets, and this letter is read aloud.
"""

import html

# Grouped so one sentence can cover twenty symbols. Keyed on what the reader should DO,
# which is the only thing that makes a failure worth printing to them at all.
REASON_TEXT = {
    "rate_limited": "the free price sources throttled this run",
    "no_key": "needs a free API key that is not set yet",
    "not_covered": "this source does not carry that symbol",
    "other": "did not answer",
}


def classify(state):
    """A machine state -> the one word a reader needs. `state` may name several rungs."""
    s = (state or "").lower()
    if "429" in s:
        return "rate_limited"
    if "no_key" in s:
        return "no_key"
    if "not_covered" in s:
        return "not_covered"
    return "other"


def human_name(name, facts):
    """
    A source's own code -> what a person calls it.

    `DCOILWTICO` is the same class of problem as `yahoo_http_429`: a machine name that
    reached the reader because nobody translated it. The watchlist already carries the
    mapping — it is what the series were named in the config — so this is a lookup, not
    a second hand-written list that can drift from the first.
    """
    return (facts.get("macro_labels") or {}).get(name, name)


def data_notes(facts):
    """
    [str] — at most one short sentence per REASON, never one per symbol.

    The old version printed a clause per failure and there are twenty of those on a bad
    run, so the note was longer than the letter it was attached to.
    """
    buckets = {}
    for group in ("quote_failures", "macro_failures", "news_failures"):
        for name, state in (facts.get(group) or {}).items():
            buckets.setdefault(classify(state), set()).add(name)
    if facts.get("index_state") not in (None, "ok"):
        buckets.setdefault(classify(facts.get("index_state")), set()).add("S&P 500")

    notes = []
    for reason, names in sorted(buckets.items()):
        shown = sorted(human_name(n, facts) for n in names)
        if len(shown) > 4:
            who = f"{', '.join(shown[:4])} and {len(shown) - 4} more"
        else:
            who = ", ".join(shown)
        notes.append(f"{who}: {REASON_TEXT[reason]}.")
    return notes


def _arrow(pct):
    return "▲" if pct > 0 else ("▼" if pct < 0 else "—")


def _colour(pct):
    return "#1a7f37" if pct > 0 else ("#c0392b" if pct < 0 else "#57606a")


def subject(mode, facts, verdict):
    """
    The subject line carries the number, because half of email is read in the list view.

    "Daily market note" told you nothing you did not already know from the schedule.
    """
    names = {"daily": "Daily note", "weekahead": "The week ahead",
             "weekly": "Week in review", "check": "Market alert"}
    base = names.get(mode, "Market note")
    idx = facts.get("index") or {}
    if verdict and verdict.get("fired_levels"):
        return f"{base} — S&P hit your {verdict['fired_levels'][-1]}% trigger"
    if idx.get("drawdown_pct") is not None and idx.get("close"):
        return f"{base} — S&P {idx['drawdown_pct']:+.1f}% from its high"
    movers = (verdict or {}).get("movers") or []
    if movers:
        m = movers[0]
        return f"{base} — {m['symbol']} {m['change_pct']:+.1f}%"
    return base


def plain(mode, facts, verdict, note):
    """The screen-reader version. Prose and short lines, no boxes, no code."""
    out = []
    idx = facts.get("index") or {}
    if idx.get("close"):
        out.append(f"S&P 500 at {idx['close']:,.0f}, "
                   f"{idx['drawdown_pct']:+.1f}% from its record high of {idx['high']:,.0f}.")
    for lvl in (verdict or {}).get("fired_levels", []):
        out.append(f"That crosses your {lvl} percent buy trigger.")
    if not (verdict or {}).get("fired_levels") and idx.get("next_trigger"):
        out.append(f"Your next buy is at {idx['next_trigger']} percent down, "
                   f"a further {idx['to_next_trigger']:.1f} percent fall from here.")
    held = [q for q in facts.get("quotes", [])
            if q["symbol"] in {p["symbol"] for p in facts.get("positions", [])
                               if p.get("status") == "held"}]
    if held:
        out.append("")
        out.append("Your holdings: " + "; ".join(
            f"{q['symbol']} at {q['price']:,.2f}, {q['change_pct']:+.1f} percent"
            for q in held) + ".")
    if note:
        out.extend(["", note])
    notes = data_notes(facts)
    if notes:
        out.extend(["", "Data notes: " + " ".join(notes)])
    return "\n".join(out)


def _row(q):
    c = _colour(q["change_pct"])
    return (f'<tr>'
            f'<td style="padding:6px 12px 6px 0;font-weight:600;">{html.escape(q["symbol"])}</td>'
            f'<td style="padding:6px 12px 6px 0;text-align:right;">{q["price"]:,.2f}</td>'
            f'<td style="padding:6px 0;text-align:right;color:{c};white-space:nowrap;">'
            f'{_arrow(q["change_pct"])} {q["change_pct"]:+.2f}%</td></tr>')


def rich(mode, facts, verdict, note):
    """
    The HTML letter. Inline styles only — every mail client strips a stylesheet.

    ONE COLUMN, LEFT ALIGNED, SYSTEM FONTS. Not a design exercise: a newsletter read on a
    phone at seven in the morning wants to be scannable in four seconds and legible at
    arm's length, and anything fancier breaks in one client or another.
    """
    idx = facts.get("index") or {}
    parts = ['<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\','
             'Roboto,Helvetica,Arial,sans-serif;font-size:16px;line-height:1.55;'
             'color:#1f2328;max-width:620px;margin:0 auto;padding:8px 4px;">']

    # ── the headline number, which is what the letter is about ──────────────────────
    if idx.get("close"):
        dd = idx["drawdown_pct"]
        fired = (verdict or {}).get("fired_levels") or []
        box = "#fff8c5" if fired else "#f6f8fa"
        edge = "#d4a72c" if fired else "#d0d7de"
        parts.append(
            f'<div style="background:{box};border:1px solid {edge};border-radius:8px;'
            f'padding:14px 16px;margin:0 0 20px;">'
            f'<div style="font-size:13px;color:#57606a;letter-spacing:.04em;'
            f'text-transform:uppercase;">S&amp;P 500</div>'
            f'<div style="font-size:26px;font-weight:700;margin:2px 0;">'
            f'{idx["close"]:,.0f}</div>'
            f'<div style="color:{_colour(dd)};font-weight:600;">'
            f'{dd:+.1f}% from its record of {idx["high"]:,.0f}</div>'
            + (f'<div style="margin-top:8px;font-weight:700;color:#7a5900;">'
               f'This crosses your {fired[-1]}% buy trigger.</div>' if fired else "")
            + (f'<div style="margin-top:8px;color:#57606a;font-size:14px;">'
               f'Your next buy is at {idx["next_trigger"]}% down — a further '
               f'{idx["to_next_trigger"]:.1f}% fall from here.</div>'
               if not fired and idx.get("next_trigger") else "")
            + '</div>')

    # ── holdings, as a scannable block ──────────────────────────────────────────────
    held_syms = {p["symbol"] for p in facts.get("positions", []) if p.get("status") == "held"}
    watch_syms = {p["symbol"] for p in facts.get("positions", []) if p.get("status") == "watching"}
    held = [q for q in facts.get("quotes", []) if q["symbol"] in held_syms]
    watching = [q for q in facts.get("quotes", []) if q["symbol"] in watch_syms]
    for title, rows in (("Holdings", held), ("Watching", watching)):
        if not rows:
            continue
        parts.append(f'<div style="font-size:13px;color:#57606a;letter-spacing:.04em;'
                     f'text-transform:uppercase;margin:0 0 6px;">{title}</div>'
                     f'<table style="border-collapse:collapse;width:100%;'
                     f'margin:0 0 20px;font-variant-numeric:tabular-nums;">'
                     + "".join(_row(q) for q in rows) + '</table>')

    # ── the written note ────────────────────────────────────────────────────────────
    if note:
        body = "".join(
            f'<p style="margin:0 0 14px;">{html.escape(p)}</p>'
            for p in note.split("\n\n") if p.strip())
        parts.append(f'<div style="border-top:1px solid #d0d7de;padding-top:16px;">'
                     f'{body}</div>')

    # ── and the data notes, small, at the end, in English ───────────────────────────
    notes = data_notes(facts)
    if notes:
        parts.append(
            '<div style="margin-top:18px;padding-top:12px;border-top:1px solid #eaeef2;'
            'font-size:13px;color:#6e7781;">'
            + "".join(f'<div>{html.escape(n)}</div>' for n in notes)
            + '</div>')
    parts.append("</div>")
    return "".join(parts)
