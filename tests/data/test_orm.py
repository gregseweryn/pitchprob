"""Schema-level tests for the ORM models (run against in-memory SQLite).

These verify structural invariants — constraints, defaults, round-trips — that
must hold on any backend. Postgres-specific behavior is covered by the marked
integration tests.
"""

from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pitchprob.data.orm import (
    Base,
    League,
    Match,
    OddsQuote,
    Season,
    Team,
    TeamAlias,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_league_season_teams(s: Session) -> tuple[Season, Team, Team]:
    league = League(code="E0", name="Premier League", country="England")
    season = Season(league=league, label="2023/24", start_year=2023)
    home = Team(canonical_name="Arsenal", country="England")
    away = Team(canonical_name="Chelsea", country="England")
    s.add_all([league, season, home, away])
    s.flush()
    return season, home, away


def test_match_round_trip(session: Session) -> None:
    season, home, away = _make_league_season_teams(session)
    match = Match(
        season_id=season.id,
        match_date=date(2023, 9, 2),
        home_team_id=home.id,
        away_team_id=away.id,
        ft_home=3,
        ft_away=1,
        ht_home=1,
        ht_away=0,
        shots_home=15,
        shots_away=8,
    )
    session.add(match)
    session.flush()

    loaded = session.execute(select(Match)).scalar_one()
    assert loaded.ft_home == 3
    assert loaded.ht_away == 0
    assert loaded.shots_home == 15
    assert loaded.kickoff_utc is None  # older seasons have no kickoff time


def test_match_natural_key_unique(session: Session) -> None:
    season, home, away = _make_league_season_teams(session)
    kwargs = {
        "season_id": season.id,
        "match_date": date(2023, 9, 2),
        "home_team_id": home.id,
        "away_team_id": away.id,
        "ft_home": 1,
        "ft_away": 1,
    }
    session.add(Match(**kwargs))
    session.flush()
    session.add(Match(**kwargs))
    with pytest.raises(IntegrityError):
        session.flush()


def test_team_alias_unique_per_source(session: Session) -> None:
    team = Team(canonical_name="Manchester United", country="England")
    session.add(team)
    session.flush()
    session.add(TeamAlias(team_id=team.id, source="football-data", alias="Man United"))
    session.flush()
    session.add(TeamAlias(team_id=team.id, source="football-data", alias="Man United"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_odds_quote_defaults_and_uniqueness(session: Session) -> None:
    season, home, away = _make_league_season_teams(session)
    match = Match(
        season_id=season.id,
        match_date=date(2023, 9, 2),
        home_team_id=home.id,
        away_team_id=away.id,
        ft_home=0,
        ft_away=0,
    )
    session.add(match)
    session.flush()

    quote = OddsQuote(
        match_id=match.id,
        bookmaker="pinnacle",
        market="1x2",
        selection="home",
        price=2.05,
        is_closing=True,
    )
    session.add(quote)
    session.flush()
    assert float(quote.line) == 0.0  # line-less markets store sentinel 0

    dup = OddsQuote(
        match_id=match.id,
        bookmaker="pinnacle",
        market="1x2",
        selection="home",
        price=2.10,
        is_closing=True,
    )
    session.add(dup)
    with pytest.raises(IntegrityError):
        session.flush()
