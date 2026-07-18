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
API-Football injury records (seasons 2022–24). ~340 tests, mypy --strict,
10 ADRs in `docs/adr/` (read them before changing anything they cover).

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

## Next candidates

GitHub CI is live on push (`.github/workflows/ci.yml`). When the 2026/27
season starts (mid-Aug), `pitchprob coupon` works off the live fixtures feed
and weekly `ingest --refresh`/`xg` keep data current. Open research lanes:
CLV-conditioned training, player-weighted absences (needs paid data — gate on
evidence), corners/cards market validation vs quoted lines.
