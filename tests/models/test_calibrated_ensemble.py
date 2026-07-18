"""Calibrated-ensemble tests (M4.5, motivated by the M3 finding: the raw
ensemble's draw/away tails are biased and betting suffers).

The correction guarantee is pinned with an injected fake base that
systematically overstates draws: after per-class isotonic calibration on a
chronological holdout, the output must move toward the realized rates.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pitchprob.models.base import OutcomeProbabilities
from pitchprob.models.calibrated import CalibratedEnsembleModel

TRUE_RATES = (0.45, 0.25, 0.30)  # home, draw, away
BIASED_OUTPUT = OutcomeProbabilities(home=0.30, draw=0.50, away=0.20)


class BiasedBase:
    """Constant, deliberately miscalibrated base model."""

    def __init__(self) -> None:
        self.n_fit_rows: int | None = None

    def fit(self, matches: pd.DataFrame) -> "BiasedBase":
        self.n_fit_rows = len(matches)
        return self

    def match_probabilities_at(
        self, home: str, away: str, as_of: date
    ) -> OutcomeProbabilities:
        return BIASED_OUTPUT


def synthetic_frame(n: int = 900, seed: int = 13) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        outcome = rng.choice(3, p=TRUE_RATES)
        ft_home, ft_away = [(2, 0), (1, 1), (0, 2)][outcome]
        rows.append(
            {
                "date": date(2022, 1, 1) + timedelta(days=i // 3),
                "home_team": f"T{i % 8}",
                "away_team": f"T{(i + 3) % 8}",
                "ft_home": ft_home,
                "ft_away": ft_away,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fitted() -> CalibratedEnsembleModel:
    # isotonic explicitly: the constant-bias correction below is its property
    # (temperature, the production default, cannot move classes independently)
    model = CalibratedEnsembleModel(
        base_factory=BiasedBase, calib_holdout=400, method="isotonic"
    )
    model.fit(synthetic_frame())
    return model


class TestBiasCorrection:
    def test_output_is_a_distribution(self, fitted: CalibratedEnsembleModel) -> None:
        p = fitted.match_probabilities_at("T0", "T3", date(2025, 1, 1))
        assert p.home + p.draw + p.away == pytest.approx(1.0)
        assert all(0.0 <= x <= 1.0 for x in (p.home, p.draw, p.away))

    def test_draw_overstatement_is_corrected(self, fitted: CalibratedEnsembleModel) -> None:
        p = fitted.match_probabilities_at("T0", "T3", date(2025, 1, 1))
        assert p.draw < 0.35  # biased base said 0.50; truth is 0.25
        assert p.home > 0.38  # biased base said 0.30; truth is 0.45

    def test_undated_interface_works(self, fitted: CalibratedEnsembleModel) -> None:
        p = fitted.match_probabilities("T0", "T3")
        assert p.home + p.draw + p.away == pytest.approx(1.0)

    def test_base_refit_on_full_window(self, fitted: CalibratedEnsembleModel) -> None:
        # stage one fits on len - calib_holdout, final base on everything
        assert fitted.base_.n_fit_rows == 900


class TestValidation:
    def test_too_few_matches_raises(self) -> None:
        model = CalibratedEnsembleModel(base_factory=BiasedBase, calib_holdout=400)
        with pytest.raises(ValueError):
            model.fit(synthetic_frame(n=300))

    def test_predict_before_fit_raises(self) -> None:
        from pitchprob.core.errors import ModelNotFittedError

        model = CalibratedEnsembleModel(base_factory=BiasedBase)
        with pytest.raises(ModelNotFittedError):
            model.match_probabilities("A", "B")


class TestHarnessFactory:
    def test_ensemble_cal_is_a_known_model(self) -> None:
        from pitchprob.services.harness import build_model_factory

        factory = build_model_factory("ensemble-cal", 390.0)
        assert isinstance(factory(), CalibratedEnsembleModel)
