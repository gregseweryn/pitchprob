# ADR 0003: Postgres system of record; long-format odds and predictions

Status: accepted · 2026-07-12

## Context

Research workflows often use flat files (parquet), but this is a product with an API,
idempotent ingestion, and future live-data writes. Odds cover many bookmakers × markets ×
lines × open/close snapshots; predictions cover many models × markets × selections.

## Decision

Postgres 16 as the system of record, SQLAlchemy 2.0 + Alembic. Match statistics are typed
columns (they are queried analytically); `odds` and `predictions` are long/narrow tables
keyed by (match, bookmaker/model, market, selection, line). Team identity is resolved via
a `team_aliases(source, alias)` table because every data source spells names differently.

## Consequences

- Adding a bookmaker, market, or model is a data change, not a schema migration.
- Long format needs pivoting for model training — an acceptable, explicit transform.
- Dev requires Docker Postgres (port 5433); unit tests must not require a DB.
