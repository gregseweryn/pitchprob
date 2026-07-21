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
