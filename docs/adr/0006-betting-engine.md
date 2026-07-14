# ADR 0006: Vig-aware bet selection, NegBin count markets, coupon tiers

Status: accepted · 2026-07-14

## Context

M2's staking simulation quantified the naive-EV trap: betting every selection
with ``model_p × best_price > 1.03`` lost 11.2% over 2,192 bets *while showing
positive closing-line value*. Mechanism: model error is largest exactly where
the favourite-longshot bias makes prices most deceptive, so an EV threshold
against raw prices preferentially harvests longshots whose "edge" is model
noise. Separately, corners/cards markets need dedicated models (goals matrices
cannot price them — ADR 0002), and coupon generation needs a defined
probability semantics.

## Decisions

1. **Market-shrunk selection.** The market's de-margined (Shin) probability is
   treated as a prior that the model updates, not an opponent to beat with raw
   output. Betting probability is the log-linear blend
   ``p_bet ∝ p_model^w · p_market^(1-w)`` (renormalized), with ``w ∈ [0,1]``
   defaulting to 0.4 — the model contributes, the market anchors. A candidate
   qualifies only if (a) EV at the offered price computed with ``p_bet``
   exceeds the threshold, and (b) the offered price is below a hard cap
   (default 8.0): beyond it, edge estimates are dominated by noise regardless
   of blend. Both knobs are backtested, not asserted.
2. **Counts model = shared negative-binomial machinery.** Corners and cards
   are overdispersed counts (variance/mean ≈ 1.2–1.6); Poisson would understate
   tail prices. One ``NegBinCountsModel`` (attack/defence/home-advantage on a
   log link, exponential time decay, shared dispersion, analytic gradient via
   digamma) is instantiated per statistic. Outputs a home×away counts matrix
   consumed by the existing totals machinery.
3. **Coupon tiers are probability bands, not promises.** Safe 80–95%, Balanced
   60–80%, Value 40–60%, High-risk 20–40% of *estimated joint probability*:
   product over legs, legs restricted to distinct matches so independence is a
   reasonable approximation (documented on every coupon). Selections come from
   the full market book (1X2, double chance, DNB, totals, BTTS); each leg
   carries machine-generated reasons from model internals (ratings, expected
   goals, blend vs market). When offered odds exist, coupons within a band are
   ranked by blended EV; without odds they are ranked by joint probability.

## Consequences

- The engine will pass on most bets most of the time; that is correct behavior,
  not a defect. A high blend toward the market (w→0) reproduces the market and
  bets nothing; w→1 reproduces M2's trap. The backtest harness owns w.
- Corners/cards books ship with wider uncertainty than goals books (less
  informative features, no referee data until M5) and say so in the payload.
- Accumulator probabilities inherit single-leg model error multiplicatively —
  tier labels are estimates with compounding uncertainty, stated verbatim in
  coupon output.
