# 0012 — The odds tape: live tick recorder on The Odds API

Date: 2026-07-19
Status: accepted

## Context

ADR 0011 shifted the program's forward emphasis to new information: the two
historical snapshots (open/close) cannot support steam or timing features,
and nothing historical can be backfilled. The user authorized tick
recording; the operator constraint is Poland — only PL-licensed bookmakers
are playable (Superbet, STS, Fortuna, …), no Betfair, no Pinnacle. The
Betfair historical-data purchase is **skipped** (Phase 2a null weakened the
case; the exchange is unplayable from Poland anyway; forward tape is free).

## Decision

1. **Append-only tape** (`odds_ticks`): every quote from every visible
   bookmaker, raw team names (canonicalization at analysis time), one
   `observed_at` per snapshot, no uniqueness constraints — the tape records,
   analysis judges.
2. **Source: The Odds API v4, free tier** (500 credits/month; a request
   costs regions × markets). Conventions match the historical odds table
   (h2h→1x2, totals→ou, spreads→ah with both sides carrying the *home*
   handicap) so tape and history join cleanly. The client never caches and
   surfaces remaining quota after every run; the API key never reaches logs.
3. **Cadence:** `pitchprob record-odds --league all` ≈ 15 credits → one
   daily snapshot fits the free tier with headroom
   (`scripts/record-odds.sh`, scheduled via Windows Task Scheduler →
   `wsl.exe`). Densification near kickoff is a later, evidence-gated step.

## First live snapshot (2026-07-19, 1 credit)

- The 2026/27 EPL opening round is **already priced** (10 events, 630 h2h
  ticks) — a month of pre-season line history is on the table if the
  recorder runs daily from now.
- 21 bookmakers visible, including **pinnacle** (live sharp reference) and
  **betfair_ex_eu** (exchange prices — measurable even though unplayable).
- **No Polish-licensed books are carried** (no Superbet/STS/Fortuna; Betclic
  appears only as betclic_fr). Consequence: the tape measures the market and
  the sharp references; the odds the operator can actually take in Poland
  must be captured at bet time in the forward pick ledger (manual entry of
  the executed PL price). Every real-money conclusion will be computed on
  those executed prices — EU best prices are not achievable in Poland and
  will not be presented as such.

## Rejected alternatives

- **Betfair historical purchase** — see Context; revisit only if the live
  tape shows movement structure worth backfilling.
- **Scraping PL bookmaker sites** — ToS-problematic; excluded per the
  program's standing rule.
- **Caching API responses** — a tape that can serve stale prices is worse
  than no tape.
