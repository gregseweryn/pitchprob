"""The Odds API adapter tests — offline, with a canned v4 payload.

Conventions pinned here: h2h→1x2 with Draw mapped to "draw", totals→ou with
the shared point as the line, spreads→ah with BOTH selections carrying the
*home* handicap (football-data convention, so the tape joins cleanly against
the historical odds table).
"""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from pitchprob.data.adapters.odds_api import (
    CORNERS_MARKETS,
    SPORT_KEYS,
    OddsApiClient,
    parse_event_list,
    parse_events,
    scrub_http_error,
)

PAYLOAD = [
    {
        "id": "abc123",
        "sport_key": "soccer_epl",
        "commence_time": "2026-08-15T14:00:00Z",
        "home_team": "Arsenal",
        "away_team": "Leeds United",
        "bookmakers": [
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.65},
                            {"name": "Draw", "price": 4.1},
                            {"name": "Leeds United", "price": 5.4},
                        ],
                    },
                    {
                        "key": "totals",
                        "outcomes": [
                            {"name": "Over", "price": 1.85, "point": 2.5},
                            {"name": "Under", "price": 1.98, "point": 2.5},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Arsenal", "price": 1.9, "point": -1.25},
                            {"name": "Leeds United", "price": 1.95, "point": 1.25},
                        ],
                    },
                    {
                        "key": "btts",  # unsupported market: skipped, not fatal
                        "outcomes": [{"name": "Yes", "price": 1.8}],
                    },
                ],
            },
            {
                "key": "betclic",
                "title": "Betclic",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Leeds United", "price": 5.0},
                            {"name": "Arsenal", "price": 1.62},
                            {"name": "Draw", "price": 4.0},
                        ],
                    }
                ],
            },
        ],
    }
]


class TestParseEvents:
    def test_h2h_maps_to_1x2_selections(self) -> None:
        ticks = parse_events(PAYLOAD, "soccer_epl")
        h2h = [t for t in ticks if t.market == "1x2" and t.bookmaker == "pinnacle"]
        by_selection = {t.selection: t for t in h2h}
        assert by_selection["home"].price == pytest.approx(1.65)
        assert by_selection["draw"].price == pytest.approx(4.1)
        assert by_selection["away"].price == pytest.approx(5.4)
        assert by_selection["home"].line is None

    def test_totals_carry_the_point_as_line(self) -> None:
        ticks = parse_events(PAYLOAD, "soccer_epl")
        over = next(
            t for t in ticks if t.market == "ou" and t.selection == "over"
        )
        assert over.line == Decimal("2.5")
        assert over.price == pytest.approx(1.85)

    def test_spreads_both_sides_carry_home_handicap(self) -> None:
        ticks = parse_events(PAYLOAD, "soccer_epl")
        ah = {t.selection: t for t in ticks if t.market == "ah"}
        assert ah["home"].line == Decimal("-1.25")
        assert ah["away"].line == Decimal("-1.25")  # football-data convention
        assert ah["away"].price == pytest.approx(1.95)

    def test_event_metadata_and_unknown_markets(self) -> None:
        ticks = parse_events(PAYLOAD, "soccer_epl")
        assert all(t.event_id == "abc123" for t in ticks)
        assert all(
            t.commence_time == datetime(2026, 8, 15, 14, 0, tzinfo=UTC)
            for t in ticks
        )
        assert not [t for t in ticks if t.market == "btts"]
        # two books, second one h2h only: 3+2+2 pinnacle + 3 betclic = 10
        assert len(ticks) == 10
        assert {t.bookmaker for t in ticks} == {"pinnacle", "betclic"}

    def test_sport_keys_cover_all_five_leagues(self) -> None:
        assert set(SPORT_KEYS) == {"E0", "SP1", "D1", "I1", "F1"}


#: Captured live on 2026-07-21 from /events/{id}/odds — the endpoint returns a
#: single event object (not a list) and corners come as *alternate* markets:
#: many `point` values per market, one outcome per (side, line). The corners
#: handicap carries the same sign convention as `spreads` (home -0.5 ↔ away
#: +0.5), which is why corners reuse the goals parsing shapes verbatim.
EVENT_ODDS_PAYLOAD = {
    "id": "f408073",
    "sport_key": "soccer_brazil_campeonato",
    "commence_time": "2026-07-21T22:30:00Z",
    "home_team": "Atletico Mineiro",
    "away_team": "Bahia",
    "bookmakers": [
        {
            "key": "pinnacle",
            "title": "Pinnacle",
            "markets": [
                {
                    "key": "alternate_totals_corners",
                    "outcomes": [
                        {"name": "Over", "price": 1.75, "point": 10.5},
                        {"name": "Under", "price": 1.85, "point": 10.5},
                        {"name": "Over", "price": 2.15, "point": 11.5},
                        {"name": "Under", "price": 1.55, "point": 11.5},
                    ],
                },
                {
                    "key": "alternate_spreads_corners",
                    "outcomes": [
                        {"name": "Atletico Mineiro", "price": 1.8, "point": -0.5},
                        {"name": "Bahia", "price": 1.95, "point": 0.5},
                    ],
                },
                {
                    "key": "alternate_totals_cards",  # outside the pilot: skipped
                    "outcomes": [
                        {"name": "Over", "price": 1.9, "point": 4.5},
                        {"name": "Under", "price": 1.8, "point": 4.5},
                    ],
                },
            ],
        }
    ],
}


class TestParseEventOdds:
    def test_corners_totals_keep_every_alternate_line(self) -> None:
        ticks = parse_events([EVENT_ODDS_PAYLOAD], "soccer_brazil_campeonato")
        corners = [t for t in ticks if t.market == "corners_ou"]
        by_key = {(t.selection, t.line): t.price for t in corners}
        assert by_key[("over", Decimal("10.5"))] == pytest.approx(1.75)
        assert by_key[("under", Decimal("11.5"))] == pytest.approx(1.55)
        assert len(corners) == 4  # two lines x two sides, none collapsed

    def test_corners_handicap_both_sides_carry_home_line(self) -> None:
        ticks = parse_events([EVENT_ODDS_PAYLOAD], "soccer_brazil_campeonato")
        ah = {t.selection: t for t in ticks if t.market == "corners_ah"}
        assert ah["home"].line == Decimal("-0.5")
        assert ah["away"].line == Decimal("-0.5")
        assert ah["away"].price == pytest.approx(1.95)

    def test_cards_are_outside_the_pilot_and_skipped(self) -> None:
        ticks = parse_events([EVENT_ODDS_PAYLOAD], "soccer_brazil_campeonato")
        assert not [t for t in ticks if "cards" in t.market]
        assert len(ticks) == 6

    def test_corners_market_keys_are_the_pilot_pair(self) -> None:
        assert CORNERS_MARKETS == ["alternate_totals_corners",
                                   "alternate_spreads_corners"]


LIST_PAYLOAD = [
    {
        "id": "e1",
        "commence_time": "2026-08-15T14:00:00Z",
        "home_team": "Arsenal",
        "away_team": "Leeds United",
    },
    {
        "id": "e2",
        "commence_time": "2026-08-16T16:30:00Z",
        "home_team": "Everton",
        "away_team": "Fulham",
    },
]


class TestParseEventList:
    """The /events endpoint costs 0 credits — it is what decides which
    fixtures are worth a paid event-odds request."""

    def test_parses_id_kickoff_and_teams(self) -> None:
        events = parse_event_list(LIST_PAYLOAD)
        assert [e.event_id for e in events] == ["e1", "e2"]
        assert events[0].commence_time == datetime(2026, 8, 15, 14, 0, tzinfo=UTC)
        assert events[1].home_team == "Everton"


class TestOddsApiClient:
    def test_builds_request_and_surfaces_quota(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return PAYLOAD, {"x-requests-remaining": "483"}

        client = OddsApiClient(api_key="k123", fetch=fake_fetch)
        snapshot = client.fetch_odds("soccer_epl", markets=["h2h", "totals"])
        url, params = calls[0]
        assert url.endswith("/sports/soccer_epl/odds")
        assert params["apiKey"] == "k123"
        assert params["regions"] == "eu"
        assert params["markets"] == "h2h,totals"
        assert params["oddsFormat"] == "decimal"
        assert snapshot.requests_remaining == 483
        assert len(snapshot.ticks) == 10

    def test_missing_quota_header_is_none(self) -> None:
        client = OddsApiClient(
            api_key="k", fetch=lambda url, params: (PAYLOAD, {})
        )
        snapshot = client.fetch_odds("soccer_epl", markets=["h2h"])
        assert snapshot.requests_remaining is None

    def test_fetch_events_hits_the_free_endpoint(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return LIST_PAYLOAD, {"x-requests-remaining": "442"}

        client = OddsApiClient(api_key="k123", fetch=fake_fetch)
        listing = client.fetch_events("soccer_epl")
        url, params = calls[0]
        assert url.endswith("/sports/soccer_epl/events")
        # No markets/regions: this endpoint is free precisely because it
        # prices nothing. Sending them would be a paid request by accident.
        assert set(params) == {"apiKey"}
        assert listing.requests_remaining == 442
        assert [e.event_id for e in listing.events] == ["e1", "e2"]

    def test_fetch_event_odds_wraps_the_single_event_object(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return EVENT_ODDS_PAYLOAD, {"x-requests-last": "1"}

        client = OddsApiClient(api_key="k123", fetch=fake_fetch)
        snapshot = client.fetch_event_odds(
            "soccer_brazil_campeonato", "f408073", markets=CORNERS_MARKETS
        )
        url, params = calls[0]
        assert url.endswith(
            "/sports/soccer_brazil_campeonato/events/f408073/odds"
        )
        assert params["markets"] == (
            "alternate_totals_corners,alternate_spreads_corners"
        )
        assert snapshot.credits_spent == 1
        assert {t.market for t in snapshot.ticks} == {"corners_ou", "corners_ah"}

    def test_empty_event_odds_costs_nothing(self) -> None:
        """A fixture with no corners priced yet returns an empty bookmaker
        list and does not count against the quota — that is what makes the
        T-24h scan affordable."""
        empty = {**EVENT_ODDS_PAYLOAD, "bookmakers": []}
        client = OddsApiClient(
            api_key="k",
            fetch=lambda url, params: (empty, {"x-requests-last": "0"}),
        )
        snapshot = client.fetch_event_odds("s", "e", markets=CORNERS_MARKETS)
        assert snapshot.ticks == []
        assert snapshot.credits_spent == 0


class TestScrubHttpError:
    """Audit finding A4: httpx error messages embed the request URL with
    the apiKey; the scrubbed message must carry status + quota, never the
    key or the URL."""

    @staticmethod
    def _status_error(status: int, headers: dict[str, str]) -> httpx.HTTPStatusError:
        request = httpx.Request(
            "GET", "https://api.the-odds-api.com/v4/sports/soccer_epl/odds",
            params={"apiKey": "SECRET-KEY-123"},
        )
        response = httpx.Response(status, headers=headers, request=request)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return exc
        raise AssertionError("raise_for_status did not raise")

    def test_quota_exhaustion_keeps_status_and_quota_drops_key(self) -> None:
        exc = self._status_error(429, {"x-requests-remaining": "0"})
        assert "SECRET-KEY-123" in str(exc)  # the hazard being scrubbed
        message = scrub_http_error(exc)
        assert "SECRET-KEY-123" not in message
        assert "apiKey" not in message
        assert "429" in message
        assert "quota" in message
        assert "0" in message

    def test_bad_key_names_the_cause_without_the_key(self) -> None:
        exc = self._status_error(401, {})
        message = scrub_http_error(exc)
        assert "SECRET-KEY-123" not in message
        assert "401" in message
        assert "API key" in message

    def test_transport_error_reports_class_only(self) -> None:
        exc = httpx.ConnectError(
            "boom https://api.the-odds-api.com/?apiKey=SECRET-KEY-123"
        )
        message = scrub_http_error(exc)
        assert "SECRET-KEY-123" not in message
        assert "ConnectError" in message
