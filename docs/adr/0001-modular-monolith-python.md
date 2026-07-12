# ADR 0001: Modular monolith on Python/FastAPI

Status: accepted · 2026-07-12

## Context

The product's core competency is statistical modelling (Poisson-family models, Elo,
later GBMs). Candidate stacks: NestJS backend + Python ML sidecar, or Python end-to-end.
Deployment targets are unknown; dev machine is WSL2 with 7.7 GB RAM.

## Decision

Python end-to-end as a modular monolith: FastAPI for HTTP, bounded-context packages
(`data`, `markets`, `models`, `betting`, `evaluation`) with no cross-imports of internals.
No microservices, Kubernetes, Elasticsearch, or message queues in development.

## Consequences

- One language, one test suite, no serialization boundary between API and models.
- Module boundaries keep later extraction (e.g. a separate inference service) cheap.
- The API layer must stay thin so the monolith doesn't ossify into a ball of mud.
