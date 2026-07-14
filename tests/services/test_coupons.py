"""Coupon generator tests (ADR 0006).

Books are fabricated with controlled probabilities so every tier band and
ranking rule can be pinned exactly.
"""

import itertools

import pytest

from pitchprob.services.coupons import (
    TIERS,
    candidate_legs,
    generate_coupons,
)


def _book(
    home: str,
    away: str,
    *,
    p_home: float = 0.55,
    p_draw: float = 0.25,
    over25: float = 0.60,
    btts_yes: float = 0.55,
    eg_home: float = 1.7,
    eg_away: float = 1.1,
) -> dict:
    p_away = 1.0 - p_home - p_draw
    return {
        "league": "E0",
        "home_team": home,
        "away_team": away,
        "expected_goals": {"home": eg_home, "away": eg_away},
        "markets": {
            "1x2": {
                "ensemble": {"home": p_home, "draw": p_draw, "away": p_away},
                "dixon_coles": {"home": p_home, "draw": p_draw, "away": p_away},
                "elo": {"home": p_home, "draw": p_draw, "away": p_away},
                "gbm": {"home": p_home, "draw": p_draw, "away": p_away},
            },
            "double_chance": {
                "home_or_draw": p_home + p_draw,
                "home_or_away": p_home + p_away,
                "draw_or_away": p_draw + p_away,
            },
            "draw_no_bet": {
                "home": p_home / (p_home + p_away),
                "away": p_away / (p_home + p_away),
            },
            "totals": {
                "2.5": {"over": over25, "under": 1.0 - over25},
            },
            "btts": {"yes": btts_yes, "no": 1.0 - btts_yes},
        },
    }


class TestCandidateLegs:
    def test_legs_cover_the_market_book(self) -> None:
        legs = candidate_legs(_book("A", "B"), min_probability=0.0, max_per_match=99)
        markets = {leg.market for leg in legs}
        assert {"1x2", "double_chance", "draw_no_bet", "totals 2.5", "btts"} <= markets

    def test_min_probability_filters(self) -> None:
        legs = candidate_legs(_book("A", "B", p_home=0.9, p_draw=0.05), min_probability=0.8)
        assert legs
        assert all(leg.probability >= 0.8 for leg in legs)

    def test_reasons_cite_expected_goals_and_probability(self) -> None:
        legs = candidate_legs(_book("A", "B"), min_probability=0.5)
        leg = next(lg for lg in legs if lg.market == "totals 2.5")
        joined = " ".join(leg.reasons).lower()
        assert "expected" in joined
        assert len(leg.reasons) >= 2

    def test_model_agreement_reason_when_all_agree(self) -> None:
        legs = candidate_legs(_book("A", "B", p_home=0.7, p_draw=0.15), min_probability=0.6)
        home_leg = next(
            lg for lg in legs if lg.market == "1x2" and lg.selection == "home"
        )
        assert any("agree" in reason.lower() for reason in home_leg.reasons)


class TestGenerateCoupons:
    def _books(self) -> list[dict]:
        return [
            _book("A", "B", p_home=0.70, p_draw=0.15),
            _book("C", "D", p_home=0.65, p_draw=0.20),
            _book("E", "F", p_home=0.60, p_draw=0.20),
        ]

    @pytest.mark.parametrize("tier", list(TIERS))
    def test_joint_probability_inside_band(self, tier: str) -> None:
        low, high = TIERS[tier]
        coupons = generate_coupons(self._books(), tier=tier, max_legs=3)
        for coupon in coupons:
            assert low <= coupon.joint_probability <= high
            product = 1.0
            for leg in coupon.legs:
                product *= leg.probability
            assert coupon.joint_probability == pytest.approx(product)

    def test_legs_come_from_distinct_matches(self) -> None:
        for tier in TIERS:
            for coupon in generate_coupons(self._books(), tier=tier, max_legs=3):
                labels = [leg.match_label for leg in coupon.legs]
                assert len(labels) == len(set(labels))

    def test_max_legs_respected(self) -> None:
        coupons = generate_coupons(self._books(), tier="high_risk", max_legs=2)
        assert all(len(c.legs) <= 2 for c in coupons)

    def test_ranked_by_joint_probability_descending(self) -> None:
        coupons = generate_coupons(self._books(), tier="balanced", max_legs=3)
        probs = [c.joint_probability for c in coupons]
        assert probs == sorted(probs, reverse=True)

    def test_every_coupon_carries_the_independence_caveat(self) -> None:
        coupons = generate_coupons(self._books(), tier="safe", max_legs=3)
        assert coupons
        assert all("independen" in c.caveat.lower() for c in coupons)

    def test_impossible_band_returns_empty(self) -> None:
        # single low-probability book cannot reach the safe band
        books = [_book("A", "B", p_home=0.40, p_draw=0.30, over25=0.5, btts_yes=0.5)]
        coupons = generate_coupons(books, tier="safe", max_legs=1)
        assert coupons == []

    def test_top_n_limits_output(self) -> None:
        coupons = generate_coupons(self._books(), tier="balanced", max_legs=3, top_n=3)
        assert len(coupons) <= 3

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError):
            generate_coupons(self._books(), tier="degenerate")

    def test_combinatorics_stay_bounded(self) -> None:
        books = [
            _book(f"H{i}", f"A{i}", p_home=0.55 + 0.02 * (i % 5)) for i in range(12)
        ]
        coupons = generate_coupons(books, tier="value", max_legs=4)
        assert len(coupons) <= 5  # default top_n
        for a, b in itertools.pairwise(coupons):
            assert a.joint_probability >= b.joint_probability
