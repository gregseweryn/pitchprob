"""Automated error analysis over a bet log (Phase 1 of the ADR 0010 program).

Where does the strategy lose? Bets are decomposed into segments (market,
price band, league, season — any grouping the caller asks for), each segment
gets block-bootstrap p-values for its ROI and mean CLV, and the family of
p-values inside one grouping is corrected with Benjamini-Hochberg so that a
20-segment fishing trip cannot manufacture a discovery. Segments below the
size floor are dropped rather than reported with meaningless intervals.
"""

from datetime import date
from itertools import pairwise
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from pitchprob.evaluation.significance import (
    block_bootstrap_mean,
    block_bootstrap_ratio,
    week_block_labels,
)

BoolArray = npt.NDArray[np.bool_]

_PRICE_BAND_EDGES = [1.0, 1.5, 2.0, 3.0, 5.0, 8.0, float("inf")]


def benjamini_hochberg(p_values: npt.NDArray[np.float64], *, q: float = 0.05) -> BoolArray:
    """BH step-up: reject the k smallest p-values where k is the largest
    index with ``p_(k) <= k/m * q``. Returns flags in the input order."""
    p = np.asarray(p_values, dtype=np.float64)
    m = len(p)
    rejected = np.zeros(m, dtype=np.bool_)
    if m == 0:
        return rejected
    order = np.argsort(p, kind="stable")
    thresholds = (np.arange(1, m + 1) / m) * q
    passing = p[order] <= thresholds
    if passing.any():
        k = int(np.max(np.nonzero(passing)[0]))
        rejected[order[: k + 1]] = True
    return rejected


def _season_label(when: date) -> str:
    start_year = when.year if when.month >= 7 else when.year - 1
    return f"{start_year}/{(start_year + 1) % 100:02d}"


def derive_segments(bet_log: pd.DataFrame) -> pd.DataFrame:
    """Add derived segmentation columns: ``season`` (July-June) and
    ``price_band`` (fixed edges — favourite-longshot axis)."""
    out = bet_log.copy()
    out["season"] = [_season_label(d) for d in out["date"]]
    labels = [
        f"{lo:.2f}-{hi:.2f}" if np.isfinite(hi) else f"{lo:.2f}+"
        for lo, hi in pairwise(_PRICE_BAND_EDGES)
    ]
    out["price_band"] = pd.cut(
        out["price"], bins=_PRICE_BAND_EDGES, labels=labels, include_lowest=True
    ).astype(str)
    return out


def analyze_segments(
    bet_log: pd.DataFrame,
    *,
    group_by: list[str],
    n_boot: int = 2000,
    min_n: int = 30,
    q: float = 0.05,
    seed: int = 0,
) -> pd.DataFrame:
    """Per-segment economics with multiplicity-corrected significance.

    Returns one row per segment (with ``n >= min_n``): n, total stake, ROI,
    mean CLV, block-bootstrap p-values for both, and BH-corrected
    significance flags computed across the segments of this grouping.
    """
    if bet_log is None or len(bet_log) == 0:
        return pd.DataFrame()
    frame = derive_segments(bet_log)
    rows: list[dict[str, Any]] = []
    for key, sub in frame.groupby(group_by, dropna=False):
        if len(sub) < min_n:
            continue
        label = key[0] if isinstance(key, tuple) and len(key) == 1 else key
        blocks = week_block_labels(list(sub["date"]))
        record: dict[str, Any] = {
            "grouping": "+".join(group_by),
            "segment": str(label),
            "n": len(sub),
            "stake": float(sub["stake"].sum()),
            "roi": float(sub["pnl"].sum() / sub["stake"].sum()),
        }
        try:
            roi_summary = block_bootstrap_ratio(
                sub["pnl"].to_numpy(dtype=np.float64),
                sub["stake"].to_numpy(dtype=np.float64),
                blocks, n_boot=n_boot, seed=seed,
            )
            record["roi_p"] = roi_summary.p_value
        except ValueError:
            record["roi_p"] = float("nan")
        with_clv = sub[sub["clv"].notna()]
        record["mean_clv"] = (
            float(with_clv["clv"].mean()) if len(with_clv) else float("nan")
        )
        try:
            clv_summary = block_bootstrap_mean(
                with_clv["clv"].to_numpy(dtype=np.float64),
                week_block_labels(list(with_clv["date"])),
                n_boot=n_boot, seed=seed,
            )
            record["clv_p"] = clv_summary.p_value
        except ValueError:
            record["clv_p"] = float("nan")
        rows.append(record)
    table = pd.DataFrame(rows)
    if table.empty:
        return table
    for column, flag in (("roi_p", "roi_significant"), ("clv_p", "clv_significant")):
        p = table[column].to_numpy(dtype=np.float64)
        finite = np.isfinite(p)
        flags = np.zeros(len(p), dtype=np.bool_)
        flags[finite] = benjamini_hochberg(p[finite], q=q)
        table[flag] = flags
    return table
