# Design: Milestone 1 — Probability Core

Status: approved (2026-07-12) · Author: Claude + Grzegorz

## Problem

Build the foundation of a football betting analytics platform that estimates **calibrated
probabilities** for match markets and computes expected value against bookmaker odds.
The platform never promises profit; its own benchmark — the margin-removed closing line —
is expected to be slightly better than the models, and reports must say so.

The full vision (data platform, live-info engine, news LLM, ML ensemble, coupon
generator, dashboard) is decomposed into milestones. M1 is the probability core:
if the probabilities aren't honest, nothing downstream matters.

## Scope (M1)

- Ingest football-data.co.uk results + odds, seasons 2014/15–2025/26, leagues E0, SP1, D1, I1, F1
- Models: independent Poisson, Dixon-Coles (time-decay weighted MLE), Elo (+ ordered-logit 1X2 map)
- Markets derived from score matrices: 1X2, Double Chance, DNB, O/U (any line), BTTS,
  Correct Score, Asian Handicap (full quarter-line semantics), expected goals
- Betting math: overround removal (multiplicative + Shin), fair odds, EV, fractional Kelly
- Walk-forward backtester; log-loss/Brier/RPS/ECE; reliability diagrams; staking simulation
  vs Pinnacle closing benchmark
- Typer CLI + FastAPI read API
- Out of scope for M1: xG data (Understat, M2), GBM/ensembles (M2), coupon generator (M3),
  frontend (M4), live data & news LLM (M5)

## Architecture

Modular monolith, Python 3.12 (uv-pinned), src layout, DDD-flavored bounded contexts:
`core` (config/db/logging/errors) · `data` (domain, adapters, normalization, repositories)
· `markets` (pure score-matrix → market functions) · `models` (Poisson, Dixon-Coles, Elo
behind a `ProbabilityModel` protocol) · `betting` (pure odds math) · `evaluation`
(walk-forward backtester, metrics, reports) · `api` (FastAPI) · `cli` (Typer).

**Contract:** goal models emit a score probability matrix P(home=i, away=j); every goals
market is a pure function over it. Models and markets never know about each other's
internals. Postgres 16 (docker compose, port 5433) with Alembic migrations; long-format
`odds` and `predictions` tables.

Full schema, model specs, data quirks, risks, and acceptance criteria are recorded in the
approved implementation plan (mirrored in `docs/adr/` where decisions are architectural).

## Acceptance

- No lookahead anywhere (property-tested backtester)
- ECE < 0.03 on 1X2 for Dixon-Coles; RPS within ~5% of margin-removed closing line
- Every market derived from one score matrix; AH(0) ≡ DNB verified by tests
- README reports honest results, including where models lose to the closing line
