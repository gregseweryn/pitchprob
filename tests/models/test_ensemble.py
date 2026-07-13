"""Stacking ensemble tests (ADR 0005)."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pitchprob.evaluation.metrics import log_loss
from pitchprob.models.base import OutcomeProbabilities
from pitchprob.models.dixon_coles import DixonColesModel
from pitchprob.models.ensemble import EnsembleModel

from .test_dixon_coles import TRUE_ATTACK, TRUE_DEFENCE, TRUE_HOME_ADV, TRUE_MU, synthetic_matches


@pytest.fixture(scope="module")
def league() -> pd.DataFrame:
    frame = synthetic_matches(n_rounds=50)
    frame["league"] = "X"
    return frame


class Oracle:
    """Fake component that knows the true generating process."""

    def fit(self, matches: pd.DataFrame) -> "Oracle":
        return self

    def match_probabilities_at(
        self, home: str, away: str, as_of: date
    ) -> OutcomeProbabilities:
        from scipy.stats import poisson

        lam = np.exp(TRUE_MU + TRUE_HOME_ADV + TRUE_ATTACK[home] + TRUE_DEFENCE[away])
        mu = np.exp(TRUE_MU + TRUE_ATTACK[away] + TRUE_DEFENCE[home])
        goals = np.arange(11)
        matrix = np.outer(poisson.pmf(goals, lam), poisson.pmf(goals, mu))
        matrix /= matrix.sum()
        margin = np.subtract.outer(goals, goals)
        return OutcomeProbabilities(
            home=float(matrix[margin > 0].sum()),
            draw=float(matrix[margin == 0].sum()),
            away=float(matrix[margin < 0].sum()),
        )

    def match_probabilities(self, home: str, away: str) -> OutcomeProbabilities:
        return self.match_probabilities_at(home, away, date(2099, 1, 1))


class Noise:
    """Fake component with zero information."""

    def fit(self, matches: pd.DataFrame) -> "Noise":
        return self

    def match_probabilities_at(
        self, home: str, away: str, as_of: date
    ) -> OutcomeProbabilities:
        return OutcomeProbabilities(home=0.5, draw=0.3, away=0.2)

    def match_probabilities(self, home: str, away: str) -> OutcomeProbabilities:
        return self.match_probabilities_at(home, away, date(2099, 1, 1))


class TestWeightFitting:
    def test_oracle_outweighs_noise(self, league: pd.DataFrame) -> None:
        ensemble = EnsembleModel(
            component_factories={"oracle": Oracle, "noise": Noise},
            holdout=300,
        )
        ensemble.fit(league)
        weights = ensemble.weights_
        assert weights["oracle"] > weights["noise"]
        assert weights["oracle"] > 0.5

    def test_probabilities_valid(self, league: pd.DataFrame) -> None:
        ensemble = EnsembleModel(
            component_factories={"oracle": Oracle, "noise": Noise}, holdout=300
        )
        ensemble.fit(league)
        p = ensemble.match_probabilities("A", "F")
        assert p.home + p.draw + p.away == pytest.approx(1.0)
        assert min(p.home, p.draw, p.away) > 0

    def test_too_little_data_raises(self, league: pd.DataFrame) -> None:
        ensemble = EnsembleModel(
            component_factories={"oracle": Oracle, "noise": Noise}, holdout=300
        )
        with pytest.raises(ValueError):
            ensemble.fit(league.iloc[:310])


class TestRealComponents:
    def test_bounded_dilution_with_correlated_components(
        self, league: pd.DataFrame
    ) -> None:
        """Poisson world: DC is the truth and Elo/GBM are correlated,
        slightly-worse encodings of the same signal. A finite holdout cannot
        tell such components apart (discrimination is proven separately by the
        oracle/noise test), so the honest guarantees here are: weights stay
        sane, and out-of-sample dilution versus the true model stays bounded."""
        split = int(len(league) * 0.85)
        train, test = league.iloc[:split], league.iloc[split:]

        dc = DixonColesModel(half_life_days=None).fit(train)
        ensemble = EnsembleModel(holdout=400, half_life_days=None).fit(train)

        weights = ensemble.weights_
        assert all(-0.2 <= w <= 1.2 for w in weights.values()), weights
        assert 0.8 <= sum(weights.values()) <= 2.0, weights

        def evaluate(predict) -> float:
            probs, outcomes = [], []
            for row in test.itertuples(index=False):
                p = predict(str(row.home_team), str(row.away_team), row.date)
                probs.append([p.home, p.draw, p.away])
                hg, ag = int(row.ft_home), int(row.ft_away)
                outcomes.append(0 if hg > ag else (1 if hg == ag else 2))
            return log_loss(np.array(outcomes, dtype=np.int64), np.array(probs))

        from pitchprob.markets import match_odds

        def dc_predict(h: str, a: str, d: date) -> OutcomeProbabilities:
            mo = match_odds(dc.score_matrix(h, a))
            return OutcomeProbabilities(home=mo.home, draw=mo.draw, away=mo.away)

        ll_dc = evaluate(dc_predict)
        ll_ens = evaluate(ensemble.match_probabilities_at)
        assert ll_ens <= ll_dc + 0.03

    def test_backtester_interface(self, league: pd.DataFrame) -> None:
        ensemble = EnsembleModel(holdout=200, half_life_days=None)
        assert hasattr(ensemble, "fit")
        assert hasattr(ensemble, "match_probabilities")
        assert hasattr(ensemble, "match_probabilities_at")
