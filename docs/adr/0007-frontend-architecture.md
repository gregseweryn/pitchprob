# ADR 0007: Dashboard architecture (Next.js over the read API)

Status: accepted · 2026-07-14

## Context

M1–M3 expose everything through a FastAPI read API and a CLI. A browser
surface is needed for market books, coupons, and backtest reports. The dev
environment is WSL2 without root; Node therefore runs from a user-space
tarball (`~/.local/node`, pinned via `.nvmrc`).

## Decisions

1. **Next.js 15 (App Router) + TypeScript + Tailwind** in `frontend/`,
   talking to FastAPI over HTTP. Server components fetch on the server
   (`API_URL`, default `http://localhost:8000`); interactive forms are small
   client components. No client-side state library: the data is read-mostly
   and request-scoped — add one only when live updates (M5) demand it.
2. **Hand-rolled Tailwind components** (cards, tables, badges) in the shadcn
   idiom rather than the shadcn CLI: fewer generated files to audit now, and
   shadcn can be layered in later without rework since both are Tailwind.
3. **The API stays the single source of truth.** The frontend renders what
   the API says — including disclaimers and caveats, which are part of the
   payload contract, not optional decoration. New endpoints needed by the UI
   (teams list, coupon generation) are added to the API with tests; the
   frontend never reimplements model math.
4. **Python and Node toolchains stay independent**: `make check` gates the
   backend; `npm run lint && tsc --noEmit && next build` gates the frontend.

## Consequences

- Coupon generation moves server-side of the browser boundary (POST
  /v1/coupons), so CLI and web share one implementation.
- The FastAPI process must allow CORS from the dashboard origin in dev.
- Model-fit latency on first prediction per league (~seconds) is surfaced
  with loading states rather than hidden by pre-warming — honest UX first,
  warm-up jobs are an M5 concern.
