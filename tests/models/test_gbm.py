"""GBM (XGBoost) 1X2 model tests on synthetic data with real signal."""

import numpy as np
import pandas as pd
import pytest

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.evaluation.metrics import log_loss
from pitchprob.models.gbm import GbmModel

from .test_dixon_coles import synthetic_matches


@pytest.fixture(scope="module")
def data() -> pd.DataFrame:
    frame = synthetic_matches(n_rounds=50)
    frame["league"] = "X"
    return frame


@pytest.fixture(scope="module")
def fitted(data: pd.DataFrame) -> GbmModel:
    model = GbmModel(n_estimators=200, seed=11)
    model.fit(data.iloc[: int(len(data) * 0.8)])
    return model


class TestProbabilities:
    def test_sum_to_one_and_positive(self, fitted: GbmModel) -> None:
        p = fitted.match_probabilities("A", "F")
        assert p.home + p.draw + p.away == pytest.approx(1.0)
        assert min(p.home, p.draw, p.away) > 0.0

    def test_strong_home_side_favoured(self, fitted: GbmModel) -> None:
        # A is the strongest team, F the weakest (see TRUE_ATTACK/TRUE_DEFENCE)
        p = fitted.match_probabilities("A", "F")
        assert p.home > p.away
        assert p.home > 0.45

    def test_unseen_team_yields_valid_probabilities(self, fitted: GbmModel) -> None:
        p = fitted.match_probabilities("Atlantis", "A")
        assert p.home + p.draw + p.away == pytest.approx(1.0)

    def test_predict_before_fit_raises(self) -> None:
        with pytest.raises(ModelNotFittedError):
            GbmModel().match_probabilities("A", "B")


class TestAbsencePassthrough:
    """The M5/A1 channel: absence counts supplied at prediction time must
    reach the booster. Trained on a league where absences are the only
    signal, the prediction must respond to the supplied counts — with a dead
    channel (unconditional NaN) both calls would be identical."""

    @staticmethod
    def _absence_driven_league(n_days: int = 400, seed: int = 3) -> pd.DataFrame:
        from datetime import date, timedelta

        rng = np.random.default_rng(seed)
        teams = ["A", "B", "C", "D", "E", "F"]
        rows = []
        for i in range(n_days):
            home, away = rng.choice(teams, size=2, replace=False)
            ah, aa = int(rng.integers(0, 5)), int(rng.integers(0, 5))
            lam_home = float(np.exp(0.25 - 0.45 * ah + 0.45 * aa))
            lam_away = float(np.exp(0.05 + 0.45 * ah - 0.45 * aa))
            rows.append(
                {
                    "date": date(2023, 1, 1) + timedelta(days=i),
                    "home_team": home,
                    "away_team": away,
                    "ft_home": int(rng.poisson(lam_home)),
                    "ft_away": int(rng.poisson(lam_away)),
                    "league": "X",
                    "absences_home": float(ah),
                    "absences_away": float(aa),
                }
            )
        return pd.DataFrame(rows)

    def test_prediction_responds_to_supplied_absences(self) -> None:
        from datetime import date

        frame = self._absence_driven_league()
        model = GbmModel(n_estimators=200, seed=11)
        model.fit(frame)

        as_of = date(2024, 2, 15)
        depleted_home = model.match_probabilities_at(
            "A", "B", as_of, absences_home=4.0, absences_away=0.0
        )
        depleted_away = model.match_probabilities_at(
            "A", "B", as_of, absences_home=0.0, absences_away=4.0
        )
        assert depleted_away.home > depleted_home.home + 0.10


class TestSkill:
    def test_beats_uniform_out_of_sample(self, data: pd.DataFrame, fitted: GbmModel) -> None:
        holdout = data.iloc[int(len(data) * 0.8):]
        probs = []
        outcomes = []
        for row in holdout.itertuples(index=False):
            p = fitted.match_probabilities_at(
                str(row.home_team), str(row.away_team), as_of=row.date
            )
            probs.append([p.home, p.draw, p.away])
            hg, ag = int(row.ft_home), int(row.ft_away)
            outcomes.append(0 if hg > ag else (1 if hg == ag else 2))
        ll = log_loss(np.array(outcomes, dtype=np.int64), np.array(probs))
        assert ll < np.log(3.0) - 0.03  # clearly better than knowing nothing

    def test_early_stopping_bounds_trees(self, data: pd.DataFrame) -> None:
        model = GbmModel(n_estimators=400, seed=11)
        model.fit(data)
        assert 1 <= model.n_trees_ <= 400
