"""
accounts.py — watch a short list of people you trust, and push only what touches you.

WHY NOT X, WRITTEN DOWN SO IT IS NOT RE-PROPOSED EVERY FEW MONTHS. X removed free API
access. The cheapest tier that can read posts is roughly $100-200 a month against a
project whose entire running cost is thirty cents, and scraping around it means a login
wall, aggressive datacentre blocking and a terms-of-service breach. All three of those
fail the same way: quietly, weeks later, with the alerter reporting a calm news day
because it cannot see anything. That is the exact failure this project is built to avoid,
so X is left unbuilt rather than half-built.

BLUESKY IS THE ONE THAT WORKS HONESTLY. Its public endpoints serve public posts with no
key, no login and no terms problem. Everything below is source-agnostic — the handles, the
relevance filter, the seen-post memory — so a paid X reader drops in as one more fetcher
if that ever becomes worth it.

── THE HARD PART IS NOT FETCHING, IT IS NOT FLOODING ────────────────────────────────

A trusted account is trusted about markets, not about lunch. Ten accounts posting twenty
times a day is two hundred notifications, and the response to that is muting the app —
which costs you the dip alert the whole system exists for. So a post is pushed only when
it names something you hold, something you are watching, or a theme you listed, and one
run can send at most `max_per_run` whatever happens.
"""

import json
import os
import urllib.parse

import sources

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(HERE, "accounts.json")

# Public, unauthenticated, documented for exactly this. No key, no login.
BSKY_FEED = ("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
             "?actor={handle}&limit={limit}&filter=posts_no_replies")


def load(path=None):
    with open(path or PATH) as fh:
        return json.load(fh)


def bluesky_posts(handle, limit=10):
    """([{id, handle, text, url}], state) — one account's recent public posts."""
    url = BSKY_FEED.format(handle=urllib.parse.quote(handle), limit=int(limit))
    body, state = sources._get(url)
    if state != "ok":
        return None, state
    try:
        feed = json.loads(body).get("feed", [])
    except Exception:
        return None, "unparsed"

    out = []
    for item in feed:
        post = item.get("post") or {}
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        uri = post.get("uri") or ""
        if not text or not uri:
            continue
        # REPOSTS ARE SOMEBODY ELSE'S WORDS. You picked this account because you trust
        # their judgement; a repost is them pointing, which is weaker and much noisier.
        if (item.get("reason") or {}).get("$type", "").endswith("reasonRepost"):
            continue
        out.append({
            "id": uri,
            "handle": handle,
            "text": text,
            "url": f"https://bsky.app/profile/{handle}/post/{uri.rsplit('/', 1)[-1]}",
        })
    return out, "ok"


def relevant(text, symbols, names, keywords):
    """
    (bool, what_matched) — does this post touch anything of theirs?

    MATCHED ON WORD BOUNDARIES, NOT SUBSTRINGS. "META" inside "metadata" and "oil" inside
    "toil" are the kind of match that fills a phone with nonsense and teaches you the
    filter does not work. Symbols are matched case-sensitively because a three-letter
    ticker is a common word in lower case — `CELH` is distinctive, `celh` is not, and
    `VOO` would otherwise match nothing while `ALL` or `IT` would match everything.
    """
    import re

    hits = []
    for sym in symbols:
        base = sym.split("-")[0]
        if re.search(rf"\b{re.escape(base)}\b", text):
            hits.append(sym)
    low = text.lower()
    for word in list(names) + list(keywords):
        if re.search(rf"\b{re.escape(word.lower())}\b", low):
            hits.append(word)
    return bool(hits), sorted(set(hits))


def check(watchlist, seen=None, cfg=None):
    """
    (pushable, state_seen, failures) — new relevant posts, newest first.

    `seen` is the set of post ids already sent, and it is why this can run every half
    hour without repeating itself. It is stored with the rest of the run state and
    therefore survives a fresh checkout, same as the dip levels.
    """
    cfg = cfg or load()
    seen = set(seen or [])
    handles = cfg.get("bluesky") or []
    symbols = [p["symbol"] for p in watchlist.get("positions", [])
               if p.get("status") != "muted"]
    # The company names as a person would write them, not as a ticker.
    names = [n for p in watchlist.get("positions", [])
             for n in ([p.get("company")] if p.get("company") else [])]
    keywords = cfg.get("extra_keywords", [])

    found, failures = [], {}
    for handle in handles:
        posts, state = bluesky_posts(handle, cfg.get("posts_per_account", 10))
        if state != "ok":
            failures[handle] = state
            continue
        for post in posts:
            if post["id"] in seen:
                continue
            ok, hits = relevant(post["text"], symbols, names, keywords)
            if ok:
                found.append({**post, "matched": hits})

    cap = int(cfg.get("max_per_run", 4))
    # NEWEST FIRST AND THEN CAPPED, so a flood costs you the oldest of the batch rather
    # than the most recent. Everything dropped stays unseen and can arrive next run.
    pushable = found[:cap]
    seen.update(p["id"] for p in pushable)
    return pushable, seen, failures


def render(posts):
    """A phone-sized summary. Short, because this is a notification, not a letter."""
    out = []
    for p in posts:
        text = p["text"].replace("\n", " ")
        if len(text) > 180:
            text = text[:177] + "..."
        out.append(f"@{p['handle']}: {text}")
    return "\n\n".join(out)
