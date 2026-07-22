# pitchprob — project context

Football probability engine. Estimates calibrated match-market probabilities
(1X2, totals, BTTS, Asian handicap, corners, cards), computes EV vs bookmaker
odds, generates risk-tiered coupons with reasoning. **Never promises profit**
— the benchmark is the margin-removed Pinnacle closing line, and reports say
plainly when models fall short (they usually do; that is expected and stated).

GitHub: https://github.com/gregseweryn/pitchprob (private).

## State: seven milestones complete; syndicate program in flight

M1 probability core · M2 xG + XGBoost + ensemble · M3 betting engine +
coupons · M4 Next.js dashboard · M4.5 calibration experiment · M5
availability experiment · M6 deployment · M7 inference engine (ADR 0010:
bet-at-open simulation with **true CLV** = open price × Shin(close fair) − 1,
OU/AH multi-market betting, block-bootstrap CIs, `pitchprob experiment
run|compare` registry with paired significance). Data: 21,589 matches (top-5
European leagues 2014/15–2025/26, football-data.co.uk; Pinnacle close covers
20,733 and open 20,717 of them), 99.98% Understat xG coverage, 40,500
API-Football injury records (seasons 2022–24). ~750 tests, mypy --strict,
17 ADRs in `docs/adr/` (read them before changing anything they cover).

The syndicate-transformation roadmap (approved 2026-07-18, plan file
`~/.claude/plans/you-are-a-principal-proud-patterson.md`) continues: Phase 1
market-information study → Phase 2 CLV meta-model bet gate → Phase 3
real-money risk layer → Phase 4 context features → Phase 5 tick recorder +
forward pick ledger for 2026/27. End-state: real money at small stakes; the
decision variable is the CLV ledger, never backtest ROI.

**Experimental verdicts (do not relitigate without new evidence, README has
the tables):** ensemble ≈ Dixon-Coles on accuracy but 8× worse for betting
(conditional, price-correlated tail error); per-class temperature calibration
fixed calibration and NOT betting ROI; isotonic overfits 190-match holdouts
catastrophically; absence features are a measured null (the market prices
team news). M7 added: **blended-at-open true CLV is significantly negative in
all five leagues** (−0.8% to −1.3%, p ≤ .003 — the closing line moves
*against* the current selector's bets; the previously reported +1.3% "CLV"
was line-shopping value at the close, not timing value), and the naive
selector's +6.3% AH CLV is an artifact suspect (best-price outliers vs
Pinnacle fair on unmoved lines), not an edge. Phase 1 movement study:
**DC-vs-open divergence is an error signal, not a steam signal** — the
market never moves toward the model (P=0.47–0.52 across leagues, D1
significantly against at p=.012, worst in the largest divergences). Phase 2a
meta-gate (ADR 0011): **null — no early timing edge**; gated bets' clv_sharp
pooled ≈ −0.4% (E0 significantly negative); the paired "improvement" was
line-shopping value (clv_exec), caught by the two-label design. Consequently:
**the betting path uses Dixon-Coles; the ensemble is display-only;
real-money betting stays locked (no demonstrated CLV-positive subset);
forward emphasis is Phase 5 (tick recorder + pick ledger before 2026/27).** No paid API
tiers, no LLM news layer — spend is gated on the experiment harness showing
lift first.

## Architecture (see ADRs)

Modular monolith, `src/pitchprob/`: `core` (config/db/logging) · `data`
(adapters: football-data, Understat, API-Football; ORM; repositories;
dataset read-models) · `markets` (pure fns over score matrices — ADR 0002:
models emit P(i,j), every goals market derives from it) · `models`
(dixon_coles, elo, gbm, ensemble, calibrated, counts) · `betting` (odds math,
Shin de-margin, selection blend, Kelly, realized settlement) · `evaluation`
(walk-forward backtest, metrics incl. per-class ECE, staking sim,
block-bootstrap significance) · `services` (prediction market-book with
per-league model cache, coupons, backtest harness `harness.py` — two clocks
close/open — and experiment registry `experiments.py`) · `api` (FastAPI) ·
`cli` (Typer). `frontend/` = Next.js 15 dashboard (ADR 0007; PRODUCT.md/DESIGN.md
in that dir). Postgres 16 via compose (port 5433), SQLite fallback works.

## Non-negotiable conventions

- **TDD, red first.** Every formula has a hand-computed test; hypothesis
  property tests for math; walk-forward only, no lookahead ever (it's
  property-tested).
- **Honesty is a feature.** Disclaimers/caveats are payload contract; negative
  results get written up in the README, not buried.
- New signal ideas go through the backtest harness (`--ablate`, `--end`,
  `--selector`) before any architecture or spend decision.

## Commands

```bash
make db-up / migrate / test / check      # dev (db-up runs docker preflight)
make stack-up                            # full containerized stack (:8000/:3000)
uv run pitchprob ingest|xg|injuries|train|predict|backtest|coupon|experiment --help
uv run pitchprob backtest --at open --markets 1x2,ou,ah   # syndicate clock
uv run pitchprob experiment compare ... --vs ablate=absences  # paired A/B
uv run pitchprob record-odds --league all   # daily odds tape (ADR 0012, ~15 credits)
uv run pitchprob scan "arsenal" --market ou --selection over --line 3.0 \
    --quote betclic:2.10   # PL scanner (ADR 0013; tax regime auto, ADR 0017)
uv run pitchprob pick log|settle|list        # forward real-money CLV ledger
uv run pitchprob record-corners --league E0  # corners tape, T-26h (ADR 0014)
uv run pitchprob study latency               # who copies the sharp line last
uv run pitchprob quote-check log|report      # validate the odds-api.io feed
uv run pitchprob risk status|report          # limits, breaker, weekly report
uv run pitchprob watch --once --dry-run       # the speaking loop: push GRAJ/LEAD (ADR 0016)
uv run pitchprob status                      # pre-round freshness gate
bash scripts/start.sh                        # gate + API + dashboard + browser
uv run pitchprob oddsio select --show        # which PL books the feed key has
cd frontend && npm run dev               # dashboard against local API
```

## Environment quirks (Windows 11 + WSL2 Ubuntu, hard-won)

- Run shell work via `wsl.exe -d Ubuntu -- bash /path/script.sh` — **write a
  script file**; inline PowerShell→WSL quoting mangles `$VAR`/spaces. Default
  distro is Ubuntu; `uv` at `~/.local/bin`, user-space Node at `~/.local/node/bin`.
- **Docker Desktop stale-socket crash**: after unclean shutdowns it
  crash-loops on orphaned AF_UNIX sockets. `scripts/docker-preflight.sh`
  sweeps them (wired into `make db-up`/`stack-up`). Windows can't delete
  those files; WSL `rm` can.
- **WSL TLS/MTU degradation**: outbound TLS handshakes from WSL periodically
  die (DNS + TCP fine) — Docker Desktop restarts trigger it. Fix:
  `wsl --shutdown`, restart; or route network work via Windows (curl.exe)
  into WSL-side caches — the API-Football client and downloaders are
  cache-first exactly for this.
- API-Football free tier: seasons 2022–24 only, 100 req/day, 10 req/min; key
  in `.env` (`PITCHPROB_API_FOOTBALL_KEY`, gitignored — never commit).
- Frontend fonts via the `geist` npm package (no build-time Google fetch).

## Handoff — state and next task (updated 2026-07-22)

**P0 "speaking loop" built (ADR 0016).** The system can now *initiate*: a
watch loop (`services/watch.py`) + notifier (`services/alerts.py`) +
`pitchprob watch`. It scans upcoming tape-anchored fixtures, pulls PL quotes
from the odds-api.io feed (`scanner.feed_quotes`), and pushes a Polish
Telegram alert **only** when an effective edge clears the threshold, the
anchor is fresh, and a flat stake fits the ADR 0015 risk limits — silent
otherwise (silence is the common case). Feed leads push as `UNVERIFIED`
(check the book's screen), never auto-`PLAY`; each standing lead is announced
once (`sent_alerts`, migration `f2a4c6e8b0d1`). Telegram token/chat + an
optional healthchecks.io URL live in `.env`
(`PITCHPROB_TELEGRAM_BOT_TOKEN`/`_CHAT_ID`/`PITCHPROB_HEALTHCHECKS_WATCH_URL`).
Run always-on beside the tape (WSL cron / Task Scheduler — RUNBOOK). **It
finds nothing until the odds-api.io key is attached and the feed populates
`odds_ticks` — that is correct, not a fault.** Sharp price still decides; the
loop adds no judgment. `make check` green.

**Blocked on you (unchanged, now higher-leverage):** sign up at odds-api.io,
put `PITCHPROB_ODDS_API_IO_KEY` in `.env` + repo secrets, select Betclic PL +
STS PL. Until then the speaking loop, the feed and the latency map have no
data; everything else runs.

**Operational now:** the odds tape records itself daily via GitHub Actions
(`.github/workflows/record-odds.yml`, 08:00 UTC, commits
`data/tape/*.csv.gz`; merge locally with `pitchprob import-tape`). The
corners pilot rides alongside (`record-corners.yml`, 20:00 UTC, E0 only).
API key in repo secrets + local `.env`. CI on push (`ci.yml`), now including
the frontend's vitest suite. Full stack in Docker (`make stack-up`,
dashboard :3000); M7 results synced into the Postgres backtests table so the
dashboard shows them.

**Git state:** the 2026-07-21 work sits in eight grouped local commits
(dashboard/A6 · audit fixes A1–A5/A7/A10 · ADR 0013 ledger+scanner ·
ADR 0014 corners+latency+feed · docs · ADR 0015 risk layer ·
scanner/ledger views · season ops), **rebased onto the recorder's daily
tape commits and not yet pushed** — push soon: the cloud recorder pushes
to `origin/main` every day, so each unpushed day means another rebase in
the weekly script. The recorder's commits touch only `data/tape/`, so
those rebases stay conflict-free.

**Audit 2026-07 findings closed:** A1 (absence passthrough — the M5 null
now needs a rerun to mean anything), A2 (early-snapshot terminology),
A3 (`value_analysis` on the betting path), A4 (API-key scrubbing),
A5 (`clv_sharp` in `experiment compare`), **A6 (sharp CLV + bootstrap CIs
on the dashboard's backtests page — done, no longer open)**, A7 (odds
composite index), A9 (README counters), A10 (SHA-pinned actions +
least-privilege permissions). **A8 (Shin vectorisation) is still open** and
remains a performance item, not a correctness one.

**Phase 5 part 3 is built (ADR 0013)** — the measurement instrument for
2026/27 is ready and green (`make check`):

- `betting/effective.py` — effective prices (×0.88 taxed, ×1.0 tax-free) and
  the **promo-EV engine**: `promo_ev` prices a quote bare-and-taxed vs with
  its promo (tax-free / boosted price / payout haircut for conditioned
  winnings), so `promo_value` is the EV the promotion itself contributes.
  `TaxFreeAllowance` tracks Betclic's 1,000 PLN tax-free turnover; a stake
  that straddles the limit counts as taxed (a coupon can't be split).
- `services/tape.py` — read-model over `odds_ticks`: `fair_at` returns the
  latest **complete** Shin-de-margined Pinnacle market at or before an
  instant (no lookahead, ever), `closing_fair` stops strictly before
  kickoff, and every quote carries `observed_at` (freshness is payload).
- `services/ledger.py` + `picks` table (migration `e3a5c7d9f1b2`) —
  `log_pick` auto-fills the sharp anchor at bet time; `auto_settle` joins
  tape naming → canonical `matches` (±1 day) and **lists unmatched picks
  rather than guessing**; `attach_clv` fills both labels per bet.
- `services/scanner.py` + `pitchprob scan` — "graj/nie graj + o ile" vs the
  tape's Pinnacle fair (primary; model fair only informational, never flips
  a verdict). NO ANCHOR on line mismatch, STALE when the anchor is >30h old.
- CLI: `pitchprob scan`, `pitchprob pick log|settle|list`.

**Audit follow-ups done (ADR 0014, 2026-07-21)** — all three verified
against the live APIs before being built, and two audit assumptions did not
survive:

- **Corners are on the tape.** `pitchprob record-corners` (E0 pilot, 26h
  window, `record-corners.yml` at 20:00 UTC) — ~43 of 500 monthly credits,
  with a `--reserve` guard so it can never starve the main tape. Measured:
  Pinnacle prices corners ~24h out, nothing at 3 days, and empty responses
  are billed zero. Cards excluded (separate credit, audit P2).
- **The line-latency map exists but has no data yet.** The audit's "already
  measurable from the existing tape" was **wrong**: one snapshot, zero PL
  books in The Odds API, and a daily cadence that quantises delay to 24h.
  `evaluation/latency.py` + `pitchprob study latency` are source-agnostic
  and currently print "No measurement" plus the failed precondition. The
  audit doc carries a correction, pinned by `tests/test_docs.py`.
- **odds-api.io prototype.** Six PL books in its catalogue (Betclic PL, STS
  PL, eFortuna PL, Betfan PL, LVbet PL, Superbet), no Pinnacle, and
  `/odds/movements` gives timestamped history — the real fuel for the
  latency map. Wired as informational only: `quote-check log|report`
  validates it against the operator's screen (bar fixed in advance: ≥30
  checks, ≥95% agreement within 0.02, ≤10% missing/stale), and
  `scan --feed` returns **UNVERIFIED** where it would say PLAY.

**Blocked on you:** sign up at odds-api.io, put `PITCHPROB_ODDS_API_IO_KEY`
in `.env` + repo secrets, and select Betclic PL + STS PL via
`/bookmakers/selected/select`. Until then the feed and the latency map have
no data; everything else runs.

**Phase 3 risk layer is built (ADR 0015)** — thresholds fixed in advance,
chosen for sample integrity and fault detection rather than capital
preservation (at 2-5 PLN on a 500 PLN roll, ruin is not the live risk):

- `betting/risk.py` — `RiskLimits` (band 2-5 PLN; per fixture 5; per day 25
  = 5%; 15 open; drawdown stop 75 = 15%), `check_exposure` reporting **every**
  breached limit, `drawdown_state` over **realized** P&L in **settlement**
  order. Boundaries inclusive. `RiskLimits.for_bankroll` scales the
  aggregate caps but not the stake band (flat staking is a program choice).
- **The per-fixture cap equals the single-stake cap on purpose**: one bet
  per match, because the weekly block bootstrap cannot see dependence inside
  a fixture and two correlated bets would narrow the CLV interval falsely.
- `ledger.exposure_state` / `realized_drawdown` feed `log_pick`, which
  raises `RiskRefusal` and writes **nothing**. `--override-risk` places the
  bet and stamps `picks.risk_override` / `risk_note` (migration
  `d4b8e2f6a9c1`) — the weekly report reads those back.
- `services/risk_report.py` + `pitchprob risk status|report`: money over 7
  days, CLV over the whole ledger, **no CI below 4 ISO weeks** (a bootstrap
  over one block is a straight line), tax-free allowance, overrides,
  drawdown vs the stop.

**Dashboard now covers the forward program (audit Etap 7 closed).** New
API surface: `GET /v1/scanner/events`, `POST /v1/scanner/scan`,
`GET /v1/ledger` — each returns a `caveats` list, because ADR 0004 makes
disclaimers payload, not prose the client is trusted to add
(`SCANNER_CAVEATS` / `LEDGER_CAVEATS` live in the services). Pages:
`/scanner` (effective prices per book, anchor age, NO ANCHOR/STALE/
UNVERIFIED as named refusals) and `/ledger` (CLV exec/sharp + shopping,
weekly report, tax-free allowance, drawdown, overrides). Verdict states use
shape as well as colour: filled = actionable, outlined = conditional,
dashed = the instrument could not answer. **Frontend is Next.js 16**, not
15 — Turbopack by default, and React 19's purity rule rejects `Date.now()`
during render (the ledger uses the report's own `generated_at` instead).

**Two bugs found by driving the real UI, both fixed:** the header `<nav>`
overflowed the viewport horizontally on **every** page at 390px (pre-
existing; five nav items made it certain — it now wraps), and repeated
quote rows lost their labels when the grid stacked on mobile.

**M5 rerun done (2026-07-22).** The absence A/B was re-run and registered with
the A1 fix live (GBM E0 2023-08→2025-06, 760 fixtures, 5000 resamples): every
primary endpoint null (log-loss Δ−0.0011 p=0.71, RPS p=0.99, ROI p=0.31,
exec-CLV p=0.47), matching the published table. The null is now backed by a
stored run, not a description; the spend gate (paid player data / LLM news)
stays closed on measured evidence. A1 fully closed.

**Betclic "Bez Podatku 2.0" auto-regime built (ADR 0017, 2026-07-22).** The
operator read the full regulamin; it invalidated three baked-in assumptions
and exposed a P0: the watch loop priced Betclic feed quotes ×0.88,
understating edge by 12 pp (the whole season fits the 1,000 PLN limit, so
the true multiplier is ×1.0). Now: `scan()` is the effective-price router —
an undeclared quote gets its regime from `betting/promos.py` (registry as
data: aliases "Betclic PL"/"betclic", limit, ×0.94 post-limit per §3
ust. 11 pkt 1) plus the ledger allowance (§3 ust. 4: a straddling stake
qualifies in full ⇒ the decision is stake-independent), degrading only
downward 1,0→0,94→0,88; explicit declarations always win.
`PITCHPROB_DISABLED_PROMOS=betclic` is the §8 kill switch (offer cancellable
in 24h). `picks.tax_multiplier` (migration `c5a9d7e1f3b6`) records the
exact regime per bet; `pick log` derives it automatically
(`--tax-free`/`--taxed` force). Caveats updated (three regimes, ledger-drift
warning, coupon-screen-decides). AKO ballast priced and declined in ADR
0017: break-even ≤3.1% margin/leg, conflicts with ADR 0015 sample design;
moot within the limit. Feed stays UNVERIFIED (ADR 0014 unchanged).

**Next task:** extend `_ODDS_API_OVERRIDES` in `data/normalize.py` from the
first real "unmatched" reports once the season starts. Open research lanes
(evidence-gated, expected null): CLV-conditioned training (model =
f(sharp-fair, residual) — the only modelling path with a shot at +CLV),
non-transitivity / bivariate-Poisson / TabPFN ablations.

**User context:** Polish operator — PL-licensed books only, communicates in
Polish. Docs, code, tests and API verdict *values* stay English; the
**dashboard's operator-facing copy is Polish** (frontend/PRODUCT.md records
the rule), including the caveat payloads in `SCANNER_CAVEATS` /
`LEDGER_CAVEATS`. `/jak-to-dziala` explains, in Polish, why the model does
not drive the verdict — the operator asked "when does the model say to
bet?", and the honest answer (never; the sharp price decides) needed a
place in the product, not just in the README.

Open research lanes (evidence-gated): CLV-conditioned training,
player-weighted absences (needs paid data), corners/cards market validation
vs quoted lines, weekly `ingest --refresh`/`xg` automation for the season.
