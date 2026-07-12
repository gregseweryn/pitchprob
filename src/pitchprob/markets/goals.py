"""Goal-market pricing: pure functions over a score probability matrix.

The score matrix is the contract between models and markets (ADR 0002):
``m[i, j] = P(home scores i, away scores j)``, non-negative, summing to 1.
Every function here is deterministic, side-effect free, and model-agnostic.

Asian handicap semantics follow standard settlement rules: quarter lines
(±0.25, ±0.75, …) are split-stake bets on the two adjacent half lines. Since
goal margins are integers and lines are exact multiples of 0.25 (all exactly
representable in binary floating point), settlement sign tests are exact.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import numpy as np
import numpy.typing as npt

ScoreMatrix = npt.NDArray[np.float64]
Side = Literal["home", "away"]

_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class MatchOdds:
    home: float
    draw: float
    away: float


@dataclass(frozen=True, slots=True)
class DoubleChance:
    home_or_draw: float
    home_or_away: float
    draw_or_away: float


@dataclass(frozen=True, slots=True)
class DrawNoBet:
    home: float
    away: float


@dataclass(frozen=True, slots=True)
class Totals:
    over: float
    under: float
    push: float


@dataclass(frozen=True, slots=True)
class Btts:
    yes: float
    no: float


@dataclass(frozen=True, slots=True)
class ExpectedGoals:
    home: float
    away: float


@dataclass(frozen=True, slots=True)
class AhOutcome:
    """Settlement distribution of an Asian-handicap bet (unit stake)."""

    full_win: float
    half_win: float
    push: float
    half_loss: float
    full_loss: float

    def expected_return(self, price: float) -> float:
        """Expected gross return per unit stake at decimal ``price``."""
        return (
            self.full_win * price
            + self.half_win * (price + 1.0) / 2.0
            + self.push * 1.0
            + self.half_loss * 0.5
        )


def _validate(matrix: npt.ArrayLike) -> ScoreMatrix:
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError(f"score matrix must be 2-D, got shape {m.shape}")
    if (m < 0).any():
        raise ValueError("score matrix contains negative probabilities")
    total = float(m.sum())
    if abs(total - 1.0) > _SUM_TOLERANCE:
        raise ValueError(f"score matrix sums to {total!r}, expected 1.0")
    return m


def _margin_grid(m: ScoreMatrix) -> ScoreMatrix:
    h = np.arange(m.shape[0], dtype=np.float64)[:, None]
    a = np.arange(m.shape[1], dtype=np.float64)[None, :]
    return h - a


def _total_grid(m: ScoreMatrix) -> ScoreMatrix:
    h = np.arange(m.shape[0], dtype=np.float64)[:, None]
    a = np.arange(m.shape[1], dtype=np.float64)[None, :]
    return h + a


def match_odds(matrix: npt.ArrayLike) -> MatchOdds:
    m = _validate(matrix)
    margin = _margin_grid(m)
    return MatchOdds(
        home=float(m[margin > 0].sum()),
        draw=float(m[margin == 0].sum()),
        away=float(m[margin < 0].sum()),
    )


def double_chance(matrix: npt.ArrayLike) -> DoubleChance:
    mo = match_odds(matrix)
    return DoubleChance(
        home_or_draw=mo.home + mo.draw,
        home_or_away=mo.home + mo.away,
        draw_or_away=mo.draw + mo.away,
    )


def draw_no_bet(matrix: npt.ArrayLike) -> DrawNoBet:
    mo = match_odds(matrix)
    decisive = mo.home + mo.away
    if decisive <= 0.0:
        raise ValueError("draw-no-bet undefined: P(draw) = 1")
    return DrawNoBet(home=mo.home / decisive, away=mo.away / decisive)


def totals(matrix: npt.ArrayLike, line: Decimal) -> Totals:
    m = _validate(matrix)
    total = _total_grid(m)
    threshold = float(line)
    return Totals(
        over=float(m[total > threshold].sum()),
        under=float(m[total < threshold].sum()),
        push=float(m[total == threshold].sum()),
    )


def btts(matrix: npt.ArrayLike) -> Btts:
    m = _validate(matrix)
    yes = float(m[1:, 1:].sum())
    return Btts(yes=yes, no=1.0 - yes)


def correct_score(matrix: npt.ArrayLike, home_goals: int, away_goals: int) -> float:
    m = _validate(matrix)
    if home_goals >= m.shape[0] or away_goals >= m.shape[1]:
        return 0.0
    return float(m[home_goals, away_goals])


def expected_goals(matrix: npt.ArrayLike) -> ExpectedGoals:
    m = _validate(matrix)
    h = np.arange(m.shape[0], dtype=np.float64)
    a = np.arange(m.shape[1], dtype=np.float64)
    return ExpectedGoals(
        home=float(h @ m.sum(axis=1)),
        away=float(a @ m.sum(axis=0)),
    )


def _is_quarter(line: Decimal) -> bool:
    if (line * 4) % 1 != 0:
        raise ValueError(f"Asian handicap line must be a multiple of 0.25, got {line}")
    return (line * 2) % 1 != 0


def asian_handicap(matrix: npt.ArrayLike, line: Decimal, side: Side) -> AhOutcome:
    """Settlement distribution for an AH bet on ``side`` at the *home* line.

    ``line`` is the handicap added to the home side (football-data convention):
    home covers when ``home_goals - away_goals + line > 0``. The away bet is
    the exact mirror at ``-line``.
    """
    m = _validate(matrix)
    margin = _margin_grid(m)
    if side == "away":
        margin, line = -margin, -line

    if not _is_quarter(line):
        settle = np.sign(margin + float(line))
        return AhOutcome(
            full_win=float(m[settle > 0].sum()),
            half_win=0.0,
            push=float(m[settle == 0].sum()),
            half_loss=0.0,
            full_loss=float(m[settle < 0].sum()),
        )

    lower = np.sign(margin + float(line - Decimal("0.25")))
    upper = np.sign(margin + float(line + Decimal("0.25")))
    # Integer margins vs quarter lines: the halves can never disagree by a
    # full win/loss, and both-push is impossible — verified by construction.
    return AhOutcome(
        full_win=float(m[(lower == 1) & (upper == 1)].sum()),
        half_win=float(m[((lower == 0) & (upper == 1)) | ((lower == 1) & (upper == 0))].sum()),
        push=float(m[(lower == 0) & (upper == 0)].sum()),
        half_loss=float(m[((lower == 0) & (upper == -1)) | ((lower == -1) & (upper == 0))].sum()),
        full_loss=float(m[(lower == -1) & (upper == -1)].sum()),
    )
