"""Per-class recalibration wrapper for the ensemble (M4.5).

Motivation (measured, not hypothesized): the M3 selector validation showed
the raw ensemble losing 8x more than Dixon-Coles under identical bet
selection — its ECE looked excellent on the *home* outcome but its draw/away
tails were biased, and betting lives in the tails.

Fit protocol (three chronological segments, no lookahead):

1. the base ensemble fits on everything before the calibration holdout
   (internally it splits again for its own stack fitting),
2. its dated predictions on the calibration holdout train one calibrator per
   class (isotonic by default; temperature as the low-capacity option),
3. the base refits on the full window; predictions route through the frozen
   calibrators and renormalize.

The calibrators are fit on out-of-sample predictions, mirroring how the
ensemble itself fits its stack — the standard two-stage pattern.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.evaluation.calibration import IsotonicCalibrator, TemperatureScaling
from pitchprob.models.base import OutcomeProbabilities, validate_matches
from pitchprob.models.ensemble import EnsembleModel

FloatArray = npt.NDArray[np.float64]

CalibrationMethod = Literal["isotonic", "temperature"]


@dataclass(frozen=True, slots=True)
class _Fitted:
    base: Any
    calibrator: IsotonicCalibrator | TemperatureScaling


class CalibratedEnsembleModel:
    def __init__(
        self,
        *,
        half_life_days: float | None = 390.0,
        stack_holdout: int = 380,
        calib_holdout: int = 190,
        min_train: int = 200,
        method: CalibrationMethod = "isotonic",
        base_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.half_life_days = half_life_days
        self.stack_holdout = stack_holdout
        self.calib_holdout = calib_holdout
        self.min_train = min_train
        self.method = method
        self.base_factory = (
            base_factory
            if base_factory is not None
            else lambda: EnsembleModel(
                half_life_days=half_life_days,
                holdout=stack_holdout,
                min_train=min_train,
            )
        )
        self._fitted: _Fitted | None = None

    # ------------------------------------------------------------------ fit

    def fit(self, matches: pd.DataFrame) -> "CalibratedEnsembleModel":
        validate_matches(matches)
        ordered = matches.copy()
        ordered["date"] = pd.to_datetime(ordered["date"]).dt.date
        ordered = ordered.sort_values("date", kind="stable").reset_index(drop=True)

        if len(ordered) < 2 * self.calib_holdout:
            raise ValueError(
                f"need at least 2x calib_holdout = {2 * self.calib_holdout} matches "
                f"so calibration never dominates training, got {len(ordered)}"
            )

        base_window = ordered.iloc[: -self.calib_holdout]
        calib_window = ordered.iloc[-self.calib_holdout :]
        stage_base = self.base_factory().fit(base_window)

        probs = np.empty((len(calib_window), 3), dtype=np.float64)
        outcomes = np.empty(len(calib_window), dtype=np.int64)
        for i, raw_row in enumerate(calib_window.itertuples(index=False)):
            row = cast(Any, raw_row)
            p: OutcomeProbabilities = stage_base.match_probabilities_at(
                str(row.home_team), str(row.away_team), row.date
            )
            probs[i] = (p.home, p.draw, p.away)
            hg, ag = int(row.ft_home), int(row.ft_away)
            outcomes[i] = 0 if hg > ag else (1 if hg == ag else 2)

        calibrator: IsotonicCalibrator | TemperatureScaling = (
            IsotonicCalibrator() if self.method == "isotonic" else TemperatureScaling()
        )
        calibrator.fit(probs, outcomes)

        self._fitted = _Fitted(
            base=self.base_factory().fit(ordered), calibrator=calibrator
        )
        return self

    # -------------------------------------------------------------- predict

    @property
    def base_(self) -> Any:
        if self._fitted is None:
            raise ModelNotFittedError("call fit() before inspecting")
        return self._fitted.base

    def _calibrate(self, p: OutcomeProbabilities) -> OutcomeProbabilities:
        if self._fitted is None:
            raise ModelNotFittedError("call fit() before predicting")
        raw = np.array([[p.home, p.draw, p.away]], dtype=np.float64)
        adjusted = self._fitted.calibrator.transform(raw)[0]
        adjusted = np.clip(adjusted, 1e-9, None)
        adjusted /= adjusted.sum()
        return OutcomeProbabilities(
            home=float(adjusted[0]), draw=float(adjusted[1]), away=float(adjusted[2])
        )

    def match_probabilities_at(
        self, home_team: str, away_team: str, as_of: date
    ) -> OutcomeProbabilities:
        return self._calibrate(
            self.base_.match_probabilities_at(home_team, away_team, as_of)
        )

    def match_probabilities(self, home_team: str, away_team: str) -> OutcomeProbabilities:
        base = self.base_
        if hasattr(base, "match_probabilities"):
            return self._calibrate(base.match_probabilities(home_team, away_team))
        return self._calibrate(
            base.match_probabilities_at(home_team, away_team, date.today())
        )
