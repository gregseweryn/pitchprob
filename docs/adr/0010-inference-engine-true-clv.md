# 0010 — Inference engine, true closing-line value, multi-market evaluation

Date: 2026-07-18
Status: accepted

## Context

An audit against professional-syndicate practice (Starlizard/Smartodds-style
operations) found three structural gaps, all fixable with data already in the
database:

1. **~1M stored odds quotes were never used as evaluation input.** The `odds`
   table holds early-snapshot *and* closing prices for four books across 1X2,
   OU 2.5 and Asian handicap, but the backtest consumed only the Pinnacle
   close (benchmark + blend anchor) and market-max close (settlement price).
2. **The reported "CLV" had no timing dimension.** Both its legs — settlement
   price and fair probability — came from the closing snapshot, so it
   measured cross-book price dispersion at the close (line-shopping value),
   not "did the market move toward our price after we bet". The Pinnacle
   closing line covers 20,733 of 21,589 matches and the early-snapshot line
   20,717: a real bet-early → measure-at-close loop was computable all along.
3. **The research loop could not certify anything.** No significance testing
   existed (the bootstrap CIs promised in ADR 0004 were never implemented),
   the ablation flag supported exactly one hardcoded family, and published
   results were single-league 1X2 only (~1,730 closing-matched predictions,
   ±5pp ROI bands) while five leagues and three markets sat in the DB.

## Decision

1. **Two simulation clocks.** `--at close` preserves the legacy kickoff
   protocol bit-for-bit (regression-pinned against the published numbers).
   `--at open` is the syndicate clock: the selector sees only the early
   snapshot (prices and Shin-de-margined Pinnacle anchor), settlement happens
   at early-snapshot prices, and **true CLV = early-snapshot price taken ×
   Shin(closing fair) − 1**. A new `benchmark_open_subset` block reports the
   model against the early-snapshot line — the earliest beatable target this
   dataset carries.
2. **Multi-market candidates from one score matrix.** In open mode,
   score-matrix models price OU 2.5 and AH at the quoted early-snapshot line
   inside the walk-forward loop (an `extra_predict` hook on the backtester — same
   fitted model, no second fit, no lookahead). Realized settlement lives in
   `betting/settlement.py` (gross return per unit stake; quarter-line
   split-stake), property-tested to agree cell-for-cell with the
   probability-side `markets.goals`. Auxiliary-market model probabilities are
   *effective* win fractions (`EV = p·price − 1` with pushes/half-wins
   folded in), which is also what a de-margined two-way book quotes, so the
   ADR 0006 log-linear blend stays coherent across markets. AH CLV exists
   only where the closing line matches the early-snapshot line (a moved line
   prices a different bet); coverage is reported.
3. **Block-bootstrap inference** (`evaluation/significance.py`). Matches and
   bets within an ISO week share teams, conditions and bankroll, so the week
   is the exchangeable unit: ROI and CLV confidence intervals resample whole
   weeks (delivering ADR 0004's promise), and every backtest now reports
   `roi_ci`/`clv_ci` with a percentile-inversion p-value floored at 1/n_boot.
4. **Experiment registry with paired comparison**
   (`services/experiments.py`, `pitchprob experiment run|compare`). Runs are
   stored named and content-hashed. Comparisons pair identical fixtures,
   block-bootstrap per-match Δlog-loss/ΔRPS and per-week ΔROI/ΔCLV, and the
   report labels anything that misses the 95% interval a **null** — the
   harness cannot be sweet-talked. `--vs key=value` turns any run into an
   A/B. The `--ablate` flag generalized to a registry
   (`harness.ABLATABLE_FEATURES`).
5. **Shared orchestration** in `services/harness.py` (the composition layer
   per ADR 0001; `evaluation/` stays a pure toolkit). The CLI `backtest` and
   `experiment` commands are thin wrappers over `run_harness`. The divergent
   half-life defaults (model 365 vs callers 390) were unified into
   `models.dixon_coles.DEFAULT_HALF_LIFE_DAYS`.

## Regression pins

The two published protocols were re-run through the new code path and matched
to the last digit before this ADR was accepted (E0 2021-08→2026-05, SQLite
corpus of 21,589 matches):

- naive weekly: LL 0.975533141008687, RPS 0.2006920358336349, 2,189 bets,
  ROI −1.8140703517587935%, CLV +0.4019270546949417%.
- blended refit-28: closing-subset LL 0.9724794502098613, 1,560 bets,
  ROI −0.68910256410256365%, CLV +1.2505271936048817%.

First honest measurement of the new benchmark: on the same subset (n=1,730)
the Pinnacle **early-snapshot** line scores LL 0.95018 vs the close's
0.94640 — the early snapshot is a weaker target, but only ~0.4pp weaker.
Nothing here promises crossing either line; the point is to measure the
right gap.

## Terminology correction (2026-07-20): "open" means "early snapshot"

What this ADR originally called the "opening line" is not the market open.
football-data.co.uk's non-closing columns (PSH, B365H, …) are collected on
Friday afternoons for weekend fixtures and Tuesday afternoons for midweek
ones (per the source's notes.txt) — a late pre-match snapshot, roughly T-3
to T-1 before kickoff and long after books first price a match. Everywhere
this document, the CLI (`--at open`) and the stored metric keys
(`benchmark_open_subset`) say "open", read **early snapshot**: the earliest
price this dataset carries, not the true opening price. The prose above has
been renamed accordingly; the flag and metric-key names keep "open" for
compatibility with stored runs.

The negative true-CLV verdicts survive this correction, conservatively: the
measured window (early snapshot → close) is *shorter* than a true
open → close window, so "the closing line moves against these bets" was
measured over less of the market's movement, not more. What the correction
forbids is any claim about the true market open — this dataset simply does
not contain it.

## Rejected alternatives

- **Per-bet t-tests / IID bootstrap** — bets within a round are correlated;
  unit-level resampling understates variance exactly where it matters.
- **Auxiliary markets in close mode** — betting OU/AH at closing prices with
  a closing anchor answers no question the 1X2 close protocol doesn't;
  refused rather than half-specified.
- **Storing predictions per run for later comparison** — comparisons re-run
  both configurations in-process instead; determinism makes persistence
  redundant and the `backtests` table stays lean.
- **Intraday tick storage** — deferred to the live-operations phase (its own
  ADR); nothing historical can be backfilled from football-data.co.uk.
