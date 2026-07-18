"""The Phase 1 market-information study: does the open→close move side with
the model?

For every walk-forward prediction with both an opening and a closing Pinnacle
1X2 book, three directions are compared (see ``evaluation.movement``):

- movement ``m`` = close fair minus open fair,
- model divergence ``d`` = model minus open fair,
- truth direction ``t`` = realized outcome (one-hot) minus open fair.

``P(m·t > 0)`` says whether the close is sharper than the open (it should
be). ``P(m·d > 0)``, bucketed by ``|d|``, is the study's headline: if the
market disproportionately moves toward the model exactly where the model
disagrees most, model divergence carries information the opening price lacks
— the precondition for any CLV-positive bet gate. If it does not, the Phase 2
meta-gate has to find the exceptional subset or conclude there is none.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from pitchprob.data.dataset import load_matches_frame, load_odds_snapshot_frame
from pitchprob.evaluation.backtest import run_backtest
from pitchprob.evaluation.movement import (
    bucketed_agreement,
    directional_agreement,
    movement_vectors,
)
from pitchprob.evaluation.significance import block_bootstrap_mean, week_block_labels
from pitchprob.models.dixon_coles import DEFAULT_HALF_LIFE_DAYS
from pitchprob.services.harness import (
    NoDataError,
    _attach_shin,  # shared row-wise Shin; the study is a sibling service
    build_model_factory,
)

_JOIN_KEYS = ["date", "home_team", "away_team"]
_PRICES = ["price_home", "price_draw", "price_away"]


@dataclass(slots=True)
class MovementStudyResult:
    metrics: dict[str, Any]
    report: str


def run_movement_study(
    session: Session,
    *,
    league: str,
    start: date,
    end: date | None = None,
    model: str = "dixon-coles",
    refit_days: int = 28,
    min_train_matches: int = 380,
    half_life: float = DEFAULT_HALF_LIFE_DAYS,
    n_buckets: int = 5,
    n_boot: int = 2000,
    model_factory: Any = None,
) -> MovementStudyResult:
    frame = load_matches_frame(
        session, league_code=league, include_stats=True, end=end
    )
    if frame.empty:
        raise NoDataError(f"no matches ingested for {league!r}")
    factory = model_factory or build_model_factory(model, half_life)
    preds = run_backtest(
        frame,
        model_factory=factory,
        start=start,
        refit_every_days=refit_days,
        min_train_matches=min_train_matches,
    )
    if preds.empty:
        raise NoDataError("no predictions generated (check start and history)")

    opening = load_odds_snapshot_frame(
        session, league_code=league, bookmaker="pinnacle", market="1x2", closing=False
    ).rename(columns={c: f"{c}_open" for c in _PRICES})
    closing = load_odds_snapshot_frame(
        session, league_code=league, bookmaker="pinnacle", market="1x2", closing=True
    ).rename(columns={c: f"{c}_close" for c in _PRICES})
    joined = preds.merge(
        opening[[*_JOIN_KEYS, *(f"{c}_open" for c in _PRICES)]],
        on=_JOIN_KEYS, how="inner",
    ).merge(
        closing[[*_JOIN_KEYS, *(f"{c}_close" for c in _PRICES)]],
        on=_JOIN_KEYS, how="inner",
    )
    if joined.empty:
        raise NoDataError("no predictions with both opening and closing books")
    _attach_shin(joined, [f"{c}_open" for c in _PRICES], ["o_home", "o_draw", "o_away"])
    _attach_shin(joined, [f"{c}_close" for c in _PRICES], ["c_home", "c_draw", "c_away"])
    joined = joined.dropna(subset=["o_home", "c_home"]).reset_index(drop=True)

    open_fair = joined[["o_home", "o_draw", "o_away"]].to_numpy(dtype=np.float64)
    close_fair = joined[["c_home", "c_draw", "c_away"]].to_numpy(dtype=np.float64)
    model_probs = joined[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
    outcomes = joined["outcome"].to_numpy(dtype=np.int64)
    onehot = np.zeros_like(model_probs)
    onehot[np.arange(len(outcomes)), outcomes] = 1.0

    movement = movement_vectors(open_fair, close_fair)
    divergence = model_probs - open_fair
    truth = onehot - open_fair

    toward_model = directional_agreement(movement, divergence)
    toward_truth = directional_agreement(movement, truth)
    divergence_l1 = np.abs(divergence).sum(axis=1)
    movement_l1 = np.abs(movement).sum(axis=1)
    buckets = bucketed_agreement(toward_model, divergence_l1, n_buckets=n_buckets)

    blocks = week_block_labels(list(joined["date"]))
    toward_model_ci = block_bootstrap_mean(toward_model, blocks, n_boot=n_boot)

    outcome_counts = {
        name: int((outcomes == index).sum())
        for index, name in enumerate(("home", "draw", "away"))
    }
    metrics: dict[str, Any] = {
        "study": "movement",
        "league": league,
        "model": model,
        "n": len(joined),
        "p_toward_truth": float((toward_truth > 0).mean()),
        "mean_toward_truth": float(toward_truth.mean()),
        "p_toward_model": float((toward_model > 0).mean()),
        "mean_toward_model": float(toward_model.mean()),
        "mean_toward_model_ci": {
            "lo": toward_model_ci.lo,
            "hi": toward_model_ci.hi,
            "p_value": toward_model_ci.p_value,
        },
        "mean_movement_l1": float(movement_l1.mean()),
        "mean_divergence_l1": float(divergence_l1.mean()),
        "outcome_counts": outcome_counts,
        "toward_model_buckets": buckets.to_dict(orient="records"),
    }
    return MovementStudyResult(metrics=metrics, report=_render(metrics))


def _render(metrics: dict[str, Any]) -> str:
    ci = metrics["mean_toward_model_ci"]
    lines = [
        f"# Movement study — {metrics['league']}, {metrics['model']} "
        f"(n={metrics['n']})",
        "",
        f"- Close sharper than open (moved toward truth): "
        f"P={metrics['p_toward_truth']:.3f}, mean dot "
        f"{metrics['mean_toward_truth']:+.5f}",
        f"- Moved toward model: P={metrics['p_toward_model']:.3f}, mean dot "
        f"{metrics['mean_toward_model']:+.5f} "
        f"(95% CI [{ci['lo']:+.5f}, {ci['hi']:+.5f}], p={ci['p_value']:.3f})",
        f"- Mean |movement| {metrics['mean_movement_l1']:.4f}, "
        f"mean |model divergence| {metrics['mean_divergence_l1']:.4f} (L1)",
        "",
        "By model-divergence bucket (small → large):",
        "",
        "| bucket | n | divergence range | P(toward model) | mean dot |",
        "|---|---|---|---|---|",
    ]
    for row in metrics["toward_model_buckets"]:
        lines.append(
            f"| {row['bucket']} | {row['n']} "
            f"| {row['magnitude_lo']:.3f}-{row['magnitude_hi']:.3f} "
            f"| {row['p_toward']:.3f} | {row['mean_dot']:+.5f} |"
        )
    lines += [
        "",
        "A mean dot near zero (or negative) in every bucket means model-open",
        "divergence carries no information the market later confirms — the",
        "honest null for this study.",
    ]
    return "\n".join(lines)
