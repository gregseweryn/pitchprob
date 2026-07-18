# Phase 2 — The CLV meta-gate

Status: draft (design written after the Phase 0/1 measurements; supersedes
nothing)
Author: Claude + Grzegorz

## What Phase 0/1 established (the priors this design must respect)

1. Blended-at-open true CLV is **significantly negative in all five leagues**
   (−0.8% to −1.3%, p ≤ .003): the closing line moves against the current
   selector's bets.
2. The movement study: **DC-vs-open divergence is an error signal, not a
   steam signal.** The market never moves toward the model on average
   (P = 0.47–0.52), significantly against it in D1, and worst in the largest
   divergences. France is flat (its line barely moves at all).
3. The naive selector's +6.3% AH "CLV" is an artifact suspect: best-price
   (market-max) outliers measured against a Pinnacle fair, conditioned on
   unmoved lines. **Lesson: never train or gate on a label that conflates
   timing with line shopping.**

Consequently the meta-gate's honest hypothesis space includes three
outcomes, all acceptable: (a) a conditionally CLV-positive subset exists and
the gate finds it; (b) none exists and the gate's correct output is *"do not
bet early with this model"* — a conclusion a real-money operation must be
able to reach; (c) the *fade* signal (market moves against the model where
it diverges most) is itself exploitable — which must then be shown to be
timing value, not repackaged line shopping.

## Design

### Candidate universe and labels

Every (match, market, selection) with an opening Pinnacle book:
1X2 (3/match) + OU 2.5 (2) + AH (2) ≈ 7 candidates/match ≈ 130k over
2019–2026 across five leagues (AH labels only where the closing line matches
the opening line, ~62%).

Two labels, kept strictly apart:

- **`clv_sharp` = Pinnacle open price × Shin(Pinnacle close) − 1** — pure
  timing value; artifact-free; **the training target and the primary
  endpoint.**
- **`clv_exec` = best open price × Shin(Pinnacle close) − 1** — what
  execution with line shopping would have realized; reported alongside,
  never trained on.

### Features (all computable at the opening snapshot; no closing data)

- Model side: DC probabilities, signed divergence from open fair (per
  selection), divergence L1, effective probability for OU/AH.
- Market side (`features/market.py`): Shin fair, overround, bet365-vs-
  Pinnacle KL, max/avg spread per selection, favourite fair probability,
  price band, AH line.
- Context: league (categorical — F1 behaves differently), season phase,
  days into season.
- As-of history (walk-forward maintained, truncation-invariant): rolling
  model log-loss vs open over trailing N predictions, rolling realized
  `clv_sharp` of past gated candidates, per-league movement agreement to
  date.
- Phase 2b: DC parameter-uncertainty spread from a weighted-likelihood
  bootstrap (`models/uncertainty.py`, ~30 refits — cheap with analytic
  gradients) and, when the ensemble is priced, component disagreement.

### Model and protocol

- XGBoost **regressor** on `clv_sharp`, shallow (max_depth ≤ 3), strong
  regularization, early stopping on a temporal holdout — the exact
  discipline `GbmModel` already implements.
- **Walk-forward**: the meta-model for prediction window W trains only on
  candidates whose kickoff precedes W's start (labels are known at kickoff).
  Any hyperparameter search is nested inside the training window (temporal
  inner split), never tuned on reported results.
- Gate: bet candidate iff predicted `clv_sharp` > buffer (default 0.5%,
  covering fair-estimate noise); stake at best open price; both labels
  recorded.
- The signed formulation lets the gate learn *fade* patterns natively: a
  candidate whose model probability sits far **below** the open fair is a
  legitimate candidate whose predicted CLV may be positive.

### Evaluation (prespecified)

- Harness integration as `--selector meta`; compared against `blended` via
  `pitchprob experiment compare` (paired, block bootstrap, identical
  fixtures).
- Primary endpoint: realized `clv_sharp` of gated bets, weekly-block 95% CI,
  across all five leagues pooled and per league.
- Secondary: realized `clv_exec`, ROI, bet volume, error-analysis segments.
- Multiplicity: the per-league family is BH-corrected; the pooled result is
  the headline.
- **Prespecified nulls**: if no gating threshold yields significantly
  positive pooled `clv_sharp`, the published conclusion is "no early edge
  with current models" and the betting recommendation remains bet-at-close
  line shopping only. A positive `clv_exec` with null `clv_sharp` is
  labeled line-shopping value, not timing edge.

### Leakage tripwires (tests written first)

- Truncation invariance: meta features for a candidate at date t are
  byte-identical when all data ≥ t is deleted.
- Label availability: the meta training frame for window W contains no
  candidate with kickoff ≥ W.start (RecordingModel-style proof).
- No closing-derived feature: feature builder rejects any column whose name
  matches the closing namespace.

## Deliverables

`betting/meta.py` (candidate frame builder + regressor + gate),
`models/uncertainty.py` (Phase 2b), harness `--selector meta`, experiment
comparisons for five leagues, README write-up of whichever verdict the data
returns, ADR 0011.
