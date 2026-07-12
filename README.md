# pitchprob

Football probability engine: estimates **calibrated probabilities** for match markets
(1X2, Over/Under, BTTS, Asian Handicap, Correct Score, …), computes expected value
against bookmaker odds, and validates itself with lookahead-free walk-forward backtests
benchmarked against the closing line.

> **Honesty policy:** this project does *not* promise profits and does not try to "beat
> the bookmaker". Its benchmark is the margin-removed Pinnacle closing line — the
> strongest publicly available probability estimate — and its reports state plainly
> when (as expected) the models fall short of it.

Milestone 1 (in progress): probability core — data ingestion (football-data.co.uk,
top-5 European leagues), Dixon-Coles / Elo / Poisson baselines, market derivation from
score matrices, betting math, walk-forward evaluation, CLI + API.

See `docs/superpowers/specs/` for the design document and `docs/adr/` for architecture
decision records.

## Quickstart

```bash
make install          # uv sync (Python 3.12, pinned via uv)
make db-up            # Postgres 16 in Docker (port 5433)
make migrate          # apply schema
make test             # unit tests (no DB needed)
```
