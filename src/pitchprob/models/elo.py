"""Football Elo with margin-of-victory scaling and an ordered-logit 1X2 map.

Ratings update sequentially per match:

    E_home = 1 / (1 + 10^(-(R_home + home_advantage - R_away) / 400))
    delta  = K * mov * (score - E_home),   score in {1, 0.5, 0}

with ``mov = 1 + ln(1 + max(goal_diff - 1, 0))`` so one-goal wins and draws
carry weight 1 and blowouts count more, sub-linearly.

Elo alone gives an expected *score*, not draw-aware probabilities. The 1X2 map
is an ordered logit fit on the model's own pre-match rating differences: a
latent logistic variable ``z = diff / s`` with cutpoints ``t1 < t2`` yields
P(away), P(draw), P(home). This keeps Elo a fully independent, outcome-space
signal next to the goal models.
"""

import math
from dataclasses import dataclass
from typing import Self, cast

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import minimize

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.models.base import OutcomeProbabilities, validate_matches

FloatArray = npt.NDArray[np.float64]

#: outcome encoding used for the ordered logit: 0 away win, 1 draw, 2 home win
_AWAY, _DRAW, _HOME = 0, 1, 2


def elo_update(
    rating_home: float,
    rating_away: float,
    *,
    home_goals: int,
    away_goals: int,
    k: float = 20.0,
    home_advantage: float = 60.0,
) -> tuple[float, float]:
    """One sequential Elo update; shared by the model and the feature builder."""
    diff = rating_home + home_advantage - rating_away
    expected = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    if home_goals > away_goals:
        score = 1.0
    elif home_goals < away_goals:
        score = 0.0
    else:
        score = 0.5
    mov = 1.0 + math.log1p(max(abs(home_goals - away_goals) - 1, 0))
    delta = k * mov * (score - expected)
    return rating_home + delta, rating_away - delta


def _sigmoid(x: FloatArray) -> FloatArray:
    return 1.0 / (1.0 + np.exp(-x))


def _sigmoid_scalar(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass(frozen=True, slots=True)
class _OrderedLogit:
    scale: float
    cut_low: float
    cut_high: float

    def probabilities(self, diff: float) -> OutcomeProbabilities:
        z = diff / self.scale
        p_away = _sigmoid_scalar(self.cut_low - z)
        p_home_or_draw = _sigmoid_scalar(self.cut_high - z)
        p_home = 1.0 - p_home_or_draw
        p_draw = max(p_home_or_draw - p_away, 1e-12)
        total = p_home + p_draw + p_away
        return OutcomeProbabilities(
            home=p_home / total, draw=p_draw / total, away=p_away / total
        )


class EloModel:
    def __init__(
        self,
        *,
        k: float = 20.0,
        home_advantage: float = 60.0,
        initial_rating: float = 1500.0,
    ) -> None:
        self.k = k
        self.home_advantage = home_advantage
        self.initial_rating = initial_rating
        self.ratings: dict[str, float] = {}
        self._logit: _OrderedLogit | None = None

    def rating(self, team: str) -> float:
        return self.ratings.get(team, self.initial_rating)

    # ------------------------------------------------------------- training

    def _replay(self, matches: pd.DataFrame) -> list[tuple[float, int]]:
        """Update ratings chronologically; return (pre-match diff, outcome)
        observations for the ordered-logit fit."""
        validate_matches(matches)
        ordered = matches.sort_values("date", kind="stable")
        homes: list[str] = ordered["home_team"].astype(str).to_list()
        aways: list[str] = ordered["away_team"].astype(str).to_list()
        home_goals = ordered["ft_home"].to_numpy(dtype=np.int64)
        away_goals = ordered["ft_away"].to_numpy(dtype=np.int64)

        observations: list[tuple[float, int]] = []
        for home, away, hg, ag in zip(homes, aways, home_goals, away_goals, strict=True):
            diff = self.rating(home) + self.home_advantage - self.rating(away)
            if hg > ag:
                outcome = _HOME
            elif hg < ag:
                outcome = _AWAY
            else:
                outcome = _DRAW
            self.ratings[home], self.ratings[away] = elo_update(
                self.rating(home), self.rating(away),
                home_goals=int(hg), away_goals=int(ag),
                k=self.k, home_advantage=self.home_advantage,
            )
            observations.append((diff, outcome))
        return observations

    def update_ratings(self, matches: pd.DataFrame) -> None:
        """Advance ratings with new results (no probability re-fit)."""
        self._replay(matches)

    def fit(self, matches: pd.DataFrame) -> Self:
        observations = self._replay(matches)
        diffs = np.array([d for d, _ in observations], dtype=np.float64)
        outcomes = np.array([o for _, o in observations], dtype=np.int64)

        def nll(x: FloatArray) -> float:
            # clip keeps Nelder-Mead from wandering into exp() overflow on
            # degenerate (tiny/deterministic) training sets
            scale = np.exp(np.clip(x[0], -10, 12))
            cut_low, gap = x[1], np.exp(np.clip(x[2], -10, 12))
            cut_high = cut_low + gap
            z = diffs / scale
            p_away = _sigmoid(cut_low - z)
            p_hd = _sigmoid(cut_high - z)
            p = np.select(
                [outcomes == _AWAY, outcomes == _DRAW, outcomes == _HOME],
                [p_away, p_hd - p_away, 1.0 - p_hd],
            )
            return float(-np.log(np.clip(p, 1e-12, None)).sum())

        x0 = np.array([np.log(200.0), -0.5, 0.0])
        result = minimize(nll, x0, method="Nelder-Mead", options={"maxiter": 2000})
        scale, cut_low, gap = (
            float(np.exp(np.clip(result.x[0], -10, 12))),
            float(result.x[1]),
            float(np.exp(np.clip(result.x[2], -10, 12))),
        )
        self._logit = _OrderedLogit(scale=scale, cut_low=cut_low, cut_high=cut_low + gap)
        return self

    # ------------------------------------------------------------ predict

    def match_probabilities(self, home_team: str, away_team: str) -> OutcomeProbabilities:
        if self._logit is None:
            raise ModelNotFittedError("call fit() before predicting")
        diff = self.rating(home_team) + self.home_advantage - self.rating(away_team)
        return self._logit.probabilities(diff)

    # -------------------------------------------------------- persistence

    def get_params(self) -> dict[str, object]:
        """JSON-safe fitted state (persisted in ``model_runs.params``)."""
        if self._logit is None:
            raise ModelNotFittedError("call fit() before serializing")
        return {
            "model": "elo",
            "config": {
                "k": self.k,
                "home_advantage": self.home_advantage,
                "initial_rating": self.initial_rating,
            },
            "ratings": dict(self.ratings),
            "ordered_logit": {
                "scale": self._logit.scale,
                "cut_low": self._logit.cut_low,
                "cut_high": self._logit.cut_high,
            },
        }

    @classmethod
    def from_params(cls, payload: dict[str, object]) -> "EloModel":
        cfg = cast(dict[str, float], payload["config"])
        model = cls(
            k=cfg["k"],
            home_advantage=cfg["home_advantage"],
            initial_rating=cfg["initial_rating"],
        )
        model.ratings = {
            str(k): float(v) for k, v in cast(dict[str, float], payload["ratings"]).items()
        }
        logit = cast(dict[str, float], payload["ordered_logit"])
        model._logit = _OrderedLogit(
            scale=float(logit["scale"]),
            cut_low=float(logit["cut_low"]),
            cut_high=float(logit["cut_high"]),
        )
        return model
