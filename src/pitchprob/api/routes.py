"""API routes (read model + on-demand predictions)."""

from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from pitchprob.api.schemas import (
    BacktestOut,
    CouponOut,
    CouponRequest,
    CouponsResponse,
    LeagueOut,
    LegOut,
    MatchOut,
    PredictionRequest,
)
from pitchprob.core.db import get_session_factory
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Backtest, League, Match, Season, Team, TeamAlias
from pitchprob.services.coupons import INDEPENDENCE_CAVEAT, TIERS, generate_coupons
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


@router.get("/leagues/{code}/teams", response_model=list[str])
def list_teams(code: str, db: Session = Depends(get_db)) -> list[str]:
    league = db.execute(select(League).where(League.code == code)).scalar_one_or_none()
    if league is None:
        raise HTTPException(status_code=404, detail=f"unknown league {code!r}")
    names = db.execute(
        select(Team.canonical_name)
        .distinct()
        .join(TeamAlias, TeamAlias.team_id == Team.id, isouter=True)
        .join(Match, (Match.home_team_id == Team.id) | (Match.away_team_id == Team.id))
        .join(Season, Match.season_id == Season.id)
        .where(Season.league_id == league.id)
        .order_by(Team.canonical_name)
    ).scalars()
    return list(dict.fromkeys(names))


@router.post("/coupons", response_model=CouponsResponse)
def create_coupons(request: CouponRequest, db: Session = Depends(get_db)) -> CouponsResponse:
    books = []
    for fixture in request.fixtures:
        try:
            books.append(
                build_market_book(db, fixture.league, fixture.home_team, fixture.away_team)
            )
        except (UnknownTeamError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    coupons = generate_coupons(
        books, tier=request.tier, max_legs=request.max_legs, top_n=request.top_n
    )
    return CouponsResponse(
        tier=request.tier,
        band=TIERS[request.tier],
        caveat=INDEPENDENCE_CAVEAT,
        coupons=[
            CouponOut(
                joint_probability=coupon.joint_probability,
                legs=[
                    LegOut(
                        match_label=leg.match_label,
                        market=leg.market,
                        selection=leg.selection,
                        probability=leg.probability,
                        reasons=list(leg.reasons),
                    )
                    for leg in coupon.legs
                ],
            )
            for coupon in coupons
        ],
    )


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
