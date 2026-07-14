"""Coupon generation: risk-tiered accumulators with per-leg reasoning.

Tiers are probability *bands* (ADR 0006), not promises: a coupon's joint
probability is the product over its legs, legs are restricted to distinct
matches so independence is a reasonable approximation, and every coupon
carries that caveat verbatim.

Legs are drawn from the full market book of each fixture; each leg explains
itself with machine-generated reasons (model probability, expected goals,
cross-model agreement). When offered prices are attached to every leg the
coupon also reports its combined price and expected value.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations, product
from math import prod
from typing import Any

TIERS: dict[str, tuple[float, float]] = {
    "safe": (0.80, 0.95),
    "balanced": (0.60, 0.80),
    "value": (0.40, 0.60),
    "high_risk": (0.20, 0.40),
}

INDEPENDENCE_CAVEAT = (
    "Joint probability assumes independence between matches; single-leg model "
    "error compounds multiplicatively, so tier labels are estimates with "
    "compounding uncertainty, not guarantees."
)

#: Legs steeper than this are excluded: the payout is negligible and the
#: model cannot distinguish 0.97 from 0.99.
_MAX_LEG_PROBABILITY = 0.97


@dataclass(frozen=True, slots=True)
class Leg:
    match_label: str
    market: str
    selection: str
    probability: float
    reasons: tuple[str, ...]
    price: float | None = None


@dataclass(frozen=True, slots=True)
class Coupon:
    tier: str
    legs: tuple[Leg, ...]
    joint_probability: float
    caveat: str
    combined_price: float | None = None
    expected_value: float | None = None


def _agreement_reason(book: dict[str, Any], selection: str) -> str | None:
    one_x_two = book["markets"]["1x2"]
    winners = {
        model: max(probs, key=lambda k: probs[k]) for model, probs in one_x_two.items()
    }
    if all(winner == selection for winner in winners.values()):
        return f"all {len(winners)} models agree {selection!r} is the most likely outcome"
    return None


def _reasons(
    book: dict[str, Any], market: str, selection: str, probability: float
) -> tuple[str, ...]:
    eg = book["expected_goals"]
    eg_text = f"expected goals {eg['home']:.1f}-{eg['away']:.1f}"
    total_eg = eg["home"] + eg["away"]
    reasons = [f"model probability {probability:.0%}"]

    if market == "1x2":
        reasons.append(eg_text)
        agreement = _agreement_reason(book, selection)
        if agreement:
            reasons.append(agreement)
    elif market in ("double_chance", "draw_no_bet"):
        reasons.append(eg_text)
        reasons.append("covers two outcomes" if market == "double_chance"
                       else "stake refunded on a draw")
    elif market.startswith("totals"):
        reasons.append(f"expected total {total_eg:.1f} goals")
    elif market == "btts":
        reasons.append(f"expected goals {eg['home']:.1f} and {eg['away']:.1f} per side")
    return tuple(reasons)


def candidate_legs(
    book: dict[str, Any],
    *,
    min_probability: float = 0.5,
    max_per_match: int = 4,
) -> list[Leg]:
    """The most confident selections of one fixture's market book."""
    label = f"{book['home_team']} vs {book['away_team']}"
    markets = book["markets"]

    raw: list[tuple[str, str, float]] = []
    raw += [("1x2", sel, p) for sel, p in markets["1x2"]["ensemble"].items()]
    raw += [("double_chance", sel, p) for sel, p in markets["double_chance"].items()]
    raw += [("draw_no_bet", sel, p) for sel, p in markets["draw_no_bet"].items()]
    for line, over_under in markets["totals"].items():
        raw += [(f"totals {line}", sel, p) for sel, p in over_under.items()]
    raw += [("btts", sel, p) for sel, p in markets["btts"].items()]

    legs = [
        Leg(
            match_label=label,
            market=market,
            selection=selection,
            probability=float(probability),
            reasons=_reasons(book, market, selection, float(probability)),
        )
        for market, selection, probability in raw
        if min_probability <= probability <= _MAX_LEG_PROBABILITY
    ]
    legs.sort(key=lambda leg: leg.probability, reverse=True)
    return legs[:max_per_match]


def _coupon_from(tier: str, legs: tuple[Leg, ...], joint: float) -> Coupon:
    combined_price: float | None = None
    expected_value: float | None = None
    if all(leg.price is not None for leg in legs):
        combined_price = prod(leg.price for leg in legs if leg.price is not None)
        expected_value = joint * combined_price - 1.0
    return Coupon(
        tier=tier,
        legs=legs,
        joint_probability=joint,
        caveat=INDEPENDENCE_CAVEAT,
        combined_price=combined_price,
        expected_value=expected_value,
    )


def generate_coupons(
    books: Iterable[dict[str, Any]],
    *,
    tier: str,
    max_legs: int = 4,
    top_n: int = 5,
    min_leg_probability: float = 0.5,
) -> list[Coupon]:
    """Enumerate accumulators over distinct matches whose joint probability
    falls inside the tier band; ranked by expected value when every leg is
    priced, else by joint probability."""
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(TIERS)}")
    low, high = TIERS[tier]

    per_match: list[Sequence[Leg]] = [
        legs
        for legs in (
            candidate_legs(book, min_probability=min_leg_probability) for book in books
        )
        if legs
    ]

    coupons: list[Coupon] = []
    for n_legs in range(1, max_legs + 1):
        for match_group in combinations(per_match, n_legs):
            for leg_combo in product(*match_group):
                joint = prod(leg.probability for leg in leg_combo)
                if low <= joint <= high:
                    coupons.append(_coupon_from(tier, tuple(leg_combo), joint))

    fully_priced = bool(coupons) and all(c.expected_value is not None for c in coupons)
    if fully_priced:
        coupons.sort(key=lambda c: (c.expected_value or 0.0), reverse=True)
    else:
        coupons.sort(key=lambda c: c.joint_probability, reverse=True)
    return coupons[:top_n]
