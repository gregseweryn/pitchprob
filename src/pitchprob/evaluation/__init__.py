"""Evaluation: walk-forward backtesting, forecast metrics, staking simulation.

Everything here enforces ADR 0004: strictly out-of-sample prediction, honest
metrics, and staking framed as risk analysis rather than profit projection.
"""

from pitchprob.evaluation.backtest import run_backtest
from pitchprob.evaluation.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss,
    ranked_probability_score,
    reliability_table,
)
from pitchprob.evaluation.significance import (
    BootstrapSummary,
    block_bootstrap_mean,
    block_bootstrap_ratio,
    block_bootstrap_ratio_delta,
    week_block_labels,
)
from pitchprob.evaluation.staking import StakingResult, simulate_staking

__all__ = [
    "BootstrapSummary",
    "StakingResult",
    "block_bootstrap_mean",
    "block_bootstrap_ratio",
    "block_bootstrap_ratio_delta",
    "brier_score",
    "expected_calibration_error",
    "log_loss",
    "ranked_probability_score",
    "reliability_table",
    "run_backtest",
    "simulate_staking",
    "week_block_labels",
]
