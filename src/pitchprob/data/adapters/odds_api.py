"""The Odds API (the-odds-api.com) adapter — the live odds tape (ADR 0012).

Quota discipline is part of the contract: the free tier grants 500 credits
per month and one request costs (regions x markets) credits, so five leagues
with three markets is ~15 credits per snapshot — roughly one snapshot per
day. The client surfaces the remaining quota from the response headers so
the recorder can print it after every run; nothing here caches, because a
tape that serves stale prices is worse than no tape.

Conventions match the historical odds table so the tape joins cleanly:
h2h→``1x2`` (home/draw/away), totals→``ou`` (over/under, point as the
line), spreads→``ah`` with BOTH selections carrying the *home* handicap
(football-data convention). Team names stay raw — canonicalization happens
at analysis time, never at capture time.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

#: league code → The Odds API sport key
SPORT_KEYS: dict[str, str] = {
    "E0": "soccer_epl",
    "SP1": "soccer_spain_la_liga",
    "D1": "soccer_germany_bundesliga",
    "I1": "soccer_italy_serie_a",
    "F1": "soccer_france_ligue_one",
}

_MARKET_NAMES = {"h2h": "1x2", "totals": "ou", "spreads": "ah"}

_BASE_URL = "https://api.the-odds-api.com/v4"

#: (url, params) -> (json payload, response headers). Injected in tests.
FetchFn = Callable[[str, dict[str, str]], tuple[list[Any], dict[str, str]]]


@dataclass(frozen=True, slots=True)
class OddsTickRecord:
    sport_key: str
    event_id: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmaker: str
    market: str
    selection: str
    line: Decimal | None
    price: float


@dataclass(frozen=True, slots=True)
class OddsSnapshot:
    ticks: list[OddsTickRecord]
    requests_remaining: int | None


def _selection_1x2(name: str, home: str, away: str) -> str | None:
    if name == home:
        return "home"
    if name == away:
        return "away"
    if name == "Draw":
        return "draw"
    return None


def parse_events(payload: list[Any], sport_key: str) -> list[OddsTickRecord]:
    """Flatten one odds response into tick records; unsupported markets and
    unrecognizable outcomes are skipped, never fatal — the tape keeps
    rolling."""
    ticks: list[OddsTickRecord] = []
    for event in payload:
        home = str(event["home_team"])
        away = str(event["away_team"])
        commence = datetime.fromisoformat(
            str(event["commence_time"]).replace("Z", "+00:00")
        )
        for book in event.get("bookmakers", []):
            bookmaker = str(book["key"])
            for market in book.get("markets", []):
                market_name = _MARKET_NAMES.get(str(market["key"]))
                if market_name is None:
                    continue
                for outcome in market.get("outcomes", []):
                    name = str(outcome["name"])
                    price = float(outcome["price"])
                    line: Decimal | None = None
                    if market_name == "1x2":
                        selection = _selection_1x2(name, home, away)
                    elif market_name == "ou":
                        selection = name.lower() if name in ("Over", "Under") else None
                        if "point" in outcome:
                            line = Decimal(str(outcome["point"]))
                    else:  # ah
                        side = _selection_1x2(name, home, away)
                        selection = side if side in ("home", "away") else None
                        if "point" in outcome:
                            point = Decimal(str(outcome["point"]))
                            # both sides carry the *home* handicap
                            line = point if side == "home" else -point
                    if selection is None:
                        continue
                    ticks.append(
                        OddsTickRecord(
                            sport_key=sport_key,
                            event_id=str(event["id"]),
                            commence_time=commence,
                            home_team=home,
                            away_team=away,
                            bookmaker=bookmaker,
                            market=market_name,
                            selection=selection,
                            line=line,
                            price=price,
                        )
                    )
    return ticks


def _http_fetch(url: str, params: dict[str, str]) -> tuple[list[Any], dict[str, str]]:
    # httpx logs full request URLs at INFO — which would put the apiKey
    # query parameter into every log line. Silence it for this call path.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    response = httpx.get(url, params=params, timeout=30.0)
    response.raise_for_status()
    payload: list[Any] = response.json()
    return payload, dict(response.headers)


class OddsApiClient:
    def __init__(self, api_key: str, *, fetch: FetchFn | None = None) -> None:
        self._api_key = api_key
        self._fetch = fetch if fetch is not None else _http_fetch

    def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot:
        url = f"{_BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        payload, headers = self._fetch(url, params)
        remaining_header = headers.get("x-requests-remaining")
        remaining = int(float(remaining_header)) if remaining_header else None
        return OddsSnapshot(
            ticks=parse_events(payload, sport_key),
            requests_remaining=remaining,
        )
