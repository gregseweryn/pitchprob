"""API request/response schemas."""

from datetime import date, datetime
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
