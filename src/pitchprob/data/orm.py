"""SQLAlchemy ORM models — the relational system of record.

Design notes (see ADR 0003):

- Match statistics are typed columns, not JSON: they are queried analytically.
- ``odds`` and ``predictions`` are long/narrow: adding a bookmaker, market or
  model is a data change, not a migration.
- ``line`` is NOT NULL with sentinel ``0`` for line-less markets (1X2, BTTS…)
  so that uniqueness constraints behave identically across backends (NULLs are
  "distinct" in both Postgres and SQLite unique constraints).
- ``match_date`` is always present; ``kickoff_utc`` is nullable because
  pre-2019 source files carry no kickoff time.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True)  # e.g. E0, SP1
    name: Mapped[str] = mapped_column(String(64))
    country: Mapped[str] = mapped_column(String(32))

    seasons: Mapped[list["Season"]] = relationship(back_populates="league")


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (UniqueConstraint("league_id", "start_year"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"))
    label: Mapped[str] = mapped_column(String(16))  # e.g. "2023/24"
    start_year: Mapped[int]

    league: Mapped[League] = relationship(back_populates="seasons")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(64), unique=True)
    country: Mapped[str] = mapped_column(String(32))

    aliases: Mapped[list["TeamAlias"]] = relationship(back_populates="team")


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    __table_args__ = (UniqueConstraint("source", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    source: Mapped[str] = mapped_column(String(32))  # e.g. "football-data"
    alias: Mapped[str] = mapped_column(String(64))

    team: Mapped[Team] = relationship(back_populates="aliases")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (
        UniqueConstraint("season_id", "match_date", "home_team_id", "away_team_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    match_date: Mapped[date] = mapped_column(Date)
    kickoff_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))

    ft_home: Mapped[int]
    ft_away: Mapped[int]
    ht_home: Mapped[int | None]
    ht_away: Mapped[int | None]

    shots_home: Mapped[int | None]
    shots_away: Mapped[int | None]
    shots_on_target_home: Mapped[int | None]
    shots_on_target_away: Mapped[int | None]
    corners_home: Mapped[int | None]
    corners_away: Mapped[int | None]
    yellows_home: Mapped[int | None]
    yellows_away: Mapped[int | None]
    reds_home: Mapped[int | None]
    reds_away: Mapped[int | None]
    fouls_home: Mapped[int | None]
    fouls_away: Mapped[int | None]

    season: Mapped[Season] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])

    odds: Mapped[list["OddsQuote"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class OddsQuote(Base):
    __tablename__ = "odds"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", "market", "selection", "line", "is_closing"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    bookmaker: Mapped[str] = mapped_column(String(32))  # pinnacle, bet365, market_max, market_avg
    market: Mapped[str] = mapped_column(String(16))  # 1x2, ou, ah
    line: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal(0))
    selection: Mapped[str] = mapped_column(String(16))  # home/draw/away/over/under
    price: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)

    match: Mapped[Match] = relationship(back_populates="odds")


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    train_cutoff: Mapped[date | None] = mapped_column(Date)

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="model_run")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("model_run_id", "match_id", "market", "selection", "line"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(ForeignKey("model_runs.id"))
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    market: Mapped[str] = mapped_column(String(16))
    selection: Mapped[str] = mapped_column(String(16))
    line: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal(0))
    probability: Mapped[float]

    model_run: Mapped[ModelRun] = relationship(back_populates="predictions")
    match: Mapped[Match] = relationship()


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    metrics: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
