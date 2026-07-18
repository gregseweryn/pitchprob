"""Open→close movement diagnostics (Phase 1 of the ADR 0010 program).

The market's move between the opening and closing snapshots is the closest
thing to ground truth about information flow that a historical dataset
offers. These are the geometric primitives:

- ``movement_vectors``: close fair minus open fair, per match (sums to zero
  across selections by construction).
- ``directional_agreement``: dot product of the movement with a reference
  direction — the model's divergence from the open, or the realized outcome
  minus the open. Positive means the market moved *toward* the reference.
- ``bucketed_agreement``: P(moved toward reference) and mean dot product by
  reference-magnitude bucket — the study's headline table: if the market
  disproportionately moves toward the model where the model disagrees most,
  the model carries information the opening price lacks.

Everything is a pure function over arrays; loading and pairing frames is the
caller's business (the study CLI), which keeps every quantity testable with
hand-computed values.
"""

import numpy as np
import numpy.typing as npt
import pandas as pd

FloatArray = npt.NDArray[np.float64]


def movement_vectors(open_fair: FloatArray, close_fair: FloatArray) -> FloatArray:
    """Per-match probability movement: ``close_fair - open_fair``."""
    open_arr = np.asarray(open_fair, dtype=np.float64)
    close_arr = np.asarray(close_fair, dtype=np.float64)
    if open_arr.shape != close_arr.shape:
        raise ValueError(
            f"open and close shapes differ: {open_arr.shape} vs {close_arr.shape}"
        )
    return close_arr - open_arr


def directional_agreement(
    movement: FloatArray, reference: FloatArray
) -> FloatArray:
    """Row-wise dot product of movement with a reference direction.

    Positive: the market moved toward the reference. The raw (un-normalized)
    dot is kept deliberately — it weights matches by how far the line
    actually moved, which is the economically relevant magnitude.
    """
    move = np.asarray(movement, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if move.shape != ref.shape:
        raise ValueError(f"shapes differ: {move.shape} vs {ref.shape}")
    dots: FloatArray = np.einsum("ij,ij->i", move, ref)
    return dots


def bucketed_agreement(
    dots: FloatArray, magnitudes: FloatArray, *, n_buckets: int = 5
) -> pd.DataFrame:
    """P(moved toward reference) and mean dot by magnitude bucket.

    ``magnitudes`` is the bucketing axis (typically the L1 size of the
    model-vs-open divergence); buckets are equal-count quantiles ordered from
    smallest to largest magnitude.
    """
    dot_arr = np.asarray(dots, dtype=np.float64)
    mag_arr = np.asarray(magnitudes, dtype=np.float64)
    if dot_arr.shape != mag_arr.shape:
        raise ValueError(f"shapes differ: {dot_arr.shape} vs {mag_arr.shape}")
    order = np.argsort(mag_arr, kind="stable")
    assignments = np.empty(len(mag_arr), dtype=np.int64)
    assignments[order] = np.minimum(
        (np.arange(len(mag_arr)) * n_buckets) // max(len(mag_arr), 1), n_buckets - 1
    )
    rows = []
    for bucket in range(n_buckets):
        mask = assignments == bucket
        if not mask.any():
            continue
        rows.append(
            {
                "bucket": bucket,
                "n": int(mask.sum()),
                "magnitude_lo": float(mag_arr[mask].min()),
                "magnitude_hi": float(mag_arr[mask].max()),
                "p_toward": float((dot_arr[mask] > 0).mean()),
                "mean_dot": float(dot_arr[mask].mean()),
            }
        )
    return pd.DataFrame(rows)
