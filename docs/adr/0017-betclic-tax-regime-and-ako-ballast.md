# 0017 — Betclic "Bez Podatku 2.0": the auto tax-regime router, promos as configuration, and the AKO-ballast verdict

Date: 2026-07-22
Status: accepted

## Context

The operator read the full regulamin of Betclic's "Bez Podatku 2.0" (in
force since 2026-05-21). Three of its provisions invalidate assumptions
this codebase had baked in:

1. **§3 ust. 3–4.** Up to 1,000 PLN of cumulative turnover since the offer
   started, *every* bet pays out with Wskaźnik 1,0 — and if **any** amount
   of the limit remains before a bet, the **whole** bet qualifies
   regardless of its stake. Our `TaxFreeAllowance.covers()` refused a
   straddling stake; the regulamin says it qualifies in full.
2. **§3 ust. 5, 8, 9, 11, 12.** Past the limit the regime depends on the
   share of stakes on "qualifying AKO" (≥2 legs, odds ≥1.20 each, no
   cashouts/voids; rolling over the last 5,000 bets / 12 months):
   - share <50%: singles and non-qualifying AKO pay **Wskaźnik 0,94**
     (a 6% tax — *not* the statutory 12%); qualifying AKO pay 1,0;
   - share ≥50%: **everything** pays 1,0.
   The docstring in `betting/effective.py` claimed the ≥50%-AKO condition
   makes the limit hard for singles, i.e. x0.88 past it. That was factually
   wrong and understated Betclic payouts by 6pp after the limit. (ADR 0013
   §3 carried the same error; it now points here.)
3. **§4 and §8.** Betclic can exclude a user, void winnings, and change or
   withdraw the offer on 24h notice. Promo parameters therefore must be
   *configuration with a kill switch*, never constants inside verdict
   logic.

The P0 consequence sat in the speaking loop (ADR 0016): `feed_quotes`
returned Betclic quotes with no promo terms, so the watch loop priced them
at x0.88 — understating the edge by 12pp of payout and staying silent on
real value. The 2026/27 measurement season (~200–750 PLN of stakes) fits
inside the 1,000 PLN limit, so the correct Betclic multiplier for the
entire season is x1.0.

## Decision

### `scan()` is the effective-price router

A quote whose tax regime is undeclared — every feed quote, a bare operator
quote, a boost without a tax flag — gets its regime from the promo
registry plus the ledger's allowance state. An explicit declaration always
wins: the operator saw the coupon, the router did not. No surface
hard-codes a bookmaker; the registry decides which books have a promo.

### The regime degrades only downward

x1.0 while any allowance remains (§3 ust. 4 makes this stake-independent),
x0.94 once it is spent, x0.88 for unknown books or a killed promo. The
router may understate a payout — it never claims a milder tax than the
account state supports. The x0.94 arm assumes a qualifying-AKO share below
50%, which is true by construction for a single-only season; an account
actually holding ≥50% would pay 1,0 on everything, and we understate it —
the permitted direction.

### Promos are data (`betting/promos.py`), with an env kill switch

`BookPromo` holds the canonical book key, its feed aliases ("Betclic PL"
vs "betclic" — `tax_free_allowance` matches canonically so both spellings
share one allowance), the limit, and the post-limit multiplier.
`PITCHPROB_DISABLED_PROMOS=betclic` flips every surface back to bare
x0.88 pricing without a code change — the §8 response.

### The ledger records the regime it actually priced

`picks.tax_multiplier` (migration `c5a9d7e1f3b6`) stores the exact
multiplier behind `price_effective`, so `clv_exec` stays auditable per
bet; `tax_free` survives as the boolean view (= 1.00). `log_pick` derives
the regime automatically — the regulamin applies the promo to every
qualifying bet whether or not the operator remembers a flag, so auto *is*
the faithful record — with `--tax-free` / `--taxed` as explicit overrides.
Forcing tax-free past a spent limit is refused with a message that names
the x0.94 regime.

### Honesty-as-payload, extended

`SCANNER_CAVEATS` / `LEDGER_CAVEATS` now state the three regimes, that the
router reads the limit **only from picks logged in the ledger** (a coupon
placed outside it inflates the remaining allowance and therefore the shown
edge), and that the regime on the bookmaker's own coupon screen is
decisive — the offer can vanish on 24h notice. Alerts name the applied
regime and order a coupon check. Feed quotes remain `UNVERIFIED` until
`quote-check` clears the feed (ADR 0014); the router changes their price,
never their provenance.

## The AKO-ballast question: analysed, not built

Past the limit, keeping the qualifying-AKO share ≥50% would restore
Wskaźnik 1,0 on everything (§3 ust. 11 pkt 2). Is deliberately betting
"ballast" AKO worth +6pp on the singles?

**Cost of ballast.** Every 1 PLN of singles needs ≥1 PLN of qualifying AKO
(≥2 legs at ≥1.20). A qualifying AKO pays 1,0 post-limit, so its EV cost
is the compounded margin alone: with margin *m* per leg, EV per unit ≈
(1/(1+m))² − 1. At the 5–6% margins typical of PL books that is **−9% to
−11%** of ballast turnover.

**Benefit.** Singles recover 1,0 instead of 0,94: at near-fair prices
(p×q ≈ 1) that is ≈ **+6pp** of stake per 1 PLN of singles.

**Break-even.** (1/(1+m))² ≥ 0.94 ⇔ m ≤ **≈3.1% per leg** — a margin no
PL-licensed book has shown us. Net effect at realistic margins: negative,
roughly −3 to −5pp per 1 PLN of singles once its ballast is paid for.
Real Betclic margins become measurable from the odds-api.io feed once the
key is attached; that measurement is the precondition for ever reopening
this question, and the bar it must clear is 3.1%.

**Conflict with ADR 0015.** Ballast AKO would also break the measurement
design: flat 2–5 PLN stakes are a program parameter, the weekly block
bootstrap cannot price dependence across an AKO's legs, and the two-label
CLV decomposition is defined per single selection. The bets would either
pollute the CLV sample or move real money while excluded from it — both
worse than the 6pp they might recover.

**Verdict: do not build it.** For the measurement season it is moot
anyway — 200–750 PLN of turnover never exhausts the 1,000 PLN limit, so
the post-limit regime never binds. Revisit only if cumulative Betclic
turnover approaches the limit *and* measured feed margins undercut 3.1%
per leg. This paragraph is the record that the option was priced and
declined, not overlooked.

## Consequences

- The watch loop now surfaces Betclic feed value at its true effective
  price; the 2.40-at-fair-0.5 lead reads +20%, not +5.6%. Silence remains
  the common case — the threshold and the sharp anchor still gate.
- The season's Betclic picks record x1.0 and `clv_exec` measures the promo
  honestly; when the promo dies (§8), one env var restores x0.88 pricing
  everywhere at once.
- The ledger's allowance is only as complete as the ledger. Any real bet
  placed outside `pick log` silently inflates the remaining limit — the
  caveats say so, and the UNVERIFIED gate keeps the coupon screen in the
  loop as the final check.
- A future promo at another book is a registry entry, not a code change.

## Alternatives rejected

- **Hard-coding "Betclic = tax-free".** The offer is cancellable on 24h
  notice and the limit is stateful; a constant would be wrong twice.
- **Keeping the stake-must-fit `covers()`.** Conservative but factually
  wrong (§3 ust. 4), and it refused real value at the boundary.
- **An AKO-ballast strategy.** Priced above: negative at any realistic
  margin, and it corrupts the CLV sample the season exists to collect.
- **Asking the operator to declare the regime on every pick.** The promo
  applies automatically at the book; a manual flag records the operator's
  memory, not the payout. Explicit flags remain as overrides for exactly
  the cases where the coupon disagrees with the registry.
