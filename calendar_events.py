"""
calendar_events.py — what is coming, for the week-ahead and week-in-review letters.

DECLARED, NOT FETCHED, AND THAT IS A DECISION RATHER THAN A SHORTCUT. There is no free,
reliable economic-calendar API, and a newsletter that says "CPI is Tuesday" when it is
Thursday is worse than one that says nothing: it is confidently wrong about the single
thing you would have acted on. So the dates live in `catalysts.json`, where they are easy
to read and easy to correct.

THE FILE GOING STALE IS ITSELF REPORTED. An empty week-ahead can mean "a quiet week" or
"nobody has added a date since October", and those are opposite. `horizon()` returns the
date of the last event it knows about so the letter can say which it is.
"""

import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "catalysts.json")


def load(path=None):
    with open(path or PATH) as fh:
        return json.load(fh).get("events", [])


def upcoming(within_days=7, today=None, events=None):
    """[event] falling in the next `within_days`, soonest first."""
    today = today or dt.date.today()
    end = today + dt.timedelta(days=within_days)
    out = []
    for e in (events if events is not None else load()):
        try:
            when = dt.date.fromisoformat(e["date"])
        except (ValueError, KeyError):
            continue                      # a malformed date is skipped, never guessed
        if today <= when <= end:
            out.append({**e, "days_away": (when - today).days})
    return sorted(out, key=lambda e: e["date"])


def horizon(today=None, events=None):
    """
    (last_known_date, days_of_runway, is_stale) — how far ahead the file actually sees.

    A WEEK-AHEAD LETTER WITH NOTHING IN IT IS AMBIGUOUS and this resolves it. Runway below
    a fortnight means the calendar needs dates, not that the market is quiet.
    """
    today = today or dt.date.today()
    dates = []
    for e in (events if events is not None else load()):
        try:
            dates.append(dt.date.fromisoformat(e["date"]))
        except (ValueError, KeyError):
            continue
    if not dates:
        return None, 0, True
    last = max(dates)
    runway = (last - today).days
    return last.isoformat(), runway, runway < 14


def render(today=None, within_days=7):
    """(text, stale_warning) for the prompt and for the plain-text fallback."""
    events = upcoming(within_days, today=today)
    last, runway, stale = horizon(today=today)
    lines = []
    for e in events:
        when = "today" if e["days_away"] == 0 else (
            "tomorrow" if e["days_away"] == 1 else f"in {e['days_away']} days")
        lines.append(f"  {e['date']} ({when}): {e['what']} — {e['why_it_matters']}")
    warning = ""
    if stale:
        warning = (f"THE CATALYST CALENDAR ONLY REACHES {last or 'nowhere'} "
                   f"({runway} days). An empty look-ahead below means the file needs "
                   f"dates, not that the week is quiet — edit alerts/catalysts.json.")
    return ("\n".join(lines) if lines else "  (nothing dated in this window)"), warning
