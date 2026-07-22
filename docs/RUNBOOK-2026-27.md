# Season runbook — 2026/27

The measurement season's operating manual: what runs where, on what
cadence, what to check before betting, and what to do when something is
stale. The season's output is the CLV ledger (ADR 0013/0015); everything
here exists to keep that instrument fed and honest.

## What runs where — and why

| job | where | cadence | why there |
|---|---|---|---|
| odds tape (`record-odds.yml`) | GitHub Actions | daily 08:00 UTC | a missed day loses prices **forever** — it must not depend on this machine being awake (ADR 0012) |
| corners pilot (`record-corners.yml`) | GitHub Actions | daily 20:00 UTC | same, and the T-26h window is time-critical (ADR 0014) |
| results + xG refresh (`weekly-refresh.sh`) | **local**, Task Scheduler | Wed + Sat mornings | the matches/xG database lives on this machine; Actions cannot write to it. A missed week loses **nothing** — the next run picks up where the last left off, so local scheduling's fragility is acceptable here and was not for the tape |
| freshness gate (`pitchprob status`) | local, manual | before every betting session | see below |
| feed validation (`quote-check log`) | local, manual | whenever placing a bet with the feed key active | 2-3 week bar, ADR 0014 |
| watch loop (`pitchprob watch`) | **local**, always-on | every few minutes | it must speak the moment a fresh PL lead appears, at a minutes-scale cadence Actions cannot promise; a missed pass loses nothing (ADR 0016) |

## Weekly refresh

`scripts/weekly-refresh.sh` does, in order: `git pull --rebase` (the cloud
recorder commits tape snapshots daily — without the pull, `import-tape`
has nothing new), `ingest --refresh` and `xg --refresh` for the current
season only, `import-tape`, `pick settle`, and finally `pitchprob status`
— whose exit code becomes the script's, so Task Scheduler's "last run
result" column doubles as the season health light.

Register it once (elevated PowerShell, mirrors the old record-odds task):

```powershell
schtasks /Create /TN "pitchprob-weekly-refresh" /SC WEEKLY /D WED,SAT /ST 08:30 `
  /TR "wsl.exe -d Ubuntu -- bash /home/greg/projects/trading/scripts/weekly-refresh.sh"
```

Why Wednesday and Saturday: football-data.co.uk refreshes its season files
on a **Friday/Tuesday** cadence (audit A2 — the same fact behind the
"early snapshot, not the market open" caveat). Wednesday catches Tuesday's
snapshot with the weekend's results; Saturday catches Friday's with the
midweek round, before the weekend's betting. `--refresh` matters: the
current season's file grows every round, and the cache-first downloader
would otherwise serve August forever. Past seasons stay cached — they
never change, and WSL's TLS flakes (see CLAUDE.md quirks) make every
avoided download a good download.

## Opening the app

Double-click `scripts/pitchprob.bat` (copy it to the Desktop or pin it to
the taskbar). It runs the freshness gate, starts the API and the dashboard,
and opens the browser at http://localhost:3000. Closing the console window
stops both servers.

Nothing is hosted: the app runs on the operator's machine and reads the
**cloud** database, so the data it shows stayed current while the machine
was off. There is no server to pay for and nothing exposed to the internet.

From the browser: `/scanner` verdicts a price, `/ledger` logs the bet and
shows the CLV decomposition. Both hit the same services as the CLI — the
risk layer runs server-side, so a bet the command line would refuse is
refused in the browser too, with the limit named.

## Before every betting session

```bash
uv run pitchprob status
```

Four checks, each naming its fix. Green means: results ≤8 days old, recent
matches carry xG, the tape's newest snapshot is ≤30h old (the scanner's own
STALE bar — same constant, imported not copied) **and** has upcoming
fixtures, no kicked-off pick is missing settlement or CLV. The gate is
DB-only and never touches the network, so it cannot hang or lie because an
API was slow.

Expect it to fail loudly in pre-season: "no upcoming fixtures" before the
tape carries 2026/27 rounds is the honest answer, not a bug. Do not bet
past a red gate — a stale tape means every scan verdicts STALE anyway, and
an unsettled backlog means the drawdown breaker is flying blind.

## The speaking loop (ADR 0016)

`pitchprob watch` is the machine's mouth: it scans upcoming fixtures the tape
anchors, pulls Polish-book quotes from the odds-api.io feed, and pushes an
alert only when an effective edge clears the threshold, the anchor is fresh,
and the bet fits the risk limits. Everything else is silence — and silence is
the expected output of most passes (the tax sits in the prices).

```bash
uv run pitchprob watch --once --dry-run     # one pass, print instead of push
uv run pitchprob watch                       # forever, push to Telegram
```

Telegram delivery needs `PITCHPROB_TELEGRAM_BOT_TOKEN` and
`PITCHPROB_TELEGRAM_CHAT_ID` in `.env` (gitignored). Without them the loop
prints to stdout and says so. Feed leads arrive as `UNVERIFIED` — a prompt to
check the book's own screen, not an authorised bet — until `quote-check`
promotes the feed (ADR 0014). Each standing lead is announced once
(`sent_alerts`).

Run it always-on beside the tape. On Windows, register it the same way as the
weekly refresh, dropping the schedule so it runs continuously:

```powershell
schtasks /Create /TN "pitchprob-watch" /SC ONSTART `
  /TR "wsl.exe -d Ubuntu -- bash -lc 'cd /home/greg/projects/trading && uv run pitchprob watch --interval 300'"
```

Set `PITCHPROB_HEALTHCHECKS_WATCH_URL` to a healthchecks.io ping URL and a
silently dead loop raises an alarm. The loop is DB-only per pass except for
the feed already on the tape and the Telegram push, so it cannot hang on a
slow upstream API.

**The feed poller is the loop's fuel line.** The loop verdicts PL prices, but
something has to put them on the tape. `pitchprob oddsio poll` sweeps the
odds-api.io feed for upcoming fixtures and appends them as `source=odds-api-io`:

```bash
uv run pitchprob oddsio poll --within-hours 72     # anchored fixtures only
```

By default it requests **only fixtures the tape already anchors** (an upcoming
Pinnacle price), because the feed carries thousands of worldwide events the
loop could never verdict and the free tier is 100 requests/hour. Pre-season,
with no top-5 fixtures anchored yet, it correctly requests nothing. It reads
the key's *selected* books automatically (`oddsio select --show`) — naming an
unselected book 403s the whole sweep. Run it on a cron beside the watch daemon
(every ~15 min; denser near kickoff). Keep the two cadences under 100 req/h.

**Nothing to push before the feed is attached.** Until the odds-api.io key is
in `.env` and populating `odds_ticks`, the loop runs and correctly finds
nothing — the honest answer, not a fault.

## After every round: settle, and grow the name map

```bash
uv run pitchprob pick settle
```

Unmatched picks are listed, never guessed (quarantine-not-drop). Each
unresolved side comes with ranked candidates from the teams table:

```
unmatched (settle with --id/--result or extend _ODDS_API_OVERRIDES in
data/normalize.py): 1. FC Union Berlin vs RB Leipzig
  candidate: "1. FC Union Berlin" -> "Union Berlin" (score 0.73)
```

The procedure — TDD like everything else in this repo:

1. Confirm the candidate is the right club (the score is advisory; the
   operator decides — a wrong entry silently mis-settles every future
   pick for that club).
2. Add a failing assertion to `TestOddsApiCanonical` in
   `tests/data/test_normalize_and_service.py` first.
3. Add the pair to `_ODDS_API_OVERRIDES` in `src/pitchprob/data/normalize.py`
   (keep alphabetical within league blocks).
4. `make check`, re-run `pick settle` — the pick settles — then commit
   the map entry and the test together.

Never extend the map speculatively. It grows from real unmatched reports
only, exactly like the Understat and API-Football maps did — a name added
"just in case" is a guess wearing a mapping's clothes.

## Attaching the odds-api.io feed (one-off)

1. Sign up at odds-api.io, copy the key.
2. Add to `.env` (gitignored — never commit it):
   `PITCHPROB_ODDS_API_IO_KEY=<key>`
3. `uv run pitchprob oddsio select --show` — what the key currently has.
4. `uv run pitchprob oddsio select --books "Betclic PL,STS PL"` — names must
   match `pitchprob oddsio books` exactly; re-running **replaces** the
   selection rather than adding to it.

Until quote-check clears the feed, its prices render UNVERIFIED and manual
entry stays ground truth (ADR 0014).

## Monthly

- API credits: printed by every recorder run in the Actions logs; the tape
  (~450) plus the corners pilot (~43) must stay under 500 (ADR 0014).
- `uv run pitchprob quote-check report` — is the odds-api.io feed earning
  promotion past UNVERIFIED? (Only once the key exists and checks are
  being logged.)
- `uv run pitchprob risk report --out docs/weekly/$(date +%F).md` for the
  written record when the ledger has bets.

## Season boot checklist (first round of 2026/27)

1. `bash scripts/weekly-refresh.sh` by hand once; expect the results check
   to go green the Tuesday after the first round.
2. `uv run pitchprob status` — tape check should already be green (the
   recorder has carried 2026/27 fixtures since July).
3. `uv run pitchprob risk status` — breaker open, zero drawdown, full
   tax-free allowance.
4. First bets: flat 2-5 PLN, one per fixture — the ledger enforces both,
   and `--override-risk` leaves a permanent mark (ADR 0015).

## When something breaks

- **WSL TLS handshake failures** (downloads die, DNS fine): `wsl --shutdown`
  and restart — the known MTU degradation from CLAUDE.md. Cached past
  seasons keep working throughout.
- **Tape gap >30h**: check the record-odds workflow run in Actions first;
  quota exhaustion prints in its log. A local `record-odds` run can fill
  today's snapshot at ~15 credits.
- **Breaker tripped**: that is the system working. Read
  `pitchprob risk report` before deciding anything; overriding it is a
  recorded, deliberate act, not a workaround.
