# 0015 — The risk layer: exposure limits, drawdown breaker, weekly report

Date: 2026-07-21
Status: accepted

## Context

Phase 3 of the syndicate roadmap, with the operator's parameters fixed:
**flat 2-5 PLN stakes against a notional 500 PLN bankroll** for the 2026/27
measurement season. ADR 0013 built the ledger that records the bets; this
decides what the ledger refuses.

The obvious framing — "protect the bankroll" — is wrong here, and getting it
wrong would produce the wrong limits. At 2-5 PLN per bet a 500 PLN roll
carries 100-250 bets. Ruin is not a live risk; the season ends from
boredom or from the calendar, not from a losing run. Two other risks are
real:

1. **Correlated exposure would corrupt the CLV sample.** The program's
   decision variable is CLV, reported with a block bootstrap over ISO weeks.
   That bootstrap treats a week as the exchangeable unit, so it can price
   dependence *between* weeks but is blind to dependence *inside* a fixture.
   Two bets on one match — two markets, or two books — share the match, the
   team news and usually the same price move. They arrive in the sample as
   two observations while carrying roughly one observation's worth of
   information, and the CI comes out too narrow.
2. **A logic or execution fault should surface early.** The likeliest way
   this season goes wrong is not variance but a mistake: a scanner bug, a
   misread promo, a mistyped price repeated twenty times.

So the limits are designed for sample integrity and fault detection, and the
drawdown breaker is a smoke alarm rather than a capital control.

## Decision

### Thresholds, fixed in advance

Chosen before any bet exists, for the same reason the experiment registry
pins its hypotheses: a limit chosen after seeing the losses is not a limit.

| limit | value | why |
|---|---|---|
| stake band | 2-5 PLN | the program parameter; flat staking is the design |
| per fixture | **5 PLN** | equals the single-stake cap ⇒ **one bet per fixture** |
| per day | 25 PLN (5% of bankroll) | about five bets a day |
| open picks | 15 | 75 PLN in flight, matching the drawdown stop |
| drawdown stop | 75 PLN (15% of bankroll) | ~15 consecutive losers: variance this deep is worth a look |

The stake band does **not** scale with bankroll — flat staking is a program
choice, so a 0.50 PLN bet is a different experiment, not a smaller one. The
aggregate caps do scale (`RiskLimits.for_bankroll`).

The per-fixture cap is the load-bearing one. Setting it equal to the
single-stake cap is what turns "one bet per fixture" from an intention into
something the ledger enforces, and it is the only limit here that exists to
protect the *statistics* rather than the money.

### Boundaries are inclusive

A limit reached is a limit hit: drawdown of exactly 75.00 PLN trips the
breaker, and a daily total of exactly 25.00 PLN is allowed but 25.01 is not.
Stated because off-by-one at a threshold is the classic way a limit silently
does nothing.

### Drawdown is realized, in settlement order

Equity is `bankroll + cumulative realized P&L` over **settled** picks only,
ordered by `settled_at`. Two deliberate choices:

- **Open picks are excluded.** An unsettled bet has no realized result;
  counting it as a loss would trip the breaker on positions that may still
  win, and at 15 open picks that is 75 PLN of phantom drawdown — the whole
  threshold.
- **Settlement order, not placement order.** Money moves when a bet
  settles. A peak computed in placement order describes an equity curve that
  never existed.

The starting bankroll is the first peak, so a ledger that only ever loses is
measured against 500 PLN rather than against its own best moment after the
losing began.

### The override is a column, not a convention

`log_pick` raises `RiskRefusal` and writes nothing. `override_risk=True`
(CLI `--override-risk`) places the bet anyway and stamps `risk_override` and
`risk_note` on the row — the same sentence the operator was shown when they
chose to bypass it. The weekly report reads those columns back.

An override that leaves no trace is worse than having no breaker: it makes
the season's story unauditable while creating the impression of control. A
pick that breaches nothing is never marked, even when the flag was passed —
the mark means "this bet broke a limit", not "the flag was on".

### The weekly report: two horizons, and a refusal to over-claim

`pitchprob risk report` covers money over the last 7 days and **CLV over the
whole ledger**. Mixing those horizons is how a bad week gets published as a
bad thesis; CLV needs every observation it can get.

**Below four distinct ISO weeks of bets, no confidence interval is
published.** A block bootstrap resampling a single block returns that block
every time, so the "95% CI" collapses to zero width — the most
confident-looking output in the system, produced by the least evidence. The
report prints the means, says `no CI (too few weeks)`, and marks the section
**not a finding**. This mirrors the latency map's resolution floor (ADR
0014): the instrument states what it cannot resolve.

Sharp CLV is printed before exec CLV, and their difference is labelled as
the shopping/promo component — under the Phase 0-2a verdicts that difference
is the only place value has been shown to live.

## Consequences

- Six ADR 0013 ledger tests and two CLI tests exercised stakes and stacked
  fixtures the risk layer now refuses. They opt out explicitly through a
  permissive `RiskLimits` (or `--override-risk`) rather than being rewritten
  around the limits: what they measure — tax-free allowance, settlement, the
  CLV decomposition — is unrelated to exposure.
- The CLI's old "stake outside the 2-5 PLN band" warning is now a refusal.
  The warning survives for the override path, where it is still true.
- Migration `d4b8e2f6a9c1` adds `picks.risk_override` / `picks.risk_note`
  with a server default, so rows written before it read as "not an override"
  rather than NULL.

## Rejected alternatives

- **Kelly or any variable staking.** Kelly sizes bets by edge; this program
  has not demonstrated an edge, so the input would be fiction. Flat stakes
  also keep the CLV sample unweighted, which is what makes the mean readable.
- **A breaker on CLV rather than on money.** Tempting — CLV is the decision
  variable — but CLV arrives days late (it needs the closing line) and is
  the thing being measured. Stopping the experiment when the measurement
  looks bad is how a null result becomes unobservable.
- **Counting open picks as losses for drawdown.** Conservative, and wrong:
  see above.
- **Auto-halving stakes on drawdown instead of stopping.** Changes the stake
  distribution mid-season, which breaks comparability of the very sample the
  season exists to collect.
