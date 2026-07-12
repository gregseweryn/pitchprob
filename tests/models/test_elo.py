"""Elo model tests: rating dynamics and ordered-logit 1X2 mapping."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pitchprob.models.elo import EloModel


def _df(rows: list[tuple[str, str, int, int]], start: date = date(2022, 8, 1)) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": start + timedelta(days=i),
                "home_team": h,
                "away_team": a,
                "ft_home": hg,
                "ft_away": ag,
            }
            for i, (h, a, hg, ag) in enumerate(rows)
        ]
    )


class TestRatingDynamics:
    def test_winner_gains_loser_drops(self) -> None:
        model = EloModel()
        model.update_ratings(_df([("A", "B", 2, 0)]))
        assert model.rating("A") > model.initial_rating
        assert model.rating("B") < model.initial_rating

    def test_margin_of_victory_scales_update(self) -> None:
        small = EloModel()
        small.update_ratings(_df([("A", "B", 1, 0)]))
        big = EloModel()
        big.update_ratings(_df([("A", "B", 4, 0)]))
        assert big.rating("A") > small.rating("A")

    def test_upset_moves_more_than_expected_result(self) -> None:
        """After A trounces B repeatedly, another A win barely moves ratings,
        while a B upset moves them a lot."""
        history = _df([("A", "B", 3, 0)] * 10)
        expected = EloModel()
        expected.update_ratings(pd.concat([history, _df([("A", "B", 1, 0)])]))
        upset = EloModel()
        upset.update_ratings(pd.concat([history, _df([("B", "A", 1, 0)])]))
        base = EloModel()
        base.update_ratings(history)
        gain_expected = expected.rating("A") - base.rating("A")
        loss_upset = base.rating("A") - upset.rating("A")
        assert loss_upset > gain_expected

    def test_draw_moves_ratings_toward_each_other_when_unequal(self) -> None:
        history = _df([("A", "B", 3, 0)] * 10)
        model = EloModel()
        model.update_ratings(pd.concat([history, _df([("A", "B", 1, 1)])]))
        base = EloModel()
        base.update_ratings(history)
        assert model.rating("A") < base.rating("A")
        assert model.rating("B") > base.rating("B")


def synthetic_league(n_rounds: int = 40, seed: int = 11) -> pd.DataFrame:
    """League where true strength is ordered A > B > C > D, generated with
    strength-dependent win probabilities."""
    rng = np.random.default_rng(seed)
    strength = {"A": 1.0, "B": 0.5, "C": -0.3, "D": -1.2}
    rows: list[tuple[str, str, int, int]] = []
    teams = list(strength)
    for _ in range(n_rounds):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                diff = strength[h] + 0.25 - strength[a]
                p_home = 1.0 / (1.0 + np.exp(-diff))
                u = rng.uniform()
                if u < p_home * 0.75:
                    hg, ag = 2, 0
                elif u < p_home * 0.75 + 0.25:
                    hg, ag = 1, 1
                else:
                    hg, ag = 0, 2
                rows.append((h, a, hg, ag))
    return _df(rows)


@pytest.fixture(scope="module")
def fitted() -> EloModel:
    model = EloModel()
    model.fit(synthetic_league())
    return model


class TestProbabilities:
    def test_probabilities_sum_to_one(self, fitted: EloModel) -> None:
        p = fitted.match_probabilities("A", "D")
        assert p.home + p.draw + p.away == pytest.approx(1.0)
        assert all(x > 0 for x in (p.home, p.draw, p.away))

    def test_stronger_team_favoured(self, fitted: EloModel) -> None:
        p = fitted.match_probabilities("A", "D")
        assert p.home > p.away
        assert p.home > 0.4

    def test_extremes_ranked_correctly(self, fitted: EloModel) -> None:
        """Middle ranks can legitimately swap under Elo's random walk on a
        single seed, but the extremes must be right."""
        ratings = {t: fitted.rating(t) for t in ("A", "B", "C", "D")}
        assert max(ratings, key=lambda t: ratings[t]) == "A"
        assert min(ratings, key=lambda t: ratings[t]) == "D"

    def test_home_probability_monotone_in_rating_diff(self, fitted: EloModel) -> None:
        """Monotonicity is a property of the ordered-logit map itself, so pin
        the ratings instead of relying on noisy league simulation."""
        fitted.ratings.update(
            {"Strong": 1700.0, "Mid": 1500.0, "Weak": 1300.0, "Strong2": 1700.0}
        )
        p_vs_weak = fitted.match_probabilities("Strong", "Weak").home
        p_vs_mid = fitted.match_probabilities("Strong", "Mid").home
        p_vs_peer = fitted.match_probabilities("Strong", "Strong2").home
        assert p_vs_weak > p_vs_mid > p_vs_peer

    def test_draw_peaks_for_even_matches(self, fitted: EloModel) -> None:
        # draws should be more likely between equals than mismatches
        d_even = fitted.match_probabilities("B", "C").draw
        d_mismatch = fitted.match_probabilities("A", "D").draw
        assert d_even > d_mismatch

    def test_unseen_team_treated_as_average(self, fitted: EloModel) -> None:
        p = fitted.match_probabilities("Atlantis", "Utopia")
        assert p.home + p.draw + p.away == pytest.approx(1.0)

    def test_predict_before_fit_raises(self) -> None:
        from pitchprob.core.errors import ModelNotFittedError

        with pytest.raises(ModelNotFittedError):
            EloModel().match_probabilities("A", "B")
