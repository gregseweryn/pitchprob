# pitchprob — project context

Football probability engine. Estimates calibrated match-market probabilities
(1X2, totals, BTTS, Asian handicap, corners, cards), computes EV vs bookmaker
odds, generates risk-tiered coupons with reasoning. **Never promises profit**
— the benchmark is the margin-removed Pinnacle closing line, and reports say
plainly when models fall short (they usually do; that is expected and stated).

GitHub: https://github.com/gregseweryn/pitchprob (private).

## State: all six milestones complete

M1 probability core · M2 xG + XGBoost + ensemble · M3 betting engine +
coupons · M4 Next.js dashboard · M4.5 calibration experiment · M5
availability experiment · M6 deployment. Data: 21,589 matches (top-5 European
leagues 2014/15–2025/26, football-data.co.uk), 99.98% Understat xG coverage,
40,500 API-Football injury records (seasons 2022–24). ~260 tests, mypy
--strict, 9 ADRs in `docs/adr/` (read them before changing anything they
cover).

**Experimental verdicts (do not relitigate without new evidence, README has
the tables):** ensemble ≈ Dixon-Coles on accuracy but 8× worse for betting
(conditional, price-correlated tail error); per-class temperature calibration
fixed calibration and NOT betting ROI; isotonic overfits 190-match holdouts
catastrophically; absence features are a measured null (the market prices
team news). Consequently: **the betting path uses Dixon-Coles; the ensemble
is display-only.** No paid API tiers, no LLM news layer — spend is gated on
the `--ablate`-style experiment harness showing lift first.

## Architecture (see ADRs)

Modular monolith, `src/pitchprob/`: `core` (config/db/logging) · `data`
(adapters: football-data, Understat, API-Football; ORM; repositories;
dataset read-models) · `markets` (pure fns over score matrices — ADR 0002:
models emit P(i,j), every goals market derives from it) · `models`
(dixon_coles, elo, gbm, ensemble, calibrated, counts) · `betting` (odds math,
Shin de-margin, selection blend, Kelly) · `evaluation` (walk-forward
backtest, metrics incl. per-class ECE, staking sim) · `services` (prediction
market-book with per-league model cache, coupons) · `api` (FastAPI) · `cli`
(Typer). `frontend/` = Next.js 15 dashboard (ADR 0007; PRODUCT.md/DESIGN.md
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
uv run pitchprob ingest|xg|injuries|train|predict|backtest|coupon --help
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
