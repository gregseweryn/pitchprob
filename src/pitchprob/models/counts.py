"""Negative-binomial team-counts model for corners and cards (ADR 0006).

NB2 parameterization: ``Y ~ NB(mean mu, dispersion r)`` with
``Var = mu + mu^2 / r`` — corners and cards are overdispersed
(variance/mean ≈ 1.2–1.6), so a Poisson model would understate tail prices.
Structure mirrors Dixon-Coles: per-team attack/defence and home advantage on
a log link,

    mu_home = exp(base + home_adv + attack[home] + defence[away])
    mu_away = exp(base + attack[away] + defence[home])

fit by exponentially time-weighted MLE with a shared dispersion (log-scale)
and L2 shrinkage pinning the additive gauge. The gradient is analytic — the
dispersion term uses digamma — and verified against a numeric gradient in
tests. Home and away counts are treated as independent given the rates; the
counts matrix feeds the same totals machinery as goal matrices.

One class serves both statistics: instantiate with ``home_column="corners_home"``
or a derived ``cards_home`` (yellows + reds) column.
"""

from dataclasses import dataclass
from datetime import date
from typing import Self

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import minimize
from scipy.special import digamma, gammaln
from scipy.stats import nbinom as sp_nbinom

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.models.base import validate_matches
from pitchprob.models.dixon_coles import time_decay_weights

FloatArray = npt.NDArray[np.float64]


@dataclass
class NegBinCountsParams:
    base: float
    home_adv: float
    dispersion: float  # NB2 r; larger -> closer to Poisson
    attack: dict[str, float]
    defence: dict[str, float]


class NegBinCountsModel:
    def __init__(
        self,
        *,
        home_column: str,
        away_column: str,
        half_life_days: float | None = 730.0,
        l2: float = 1e-3,
        max_count: int = 25,
    ) -> None:
        self.home_column = home_column
        self.away_column = away_column
        self.half_life_days = half_life_days
        self.l2 = l2
        self.max_count = max_count
        self._params: NegBinCountsParams | None = None
        self._teams: list[str] = []
        self.n_train_: int = 0

    # ------------------------------------------------------------------ fit

    def _clean(self, matches: pd.DataFrame) -> pd.DataFrame:
        validate_matches(matches)
        for column in (self.home_column, self.away_column):
            if column not in matches.columns:
                raise ValueError(f"training frame missing column {column!r}")
        frame = matches.dropna(subset=[self.home_column, self.away_column])
        if frame.empty:
            raise ValueError(
                f"no rows with both {self.home_column!r} and {self.away_column!r}"
            )
        return frame

    def build_objective(self, matches: pd.DataFrame, *, as_of: date):
        """Return ``(objective, gradient, x0)`` over packed parameters
        ``[base, home_adv, log_r, attack_0..n-1, defence_0..n-1]``."""
        frame = self._clean(matches)
        teams = sorted(set(frame["home_team"]) | set(frame["away_team"]))
        index = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        hidx = frame["home_team"].map(index).to_numpy(dtype=np.int64)
        aidx = frame["away_team"].map(index).to_numpy(dtype=np.int64)
        k_home = frame[self.home_column].to_numpy(dtype=np.float64)
        k_away = frame[self.away_column].to_numpy(dtype=np.float64)
        w = time_decay_weights(frame["date"], as_of=as_of, half_life_days=self.half_life_days)
        l2 = self.l2

        def unpack(x: FloatArray) -> tuple[float, float, float, FloatArray, FloatArray]:
            return x[0], x[1], x[2], x[3 : 3 + n], x[3 + n :]

        def rates(
            base: float, ha: float, att: FloatArray, deff: FloatArray
        ) -> tuple[FloatArray, FloatArray]:
            mu_h = np.exp(base + ha + att[hidx] + deff[aidx])
            mu_a = np.exp(base + att[aidx] + deff[hidx])
            return mu_h, mu_a

        def _side_ll(k: FloatArray, mu: FloatArray, r: float) -> FloatArray:
            return (
                gammaln(k + r)
                - gammaln(r)
                - gammaln(k + 1.0)
                + r * np.log(r)
                + k * np.log(mu)
                - (r + k) * np.log(r + mu)
            )

        def objective(x: FloatArray) -> float:
            base, ha, log_r, att, deff = unpack(x)
            r = float(np.exp(log_r))
            mu_h, mu_a = rates(base, ha, att, deff)
            ll = float((w * (_side_ll(k_home, mu_h, r) + _side_ll(k_away, mu_a, r))).sum())
            return -ll + l2 * float(att @ att + deff @ deff)

        def gradient(x: FloatArray) -> FloatArray:
            base, ha, log_r, att, deff = unpack(x)
            r = float(np.exp(log_r))
            mu_h, mu_a = rates(base, ha, att, deff)

            # d ll / d eta = k - mu (r + k) / (r + mu)
            g_eta_h = w * (k_home - mu_h * (r + k_home) / (r + mu_h))
            g_eta_a = w * (k_away - mu_a * (r + k_away) / (r + mu_a))

            def dll_dr(k: FloatArray, mu: FloatArray) -> FloatArray:
                return (
                    digamma(k + r)
                    - digamma(r)
                    + np.log(r)
                    + 1.0
                    - np.log(r + mu)
                    - (r + k) / (r + mu)
                )

            g_log_r = float((w * (dll_dr(k_home, mu_h) + dll_dr(k_away, mu_a))).sum()) * r

            g_base = float((g_eta_h + g_eta_a).sum())
            g_ha = float(g_eta_h.sum())
            g_att = np.bincount(hidx, weights=g_eta_h, minlength=n) + np.bincount(
                aidx, weights=g_eta_a, minlength=n
            )
            g_def = np.bincount(aidx, weights=g_eta_h, minlength=n) + np.bincount(
                hidx, weights=g_eta_a, minlength=n
            )
            grad_ll = np.concatenate(([g_base, g_ha, g_log_r], g_att, g_def))
            grad_pen = np.concatenate((np.zeros(3), 2.0 * l2 * att, 2.0 * l2 * deff))
            return -grad_ll + grad_pen

        mean_count = float((k_home.sum() + k_away.sum()) / (2.0 * len(frame)))
        x0 = np.zeros(3 + 2 * n, dtype=np.float64)
        x0[0] = np.log(max(mean_count, 0.2))
        x0[1] = 0.1
        x0[2] = np.log(10.0)
        self._teams = teams
        self.n_train_ = int(len(frame))
        return objective, gradient, x0

    def fit(self, matches: pd.DataFrame, *, as_of: date | None = None) -> Self:
        frame = self._clean(matches)
        if as_of is None:
            as_of = pd.to_datetime(frame["date"]).max().date()
        objective, gradient, x0 = self.build_objective(matches, as_of=as_of)
        n = len(self._teams)
        bounds = (
            [(-2.0, 4.0), (-1.0, 1.0), (np.log(0.5), np.log(1e5))]
            + [(-2.0, 2.0)] * (2 * n)
        )
        result = minimize(
            objective, x0, jac=gradient, method="L-BFGS-B", bounds=bounds,
            options={"maxiter": 500},
        )
        x = result.x
        self._params = NegBinCountsParams(
            base=float(x[0]),
            home_adv=float(x[1]),
            dispersion=float(np.exp(x[2])),
            attack={t: float(v) for t, v in zip(self._teams, x[3 : 3 + n], strict=True)},
            defence={t: float(v) for t, v in zip(self._teams, x[3 + n :], strict=True)},
        )
        return self

    # -------------------------------------------------------------- predict

    @property
    def params(self) -> NegBinCountsParams:
        if self._params is None:
            raise ModelNotFittedError("call fit() before predicting")
        return self._params

    def expected_counts(self, home_team: str, away_team: str) -> tuple[float, float]:
        p = self.params
        mu_h = np.exp(
            p.base + p.home_adv + p.attack.get(home_team, 0.0) + p.defence.get(away_team, 0.0)
        )
        mu_a = np.exp(p.base + p.attack.get(away_team, 0.0) + p.defence.get(home_team, 0.0))
        return float(mu_h), float(mu_a)

    def counts_matrix(self, home_team: str, away_team: str) -> FloatArray:
        p = self.params
        mu_h, mu_a = self.expected_counts(home_team, away_team)
        r = p.dispersion
        k = np.arange(self.max_count + 1)
        pmf_h = sp_nbinom.pmf(k, r, r / (r + mu_h))
        pmf_a = sp_nbinom.pmf(k, r, r / (r + mu_a))
        matrix = np.outer(pmf_h, pmf_a)
        matrix /= matrix.sum()
        return matrix
