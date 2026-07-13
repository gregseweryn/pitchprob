# pitchprob

Football probability engine: estimates **calibrated probabilities** for match markets
(1X2, Over/Under, BTTS, Asian Handicap, Correct Score, …), computes expected value
against bookmaker odds, and validates itself with lookahead-free walk-forward backtests
benchmarked against the closing line.

> **Honesty policy (ADR 0004):** this project does *not* promise profits and does not
> claim to "beat the bookmaker". Its benchmark is the margin-removed Pinnacle closing
> line — the strongest publicly available probability estimate — and its reports state
> plainly when the models fall short of it. They usually do; that is the expected result
> and the reports are designed to show it rather than hide it.

## What works today (Milestone 1: probability core)

- **Data**: 21,589 matches across the top-5 European leagues (EPL, La Liga, Bundesliga,
  Serie A, Ligue 1), seasons 2014/15–2025/26, ingested from football-data.co.uk with
  bet365/Pinnacle/market-max/market-avg odds (opening + closing where published) —
  ~1M odds quotes, zero silently dropped rows (hard failures are quarantined with
  reasons).
- **Models**: Dixon-Coles (time-decay weighted MLE with analytic gradients), independent
  Poisson (the baseline DC must beat), and Elo with an ordered-logit 1X2 mapping.
- **Markets**: every goals market is derived from one score probability matrix
  (ADR 0002) — 1X2, Double Chance, Draw No Bet, totals at any line, BTTS, Correct
  Score, and Asian Handicap with full quarter-line split-stake settlement semantics.
- **Betting math**: implied probabilities, multiplicative and Shin overround removal,
  fair odds, EV, fractional Kelly.
- **Evaluation**: walk-forward backtester (its no-lookahead property is itself under
  test), log-loss / Brier / RPS / ECE, reliability tables, and staking simulation
  (flat + compounding Kelly) with closing-line value.
- **Interfaces**: a Typer CLI (`ingest / train / predict / backtest`) and a FastAPI
  read API (`/v1/leagues`, `/v1/matches`, `/v1/predictions`, `/v1/backtests`).

## Quickstart

```bash
make install                    # uv sync (Python 3.12, pinned via uv)

# Postgres (production target):
make db-up && make migrate      # Docker Postgres 16 on port 5433
# — or SQLite (zero-dependency dev fallback):
#   echo 'PITCHPROB_DATABASE_URL=sqlite:///data/pitchprob.db' > .env
#   uv run alembic upgrade head

uv run pitchprob ingest --all --from-year 2014 --to-year 2025
uv run pitchprob train --league E0
uv run pitchprob predict --league E0 --home "Manchester City" --away "Chelsea" \
    --odds 1.55,4.4,5.9
uv run pitchprob backtest --league E0 --start 2021-08-01
make serve                      # FastAPI on :8000, OpenAPI docs at /docs
make check                      # ruff + mypy --strict + 137 tests
```

Example output (real run, July 2026):

```
Manchester City vs Chelsea  (E0, trained on 4560 matches to 2026-05-24)
expected goals: 2.11 - 0.98

1X2                home   draw   away
  dixon_coles       62.7%  21.6%  15.8%
  elo               68.2%  18.8%  13.0%
...
Value vs offered odds (quarter-Kelly):
  home  price 1.55  fair 1.60  Expected value -0.029  kelly 0.000
```

Negative EV at realistic prices is the *correct* answer most of the time.

## Honest results (walk-forward, EPL 2021-08 → 2026-05, weekly refits)

1,900 out-of-sample predictions, each made by a model trained strictly on matches
before it (1,730 with Pinnacle closing odds for the benchmark comparison):

| 1X2 metric | Dixon-Coles | Pinnacle closing (Shin de-margined) | gap |
|---|---|---|---|
| Log-loss | 0.9755 | 0.9464 | +3.1% |
| RPS | 0.2007 | 0.1917 | +4.7% |
| ECE (home win) | **0.021** | — | — |

The model is well calibrated (ECE 0.021) and lands within ~5% of the closing line on
RPS — close, and still behind the market, exactly as theory predicts for a
results-only model.

A flat-stake simulation betting every selection with model EV > +3% against the best
listed market price placed 2,189 bets: **ROI −1.8%**, hit rate 27.5%, max drawdown
116 units, mean closing-line value **+0.4%**. Slightly positive CLV with slightly
negative ROI over ~2,200 bets is the regime where variance dominates edge: the system
finds mild value signals, and none of them survive the margin with confidence. That
is the honest state of the art for a results-only model — the roadmap items (xG
features, ensembling, calibration layers) exist to close the gap, not to promise
crossing it.

Reproduce with: `uv run pitchprob backtest --league E0 --start 2021-08-01`
(results are stored in the `backtests` table).

## Architecture

Modular monolith (ADR 0001), Python 3.12, src layout, DDD-flavored bounded contexts:

```
src/pitchprob/
├── core/         config (pydantic-settings), db session, logging, errors
├── data/         adapters (football-data.co.uk) → domain records → repositories
│                 + dataset.py read-models (DB → training frames)
├── markets/      pure functions: score matrix → every goals market   (ADR 0002)
├── models/       Dixon-Coles, Poisson, Elo (+ JSON param round-trips)
├── betting/      odds math (Shin/multiplicative de-margin), EV, Kelly
├── evaluation/   walk-forward backtester, metrics, staking simulation (ADR 0004)
├── services/     market-book construction (shared by CLI + API)
├── api/          FastAPI read API
└── cli/          Typer commands
```

Postgres 16 is the system of record (long-format odds/predictions, ADR 0003) with
Alembic migrations; the code is dialect-portable and runs unmodified on SQLite for
zero-dependency development. See `docs/adr/` for the four architecture decision
records and `docs/superpowers/specs/` for the approved milestone design.

## Testing

137 tests: hand-computed reference values for every formula, hypothesis property tests
(market partitions sum to 1, quarter-line AH EV ≡ mean of adjacent half lines, Shin
books renormalize), analytic-vs-numeric gradient checks, synthetic-data parameter
recovery, a no-lookahead proof for the backtester, and offline CLI/API integration
tests. `mypy --strict` and `ruff` clean.

```bash
make test        # unit tests (no DB needed)
make test-int    # Postgres integration tests (needs make db-up)
```

## Roadmap

| Milestone | Scope |
|---|---|
| **M1 (this)** | Probability core: data, DC/Poisson/Elo, markets, EV, honest backtests, CLI + API |
| M2 | Feature store, xG (Understat), gradient boosting, ensemble + calibration layer, fitted-model cache |
| M3 | Full betting engine: coupon generator with risk tiers, corners/cards models (data already ingested) |
| M4 | Next.js dashboard |
| M5 | Live-info engine (API-Football) + LLM news intelligence |
| M6 | Deployment hardening (Docker images, CI/CD) |
