"""Negative-binomial counts model tests (corners/cards, ADR 0006).

Guarantees pinned here:
- the analytic gradient (incl. the digamma dispersion term) matches numeric,
- known parameters are recovered from synthetic NB data,
- the counts matrix is a distribution and reuses the totals machinery,
- large dispersion converges to the Poisson limit.
"""

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from scipy.optimize import approx_fprime
from scipy.stats import poisson as sp_poisson

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.markets import totals
from pitchprob.models.counts import NegBinCountsModel

RNG = np.random.default_rng(21)

TRUE_BASE = 1.55  # log-scale intercept: exp(1.55) ~ 4.7 corners
TRUE_HOME_ADV = 0.15
TRUE_R = 12.0  # dispersion: var/mean ~ 1.4 at mean 4.7
TRUE_ATTACK = {"A": 0.25, "B": 0.10, "C": 0.0, "D": -0.10, "E": -0.15, "F": -0.30}
TRUE_DEFENCE = {"A": -0.20, "B": -0.05, "C": 0.0, "D": 0.05, "E": 0.10, "F": 0.15}


def _sample_nb(mu: float, r: float) -> int:
    return int(RNG.negative_binomial(r, r / (r + mu)))


def synthetic_matches(n_rounds: int = 50, start: date = date(2020, 8, 1)) -> pd.DataFrame:
    teams = list(TRUE_ATTACK)
    rows = []
    day = start
    for _ in range(n_rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                mu_h = np.exp(
                    TRUE_BASE + TRUE_HOME_ADV + TRUE_ATTACK[home] + TRUE_DEFENCE[away]
                )
                mu_a = np.exp(TRUE_BASE + TRUE_ATTACK[away] + TRUE_DEFENCE[home])
                rows.append(
                    {
                        "date": day,
                        "home_team": home,
                        "away_team": away,
                        "ft_home": 1,  # required by frame validation, unused
                        "ft_away": 1,
                        "corners_home": _sample_nb(float(mu_h), TRUE_R),
                        "corners_away": _sample_nb(float(mu_a), TRUE_R),
                    }
                )
        day += timedelta(days=7)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted() -> NegBinCountsModel:
    model = NegBinCountsModel(
        home_column="corners_home", away_column="corners_away", half_life_days=None
    )
    model.fit(synthetic_matches())
    return model


class TestGradient:
    def test_analytic_matches_numeric(self) -> None:
        matches = synthetic_matches(n_rounds=4)
        model = NegBinCountsModel(
            home_column="corners_home", away_column="corners_away",
            half_life_days=365.0, l2=1e-2,
        )
        objective, gradient, x0 = model.build_objective(matches, as_of=date(2021, 8, 1))
        rng = np.random.default_rng(5)
        x = x0 + rng.normal(0, 0.05, size=x0.shape)
        numeric = approx_fprime(x, objective, 1e-7)
        analytic = gradient(x)
        assert analytic == pytest.approx(numeric, rel=3e-4, abs=3e-4)


class TestRecovery:
    def test_home_advantage(self, fitted: NegBinCountsModel) -> None:
        assert fitted.params.home_adv == pytest.approx(TRUE_HOME_ADV, abs=0.05)

    def test_dispersion(self, fitted: NegBinCountsModel) -> None:
        assert fitted.params.dispersion == pytest.approx(TRUE_R, rel=0.35)

    def test_attack_ordering(self, fitted: NegBinCountsModel) -> None:
        atts = [fitted.params.attack[t] for t in ("A", "B", "C", "D", "E", "F")]
        assert atts == sorted(atts, reverse=True)

    def test_expected_counts(self, fitted: NegBinCountsModel) -> None:
        mu_h, mu_a = fitted.expected_counts("A", "F")
        true_h = np.exp(TRUE_BASE + TRUE_HOME_ADV + TRUE_ATTACK["A"] + TRUE_DEFENCE["F"])
        true_a = np.exp(TRUE_BASE + TRUE_ATTACK["F"] + TRUE_DEFENCE["A"])
        assert mu_h == pytest.approx(true_h, rel=0.10)
        assert mu_a == pytest.approx(true_a, rel=0.10)


class TestCountsMatrix:
    def test_distribution(self, fitted: NegBinCountsModel) -> None:
        m = fitted.counts_matrix("A", "B")
        assert m.shape == (26, 26)
        assert (m >= 0).all()
        assert m.sum() == pytest.approx(1.0)

    def test_totals_machinery_applies(self, fitted: NegBinCountsModel) -> None:
        m = fitted.counts_matrix("A", "B")
        result = totals(m, Decimal("9.5"))
        assert result.over + result.under == pytest.approx(1.0)
        assert result.push == 0.0

    def test_unseen_team_league_average(self, fitted: NegBinCountsModel) -> None:
        m = fitted.counts_matrix("Atlantis", "B")
        assert m.sum() == pytest.approx(1.0)

    def test_poisson_limit_at_huge_dispersion(self, fitted: NegBinCountsModel) -> None:
        model = NegBinCountsModel(
            home_column="corners_home", away_column="corners_away", max_count=25
        )
        model._params = type(fitted.params)(
            base=1.5, home_adv=0.1, dispersion=1e7,
            attack={"A": 0.0}, defence={"A": 0.0},
        )
        model._teams = ["A"]
        m = model.counts_matrix("A", "A")
        mu_h, mu_a = model.expected_counts("A", "A")
        k = np.arange(26)
        expected = np.outer(sp_poisson.pmf(k, mu_h), sp_poisson.pmf(k, mu_a))
        expected /= expected.sum()
        assert m == pytest.approx(expected, abs=1e-6)


class TestRobustness:
    def test_nan_rows_are_dropped(self) -> None:
        frame = synthetic_matches(n_rounds=4)
        frame.loc[frame.index[:20], "corners_home"] = np.nan
        model = NegBinCountsModel(
            home_column="corners_home", away_column="corners_away"
        )
        model.fit(frame)
        assert model.n_train_ == len(frame) - 20

    def test_all_nan_raises(self) -> None:
        frame = synthetic_matches(n_rounds=2)
        frame["corners_home"] = np.nan
        model = NegBinCountsModel(home_column="corners_home", away_column="corners_away")
        with pytest.raises(ValueError):
            model.fit(frame)

    def test_predict_before_fit_raises(self) -> None:
        model = NegBinCountsModel(home_column="corners_home", away_column="corners_away")
        with pytest.raises(ModelNotFittedError):
            model.counts_matrix("A", "B")
