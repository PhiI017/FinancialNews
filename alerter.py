"""
alerter.py — the entry point. Four modes, one state file.

    python alerter.py --setup     what is configured, what is missing, and a safe topic
    python alerter.py --check     urgent scan. Arithmetic only. Cheap, run it often
    python alerter.py --accounts  the people you trust, pushed to your phone only
    python alerter.py --daily      note after the close
    python alerter.py --weekahead  Monday: what is coming this week
    python alerter.py --weekly     Sunday: the week in review, and next week
    python alerter.py --dry-run   with any of the above: print, send nothing, spend nothing

── WHY THE STATE FILE IS COMMITTED ──────────────────────────────────────────────────

`state.json` remembers which dip levels are spent and the highest close ever seen. A
runner starts from a fresh checkout every time, so state that lives only on disk is state
that does not exist — the -10% alert would fire again on every single run. Committing it
is the same decision `ptr_documents` makes in the stocks collector, for the same reason.

── WHAT RUNS WITHOUT ANY KEYS AT ALL ────────────────────────────────────────────────

Prices, the index drawdown and every dip trigger need no credentials. FRED needs a free
key, the summary needs an Anthropic key, and sending needs ntfy or SMTP — each is skipped
with a STATED reason and never silently. `--setup` prints exactly which of those you have.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accounts
import calendar_events
import letter
import notify
import sources
import summarize
import triggers

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "watchlist.json")
STATE = os.path.join(HERE, "state.json")


def load_watchlist():
    with open(WATCHLIST) as fh:
        return json.load(fh)


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"highest_close": 0.0, "fired_levels": [], "last_run": "", "history": []}


def save_state(state):
    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    state["history"] = state.get("history", [])[-60:]
    with open(STATE, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def active_positions(wl):
    return [p for p in wl["positions"] if p.get("status") != "muted"]


def gather(wl, want_news=True, want_macro=True):
    """Every fact, with every failure named. The one place that touches the network."""
    facts = {"date": time.strftime("%Y-%m-%d", time.gmtime()),
             "positions": active_positions(wl),
             "themes": wl.get("themes", []),
             "quotes": [], "quote_failures": {},
             "macro": {}, "macro_failures": {}, "macro_labels": wl["macro"]["series"],
             "headlines": [], "news_failures": {}}

    for pos in facts["positions"]:
        q, state = sources.quote(pos["symbol"])
        if state == "ok":
            facts["quotes"].append(q)
        else:
            facts["quote_failures"][pos["symbol"]] = state

    rows, state = sources.index_history(wl["index"]["symbol"])
    facts["index_state"] = state
    facts["index"] = {}
    if state == "ok":
        closes = [c for _d, c in rows]
        facts["index"] = {"close": closes[-1], "observed_high": max(closes)}
        # THE DAY'S MOVE, WHICH WAS MISSING AND GOT INVENTED IN ITS PLACE.
        #
        # Measured 2026-09-22: the letter said "the market barely moved today" and called
        # VOO "essentially flat" on a day the S&P closed up 1.49%. Nothing lied to it —
        # the only index number it had was the DRAWDOWN FROM THE RECORD, -0.4%, and it
        # read that small number as the day's change. Two different quantities that both
        # arrive as a small negative percentage, and only one of them is "today".
        #
        # The rows were already here; nobody had asked them the question. Same shape as
        # the trigger price: the model does not need better instructions about a number
        # it was never given.
        if len(closes) > 1 and closes[-2]:
            facts["index"]["prev_close"] = closes[-2]
            facts["index"]["change_pct"] = (closes[-1] / closes[-2] - 1.0) * 100.0
        facts["index"]["asof"] = rows[-1][0]

    if want_macro:
        for sid in wl["macro"]["series"]:
            rows, state = sources.fred_series(sid)
            if state == "ok":
                facts["macro"][sid] = {"latest": rows[-1][1], "previous": rows[-2][1]
                                       if len(rows) > 1 else rows[-1][1],
                                       "asof": rows[-1][0], "rows": rows}
            else:
                facts["macro_failures"][sid] = state

    if want_news:
        for pos in facts["positions"]:
            items, state = sources.headlines(pos["symbol"], limit=6)
            if state == "ok":
                facts["headlines"].extend(items)
            else:
                facts["news_failures"][pos["symbol"]] = state

        # AND THE BROAD FEEDS, which are the only ones that can carry a story arriving
        # from OUTSIDE the watchlist. A per-symbol feed cannot warn you about a Fed
        # decision or an oil shock, because neither one names your companies.
        limit = int(wl.get("feed_limit", 8))
        for label, url in (wl.get("feeds") or {}).items():
            items, state = sources.feed(url, label=label, limit=limit)
            if state == "ok":
                facts["headlines"].extend(items)
            else:
                facts["news_failures"][label] = state

        before = len(facts["headlines"])
        facts["headlines"] = sources.dedupe(facts["headlines"])
        facts["duplicates_removed"] = before - len(facts["headlines"])
    return facts


def evaluate(facts, wl, state):
    """Apply the triggers. Returns (verdict, updated_state). No network, no model."""
    idx = facts.get("index") or {}
    fired, rearmed, drawdown = [], [], 0.0
    if idx.get("close"):
        high, moved = triggers.ratchet_high(state.get("highest_close"), idx["observed_high"])
        state["highest_close"] = high
        idx["high"] = high
        idx["high_moved_up"] = moved
        drawdown, fired, rearmed = triggers.dip_state(
            idx["close"], high, wl["index"]["dip_levels_pct"], state.get("fired_levels", []))
        idx["drawdown_pct"] = drawdown
        idx["fired"] = fired[-1] if fired else None
        # BUY LEVELS ONLY — the 5% rung is an early warning, not a step in the plan, so
        # counting down to it would mean announcing a buy that is not one.
        nxt, gap = triggers.next_trigger(-drawdown, [l for l in wl["index"]["dip_levels_pct"]
                                                     if int(l) >= 10])
        idx["next_trigger"], idx["to_next_trigger"] = nxt, gap
        # THE TRIGGER'S ACTUAL PRICE, COMPUTED HERE AND NEVER BY THE MODEL.
        #
        # Measured 2026-09-22: given the level and the percentage but not the price, the
        # letter wrote "the S&P sits 34 points above your first staged buy trigger". 34
        # was the distance to the RECORD; the trigger was 746 points away. It had two
        # numbers and combined the wrong pair, and the sentence reads perfectly.
        #
        # This is the same rule the urgent path follows for a different reason:
        # arithmetic belongs in code. There it was about reliability, here it is about
        # a plausible wrong number being worse than no number.
        if nxt:
            idx["next_trigger_price"] = high * (1 - nxt / 100.0)
            idx["points_to_trigger"] = idx["close"] - idx["next_trigger_price"]
        spent = set(state.get("fired_levels", [])) | set(fired)
        spent -= set(rearmed)
        state["fired_levels"] = sorted(spent)

    big = triggers.movers(facts["quotes"], wl.get("overrides", {}))
    # TWO LISTS, ON PURPOSE. `big` is every move of the session and is what the letter
    # describes; `push_worthy` is what has not already been sent to a phone. Collapsing
    # them would empty the digest along with the duplicate alerts.
    session = next((q.get("asof") for q in facts["quotes"] if q and q.get("asof")), "")
    push_worthy, state["movers_alerted"] = triggers.unreported_movers(
        big, state.get("movers_alerted"), session)
    macro_rows = {sid: row["rows"] for sid, row in (facts.get("macro") or {}).items()}
    macro_hits = triggers.macro_moves(macro_rows, wl["macro"].get("thresholds", {}))

    return {"urgency": triggers.urgency(fired, push_worthy, macro_hits),
            "fired_levels": fired, "rearmed_levels": rearmed,
            "drawdown_pct": drawdown, "movers": big, "push_movers": push_worthy,
            "macro_hits": macro_hits}, state


def urgent_text(verdict, facts):
    """Short enough for a phone notification. Numbers, not prose."""
    bits = []
    for level in verdict["fired_levels"]:
        bits.append(f"S&P is {abs(verdict['drawdown_pct']):.1f}% below its high "
                    f"— your {level}% trigger.")
    for m in verdict.get("push_movers", verdict["movers"]):
        bits.append(f"{m['symbol']} {m['change_pct']:+.1f}% today.")
    for h in verdict["macro_hits"]:
        label = facts.get("macro_labels", {}).get(h["series"], h["series"])
        bits.append(f"{label} {h['change']:+.2f} to {h['latest']}.")
    return "\n".join(bits) or "Nothing triggered."


def failures_line(facts):
    """WHAT DID NOT ANSWER, ALWAYS PRINTED. A quiet digest and a broken one look alike."""
    out = []
    for sym, st in (facts.get("quote_failures") or {}).items():
        out.append(f"no price for {sym} ({st})")
    for sid, st in (facts.get("macro_failures") or {}).items():
        out.append(f"no {sid} ({st})")
    for name, st in (facts.get("news_failures") or {}).items():
        out.append(f"no news from {name} ({st})")
    if facts.get("index_state") != "ok":
        out.append(f"no S&P history ({facts.get('index_state')})")
    return ("Not retrieved this run: " + "; ".join(out)) if out else ""


def run(mode, dry_run=False):
    wl = load_watchlist()
    state = load_state()
    wants_note = mode in ("daily", "weekahead", "weekly")
    facts = gather(wl, want_news=wants_note, want_macro=True)
    verdict, state = evaluate(facts, wl, state)

    # HOW FAR AHEAD EACH LETTER LOOKS. The Monday letter is about the week in front of
    # it; Sunday's covers the week that starts tomorrow; the daily one only flags
    # something landing within a couple of days, or it becomes a weekly letter every day.
    window = {"daily": 2, "weekahead": 8, "weekly": 9}.get(mode, 0)
    if window:
        facts["catalysts_text"], facts["catalysts_warning"] = \
            calendar_events.render(within_days=window)

    note, note_state = (None, "not requested")
    if wants_note and not dry_run:
        note, note_state = summarize.summarize(facts, kind=mode)
    elif wants_note:
        note_state = "skipped (dry run — nothing spent)"

    # THE SUBJECT CARRIES THE NUMBER, because half of email is read in the list view and
    # "Daily market note" says nothing the schedule had not already said.
    subject = letter.subject(mode, facts, verdict)
    html_body = None
    if mode == "check":
        body = urgent_text(verdict, facts)
        notes = letter.data_notes(facts)
        if notes:
            body += "\n\n" + " ".join(notes)
    else:
        body = letter.plain(mode, facts, verdict, note or f"[no written summary: {note_state}]")
        html_body = letter.rich(mode, facts, verdict, note)

    # THE ENGINEER'S VERSION STILL EXISTS AND STILL NAMES EVERY STATE — it goes to the
    # run log, which is where somebody debugging looks. What changed is that it stopped
    # being pasted into a letter meant for a person.
    failed = failures_line(facts)

    print(f"--- {mode} / {verdict['urgency']} ---\n{subject}\n{body}\n")
    if failed:
        print(failed + "\n")
    if wants_note:
        print(f"summary: {note_state}")

    if dry_run:
        print("DRY RUN — nothing sent, nothing spent, state not written.")
        return 0

    sent = {}
    if mode == "check":
        # A QUIET CHECK SENDS NOTHING AT ALL. An alerter that pings you to say nothing
        # happened is one you mute, and a muted alerter misses the day that matters.
        if verdict["urgency"] == "urgent":
            sent = notify.send(subject, body, level="urgent", channels=("ntfy", "email"))
        else:
            print("quiet — nothing sent")
    else:
        # THE LETTERS ARE EMAIL. A 450-word newsletter on a phone notification is
        # unreadable, and pushing one every weekday is how the urgent channel — which
        # shares the app — gets muted. ntfy carries a one-line pointer instead.
        sent = notify.send(subject, body, level="important", channels=("email",),
                           html_body=html_body)
        head = body.split("\n\n")[0][:180]
        sent["ntfy"] = notify.push(subject, head + "\n(full letter in your email)",
                                   level="quiet")[1]
    if sent:
        print("delivery:", sent)

    state.setdefault("history", []).append(
        {"at": facts["date"], "mode": mode, "urgency": verdict["urgency"],
         "drawdown_pct": round(verdict["drawdown_pct"], 2),
         "fired": verdict["fired_levels"], "sent": sent})
    save_state(state)
    return 0


def run_accounts(dry_run=False):
    """
    Check the trusted accounts and push anything new that touches a position.

    PHONE ONLY, AND NEVER EMAIL. These are single posts, not letters — a notification is
    the right shape and an inbox is not. It is also the one mode that can send several
    times a day, which is exactly why `max_per_run` exists.
    """
    wl = load_watchlist()
    state = load_state()
    cfg = accounts.load()
    if not (cfg.get("bluesky") or []):
        print("  no accounts listed yet — add handles to accounts.json")
        return 0

    posts, seen, failures = accounts.check(wl, state.get("seen_posts", []), cfg)
    for handle, why in failures.items():
        print(f"  {handle}: {why}")
    if not posts:
        print("  nothing new that touches a position")
        return 0

    body = accounts.render(posts)
    print(body)
    if dry_run:
        print("DRY RUN — nothing sent, state not written.")
        return 0

    sent = notify.push(f"{len(posts)} post(s) worth seeing", body, level="important",
                       click=posts[0]["url"])
    print("delivery:", sent[1])
    # THE SEEN LIST IS TRIMMED, or it grows forever and the state file with it. A post
    # older than the last few hundred cannot come back round anyway.
    state["seen_posts"] = sorted(seen)[-500:]
    save_state(state)
    return 0


def setup():
    """What this machine can do, and what it is missing. Sends nothing."""
    wl = load_watchlist()
    have = notify.configured()
    ok_llm, why = summarize.available()
    print("\n  WATCHING")
    for p in active_positions(wl):
        print(f"    {p['symbol']:<9} {p['status']}")
    print(f"    S&P dip levels: {wl['index']['dip_levels_pct']}")
    print("\n  CHANNELS")
    print(f"    ntfy   {'ready' if have['ntfy'] else 'NOT SET — set NTFY_TOPIC'}")
    print(f"    email  {'ready' if have['email'] else 'NOT SET — set SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO'}")
    print("\n  OPTIONAL")
    print(f"    FRED   {'ready' if os.getenv('FRED_API_KEY') else 'NOT SET — oil, yields and Fed rate will be skipped'}")
    print(f"    LLM    {'ready — ' + summarize.MODEL if ok_llm else 'NOT SET — ' + why}")
    if not have["ntfy"]:
        print(f"\n  A topic nobody will guess: {notify.random_topic()}")
    print(f"\n  {summarize.estimate_cost(3000, 700)[1]}\n")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    if "--setup" in argv:
        sys.exit(setup())
    if "--accounts" in argv:
        sys.exit(run_accounts(dry_run=dry))
    if "--probe" in argv:
        # A SURVEY, NOT A FETCH. It sends nothing, spends nothing and changes no state —
        # it reports which keyless price routes answer from the machine it runs on,
        # because the container this was written in cannot reach any of them.
        import pricefinder
        pricefinder.probe()
        sys.exit(0)
    for flag in ("check", "daily", "weekahead", "weekly"):
        if f"--{flag}" in argv:
            sys.exit(run(flag, dry_run=dry))
    print(__doc__.split("──")[0].rstrip())
