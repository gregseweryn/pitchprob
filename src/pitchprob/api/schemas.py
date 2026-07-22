"""API request/response schemas.

Caveats are response *fields*, never prose the client is trusted to add:
ADR 0004 makes disclaimers part of the payload, so a surface that renders
the numbers cannot render them bare.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class LeagueOut(BaseModel):
    code: str
    name: str
    country: str
    match_count: int


class MatchOut(BaseModel):
    date: date
    home_team: str
    away_team: str
    ft_home: int
    ft_away: int


class PredictionRequest(BaseModel):
    league: str = Field(examples=["E0"])
    home_team: str = Field(examples=["Arsenal"])
    away_team: str = Field(examples=["Chelsea"])
    offered_1x2: tuple[float, float, float] | None = Field(
        default=None, description="Offered decimal prices (home, draw, away) for EV analysis"
    )


class BacktestOut(BaseModel):
    id: int
    created_at: datetime
    config: dict[str, Any]
    metrics: dict[str, Any]


class FixtureIn(BaseModel):
    league: str = Field(examples=["E0"])
    home_team: str
    away_team: str


class CouponRequest(BaseModel):
    tier: Literal["safe", "balanced", "value", "high_risk"]
    fixtures: list[FixtureIn] = Field(min_length=1, max_length=16)
    max_legs: int = Field(default=4, ge=1, le=6)
    top_n: int = Field(default=5, ge=1, le=10)


class LegOut(BaseModel):
    match_label: str
    market: str
    selection: str
    probability: float
    reasons: list[str]


class CouponOut(BaseModel):
    joint_probability: float
    legs: list[LegOut]


class CouponsResponse(BaseModel):
    tier: str
    band: tuple[float, float]
    caveat: str
    coupons: list[CouponOut]


# --- The PL scanner (ADR 0013) -------------------------------------------


class TapeEventOut(BaseModel):
    """An upcoming fixture the odds tape can anchor."""

    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime


class QuoteIn(BaseModel):
    """One price the operator sees at a Polish book, with its promo."""

    bookmaker: str = Field(min_length=1, max_length=32)
    price: Decimal = Field(gt=1)
    tax_free: bool = False
    boosted_price: Decimal | None = Field(default=None, gt=1)
    payout_haircut: float = Field(default=1.0, gt=0.0, le=1.0)
    source: Literal["operator", "feed"] = "operator"


class ScanRequest(BaseModel):
    market: Literal["1x2", "ou", "ah", "corners_ou", "corners_ah"]
    selection: str = Field(min_length=1, max_length=16)
    quotes: list[QuoteIn] = Field(min_length=1, max_length=12)
    line: Decimal | None = None
    event_id: str | None = None
    query: str | None = None
    model_probability: float | None = Field(default=None, gt=0.0, lt=1.0)
    min_edge: float = Field(default=0.02, ge=0.0, le=1.0)


class AnchorOut(BaseModel):
    bookmaker: str
    price: Decimal
    fair_probability: float
    observed_at: datetime
    age_hours: float
    line: Decimal | None


class VerdictOut(BaseModel):
    bookmaker: str
    price_quoted: Decimal
    price_effective: Decimal
    tax_free: bool
    #: The payout regime the router applied: 1.0 tax-free inside the promo
    #: limit, 0.94 past it, 0.88 bare tax (ADR 0017).
    tax_multiplier: Decimal
    boosted: bool
    #: EV the promotion itself contributes over the bare taxed quote.
    promo_value: float | None
    edge: float | None
    edge_model: float | None
    verdict: Literal["PLAY", "NO BET", "STALE", "NO ANCHOR", "UNVERIFIED"]
    source: Literal["operator", "feed"]


class ScanResponse(BaseModel):
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    market: str
    selection: str
    line: Decimal | None
    anchor: AnchorOut | None
    model_probability: float | None
    verdicts: list[VerdictOut]
    caveats: list[str]


# --- The forward pick ledger (ADR 0013) + risk layer (ADR 0015) ----------


class PickOut(BaseModel):
    id: int
    kickoff_utc: datetime
    home_team: str
    away_team: str
    market: str
    selection: str
    line: Decimal | None
    bookmaker: str
    stake_pln: Decimal
    price_quoted: Decimal
    price_effective: Decimal
    tax_free: bool
    price_sharp: Decimal | None
    gross_return_pln: Decimal | None
    settled_at: datetime | None
    closing_observed_at: datetime | None
    clv_exec: float | None
    clv_sharp: float | None
    #: clv_exec - clv_sharp: the venue/promo component, paired per bet.
    clv_shopping: float | None
    risk_override: bool
    risk_note: str | None


class PickRequest(BaseModel):
    """One executed bet, as the dashboard submits it.

    Deliberately mirrors `pitchprob pick log`: the browser is a second front
    door to the same ledger, never a looser one. The risk layer runs
    server-side, so `override_risk` here costs exactly what it costs at the
    command line — a permanent mark on the pick.
    """

    home_team: str = Field(min_length=1, max_length=64)
    away_team: str = Field(min_length=1, max_length=64)
    kickoff_utc: datetime
    market: Literal["1x2", "ou", "ah", "corners_ou", "corners_ah"]
    selection: str = Field(min_length=1, max_length=16)
    bookmaker: str = Field(min_length=1, max_length=32)
    stake_pln: Decimal = Field(gt=0)
    price_quoted: Decimal = Field(gt=1)
    line: Decimal | None = None
    tax_free: bool = False
    event_id: str | None = None
    notes: str | None = Field(default=None, max_length=256)
    override_risk: bool = False


class LedgerResponse(BaseModel):
    picks: list[PickOut]
    summary: dict[str, Any]
    weekly: dict[str, Any]
    caveats: list[str]
