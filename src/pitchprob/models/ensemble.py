"""Stacking ensemble: log-linear pooling with per-class biases (ADR 0005).

    log p_ens,c ∝ Σ_i w_i · log p_i,c + b_c

Weights and biases are fit by NLL on a temporal holdout carved from the end
of the training window: components are first fit on the window minus the
holdout (so stacking inputs are honestly out-of-sample), then refit on the
full window for prediction. The bias terms make this a strict generalization
of vector/temperature calibration — the stack IS the calibration layer.

Default components: Dixon-Coles (goal-space), Elo (outcome-space),
GBM (feature-space). Weights are unconstrained (logistic stacking on
log-probabilities); a negative weight would mean a component is informative
as a contrarian signal, which the holdout NLL is free to discover.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Self, cast

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import minimize

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.markets import match_odds
from pitchprob.models.base import OutcomeProbabilities, validate_matches
from pitchprob.models.dixon_coles import DEFAULT_HALF_LIFE_DAYS, DixonColesModel
from pitchprob.models.elo import EloModel
from pitchprob.models.gbm import GbmModel

FloatArray = npt.NDArray[np.float64]

_EPS = 1e-12
_DEFAULT_PREDICT_GAP_DAYS = 7


def component_probabilities(model: Any, home: str, away: str, as_of: date) -> FloatArray:
    """Uniform 1X2 access across goal-space and outcome-space models."""
    if hasattr(model, "match_probabilities_at"):
        p = model.match_probabilities_at(home, away, as_of)
    elif hasattr(model, "score_matrix"):
        mo = match_odds(model.score_matrix(home, away))
        p = OutcomeProbabilities(home=mo.home, draw=mo.draw, away=mo.away)
    else:
        p = model.match_probabilities(home, away)
    return np.array([p.home, p.draw, p.away], dtype=np.float64)


def _default_factories(half_life_days: float | None) -> dict[str, Callable[[], Any]]:
    return {
        "dixon_coles": lambda: DixonColesModel(half_life_days=half_life_days),
        "elo": EloModel,
        "gbm": GbmModel,
    }


@dataclass(frozen=True, slots=True)
class _StackParams:
    weights: FloatArray  # per component
    biases: FloatArray  # per class, biases[0] == 0 by construction


class EnsembleModel:
    def __init__(
        self,
        *,
        half_life_days: float | None = DEFAULT_HALF_LIFE_DAYS,
        holdout: int = 380,
        min_train: int = 200,
        l2: float = 0.02,
        component_factories: dict[str, Callable[[], Any]] | None = None,
    ) -> None:
        self.half_life_days = half_life_days
        self.holdout = holdout
        self.min_train = min_train
        #: Ridge strength pulling weights toward uniform and biases toward 0.
        #: Kept deliberately light: it damps holdout noise but must not tax
        #: legitimate concentration on a dominant component (a few hundred
        #: holdout matches cannot distinguish components closer than ~0.03
        #: log-loss, so some dilution is irreducible, not a bug).
        self.l2 = l2
        self.component_factories = (
            component_factories
            if component_factories is not None
            else _default_factories(half_life_days)
        )
        self._components: dict[str, Any] | None = None
        self._stack: _StackParams | None = None
        self._train_max_date: date | None = None

    # ------------------------------------------------------------------ fit

    def fit(self, matches: pd.DataFrame) -> Self:
        validate_matches(matches)
        ordered = matches.copy()
        ordered["date"] = pd.to_datetime(ordered["date"]).dt.date
        ordered = ordered.sort_values("date", kind="stable").reset_index(drop=True)

        if len(ordered) < self.holdout + self.min_train:
            raise ValueError(
                f"need at least holdout+min_train = {self.holdout + self.min_train} "
                f"matches, got {len(ordered)}"
            )
        train_a = ordered.iloc[: -self.holdout]
        holdout = ordered.iloc[-self.holdout :]

        names = list(self.component_factories)
        stage_one = {name: self.component_factories[name]().fit(train_a) for name in names}

        log_probs = np.empty((len(names), len(holdout), 3), dtype=np.float64)
        outcomes = np.empty(len(holdout), dtype=np.int64)
        for j, raw_row in enumerate(holdout.itertuples(index=False)):
            row = cast(Any, raw_row)
            for i, name in enumerate(names):
                probs = component_probabilities(
                    stage_one[name], str(row.home_team), str(row.away_team), row.date
                )
                log_probs[i, j] = np.log(np.clip(probs, _EPS, None))
            hg, ag = int(row.ft_home), int(row.ft_away)
            outcomes[j] = 0 if hg > ag else (1 if hg == ag else 2)

        self._stack = self._fit_stack(log_probs, outcomes, self.l2)
        self._components = {
            name: self.component_factories[name]().fit(ordered) for name in names
        }
        self._train_max_date = ordered["date"].max()
        return self

    @staticmethod
    def _fit_stack(log_probs: FloatArray, outcomes: np.ndarray, l2: float) -> _StackParams:
        n_components = log_probs.shape[0]
        index = np.arange(log_probs.shape[1])
        prior = np.full(n_components, 1.0 / n_components)

        def nll(params: FloatArray) -> float:
            weights = params[:n_components]
            biases = np.concatenate(([0.0], params[n_components:]))
            z = np.tensordot(weights, log_probs, axes=(0, 0)) + biases
            z -= z.max(axis=1, keepdims=True)
            p = np.exp(z)
            p /= p.sum(axis=1, keepdims=True)
            data_term = float(-np.log(np.clip(p[index, outcomes], _EPS, None)).mean())
            penalty = l2 * (
                float(((weights - prior) ** 2).sum()) + float((params[n_components:] ** 2).sum())
            )
            return data_term + penalty

        x0 = np.concatenate((prior, np.zeros(2)))
        result = minimize(nll, x0, method="L-BFGS-B")
        return _StackParams(
            weights=result.x[:n_components].astype(np.float64),
            biases=np.concatenate(([0.0], result.x[n_components:])).astype(np.float64),
        )

    # -------------------------------------------------------------- predict

    @property
    def weights_(self) -> dict[str, float]:
        if self._stack is None:
            raise ModelNotFittedError("call fit() before inspecting")
        return {
            name: float(w)
            for name, w in zip(self.component_factories, self._stack.weights, strict=True)
        }

    @property
    def components_(self) -> dict[str, Any]:
        """Fitted stage-two components (trained on the full window). Consumers
        use these for component-level views, e.g. the Dixon-Coles score matrix
        that prices goals markets."""
        if self._components is None:
            raise ModelNotFittedError("call fit() before inspecting")
        return self._components

    def match_probabilities_at(
        self, home_team: str, away_team: str, as_of: date
    ) -> OutcomeProbabilities:
        if self._components is None or self._stack is None:
            raise ModelNotFittedError("call fit() before predicting")
        log_p = np.stack(
            [
                np.log(np.clip(component_probabilities(model, home_team, away_team, as_of),
                               _EPS, None))
                for model in self._components.values()
            ]
        )
        z = self._stack.weights @ log_p + self._stack.biases
        z -= z.max()
        p = np.exp(z)
        p /= p.sum()
        return OutcomeProbabilities(home=float(p[0]), draw=float(p[1]), away=float(p[2]))

    def match_probabilities(self, home_team: str, away_team: str) -> OutcomeProbabilities:
        if self._train_max_date is None:
            raise ModelNotFittedError("call fit() before predicting")
        as_of = self._train_max_date + timedelta(days=_DEFAULT_PREDICT_GAP_DAYS)
        return self.match_probabilities_at(home_team, away_team, as_of)
