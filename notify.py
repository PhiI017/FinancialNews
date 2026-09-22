"""
notify.py — ntfy for the push, email for the digests. Both free, neither required.

WHY TWO CHANNELS AND NOT ONE. They fail differently and they are good at different
things. ntfy reaches a phone in seconds and is terrible at four hundred words; email is
the opposite. So urgent goes to ntfy, digests go to email, and the caller can send one
thing to both when it wants to.

EVERY SEND RETURNS (ok, state) AND NOTHING RAISES. A crash inside the notifier would take
down the run that was trying to tell you something, which is the worst possible moment to
lose. So a failed send is reported and the other channel still gets its copy.

── WHAT YOU NEED, AND IT IS GENUINELY FREE ──────────────────────────────────────────

ntfy    install the app, pick a topic, set NTFY_TOPIC. No account, no key.
        USE A LONG RANDOM TOPIC. The topic name IS the credential on the public server —
        anyone who guesses it reads your alerts. `random_topic()` prints a usable one.

email   Gmail with an APP PASSWORD (not your real password; needs 2FA turned on, at
        myaccount.google.com/apppasswords). Set SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO.
        Any SMTP host works — override SMTP_HOST and SMTP_PORT.

Absent settings are reported as `not_configured`, never as success.
"""

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
TIMEOUT = 20

# ntfy renders these as a priority and an icon. Mapped from our own urgency words so the
# vocabulary stays in one place rather than being spelled out at each call site.
#
# THE PRIORITIES ARE NUMBERS, NOT WORDS, because this publishes as JSON. The header API
# accepts "urgent"; the JSON API accepts 1-5 and silently ignores a string, which would
# have downgraded every urgent alert to default without failing.
PRIORITY = {"urgent": 5, "important": 3, "quiet": 2}
TAGS = {"urgent": "rotating_light", "important": "chart_with_upwards_trend", "quiet": "leaves"}


def random_topic(words=4):
    """A topic name that is not worth guessing. Printed by `alerter.py --setup`."""
    import secrets
    return "edgewise-" + secrets.token_urlsafe(words * 4).replace("_", "").replace("-", "")[:22]


def push(title, body, level="important", topic=None, click=None):
    """
    (ok, state) — one ntfy notification, published as JSON.

    ── WHY JSON AND NOT THE HEADER API ──────────────────────────────────────────────

    MEASURED 2026-09-22: every push failed with `UnicodeEncodeError` and the email beside
    it went out fine. The title was "Daily note — S&P -0.4% from its high"; HTTP header
    values are latin-1 and an em-dash is not in latin-1. The letter-writing model puts one
    in most titles, so this was not an edge case — it was the whole channel, dead, while
    the run reported success everywhere else.

    Stripping the character would have worked and would have been the wrong fix: the next
    non-latin-1 character the model reaches for (a curly quote, a degree sign, a euro)
    brings it back. ntfy publishes to the server ROOT with the topic in a JSON body, so
    the text travels as UTF-8 payload and no header carries prose at all. The class of bug
    is removed rather than the instance.

    AND IT FAILED LOUDLY, WHICH IS THE ONLY REASON IT WAS FOUND — `delivery: {'email':
    'ok', 'ntfy': 'UnicodeEncodeError'}` in the log. A notifier that swallowed the
    exception would have looked identical to a quiet day.
    """
    topic = topic or os.getenv("NTFY_TOPIC", "")
    if not topic:
        return False, "not_configured"
    payload = {
        "topic": topic,
        "title": title,
        "message": body,
        "priority": PRIORITY.get(level, 3),
        "tags": [TAGS.get(level, "bell")],
    }
    if click:
        payload["click"] = click
    req = urllib.request.Request(
        NTFY_SERVER.rstrip("/") + "/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return (200 <= r.status < 300), f"http_{r.status}"
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except Exception as e:
        return False, type(e).__name__


def email(subject, body, to=None, html_body=None):
    """
    (ok, state) — one email, plain text plus an optional HTML alternative.

    MULTIPART, WITH PLAIN TEXT ALWAYS PRESENT AND ALWAYS FIRST. The HTML is what most
    clients show; the plain part is what a screen reader gets, what a text-only client
    gets, and what survives a client that strips styling. Sending HTML alone would make
    the letter unreadable aloud, which is how this one is actually read.
    """
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASS", "")
    to = to or os.getenv("ALERT_EMAIL_TO", "") or user
    if not (user and password and to):
        return False, "not_configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT,
                              context=ssl.create_default_context(), timeout=TIMEOUT) as s:
            s.login(user, password)
            s.send_message(msg)
        return True, "ok"
    except smtplib.SMTPAuthenticationError:
        # NAMED SEPARATELY BECAUSE THE FIX IS SPECIFIC AND THE GENERIC ERROR HIDES IT:
        # Gmail refuses a real password here. It wants an app password.
        return False, "auth_failed_use_app_password"
    except Exception as e:
        return False, type(e).__name__


def send(subject, body, level="important", channels=("ntfy", "email"), html_body=None):
    """
    {channel: state} — deliver to each channel, independently.

    ONE CHANNEL'S FAILURE NEVER STOPS THE OTHER. That is the whole reason for two, and a
    loop that raised on the first would deliver the opposite of what it promised.
    """
    out = {}
    if "ntfy" in channels:
        _, out["ntfy"] = push(subject, body, level=level)
    if "email" in channels:
        _, out["email"] = email(subject, body, html_body=html_body)
    return out


def configured():
    """{channel: bool} — what this machine can actually send, for the setup check."""
    return {
        "ntfy": bool(os.getenv("NTFY_TOPIC")),
        "email": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS")),
    }
