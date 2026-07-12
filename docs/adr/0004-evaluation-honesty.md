# ADR 0004: Walk-forward evaluation against the closing line; honesty policy

Status: accepted · 2026-07-12

## Context

Betting-model projects routinely fool themselves with lookahead leakage, in-sample
evaluation, and profit claims built on soft bookmaker odds.

## Decision

- All evaluation is walk-forward: train strictly on matches before time t, predict t.
- The benchmark is the margin-removed Pinnacle closing line (Shin + multiplicative
  overround removal both implemented; closing columns exist from ~2019).
- Metrics: multiclass log-loss, Brier, RPS, ECE with reliability diagrams.
- Staking simulations (flat + fractional Kelly) are framed as risk analysis with bootstrap
  confidence intervals and CLV, never as profit projections.
- Every user-facing report states that consistently beating the closing line is unlikely.

## Consequences

- EV backtests are restricted to seasons with closing odds; training may use full history.
- The backtester's no-lookahead property is itself under test.
- Reports may look "disappointing" relative to hype; that is the point.
