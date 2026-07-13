"""Dixon-Coles (1997) goal model with time-decay weighting.

Model
-----
Home goals ~ Poisson(lambda), away goals ~ Poisson(mu) with

    lambda = exp(mu0 + home_adv + attack[home] + defence[away])
    mu     = exp(mu0 + attack[away] + defence[home])

(``defence`` is defensive *weakness*: higher concedes more), and the
low-score dependence correction

    tau(0,0) = 1 - lambda*mu*rho    tau(0,1) = 1 + lambda*rho
    tau(1,0) = 1 + mu*rho           tau(1,1) = 1 - rho

Fitting maximizes the exponentially time-weighted log-likelihood
(weights halve every ``half_life_days``) with an L2 penalty on attack and
defence. The penalty simultaneously (a) pins the additive gauge freedom
(att + c, mu0 - c), and (b) shrinks sparsely observed (newly promoted) teams
toward league average. The gradient is analytic and verified against a
numeric gradient in tests.

``IndependentPoissonModel`` is the same fit with rho fixed at 0 — the
baseline that Dixon-Coles must beat to justify its extra parameter.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Self

import numpy as np
import numpy.typing as npt
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson as sp_poisson

from pitchprob.core.errors import ModelNotFittedError
from pitchprob.models.base import validate_matches

FloatArray = npt.NDArray[np.float64]

Objective = Callable[[FloatArray], float]
Gradient = Callable[[FloatArray], FloatArray]

_TAU_FLOOR = 1e-10


def time_decay_weights(
    dates: "pd.Series[Any]", *, as_of: date, half_life_days: float | None
) -> FloatArray:
    """Exponential decay weights: 1.0 at ``as_of``, halving every half-life."""
    if half_life_days is None:
        return np.ones(len(dates), dtype=np.float64)
    delta_days = (pd.Timestamp(as_of) - pd.to_datetime(dates)).dt.days.to_numpy(dtype=np.float64)
    return np.power(0.5, delta_days / float(half_life_days))


@dataclass
class DixonColesParams:
    mu: float
    home_adv: float
    rho: float
    attack: dict[str, float]
    defence: dict[str, float]


class DixonColesModel:
    def __init__(
        self,
        *,
        half_life_days: float | None = 365.0,
        l2: float = 1e-3,
        max_goals: int = 10,
        fixed_rho: float | None = None,
    ) -> None:
        self.half_life_days = half_life_days
        self.l2 = l2
        self.max_goals = max_goals
        self.fixed_rho = fixed_rho
        self._params: DixonColesParams | None = None
        self._teams: list[str] = []

    # ------------------------------------------------------------------ fit

    def build_objective(
        self, matches: pd.DataFrame, *, as_of: date
    ) -> tuple[Objective, Gradient, FloatArray]:
        """Return ``(objective, gradient, x0)`` over packed parameters
        ``[mu, home_adv, rho, attack_0..n-1, defence_0..n-1]``.

        Exposed publicly so tests can verify the analytic gradient.
        """
        validate_matches(matches)
        teams = sorted(set(matches["home_team"]) | set(matches["away_team"]))
        index = {t: i for i, t in enumerate(teams)}
        n = len(teams)

        hidx = matches["home_team"].map(index).to_numpy(dtype=np.int64)
        aidx = matches["away_team"].map(index).to_numpy(dtype=np.int64)
        xg = matches["ft_home"].to_numpy(dtype=np.float64)
        yg = matches["ft_away"].to_numpy(dtype=np.float64)
        w = time_decay_weights(matches["date"], as_of=as_of, half_life_days=self.half_life_days)

        m00 = (xg == 0) & (yg == 0)
        m01 = (xg == 0) & (yg == 1)
        m10 = (xg == 1) & (yg == 0)
        m11 = (xg == 1) & (yg == 1)
        l2 = self.l2

        def unpack(x: FloatArray) -> tuple[float, float, float, FloatArray, FloatArray]:
            return x[0], x[1], x[2], x[3 : 3 + n], x[3 + n :]

        def tau_of(lam: FloatArray, mu_a: FloatArray, rho: float) -> FloatArray:
            tau = np.ones_like(lam)
            tau[m00] = 1.0 - lam[m00] * mu_a[m00] * rho
            tau[m01] = 1.0 + lam[m01] * rho
            tau[m10] = 1.0 + mu_a[m10] * rho
            tau[m11] = 1.0 - rho
            return np.clip(tau, _TAU_FLOOR, None)

        def objective(x: FloatArray) -> float:
            mu0, ha, rho, att, deff = unpack(x)
            eta_h = mu0 + ha + att[hidx] + deff[aidx]
            eta_a = mu0 + att[aidx] + deff[hidx]
            lam, mu_a = np.exp(eta_h), np.exp(eta_a)
            ll = float((w * (xg * eta_h - lam + yg * eta_a - mu_a)).sum())
            ll += float((w * np.log(tau_of(lam, mu_a, rho))).sum())
            penalty = l2 * float(att @ att + deff @ deff)
            return -ll + penalty

        def gradient(x: FloatArray) -> FloatArray:
            mu0, ha, rho, att, deff = unpack(x)
            eta_h = mu0 + ha + att[hidx] + deff[aidx]
            eta_a = mu0 + att[aidx] + deff[hidx]
            lam, mu_a = np.exp(eta_h), np.exp(eta_a)
            tau = tau_of(lam, mu_a, rho)
            inv_tau = 1.0 / tau

            # d log(tau) / d lambda and / d mu (zero outside the 2x2 corner)
            dlt_dlam = np.zeros_like(lam)
            dlt_dmu = np.zeros_like(lam)
            dlt_drho = np.zeros_like(lam)
            dlt_dlam[m00] = -mu_a[m00] * rho * inv_tau[m00]
            dlt_dmu[m00] = -lam[m00] * rho * inv_tau[m00]
            dlt_drho[m00] = -lam[m00] * mu_a[m00] * inv_tau[m00]
            dlt_dlam[m01] = rho * inv_tau[m01]
            dlt_drho[m01] = lam[m01] * inv_tau[m01]
            dlt_dmu[m10] = rho * inv_tau[m10]
            dlt_drho[m10] = mu_a[m10] * inv_tau[m10]
            dlt_drho[m11] = -inv_tau[m11]

            g_eta_h = w * (xg - lam + lam * dlt_dlam)
            g_eta_a = w * (yg - mu_a + mu_a * dlt_dmu)

            g_mu0 = float((g_eta_h + g_eta_a).sum())
            g_ha = float(g_eta_h.sum())
            g_rho = float((w * dlt_drho).sum())
            g_att = np.bincount(hidx, weights=g_eta_h, minlength=n) + np.bincount(
                aidx, weights=g_eta_a, minlength=n
            )
            g_def = np.bincount(aidx, weights=g_eta_h, minlength=n) + np.bincount(
                hidx, weights=g_eta_a, minlength=n
            )
            grad_ll = np.concatenate(([g_mu0, g_ha, g_rho], g_att, g_def))
            grad_pen = np.concatenate(
                (np.zeros(3), 2.0 * l2 * att, 2.0 * l2 * deff)
            )
            return -grad_ll + grad_pen

        mean_goals = float((xg.sum() + yg.sum()) / (2.0 * len(xg)))
        x0 = np.zeros(3 + 2 * n, dtype=np.float64)
        x0[0] = np.log(max(mean_goals, 0.1))
        x0[1] = 0.25
        x0[2] = self.fixed_rho if self.fixed_rho is not None else 0.0
        self._teams = teams
        return objective, gradient, x0

    def fit(self, matches: pd.DataFrame, *, as_of: date | None = None) -> Self:
        validate_matches(matches)
        if as_of is None:
            as_of = pd.to_datetime(matches["date"]).max().date()
        objective, gradient, x0 = self.build_objective(matches, as_of=as_of)
        n = len(self._teams)

        rho_bounds = (
            (self.fixed_rho, self.fixed_rho)
            if self.fixed_rho is not None
            else (-0.35, 0.35)
        )
        bounds = [(-3.0, 3.0), (-1.0, 1.5), rho_bounds] + [(-3.0, 3.0)] * (2 * n)
        result = minimize(
            objective, x0, jac=gradient, method="L-BFGS-B", bounds=bounds,
            options={"maxiter": 500},
        )
        x = result.x
        self._params = DixonColesParams(
            mu=float(x[0]),
            home_adv=float(x[1]),
            rho=float(x[2]),
            attack={t: float(v) for t, v in zip(self._teams, x[3 : 3 + n], strict=True)},
            defence={t: float(v) for t, v in zip(self._teams, x[3 + n :], strict=True)},
        )
        return self

    # -------------------------------------------------------------- predict

    @property
    def params(self) -> DixonColesParams:
        if self._params is None:
            raise ModelNotFittedError("call fit() before predicting")
        return self._params

    def expected_goals(self, home_team: str, away_team: str) -> tuple[float, float]:
        p = self.params
        lam = np.exp(
            p.mu + p.home_adv + p.attack.get(home_team, 0.0) + p.defence.get(away_team, 0.0)
        )
        mu_a = np.exp(p.mu + p.attack.get(away_team, 0.0) + p.defence.get(home_team, 0.0))
        return float(lam), float(mu_a)

    def score_matrix(self, home_team: str, away_team: str) -> FloatArray:
        p = self.params
        lam, mu_a = self.expected_goals(home_team, away_team)
        goals = np.arange(self.max_goals + 1)
        matrix = np.outer(sp_poisson.pmf(goals, lam), sp_poisson.pmf(goals, mu_a))
        matrix[0, 0] *= 1.0 - lam * mu_a * p.rho
        matrix[0, 1] *= 1.0 + lam * p.rho
        matrix[1, 0] *= 1.0 + mu_a * p.rho
        matrix[1, 1] *= 1.0 - p.rho
        np.clip(matrix, 0.0, None, out=matrix)
        matrix /= matrix.sum()
        return matrix


    # -------------------------------------------------------- persistence

    def get_params(self) -> dict[str, Any]:
        """JSON-safe fitted state (persisted in ``model_runs.params``)."""
        p = self.params
        return {
            "model": "dixon_coles",
            "config": {
                "half_life_days": self.half_life_days,
                "l2": self.l2,
                "max_goals": self.max_goals,
                "fixed_rho": self.fixed_rho,
            },
            "mu": p.mu,
            "home_adv": p.home_adv,
            "rho": p.rho,
            "attack": dict(p.attack),
            "defence": dict(p.defence),
        }

    @classmethod
    def from_params(cls, payload: dict[str, Any]) -> "DixonColesModel":
        """Rebuild a fitted model from :meth:`get_params` output.

        Always returns a ``DixonColesModel`` (an ``IndependentPoissonModel``
        round-trips as its rho-fixed equivalent).
        """
        cfg = payload["config"]
        model = DixonColesModel(
            half_life_days=cfg["half_life_days"],
            l2=cfg["l2"],
            max_goals=cfg["max_goals"],
            fixed_rho=cfg.get("fixed_rho"),
        )
        model._params = DixonColesParams(
            mu=float(payload["mu"]),
            home_adv=float(payload["home_adv"]),
            rho=float(payload["rho"]),
            attack={str(k): float(v) for k, v in payload["attack"].items()},
            defence={str(k): float(v) for k, v in payload["defence"].items()},
        )
        model._teams = sorted(model._params.attack)
        return model


class IndependentPoissonModel(DixonColesModel):
    """Dixon-Coles with the dependence correction disabled (rho = 0)."""

    def __init__(
        self,
        *,
        half_life_days: float | None = 365.0,
        l2: float = 1e-3,
        max_goals: int = 10,
    ) -> None:
        super().__init__(
            half_life_days=half_life_days, l2=l2, max_goals=max_goals, fixed_rho=0.0
        )
