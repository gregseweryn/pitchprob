"""Market-derived feature tests (Phase 1): everything here must be computable
at bet time from the opening snapshot alone — closing data never enters."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.features.market import build_1x2_market_features, kl_divergence


def _book(prices: dict[str, tuple[float, float, float]]) -> pd.DataFrame:
    rows = []
    for i, (home, (ph, pd_, pa)) in enumerate(prices.items()):
        rows.append(
            {
                "match_id": i + 1,
                "date": date(2024, 3, 2),
                "home_team": home,
                "away_team": f"Opp{i}",
                "price_home": ph,
                "price_draw": pd_,
                "price_away": pa,
            }
        )
    return pd.DataFrame(rows)


class TestKlDivergence:
    def test_zero_for_identical_distributions(self) -> None:
        p = np.array([0.5, 0.3, 0.2])
        assert kl_divergence(p, p) == pytest.approx(0.0)

    def test_hand_computed_two_point(self) -> None:
        p = np.array([0.75, 0.25])
        q = np.array([0.5, 0.5])
        expected = 0.75 * np.log(1.5) + 0.25 * np.log(0.5)
        assert kl_divergence(p, q) == pytest.approx(expected)

    def test_nonnegative(self) -> None:
        p = np.array([0.6, 0.3, 0.1])
        q = np.array([0.2, 0.5, 0.3])
        assert kl_divergence(p, q) > 0.0


class TestBuild1x2MarketFeatures:
    def test_fair_probabilities_are_shin_of_pinnacle(self) -> None:
        pinnacle = _book({"Arsenal": (2.5, 3.3, 3.1)})
        features = build_1x2_market_features({"pinnacle": pinnacle})
        shin = remove_overround_shin([2.5, 3.3, 3.1])
        row = features.iloc[0]
        assert row["fair_home"] == pytest.approx(shin[0])
        assert row["fair_draw"] == pytest.approx(shin[1])
        assert row["fair_away"] == pytest.approx(shin[2])

    def test_overround_hand_computed(self) -> None:
        pinnacle = _book({"Arsenal": (2.0, 4.0, 5.0)})
        features = build_1x2_market_features({"pinnacle": pinnacle})
        assert features.iloc[0]["overround"] == pytest.approx(
            1 / 2.0 + 1 / 4.0 + 1 / 5.0 - 1.0
        )

    def test_book_disagreement_is_kl_to_pinnacle(self) -> None:
        pinnacle = _book({"Arsenal": (2.5, 3.3, 3.1)})
        bet365 = _book({"Arsenal": (2.3, 3.4, 3.4)})
        features = build_1x2_market_features(
            {"pinnacle": pinnacle, "bet365": bet365}
        )
        expected = kl_divergence(
            np.array(remove_overround_shin([2.3, 3.4, 3.4])),
            np.array(remove_overround_shin([2.5, 3.3, 3.1])),
        )
        assert features.iloc[0]["bet365_kl"] == pytest.approx(expected)

    def test_max_avg_spread_per_selection(self) -> None:
        pinnacle = _book({"Arsenal": (2.5, 3.3, 3.1)})
        market_max = _book({"Arsenal": (2.7, 3.5, 3.3)})
        market_avg = _book({"Arsenal": (2.5, 3.3, 3.0)})
        features = build_1x2_market_features(
            {"pinnacle": pinnacle, "market_max": market_max, "market_avg": market_avg}
        )
        row = features.iloc[0]
        assert row["maxavg_spread_home"] == pytest.approx((2.7 - 2.5) / 2.5)
        assert row["maxavg_spread_away"] == pytest.approx((3.3 - 3.0) / 3.0)

    def test_missing_books_leave_nan_not_dropped_rows(self) -> None:
        pinnacle = _book({"Arsenal": (2.5, 3.3, 3.1), "Chelsea": (1.8, 3.6, 4.6)})
        bet365 = _book({"Arsenal": (2.4, 3.3, 3.2)})  # Chelsea missing
        features = build_1x2_market_features(
            {"pinnacle": pinnacle, "bet365": bet365}
        )
        assert len(features) == 2
        chelsea = features[features["home_team"] == "Chelsea"].iloc[0]
        assert np.isnan(chelsea["bet365_kl"])

    def test_pinnacle_base_is_required(self) -> None:
        with pytest.raises(ValueError):
            build_1x2_market_features({"bet365": _book({"Arsenal": (2.0, 3.0, 4.0)})})

    def test_favourite_probability_feature(self) -> None:
        pinnacle = _book({"Arsenal": (1.5, 4.5, 7.0)})
        features = build_1x2_market_features({"pinnacle": pinnacle})
        row = features.iloc[0]
        shin = remove_overround_shin([1.5, 4.5, 7.0])
        assert row["fair_favourite"] == pytest.approx(max(shin))
