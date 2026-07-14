# ADR 0008: API-Football as a research instrument (free tier)

Status: accepted · 2026-07-15

## Context

An API-Football key is available on the Free plan. Verified constraints
(probed live, stated by the API itself): 100 requests/day; seasons
**2022–2024 only**; no `next` fixtures parameter; no live/odds endpoints.
The originally envisioned "live information engine" (current-season
fixtures, injuries, lineups) is entirely behind the paid tier. Separately,
M4.5 taught us to demand measured evidence before investing in any new
signal.

## Decisions

1. **The free key is a research instrument, not a live feed.** We use it to
   answer one question with walk-forward evidence: *do player-availability
   features improve match probabilities?* The `/injuries` endpoint returns a
   full league-season per request (~3k records), so the whole corpus for 5
   leagues × seasons 2022/23–2024/25 costs ~15 requests.
2. **The answer gates the spend.** If absence features measurably improve
   log-loss/RPS (or CLV) in the 2023–2025 walk-forward window, upgrading to
   a paid plan for current-season data is justified by numbers; if not, the
   user saved a subscription. No LLM news layer is built either way until
   this cheaper signal is measured (see the M4.5 lesson).
3. **Client design respects the quota**: persistent on-disk cache keyed by
   URL (historical seasons are immutable), the daily budget read from
   `/status` is logged, and the adapter is offline-tested — network touches
   happen only in explicit CLI commands.
4. Upcoming fixtures for coupons stay on football-data's free fixtures.csv;
   API-Football adds nothing there on the free plan.

## Consequences

- New `injuries` table (player, team, match date, reason, season); absence
  *counts* join matches by team + date with the same ±1-day tolerance as xG.
- Known data limitation, documented rather than hidden: historical injury
  lists may include post-hoc edits; treat the experiment's positive result
  as an upper bound on live value.
- A paid-tier upgrade would change only the client's season guard, not the
  architecture.
