"""Dixon-Coles and independent-Poisson model tests.

The critical guarantees:
- the analytic gradient matches a numeric gradient (the fit is only as good
  as its gradient),
- known parameters are recovered from synthetic data,
- with rho = 0 the score matrix factorizes exactly (independent Poisson),
- time-decay weights halve at the configured half-life.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import approx_fprime
from scipy.stats import poisson as sp_poisson

from pitchprob.models.dixon_coles import (
    DixonColesModel,
    IndependentPoissonModel,
    time_decay_weights,
)

RNG = np.random.default_rng(7)

TRUE_HOME_ADV = 0.30
TRUE_MU = 0.10
TRUE_ATTACK = {"A": 0.35, "B": 0.15, "C": 0.0, "D": -0.10, "E": -0.15, "F": -0.25}
TRUE_DEFENCE = {"A": -0.30, "B": -0.10, "C": 0.0, "D": 0.05, "E": 0.15, "F": 0.20}


def synthetic_matches(n_rounds: int = 60, start: date = date(2020, 8, 1)) -> pd.DataFrame:
    """Double round-robin repeated ``n_rounds`` times with Poisson goals."""
    teams = list(TRUE_ATTACK)
    rows = []
    day = start
    for _ in range(n_rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                lam = np.exp(
                    TRUE_MU + TRUE_HOME_ADV + TRUE_ATTACK[home] + TRUE_DEFENCE[away]
                )
                mu = np.exp(TRUE_MU + TRUE_ATTACK[away] + TRUE_DEFENCE[home])
                rows.append(
                    {
                        "date": day,
                        "home_team": home,
                        "away_team": away,
                        "ft_home": int(RNG.poisson(lam)),
                        "ft_away": int(RNG.poisson(mu)),
                    }
                )
        day += timedelta(days=7)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted() -> DixonColesModel:
    model = DixonColesModel(half_life_days=None, l2=1e-3)
    model.fit(synthetic_matches())
    return model


class TestTimeDecayWeights:
    def test_halves_at_half_life(self) -> None:
        dates = pd.Series([date(2024, 1, 1), date(2024, 12, 31)])
        w = time_decay_weights(dates, as_of=date(2024, 12, 31), half_life_days=365.0)
        assert w[1] == pytest.approx(1.0)
        assert w[0] == pytest.approx(0.5, rel=1e-2)

    def test_none_means_uniform(self) -> None:
        dates = pd.Series([date(2015, 1, 1), date(2024, 1, 1)])
        w = time_decay_weights(dates, as_of=date(2024, 1, 1), half_life_days=None)
        assert list(w) == [1.0, 1.0]


class TestGradient:
    def test_analytic_matches_numeric(self) -> None:
        matches = synthetic_matches(n_rounds=4)
        model = DixonColesModel(half_life_days=365.0, l2=1e-2)
        objective, gradient, x0 = model.build_objective(matches, as_of=date(2021, 8, 1))
        rng = np.random.default_rng(3)
        x = x0 + rng.normal(0, 0.05, size=x0.shape)
        numeric = approx_fprime(x, objective, 1e-7)
        analytic = gradient(x)
        assert analytic == pytest.approx(numeric, rel=2e-4, abs=2e-4)


class TestParameterRecovery:
    def test_home_advantage(self, fitted: DixonColesModel) -> None:
        assert fitted.params.home_adv == pytest.approx(TRUE_HOME_ADV, abs=0.06)

    def test_attack_ordering_preserved(self, fitted: DixonColesModel) -> None:
        atts = [fitted.params.attack[t] for t in ("A", "B", "C", "D", "E", "F")]
        assert atts == sorted(atts, reverse=True)

    def test_expected_goals_close_to_truth(self, fitted: DixonColesModel) -> None:
        lam, mu = fitted.expected_goals("A", "F")
        true_lam = np.exp(TRUE_MU + TRUE_HOME_ADV + TRUE_ATTACK["A"] + TRUE_DEFENCE["F"])
        true_mu = np.exp(TRUE_MU + TRUE_ATTACK["F"] + TRUE_DEFENCE["A"])
        assert lam == pytest.approx(true_lam, rel=0.10)
        assert mu == pytest.approx(true_mu, rel=0.10)


class TestScoreMatrix:
    def test_valid_probability_matrix(self, fitted: DixonColesModel) -> None:
        m = fitted.score_matrix("A", "B")
        assert m.shape == (11, 11)
        assert (m >= 0).all()
        assert m.sum() == pytest.approx(1.0)

    def test_unseen_team_gets_league_average(self, fitted: DixonColesModel) -> None:
        m = fitted.score_matrix("Atlantis", "B")
        assert m.sum() == pytest.approx(1.0)

    def test_rho_zero_factorizes(self) -> None:
        model = IndependentPoissonModel(half_life_days=None)
        model.fit(synthetic_matches(n_rounds=6))
        assert model.params.rho == 0.0
        m = model.score_matrix("A", "B")
        lam, mu = model.expected_goals("A", "B")
        h = sp_poisson.pmf(np.arange(11), lam)
        a = sp_poisson.pmf(np.arange(11), mu)
        expected = np.outer(h, a)
        expected /= expected.sum()
        assert m == pytest.approx(expected, abs=1e-12)

    def test_negative_rho_inflates_low_draws(self) -> None:
        """Dixon-Coles' empirical finding: rho < 0 raises P(0-0) and P(1-1)
        relative to independence."""
        dc = DixonColesModel(half_life_days=None)
        matches = synthetic_matches(n_rounds=6)
        dc.fit(matches)
        base = IndependentPoissonModel(half_life_days=None)
        base.fit(matches)
        # force a clearly negative rho to isolate the tau effect
        dc.params.rho = -0.10
        m_dc = dc.score_matrix("A", "B")
        m_p = base.score_matrix("A", "B")
        assert m_dc[0, 0] > m_p[0, 0]
        assert m_dc[1, 1] > m_p[1, 1]
        assert m_dc[1, 0] < m_p[1, 0]


class TestValidation:
    def test_predict_before_fit_raises(self) -> None:
        from pitchprob.core.errors import ModelNotFittedError

        with pytest.raises(ModelNotFittedError):
            DixonColesModel().score_matrix("A", "B")

    def test_missing_columns_raise(self) -> None:
        with pytest.raises(ValueError):
            DixonColesModel().fit(pd.DataFrame({"home_team": ["A"]}))
