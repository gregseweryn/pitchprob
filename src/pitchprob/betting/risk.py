"""Exposure limits and the drawdown circuit breaker (Phase 3, ADR 0015).

What this layer is *not* for: at a 500 PLN notional bankroll and flat 2-5
PLN stakes there are 100-250 bets of runway, so ruin is not a risk worth
engineering against. Two things are:

1. **Correlated exposure would corrupt the CLV sample.** Two bets on one
   fixture are not two observations — they share the same match, the same
   team news, often the same price move — and the weekly block bootstrap
   the ledger reports with cannot see dependence *inside* a block's match.
   So the per-match cap equals the single-stake cap: one bet per fixture,
   enforced rather than intended.
2. **A logic or execution fault should surface early.** The drawdown
   breaker is a smoke alarm, not capital preservation: at these stakes a
   15% drawdown is 75 PLN, which is cheap to lose and expensive to ignore.

Every threshold is fixed in advance (ADR 0015) for the same reason the
experiment registry pins its hypotheses: a limit chosen after seeing the
losses is not a limit. Money arithmetic is Decimal throughout, exact to the
grosz; boundaries are inclusive — a limit reached is a limit hit.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

#: Program parameters agreed with the operator for the 2026/27 measurement
#: season. The stake band is a *program* choice (flat staking is the design,
#: so a 0.50 PLN bet is a different experiment, not a smaller one); the
#: exposure caps are fractions of bankroll and scale with it.
DEFAULT_BANKROLL_PLN = Decimal("500")
MIN_STAKE_PLN = Decimal("2")
MAX_STAKE_PLN = Decimal("5")

#: Fractions of bankroll: 5% of the roll may be at stake in one day (about
#: five bets), 15% of drawdown stops betting.
DAILY_STAKE_FRACTION = Decimal("0.05")
DRAWDOWN_FRACTION = Decimal("0.15")

#: Unsettled picks allowed at once: 15 x 5 PLN = 75 PLN in flight, matching
#: the drawdown threshold — the most the ledger can lose before the breaker
#: would have fired anyway.
MAX_OPEN_PICKS = 15

_GROSZ = Decimal("0.01")


class RiskRefusal(ValueError):
    """The ledger declined to record a bet: a limit, or the breaker.

    A ``ValueError`` subclass so every existing caller that already treats
    bad bets as bad parameters keeps working; a distinct type so the CLI can
    say *why* the bet was refused and how to override it deliberately.
    """


@dataclass(frozen=True, slots=True)
class RiskLimits:
    bankroll_pln: Decimal
    min_stake_pln: Decimal
    max_stake_pln: Decimal
    max_match_stake_pln: Decimal
    max_daily_stake_pln: Decimal
    max_open_picks: int
    max_drawdown_pln: Decimal

    def __post_init__(self) -> None:
        if self.min_stake_pln <= 0 or self.max_stake_pln < self.min_stake_pln:
            raise ValueError(
                f"incoherent stake band: {self.min_stake_pln}-{self.max_stake_pln}"
            )
        if self.max_match_stake_pln < self.min_stake_pln:
            raise ValueError(
                f"per-match cap {self.max_match_stake_pln} is below the minimum "
                f"stake {self.min_stake_pln}: no bet could ever be placed"
            )
        if self.max_daily_stake_pln < self.max_stake_pln:
            raise ValueError(
                f"daily cap {self.max_daily_stake_pln} is below the single-stake "
                f"cap {self.max_stake_pln}: the caps contradict each other"
            )
        if self.max_open_picks < 1:
            raise ValueError(f"max_open_picks must be >= 1, got {self.max_open_picks}")
        if self.max_drawdown_pln <= 0:
            raise ValueError(
                f"max_drawdown_pln must be positive, got {self.max_drawdown_pln}"
            )

    @classmethod
    def for_bankroll(cls, bankroll: Decimal) -> "RiskLimits":
        """Scale the exposure caps to a bankroll, keeping the stake band.

        The band is a program parameter (flat staking), so it does not move
        with the roll — only the aggregate caps do.
        """
        return cls(
            bankroll_pln=bankroll,
            min_stake_pln=MIN_STAKE_PLN,
            max_stake_pln=MAX_STAKE_PLN,
            max_match_stake_pln=MAX_STAKE_PLN,
            # Quantised to the grosz: a limit is money, and "25.000 PLN" in
            # a refusal message reads as a rounding artefact rather than a
            # rule the operator agreed to.
            max_daily_stake_pln=(bankroll * DAILY_STAKE_FRACTION).quantize(_GROSZ),
            max_open_picks=MAX_OPEN_PICKS,
            max_drawdown_pln=(bankroll * DRAWDOWN_FRACTION).quantize(_GROSZ),
        )


DEFAULT_LIMITS = RiskLimits.for_bankroll(DEFAULT_BANKROLL_PLN)


@dataclass(frozen=True, slots=True)
class ExposureState:
    """What the ledger already carries, as of the candidate bet."""

    #: Already staked on this fixture (any market, any book).
    match_stake_pln: Decimal
    #: Already staked today, by the bet's own placement date in UTC.
    daily_stake_pln: Decimal
    #: Picks logged but not yet settled.
    open_picks: int


@dataclass(frozen=True, slots=True)
class LimitViolation:
    limit: str
    attempted: Decimal | int
    allowed: Decimal | int

    def describe(self) -> str:
        return f"{self.limit}: {self.attempted} would exceed the limit {self.allowed}"


@dataclass(frozen=True, slots=True)
class DrawdownState:
    equity_pln: Decimal
    peak_equity_pln: Decimal
    drawdown_pln: Decimal

    def fraction_of(self, bankroll: Decimal) -> float:
        return float(self.drawdown_pln / bankroll) if bankroll else 0.0


def check_exposure(
    state: ExposureState,
    stake: Decimal,
    *,
    limits: RiskLimits = DEFAULT_LIMITS,
) -> list[LimitViolation]:
    """Every limit the candidate stake would breach — not just the first.

    An operator who fixes one limit only to hit the next learns nothing, so
    the refusal names all of them at once.
    """
    violations: list[LimitViolation] = []
    if stake < limits.min_stake_pln:
        violations.append(
            LimitViolation("min_stake", stake, limits.min_stake_pln)
        )
    if stake > limits.max_stake_pln:
        violations.append(
            LimitViolation("max_stake", stake, limits.max_stake_pln)
        )
    match_total = state.match_stake_pln + stake
    if match_total > limits.max_match_stake_pln:
        violations.append(
            LimitViolation("max_match_stake", match_total, limits.max_match_stake_pln)
        )
    daily_total = state.daily_stake_pln + stake
    if daily_total > limits.max_daily_stake_pln:
        violations.append(
            LimitViolation("max_daily_stake", daily_total, limits.max_daily_stake_pln)
        )
    open_total = state.open_picks + 1
    if open_total > limits.max_open_picks:
        violations.append(
            LimitViolation("max_open_picks", open_total, limits.max_open_picks)
        )
    return violations


def drawdown_state(
    settlements: Iterable[tuple[datetime, Decimal]],
    *,
    bankroll: Decimal = DEFAULT_BANKROLL_PLN,
) -> DrawdownState:
    """Peak-to-current drawdown of realized P&L, in settlement order.

    ``settlements`` are ``(settled_at, profit_pln)`` pairs — profit being
    gross return minus stake. Only settled picks appear: an open bet has no
    realized P&L, and counting it as a loss would trip the breaker on
    positions that may still win.

    Ordering is by settlement time, not placement: money moves when a bet
    settles, and a peak computed in placement order would be an equity curve
    that never existed. The starting bankroll is the first peak, so a ledger
    that only ever loses is measured against 500, not against its own best
    moment after the losing began.
    """
    equity = bankroll
    peak = bankroll
    for _when, profit in sorted(settlements, key=lambda row: row[0]):
        equity += profit
        peak = max(peak, equity)
    return DrawdownState(
        equity_pln=equity,
        peak_equity_pln=peak,
        drawdown_pln=peak - equity,
    )


def breaker_tripped(
    state: DrawdownState, *, limits: RiskLimits = DEFAULT_LIMITS
) -> bool:
    """Has realized drawdown reached the stop? Inclusive at the threshold."""
    return state.drawdown_pln >= limits.max_drawdown_pln


def describe_violations(violations: Sequence[LimitViolation]) -> str:
    return "; ".join(violation.describe() for violation in violations)
