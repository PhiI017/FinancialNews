"""
xfetch.py — read X with a session cookie, or say precisely why it could not.

WRITTEN AS A PROBE FIRST, ON PURPOSE. X's web client talks to GraphQL endpoints whose
query ids rotate, and it gates almost everything behind authentication. Whether a cookie
from a browser works from a GitHub runner is an empirical question, and the honest way to
answer it is to try each route and report where the funnel stopped — the same ladder
`captions.py --probe` uses, and the same reason: guessing produced four wrong theories
about YouTube before a probe produced one right answer.

    python xfetch.py --probe              can this machine read X at all?
    python xfetch.py --user HANDLE        recent posts from one account

── THE CREDENTIAL ───────────────────────────────────────────────────────────────────

`X_COOKIES` holds the raw cookie header from a logged-in browser. Two values inside it
matter: `auth_token` is the session, and `ct0` is the CSRF token that must ALSO be sent
as a header — a request with the cookie but not the header is rejected, and that is the
single most common reason this appears broken when the credential is fine.

IT IS NEVER PRINTED, NEVER LOGGED, AND NEVER RETURNED. Only whether it was present and
which fields were found. A probe that echoes the thing it is testing is a probe that
leaks it into a build log.

── AND WHAT THIS COSTS YOU, STATED BECAUSE IT IS NOT OBVIOUS ────────────────────────

An X session cookie is full account access — posting, DMs, settings. Unlike an app
password it cannot be scoped. It also rotates: expect this to stop working and need a
re-export, which is the property that made the YouTube cookie route not worth keeping.
Automating X is against their terms; suspension is the downside.
"""

import json
import os
import re
import sys
import urllib.parse

import sources

# The public web client's bearer. Not a secret — it ships in X's own JavaScript and is
# the same for every anonymous browser. The SESSION is the cookie; this just identifies
# the client, and without it the endpoints reject the request outright.
WEB_BEARER = ("Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
              "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")

ROUTES = {
    # The old REST timeline. Long deprecated; tried first because if it answers it is by
    # far the simplest thing that could work.
    "v1_timeline": "https://api.x.com/1.1/statuses/user_timeline.json?screen_name={handle}&count={n}",
    # The syndication widget, which is what embedded timelines use. No auth in theory.
    "syndication": "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
    # The public profile page, parsed for embedded JSON.
    "profile_html": "https://x.com/{handle}",
}


def credential():
    """
    (headers, state) — the auth headers, or why there are none. NEVER the cookie itself.
    """
    raw = os.getenv("X_COOKIES", "").strip()
    if not raw:
        return None, "no_cookie"
    ct0 = re.search(r"ct0=([0-9a-fA-F]+)", raw)
    auth = re.search(r"auth_token=([0-9a-fA-F]+)", raw)
    if not auth:
        # NAMED SEPARATELY because it is a different fix: the export was incomplete, not
        # expired. Telling someone "auth failed" when the token was never there sends
        # them to re-log-in instead of to re-copy.
        return None, "cookie_has_no_auth_token"
    if not ct0:
        return None, "cookie_has_no_ct0"
    return {
        "Cookie": raw,
        "Authorization": WEB_BEARER,
        "x-csrf-token": ct0.group(1),
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "Accept": "*/*",
    }, "ok"


def describe_credential():
    """What was found, WITHOUT the value. Safe to print in a build log."""
    raw = os.getenv("X_COOKIES", "")
    if not raw:
        return "X_COOKIES is not set"
    return (f"X_COOKIES present, {len(raw)} chars, "
            f"auth_token {'found' if 'auth_token=' in raw else 'MISSING'}, "
            f"ct0 {'found' if 'ct0=' in raw else 'MISSING'}")


def try_route(name, handle, headers, n=10):
    """(count_or_None, state) — one rung. Never raises, never prints the credential."""
    url = ROUTES[name].format(handle=urllib.parse.quote(handle), n=n)
    body, state = sources._get(url, headers=headers, retries=1)
    if state != "ok":
        return None, state
    text = body.decode("utf-8", "replace")

    # A LOGIN WALL ANSWERS 200. That is the whole difficulty here and the reason a status
    # code is not evidence: X serves its sign-in page with a success code, so a route
    # that "worked" can contain nothing but a request to log in.
    low = text[:4000].lower()
    if "login" in low and ("sign in to x" in low or "js_instrumentation" in low):
        return None, "login_wall"

    if name == "v1_timeline":
        try:
            data = json.loads(text)
        except Exception:
            return None, "unparsed"
        if isinstance(data, dict) and data.get("errors"):
            return None, f"api_error_{data['errors'][0].get('code', '?')}"
        return len(data) if isinstance(data, list) else None, "ok"

    # For the HTML routes, count the post-shaped objects in whatever JSON is embedded.
    hits = len(re.findall(r'"full_text"\s*:', text)) or len(re.findall(r'"text"\s*:', text))
    if not hits:
        return None, "no_posts_found"
    return hits, "ok"


def probe(handle="AP"):
    """Try every route and print where each stopped. Writes nothing, sends nothing."""
    print(f"\n  {describe_credential()}")
    headers, state = credential()
    if state != "ok":
        print(f"  cannot build auth headers: {state}\n")
        if state == "cookie_has_no_ct0":
            print("  The export is missing ct0, which X requires as a HEADER as well as")
            print("  a cookie. Re-copy the whole cookie string, not just auth_token.\n")
        return 1

    print(f"  probing against @{handle}\n")
    print(f"  {'route':<16}{'state':<24}{'posts'}")
    print(f"  {'-' * 50}")
    worked = []
    for name in ROUTES:
        count, st = try_route(name, handle, headers)
        print(f"  {name:<16}{st:<24}{count if count is not None else '-'}")
        if st == "ok" and count:
            worked.append(name)

    print()
    if worked:
        print(f"  {worked[0]} answered. That is the route to build on.")
    else:
        print("  NO ROUTE ANSWERED. Read the states above before changing anything:")
        print("    login_wall      the cookie did not authenticate this request")
        print("    http_403/401    rejected outright — usually a rotated session")
        print("    http_429        rate limited, which a retry may clear")
        print("    no_posts_found  reached the page and the shape changed")
    return 0 if worked else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--user" in argv:
        i = argv.index("--user")
        who = argv[i + 1] if len(argv) > i + 1 else "AP"
        headers, state = credential()
        if state != "ok":
            print(f"  {state}")
            sys.exit(1)
        for name in ROUTES:
            count, st = try_route(name, who, headers)
            print(f"  {name}: {st} ({count})")
        sys.exit(0)
    sys.exit(probe(argv[argv.index("--probe") + 1]
                   if "--probe" in argv and len(argv) > argv.index("--probe") + 1
                   else "AP"))
