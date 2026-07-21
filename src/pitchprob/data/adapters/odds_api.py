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

Corners (ADR 0014) arrive through a different door. They are "additional
markets", served only by ``/events/{id}/odds`` — one paid request per
fixture — and they are priced late: measured on 2026-07-21, Pinnacle
quoted corners about a day before kickoff, nothing at three days, nothing
at a month. Their payload shape is identical to the goals markets
(``Over``/``Under`` with a ``point``; team names with a signed handicap),
so ``corners_ou``/``corners_ah`` reuse the same parsing shapes rather than
growing a second code path. They are *alternate* markets: several lines
per market, every one kept as its own tick.
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

_MARKET_NAMES = {
    "h2h": "1x2",
    "totals": "ou",
    "spreads": "ah",
    "alternate_totals_corners": "corners_ou",
    "alternate_spreads_corners": "corners_ah",
}

#: Every supported market parses as one of three shapes. Corners borrow the
#: goals shapes outright — see the module docstring.
_MARKET_SHAPES = {
    "1x2": "1x2",
    "ou": "ou",
    "ah": "ah",
    "corners_ou": "ou",
    "corners_ah": "ah",
}

#: The corners pilot (ADR 0014): totals and handicap, no cards. Cards are a
#: separate market key and a separate credit, and the audit rates them P2 —
#: they stay out until corners have earned the spend.
CORNERS_MARKETS = ["alternate_totals_corners", "alternate_spreads_corners"]

_BASE_URL = "https://api.the-odds-api.com/v4"

#: (url, params) -> (json payload, response headers). Injected in tests.
#: The payload is a list for the listing endpoints and a single object for
#: event odds, hence ``Any``.
FetchFn = Callable[[str, dict[str, str]], tuple[Any, dict[str, str]]]


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
    #: When this price was true, when the source knows. Snapshot endpoints
    #: leave it None and the recorder stamps the instant it looked; a
    #: movement history knows better, and a tick that carries its own
    #: instant must keep it — that timestamp is the whole resolution of the
    #: latency map.
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OddsSnapshot:
    ticks: list[OddsTickRecord]
    requests_remaining: int | None
    #: What this one request actually cost, from ``x-requests-last``. The
    #: event-odds endpoint bills per market *returned*, so the cost is not
    #: knowable in advance — the recorder budgets against the real number.
    credits_spent: int | None = None


@dataclass(frozen=True, slots=True)
class EventSummary:
    """One fixture from the free ``/events`` listing: enough to decide
    whether it is close enough to kickoff to be worth a paid request."""

    event_id: str
    commence_time: datetime
    home_team: str
    away_team: str


@dataclass(frozen=True, slots=True)
class EventListing:
    events: list[EventSummary]
    requests_remaining: int | None


def _selection_1x2(name: str, home: str, away: str) -> str | None:
    if name == home:
        return "home"
    if name == away:
        return "away"
    if name == "Draw":
        return "draw"
    return None


def _commence_time(event: Any) -> datetime:
    return datetime.fromisoformat(
        str(event["commence_time"]).replace("Z", "+00:00")
    )


def parse_event_list(payload: list[Any]) -> list[EventSummary]:
    """Flatten the free ``/events`` listing."""
    return [
        EventSummary(
            event_id=str(event["id"]),
            commence_time=_commence_time(event),
            home_team=str(event["home_team"]),
            away_team=str(event["away_team"]),
        )
        for event in payload
    ]


def parse_events(payload: list[Any], sport_key: str) -> list[OddsTickRecord]:
    """Flatten one odds response into tick records; unsupported markets and
    unrecognizable outcomes are skipped, never fatal — the tape keeps
    rolling."""
    ticks: list[OddsTickRecord] = []
    for event in payload:
        home = str(event["home_team"])
        away = str(event["away_team"])
        commence = _commence_time(event)
        for book in event.get("bookmakers", []):
            bookmaker = str(book["key"])
            for market in book.get("markets", []):
                market_name = _MARKET_NAMES.get(str(market["key"]))
                if market_name is None:
                    continue
                shape = _MARKET_SHAPES[market_name]
                for outcome in market.get("outcomes", []):
                    name = str(outcome["name"])
                    price = float(outcome["price"])
                    line: Decimal | None = None
                    if shape == "1x2":
                        selection = _selection_1x2(name, home, away)
                    elif shape == "ou":
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


def scrub_http_error(exc: httpx.HTTPError) -> str:
    """One-line, key-free description of an HTTP failure.

    httpx embeds the full request URL — the ``apiKey`` query parameter
    included — in exception messages, so neither ``str(exc)`` nor the URL
    may ever reach the console (GitHub Actions masks secrets; local runs
    do not). Status code and the quota header are safe, and they are the
    two facts an operator needs: 401 = bad key, 429 = monthly quota gone.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        message = f"The Odds API request failed: HTTP {status}"
        if status == 401:
            message += " (invalid or missing API key)"
        elif status == 429:
            message += " (monthly credit quota exhausted)"
        remaining = exc.response.headers.get("x-requests-remaining")
        if remaining is not None:
            message += f"; credits remaining this month: {remaining}"
        return message
    return (
        f"The Odds API request failed: {type(exc).__name__} "
        "(URL withheld — it carries the API key)"
    )


def _http_fetch(url: str, params: dict[str, str]) -> tuple[Any, dict[str, str]]:
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

    @staticmethod
    def _quota(headers: dict[str, str]) -> tuple[int | None, int | None]:
        remaining = headers.get("x-requests-remaining")
        spent = headers.get("x-requests-last")
        return (
            int(float(remaining)) if remaining else None,
            int(float(spent)) if spent is not None and spent != "" else None,
        )

    def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot:
        url = f"{_BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        payload, headers = self._fetch(url, params)
        remaining, spent = self._quota(headers)
        return OddsSnapshot(
            ticks=parse_events(payload, sport_key),
            requests_remaining=remaining,
            credits_spent=spent,
        )

    def fetch_events(self, sport_key: str) -> EventListing:
        """The fixture list for a competition. Costs zero credits — it
        carries no prices — so the recorder may always afford to look before
        it spends."""
        url = f"{_BASE_URL}/sports/{sport_key}/events"
        payload, headers = self._fetch(url, {"apiKey": self._api_key})
        remaining, _ = self._quota(headers)
        return EventListing(
            events=parse_event_list(payload), requests_remaining=remaining
        )

    def fetch_event_odds(
        self, sport_key: str, event_id: str, *, markets: list[str]
    ) -> OddsSnapshot:
        """Additional markets for one fixture. Billed per market *returned*,
        so a fixture with nothing priced yet is free."""
        url = f"{_BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
        params = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }
        payload, headers = self._fetch(url, params)
        remaining, spent = self._quota(headers)
        return OddsSnapshot(
            ticks=parse_events([payload], sport_key),
            requests_remaining=remaining,
            credits_spent=spent,
        )
