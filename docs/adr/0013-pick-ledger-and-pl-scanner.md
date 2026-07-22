# 0013 — The forward pick ledger and the PL value scanner

Date: 2026-07-21
Status: accepted

## Context

Phases 0–2a closed the "does the model beat the market?" question with a
null: blended-at-open true CLV is significantly negative in all five leagues,
the market never moves toward the model, and the meta-gate found no early
timing edge (ADR 0010, ADR 0011). What survives is the mechanism the
literature actually validates with real money — Kaunitz, Zhong and Kreiner
bet soft books against a consensus fair price, not a better forecast — and
its documented failure mode, limit cuts.

The operator plays only Polish-licensed books, where a **12% turnover tax**
is collected on the stake: a winning bet returns `quoted × 0.88`. That tax is
larger than any edge this project has ever measured, so a comparison on
quoted prices is not merely imprecise, it inverts conclusions. Meanwhile the
2026-07 audit observed that in Poland the value does not live in the prices
at all: it lives in promotions (boosts, "Gra bez podatku", freebets) that
nobody prices rigorously.

The odds tape (ADR 0012) records Pinnacle daily but **no Polish book** — so
the prices the operator can actually take must be entered by hand at bet
time, and the sharp anchor must come from the tape.

## Decision

**1. The sharp anchor is primary; the model is secondary and cannot flip a
verdict.** `services/scanner.py` computes the edge against the Shin-de-margined
Pinnacle fair from the tape. A supplied model probability produces an
informational `edge_model` column only. This is the Phase 0–2a verdicts
encoded in the product surface rather than restated in prose.

**2. Every comparison runs on effective prices.** `betting/effective.py`:
`effective_price(quoted) = quoted × 0.88`, or `× 1.0` under a tax-free promo.
Prices are `Decimal` end to end. A taxed favourite below ~1.14 returns less
than the stake when it wins, and the math is allowed to show that.

**3. Promotions are priced as instruments, not footnotes.** `promo_ev` values
a quote twice against the same fair probability — bare taxed, and with its
promo (tax-free, boosted price, payout haircut for conditioned winnings) —
so `promo_value` is the EV the promotion itself contributes. Betclic's "Gra
bez podatku 2.0" is worth +13.6% of payout, which is why it is the default
venue for the measurement season; the ledger tracks the 1,000 PLN tax-free
turnover allowance per bookmaker and **refuses** a tax-free pick that no
longer fits, because past the limit the promo requires a ≥50%-odds AKO that
a single cannot satisfy.

> **Correction (2026-07-22, ADR 0017).** The full regulamin says otherwise
> on two points: a stake straddling the limit qualifies *in full* (§3
> ust. 4), and past the limit singles pay Wskaźnik **0,94** — a 6% tax —
> not the bare x0.88 (§3 ust. 11 pkt 1); the ≥50%-AKO share restores 1,0
> on everything, it is not a precondition for any relief. The allowance
> logic and the regime routing now live in ADR 0017.

**4. Freshness is payload, not metadata.** The tape snapshots daily, so the
"live" anchor can be hours old. `fair_at` returns the latest **complete**
selection set at or before the asked-for instant — never a later one — and
every verdict and every pick carries the anchor's `observed_at`. An anchor
older than 30 hours downgrades PLAY to **STALE**: a stale sharp line is a
prompt to fix the recorder, not a basis for a bet.

**5. Line mismatch is NO ANCHOR, never an approximation.** If the operator's
line is not the line Pinnacle currently quotes, the scanner refuses to
compare rather than anchoring on a different market.

**6. The ledger carries the two-label CLV decomposition per bet** (the ADR
0011 design applied to real money):

- `clv_sharp = price_sharp × Shin(close) − 1` — timing alone. `price_sharp`
  is the Pinnacle quote at bet time, auto-filled from the tape at log time so
  it can never be reconstructed favourably after the fact.
- `clv_exec = price_effective × Shin(close) − 1` — the PLN-real number, on
  the price actually executed after tax or promo.
- `clv_exec − clv_sharp` is the venue/shopping/promo component. Under the
  Phase 0–2a verdicts this is the only place value can live, and the
  decomposition is what will say so honestly when the season ends.

**7. Auto-settlement joins tape naming to canonical matches at analysis
time**, with ±1 day tolerance on the kickoff date. Picks that cannot be
matched are **listed, not guessed** (quarantine-not-drop, as in ingestion);
the operator settles them by id or extends the odds-api name map.

## Consequences

- Expect mostly "NO BET". That is the honest output of a 12% tax against a
  market this project has measured itself unable to beat, and the scanner is
  built to say it rather than manufacture action.
- The decision variable for 2026/27 is the CLV ledger, not ROI. A positive
  `clv_exec` driven entirely by `clv_exec − clv_sharp` means the promo
  structure paid, not that the model predicted — and the ledger will
  attribute it that way.
- Limit cuts (the Kaunitz failure mode) arrive late at 2–5 PLN stakes but
  will arrive; the ledger is the instrument that will show when.

## Rejected alternatives

- **Scanning on quoted prices with a tax footnote** — the tax exceeds every
  edge measured here; a footnote would let a losing bet read as value.
- **Model fair as the primary anchor** — contradicted by the project's own
  Phase 0–2a results; the audit found `value_analysis` doing exactly this
  (finding A3) and the scanner must not repeat it.
- **Approximating a missing line** (nearest tape line) — a fair price for a
  different market is not an anchor, it is a plausible-looking error.
- **Automatic PL price feeds** (odds-api.io free tier carries Betclic PL and
  STS PL) — evidence-gated for later; manual operator entry stays the ground
  truth until quoted-vs-feed agreement has been validated for weeks.
