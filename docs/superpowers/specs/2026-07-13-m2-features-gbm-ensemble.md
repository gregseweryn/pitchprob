# Design: Milestone 2 — xG, Feature Store, GBM, Stacking Ensemble

Status: in progress (2026-07-13) · Depends on: M1 probability core

## Goal

Close part of the gap to the closing line (M1: Dixon-Coles RPS +4.7% vs Pinnacle
closing on EPL 2021–2026) by adding information the goals-only models cannot see:
shot quality (xG), form, schedule, and cross-league patterns — combined through a
properly validated stacking layer. Honesty policy unchanged: all evaluation is
walk-forward vs the de-margined closing line, and results are reported even when
they disappoint.

## Scope

1. **Understat xG ingestion** — league-season pages carry a `datesData` JSON blob
   with per-match xG for both sides; 5 leagues × 12 seasons = 60 requests, cached
   on disk like the CSV source. New nullable `matches.xg_home/xg_away` columns
   (Alembic revision 2). The updater only *annotates existing matches* (alias
   resolution + explicit override map; date matched with ±1 day tolerance for
   timezone skew); it never creates teams or matches, and reports unmatched rows.
2. **Feature store** (`features/`) — one O(n) chronological pass maintaining
   per-team streaming state; for every match it emits *strictly pre-match*
   features: rolling-10 overall and rolling-5 venue-specific form (points, goals,
   shots, shots-on-target, corners, xG — for and against), rest days, Elo ratings
   and diff, league as a categorical. Anti-leakage is property-tested: the feature
   row of match *k* must be identical whether or not later matches exist.
3. **GBM model** (`models/gbm.py`) — XGBoost `multi:softprob` over the feature
   frame, NaN-native (early-history rows keep NaNs), league-categorical, temporal
   holdout early-stopping to pick the tree count, then refit on the full window.
   Trains pooled across leagues — cross-league sample size is precisely where GBM
   beats the per-league statistical models.
4. **Calibration module** (`evaluation/calibration.py`) — temperature scaling
   (scipy) and per-class isotonic with renormalization (scikit-learn), behind one
   protocol, for calibrating any model and for reliability reporting.
5. **Stacking ensemble** (`models/ensemble.py`, ADR 0005) — log-linear pooling
   with per-class biases: `log p_c ∝ Σ_i w_i log p_i,c + b_c`, fit by NLL on a
   temporal holdout of the training window; components then refit on the full
   window. The bias terms make the stack a strict generalization of vector
   calibration, so ensembling and calibration are one fitted layer, not two.
6. **Integration** — backtester gains league passthrough and a predict-filter
   (train pooled, evaluate one league); CLI `backtest --model gbm|ensemble`;
   prediction service gains a fitted-model cache keyed by (league, data version)
   and reports `gbm` + `ensemble` rows in the 1X2 book, with value analysis moving
   to the ensemble probabilities.

## Out of scope (deferred)

Player-level data, injuries/lineups (M5), coupon generation (M3), corners/cards
models (M3), model persistence for GBM boosters (deployment milestone), odds-based
features (would leak the market into the model we benchmark against it).

## Acceptance

- xG matched for ≥97% of matches 2014+ in every league (Understat coverage is
  complete for top-5; residuals must be explainable name mismatches).
- Feature builder passes the truncation-invariance (no-leakage) property test.
- Walk-forward E0 ensemble beats standalone Dixon-Coles on log-loss and RPS and
  narrows the gap to the closing line; the README table is updated with whatever
  the numbers actually are.
- Everything green: ruff, mypy --strict, full test suite incl. Postgres integration.
