"""Implied probabilities and overround (margin) removal.

Two de-margining methods are provided (ADR 0004):

- **Multiplicative**: normalize implied probabilities to sum to 1. Simple and
  common, but ignores the favourite-longshot bias.
- **Shin (1992/1993)**: models the margin as protection against insider
  trading; the insider fraction ``z`` is solved so probabilities sum to 1.
  Longshots absorb proportionally more of the margin, which matches observed
  bookmaker behavior, so Shin probabilities are the preferred benchmark input.
"""

from collections.abc import Sequence

from scipy.optimize import brentq


def _validate_prices(prices: Sequence[float]) -> list[float]:
    ps = [float(p) for p in prices]
    if len(ps) < 2:
        raise ValueError("need at least two prices for a complete market")
    if any(p <= 1.0 for p in ps):
        raise ValueError(f"decimal prices must exceed 1.0, got {ps}")
    return ps


def implied_probabilities(prices: Sequence[float]) -> list[float]:
    """Raw implied probabilities 1/price (sum > 1 for a book with margin)."""
    return [1.0 / p for p in _validate_prices(prices)]


def overround(prices: Sequence[float]) -> float:
    """Bookmaker margin: sum of implied probabilities minus 1."""
    return sum(implied_probabilities(prices)) - 1.0


def remove_overround_multiplicative(prices: Sequence[float]) -> list[float]:
    implied = implied_probabilities(prices)
    total = sum(implied)
    return [pi / total for pi in implied]


def remove_overround_shin(prices: Sequence[float]) -> list[float]:
    """Shin-method de-margined probabilities.

    With normalized implied probabilities ``pi_i`` (book sum ``B``), Shin
    probabilities for insider fraction ``z`` are::

        p_i(z) = (sqrt(z^2 + 4 (1 - z) pi_i^2 / B) - z) / (2 (1 - z))

    ``z`` is the root of ``sum_i p_i(z) = 1``. The sum is ``sqrt(B) >= 1`` at
    ``z = 0`` and tends to ``sum pi_i^2 / B <= 1`` as ``z -> 1``, so a root
    always exists on ``[0, 1)`` for any book with a margin; a fair book yields
    ``z = 0`` and returns the implied probabilities unchanged.
    """
    implied = implied_probabilities(prices)
    book_sum = sum(implied)

    def shin_probs(z: float) -> list[float]:
        return [
            ((z**2 + 4.0 * (1.0 - z) * pi**2 / book_sum) ** 0.5 - z) / (2.0 * (1.0 - z))
            for pi in implied
        ]

    def excess(z: float) -> float:
        return sum(shin_probs(z)) - 1.0

    if abs(excess(0.0)) < 1e-12:
        return shin_probs(0.0)
    if excess(0.0) < 0.0:
        # Sub-unit book (arb / no margin): fall back to normalization.
        return remove_overround_multiplicative(prices)
    z = float(brentq(excess, 0.0, 1.0 - 1e-9, xtol=1e-12))
    probs = shin_probs(z)
    total = sum(probs)  # eliminate residual float drift
    return [p / total for p in probs]


def fair_odds(probabilities: Sequence[float]) -> list[float]:
    if any(p <= 0.0 for p in probabilities):
        raise ValueError(f"probabilities must be positive, got {list(probabilities)}")
    return [1.0 / p for p in probabilities]
