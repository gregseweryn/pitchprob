# ADR 0009: Deployment shape — two images, one compose file, CI gates

Status: accepted · 2026-07-16

## Context

M1–M5 run from a dev checkout. The platform needs a reproducible deployment
unit. The original wishlist named Kubernetes/Elasticsearch/RabbitMQ; nothing
in the measured workload (single-node read API, model fits of seconds,
21k-row tables) justifies any of them yet.

## Decisions

1. **Two images.** `Dockerfile.api`: python:3.12-slim, uv-built venv in a
   builder stage, non-root runtime, entrypoint runs `alembic upgrade head`
   then uvicorn — single-node deploys migrate on boot, fail-fast on schema
   drift. `frontend/Dockerfile`: node:22-alpine builder → Next standalone
   output, non-root runtime. Images carry no secrets; configuration enters
   via environment only.
2. **One compose file, profiles for scope.** The existing dev flow
   (`make db-up` = db only) is untouched; `--profile stack` adds `api` and
   `web` with healthcheck-gated startup order. Server-side rendering reaches
   the API on the internal network (`API_URL=http://api:8000`); browsers use
   the published port.
3. **CI mirrors the local gates exactly** (GitHub Actions): backend job =
   ruff + mypy + full pytest against a Postgres service container (the
   integration tests run in CI even when a laptop skips them); frontend job
   = eslint + tsc + production build. No deploy step until there is a
   deploy target.
4. **Kubernetes et al. stay out** until a measured need (horizontal scale,
   queue depth) exists. The compose file is the deployment contract; k8s
   manifests would be a translation, not a redesign.

## Consequences

- `pitchprob` CLI work (ingestion, backtests) stays host-side; the api image
  serves reads. A jobs image is a later need.
- Model-fit latency on first request per league exists in the container
  exactly as in dev; acceptable and stated (ADR 0007).
- CI has no publish/deploy credentials — by design until a target exists.
