"""Expected value and Kelly staking for binary-outcome bets."""


def _validate(probability: float, price: float) -> None:
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"probability must be in [0, 1], got {probability}")
    if price <= 1.0:
        raise ValueError(f"decimal price must exceed 1.0, got {price}")


def expected_value(*, probability: float, price: float) -> float:
    """Expected profit per unit stake: ``p * price - 1``."""
    _validate(probability, price)
    return probability * price - 1.0


def kelly_fraction(*, probability: float, price: float, fraction: float = 1.0) -> float:
    """Kelly-optimal bankroll fraction, floored at 0 for non-positive edges.

    ``fraction`` scales the stake (fractional Kelly); production use should
    stay well below 1.0 — full Kelly assumes the probability estimate is
    exactly right, which it never is.
    """
    _validate(probability, price)
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    edge = probability * price - 1.0
    if edge <= 0.0:
        return 0.0
    return fraction * edge / (price - 1.0)
