# ADR 0002: Score matrix as the model↔market contract

Status: accepted · 2026-07-12

## Context

The platform must price many markets (1X2, O/U at arbitrary lines, BTTS, Correct Score,
Asian Handicap with quarter-line push/half-win semantics, …) from several models, and more
of both will be added later.

## Decision

Goal-based models emit a single artifact: a score probability matrix P(home=i, away=j),
truncated at 10 goals and renormalized. The `markets` package is a library of pure
functions over that matrix. Outcome-space models (Elo) emit 1X2 directly and simply don't
support goals markets.

## Consequences

- N models × M markets requires N + M implementations, not N × M.
- Market functions are trivially property-testable (probabilities sum to 1, complements
  agree, AH(0) ≡ DNB) independent of any model.
- Markets not derivable from goals (cards, corners) need their own models later — by
  design, not as a hack.
