"""API request/response schemas."""

from datetime import date, datetime
from typing import Any

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
