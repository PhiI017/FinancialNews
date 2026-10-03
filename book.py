"""
book.py — what the positions are WORTH, not just what they cost today.

The letter already knows every price. This turns that into a book: value per position,
the day's change in money, and each position's WEIGHT.

────────────────────────────────────────────────────────────────────────────────────
WHY WEIGHT IS THE NUMBER, AND WHY IT IS HERE AT ALL

This was asked for as FRICTION. The brokerage app comes off the phone and stays on a
computer that is five days away; for those five days this letter is the only window onto
the book, so it has to be complete enough that nobody feels the need to reinstall the app
to find something out. That is the whole design brief: not to inform, but to make checking
unnecessary.

**Weight is the one figure that argues against buying more.** A price ticker never does —
green says "going up", red says "cheap", and both read as reasons to add. "AMZN is 31% of
the book" is a sentence with an opinion in it, and it is the opinion that is usually right.

────────────────────────────────────────────────────────────────────────────────────
AN UNSIZED POSITION IS NOT A POSITION WORTH ZERO

`BTC-USD` is held and has no share count, because the screenshot the counts came from was
the Stocks & ETFs tab. Counting it as zero would understate the total and OVERSTATE every
weight computed against that total — and the error would look entirely reasonable, which
is the shape that does the damage. So the total is marked incomplete, the unsized names
are listed by name, and the weights say what they are a percentage OF.

NOTHING HERE IS A COST BASIS. The brokerage was not asked for one and none is stored, so
there is no profit, no loss and no return in this module. A made-up basis would be the
most believable wrong number on the page.
"""

UNSIZED = "unsized"


def value(positions, quotes):
    """
    (rows, total, unsized) — rows are dicts, newest facts only, nothing invented.

    `rows` carries symbol, shares, price, value, day change in money, and weight. `total`
    is the sum of what COULD be valued. `unsized` lists held symbols with no share count,
    which is why `total` may be incomplete.
    """
    px = {q["symbol"]: q for q in quotes or []}
    rows, total, unsized = [], 0.0, []
    for p in positions or []:
        if p.get("status") != "held":
            continue
        sym = p["symbol"]
        shares = p.get("shares")
        q = px.get(sym)
        if shares is None:
            unsized.append(sym)
            continue
        if not q or q.get("price") is None:
            # A MISSING PRICE IS NOT A ZERO VALUE EITHER. The position is real; the quote
            # did not arrive, and the letter already reports a failed fetch in its data
            # notes. Folding it in at zero would quietly shrink the book.
            unsized.append(sym)
            continue
        v = float(shares) * float(q["price"])
        chg = q.get("change_pct")
        rows.append(dict(
            symbol=sym, shares=float(shares), price=float(q["price"]), value=v,
            change_pct=chg,
            # the day's move in MONEY, which is what a percentage hides on a small position
            day_money=(v - v / (1 + chg / 100.0)) if chg not in (None, -100) else None))
        total += v
    for r in rows:
        r["weight_pct"] = (r["value"] / total * 100.0) if total else None
    rows.sort(key=lambda r: -r["value"])
    return rows, total, unsized


def concentration(rows, top=3):
    """What share of the book the biggest `top` positions are. None if there is no book."""
    if not rows:
        return None
    return sum(r["weight_pct"] or 0.0 for r in rows[:top])


def lines(rows, total, unsized, top=3):
    """
    Plain-English sentences for the text letter and the LLM's context.

    SENTENCES, NOT A GRID. The letter is read on a phone and some of it is read ALOUD, and
    a table of columns does not survive either.
    """
    if not rows:
        return ["No position in the book could be valued — see the data notes."]
    out = []
    day = sum(r["day_money"] for r in rows if r["day_money"] is not None)
    out.append(f"The book is worth ${total:,.0f}" + (
        f", and moved {'up' if day >= 0 else 'down'} ${abs(day):,.0f} today." if day else "."))
    big = rows[0]
    out.append(f"{big['symbol']} is the largest holding at {big['weight_pct']:.0f}% "
               f"(${big['value']:,.0f}).")
    conc = concentration(rows, top)
    if conc is not None and len(rows) > top:
        out.append(f"The top {top} are {conc:.0f}% of it.")
    if unsized:
        out.append(f"NOT INCLUDED, because no share count is recorded: "
                   f"{', '.join(unsized)}. Every percentage above is a share of the rest.")
    return out
