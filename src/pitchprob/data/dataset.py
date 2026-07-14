"""DB → DataFrame read models for model training and evaluation.

Long-format persistence (ADR 0003) is pivoted here into the wide frames that
models and backtests consume. Team names are canonical.
"""

from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from pitchprob.data.orm import League, Match, OddsQuote, Season, Team

MATCH_COLUMNS = ["date", "home_team", "away_team", "ft_home", "ft_away"]
STATS_COLUMNS = [
    "league",
    "shots_home",
    "shots_away",
    "shots_on_target_home",
    "shots_on_target_away",
    "corners_home",
    "corners_away",
    "yellows_home",
    "yellows_away",
    "reds_home",
    "reds_away",
    "xg_home",
    "xg_away",
]
ODDS_COLUMNS = ["date", "home_team", "away_team", "price_home", "price_draw", "price_away"]

_NUMERIC_STATS = [c for c in STATS_COLUMNS if c != "league"]


def load_matches_frame(
    session: Session,
    *,
    league_code: str | None = None,
    start: date | None = None,
    end: date | None = None,
    include_stats: bool = False,
) -> pd.DataFrame:
    """Completed matches, one row each, sorted by date.

    With ``include_stats`` the frame additionally carries the league code and
    the match statistics the feature builder consumes (shots, shots on target,
    corners, xG) as floats with NaN for missing values.
    """
    home, away = aliased(Team), aliased(Team)
    columns: list[Any] = [
        Match.match_date.label("date"),
        home.canonical_name.label("home_team"),
        away.canonical_name.label("away_team"),
        Match.ft_home,
        Match.ft_away,
    ]
    if include_stats:
        columns += [
            League.code.label("league"),
            Match.shots_home,
            Match.shots_away,
            Match.shots_on_target_home,
            Match.shots_on_target_away,
            Match.corners_home,
            Match.corners_away,
            Match.yellows_home,
            Match.yellows_away,
            Match.reds_home,
            Match.reds_away,
            Match.xg_home,
            Match.xg_away,
        ]
    stmt = (
        select(*columns)
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .join(Season, Match.season_id == Season.id)
        .join(League, Season.league_id == League.id)
        .order_by(Match.match_date, Match.id)
    )
    if league_code is not None:
        stmt = stmt.where(League.code == league_code)
    if start is not None:
        stmt = stmt.where(Match.match_date >= start)
    if end is not None:
        stmt = stmt.where(Match.match_date < end)
    rows = session.execute(stmt).all()
    frame_columns = MATCH_COLUMNS + (STATS_COLUMNS if include_stats else [])
    frame = pd.DataFrame(rows, columns=frame_columns)
    if include_stats:
        for column in _NUMERIC_STATS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def load_closing_odds_frame(
    session: Session,
    *,
    league_code: str | None = None,
    bookmaker: str = "pinnacle",
) -> pd.DataFrame:
    """Closing 1X2 prices pivoted to one row per match."""
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(
            Match.id.label("match_id"),
            Match.match_date.label("date"),
            home.canonical_name.label("home_team"),
            away.canonical_name.label("away_team"),
            OddsQuote.selection,
            OddsQuote.price,
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .join(Season, Match.season_id == Season.id)
        .join(League, Season.league_id == League.id)
        .join(OddsQuote, OddsQuote.match_id == Match.id)
        .where(
            OddsQuote.bookmaker == bookmaker,
            OddsQuote.market == "1x2",
            OddsQuote.is_closing.is_(True),
        )
    )
    if league_code is not None:
        stmt = stmt.where(League.code == league_code)
    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=ODDS_COLUMNS)

    long = pd.DataFrame(
        rows, columns=["match_id", "date", "home_team", "away_team", "selection", "price"]
    )
    long["price"] = long["price"].astype(float)
    wide = long.pivot_table(
        index=["match_id", "date", "home_team", "away_team"],
        columns="selection",
        values="price",
        aggfunc="first",
    ).reset_index()
    wide = wide.rename(
        columns={"home": "price_home", "draw": "price_draw", "away": "price_away"}
    )
    wide = wide.sort_values("date", kind="stable").reset_index(drop=True)
    return wide[ODDS_COLUMNS]
