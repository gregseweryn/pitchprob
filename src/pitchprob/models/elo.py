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
from typing import Self

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import minimize

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.models.base import OutcomeProbabilities, validate_matches

FloatArray = npt.NDArray[np.float64]

#: outcome encoding used for the ordered logit: 0 away win, 1 draw, 2 home win
_AWAY, _DRAW, _HOME = 0, 1, 2


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
            expected = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
            if hg > ag:
                score, outcome = 1.0, _HOME
            elif hg < ag:
                score, outcome = 0.0, _AWAY
            else:
                score, outcome = 0.5, _DRAW
            mov = 1.0 + math.log1p(max(int(abs(hg - ag)) - 1, 0))
            delta = self.k * mov * (score - expected)
            self.ratings[home] = self.rating(home) + delta
            self.ratings[away] = self.rating(away) - delta
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
            scale, cut_low, gap = np.exp(x[0]), x[1], np.exp(x[2])
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
        scale, cut_low, gap = float(np.exp(result.x[0])), float(result.x[1]), float(
            np.exp(result.x[2])
        )
        self._logit = _OrderedLogit(scale=scale, cut_low=cut_low, cut_high=cut_low + gap)
        return self

    # ------------------------------------------------------------ predict

    def match_probabilities(self, home_team: str, away_team: str) -> OutcomeProbabilities:
        if self._logit is None:
            raise ModelNotFittedError("call fit() before predicting")
        diff = self.rating(home_team) + self.home_advantage - self.rating(away_team)
        return self._logit.probabilities(diff)
