"""Immutable domain records produced by source adapters.

These are deliberately decoupled from the ORM: adapters know nothing about
persistence, repositories know nothing about CSV columns.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class OddsQuoteRecord:
    bookmaker: str
    market: str  # "1x2" | "ou" | "ah"
    selection: str  # home/draw/away/over/under
    price: Decimal
    line: Decimal = Decimal(0)
    is_closing: bool = False


@dataclass(frozen=True, slots=True)
class MatchRecord:
    match_date: date
    kickoff_utc: datetime | None
    home_team: str
    away_team: str
    ft_home: int
    ft_away: int
    ht_home: int | None = None
    ht_away: int | None = None
    shots_home: int | None = None
    shots_away: int | None = None
    shots_on_target_home: int | None = None
    shots_on_target_away: int | None = None
    corners_home: int | None = None
    corners_away: int | None = None
    yellows_home: int | None = None
    yellows_away: int | None = None
    reds_home: int | None = None
    reds_away: int | None = None
    fouls_home: int | None = None
    fouls_away: int | None = None
    odds: tuple[OddsQuoteRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class QuarantinedRow:
    row_number: int  # 1-based data row number (header excluded)
    reason: str
    raw: str


@dataclass(frozen=True, slots=True)
class ParseResult:
    records: tuple[MatchRecord, ...] = ()
    quarantined: tuple[QuarantinedRow, ...] = field(default_factory=tuple)
