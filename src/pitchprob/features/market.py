"""Market-derived features available at bet time (Phase 1 of ADR 0010's
research program).

Everything here is computable from the *opening* snapshot alone: the sharp
book's de-margined probabilities, its margin, how far the soft and consensus
books sit from it, and where the favourite is priced. Closing information
never enters — these features feed models and bet gates that must act before
the market settles, while open→close movement is reserved for *labels* and
diagnostics.
"""

from collections.abc import Mapping
from typing import Any, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from pitchprob.betting.odds_math import implied_probabilities, remove_overround_shin

FloatArray = npt.NDArray[np.float64]

_KEYS = ["date", "home_team", "away_team"]
_PRICES = ["price_home", "price_draw", "price_away"]
_EPS = 1e-12


def kl_divergence(p: FloatArray, q: FloatArray) -> float:
    """KL(p || q) in nats — the standard asymmetric disagreement measure;
    ``p`` is the book being compared, ``q`` the reference (Pinnacle)."""
    p_safe = np.clip(np.asarray(p, dtype=np.float64), _EPS, None)
    q_safe = np.clip(np.asarray(q, dtype=np.float64), _EPS, None)
    return float(np.sum(p_safe * np.log(p_safe / q_safe)))


def _shin_row(prices: list[float]) -> list[float]:
    if any(not np.isfinite(value) or value <= 1.0 for value in prices):
        return [float("nan")] * len(prices)
    try:
        return remove_overround_shin(prices)
    except ValueError:
        return [float("nan")] * len(prices)


def _fair_columns(frame: pd.DataFrame, prefix: str) -> pd.DataFrame:
    out = frame[_KEYS].copy()
    fair = np.array(
        [
            _shin_row(
                [
                    float(cast(Any, row).price_home),
                    float(cast(Any, row).price_draw),
                    float(cast(Any, row).price_away),
                ]
            )
            for row in frame.itertuples(index=False)
        ]
    )
    out[f"{prefix}_home"] = fair[:, 0]
    out[f"{prefix}_draw"] = fair[:, 1]
    out[f"{prefix}_away"] = fair[:, 2]
    return out


def build_1x2_market_features(
    opening_books: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """One row per match with bet-time market features.

    ``opening_books`` maps bookmaker name to an opening-snapshot frame from
    ``load_odds_snapshot_frame`` (must include ``pinnacle`` — the sharp
    reference every other book is measured against). Books missing a match
    contribute NaN features for it; the match row itself survives.

    Columns: ``fair_home/draw/away`` and ``overround`` (Pinnacle),
    ``fair_favourite`` (the favourite's fair probability — the
    favourite-longshot axis), ``<book>_kl`` for each non-consensus book
    (Shin-fair KL against Pinnacle), and ``maxavg_spread_<sel>`` (relative
    max-over-avg price spread — a cross-book dispersion/liquidity proxy)
    when both consensus books are present.
    """
    if "pinnacle" not in opening_books:
        raise ValueError("market features require the 'pinnacle' reference book")
    pinnacle = opening_books["pinnacle"]
    features = _fair_columns(pinnacle, "fair")
    features = features.rename(
        columns={"fair_home": "fair_home", "fair_draw": "fair_draw", "fair_away": "fair_away"}
    )
    overrounds: list[float] = []
    for raw_row in pinnacle.itertuples(index=False):
        row = cast(Any, raw_row)
        prices = [float(row.price_home), float(row.price_draw), float(row.price_away)]
        if min(prices) > 1.0:
            overrounds.append(sum(implied_probabilities(prices)) - 1.0)
        else:
            overrounds.append(float("nan"))
    features["overround"] = overrounds
    fair_matrix = features[["fair_home", "fair_draw", "fair_away"]].to_numpy()
    features["fair_favourite"] = np.nanmax(fair_matrix, axis=1)

    for book, frame in opening_books.items():
        if book in ("pinnacle", "market_max", "market_avg"):
            continue
        other = _fair_columns(frame, "other")
        merged = features[_KEYS].merge(other, on=_KEYS, how="left")
        kl_values = []
        for i in range(len(features)):
            p = merged[["other_home", "other_draw", "other_away"]].iloc[i].to_numpy()
            q = fair_matrix[i]
            if np.isnan(p).any() or np.isnan(q).any():
                kl_values.append(float("nan"))
            else:
                kl_values.append(kl_divergence(p, q))
        features[f"{book}_kl"] = kl_values

    if "market_max" in opening_books and "market_avg" in opening_books:
        best = opening_books["market_max"][_KEYS + _PRICES].rename(
            columns={c: f"{c}_max" for c in _PRICES}
        )
        avg = opening_books["market_avg"][_KEYS + _PRICES].rename(
            columns={c: f"{c}_avg" for c in _PRICES}
        )
        merged = features[_KEYS].merge(best, on=_KEYS, how="left").merge(
            avg, on=_KEYS, how="left"
        )
        for selection in ("home", "draw", "away"):
            features[f"maxavg_spread_{selection}"] = (
                merged[f"price_{selection}_max"] - merged[f"price_{selection}_avg"]
            ) / merged[f"price_{selection}_avg"]

    return features
