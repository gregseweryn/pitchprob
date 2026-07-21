# pitchprob dashboard

register: product

## What this is

The browser surface of a football probability engine. Users are
quantitatively-minded bettors and analysts reviewing model output: full
market probability books per fixture (1X2 across four models, totals, BTTS,
Asian handicap, corners, cards), risk-tiered coupon suggestions with
machine-generated reasoning, and walk-forward backtest reports.

Two surfaces read the *forward* program rather than the model: the
**scanner** verdicts Polish bookmaker quotes against the odds tape's
de-margined Pinnacle fair, and the **ledger** shows real-money picks with
their CLV decomposition, the weekly report and the risk layer's state.

## Who uses it, where

One person at a desk with coffee, weekend mornings, reading probability
tables the way older generations read the racing form in a broadsheet:
deciding whether any edge is real before a shilling moves. Task-focused,
numbers-first, ambient daylight.

## Product truths the UI must carry

- **The numbers do not promise profit.** Disclaimers and caveats arrive in
  the API payload and are rendered as first-class content, never hidden in
  footers or tooltips.
- **Model disagreement is information.** The four 1X2 models are shown side
  by side; the ensemble leads but never hides its components.
- **Wider uncertainty is stated where it exists** (corners/cards books,
  accumulator tier labels).
- **An instrument that cannot answer says so.** A missing anchor, a stale
  quote or too few weeks for a confidence interval are rendered as named
  refusals, never as a blank cell or a zero — absence and zero mean
  different things and the UI must not conflate them.

## Non-goals

Live scores, bet placement, account management, anything transactional.
This is a reading instrument.
