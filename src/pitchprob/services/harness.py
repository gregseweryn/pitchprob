"""Backtest orchestration shared by the CLI and the experiment harness (ADR 0010).

Two simulation clocks:

- ``at="close"`` — the legacy kickoff simulation: selection anchored on the
  Shin-de-margined Pinnacle closing line, settled at the best listed closing
  price. Its "CLV" measures cross-book price dispersion at the close
  (line-shopping value); there is no timing dimension.
- ``at="open"`` — the syndicate clock: everything the selector sees comes
  from the opening snapshot (prices and market anchor), settlement happens at
  opening prices, and closing-line value is the real thing — the price taken
  at the open versus where the sharp book closed. Auxiliary markets (OU 2.5
  and Asian handicap at the quoted opening line) are priced inside the
  walk-forward loop by score-matrix models; Asian-handicap CLV exists only
  where the closing line matches the opening line (coverage is reported).

Auxiliary-market model probabilities are *effective* win fractions of the
stake — for a book with pushes/half-wins the number ``p`` such that
``EV = p * price - 1`` — which is also exactly what a de-margined two-way
book quotes, so the log-linear blend stays coherent across markets.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, cast

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.betting.selection import select_value_bets
from pitchprob.betting.settlement import (
    settle_1x2,
    settle_asian_handicap,
    settle_totals,
)
from pitchprob.core.errors import PitchprobError
from pitchprob.data.dataset import load_matches_frame, load_odds_snapshot_frame
from pitchprob.evaluation.backtest import run_backtest
from pitchprob.evaluation.error_analysis import analyze_segments
from pitchprob.evaluation.metrics import (
    brier_score,
    expected_calibration_error,
    log_loss,
    ranked_probability_score,
)
from pitchprob.evaluation.significance import (
    block_bootstrap_mean,
    block_bootstrap_ratio,
    week_block_labels,
)
from pitchprob.evaluation.staking import StakingResult, simulate_staking
from pitchprob.markets import asian_handicap, totals

_ALLOWED_AT = ("close", "open")
_ALLOWED_MARKETS = ("1x2", "ou", "ah")
_ALLOWED_SELECTORS = ("naive", "blended", "meta")

#: Feature families the ablation flag can NaN out for A/B runs. New families
#: register here; the CLI validates against this registry.
ABLATABLE_FEATURES: dict[str, tuple[str, ...]] = {
    "absences": ("absences_home", "absences_away"),
}

#: Bootstrap resolution for the reported confidence intervals. 2000 keeps a
#: full-corpus run interactive; the significance p-floor is 1/n_boot.
_N_BOOT = 2000

_JOIN_KEYS = ["date", "home_team", "away_team"]
_PRICE_1X2 = ["price_home", "price_draw", "price_away"]


class NoDataError(PitchprobError):
    """No matches or predictions exist for the requested configuration —
    an operational condition (exit 1), not a usage error."""


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    start: date
    league: str = "E0"
    model: str = "dixon-coles"
    refit_days: int = 7
    min_train_matches: int = 380
    half_life: float = 390.0
    ev_threshold: float = 0.03
    pool: bool = False
    selector: str = "naive"
    blend_weight: float = 0.4
    max_price: float = 8.0
    calibration: str = "isotonic"
    end: date | None = None
    ablate: str | None = None
    at: str = "close"
    markets: tuple[str, ...] = ("1x2",)
    # CLV meta-gate (Phase 2 spec; used only with selector="meta")
    meta_refit_days: int = 90
    meta_buffer: float = 0.005
    meta_min_train: int = 1000


@dataclass(slots=True)
class HarnessResult:
    metrics: dict[str, Any]
    predictions: pd.DataFrame
    bets: pd.DataFrame | None


def build_model_factory(
    name: str, half_life: float, calibration: str = "isotonic"
) -> Callable[[], Any]:
    from pitchprob.models.dixon_coles import DixonColesModel, IndependentPoissonModel
    from pitchprob.models.elo import EloModel
    from pitchprob.models.ensemble import EnsembleModel
    from pitchprob.models.gbm import GbmModel

    key = name.replace("_", "-").lower()
    if key == "dixon-coles":
        return lambda: DixonColesModel(half_life_days=half_life)
    if key == "poisson":
        return lambda: IndependentPoissonModel(half_life_days=half_life)
    if key == "elo":
        return lambda: EloModel()
    if key == "gbm":
        return lambda: GbmModel()
    if key == "ensemble":
        return lambda: EnsembleModel(half_life_days=half_life)
    if key == "ensemble-cal":
        from pitchprob.models.calibrated import CalibratedEnsembleModel

        if calibration not in ("isotonic", "temperature"):
            raise ValueError(f"unknown calibration {calibration!r}")
        return lambda: CalibratedEnsembleModel(
            half_life_days=half_life,
            method=cast(Any, calibration),
        )
    raise ValueError(
        f"unknown model {name!r} "
        "(dixon-coles | poisson | elo | gbm | ensemble | ensemble-cal)"
    )


def _validate(config: HarnessConfig) -> None:
    if config.selector not in _ALLOWED_SELECTORS:
        raise ValueError(
            f"unknown selector {config.selector!r} (naive | blended | meta)"
        )
    if config.at not in _ALLOWED_AT:
        raise ValueError(f"unknown snapshot {config.at!r} (close | open)")
    if config.selector == "meta" and config.at != "open":
        raise ValueError(
            "the meta selector predicts sharp CLV at the opening snapshot; "
            "it requires at='open'"
        )
    unknown = [m for m in config.markets if m not in _ALLOWED_MARKETS]
    if unknown or not config.markets:
        raise ValueError(f"unknown markets {unknown!r} (choose from {_ALLOWED_MARKETS})")
    if config.at == "close" and set(config.markets) != {"1x2"}:
        raise ValueError(
            "auxiliary markets are only simulated at the opening snapshot "
            "(at='open'); the closing simulation is the legacy 1X2 protocol"
        )
    if config.ablate is not None and config.ablate not in ABLATABLE_FEATURES:
        known = ", ".join(sorted(ABLATABLE_FEATURES))
        raise ValueError(f"unknown ablation {config.ablate!r} (known: {known})")


def _shin_or_nan(prices: list[float]) -> list[float]:
    if any(not np.isfinite(p) or p <= 1.0 for p in prices):
        return [float("nan")] * len(prices)
    try:
        return remove_overround_shin(prices)
    except ValueError:
        return [float("nan")] * len(prices)


def _attach_shin(
    frame: pd.DataFrame, price_columns: list[str], out_columns: list[str]
) -> None:
    """Row-wise Shin de-margin of ``price_columns`` into ``out_columns``."""
    if frame.empty:
        for column in out_columns:
            frame[column] = float("nan")
        return
    values = np.array(
        [
            _shin_or_nan([float(getattr(row, c)) for c in price_columns])
            for row in frame.itertuples(index=False)
        ]
    )
    for i, column in enumerate(out_columns):
        frame[column] = values[:, i]


def _make_extra_predict(
    aux_markets: list[str], aux_books: Mapping[str, pd.DataFrame]
) -> Callable[[Any, Any], Mapping[str, float]]:
    ou_lines: dict[tuple[Any, str, str], Decimal] = {}
    ah_lines: dict[tuple[Any, str, str], Decimal] = {}
    if "ou" in aux_markets:
        for raw in aux_books["ou"].itertuples(index=False):
            row = cast(Any, raw)
            ou_lines[(row.date, row.home_team, row.away_team)] = row.line
    if "ah" in aux_markets:
        for raw in aux_books["ah"].itertuples(index=False):
            row = cast(Any, raw)
            ah_lines[(row.date, row.home_team, row.away_team)] = row.line

    def extra(model: Any, row: Any) -> Mapping[str, float]:
        if not hasattr(model, "score_matrix"):
            return {}
        key = (row.date, row.home_team, row.away_team)
        out: dict[str, float] = {}
        matrix = None
        if key in ou_lines:
            matrix = model.score_matrix(row.home_team, row.away_team)
            t = totals(matrix, ou_lines[key])
            out.update(p_over=t.over, p_under=t.under, p_ou_push=t.push)
        if key in ah_lines:
            if matrix is None:
                matrix = model.score_matrix(row.home_team, row.away_team)
            o = asian_handicap(matrix, ah_lines[key], "home")
            out.update(
                ah_full_win=o.full_win,
                ah_half_win=o.half_win,
                ah_push=o.push,
                ah_half_loss=o.half_loss,
                ah_full_loss=o.full_loss,
            )
        return out

    return extra


def _model_metrics(preds: pd.DataFrame) -> dict[str, Any]:
    probs = preds[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
    outcomes = preds["outcome"].to_numpy(dtype=np.int64)
    per_class_ece = {
        f"ece_{name}": expected_calibration_error(
            (outcomes == index).astype(np.int64), probs[:, index]
        )
        for index, name in enumerate(("home", "draw", "away"))
    }
    return {
        "n_predictions": len(preds),
        "model": {
            "log_loss": log_loss(outcomes, probs),
            "brier": brier_score(outcomes, probs),
            "rps": ranked_probability_score(outcomes, probs),
            **per_class_ece,
        },
    }


def _benchmark_block(
    preds: pd.DataFrame, book: pd.DataFrame, label: str
) -> dict[str, Any] | None:
    joined = preds.merge(book[[*_JOIN_KEYS, *_PRICE_1X2]], on=_JOIN_KEYS, how="inner")
    if joined.empty:
        return None
    prices = joined[_PRICE_1X2].to_numpy(dtype=np.float64)
    shin = np.array([remove_overround_shin(list(p)) for p in prices])
    outcomes = joined["outcome"].to_numpy(dtype=np.int64)
    probs = joined[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
    return {
        "n": len(joined),
        "model_log_loss": log_loss(outcomes, probs),
        "model_rps": ranked_probability_score(outcomes, probs),
        f"{label}_log_loss": log_loss(outcomes, shin),
        f"{label}_rps": ranked_probability_score(outcomes, shin),
    }


def _league_of(row: Any, config: HarnessConfig) -> str:
    return str(getattr(row, "league", config.league))


def _candidates_1x2_close(
    session: Session,
    config: HarnessConfig,
    preds: pd.DataFrame,
    closing_pinnacle: pd.DataFrame,
    eval_league: str | None,
) -> pd.DataFrame:
    """The legacy kickoff protocol: anchor and CLV from the Pinnacle close,
    settlement at the best listed closing price."""
    joined = preds.merge(
        closing_pinnacle[[*_JOIN_KEYS, *_PRICE_1X2]], on=_JOIN_KEYS, how="inner"
    )
    if joined.empty:
        return pd.DataFrame()
    _attach_shin(joined, _PRICE_1X2, ["q_home", "q_draw", "q_away"])

    best = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="market_max",
        market="1x2", closing=True,
    )
    price_source = best if len(best) else closing_pinnacle
    merged = joined.merge(
        price_source[[*_JOIN_KEYS, *_PRICE_1X2]],
        on=_JOIN_KEYS,
        how="inner",
        suffixes=("", "_best"),
    )
    records: list[dict[str, Any]] = []
    for raw_row in merged.itertuples(index=False):
        row = cast(Any, raw_row)
        for index, selection in enumerate(("home", "draw", "away")):
            fair = float(getattr(row, ("q_home", "q_draw", "q_away")[index]))
            records.append(
                {
                    "date": row.date,
                    "league": _league_of(row, config),
                    "market": "1x2",
                    "selection": selection,
                    "probability": float(
                        getattr(row, ("p_home", "p_draw", "p_away")[index])
                    ),
                    "market_probability": fair,
                    "price": float(getattr(row, f"price_{selection}_best")),
                    "won": int(row.outcome) == index,
                    "closing_probability": fair,
                }
            )
    return pd.DataFrame(records)


def _candidates_1x2_open(
    session: Session,
    config: HarnessConfig,
    preds: pd.DataFrame,
    opening_pinnacle: pd.DataFrame,
    closing_pinnacle: pd.DataFrame,
    eval_league: str | None,
) -> pd.DataFrame:
    joined = preds.merge(
        opening_pinnacle[[*_JOIN_KEYS, *_PRICE_1X2]], on=_JOIN_KEYS, how="inner"
    )
    if joined.empty:
        return pd.DataFrame()
    _attach_shin(joined, _PRICE_1X2, ["q_home", "q_draw", "q_away"])

    close = closing_pinnacle[[*_JOIN_KEYS, *_PRICE_1X2]].rename(
        columns={c: f"{c}_close" for c in _PRICE_1X2}
    )
    joined = joined.merge(close, on=_JOIN_KEYS, how="left")
    _attach_shin(
        joined,
        [f"{c}_close" for c in _PRICE_1X2],
        ["qc_home", "qc_draw", "qc_away"],
    )

    best = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="market_max",
        market="1x2", closing=False,
    )
    price_source = best if len(best) else opening_pinnacle
    merged = joined.merge(
        price_source[[*_JOIN_KEYS, *_PRICE_1X2]],
        on=_JOIN_KEYS,
        how="inner",
        suffixes=("", "_best"),
    )
    records: list[dict[str, Any]] = []
    for raw_row in merged.itertuples(index=False):
        row = cast(Any, raw_row)
        for index, selection in enumerate(("home", "draw", "away")):
            price = float(getattr(row, f"price_{selection}_best"))
            records.append(
                {
                    "date": row.date,
                    "league": _league_of(row, config),
                    "market": "1x2",
                    "selection": selection,
                    "probability": float(
                        getattr(row, ("p_home", "p_draw", "p_away")[index])
                    ),
                    "market_probability": float(
                        getattr(row, ("q_home", "q_draw", "q_away")[index])
                    ),
                    "price": price,
                    "price_sharp": float(getattr(row, f"price_{selection}")),
                    "closing_probability": float(
                        getattr(row, ("qc_home", "qc_draw", "qc_away")[index])
                    ),
                    "gross_return": settle_1x2(
                        cast(Any, selection), int(row.ft_home), int(row.ft_away), price
                    ),
                }
            )
    return pd.DataFrame(records)


def _candidates_ou_open(
    session: Session,
    config: HarnessConfig,
    preds: pd.DataFrame,
    aux_book: pd.DataFrame,
    eval_league: str | None,
) -> pd.DataFrame:
    covered = preds[preds["p_over"].notna()] if "p_over" in preds.columns else preds.iloc[0:0]
    if covered.empty:
        return pd.DataFrame()
    book = aux_book.rename(columns={"price_over": "b_over", "price_under": "b_under"})
    joined = covered.merge(
        book[[*_JOIN_KEYS, "line", "b_over", "b_under"]], on=_JOIN_KEYS, how="inner"
    )
    _attach_shin(joined, ["b_over", "b_under"], ["q_over", "q_under"])

    best = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="market_max",
        market="ou", closing=False,
    ).rename(columns={"price_over": "best_over", "price_under": "best_under"})
    joined = joined.merge(
        best[[*_JOIN_KEYS, "line", "best_over", "best_under"]],
        on=[*_JOIN_KEYS, "line"],
        how="left",
    )
    close = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="pinnacle",
        market="ou", closing=True,
    ).rename(columns={"price_over": "c_over", "price_under": "c_under"})
    joined = joined.merge(
        close[[*_JOIN_KEYS, "line", "c_over", "c_under"]],
        on=[*_JOIN_KEYS, "line"],
        how="left",
    )
    _attach_shin(joined, ["c_over", "c_under"], ["qc_over", "qc_under"])

    records: list[dict[str, Any]] = []
    for raw_row in joined.itertuples(index=False):
        row = cast(Any, raw_row)
        total = int(row.ft_home) + int(row.ft_away)
        push = float(row.p_ou_push)
        for selection, p_model in (("over", float(row.p_over)), ("under", float(row.p_under))):
            best_price = float(getattr(row, f"best_{selection}"))
            price = best_price if np.isfinite(best_price) else float(getattr(row, f"b_{selection}"))
            if not np.isfinite(price) or price <= 1.0:
                continue
            records.append(
                {
                    "date": row.date,
                    "league": _league_of(row, config),
                    "market": "ou",
                    "line": row.line,
                    "selection": selection,
                    # effective win fraction: EV = p*price - 1 with pushes in.
                    "probability": p_model + push / price,
                    "market_probability": float(getattr(row, f"q_{selection}")),
                    "price": price,
                    "price_sharp": float(getattr(row, f"b_{selection}")),
                    "closing_probability": float(getattr(row, f"qc_{selection}")),
                    "gross_return": settle_totals(
                        cast(Any, selection), total, row.line, price
                    ),
                }
            )
    return pd.DataFrame(records)


def _candidates_ah_open(
    session: Session,
    config: HarnessConfig,
    preds: pd.DataFrame,
    aux_book: pd.DataFrame,
    eval_league: str | None,
) -> pd.DataFrame:
    covered = (
        preds[preds["ah_full_win"].notna()]
        if "ah_full_win" in preds.columns
        else preds.iloc[0:0]
    )
    if covered.empty:
        return pd.DataFrame()
    book = aux_book.rename(columns={"price_home": "b_home", "price_away": "b_away"})
    joined = covered.merge(
        book[[*_JOIN_KEYS, "line", "b_home", "b_away"]], on=_JOIN_KEYS, how="inner"
    )
    _attach_shin(joined, ["b_home", "b_away"], ["q_home_ah", "q_away_ah"])

    best = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="market_max",
        market="ah", closing=False,
    ).rename(columns={"price_home": "best_home", "price_away": "best_away"})
    joined = joined.merge(
        best[[*_JOIN_KEYS, "line", "best_home", "best_away"]],
        on=[*_JOIN_KEYS, "line"],
        how="left",
    )
    close = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="pinnacle",
        market="ah", closing=True,
    ).rename(columns={"price_home": "c_home", "price_away": "c_away"})
    # CLV only where the closing line matches the opening line — a moved
    # line prices a different bet.
    joined = joined.merge(
        close[[*_JOIN_KEYS, "line", "c_home", "c_away"]],
        on=[*_JOIN_KEYS, "line"],
        how="left",
    )
    _attach_shin(joined, ["c_home", "c_away"], ["qc_home_ah", "qc_away_ah"])

    records: list[dict[str, Any]] = []
    for raw_row in joined.itertuples(index=False):
        row = cast(Any, raw_row)
        home_dist = (
            float(row.ah_full_win),
            float(row.ah_half_win),
            float(row.ah_push),
            float(row.ah_half_loss),
            float(row.ah_full_loss),
        )
        away_dist = tuple(reversed(home_dist))
        for side, dist in (("home", home_dist), ("away", away_dist)):
            best_price = float(getattr(row, f"best_{side}"))
            price = best_price if np.isfinite(best_price) else float(getattr(row, f"b_{side}"))
            if not np.isfinite(price) or price <= 1.0:
                continue
            fw, hw, push, hl, _ = dist
            expected_gross = fw * price + hw * (price + 1.0) / 2.0 + push + hl * 0.5
            records.append(
                {
                    "date": row.date,
                    "league": _league_of(row, config),
                    "market": "ah",
                    "line": row.line,
                    "selection": side,
                    "probability": expected_gross / price,
                    "market_probability": float(getattr(row, f"q_{side}_ah")),
                    "price": price,
                    "price_sharp": float(getattr(row, f"b_{side}")),
                    "closing_probability": float(getattr(row, f"qc_{side}_ah")),
                    "gross_return": settle_asian_handicap(
                        cast(Any, side), int(row.ft_home), int(row.ft_away),
                        row.line, price,
                    ),
                }
            )
    return pd.DataFrame(records)


def _run_selector(candidates: pd.DataFrame, config: HarnessConfig) -> StakingResult:
    if config.selector == "meta":
        from pitchprob.betting.meta import MetaGateConfig, walk_forward_gate

        gated = walk_forward_gate(
            candidates,
            config=MetaGateConfig(
                refit_days=config.meta_refit_days,
                buffer=config.meta_buffer,
                min_train=config.meta_min_train,
            ),
        )
        return simulate_staking(gated, strategy="flat", ev_threshold=-1.0)
    if config.selector == "blended":
        selected = select_value_bets(
            candidates,
            blend_weight=config.blend_weight,
            ev_threshold=config.ev_threshold,
            max_price=config.max_price,
        )
        # bets are pre-qualified on blended EV; settle them all at the
        # anchored probability
        sim_frame = (
            selected.assign(probability=selected["p_bet"]) if len(selected) else selected
        )
        return simulate_staking(sim_frame, strategy="flat", ev_threshold=-1.0)
    return simulate_staking(
        candidates, strategy="flat", ev_threshold=config.ev_threshold
    )


def _staking_block(staking: StakingResult, config: HarnessConfig) -> dict[str, Any]:
    block: dict[str, Any] = {
        "selector": config.selector,
        "n_bets": staking.n_bets,
        "roi": staking.roi,
        "profit_units": staking.profit,
        "hit_rate": staking.hit_rate,
        "max_drawdown": staking.max_drawdown,
        "mean_clv": staking.mean_clv,
    }
    log = staking.bet_log
    if log is None or len(log) == 0:
        return block
    blocks = week_block_labels(list(log["date"]))
    try:
        roi_ci = block_bootstrap_ratio(
            log["pnl"].to_numpy(dtype=np.float64),
            log["stake"].to_numpy(dtype=np.float64),
            blocks,
            n_boot=_N_BOOT,
        )
        block["roi_ci"] = {
            "lo": roi_ci.lo, "hi": roi_ci.hi,
            "p_value": roi_ci.p_value, "n_blocks": roi_ci.n_blocks,
        }
    except ValueError:
        pass
    with_clv = log[log["clv"].notna()]
    if len(with_clv):
        try:
            clv_ci = block_bootstrap_mean(
                with_clv["clv"].to_numpy(dtype=np.float64),
                week_block_labels(list(with_clv["date"])),
                n_boot=_N_BOOT,
            )
            block["clv_ci"] = {
                "lo": clv_ci.lo, "hi": clv_ci.hi,
                "p_value": clv_ci.p_value, "n_blocks": clv_ci.n_blocks,
            }
        except ValueError:
            pass
    # The Phase 2 primary endpoint: timing-only CLV against the sharp book,
    # immune to the best-price line-shopping artifact.
    if "clv_sharp" in log.columns:
        sharp = log[log["clv_sharp"].notna()]
        if len(sharp):
            block["mean_clv_sharp"] = float(sharp["clv_sharp"].mean())
            try:
                sharp_ci = block_bootstrap_mean(
                    sharp["clv_sharp"].to_numpy(dtype=np.float64),
                    week_block_labels(list(sharp["date"])),
                    n_boot=_N_BOOT,
                )
                block["clv_sharp_ci"] = {
                    "lo": sharp_ci.lo, "hi": sharp_ci.hi,
                    "p_value": sharp_ci.p_value, "n_blocks": sharp_ci.n_blocks,
                }
            except ValueError:
                pass
    if "market" in log.columns and log["market"].nunique() > 1:
        per_market: dict[str, Any] = {}
        for market, sub in log.groupby("market"):
            clv_sub = sub[sub["clv"].notna()]["clv"]
            per_market[str(market)] = {
                "n_bets": len(sub),
                "roi": float(sub["pnl"].sum() / sub["stake"].sum()),
                "mean_clv": float(clv_sub.mean()) if len(clv_sub) else None,
            }
        block["per_market"] = per_market
    return block


#: Bet logs smaller than this produce segment tables that are all noise;
#: the error analysis stays silent instead.
_ERROR_ANALYSIS_MIN_BETS = 60


def _error_analysis(log: pd.DataFrame | None) -> dict[str, list[dict[str, Any]]]:
    """Segment-level loss decomposition (market / price band / season /
    league), BH-corrected, JSON-safe for the metrics payload."""
    if log is None or len(log) < _ERROR_ANALYSIS_MIN_BETS:
        return {}
    groupings = [["price_band"], ["season"]]
    for column in ("market", "league"):
        if column in log.columns and log[column].nunique() > 1:
            groupings.append([column])
    out: dict[str, list[dict[str, Any]]] = {}
    for group in groupings:
        table = analyze_segments(log, group_by=group, n_boot=1000)
        if len(table) == 0:
            continue
        records: list[dict[str, Any]] = []
        for raw in table.to_dict(orient="records"):
            record: dict[str, Any] = {}
            for key, value in raw.items():
                name = str(key)
                if isinstance(value, np.bool_):
                    record[name] = bool(value)
                elif isinstance(value, np.integer):
                    record[name] = int(value)
                elif isinstance(value, np.floating):
                    record[name] = float(value)
                else:
                    record[name] = value
            records.append(record)
        out["+".join(group)] = records
    return out


def run_harness(
    session: Session,
    config: HarnessConfig,
    *,
    model_factory: Callable[[], Any] | None = None,
) -> HarnessResult:
    _validate(config)
    factory = model_factory or build_model_factory(
        config.model, config.half_life, config.calibration
    )
    eval_league = None if config.league == "all" else config.league
    frame_league = None if (config.pool or config.league == "all") else config.league
    frame = load_matches_frame(
        session, league_code=frame_league, include_stats=True, end=config.end
    )
    if frame.empty:
        raise NoDataError(f"no matches ingested for {config.league!r}")
    if config.ablate is not None:
        for column in ABLATABLE_FEATURES[config.ablate]:
            if column in frame.columns:
                frame[column] = float("nan")

    aux_markets = [m for m in config.markets if m != "1x2"]
    aux_books: dict[str, pd.DataFrame] = {}
    extra = None
    if aux_markets:
        for market in aux_markets:
            aux_books[market] = load_odds_snapshot_frame(
                session, league_code=eval_league, bookmaker="pinnacle",
                market=market, closing=False,
            )
        extra = _make_extra_predict(aux_markets, aux_books)

    preds = run_backtest(
        frame,
        model_factory=factory,
        start=config.start,
        refit_every_days=config.refit_days,
        min_train_matches=config.min_train_matches,
        predict_only=(
            {"league": config.league}
            if (config.pool and config.league != "all")
            else None
        ),
        extra_predict=extra,
    )
    if preds.empty:
        raise NoDataError("no predictions generated (check --start and training history)")

    metrics = _model_metrics(preds)
    if config.league == "all" and "league" in preds.columns:
        per_league: dict[str, Any] = {}
        for code, sub in preds.groupby("league"):
            probs = sub[["p_home", "p_draw", "p_away"]].to_numpy(dtype=np.float64)
            outcomes = sub["outcome"].to_numpy(dtype=np.int64)
            per_league[str(code)] = {
                "n": len(sub),
                "log_loss": log_loss(outcomes, probs),
                "rps": ranked_probability_score(outcomes, probs),
            }
        metrics["per_league"] = per_league

    closing_pinnacle = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="pinnacle",
        market="1x2", closing=True,
    )
    opening_pinnacle = load_odds_snapshot_frame(
        session, league_code=eval_league, bookmaker="pinnacle",
        market="1x2", closing=False,
    )
    close_block = _benchmark_block(preds, closing_pinnacle, "closing")
    if close_block is not None:
        metrics["benchmark_subset"] = close_block
    open_block = _benchmark_block(preds, opening_pinnacle, "open")
    if open_block is not None:
        metrics["benchmark_open_subset"] = open_block

    parts: list[pd.DataFrame] = []
    coverage: dict[str, float] = {}
    if config.at == "close":
        parts.append(
            _candidates_1x2_close(session, config, preds, closing_pinnacle, eval_league)
        )
    else:
        if "1x2" in config.markets:
            parts.append(
                _candidates_1x2_open(
                    session, config, preds, opening_pinnacle, closing_pinnacle,
                    eval_league,
                )
            )
        if "ou" in config.markets:
            parts.append(
                _candidates_ou_open(session, config, preds, aux_books["ou"], eval_league)
            )
        if "ah" in config.markets:
            parts.append(
                _candidates_ah_open(session, config, preds, aux_books["ah"], eval_league)
            )
    parts = [p for p in parts if len(p)]
    bets: pd.DataFrame | None = None
    if parts:
        candidates = pd.concat(parts, ignore_index=True)
        # Sharp CLV (the Phase 2 primary endpoint) is reported for every
        # selector, not just the meta gate — a strategy's clv_exec without
        # its clv_sharp invites the line-shopping misreading.
        if "price_sharp" in candidates.columns:
            candidates["clv_sharp"] = (
                candidates["price_sharp"] * candidates["closing_probability"] - 1.0
            )
        for market in ("ou", "ah"):
            if "market" not in candidates.columns:
                continue
            sub = candidates[candidates["market"] == market]
            if len(sub):
                coverage[market] = float(sub["closing_probability"].notna().mean())
        staking = _run_selector(candidates, config)
        metrics["staking_flat"] = _staking_block(staking, config)
        if coverage:
            metrics["clv_coverage"] = coverage
        bets = staking.bet_log
        error_blocks = _error_analysis(staking.bet_log)
        if error_blocks:
            metrics["error_analysis"] = error_blocks
    return HarnessResult(metrics=metrics, predictions=preds, bets=bets)
