# 0016 — The speaking loop: a watch service that pushes verdicts

Date: 2026-07-22
Status: accepted

## Context

Every instrument the syndicate program needs to *decide* a bet already
existed before this ADR: the tape (ADR 0012), the pick ledger and PL scanner
(ADR 0013), the odds-api.io feed and latency map (ADR 0014), the risk layer
(ADR 0015). What did not exist was a way for the system to **speak**. Every
verdict was pull-only — the operator ran `pitchprob scan` with a hand-typed
price, or opened the dashboard. Nothing reached a phone unprompted.

For a program whose thesis is *the value is a slow Polish price after the
sharp line has moved*, pull-only is the wrong shape. Those windows are hours
long and close; a person who has to remember to look will miss most of them.
The operator's request was explicit: **the machine has to say when to bet.**

The danger in building this is equally explicit, and it is the danger of
every betting product: a machine that always finds a "sure thing" is a
machine that burns the bankroll. The 12% turnover tax sits in the prices, so
value is the exception. A loop whose default is a green light is worse than
no loop at all.

## Decision

Build a **watch loop** (`services/watch.py`) and a **notifier**
(`services/alerts.py`), plus `pitchprob watch`. The loop adds no new
judgment — it composes parts already proven — and every design choice below
exists to keep it honest and quiet.

### The sharp price decides, never the loop

Each candidate is verdicted by `scanner.scan`, whose primary anchor is the
tape's Shin-de-margined Pinnacle fair (ADR 0004/0011). The loop reads that
verdict; it does not compute an edge of its own. The model, as everywhere in
this repo, is display-only.

### Feed-driven, therefore silent by default

Polish-book prices come from the odds-api.io feed (`scanner.feed_quotes`,
`source="odds-api-io"`). With no feed data there is nothing to verdict and
the loop says nothing — the correct behaviour before the feed key is
attached, not an error. Feed prices are **unvalidated** until `quote-check`
clears them (ADR 0014), so they surface as `UNVERIFIED` **leads**, never an
auto-`PLAY`. A lead alert tells the operator to go and look at the book's own
screen; it does not authorise a bet. Only `PLAY` and `UNVERIFIED` become
alerts. `NO BET`, `STALE`, `NO ANCHOR` are silence, by design.

### Never alert a bet it could not place

Before an alert fires, the loop computes the flat stake that fits the ADR
0015 exposure limits as of now (`ledger.exposure_state`), and drops the
candidate if none fits — one bet per fixture already taken, the day's cap
spent, too many open picks — or if the drawdown breaker is tripped. An alert
the operator could not act on is noise, and noise is exactly what erodes the
value of a rare signal.

### Say a standing edge once

A new table `sent_alerts` (migration `f2a4c6e8b0d1`) records every pushed
alert, keyed on `(event_id, market, selection, line, bookmaker)`. The loop
reads it before sending and skips anything already announced. Re-pinging the
same lead every pass trains the operator to ignore alerts, which defeats
alerting. `verdict` and `edge` are stored for the record, not the key: an
edge that drifts a little is the same lead.

### Honesty-as-payload, in Polish

The alert message carries the scanner caveats **in its body** (ADR 0004),
not in a client footer. Verdict *values* stay English (`PLAY`/`UNVERIFIED`);
the operator-facing prose is Polish (frontend/PRODUCT.md). A `PLAY` renders
as `🟢 GRAJ`; an `UNVERIFIED` renders as a lead that says *sprawdź kurs na
stronie buka* — the wording itself refuses to overclaim.

### Delivery: Telegram, with the token scrubbed

The default notifier posts to one Telegram chat
(`PITCHPROB_TELEGRAM_BOT_TOKEN` / `PITCHPROB_TELEGRAM_CHAT_ID`, gitignored).
The bot token rides in the request *path*, so — as with the odds adapters —
a delivery failure is scrubbed to a status line before it can reach a log or
a traceback. A failed send is **not** recorded in `sent_alerts`, so the next
pass retries it: a dropped message must not silently become a lead the
operator never heard. `StdoutNotifier` backs `--dry-run` and the tests; with
no token set, the loop prints to stdout and says so.

### Scheduling (operational, not code)

`pitchprob watch` runs a pass, then sleeps `--interval` seconds, forever
(`--once` for a single pass). It belongs on the operator's always-on machine
(WSL cron / Task Scheduler), not in GitHub Actions: the loop needs the
minutes-scale cadence Actions cannot promise, and — unlike the tape — a
missed pass loses nothing recoverable. After each pass it pings
`PITCHPROB_HEALTHCHECKS_WATCH_URL` (optional) so a silently dead loop raises
an alarm.

## Consequences

- The system can now initiate: the operator gets a phone push the moment a
  fresh, risk-fitting, above-threshold PL lead appears, and silence the rest
  of the time. Silence is the expected output of most passes.
- Until the odds-api.io key is attached and the feed is populating
  `odds_ticks`, the loop runs and correctly finds nothing. It is also useful
  the moment the feed lands, with no further code.
- Feed leads are `UNVERIFIED` until `quote-check` promotes the feed (ADR
  0014); the loop does not shortcut that gate.
- This is the "MÓWI" half of the product. It does not change what a bet *is*
  — the sharp price still decides, the risk layer still refuses, the ledger
  is still the decision variable. It changes only whether the operator finds
  out in time.

## Alternatives rejected

- **A green light with a confidence score.** Rejected: a loop that always
  emits *something* is the failure mode this ADR exists to avoid. The output
  space includes silence, and silence is the common case.
- **Running the loop in GitHub Actions.** Rejected: cron in Actions quantises
  to coarse, queue-delayed intervals; the value windows are shorter than
  that. The tape lives in Actions because a missed day is unrecoverable; a
  missed watch pass is not.
- **Re-alerting on every pass, or on any edge change.** Rejected as alert
  fatigue. One announcement per standing lead; a materially different lead is
  a different `(selection, book)` and alerts on its own.
- **Letting the model raise alerts.** Rejected — it contradicts every prior
  verdict (Phase 0–2a). The sharp anchor decides.
