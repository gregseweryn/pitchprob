"""Experiment registry and paired comparison over the backtest harness (ADR 0010).

Every research idea runs through the same protocol: two harness
configurations predict the *identical* fixtures, per-match metric deltas are
block-bootstrapped by ISO week, and betting deltas are compared on per-week
aggregates through the same resampled blocks. A comparison that does not
clear its confidence interval is reported as a null — the harness cannot be
sweet-talked.

Runs are stored in the ``backtests`` table with a name and a content hash of
their configuration, so any published number can be traced to the exact
configuration that produced it.
"""

import hashlib
import json
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Any, cast

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from pitchprob.data.orm import Backtest
from pitchprob.evaluation.significance import (
    BootstrapSummary,
    block_bootstrap_mean,
    block_bootstrap_ratio_delta,
    week_block_labels,
)
from pitchprob.services.harness import HarnessConfig, run_harness

_JOIN_KEYS = ["date", "home_team", "away_team"]
_EPS = 1e-15

#: Two-sided significance level for the report's verdict column.
_ALPHA = 0.05


@dataclass(slots=True)
class ComparisonResult:
    metrics: dict[str, Any]
    report: str


def _config_payload(config: HarnessConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in fields(HarnessConfig):
        value = getattr(config, field.name)
        if isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = list(value)
        payload[field.name] = value
    return payload


def config_hash(config: HarnessConfig) -> str:
    canonical = json.dumps(_config_payload(config), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def apply_overrides(config: HarnessConfig, overrides: list[str]) -> HarnessConfig:
    """Derive a B-side configuration from ``key=value`` override strings —
    the one-liner that turns any run into an A/B experiment."""
    valid = {field.name for field in fields(HarnessConfig)}
    updates: dict[str, Any] = {}
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"override must be key=value, got {item!r}")
        key, _, raw = item.partition("=")
        key = key.strip().replace("-", "_")
        if key not in valid:
            known = ", ".join(sorted(valid))
            raise ValueError(f"unknown config field {key!r} (known: {known})")
        updates[key] = _coerce(key, raw.strip())
    return replace(config, **updates)


def _coerce(key: str, value: str) -> Any:
    if key in ("start", "end"):
        return date.fromisoformat(value) if value else None
    if key == "markets":
        return tuple(m.strip() for m in value.split(",") if m.strip())
    if key in ("refit_days", "min_train_matches", "meta_refit_days", "meta_min_train"):
        return int(value)
    if key in ("half_life", "ev_threshold", "blend_weight", "max_price", "meta_buffer"):
        return float(value)
    if key == "pool":
        return value.lower() in ("1", "true", "yes")
    if key == "ablate":
        return value or None
    return value


def run_experiment(
    session: Session,
    config: HarnessConfig,
    *,
    name: str | None = None,
    model_factory: Any = None,
) -> tuple[int, dict[str, Any]]:
    """Run one harness configuration and store the result as a named,
    content-hashed row in the ``backtests`` table."""
    from datetime import UTC, datetime

    result = run_harness(session, config, model_factory=model_factory)
    row = Backtest(
        created_at=datetime.now(tz=UTC),
        config={
            **_config_payload(config),
            "experiment": {"name": name, "hash": config_hash(config)},
        },
        metrics=result.metrics,
    )
    session.add(row)
    session.flush()
    return int(row.id), result.metrics


def _per_match_log_loss(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    realized = probs[np.arange(len(outcomes)), outcomes]
    result: np.ndarray = -np.log(np.clip(realized, _EPS, None))
    return result


def _per_match_rps(probs: np.ndarray, outcomes: np.ndarray) -> np.ndarray:
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(outcomes)), outcomes] = 1.0
    cum_diff = probs.cumsum(axis=1) - onehot.cumsum(axis=1)
    result: np.ndarray = (cum_diff[:, :-1] ** 2).sum(axis=1) / (probs.shape[1] - 1)
    return result


def _weekly_bet_aggregates(bets: pd.DataFrame | None) -> pd.DataFrame | None:
    if bets is None or len(bets) == 0:
        return None
    frame = bets.copy()
    frame["week"] = week_block_labels(list(frame["date"]))
    frame["clv_present"] = frame["clv"].notna().astype(float)
    frame["clv_filled"] = frame["clv"].fillna(0.0)
    grouped = frame.groupby("week").agg(
        pnl=("pnl", "sum"),
        stake=("stake", "sum"),
        clv_sum=("clv_filled", "sum"),
        clv_n=("clv_present", "sum"),
    )
    return grouped


def _summary_dict(summary: BootstrapSummary) -> dict[str, Any]:
    return {
        "point": summary.point,
        "lo": summary.lo,
        "hi": summary.hi,
        "p_value": summary.p_value,
        "n_blocks": summary.n_blocks,
    }


def compare_experiments(
    session: Session,
    config_a: HarnessConfig,
    config_b: HarnessConfig,
    *,
    n_boot: int = 5000,
    store: bool = False,
    model_factory_a: Any = None,
    model_factory_b: Any = None,
) -> ComparisonResult:
    """Run both configurations, pair them on identical fixtures, and
    block-bootstrap the deltas (A minus B). Betting deltas compare per-week
    aggregates through the same resampled blocks."""
    result_a = run_harness(session, config_a, model_factory=model_factory_a)
    result_b = run_harness(session, config_b, model_factory=model_factory_b)

    paired = result_a.predictions.merge(
        result_b.predictions, on=_JOIN_KEYS, how="inner", suffixes=("_a", "_b")
    )
    if paired.empty:
        raise ValueError("configurations share no predicted fixtures — nothing to pair")
    outcomes = paired["outcome_a"].to_numpy(dtype=np.int64)
    if not (outcomes == paired["outcome_b"].to_numpy(dtype=np.int64)).all():
        raise ValueError("paired fixtures disagree on outcomes — data integrity error")
    probs_a = paired[["p_home_a", "p_draw_a", "p_away_a"]].to_numpy(dtype=np.float64)
    probs_b = paired[["p_home_b", "p_draw_b", "p_away_b"]].to_numpy(dtype=np.float64)
    blocks = week_block_labels(list(paired["date"]))

    delta_ll = block_bootstrap_mean(
        _per_match_log_loss(probs_a, outcomes) - _per_match_log_loss(probs_b, outcomes),
        blocks,
        n_boot=n_boot,
    )
    delta_rps = block_bootstrap_mean(
        _per_match_rps(probs_a, outcomes) - _per_match_rps(probs_b, outcomes),
        blocks,
        n_boot=n_boot,
    )

    metrics: dict[str, Any] = {
        "n_paired": len(paired),
        "n_boot": n_boot,
        "config_a": _config_payload(config_a),
        "config_b": _config_payload(config_b),
        "hash_a": config_hash(config_a),
        "hash_b": config_hash(config_b),
        "a": result_a.metrics,
        "b": result_b.metrics,
        "delta_log_loss": _summary_dict(delta_ll),
        "delta_rps": _summary_dict(delta_rps),
    }

    weekly_a = _weekly_bet_aggregates(result_a.bets)
    weekly_b = _weekly_bet_aggregates(result_b.bets)
    if weekly_a is not None and weekly_b is not None:
        weeks = weekly_a.index.union(weekly_b.index)
        if len(weeks) >= 2:
            a = weekly_a.reindex(weeks, fill_value=0.0)
            b = weekly_b.reindex(weeks, fill_value=0.0)
            delta_roi = block_bootstrap_ratio_delta(
                a["pnl"].to_numpy(), a["stake"].to_numpy(),
                b["pnl"].to_numpy(), b["stake"].to_numpy(),
                n_boot=n_boot,
            )
            metrics["delta_roi"] = _summary_dict(delta_roi)
            if a["clv_n"].sum() > 0 and b["clv_n"].sum() > 0:
                delta_clv = block_bootstrap_ratio_delta(
                    a["clv_sum"].to_numpy(), a["clv_n"].to_numpy(),
                    b["clv_sum"].to_numpy(), b["clv_n"].to_numpy(),
                    n_boot=n_boot,
                )
                metrics["delta_clv"] = _summary_dict(delta_clv)

    report = _render_report(metrics)
    if store:
        from datetime import UTC, datetime

        session.add(
            Backtest(
                created_at=datetime.now(tz=UTC),
                config={
                    "experiment_compare": {
                        "hash_a": metrics["hash_a"],
                        "hash_b": metrics["hash_b"],
                    }
                },
                metrics=metrics,
            )
        )
    return ComparisonResult(metrics=metrics, report=report)


def _verdict(summary: dict[str, Any], lower_is_better: bool) -> str:
    if summary["p_value"] >= _ALPHA:
        return "null (indistinguishable)"
    better_is_a = summary["point"] < 0 if lower_is_better else summary["point"] > 0
    return "A better" if better_is_a else "B better"


def _fmt(value: float) -> str:
    return f"{value:+.4f}"


def _render_report(metrics: dict[str, Any]) -> str:
    lines = [
        "# Experiment comparison (paired, block bootstrap by ISO week)",
        "",
        f"- fixtures paired: {metrics['n_paired']}, resamples: {metrics['n_boot']}",
        f"- A: `{metrics['hash_a']}` — B: `{metrics['hash_b']}` "
        "(full configs stored alongside)",
        "",
        "| metric | A | B | delta (A-B) | 95% CI | p | verdict |",
        "|---|---|---|---|---|---|---|",
    ]

    def row(
        label: str,
        a_value: float | None,
        b_value: float | None,
        summary: dict[str, Any] | None,
        lower_is_better: bool,
    ) -> None:
        if summary is None:
            return
        a_text = f"{a_value:.4f}" if a_value is not None else "—"
        b_text = f"{b_value:.4f}" if b_value is not None else "—"
        lines.append(
            f"| {label} | {a_text} | {b_text} | {_fmt(summary['point'])} "
            f"| [{_fmt(summary['lo'])}, {_fmt(summary['hi'])}] "
            f"| {summary['p_value']:.3f} | {_verdict(summary, lower_is_better)} |"
        )

    a_model = metrics["a"]["model"]
    b_model = metrics["b"]["model"]
    row("log-loss", a_model["log_loss"], b_model["log_loss"],
        metrics["delta_log_loss"], lower_is_better=True)
    row("RPS", a_model["rps"], b_model["rps"],
        metrics["delta_rps"], lower_is_better=True)
    a_staking = cast(dict[str, Any], metrics["a"].get("staking_flat", {}))
    b_staking = cast(dict[str, Any], metrics["b"].get("staking_flat", {}))
    row("ROI", a_staking.get("roi"), b_staking.get("roi"),
        metrics.get("delta_roi"), lower_is_better=False)
    row("mean CLV", a_staking.get("mean_clv"), b_staking.get("mean_clv"),
        metrics.get("delta_clv"), lower_is_better=False)
    lines += [
        "",
        "Lower is better for log-loss/RPS; higher for ROI/CLV. A verdict of "
        f"null means the delta did not clear the {int((1 - _ALPHA) * 100)}% "
        "interval — report it as a null result, not a trend.",
    ]
    return "\n".join(lines)
