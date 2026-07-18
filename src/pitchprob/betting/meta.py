"""The CLV meta-gate (Phase 2 spec, docs/superpowers/specs/2026-07-18-*).

A second model that never predicts football: it predicts, per candidate bet,
the *sharp* closing-line value — ``clv_sharp = price_sharp * Shin(close
fair) - 1`` — from information available at the opening snapshot, and gates
betting on the prediction clearing a buffer. Trained strictly walk-forward
(labels are known at kickoff, so the training past is every candidate whose
match already kicked off), refit on a fixed cadence.

Feature hygiene is enforced by a whitelist: the regressor only ever sees
``META_FEATURE_COLUMNS`` — closing data and the label are structurally
unreachable. The signed divergence feature lets the gate learn *fade*
patterns (Phase 1 showed the market tends to move against the model's
largest divergences) as naturally as follow patterns; and if nothing clears
the buffer, the gate's output is the honest one: no bets.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import numpy as np
import pandas as pd

#: The only columns a meta regressor is ever shown. Everything is available
#: at the opening snapshot; closing data and labels are excluded by
#: construction (property-tested).
META_FEATURE_COLUMNS = [
    "probability",
    "market_probability",
    "divergence",
    "abs_divergence",
    "price",
    "sharp_spread",
    "month",
    "market",
    "selection",
    "league",
]

_CATEGORICAL = ("market", "selection", "league")


class _Regressor(Protocol):
    def fit(self, features: pd.DataFrame, labels: np.ndarray) -> Any: ...
    def predict(self, features: pd.DataFrame) -> np.ndarray: ...


@dataclass(frozen=True, slots=True)
class MetaGateConfig:
    refit_days: int = 90
    buffer: float = 0.005
    min_train: int = 1000
    n_estimators: int = 300
    learning_rate: float = 0.05
    max_depth: int = 3
    min_child_weight: float = 50.0
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_lambda: float = 5.0
    seed: int = 7


def add_meta_features(candidates: pd.DataFrame) -> pd.DataFrame:
    """Derive bet-time features and the ``clv_sharp`` label column.

    The label lives on the frame for training and reporting; it is kept out
    of the model by the ``META_FEATURE_COLUMNS`` whitelist, not by deletion —
    the harness needs it downstream for realized-CLV statistics.
    """
    out = candidates.copy()
    out["divergence"] = out["probability"] - out["market_probability"]
    out["abs_divergence"] = out["divergence"].abs()
    out["sharp_spread"] = out["price"] / out["price_sharp"] - 1.0
    out["month"] = [d.month for d in out["date"]]
    out["clv_sharp"] = out["price_sharp"] * out["closing_probability"] - 1.0
    for column in _CATEGORICAL:
        if column in out.columns:
            out[column] = out[column].astype("category")
    return out


def _default_regressor_factory(config: MetaGateConfig) -> Callable[[], _Regressor]:
    def factory() -> _Regressor:
        import xgboost as xgb

        return xgb.XGBRegressor(
            objective="reg:squarederror",
            tree_method="hist",
            enable_categorical=True,
            n_estimators=config.n_estimators,
            learning_rate=config.learning_rate,
            max_depth=config.max_depth,
            min_child_weight=config.min_child_weight,
            subsample=config.subsample,
            colsample_bytree=config.colsample_bytree,
            reg_lambda=config.reg_lambda,
            random_state=config.seed,
            n_jobs=4,
        )

    return factory


def walk_forward_gate(
    candidates: pd.DataFrame,
    *,
    config: MetaGateConfig | None = None,
    regressor_factory: Callable[[], _Regressor] | None = None,
) -> pd.DataFrame:
    """Score candidates walk-forward and return the gated subset.

    Windows advance by ``refit_days``; each window's regressor trains on
    every *labeled* candidate dated strictly before the window (a label
    exists once the match kicked off and the closing book was recorded —
    line-moved AH candidates never get one and are scored but not trained
    on). Windows whose training past is smaller than ``min_train`` produce
    no bets rather than degraded ones. Output rows carry ``predicted_clv``
    and pass the gate ``predicted_clv > buffer``.
    """
    gate_config = config or MetaGateConfig()
    factory = regressor_factory or _default_regressor_factory(gate_config)
    frame = add_meta_features(candidates)
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    feature_columns = [c for c in META_FEATURE_COLUMNS if c in frame.columns]
    labeled = frame["clv_sharp"].notna()

    dates = frame["date"]
    last_date = dates.max()
    window_start = dates.min()
    gated_parts: list[pd.DataFrame] = []
    while window_start <= last_date:
        window_end = window_start + timedelta(days=gate_config.refit_days)
        train_mask = labeled & (dates < window_start)
        test_mask = (dates >= window_start) & (dates < window_end)
        if test_mask.any() and int(train_mask.sum()) >= gate_config.min_train:
            model = factory()
            model.fit(
                frame.loc[train_mask, feature_columns],
                frame.loc[train_mask, "clv_sharp"].to_numpy(dtype=np.float64),
            )
            predictions = np.asarray(
                model.predict(frame.loc[test_mask, feature_columns]),
                dtype=np.float64,
            )
            window = frame.loc[test_mask].copy()
            window["predicted_clv"] = predictions
            gated_parts.append(window[window["predicted_clv"] > gate_config.buffer])
        window_start = window_end

    if not gated_parts:
        return frame.iloc[0:0].assign(predicted_clv=np.float64(0.0))
    return pd.concat(gated_parts, ignore_index=True)
