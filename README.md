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

## What works today (M1 core + M2 ML + M3 betting engine)

- **Data**: 21,589 matches across the top-5 European leagues (EPL, La Liga, Bundesliga,
  Serie A, Ligue 1), seasons 2014/15–2025/26, ingested from football-data.co.uk with
  bet365/Pinnacle/market-max/market-avg odds (opening + closing where published) —
  ~1M odds quotes, zero silently dropped rows (hard failures are quarantined with
  reasons).
- **Models**: Dixon-Coles (time-decay weighted MLE with analytic gradients), independent
  Poisson (the baseline DC must beat), and Elo with an ordered-logit 1X2 mapping.
- **M2 ML layer**: per-match xG from Understat (99.98% coverage via the
  ``getLeagueData`` JSON API), a leakage-free streaming feature builder (rolling
  goals/xG/shots/corners form, venue splits, rest days), an XGBoost 1X2 model with
  temporal-holdout early stopping, temperature/isotonic calibration, and a stacking
  ensemble (log-linear pool + per-class bias, ridge-fit on a temporal holdout). The
  prediction service serves the ensemble headline with component probabilities and
  stack weights alongside, cached per (league, data version).
- **Markets**: every goals market is derived from one score probability matrix
  (ADR 0002) — 1X2, Double Chance, Draw No Bet, totals at any line, BTTS, Correct
  Score, and Asian Handicap with full quarter-line split-stake settlement semantics.
- **Betting math**: implied probabilities, multiplicative and Shin overround removal,
  fair odds, EV, fractional Kelly.
- **M3 betting engine**: vig-aware bet selection (model probabilities log-linearly
  anchored to the de-margined market, hard price cap — ADR 0006), negative-binomial
  corners and cards models with O/U lines in every book, risk-tiered coupon
  generation (safe/balanced/value/high-risk bands) with per-leg machine-generated
  reasoning and an explicit independence caveat, and a fixtures.csv adapter so
  `pitchprob coupon` runs against the live upcoming-matches feed.
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

### M3: what vig-aware selection did (and honestly did not) fix

Same protocol, flat stakes on selections with EV > 3% at best listed prices:

| E0 2021–26 | naive ROI | blended ROI (w=0.4, cap 8.0) | naive CLV | blended CLV |
|---|---|---|---|---|
| Dixon-Coles | −0.2% (2,194 bets) | −0.7% (1,560 bets) | +0.4% | **+1.3%** |
| Ensemble | −11.2% (2,192 bets) | −8.9% (1,537 bets) | +0.5% | **+1.6%** |

Anchoring to the market improved every diagnostic — 30% fewer bets, higher hit
rates, closing-line value tripled — but it cannot rescue a model with biased
tails: under *identical* selection the ensemble still loses 8× more than
Dixon-Coles. The ensemble's calibration win (ECE 0.014) was measured on the
home outcome; its draw/away tails are where the losing bets come from.
Calibration where you display is not calibration where you bet. Consequently
the current recommendation baked into the docs: **the betting path uses
Dixon-Coles probabilities; the ensemble serves the headline display** until
per-class calibration lands in M4. ROI confidence bands at ~1,500 bets are
roughly ±5pp, so DC-blended's true edge is statistically indistinguishable
from zero — exactly what an honest engine should report at this stage.

Reproduce with: `uv run pitchprob backtest --league E0 --start 2021-08-01
--model ensemble --refit-days 28 --selector blended`.

### M2 results: does the ML layer help? (same protocol, 28-day refits)

| E0 2021–26, closing-odds subset (n=1730) | log-loss | RPS | ECE (home) |
|---|---|---|---|
| Dixon-Coles | 0.9725 | 0.2002 | 0.021 |
| GBM (xG/form features) | 0.9796 | 0.2010 | 0.020 |
| **Ensemble (DC+Elo+GBM stack)** | 0.9756 | 0.2004 | **0.014** |
| Pinnacle closing (Shin de-margined) | 0.9464 | 0.1917 | — |

Honest read: on log-loss/RPS the ensemble lands *between* its components — with
components this close and correlated, a few hundred holdout matches cannot estimate
stack weights precisely enough to guarantee beating the best component (the test
suite documents this as irreducible dilution, not a bug). What the ensemble *does*
deliver is *calibration*: ECE drops ~30% versus any single model, which is the
property an EV engine actually depends on. On the full E0 history the stack weights
are DC 0.29 / Elo 0.04 / GBM 0.41 — the GBM earns the largest weight through error
diversity despite losing to DC solo.

**A trap found and reported, not hidden:** the ensemble's flat-staking simulation
(EV > 3% vs best market price) lost **−11.2% ROI over 2,192 bets despite +0.5% mean
CLV** — its bias-corrected draw/away probabilities push more marginal longshots over
the EV threshold, exactly where the favourite-longshot bias makes market prices most
punishing (hit rate 25.9% vs DC's 28.0%). Naive EV thresholds over-select longshots;
vig-aware bet selection is the designated M3 betting-engine problem.

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
| **M1 ✓** | Probability core: data, DC/Poisson/Elo, markets, EV, honest backtests, CLI + API |
| **M2 ✓** | xG (Understat), feature builder, XGBoost, calibration, stacking ensemble, model cache |
| **M3 ✓** | Vig-aware selection, NegBin corners/cards, risk-tiered coupons with reasons, fixtures feed |
| M4 | Next.js dashboard |
| M5 | Live-info engine (API-Football) + LLM news intelligence |
| M6 | Deployment hardening (Docker images, CI/CD) |
