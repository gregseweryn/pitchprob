"""Repository behavior tests (in-memory SQLite; portability is deliberate)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Base, Match, OddsQuote
from pitchprob.data.repository import (
    LeagueRepository,
    MatchRepository,
    OddsRepository,
    SeasonRepository,
    TeamRepository,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_league_get_or_create_idempotent(session: Session) -> None:
    a = LeagueRepository.get_or_create(
        session, code="E0", name="Premier League", country="England"
    )
    b = LeagueRepository.get_or_create(
        session, code="E0", name="Premier League", country="England"
    )
    assert a.id == b.id


def test_season_get_or_create_idempotent(session: Session) -> None:
    league = LeagueRepository.get_or_create(
        session, code="E0", name="Premier League", country="England"
    )
    a = SeasonRepository.get_or_create(session, league_id=league.id, start_year=2023)
    b = SeasonRepository.get_or_create(session, league_id=league.id, start_year=2023)
    assert a.id == b.id
    assert a.label == "2023/24"


def test_team_resolution_via_alias(session: Session) -> None:
    team = TeamRepository.create_with_alias(
        session, canonical_name="Manchester United", country="England",
        source="football-data", alias="Man United",
    )
    resolved = TeamRepository.resolve(session, source="football-data", alias="Man United")
    assert resolved.id == team.id


def test_team_resolution_unknown_raises(session: Session) -> None:
    with pytest.raises(UnknownTeamError):
        TeamRepository.resolve(session, source="football-data", alias="Atlantis FC")


def test_team_resolve_or_create_creates_once(session: Session) -> None:
    a = TeamRepository.resolve_or_create(
        session, source="football-data", alias="Wolves", country="England"
    )
    b = TeamRepository.resolve_or_create(
        session, source="football-data", alias="Wolves", country="England"
    )
    assert a.id == b.id


def _fixture_ids(session: Session) -> tuple[int, int, int]:
    league = LeagueRepository.get_or_create(
        session, code="E0", name="Premier League", country="England"
    )
    season = SeasonRepository.get_or_create(session, league_id=league.id, start_year=2023)
    home = TeamRepository.resolve_or_create(
        session, source="football-data", alias="Arsenal", country="England"
    )
    away = TeamRepository.resolve_or_create(
        session, source="football-data", alias="Chelsea", country="England"
    )
    return season.id, home.id, away.id


def test_match_upsert_insert_then_update(session: Session) -> None:
    season_id, home_id, away_id = _fixture_ids(session)

    match, created = MatchRepository.upsert(
        session, season_id=season_id, match_date=date(2023, 9, 2),
        home_team_id=home_id, away_team_id=away_id, ft_home=3, ft_away=1,
    )
    assert created is True

    match2, created2 = MatchRepository.upsert(
        session, season_id=season_id, match_date=date(2023, 9, 2),
        home_team_id=home_id, away_team_id=away_id, ft_home=3, ft_away=1,
        shots_home=15,  # richer data arrives on re-ingest
    )
    assert created2 is False
    assert match2.id == match.id
    assert match2.shots_home == 15
    assert session.execute(select(func.count()).select_from(Match)).scalar_one() == 1


def test_replace_odds_is_idempotent(session: Session) -> None:
    season_id, home_id, away_id = _fixture_ids(session)
    match, _ = MatchRepository.upsert(
        session, season_id=season_id, match_date=date(2023, 9, 2),
        home_team_id=home_id, away_team_id=away_id, ft_home=3, ft_away=1,
    )
    quotes = [
        {"bookmaker": "pinnacle", "market": "1x2", "selection": "home",
         "price": Decimal("2.05"), "is_closing": True},
        {"bookmaker": "pinnacle", "market": "1x2", "selection": "draw",
         "price": Decimal("3.60"), "is_closing": True},
        {"bookmaker": "pinnacle", "market": "ou", "line": Decimal("2.5"),
         "selection": "over", "price": Decimal("1.95"), "is_closing": True},
    ]
    OddsRepository.replace_for_match(session, match_id=match.id, quotes=quotes)
    OddsRepository.replace_for_match(session, match_id=match.id, quotes=quotes)

    n = session.execute(select(func.count()).select_from(OddsQuote)).scalar_one()
    assert n == 3
