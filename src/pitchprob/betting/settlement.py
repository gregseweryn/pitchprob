"""Realized bet settlement from final scores (ADR 0010).

The contract is gross return per unit stake: a winning bet at decimal price
``p`` returns ``p``, a push returns the stake (1.0), a loss returns 0.0.
Quarter lines split the stake across the two adjacent half lines — the exact
mirror of the probability-side split in ``pitchprob.markets.goals``, and the
two are property-tested to agree cell-for-cell so that expected and realized
settlement can never drift apart.

Lines are ``Decimal`` (as stored in the odds table): line arithmetic must be
exact — a push decided by float rounding would be a settlement bug.
"""

from decimal import Decimal
from typing import Literal

MatchSelection = Literal["home", "draw", "away"]
TotalsSelection = Literal["over", "under"]
AhSide = Literal["home", "away"]

_QUARTER = Decimal("0.25")


def _is_quarter(line: Decimal) -> bool:
    if (line * 4) % 1 != 0:
        raise ValueError(f"line must be a multiple of 0.25, got {line}")
    return (line * 2) % 1 != 0


def _validate_score(ft_home: int, ft_away: int) -> None:
    if ft_home < 0 or ft_away < 0:
        raise ValueError(f"goals cannot be negative, got {ft_home}-{ft_away}")


def settle_1x2(
    selection: MatchSelection, ft_home: int, ft_away: int, price: float
) -> float:
    """Gross return of a 1X2 bet: price on the realized outcome, else 0."""
    _validate_score(ft_home, ft_away)
    if selection not in ("home", "draw", "away"):
        raise ValueError(f"unknown 1X2 selection {selection!r}")
    if ft_home > ft_away:
        outcome = "home"
    elif ft_home == ft_away:
        outcome = "draw"
    else:
        outcome = "away"
    return price if selection == outcome else 0.0


def settle_totals(
    selection: TotalsSelection, total_goals: int, line: Decimal, price: float
) -> float:
    """Gross return of an over/under bet at ``line``. Integer lines push on
    the exact total; quarter lines split the stake across the half lines."""
    if total_goals < 0:
        raise ValueError(f"total goals cannot be negative, got {total_goals}")
    if selection not in ("over", "under"):
        raise ValueError(f"unknown totals selection {selection!r}")
    if _is_quarter(line):
        lower = settle_totals(selection, total_goals, line - _QUARTER, price)
        upper = settle_totals(selection, total_goals, line + _QUARTER, price)
        return (lower + upper) / 2.0
    diff = Decimal(total_goals) - line
    if diff == 0:
        return 1.0
    won = diff > 0 if selection == "over" else diff < 0
    return price if won else 0.0


def _settle_ah_margin(margin: int, line: Decimal, price: float) -> float:
    if _is_quarter(line):
        lower = _settle_ah_margin(margin, line - _QUARTER, price)
        upper = _settle_ah_margin(margin, line + _QUARTER, price)
        return (lower + upper) / 2.0
    adjusted = Decimal(margin) + line
    if adjusted == 0:
        return 1.0
    return price if adjusted > 0 else 0.0


def settle_asian_handicap(
    side: AhSide, ft_home: int, ft_away: int, line: Decimal, price: float
) -> float:
    """Gross return of an Asian-handicap bet on ``side`` at the *home* line
    (football-data convention, as in ``markets.asian_handicap``): home covers
    when ``ft_home - ft_away + line > 0``; the away bet mirrors at ``-line``.
    """
    _validate_score(ft_home, ft_away)
    if side not in ("home", "away"):
        raise ValueError(f"unknown Asian-handicap side {side!r}")
    margin = ft_home - ft_away
    if side == "away":
        margin, line = -margin, -line
    return _settle_ah_margin(margin, line, price)
