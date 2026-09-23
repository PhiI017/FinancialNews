# Market alerts

Watches your positions and the S&P, pushes the urgent things to your phone, and emails
eleven letters a week: two every weekday and one on Sunday. Runs on GitHub's servers — your computer is never involved.

## What it sends

**Urgent, straight to your phone.** The S&P crossing one of your dip triggers (-5, -10,
-15, -20, -25% from its all-time closing high), any holding moving more than its
threshold in a day, or a big move in oil, the 10-year or the Fed rate. Checked every
thirty minutes from the open until after the bell, plus once each weekend day because bitcoin does
not stop. **A quiet check sends nothing at all** — an alerter that pings you to say
nothing happened is one you mute, and a muted alerter misses the day that matters.

**Four letters, two of them on the same day and doing different jobs.** Before the open,
at 8:45am New York: what is due today and anything overnight that changes how your
positions start. Before the close, at 3:40pm: what actually moved and why, while there is
still twenty minutes to do something about it. On Monday the morning letter is instead the
week ahead — the dated events coming and what each would mean for you. On Sunday, what
happened and then what is coming, with dates.

**The letter times are New York, not UTC, and that is deliberate.** Written as a fixed
timer, "before the close" becomes 2:40pm every November when the clocks move. So no timer
sends a letter: the schedule fires candidates on both sides of the boundary and
`overdue_letter()` sends whichever slot has passed with nothing sent. The same mechanism
covers a dropped run — scheduled jobs are best-effort and most of them are dropped, so a
letter that depended on one timer would simply not exist that day.

The phone gets a one-line pointer for the letters, not the letter. A 450-word newsletter
as a notification is unreadable, and pushing one every weekday teaches you to swipe away
the channel the urgent alerts share.

## What it costs

**The data is free.** Prices and headlines from Yahoo, which needs no key. Oil, yields
and the Fed rate from FRED, which needs a free key.

**The writing is about 35 cents a month.** Claude Haiku 4.5 at $1 per million input
tokens and $5 per million output; one letter is roughly 3,000 in and 700 out, about two
thirds of a cent, and there are about fifty a month. `python alerter.py --setup` prints the arithmetic for whichever
model is set. Change `MODEL` in `summarize.py` if you want a better writer — Sonnet 5 is
about four times as much and still under a dollar and a half a month.

**GitHub Actions is free on a public repo** and metered on a private one. That is why
this belongs in its own public repository.

## Setting it up

**1. Already done.** This repo is public, which is what makes the Actions minutes free,
and it has no self-hosted runner. Keep it that way: a public repo plus a self-hosted
runner lets a stranger's pull request run code on your machine.

**2. Install ntfy and pick a topic.** Get the app (ntfy.sh), tap subscribe, and paste a
topic name. `python alerter.py --setup` prints a random one — use it rather than
something guessable, because on the public server the topic name *is* the password.

**3. Add the secrets.** Settings, then Secrets and variables, then Actions:

- `NTFY_TOPIC` — the topic from step 2
- `SMTP_USER` — your Gmail address
- `SMTP_PASS` — a Gmail **app password**, not your real one. Turn on 2FA, then
  myaccount.google.com/apppasswords. Your real password will be refused
- `ALERT_EMAIL_TO` — where the letters go
- `ANTHROPIC_API_KEY` — optional; without it the letters still send, carrying every
  number, with one line saying there is no written summary
- `FRED_API_KEY` — optional and free; without it oil, yields and the Fed rate are
  skipped **with a stated reason**, never silently
- `WATCHLIST_JSON` — optional. Paste the whole of `watchlist.json` here to keep your
  holdings out of a public repo. The workflow uses it instead of the committed file and
  never commits it back

**4. Check it.** Actions, "market alerts", Run workflow, mode `check`, dry run ticked.
That fetches and prints without sending or spending anything.

## Changing what it watches

Edit `watchlist.json`. One line per position. `status` is `held`, `watching` (you are
deciding — the letters will not say you own it) or `muted` (kept but never fetched).
`overrides` sets a per-symbol daily move threshold, because 5% in VOO is a market event
and 5% in CELH is a Tuesday.

Edit `catalysts.json` to add dated events. Anything in the past drops off automatically,
and if the file stops reaching two weeks ahead the letters say so — an empty week-ahead
otherwise looks the same whether the week is quiet or the file is stale.

## Running it by hand

    python alerter.py --setup              what is configured and what is missing
    python alerter.py --check --dry-run    print, send nothing, spend nothing
    python alerter.py --preopen
    python alerter.py --preclose
    python alerter.py --weekahead
    python alerter.py --weekly
    python test_alerts.py                  the suite: no network, no cost

## The things most likely to bite

**Every fetch reports where it stopped.** A digest that silently omits oil because the
fetch failed reads exactly like a digest where oil did not move. Failures are named in
the letter — `no_key`, `timeout`, `http_404`, `unparsed` — and `unparsed` specifically
means our bug, not the source's.

**The all-time high only ever goes up.** If a bad fetch returns three years instead of
forty, the computed high falls, every dip level rescales, and a -15% alert quietly never
fires. A lower reading is treated as evidence about the fetch, not the market.

**A dip level fires once.** It re-arms only after the market recovers past it, not on a
timer — a timer re-fires at the same level on the same drawdown a day later.

**`state.json` is committed on purpose.** The runner starts from a fresh checkout every
time, so state that lives only on disk does not exist, and every run would resend the
-10% alert.

**A market down exactly 10.00% nearly did not fire the 10% trigger.** 6930/7700 is 0.9
in decimal and slightly more in binary, so the depth computes as 9.999999999999998. See
`triggers.EPSILON_PCT` — the round number is the one that breaks.
