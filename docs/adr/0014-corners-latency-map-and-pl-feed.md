# 0014 — Corners on the tape, the line-latency map, and the PL quote feed

Date: 2026-07-21
Status: accepted

## Context

The July 2026 audit set three P1 items: a line-latency map for Polish books
(Etap 3.2 #2), corners markets (Etap 3.1), and the odds-api.io free tier as
an automatic scanner feed (Etap 5). Each was checked against the live APIs
before any of it was built. Two of the three assumptions did not survive.

### The latency map was not measurable from the existing tape

The audit called it "mierzalne już dziś z istniejących danych". It was not,
for three independent reasons:

1. `data/tape/` held **one** snapshot — a single `observed_at`, 4,014 ticks.
   The scheduled recorder had not yet added a second.
2. The Odds API carries **no PL-licensed bookmaker**. ADR 0012 said so; the
   snapshot confirms it — the 22 books visible are `betclic_fr`,
   `unibet_fr/nl/se`, `winamax_de/fr`, `tipico_de`, `codere_it`, `pinnacle`,
   `betfair_ex_eu` and similar. Not one Polish book.
3. Even a perfect year of that tape would not answer the question. One
   snapshot a day quantises every delay to 24 hours, and the phenomenon —
   a slow book copying a sharp move — lives in minutes to hours.

So the audit's claim is corrected rather than quietly satisfied. The
measurement needed a different source, which is what made items 2 and 3
one decision instead of three.

### Corners exist on The Odds API, are Pinnacle-priced, and are late

Measured live on 2026-07-21:

| probe | result | credits |
|---|---|---|
| EPL fixture, 31 days out, 4 corner/card markets | empty | **0** |
| Norway/MLS/Ekstraklasa, ~1–3 days out, corners totals | `leovegas` only | 1 |
| Brazil Série A, ~24h out, 4 markets, region `eu` | **`pinnacle`, all four** | 4 |
| same fixture, corners handicap only | `pinnacle`, home −0.5 ↔ away +0.5 | 1 |

Three facts follow. Corners are served only by `/events/{id}/odds`, one
fixture per request. Billing is per market **returned**, so fixtures with
nothing priced are free — looking too early costs nothing, it is merely
pointless. And the payload shape is identical to the goals markets, so
corners need no new parsing path.

### odds-api.io carries more PL books than the audit found

Its `/bookmakers` catalogue is unauthenticated; read on 2026-07-21 it lists
266 books, of which **Betclic PL, STS PL, eFortuna PL, Betfan PL, LVbet PL
and Superbet** are active. There is **no Pinnacle** — the audit's suspicion
was right. Betfair Exchange is present.

The decisive find was `/odds/movements`: a per-bookmaker, per-market,
per-line **timestamped price history**, retrievable now. A movement series
is a tick series. That is what makes the latency map answerable in days
instead of a season.

## Decision

### 1. Corners: an E0 pilot, bounded twice

`pitchprob record-corners` records `corners_ou` and `corners_ah` for
fixtures inside a 26-hour window, via `record-corners.yml` at 20:00 UTC
(Friday's run covers Saturday, Saturday's covers Sunday). E0 only: ~10
fixtures a round, **~43 of the 500 monthly credits**, taken from the
headroom the main tape leaves.

Spending is bounded twice and both stops are reported, never silent:
`--reserve` (default 60) refuses to spend when the month's remaining credits
fall near the main tape's needs, and `--max-credits` caps a single run. The
main 1x2/ou/ah tape is the load-bearing instrument — the scanner and the
pick ledger both anchor on it — and the corners experiment does not get to
starve the instrument it will be measured against.

Cards (`alternate_totals_cards`) are **excluded**: a separate market, a
separate credit, and the audit rates them P2. They return when corners have
earned the spend.

Corners join `MARKET_SELECTIONS` in `services/tape.py`, so the scanner
verdicts them with no new code — with the caveat that the tape only carries
them from about a day before kickoff.

### 2. The latency map is built now, source-agnostic

`evaluation/latency.py` holds the primitives and `services/latency_map.py`
the report (`pitchprob study latency`). It reads `odds_ticks` whatever
wrote them, so the odds-api.io feed lights it up without new plumbing.

Three failure modes are closed deliberately, because each would bias the
answer toward "books are fast":

- **Censoring.** A book that never follows has no latency, not a large one.
  The median is over reactions only and is always reported beside the
  reaction rate.
- **Margin changes.** Both sides are compared as Shin-de-margined
  probabilities, so a book widening its margin does not read as an opinion.
- **Absence.** A follower with no quote from before the reference moved has
  no baseline; it is `unobserved` and leaves the denominator.

Two limitations are stated rather than hidden: series are per (market,
line), so a book that responds by moving its **line** reads as no reaction
(this understates OU/AH responsiveness — trust 1X2 here); and the report
leads with its **resolution floor**, the median gap between reference
snapshots, and declines to publish anything below it. The evidence bar —
30 observed moves per book — is fixed here, before the data exists.

On today's tape the report correctly says: single snapshot, no measurement,
and which precondition failed.

### 3. odds-api.io is a prototype feed, informational until validated

Free tier: **Betclic PL + STS PL**, 100 requests an hour. Ticks land in
`odds_ticks` with `source="odds-api-io"` and the feed's own naming; the key
travels as a query parameter, so the same scrubbing discipline as ADR 0012
applies. This feed is a source of **followers only** — the sharp anchor
stays Pinnacle from The Odds API, because odds-api.io has none.

**Manual entry remains ground truth.** `pitchprob quote-check log` records
what the operator sees on the bookmaker's own screen and attaches what the
feed claimed at that instant; `quote-check report` judges the feed against a
bar fixed in advance:

- at least **30** checks,
- agreement within **0.02** decimal odds on at least **95%** of comparable
  checks,
- missing-or-stale at most **10%**.

Missing and stale count against the feed exactly as much as wrong prices do:
a quote the scanner cannot get in time is a quote it does not have.

Until a book clears that bar, `pitchprob scan --feed` returns its quotes
with verdict **UNVERIFIED** rather than PLAY. The edge is still computed —
the lead is real; the authorisation is not.

## Consequences

- The audit's Etap 3.2 #2 claim is corrected in `docs/AUDYT-2026-07.pl.md`,
  pinned by a test in `tests/test_docs.py`.
- Monthly credit budget: ~450 main tape + ~43 corners, ~7 spare. Any further
  corners league requires an explicit trade against the main tape.
- The latency map stays at "no measurement" until an odds-api.io key exists
  and movements are recorded. That is a data blocker, not a code one.
- `OddsTickRecord` gained an optional `observed_at`: a tick from a movement
  history knows when it was true, and that timestamp is the entire
  resolution of the latency map.

## Rejected alternatives

- **Corners for all five leagues** (~200 credits/month) — would force the
  main tape down to two markets with zero headroom. Revisit after the pilot
  shows Pinnacle prices corners in the top five as it does in Brazil.
- **Betfair Exchange instead of STS PL** in the two free slots — would give
  a sharp reference inside the same feed and cadence, making the latency map
  cleaner. Rejected because the exchange is unplayable from Poland and the
  operator's second real book is worth more than a tidier measurement.
- **Reading the latency map off `last_update` in the main tape.** The Odds
  API stamps each market with a last-update time, which would sharpen a
  daily snapshot considerably. Noted, not built: it still yields no PL book.
- **Trusting the vendor's market names.** The mapping is built from what
  `pitchprob oddsio probe` observes, because names are the part of a
  third-party feed most likely to differ from the documentation.
