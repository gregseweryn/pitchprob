# 0011 — CLV meta-gate: implemented, measured, and the null published

Date: 2026-07-19
Status: accepted

## Context

The syndicate program's Phase 2 (spec:
`docs/superpowers/specs/2026-07-18-phase2-clv-meta-gate.md`) asked whether a
meta-model can find the subset of bets whose price the market subsequently
confirms — the precondition for betting early. Phase 0/1 had already
established the hostile prior: blended-at-open true CLV significantly
negative in all five leagues, and model-vs-open divergence acting as an
error signal, not a steam signal.

## Decision

1. **The gate exists and stays** (`betting/meta.py`, `--selector meta`): an
   XGBoost regressor on the timing-only label `clv_sharp = Pinnacle open
   price × Shin(Pinnacle close) − 1`, trained strictly walk-forward (leak
   detection property-tested), features restricted to a bet-time whitelist,
   signed divergence so fade patterns are learnable, and "no bets" as a
   first-class output.
2. **Two CLV labels are permanent vocabulary.** `clv_sharp` (timing only,
   sharp book both sides) is the primary endpoint everywhere; `clv_exec`
   (best listed price vs sharp fair) is execution-flavored and conflates
   line shopping with timing. Every staking block reports both, for every
   selector — a strategy's `clv_exec` without its `clv_sharp` invites
   exactly the misreading documented below.

## Verdict (2021-08 → 2026-05, five leagues, 1X2+OU+AH at the open)

The paired comparison against the blended baseline showed "CLV significantly
better" in 4 of 5 leagues — on `clv_exec`. The primary endpoint refused:
realized `clv_sharp` of gated bets was E0 −1.32% (significantly negative,
p=.037), SP1 +0.17% (null), D1 +0.25% (null), I1 −0.66% (null), F1 −0.67%
(null); pooled ≈ −0.4%. The gate concentrated in Asian handicap where the
best-vs-sharp spread is widest (I1: +14.8% `clv_exec` on AH vs −0.66%
`clv_sharp`). **The improvement is line-shopping value, not timing edge.**

Prespecified conclusion, adopted: **no early timing edge with current
models.** The betting recommendation remains bet-at-kickoff with line
shopping only, and the real-money unlock (which requires a demonstrated
CLV-positive subset) is not granted by this phase.

## Consequences

- The program's forward emphasis shifts to Phase 5: the odds tick recorder
  (the tape must start before the 2026/27 season) and the forward pick
  ledger — new information, not further mining of two historical snapshots.
- Phase 2b (DC bootstrap uncertainty, ensemble disagreement, market-feature
  enrichment) remains open but carries a weakened prior; it must clear the
  same prespecified endpoint to change any recommendation.
- The risk layer (Phase 3) proceeds — it protects whatever is eventually
  staked, regardless of which strategy earns the unlock.

## Rejected alternatives

- **Gating on `clv_exec`** — trains the model to chase max-book outliers;
  the "edge" is the bookmaker mix, not the market's information flow.
- **Cherry-picking D1/SP1** (the two mildly positive leagues) — the
  per-league family is a multiplicity trap; the pooled endpoint was
  prespecified and it is negative.
- **Burying the result** — contradicts ADR 0004; the null is in the README
  with the same prominence as any positive result would have received.
