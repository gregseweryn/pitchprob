"""odds-api.io adapter — the Polish-book feed (ADR 0014).

The Odds API, which records the main tape, carries no PL-licensed
bookmaker; this one does. Its unauthenticated ``/bookmakers`` catalogue,
read on 2026-07-21, lists **Betclic PL, STS PL, eFortuna PL, Betfan PL,
LVbet PL and Superbet** as active — six of the books the operator can
actually bet with. It carries no Pinnacle, so its role is fixed by what it
has: a source of *followers*, never of the sharp anchor. The anchor stays
Pinnacle from The Odds API tape.

Two things it offers that the main tape cannot:

- **PL prices without manual typing** — subject to validation. Until
  ``quote_checks`` says the feed agrees with what the operator sees on the
  book's own site, feed prices are informational and manual entry remains
  ground truth (audit Etap 5).
- **``/odds/movements``: history, already recorded.** A movement series is
  a timestamped price series, so it maps onto ``odds_ticks`` one row per
  movement and the latency map reads it with no new plumbing. That is what
  makes the line-latency question answerable in days rather than a season.

Ticks carry ``source="odds-api-io"`` and the feed's own bookmaker naming
("Betclic PL", not "betclic"), because the tape records what a source said
and canonicalisation belongs at analysis time.

The free tier allows two selected bookmakers and 100 requests an hour. The
key travels as a query parameter, so the same scrubbing discipline as The
Odds API applies: no URL may ever reach a log or a traceback.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from pitchprob.data.adapters.odds_api import OddsTickRecord, scrub_http_error

__all__ = [
    "SOURCE",
    "EventRef",
    "OddsApiIoClient",
    "observed_markets",
    "parse_movements",
    "parse_odds",
    "scrub_http_error",
]

#: Tape provenance. Analysis filters on this to keep feeds comparable —
#: two sources with different cadences must never be pooled silently.
SOURCE = "odds-api-io"

_BASE_URL = "https://api.odds-api.io/v3"

#: Their market names → ours. Deliberately small: the mapping grows from
#: what `pitchprob oddsio probe` observes, not from what the docs promise.
MARKET_NAMES = {
    "ML": "1x2",
    "Totals": "ou",
    "Asian Handicap": "ah",
}
_MARKET_NAMES_REVERSED = {ours: theirs for theirs, ours in MARKET_NAMES.items()}

#: Which keys in an odds entry are selections, per market.
_SELECTION_KEYS = {
    "1x2": ("home", "draw", "away"),
    "ou": ("over", "under"),
    "ah": ("home", "away"),
}

FetchFn = Callable[[str, dict[str, str]], tuple[Any, dict[str, str]]]


@dataclass(frozen=True, slots=True)
class EventRef:
    """Fixture context. Movements responses carry none of it, so the caller
    supplies it from the events listing — a tick without a kickoff cannot be
    checked for lookahead later."""

    event_id: str
    sport_key: str
    home_team: str
    away_team: str
    commence_time: datetime


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def event_ref(payload: dict[str, Any]) -> EventRef:
    return EventRef(
        event_id=str(payload["id"]),
        sport_key=str(payload.get("league", {}).get("slug", "")),
        home_team=str(payload["home"]),
        away_team=str(payload["away"]),
        commence_time=_parse_iso(str(payload["date"])),
    )


def observed_markets(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Raw market names per bookmaker, as the feed sent them.

    The mapping table above is built from this rather than from the vendor's
    documentation: names are the part of a third-party feed most likely to
    differ from what is written down.
    """
    return {
        str(book): sorted({str(market["name"]) for market in markets})
        for book, markets in payload.get("bookmakers", {}).items()
    }


def _tick(
    event: EventRef,
    *,
    bookmaker: str,
    market: str,
    selection: str,
    line: Decimal | None,
    price: float,
    observed_at: datetime,
) -> OddsTickRecord:
    return OddsTickRecord(
        sport_key=event.sport_key,
        event_id=event.event_id,
        commence_time=event.commence_time,
        home_team=event.home_team,
        away_team=event.away_team,
        bookmaker=bookmaker,
        market=market,
        selection=selection,
        line=line,
        price=price,
        observed_at=observed_at,
    )


def parse_odds(
    payload: dict[str, Any], *, observed_at: datetime
) -> list[OddsTickRecord]:
    """Flatten one ``/odds`` response. Unmapped markets are skipped."""
    event = event_ref(payload)
    ticks: list[OddsTickRecord] = []
    for bookmaker, markets in payload.get("bookmakers", {}).items():
        for market in markets:
            name = MARKET_NAMES.get(str(market["name"]))
            if name is None:
                continue
            for entry in market.get("odds", []):
                line = (
                    Decimal(str(entry["hdp"])) if entry.get("hdp") is not None
                    else None
                )
                for selection in _SELECTION_KEYS[name]:
                    if entry.get(selection) is None:
                        continue
                    ticks.append(
                        _tick(
                            event,
                            bookmaker=str(bookmaker),
                            market=name,
                            selection=selection,
                            line=line,
                            price=float(entry[selection]),
                            observed_at=observed_at,
                        )
                    )
    return ticks


def parse_movements(
    payload: dict[str, Any],
    *,
    event: EventRef,
    market: str,
    line: Decimal | None,
) -> list[OddsTickRecord]:
    """Flatten a ``/odds/movements`` history into ticks, one per instant.

    ``timestamp`` is Unix seconds; it becomes the tick's ``observed_at``,
    which means these ticks describe when the *book* moved rather than when
    we happened to look — the resolution the latency map needs.
    """
    bookmaker = str(payload.get("bookmaker", ""))
    ticks: list[OddsTickRecord] = []
    for movement in payload.get("movements", []):
        when = datetime.fromtimestamp(int(movement["timestamp"]), tz=UTC)
        for selection in _SELECTION_KEYS[market]:
            if movement.get(selection) is None:
                continue
            ticks.append(
                _tick(
                    event,
                    bookmaker=bookmaker,
                    market=market,
                    selection=selection,
                    line=line,
                    price=float(movement[selection]),
                    observed_at=when,
                )
            )
    return ticks


def _http_fetch(url: str, params: dict[str, str]) -> tuple[Any, dict[str, str]]:
    # As with The Odds API: httpx logs full request URLs at INFO, and this
    # API's key rides in the query string.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    response = httpx.get(url, params=params, timeout=30.0)
    response.raise_for_status()
    payload: Any = response.json()
    return payload, dict(response.headers)


class OddsApiIoClient:
    def __init__(self, api_key: str, *, fetch: FetchFn | None = None) -> None:
        self._api_key = api_key
        self._fetch = fetch if fetch is not None else _http_fetch

    def list_bookmakers(self) -> list[dict[str, Any]]:
        """The catalogue. Unauthenticated, so this is the cheap way to check
        which PL books the plan can actually see."""
        payload, _ = self._fetch(f"{_BASE_URL}/bookmakers", {})
        return list(payload)

    def select_bookmakers(self, bookmakers: list[str]) -> Any:
        """Choose the two books the free tier allows."""
        payload, _ = self._fetch(
            f"{_BASE_URL}/bookmakers/selected/select",
            {"apiKey": self._api_key, "bookmakers": ",".join(bookmakers)},
        )
        return payload

    def list_events(
        self, *, sport: str = "football", league: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"apiKey": self._api_key, "sport": sport}
        if league is not None:
            params["league"] = league
        payload, _ = self._fetch(f"{_BASE_URL}/events", params)
        return list(payload)

    def fetch_odds(self, event_id: str, *, bookmakers: list[str]) -> Any:
        payload, _ = self._fetch(
            f"{_BASE_URL}/odds",
            {
                "apiKey": self._api_key,
                "eventId": event_id,
                "bookmakers": ",".join(bookmakers),
            },
        )
        return payload

    def fetch_movements(
        self,
        event: EventRef,
        *,
        bookmaker: str,
        market: str,
        line: Decimal | None = None,
    ) -> list[OddsTickRecord]:
        params = {
            "apiKey": self._api_key,
            "eventId": event.event_id,
            "bookmaker": bookmaker,
            "market": _MARKET_NAMES_REVERSED[market],
        }
        if line is not None:
            params["marketLine"] = str(line)
        payload, _ = self._fetch(f"{_BASE_URL}/odds/movements", params)
        return parse_movements(payload, event=event, market=market, line=line)
