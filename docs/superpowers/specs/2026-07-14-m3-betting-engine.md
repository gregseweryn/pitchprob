# Design: Milestone 3 — Betting Engine

Status: in progress (2026-07-14) · builds on ADR 0006

## Problem

M1/M2 produce calibrated probabilities; nothing yet turns them into
*defensible* betting decisions. The naive-EV baseline is quantified at
−11.2% ROI (E0 2021–26, ensemble, best market prices) — the engine's job is
bet selection that survives the bookmaker margin structure, plus the two
non-goals market families (corners, cards) and risk-tiered coupons with
per-leg reasoning.

## Components

1. `betting/selection.py` — pure functions:
   - `blend_probabilities(model, market, weight)` — log-linear pool,
     renormalized; weight 0 = market, 1 = model.
   - `select_value_bets(candidates, *, blend_weight, ev_threshold, max_price)`
     over a candidates frame (probability, market_probability, price, …) →
     qualified bets with `p_bet`, EV, quarter-Kelly.
2. `models/counts.py` — `NegBinCountsModel(stat=...)`: NB2 with per-team
   attack/defence + home advantage on log link, shared log-dispersion, time
   decay, L2 gauge pinning, analytic gradient (digamma), `counts_matrix()`
   up to 25; consumed by `markets.totals` for O/U lines and team totals.
3. `services/coupons.py` — `generate_coupons(books, tier, *, max_legs=4)`:
   candidate legs from each book's high-confidence markets, exhaustive
   combination search over distinct matches into tier bands, EV ranking when
   odds present, reasons from ratings/xG/blend internals.
4. Fixtures: `parse_fixtures_csv` (scores optional) over
   football-data.co.uk/fixtures.csv; fixtures are transient inputs to the
   coupon service, never persisted (schema untouched).
5. CLI: `backtest --selector naive|blended --blend-weight --max-price`;
   new `coupon` command (explicit fixtures or fixtures.csv).

## Acceptance

- Blended selector on the M2 disaster scenario (E0 2021–26 ensemble, EV>3%,
  best prices) improves ROI materially vs −11.2% and cuts bet volume; results
  reported honestly whatever they are.
- Counts model: synthetic NB recovery within tolerance, gradient check vs
  numeric, corners/cards O/U in market book behind a `counts_markets` key.
- Coupons: every tier's joint probability inside its band on constructed
  books; reasons cite at least ratings + expected goals; independence caveat
  in every payload.
