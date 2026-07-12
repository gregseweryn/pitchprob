"""Staking simulation over value-bet candidates.

Framing per ADR 0004: this answers "how would this strategy have behaved",
including drawdowns and closing-line value — it is a risk analysis, not a
profit projection.

Input frame columns: ``date``, ``probability`` (model), ``price`` (decimal
odds actually available), ``won`` (bool settlement), optionally
``closing_probability`` (de-margined closing probability for CLV).
"""

import math
from dataclasses import dataclass
from typing import Any, Literal, cast

import pandas as pd

from pitchprob.betting.staking import kelly_fraction as kelly_stake_fraction

Strategy = Literal["flat", "kelly"]


@dataclass(frozen=True, slots=True)
class StakingResult:
    n_bets: int
    total_staked: float
    profit: float
    roi: float
    hit_rate: float
    max_drawdown: float
    final_bankroll: float
    mean_clv: float | None


def simulate_staking(
    candidates: pd.DataFrame,
    *,
    strategy: Strategy = "flat",
    ev_threshold: float = 0.02,
    kelly_fraction: float = 0.25,
    initial_bankroll: float = 100.0,
) -> StakingResult:
    ordered = candidates.sort_values("date", kind="stable")

    bankroll = initial_bankroll
    n_bets = 0
    wins = 0
    total_staked = 0.0
    profit = 0.0
    clv_values: list[float] = []
    curve_peak = 0.0 if strategy == "flat" else initial_bankroll
    max_drawdown = 0.0

    for raw_row in ordered.itertuples(index=False):
        row = cast(Any, raw_row)  # pandas named tuples are untyped
        probability = float(row.probability)
        price = float(row.price)
        edge = probability * price - 1.0
        if edge <= ev_threshold:
            continue

        if strategy == "flat":
            stake = 1.0
        else:
            fraction = kelly_stake_fraction(
                probability=probability, price=price, fraction=kelly_fraction
            )
            stake = fraction * bankroll
            if stake <= 0.0:
                continue

        n_bets += 1
        total_staked += stake
        won = bool(row.won)
        pnl = stake * (price - 1.0) if won else -stake
        profit += pnl
        bankroll += pnl
        wins += int(won)

        closing = getattr(row, "closing_probability", None)
        if closing is not None and not (isinstance(closing, float) and math.isnan(closing)):
            clv_values.append(price * float(closing) - 1.0)

        level = profit if strategy == "flat" else bankroll
        curve_peak = max(curve_peak, level)
        max_drawdown = max(max_drawdown, curve_peak - level)

    return StakingResult(
        n_bets=n_bets,
        total_staked=total_staked,
        profit=profit,
        roi=(profit / total_staked) if total_staked > 0 else 0.0,
        hit_rate=(wins / n_bets) if n_bets else 0.0,
        max_drawdown=max_drawdown,
        final_bankroll=bankroll,
        mean_clv=(sum(clv_values) / len(clv_values)) if clv_values else None,
    )
