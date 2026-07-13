# ADR 0005: One stacking layer = ensemble + calibration

Status: accepted · 2026-07-13

## Context

M2 adds a third model (GBM) next to Dixon-Coles and Elo, plus a calibration
requirement. Treating "combine models" and "calibrate the result" as separate
fitted stages doubles the holdout bookkeeping and the leakage surface.

## Decision

The ensemble is log-linear pooling with per-class biases,

    log p_ens,c ∝ Σ_i w_i · log p_i,c + b_c

fit by NLL on a temporal holdout carved from the end of each training window
(components are fit on the window minus holdout to produce honest stacking
inputs, then refit on the full window for prediction). With a single component
this reduces exactly to vector/temperature calibration, so the stack *is* the
calibration layer.

A standalone calibration module (temperature, per-class isotonic) still exists
in `evaluation/` for calibrating individual models and for reliability
reporting, but the production path has one fitted combination stage, not two.

## Consequences

- ~5 free parameters fit on ~380 holdout matches: low variance, hard to overfit.
- Ensemble weights are refit inside every walk-forward window — no global
  weight leakage across time.
- Isotonic (non-parametric) calibration of the *ensemble* is intentionally not
  applied; if reliability plots ever show structural miscalibration the
  parametric stack cannot fix, that is an M3 conversation with evidence.
