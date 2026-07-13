"""Gradient-boosted 1X2 model (XGBoost) over the streaming feature frame.

Outcome-space model: emits home/draw/away probabilities, no score matrix.
Missing history stays NaN (XGBoost handles missing values natively); the
league rides along as a native categorical so one model trains pooled across
leagues — cross-league sample size is where boosting earns its keep against
the per-league statistical models.

Tree count is chosen by early stopping on a temporal holdout (never a random
split — that would leak future form backwards), then the model is refit on
the full window at the chosen size so recent matches are not wasted.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Self

import pandas as pd
import xgboost as xgb

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.features.builder import FEATURE_COLUMNS, FeatureBuilder, FeatureState
from pitchprob.models.base import OutcomeProbabilities, validate_matches

#: Assumed days-until-kickoff when predicting without an explicit date.
_DEFAULT_PREDICT_GAP_DAYS = 7

_MODEL_COLUMNS = [*FEATURE_COLUMNS, "league"]


@dataclass(frozen=True, slots=True)
class _FittedState:
    booster: xgb.XGBClassifier
    features: FeatureState
    team_league: dict[str, str]
    train_max_date: date
    n_trees: int


class GbmModel:
    def __init__(
        self,
        *,
        n_estimators: int = 600,
        learning_rate: float = 0.05,
        max_depth: int = 4,
        min_child_weight: float = 5.0,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        reg_lambda: float = 1.0,
        early_stopping_rounds: int = 50,
        holdout_fraction: float = 0.1,
        seed: int = 7,
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.min_child_weight = min_child_weight
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.reg_lambda = reg_lambda
        self.early_stopping_rounds = early_stopping_rounds
        self.holdout_fraction = holdout_fraction
        self.seed = seed
        self._fitted: _FittedState | None = None
        self._builder = FeatureBuilder()

    # ------------------------------------------------------------------ fit

    def _classifier(self, n_estimators: int, early_stopping: bool) -> xgb.XGBClassifier:
        return xgb.XGBClassifier(
            objective="multi:softprob",
            n_estimators=n_estimators,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            min_child_weight=self.min_child_weight,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            reg_lambda=self.reg_lambda,
            tree_method="hist",
            enable_categorical=True,
            eval_metric="mlogloss",
            early_stopping_rounds=self.early_stopping_rounds if early_stopping else None,
            random_state=self.seed,
            n_jobs=4,
            verbosity=0,
        )

    @staticmethod
    def _model_matrix(frame: pd.DataFrame) -> pd.DataFrame:
        matrix = frame[_MODEL_COLUMNS].copy()
        matrix["league"] = matrix["league"].astype("category")
        return matrix

    def fit(self, matches: pd.DataFrame) -> Self:
        validate_matches(matches)
        X_meta, y = self._builder.build_training_frame(matches)
        X = self._model_matrix(X_meta)

        split = max(int(len(X) * (1.0 - self.holdout_fraction)), 1)
        finder = self._classifier(self.n_estimators, early_stopping=True)
        finder.fit(
            X.iloc[:split], y[:split],
            eval_set=[(X.iloc[split:], y[split:])],
            verbose=False,
        )
        best_n = int(finder.best_iteration) + 1

        booster = self._classifier(best_n, early_stopping=False)
        booster.fit(X, y, verbose=False)

        ordered = matches.sort_values("date", kind="stable")
        team_league: dict[str, str] = {}
        if "league" in ordered.columns:
            for row in ordered.to_dict("records"):
                team_league[str(row["home_team"])] = str(row["league"])
                team_league[str(row["away_team"])] = str(row["league"])

        self._fitted = _FittedState(
            booster=booster,
            features=self._builder.snapshot(matches),
            team_league=team_league,
            train_max_date=pd.to_datetime(ordered["date"]).max().date(),
            n_trees=best_n,
        )
        return self

    # -------------------------------------------------------------- predict

    @property
    def n_trees_(self) -> int:
        if self._fitted is None:
            raise ModelNotFittedError("call fit() before inspecting")
        return self._fitted.n_trees

    def match_probabilities_at(
        self, home_team: str, away_team: str, as_of: date
    ) -> OutcomeProbabilities:
        if self._fitted is None:
            raise ModelNotFittedError("call fit() before predicting")
        fitted = self._fitted
        league = fitted.team_league.get(home_team) or fitted.team_league.get(away_team) or ""
        row = self._builder.features_for(
            fitted.features,
            home_team=home_team,
            away_team=away_team,
            league=league,
            as_of=as_of,
        )
        probs = fitted.booster.predict_proba(self._model_matrix(row))[0]
        return OutcomeProbabilities(
            home=float(probs[0]), draw=float(probs[1]), away=float(probs[2])
        )

    def match_probabilities(self, home_team: str, away_team: str) -> OutcomeProbabilities:
        if self._fitted is None:
            raise ModelNotFittedError("call fit() before predicting")
        as_of = self._fitted.train_max_date + timedelta(days=_DEFAULT_PREDICT_GAP_DAYS)
        return self.match_probabilities_at(home_team, away_team, as_of)
