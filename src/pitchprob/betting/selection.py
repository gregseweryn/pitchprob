"""Vig-aware bet selection (ADR 0006).

The market's de-margined probability is a *prior the model updates*, not an
opponent. Betting probabilities are the log-linear pool

    p_bet ∝ p_model^w · p_market^(1-w)

renormalized over the outcome space (for per-selection rows: the selection vs
its complement). Selection then demands (a) expected value at the offered
price computed from ``p_bet``, and (b) an offered price below a hard cap —
beyond it the edge estimate is model noise amplified by the favourite-longshot
margin structure, the exact failure quantified at -11.2% ROI in M2.
"""

import math
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from pitchprob.betting.staking import kelly_fraction

FloatArray = npt.NDArray[np.float64]

#: Model share in the log-linear pool (ADR 0006); shared by the backtest
#: harness and the prediction service's value analysis.
DEFAULT_BLEND_WEIGHT = 0.4

_OUTPUT_COLUMNS = ("p_bet", "expected_value", "kelly_fraction")


def blend_probabilities(
    model: npt.ArrayLike, market: npt.ArrayLike, *, weight: float
) -> FloatArray:
    """Log-linear pool of two distributions; ``weight`` is the model's share.

    weight=0 returns the market distribution, weight=1 the model's.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"weight must be in [0, 1], got {weight}")
    p_model = np.asarray(model, dtype=np.float64)
    p_market = np.asarray(market, dtype=np.float64)
    if p_model.shape != p_market.shape:
        raise ValueError("model and market distributions differ in shape")
    log_pool = weight * np.log(np.clip(p_model, 1e-15, None)) + (1.0 - weight) * np.log(
        np.clip(p_market, 1e-15, None)
    )
    pooled = np.exp(log_pool - log_pool.max())
    return cast(FloatArray, pooled / pooled.sum())


def blend_binary(p_model: float, p_market: float, *, weight: float) -> float:
    """Per-selection blend: the selection vs its complement (two outcomes)."""
    pooled = blend_probabilities(
        np.array([p_model, 1.0 - p_model]),
        np.array([p_market, 1.0 - p_market]),
        weight=weight,
    )
    return float(pooled[0])


def select_value_bets(
    candidates: pd.DataFrame,
    *,
    blend_weight: float = DEFAULT_BLEND_WEIGHT,
    ev_threshold: float = 0.03,
    max_price: float = 8.0,
    kelly_scale: float = 0.25,
) -> pd.DataFrame:
    """Filter a candidates frame down to qualified bets.

    Required columns: ``probability`` (model), ``price``; optional
    ``market_probability`` (de-margined; NaN/absent rows fall back to the
    model probability, i.e. no anchoring available). All input columns pass
    through; ``p_bet``, ``expected_value`` and ``kelly_fraction`` are added.
    """
    records: list[dict[str, Any]] = []
    has_market = "market_probability" in candidates.columns
    for raw_row in candidates.itertuples(index=False):
        row = cast(Any, raw_row)
        price = float(row.price)
        if price > max_price or price <= 1.0:
            continue
        p_model = float(row.probability)
        p_market = float(row.market_probability) if has_market else float("nan")
        p_bet = (
            p_model
            if math.isnan(p_market)
            else blend_binary(p_model, p_market, weight=blend_weight)
        )
        expected_value = p_bet * price - 1.0
        if expected_value <= ev_threshold:
            continue
        records.append(
            {
                **row._asdict(),
                "p_bet": p_bet,
                "expected_value": expected_value,
                "kelly_fraction": kelly_fraction(
                    probability=p_bet, price=price, fraction=kelly_scale
                ),
            }
        )

    columns = list(candidates.columns) + [
        c for c in _OUTPUT_COLUMNS if c not in candidates.columns
    ]
    return pd.DataFrame(records, columns=columns)
