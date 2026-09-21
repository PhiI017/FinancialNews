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

import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
TIMEOUT = 20

# ntfy renders these as a priority and an icon. Mapped from our own urgency words so the
# vocabulary stays in one place rather than being spelled out at each call site.
PRIORITY = {"urgent": "urgent", "important": "default", "quiet": "low"}
TAGS = {"urgent": "rotating_light", "important": "chart_with_upwards_trend", "quiet": "leaves"}


def random_topic(words=4):
    """A topic name that is not worth guessing. Printed by `alerter.py --setup`."""
    import secrets
    return "edgewise-" + secrets.token_urlsafe(words * 4).replace("_", "").replace("-", "")[:22]


def push(title, body, level="important", topic=None, click=None):
    """(ok, state) — one ntfy notification."""
    topic = topic or os.getenv("NTFY_TOPIC", "")
    if not topic:
        return False, "not_configured"
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": PRIORITY.get(level, "default"),
        "Tags": TAGS.get(level, "bell"),
    }
    if click:
        headers["Click"] = click
    req = urllib.request.Request(
        f"{NTFY_SERVER.rstrip('/')}/{topic}",
        data=body.encode("utf-8"),
        headers={k: (v.decode() if isinstance(v, bytes) else v) for k, v in headers.items()},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return (200 <= r.status < 300), f"http_{r.status}"
    except Exception as e:
        return False, type(e).__name__


def email(subject, body, to=None):
    """(ok, state) — one plain-text email."""
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


def send(subject, body, level="important", channels=("ntfy", "email")):
    """
    {channel: state} — deliver to each channel, independently.

    ONE CHANNEL'S FAILURE NEVER STOPS THE OTHER. That is the whole reason for two, and a
    loop that raised on the first would deliver the opposite of what it promised.
    """
    out = {}
    if "ntfy" in channels:
        _, out["ntfy"] = push(subject, body, level=level)
    if "email" in channels:
        _, out["email"] = email(subject, body)
    return out


def configured():
    """{channel: bool} — what this machine can actually send, for the setup check."""
    return {
        "ntfy": bool(os.getenv("NTFY_TOPIC")),
        "email": bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS")),
    }
