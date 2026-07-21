"""Market-book construction: one fixture → every market probability.

This is the application service shared by the CLI and the API. Since M2 the
headline 1X2 probabilities come from the stacking **ensemble** (Dixon-Coles +
Elo + GBM, ADR 0005); component probabilities are reported alongside for
transparency, and goals markets still derive from the Dixon-Coles score
matrix per ADR 0002 (the ensemble is outcome-space).

Fitted ensembles are cached per ``(league, half-life, data version)`` where
the data version changes whenever matches are ingested — the M1 fit-per-call
cost is gone and a stale model can never serve after new data lands.

Asian handicap entries report, per line, the probability that a bet on each
side wins (fully or half) plus the push probability.

Value analysis against offered odds runs on the **betting path**: Dixon-Coles
probabilities blended with the Shin-de-margined offered prices (ADR 0006).
The ensemble is display-only per the M3/M7 verdicts — its raw EV is the
quantified longshot trap (-11.2% ROI in M2) and must never price a bet.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.betting.selection import DEFAULT_BLEND_WEIGHT, blend_binary
from pitchprob.betting.staking import expected_value, kelly_fraction
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.dataset import load_matches_frame
from pitchprob.data.orm import League, Match, Season
from pitchprob.markets import (
    asian_handicap,
    btts,
    double_chance,
    draw_no_bet,
    expected_goals,
    match_odds,
    totals,
)
from pitchprob.models.base import OutcomeProbabilities
from pitchprob.models.counts import NegBinCountsModel
from pitchprob.models.dixon_coles import (
    DEFAULT_HALF_LIFE_DAYS as _DEFAULT_HALF_LIFE_DAYS,
)
from pitchprob.models.ensemble import EnsembleModel

DISCLAIMER = (
    "Probabilities are model estimates with uncertainty; they are routinely "
    "worse than closing-line odds and nothing here guarantees profit."
)

_TOTALS_LINES = ("0.5", "1.5", "2.5", "3.5", "4.5")
_AH_LINES = ("-2", "-1.5", "-1", "-0.5", "-0.25", "0", "0.25", "0.5", "1", "1.5", "2")
_TOP_SCORES = 5
_CORNER_LINES = ("7.5", "8.5", "9.5", "10.5", "11.5", "12.5")
_CARD_LINES = ("1.5", "2.5", "3.5", "4.5", "5.5", "6.5")

COUNTS_CAVEAT = (
    "Corners/cards estimates come from team-rate models without referee or "
    "lineup information; treat them as wider-uncertainty than goals markets."
)

#: Re-exported for the CLI; the number itself lives with the model
#: (ADR 0010 unified the previously divergent defaults).
DEFAULT_HALF_LIFE_DAYS = _DEFAULT_HALF_LIFE_DAYS

#: Fitted ensembles keyed by (league, half_life, n_matches, max_match_id).
@dataclass(frozen=True, slots=True)
class LeagueModels:
    """Everything fitted for one league at one data version."""

    ensemble: EnsembleModel
    corners: NegBinCountsModel | None
    cards: NegBinCountsModel | None


_MODEL_CACHE: dict[tuple[str, float | None, int, int], LeagueModels] = {}


def _data_version(session: Session, league_code: str) -> tuple[int, int]:
    """(match count, max match id) — changes iff the league's data changes."""
    row = session.execute(
        select(func.count(Match.id), func.coalesce(func.max(Match.id), 0))
        .join(Season, Match.season_id == Season.id)
        .join(League, Season.league_id == League.id)
        .where(League.code == league_code)
    ).one()
    return int(row[0]), int(row[1])


def _fit_counts(
    frame: pd.DataFrame, home_column: str, away_column: str
) -> NegBinCountsModel | None:
    """Fit a counts model, or return None when the stat isn't recorded."""
    try:
        return NegBinCountsModel(
            home_column=home_column, away_column=away_column
        ).fit(frame)
    except ValueError:
        return None


def _cached_models(
    session: Session, league_code: str, half_life_days: float | None
) -> LeagueModels:
    version = _data_version(session, league_code)
    key = (league_code, half_life_days, *version)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    frame = load_matches_frame(session, league_code=league_code, include_stats=True)
    if frame.empty:
        raise ValueError(f"no ingested matches for league {league_code!r}")
    # Scale the stacking holdout to the available history so small leagues
    # (and test fixtures) still fit; 380 ≈ one EPL season is the ceiling.
    holdout = min(380, max(10, len(frame) // 5))
    min_train = max(1, min(200, len(frame) - holdout))
    ensemble = EnsembleModel(
        half_life_days=half_life_days, holdout=holdout, min_train=min_train
    ).fit(frame)

    frame = frame.assign(
        cards_home=frame["yellows_home"] + frame["reds_home"],
        cards_away=frame["yellows_away"] + frame["reds_away"],
    )
    bundle = LeagueModels(
        ensemble=ensemble,
        corners=_fit_counts(frame, "corners_home", "corners_away"),
        cards=_fit_counts(frame, "cards_home", "cards_away"),
    )

    # Retain only the newest version per league to bound memory.
    for stale in [k for k in _MODEL_CACHE if k[0] == league_code]:
        del _MODEL_CACHE[stale]
    _MODEL_CACHE[key] = bundle
    return bundle


def _cached_ensemble(
    session: Session, league_code: str, half_life_days: float | None
) -> EnsembleModel:
    return _cached_models(session, league_code, half_life_days).ensemble


def _probs_dict(p: OutcomeProbabilities) -> dict[str, float]:
    return {"home": p.home, "draw": p.draw, "away": p.away}


def _counts_section(
    model: NegBinCountsModel | None, home: str, away: str, lines: tuple[str, ...]
) -> dict[str, Any] | None:
    if model is None:
        return None
    mu_h, mu_a = model.expected_counts(home, away)
    matrix = model.counts_matrix(home, away)
    return {
        "expected": {"home": mu_h, "away": mu_a, "total": mu_h + mu_a},
        "totals": {
            line: {
                "over": totals(matrix, Decimal(line)).over,
                "under": totals(matrix, Decimal(line)).under,
            }
            for line in lines
        },
    }


def value_analysis_1x2(
    model_probabilities: Mapping[str, float],
    offered_prices: tuple[float, float, float],
    *,
    blend_weight: float = DEFAULT_BLEND_WEIGHT,
    kelly_scale: float = 0.25,
) -> dict[str, dict[str, float]]:
    """Per-selection EV at the offered 1X2 prices, on the betting path.

    The offered prices are Shin de-margined into a market distribution; each
    selection's model probability is pooled with its market counterpart
    (binary blend, selection vs complement, ADR 0006) and EV plus fractional
    Kelly come from the pooled ``p_bet``. ``fair_price`` is the break-even
    price ``1 / p_bet``. ``model_probabilities`` must be the betting model's
    (Dixon-Coles) — never raw ensemble EV, the trap quantified at -11.2% ROI.
    """
    shin = remove_overround_shin(offered_prices)
    analysis: dict[str, dict[str, float]] = {}
    for selection, price, p_market in zip(
        ("home", "draw", "away"), offered_prices, shin, strict=True
    ):
        p_model = float(model_probabilities[selection])
        p_bet = blend_binary(p_model, p_market, weight=blend_weight)
        analysis[selection] = {
            "offered_price": price,
            "model_probability": p_model,
            "market_probability": p_market,
            "p_bet": p_bet,
            "fair_price": 1.0 / p_bet,
            "expected_value": expected_value(probability=p_bet, price=price),
            "kelly_fraction": kelly_fraction(
                probability=p_bet, price=price, fraction=kelly_scale
            ),
        }
    return analysis


def build_market_book(
    session: Session,
    league_code: str,
    home_team: str,
    away_team: str,
    *,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    offered_1x2: tuple[float, float, float] | None = None,
) -> dict[str, Any]:
    frame = load_matches_frame(session, league_code=league_code)
    if frame.empty:
        raise ValueError(f"no ingested matches for league {league_code!r}")
    known_teams = set(frame["home_team"]) | set(frame["away_team"])
    for team in (home_team, away_team):
        if team not in known_teams:
            raise UnknownTeamError(
                f"{team!r} has no matches in league {league_code!r}; "
                "use the canonical team name"
            )

    bundle = _cached_models(session, league_code, half_life_days)
    ensemble = bundle.ensemble
    components = ensemble.components_
    dc = components["dixon_coles"]

    matrix = dc.score_matrix(home_team, away_team)
    mo = match_odds(matrix)
    dc_book = double_chance(matrix)
    dnb = draw_no_bet(matrix)
    both = btts(matrix)
    goals = expected_goals(matrix)

    ensemble_p = ensemble.match_probabilities(home_team, away_team)
    one_x_two: dict[str, dict[str, float]] = {
        "ensemble": _probs_dict(ensemble_p),
        "dixon_coles": _probs_dict(
            OutcomeProbabilities(home=mo.home, draw=mo.draw, away=mo.away)
        ),
        "elo": _probs_dict(components["elo"].match_probabilities(home_team, away_team)),
        "gbm": _probs_dict(components["gbm"].match_probabilities(home_team, away_team)),
    }

    top_scores = sorted(
        (
            (h, a, float(matrix[h, a]))
            for h in range(matrix.shape[0])
            for a in range(matrix.shape[1])
        ),
        key=lambda item: item[2],
        reverse=True,
    )[:_TOP_SCORES]

    ah_book: dict[str, dict[str, float]] = {}
    for line_text in _AH_LINES:
        line = Decimal(line_text)
        home_side = asian_handicap(matrix, line, "home")
        away_side = asian_handicap(matrix, line, "away")
        ah_book[line_text] = {
            "home": home_side.full_win + home_side.half_win,
            "push": home_side.push,
            "away": away_side.full_win + away_side.half_win,
        }

    book: dict[str, Any] = {
        "league": league_code,
        "home_team": home_team,
        "away_team": away_team,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "trained_on_matches": len(frame),
        "train_max_date": str(frame["date"].max()),
        "ensemble_weights": ensemble.weights_,
        "expected_goals": {"home": goals.home, "away": goals.away},
        "markets": {
            "1x2": one_x_two,
            "double_chance": {
                "home_or_draw": dc_book.home_or_draw,
                "home_or_away": dc_book.home_or_away,
                "draw_or_away": dc_book.draw_or_away,
            },
            "draw_no_bet": {"home": dnb.home, "away": dnb.away},
            "totals": {
                line: {
                    "over": totals(matrix, Decimal(line)).over,
                    "under": totals(matrix, Decimal(line)).under,
                }
                for line in _TOTALS_LINES
            },
            "btts": {"yes": both.yes, "no": both.no},
            "asian_handicap": ah_book,
            "correct_score_top": [
                {"score": f"{h}-{a}", "probability": p} for h, a, p in top_scores
            ],
        },
        "disclaimer": DISCLAIMER,
    }

    counts_markets: dict[str, Any] = {"caveat": COUNTS_CAVEAT}
    corners = _counts_section(bundle.corners, home_team, away_team, _CORNER_LINES)
    if corners is not None:
        counts_markets["corners"] = corners
    cards = _counts_section(bundle.cards, home_team, away_team, _CARD_LINES)
    if cards is not None:
        counts_markets["cards"] = cards
    book["counts_markets"] = counts_markets

    if offered_1x2 is not None:
        book["value_analysis"] = value_analysis_1x2(
            one_x_two["dixon_coles"], offered_1x2
        )

    return book
