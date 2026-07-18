"""CLV meta-gate tests (Phase 2 spec).

The no-lookahead proof: a mean-predicting stub regressor's output equals the
mean label it was trained on, so a poisoned future label would show up
immediately in the predictions. Feature hygiene: the regressor must only
ever see the whitelisted bet-time columns — never closing data, never the
label.
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from pitchprob.betting.meta import (
    META_FEATURE_COLUMNS,
    MetaGateConfig,
    add_meta_features,
    walk_forward_gate,
)


def _candidates(n_days: int = 200, flip_day: int = 120) -> pd.DataFrame:
    """One candidate per day; clv_sharp is -1 before flip_day, +1 after."""
    rows = []
    for i in range(n_days):
        when = date(2023, 1, 1) + timedelta(days=i)
        closing = 0.5  # placeholder fair prob; label injected directly below
        rows.append(
            {
                "date": when,
                "league": "E0",
                "market": "1x2",
                "selection": "home",
                "probability": 0.5,
                "market_probability": 0.45,
                "price": 2.2,
                "price_sharp": 2.1,
                "closing_probability": closing,
                "gross_return": 0.0,
                "_flip": i >= flip_day,
            }
        )
    frame = pd.DataFrame(rows)
    return frame


class MeanRegressor:
    """Predicts the mean of the labels it saw — a leak detector."""

    def __init__(self) -> None:
        self.seen_columns: set[str] | None = None
        self.n_train = 0

    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> "MeanRegressor":
        self.seen_columns = set(features.columns)
        self.n_train = len(features)
        self.mean = float(np.mean(labels))
        return self

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        return np.full(len(features), self.mean)


class TestAddMetaFeatures:
    def test_hand_computed_derivations(self) -> None:
        frame = add_meta_features(_candidates(3, flip_day=99))
        row = frame.iloc[0]
        assert row["divergence"] == pytest.approx(0.05)
        assert row["abs_divergence"] == pytest.approx(0.05)
        assert row["sharp_spread"] == pytest.approx(2.2 / 2.1 - 1.0)
        assert row["month"] == 1
        # label: price_sharp * closing_fair - 1
        assert row["clv_sharp"] == pytest.approx(2.1 * 0.5 - 1.0)

    def test_whitelist_never_contains_closing_data_or_label(self) -> None:
        forbidden = {"clv_sharp", "closing_probability", "gross_return", "won"}
        assert not forbidden & set(META_FEATURE_COLUMNS)


class TestWalkForwardGate:
    def _run(self, **overrides: object):
        config = MetaGateConfig(
            refit_days=10, min_train=50, buffer=0.0,
            **overrides,  # type: ignore[arg-type]
        )
        # 400 days so the mean over the past crosses zero inside the sample:
        # -1*120 + 1.1*(s-120) > 0 from day ~230 on.
        frame = _candidates(400, flip_day=120)
        # inject the two-regime label directly (constant closing prob would
        # make every label identical otherwise)
        frame["closing_probability"] = np.where(frame["_flip"], 1.0, 0.0)
        regressors: list[MeanRegressor] = []

        def factory() -> MeanRegressor:
            model = MeanRegressor()
            regressors.append(model)
            return model

        gated = walk_forward_gate(frame, config=config, regressor_factory=factory)
        return gated, regressors

    def test_first_window_predicts_past_mean_only(self) -> None:
        _, regressors = self._run()
        # Every training set before the flip day has mean label
        # 2.1*0 - 1 = -1: with buffer 0 nothing can be gated before the flip
        # regime enters the training past.
        first = regressors[0]
        assert first.n_train >= 50
        assert first.mean == pytest.approx(-1.0)

    def test_no_gated_bet_before_labels_could_be_known(self) -> None:
        gated, _ = self._run()
        assert len(gated) > 0
        # Positive labels start at day 120; a mean over the past can only
        # cross zero after they dominate — strictly after the flip date.
        assert gated["date"].min() > date(2023, 1, 1) + timedelta(days=120)

    def test_regressor_sees_only_whitelisted_features(self) -> None:
        _, regressors = self._run()
        for model in regressors:
            assert model.seen_columns is not None
            assert model.seen_columns <= set(META_FEATURE_COLUMNS)

    def test_unlabeled_candidates_are_scored_but_not_trained_on(self) -> None:
        frame = _candidates()
        frame["closing_probability"] = np.where(frame["_flip"], 1.0, 0.0)
        # poison: unlabeled rows late in the sample
        frame.loc[frame.index[-5:], "closing_probability"] = float("nan")
        config = MetaGateConfig(refit_days=10, min_train=50, buffer=-2.0)
        gated = walk_forward_gate(
            frame, config=config, regressor_factory=MeanRegressor
        )
        # buffer -2 gates everything scoreable: the unlabeled tail must
        # still be present (scored), carrying a predicted_clv.
        tail_dates = set(frame["date"].iloc[-5:])
        assert tail_dates <= set(gated["date"])
        assert gated["predicted_clv"].notna().all()

    def test_insufficient_history_yields_no_bets(self) -> None:
        frame = _candidates(60)
        frame["closing_probability"] = 0.6
        config = MetaGateConfig(refit_days=10, min_train=1000, buffer=0.0)
        gated = walk_forward_gate(
            frame, config=config, regressor_factory=MeanRegressor
        )
        assert len(gated) == 0
