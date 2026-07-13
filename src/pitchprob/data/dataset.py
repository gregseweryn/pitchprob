"""DB → DataFrame read models for model training and evaluation.

Long-format persistence (ADR 0003) is pivoted here into the wide frames that
models and backtests consume. Team names are canonical.
"""

from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from pitchprob.data.orm import League, Match, OddsQuote, Season, Team

MATCH_COLUMNS = ["date", "home_team", "away_team", "ft_home", "ft_away"]
ODDS_COLUMNS = ["date", "home_team", "away_team", "price_home", "price_draw", "price_away"]


def load_matches_frame(
    session: Session,
    *,
    league_code: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Completed matches, one row each, sorted by date."""
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(
            Match.match_date.label("date"),
            home.canonical_name.label("home_team"),
            away.canonical_name.label("away_team"),
            Match.ft_home,
            Match.ft_away,
        )
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
    return pd.DataFrame(rows, columns=MATCH_COLUMNS)


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
