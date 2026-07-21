"""API routes (read model + on-demand predictions)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from pitchprob.api.schemas import (
    AnchorOut,
    BacktestOut,
    CouponOut,
    CouponRequest,
    CouponsResponse,
    LeagueOut,
    LedgerResponse,
    LegOut,
    MatchOut,
    PickOut,
    PredictionRequest,
    ScanRequest,
    ScanResponse,
    TapeEventOut,
    VerdictOut,
)
from pitchprob.betting.effective import PromoTerms, effective_price
from pitchprob.core.db import get_session_factory
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Backtest, League, Match, Pick, Season, Team, TeamAlias
from pitchprob.services.coupons import INDEPENDENCE_CAVEAT, TIERS, generate_coupons
from pitchprob.services.ledger import LEDGER_CAVEATS, ledger_summary
from pitchprob.services.prediction import build_market_book
from pitchprob.services.risk_report import weekly_report
from pitchprob.services.scanner import SCANNER_CAVEATS, OperatorQuote, scan
from pitchprob.services.tape import find_events

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


@router.get("/scanner/events", response_model=list[TapeEventOut])
def list_tape_events(
    query: str = Query(default="", description="Team substring; empty lists all"),
    db: Session = Depends(get_db),
) -> list[TapeEventOut]:
    """Upcoming fixtures the tape can anchor — the scanner's fixture picker."""
    events = find_events(db, query, at=datetime.now(tz=UTC))
    return [
        TapeEventOut(
            event_id=event.event_id,
            home_team=event.home_team,
            away_team=event.away_team,
            commence_time=event.commence_time,
        )
        for event in events
    ]


@router.post("/scanner/scan", response_model=ScanResponse)
def scan_quotes(request: ScanRequest, db: Session = Depends(get_db)) -> ScanResponse:
    """Verdict operator quotes against the tape's Pinnacle fair."""
    now = datetime.now(tz=UTC)
    quotes = [
        OperatorQuote(
            bookmaker=quote.bookmaker,
            price=quote.price,
            promo=(
                PromoTerms(
                    tax_free=quote.tax_free,
                    boosted_price=quote.boosted_price,
                    payout_haircut=quote.payout_haircut,
                )
                if quote.tax_free
                or quote.boosted_price is not None
                or quote.payout_haircut != 1.0
                else None
            ),
            source=quote.source,
        )
        for quote in request.quotes
    ]
    try:
        result = scan(
            db,
            market=request.market,
            selection=request.selection,
            quotes=quotes,
            line=request.line,
            query=request.query,
            event_id=request.event_id,
            model_probability=request.model_probability,
            now=now,
            min_edge=request.min_edge,
        )
    except ValueError as exc:
        # Unknown fixture, ambiguous query, bad market/selection shape: all
        # of them are the caller's input, not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    anchor = None
    if result.anchor is not None and result.anchor_age is not None:
        anchor = AnchorOut(
            bookmaker=result.anchor.bookmaker,
            price=result.anchor.prices[request.selection],
            fair_probability=result.anchor.probabilities[request.selection],
            observed_at=result.anchor.observed_at,
            age_hours=result.anchor_age.total_seconds() / 3600,
            line=result.anchor.line,
        )
    return ScanResponse(
        event_id=result.event_id,
        home_team=result.home_team,
        away_team=result.away_team,
        commence_time=result.commence_time,
        market=result.market,
        selection=request.selection,
        line=result.line,
        anchor=anchor,
        model_probability=result.model_probability,
        verdicts=[
            VerdictOut(
                bookmaker=verdict.bookmaker,
                price_quoted=verdict.price_quoted,
                price_effective=(
                    verdict.evaluation.price_effective_promo
                    if verdict.evaluation is not None
                    else effective_price(
                        verdict.price_quoted,
                        tax_free=verdict.promo is not None and verdict.promo.tax_free,
                    )
                ),
                tax_free=verdict.promo is not None and verdict.promo.tax_free,
                boosted=verdict.promo is not None
                and verdict.promo.boosted_price is not None,
                promo_value=(
                    verdict.evaluation.promo_value
                    if verdict.evaluation is not None and verdict.promo is not None
                    else None
                ),
                edge=verdict.edge,
                edge_model=verdict.edge_model,
                verdict=verdict.verdict,
                source=verdict.source,
            )
            for verdict in result.verdicts
        ],
        caveats=list(SCANNER_CAVEATS),
    )


@router.get("/ledger", response_model=LedgerResponse)
def read_ledger(db: Session = Depends(get_db)) -> LedgerResponse:
    """Every real-money pick, the CLV decomposition, and the week's report."""
    picks = (
        db.execute(select(Pick).order_by(Pick.placed_at.desc(), Pick.id.desc()))
        .scalars()
        .all()
    )
    return LedgerResponse(
        picks=[
            PickOut(
                id=pick.id,
                kickoff_utc=pick.kickoff_utc,
                home_team=pick.home_team,
                away_team=pick.away_team,
                market=pick.market,
                selection=pick.selection,
                line=pick.line,
                bookmaker=pick.bookmaker,
                stake_pln=pick.stake_pln,
                price_quoted=pick.price_quoted,
                price_effective=pick.price_effective,
                tax_free=pick.tax_free,
                price_sharp=pick.price_sharp,
                gross_return_pln=pick.gross_return_pln,
                settled_at=pick.settled_at,
                closing_observed_at=pick.closing_observed_at,
                clv_exec=pick.clv_exec,
                clv_sharp=pick.clv_sharp,
                clv_shopping=(
                    pick.clv_exec - pick.clv_sharp
                    if pick.clv_exec is not None and pick.clv_sharp is not None
                    else None
                ),
                risk_override=pick.risk_override,
                risk_note=pick.risk_note,
            )
            for pick in picks
        ],
        summary=ledger_summary(db),
        weekly=weekly_report(db, now=datetime.now(tz=UTC)).metrics,
        caveats=list(LEDGER_CAVEATS),
    )
