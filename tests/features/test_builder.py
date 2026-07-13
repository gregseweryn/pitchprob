"""Feature builder tests.

The non-negotiable property: features for match *k* are computed strictly
from matches before *k* — verified by truncation invariance. Rolling values
are hand-checked on a four-match micro-league.
"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pitchprob.features.builder import FEATURE_COLUMNS, FeatureBuilder


def micro_league() -> pd.DataFrame:
    """Four matches; the fourth (C vs A) is the one we hand-check."""
    rows = [
        # date, home, away, ft_home, ft_away, shots_home, shots_away
        (date(2024, 1, 1), "A", "B", 2, 0, 10, 4),
        (date(2024, 1, 8), "B", "A", 1, 1, 7, 9),
        (date(2024, 1, 15), "A", "C", 0, 1, 8, 5),
        (date(2024, 1, 22), "C", "A", 2, 2, 11, 12),
    ]
    frame = pd.DataFrame(
        rows,
        columns=["date", "home_team", "away_team", "ft_home", "ft_away",
                 "shots_home", "shots_away"],
    )
    frame["league"] = "X"
    return frame


@pytest.fixture(scope="module")
def built() -> tuple[pd.DataFrame, np.ndarray]:
    return FeatureBuilder().build_training_frame(micro_league())


class TestShape:
    def test_one_row_per_match_in_order(self, built) -> None:
        X, y = built
        assert len(X) == 4
        assert len(y) == 4
        assert set(FEATURE_COLUMNS) <= set(X.columns)
        assert {"date", "league", "home_team", "away_team"} <= set(X.columns)

    def test_outcome_encoding(self, built) -> None:
        X, y = built
        # results: A win (home), draw, away win (C), draw
        assert list(y) == [0, 1, 2, 1]

    def test_first_match_has_no_history(self, built) -> None:
        X, _ = built
        first = X.iloc[0]
        assert np.isnan(first["h_ppg10"])
        assert np.isnan(first["a_ppg10"])
        assert np.isnan(first["h_rest_days"])


class TestHandComputedFourthMatch:
    """Match 4: C (home) vs A (away)."""

    @pytest.fixture()
    def row(self, built) -> pd.Series:
        X, _ = built
        return X.iloc[3]

    def test_away_side_overall_form(self, row: pd.Series) -> None:
        # A's history: W (2-0), D (1-1), L (0-1)
        assert row["a_ppg10"] == pytest.approx(4 / 3)
        assert row["a_gf10"] == pytest.approx(1.0)
        assert row["a_ga10"] == pytest.approx(2 / 3)
        assert row["a_shots_for10"] == pytest.approx((10 + 9 + 8) / 3)
        assert row["a_shots_against10"] == pytest.approx((4 + 7 + 5) / 3)

    def test_home_side_overall_form(self, row: pd.Series) -> None:
        # C's history: one away win 1-0
        assert row["h_ppg10"] == pytest.approx(3.0)
        assert row["h_gf10"] == pytest.approx(1.0)
        assert row["h_ga10"] == pytest.approx(0.0)

    def test_venue_specific_form(self, row: pd.Series) -> None:
        # C has never played at home -> NaN
        assert np.isnan(row["h_venue_ppg5"])
        # A away matches: only the 1-1 at B
        assert row["a_venue_ppg5"] == pytest.approx(1.0)
        assert row["a_venue_gf5"] == pytest.approx(1.0)
        assert row["a_venue_ga5"] == pytest.approx(1.0)

    def test_rest_days(self, row: pd.Series) -> None:
        assert row["h_rest_days"] == pytest.approx(7.0)
        assert row["a_rest_days"] == pytest.approx(7.0)

    def test_elo_features_present_and_consistent(self, row: pd.Series) -> None:
        assert np.isfinite(row["h_elo"])
        assert np.isfinite(row["a_elo"])
        assert row["elo_diff"] == pytest.approx(row["h_elo"] - row["a_elo"])

    def test_xg_features_nan_without_xg_data(self, row: pd.Series) -> None:
        assert np.isnan(row["h_xg_for10"])
        assert np.isnan(row["a_xg_against10"])


class TestXgAggregation:
    def test_nanmean_over_partial_xg(self) -> None:
        frame = micro_league()
        frame["xg_home"] = [1.5, np.nan, 0.8, np.nan]
        frame["xg_away"] = [0.5, np.nan, 1.1, np.nan]
        X, _ = FeatureBuilder().build_training_frame(frame)
        # A's xG history at match 4: 1.5 (m1 home), NaN (m2), 0.8 (m3 home)
        assert X.iloc[3]["a_xg_for10"] == pytest.approx((1.5 + 0.8) / 2)
        assert X.iloc[3]["a_xg_against10"] == pytest.approx((0.5 + 1.1) / 2)


class TestNoLeakage:
    def test_truncation_invariance(self) -> None:
        """Features of match k must be identical with or without later data."""
        rng = np.random.default_rng(5)
        teams = ["A", "B", "C", "D", "E", "F"]
        rows = []
        day = date(2023, 8, 1)
        from datetime import timedelta

        for i in range(120):
            home, away = rng.choice(teams, size=2, replace=False)
            rows.append(
                {
                    "date": day + timedelta(days=i),
                    "home_team": home,
                    "away_team": away,
                    "ft_home": int(rng.poisson(1.4)),
                    "ft_away": int(rng.poisson(1.1)),
                    "shots_home": int(rng.poisson(12)),
                    "shots_away": int(rng.poisson(10)),
                    "league": "X",
                    "xg_home": float(rng.gamma(2, 0.7)),
                    "xg_away": float(rng.gamma(2, 0.6)),
                }
            )
        frame = pd.DataFrame(rows)
        X_full, _ = FeatureBuilder().build_training_frame(frame)
        for k in (10, 47, 80, 119):
            X_trunc, _ = FeatureBuilder().build_training_frame(frame.iloc[: k + 1])
            full_row = X_full.iloc[k][FEATURE_COLUMNS].astype(float)
            trunc_row = X_trunc.iloc[k][FEATURE_COLUMNS].astype(float)
            pd.testing.assert_series_equal(full_row, trunc_row, check_names=False)


class TestSnapshotPrediction:
    def test_features_for_matches_training_row(self) -> None:
        """features_for(snapshot(first n), fixture) == training row n+1."""
        frame = micro_league()
        builder = FeatureBuilder()
        X_all, _ = builder.build_training_frame(frame)

        state = FeatureBuilder().snapshot(frame.iloc[:3])
        predicted = FeatureBuilder().features_for(
            state, home_team="C", away_team="A",
            league="X", as_of=date(2024, 1, 22),
        )
        training_row = X_all.iloc[3][FEATURE_COLUMNS].astype(float)
        pd.testing.assert_series_equal(
            predicted.iloc[0][FEATURE_COLUMNS].astype(float),
            training_row,
            check_names=False,
        )
