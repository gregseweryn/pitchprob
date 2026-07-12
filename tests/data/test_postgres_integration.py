"""Integration tests against a real Postgres (make db-up).

Uses a dedicated ``pitchprob_test`` database inside the compose Postgres so
the dev database is never touched. Skips cleanly when Postgres is down.
"""

import os
from datetime import date

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pitchprob.data.repository import (
    LeagueRepository,
    MatchRepository,
    SeasonRepository,
    TeamRepository,
)

pytestmark = pytest.mark.integration

TEST_DB = "pitchprob_test"


@pytest.fixture(scope="session")
def pg_test_url() -> str:
    base_url = os.environ["PITCHPROB_DATABASE_URL"]
    root, _, _ = base_url.rpartition("/")
    admin_url = f"{root}/postgres"
    test_url = f"{root}/{TEST_DB}"
    try:
        engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except OperationalError:
        pytest.skip("Postgres is not reachable (run: make db-up)")
    return test_url


@pytest.fixture(scope="session")
def migrated_engine(pg_test_url: str):
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", pg_test_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(pg_test_url)
    yield engine
    engine.dispose()


def test_migration_creates_all_tables(migrated_engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names())
    expected = {
        "leagues",
        "seasons",
        "teams",
        "team_aliases",
        "matches",
        "odds",
        "model_runs",
        "predictions",
        "backtests",
    }
    assert expected <= tables


def test_upsert_on_postgres(migrated_engine) -> None:
    with Session(migrated_engine) as session:
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
        _, created1 = MatchRepository.upsert(
            session, season_id=season.id, match_date=date(2023, 9, 2),
            home_team_id=home.id, away_team_id=away.id, ft_home=3, ft_away=1,
        )
        _, created2 = MatchRepository.upsert(
            session, season_id=season.id, match_date=date(2023, 9, 2),
            home_team_id=home.id, away_team_id=away.id, ft_home=3, ft_away=1,
        )
        session.rollback()
    assert created1 is True
    assert created2 is False
