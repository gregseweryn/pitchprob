"""API routes (read model + on-demand predictions)."""

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from pitchprob.api.schemas import BacktestOut, LeagueOut, MatchOut, PredictionRequest
from pitchprob.core.db import get_session_factory
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Backtest, League, Match, Season, Team
from pitchprob.services.prediction import build_market_book

router = APIRouter(prefix="/v1")


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


@router.get("/leagues", response_model=list[LeagueOut])
def list_leagues(db: Session = Depends(get_db)) -> list[LeagueOut]:
    rows = db.execute(
        select(League, func.count(Match.id))
        .join(Season, Season.league_id == League.id, isouter=True)
        .join(Match, Match.season_id == Season.id, isouter=True)
        .group_by(League.id)
        .order_by(League.code)
    ).all()
    return [
        LeagueOut(
            code=league.code, name=league.name, country=league.country, match_count=count
        )
        for league, count in rows
    ]


@router.get("/matches", response_model=list[MatchOut])
def list_matches(
    league: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[MatchOut]:
    home, away = aliased(Team), aliased(Team)
    stmt = (
        select(
            Match.match_date,
            home.canonical_name,
            away.canonical_name,
            Match.ft_home,
            Match.ft_away,
        )
        .join(home, Match.home_team_id == home.id)
        .join(away, Match.away_team_id == away.id)
        .join(Season, Match.season_id == Season.id)
        .join(League, Season.league_id == League.id)
        .order_by(Match.match_date.desc(), Match.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if league is not None:
        stmt = stmt.where(League.code == league)
    rows = db.execute(stmt).all()
    return [
        MatchOut(
            date=match_date, home_team=h, away_team=a, ft_home=ft_home, ft_away=ft_away
        )
        for match_date, h, a, ft_home, ft_away in rows
    ]


@router.post("/predictions")
def create_prediction(
    request: PredictionRequest, db: Session = Depends(get_db)
) -> dict[str, Any]:
    try:
        return build_market_book(
            db,
            request.league,
            request.home_team,
            request.away_team,
            offered_1x2=request.offered_1x2,
        )
    except (UnknownTeamError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/backtests", response_model=list[BacktestOut])
def list_backtests(db: Session = Depends(get_db)) -> list[BacktestOut]:
    rows = db.execute(select(Backtest).order_by(Backtest.id.desc())).scalars().all()
    return [
        BacktestOut(
            id=row.id, created_at=row.created_at, config=row.config, metrics=row.metrics
        )
        for row in rows
    ]
