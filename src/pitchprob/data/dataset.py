"""DB → DataFrame read models for model training and evaluation.

Long-format persistence (ADR 0003) is pivoted here into the wide frames that
models and backtests consume. Team names are canonical.
"""

from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from pitchprob.data.orm import Injury, League, Match, OddsQuote, Season, Team

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

#: Selections that make one complete book per market; a row missing any of
#: them is an incomplete book and is dropped rather than surfaced as NaN.
_MARKET_SELECTIONS: dict[str, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "ou": ("over", "under"),
    "ah": ("home", "away"),
}

#: Line markets carry the line in the output; 1X2 stores a constant 0 line
#: (schema sentinel, ADR 0003) which would only add noise downstream.
_LINE_MARKETS = ("ou", "ah")


def load_matches_frame(
    session: Session,
    *,
    league_code: str | None = None,
    start: date | None = None,
    end: date | None = None,
    include_stats: bool = False,
) -> pd.DataFrame:
    """Completed matches, one row each, sorted by date.

    With ``include_stats`` the frame additionally carries the league code, the
    match statistics the feature builder consumes (shots, shots on target,
    corners, xG) as floats with NaN for missing values, and listed absence
    counts per side (ADR 0008). Absence semantics are strict: ``0`` means the
    injury corpus covers the date and lists nobody; ``NaN`` means the date is
    outside coverage entirely — the models must never mistake missing data for
    a healthy squad.
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
        _attach_absences(session, frame)
    return frame


def _attach_absences(session: Session, frame: pd.DataFrame) -> None:
    """Add ``absences_home``/``absences_away`` (ADR 0008).

    Semantics matter for honesty: ``0`` means "inside the injury-data
    coverage window and nothing listed"; ``NaN`` means "no coverage for this
    date" (e.g. seasons before 2022). Coverage is the global date window of
    ingested injuries — all leagues are collected over the same seasons, so a
    per-league window would add complexity without changing results. Counts
    use the same ±1-day tolerance as the xG join.
    """
    rows = session.execute(
        select(Team.canonical_name, Injury.match_date, func.count())
        .join(Team, Injury.team_id == Team.id)
        .group_by(Team.canonical_name, Injury.match_date)
    ).all()
    if not rows or frame.empty:
        frame["absences_home"] = float("nan")
        frame["absences_away"] = float("nan")
        return

    counts = {(str(name), when): int(n) for name, when, n in rows}
    dates = [when for _, when, _ in rows]
    window_low, window_high = min(dates), max(dates)
    one_day = timedelta(days=1)

    def count_for(team: str, when: date) -> float:
        if not (window_low - one_day <= when <= window_high + one_day):
            return float("nan")
        return float(
            sum(counts.get((team, when + delta), 0) for delta in (-one_day, timedelta(0), one_day))
        )

    frame["absences_home"] = [
        count_for(team, when) for team, when in zip(frame["home_team"], frame["date"], strict=True)
    ]
    frame["absences_away"] = [
        count_for(team, when) for team, when in zip(frame["away_team"], frame["date"], strict=True)
    ]


def load_odds_snapshot_frame(
    session: Session,
    *,
    league_code: str | None = None,
    bookmaker: str = "pinnacle",
    market: str = "1x2",
    closing: bool = True,
) -> pd.DataFrame:
    """One snapshot of one bookmaker's book for one market, pivoted wide
    (ADR 0010): one row per match — and per line for line markets, since the
    Asian-handicap line can move between the opening and closing snapshots.

    ``line`` stays ``Decimal``: settlement arithmetic must be exact, and every
    stored line is a multiple of 0.25 so the value round-trips losslessly.
    Incomplete books (a selection missing from the snapshot) are dropped —
    they cannot be de-margined and would poison downstream Shin removal.
    """
    if market not in _MARKET_SELECTIONS:
        known = ", ".join(sorted(_MARKET_SELECTIONS))
        raise ValueError(f"unknown market {market!r} (known: {known})")
    selections = _MARKET_SELECTIONS[market]
    price_columns = [
        f"price_{selection}" for selection in selections
    ]
    keep_line = market in _LINE_MARKETS
    output_columns = (
        ["match_id", "date", "home_team", "away_team"]
        + (["line"] if keep_line else [])
        + price_columns
    )

    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(
            Match.id.label("match_id"),
            Match.match_date.label("date"),
            home.canonical_name.label("home_team"),
            away.canonical_name.label("away_team"),
            OddsQuote.line,
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
            OddsQuote.market == market,
            OddsQuote.is_closing.is_(closing),
        )
    )
    if league_code is not None:
        stmt = stmt.where(League.code == league_code)
    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=output_columns)

    long = pd.DataFrame(
        rows,
        columns=["match_id", "date", "home_team", "away_team", "line", "selection", "price"],
    )
    long["price"] = long["price"].astype(float)
    wide = long.pivot_table(
        index=["match_id", "date", "home_team", "away_team", "line"],
        columns="selection",
        values="price",
        aggfunc="first",
    ).reset_index()
    wide = wide.rename(
        columns={selection: f"price_{selection}" for selection in selections}
    )
    for column in price_columns:
        if column not in wide.columns:
            wide[column] = float("nan")
    wide = wide.dropna(subset=price_columns)
    wide = wide.sort_values("date", kind="stable").reset_index(drop=True)
    return wide[output_columns]


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
