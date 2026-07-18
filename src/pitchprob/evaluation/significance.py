"""Block-bootstrap inference for backtest metrics (ADR 0010).

Matches and bets are not independent draws: one match-week shares teams,
conditions and market state, and consecutive bets share bankroll. The
exchangeable unit is therefore the ISO week, and every statistic here is
bootstrapped by resampling whole weeks with replacement — the honest version
of the confidence intervals ADR 0004 promised.

Three statistics cover the harness's needs:

- ``block_bootstrap_mean``: mean of per-unit values (Δlog-loss, mean CLV, …)
- ``block_bootstrap_ratio``: sum(numer)/sum(denom) (ROI = pnl over stake)
- ``block_bootstrap_ratio_delta``: difference of two ratios over aligned
  per-block aggregates (ΔROI between two strategies on shared weeks)

The p-value is the percentile-interval inversion for H0: statistic == 0,
floored at 1/n_boot — a bootstrap cannot certify beyond its own resolution.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
IntArray = npt.NDArray[np.int64]

_MIN_VALID_RESAMPLE_SHARE = 0.5


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    point: float
    lo: float
    hi: float
    p_value: float
    n_units: int
    n_blocks: int
    n_boot: int


def week_block_labels(dates: Iterable[date]) -> IntArray:
    """ISO (year, week) encoded as year*100 + week.

    ISO weeks, not calendar weeks: 2021-01-03 belongs to 2020-W53 (202053)
    while 2021-01-04 opens 2021-W01 (202101) — a Friday-to-Monday round is
    never split across two blocks by a year boundary.
    """
    labels = [d.isocalendar()[0] * 100 + d.isocalendar()[1] for d in dates]
    return np.asarray(labels, dtype=np.int64)


def _summarize(
    point: float,
    stats: FloatArray,
    *,
    alpha: float,
    n_units: int,
    n_blocks: int,
    n_boot: int,
) -> BootstrapSummary:
    lo, hi = (float(q) for q in np.quantile(stats, [alpha / 2.0, 1.0 - alpha / 2.0]))
    p_low = float(np.mean(stats <= 0.0))
    p_high = float(np.mean(stats >= 0.0))
    p_value = min(1.0, max(1.0 / n_boot, 2.0 * min(p_low, p_high)))
    return BootstrapSummary(
        point=point,
        lo=lo,
        hi=hi,
        p_value=p_value,
        n_units=n_units,
        n_blocks=n_blocks,
        n_boot=n_boot,
    )


def _block_index(blocks: Any, n_units: int) -> tuple[IntArray, int]:
    labels = np.asarray(blocks)
    if len(labels) != n_units:
        raise ValueError(
            f"blocks length {len(labels)} does not match values length {n_units}"
        )
    if n_units == 0:
        raise ValueError("cannot bootstrap an empty sample")
    _, inverse = np.unique(labels, return_inverse=True)
    inverse = inverse.astype(np.int64)
    n_blocks = int(inverse.max()) + 1
    if n_blocks < 2:
        raise ValueError("block bootstrap needs at least 2 blocks")
    return inverse, n_blocks


def _resample_block_sums(
    per_block: FloatArray, rng: np.random.Generator, n_boot: int
) -> FloatArray:
    """Sum each column of ``per_block`` (n_blocks by k) over n_boot block
    resamples drawn with replacement; returns (n_boot by k)."""
    n_blocks = per_block.shape[0]
    idx = rng.integers(0, n_blocks, size=(n_boot, n_blocks))
    return np.asarray(per_block[idx].sum(axis=1), dtype=np.float64)


def block_bootstrap_mean(
    values: Any,
    blocks: Any,
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> BootstrapSummary:
    """Bootstrap the mean of per-unit values, resampling whole blocks."""
    vals = np.asarray(values, dtype=np.float64)
    inverse, n_blocks = _block_index(blocks, len(vals))
    sums = np.bincount(inverse, weights=vals, minlength=n_blocks)
    counts = np.bincount(inverse, minlength=n_blocks).astype(np.float64)
    per_block = np.column_stack([sums, counts]).astype(np.float64)

    rng = np.random.default_rng(seed)
    resampled = _resample_block_sums(per_block, rng, n_boot)
    stats = resampled[:, 0] / resampled[:, 1]
    return _summarize(
        float(vals.mean()),
        stats,
        alpha=alpha,
        n_units=len(vals),
        n_blocks=n_blocks,
        n_boot=n_boot,
    )


def block_bootstrap_ratio(
    numerators: Any,
    denominators: Any,
    blocks: Any,
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> BootstrapSummary:
    """Bootstrap sum(numerators)/sum(denominators) — e.g. ROI = pnl/stake —
    resampling whole blocks. Resamples whose denominator sums to zero carry
    no information about the ratio and are dropped; if most resamples are
    degenerate the data cannot support the statistic and this raises."""
    numer = np.asarray(numerators, dtype=np.float64)
    denom = np.asarray(denominators, dtype=np.float64)
    if len(numer) != len(denom):
        raise ValueError("numerators and denominators differ in length")
    inverse, n_blocks = _block_index(blocks, len(numer))
    total_denom = float(denom.sum())
    if total_denom <= 0.0:
        raise ValueError("denominator total must be positive")
    per_block = np.column_stack(
        [
            np.bincount(inverse, weights=numer, minlength=n_blocks),
            np.bincount(inverse, weights=denom, minlength=n_blocks),
        ]
    ).astype(np.float64)

    rng = np.random.default_rng(seed)
    resampled = _resample_block_sums(per_block, rng, n_boot)
    valid = resampled[:, 1] > 0.0
    if valid.sum() < n_boot * _MIN_VALID_RESAMPLE_SHARE:
        raise ValueError("too many zero-denominator resamples to bootstrap a ratio")
    stats = resampled[valid, 0] / resampled[valid, 1]
    return _summarize(
        float(numer.sum() / total_denom),
        stats,
        alpha=alpha,
        n_units=len(numer),
        n_blocks=n_blocks,
        n_boot=n_boot,
    )


def block_bootstrap_ratio_delta(
    a_numerators: Any,
    a_denominators: Any,
    b_numerators: Any,
    b_denominators: Any,
    *,
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> BootstrapSummary:
    """Bootstrap the difference of two ratios — e.g. ΔROI between strategies
    A and B — from *per-block aggregates* aligned on the union of blocks
    (zeros where a strategy placed nothing that week). Both strategies are
    resampled on the same blocks, which is what makes the comparison paired.
    """
    a_num = np.asarray(a_numerators, dtype=np.float64)
    a_den = np.asarray(a_denominators, dtype=np.float64)
    b_num = np.asarray(b_numerators, dtype=np.float64)
    b_den = np.asarray(b_denominators, dtype=np.float64)
    n_blocks = len(a_num)
    if not (len(a_den) == len(b_num) == len(b_den) == n_blocks):
        raise ValueError("per-block aggregate arrays differ in length")
    if n_blocks < 2:
        raise ValueError("block bootstrap needs at least 2 blocks")
    if float(a_den.sum()) <= 0.0 or float(b_den.sum()) <= 0.0:
        raise ValueError("both strategies need positive total stake")
    per_block = np.column_stack([a_num, a_den, b_num, b_den]).astype(np.float64)

    rng = np.random.default_rng(seed)
    resampled = _resample_block_sums(per_block, rng, n_boot)
    valid = (resampled[:, 1] > 0.0) & (resampled[:, 3] > 0.0)
    if valid.sum() < n_boot * _MIN_VALID_RESAMPLE_SHARE:
        raise ValueError("too many zero-stake resamples to bootstrap a ratio delta")
    stats = (
        resampled[valid, 0] / resampled[valid, 1]
        - resampled[valid, 2] / resampled[valid, 3]
    )
    point = float(a_num.sum() / a_den.sum() - b_num.sum() / b_den.sum())
    return _summarize(
        point,
        stats,
        alpha=alpha,
        n_units=n_blocks,
        n_blocks=n_blocks,
        n_boot=n_boot,
    )
