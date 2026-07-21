"""Migration/ORM parity tests that run on SQLite — no Postgres needed.

The Alembic chain and the ORM metadata are two declarations of the same
schema; they drift silently unless something diffs them. These tests run
the full migration chain against a throwaway SQLite file and assert the
structures the code depends on actually exist after ``upgrade head``.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from pitchprob.data.orm import OddsQuote, Pick

ROOT = Path(__file__).resolve().parents[2]

ODDS_COMPOSITE_INDEX = "ix_odds_bookmaker_market_is_closing"


@pytest.fixture()
def migrated_sqlite_engine(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_orm_declares_the_composite_odds_index() -> None:
    """Audit finding A7: the harness filters odds on exactly this triple."""
    index = next(
        (i for i in OddsQuote.__table__.indexes if i.name == ODDS_COMPOSITE_INDEX),
        None,
    )
    assert index is not None
    assert [c.name for c in index.columns] == ["bookmaker", "market", "is_closing"]


def test_migration_chain_creates_the_composite_odds_index(
    migrated_sqlite_engine,
) -> None:
    indexes = inspect(migrated_sqlite_engine).get_indexes("odds")
    by_name = {i["name"]: i for i in indexes}
    assert ODDS_COMPOSITE_INDEX in by_name
    assert by_name[ODDS_COMPOSITE_INDEX]["column_names"] == [
        "bookmaker",
        "market",
        "is_closing",
    ]


def test_migration_chain_creates_picks_matching_the_orm(
    migrated_sqlite_engine,
) -> None:
    """The picks ledger (Phase 5 part 3): migration and ORM must agree on
    every column — settlement money lives here, drift is not acceptable."""
    inspector = inspect(migrated_sqlite_engine)
    assert "picks" in inspector.get_table_names()
    migrated = {c["name"] for c in inspector.get_columns("picks")}
    declared = {c.name for c in Pick.__table__.columns}
    assert migrated == declared
    index_columns = {
        tuple(i["column_names"]) for i in inspector.get_indexes("picks")
    }
    assert ("event_id",) in index_columns
    assert ("kickoff_utc",) in index_columns
