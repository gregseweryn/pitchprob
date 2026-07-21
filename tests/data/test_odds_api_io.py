"""odds-api.io adapter tests — offline, payloads from the published docs.

This feed exists for one reason The Odds API cannot serve: its catalogue
carries Polish-licensed books (verified live 2026-07-21 against the
unauthenticated /bookmakers endpoint: Betclic PL, STS PL, eFortuna PL,
Betfan PL, LVbet PL, Superbet). It carries no Pinnacle, so it is a source of
*followers*, never of the sharp anchor.

Its market names are its own ("ML", "Totals", "Asian Handicap") and the
mapping to ours is pinned here. Anything unrecognised is skipped, never
fatal — same rule as the The Odds API adapter.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pitchprob.data.adapters.odds_api_io import (
    SOURCE,
    EventRef,
    OddsApiIoClient,
    observed_markets,
    parse_movements,
    parse_odds,
)

EVENT_PAYLOAD = {
    "id": 123456,
    "home": "Manchester United",
    "away": "Liverpool",
    "date": "2026-08-15T15:00:00Z",
    "status": "pending",
    "sport": {"name": "Football", "slug": "football"},
    "league": {"name": "England - Premier League", "slug": "england-premier-league"},
    "bookmakers": {
        "Betclic PL": [
            {
                "name": "ML",
                "updatedAt": "2026-08-14T10:30:00Z",
                "odds": [{"home": "2.10", "draw": "3.40", "away": "3.20"}],
            },
            {
                "name": "Asian Handicap",
                "updatedAt": "2026-08-14T10:30:00Z",
                "odds": [
                    {"hdp": -0.5, "home": "1.95", "away": "1.85"},
                    {"hdp": 0, "home": "1.75", "away": "2.05"},
                ],
            },
            {
                "name": "Totals",
                "updatedAt": "2026-08-14T10:30:00Z",
                "odds": [{"hdp": 2.5, "over": "1.90", "under": "1.92"}],
            },
            {
                "name": "Correct Score",  # unmapped: skipped, not fatal
                "updatedAt": "2026-08-14T10:30:00Z",
                "odds": [{"label": "1-0", "odds": "8.00"}],
            },
        ],
        "STS PL": [
            {
                "name": "ML",
                "updatedAt": "2026-08-14T10:00:00Z",
                "odds": [{"home": "2.05", "draw": "3.45", "away": "3.30"}],
            }
        ],
    },
}

_OBSERVED = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


class TestParseOdds:
    def test_ml_maps_to_1x2(self) -> None:
        ticks = parse_odds(EVENT_PAYLOAD, observed_at=_OBSERVED)
        ml = {
            t.selection: t
            for t in ticks
            if t.market == "1x2" and t.bookmaker == "Betclic PL"
        }
        assert ml["home"].price == pytest.approx(2.10)
        assert ml["draw"].price == pytest.approx(3.40)
        assert ml["away"].price == pytest.approx(3.20)
        assert ml["home"].line is None

    def test_totals_use_hdp_as_the_line(self) -> None:
        ticks = parse_odds(EVENT_PAYLOAD, observed_at=_OBSERVED)
        over = next(t for t in ticks if t.market == "ou" and t.selection == "over")
        assert over.line == Decimal("2.5")
        assert over.price == pytest.approx(1.90)

    def test_asian_handicap_keeps_every_line_with_the_home_sign(self) -> None:
        """This feed already quotes hdp from the home side, which is the
        convention the rest of the project uses — no negation needed, and a
        test to keep it that way."""
        ticks = parse_odds(EVENT_PAYLOAD, observed_at=_OBSERVED)
        ah = [t for t in ticks if t.market == "ah"]
        by_key = {(t.selection, t.line): t.price for t in ah}
        assert by_key[("home", Decimal("-0.5"))] == pytest.approx(1.95)
        assert by_key[("away", Decimal("-0.5"))] == pytest.approx(1.85)
        assert by_key[("home", Decimal("0"))] == pytest.approx(1.75)

    def test_event_context_and_source_are_attached(self) -> None:
        ticks = parse_odds(EVENT_PAYLOAD, observed_at=_OBSERVED)
        assert all(t.event_id == "123456" for t in ticks)
        assert all(t.sport_key == "england-premier-league" for t in ticks)
        assert all(
            t.commence_time == datetime(2026, 8, 15, 15, 0, tzinfo=UTC)
            for t in ticks
        )
        assert {t.bookmaker for t in ticks} == {"Betclic PL", "STS PL"}

    def test_unmapped_markets_are_skipped(self) -> None:
        ticks = parse_odds(EVENT_PAYLOAD, observed_at=_OBSERVED)
        # Betclic: 3 ML + 4 AH + 2 OU = 9; STS: 3 ML. Correct Score dropped.
        assert len(ticks) == 12

    def test_observed_markets_reports_raw_names_for_mapping_work(self) -> None:
        """The mapping table is built from what the feed actually sends, not
        from guesses — `pitchprob oddsio probe` prints this."""
        seen = observed_markets(EVENT_PAYLOAD)
        assert seen["Betclic PL"] == [
            "Asian Handicap", "Correct Score", "ML", "Totals",
        ]


MOVEMENTS_PAYLOAD = {
    "eventid": "123456",
    "bookmaker": "Betclic PL",
    "movements": [
        {"home": 2.00, "draw": 3.20, "away": 3.50, "timestamp": 1786000000},
        {"home": 2.10, "draw": 3.30, "away": 3.40, "timestamp": 1786003600},
    ],
}

_EVENT = EventRef(
    event_id="123456",
    sport_key="england-premier-league",
    home_team="Manchester United",
    away_team="Liverpool",
    commence_time=datetime(2026, 8, 15, 15, 0, tzinfo=UTC),
)


class TestParseMovements:
    def test_each_movement_becomes_a_tick_at_its_own_instant(self) -> None:
        """This is what makes the latency map possible: a movement series is
        a tick series, so it lands in odds_ticks and the existing analysis
        reads it with no new plumbing."""
        ticks = parse_movements(
            MOVEMENTS_PAYLOAD, event=_EVENT, market="1x2", line=None
        )
        assert len(ticks) == 6  # two snapshots x three selections
        first = [t for t in ticks if t.observed_at.timestamp() == 1786000000]
        assert {t.selection: t.price for t in first} == pytest.approx(
            {"home": 2.00, "draw": 3.20, "away": 3.50}
        )
        assert all(t.observed_at.tzinfo is not None for t in ticks)

    def test_totals_movements_carry_the_requested_line(self) -> None:
        payload = {
            "eventid": "123456",
            "bookmaker": "STS PL",
            "movements": [
                {"over": 1.90, "under": 1.92, "timestamp": 1786000000},
            ],
        }
        ticks = parse_movements(
            payload, event=_EVENT, market="ou", line=Decimal("2.5")
        )
        assert {t.selection for t in ticks} == {"over", "under"}
        assert all(t.line == Decimal("2.5") for t in ticks)

    def test_an_empty_history_is_not_an_error(self) -> None:
        assert parse_movements(
            {"movements": []}, event=_EVENT, market="1x2", line=None
        ) == []


class TestClient:
    def test_key_travels_as_a_query_param_and_bookmakers_are_explicit(
        self,
    ) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return EVENT_PAYLOAD, {}

        client = OddsApiIoClient(api_key="k123", fetch=fake_fetch)
        client.fetch_odds("123456", bookmakers=["Betclic PL", "STS PL"])
        url, params = calls[0]
        assert url.endswith("/odds")
        assert params["apiKey"] == "k123"
        assert params["eventId"] == "123456"
        assert params["bookmakers"] == "Betclic PL,STS PL"

    def test_movements_request_names_market_and_line(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return MOVEMENTS_PAYLOAD, {}

        client = OddsApiIoClient(api_key="k", fetch=fake_fetch)
        client.fetch_movements(
            _EVENT, bookmaker="Betclic PL", market="ou", line=Decimal("2.5")
        )
        _url, params = calls[0]
        assert params["market"] == "Totals"
        assert params["marketLine"] == "2.5"

    def test_ml_movements_send_no_market_line(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_fetch(url: str, params: dict[str, str]):
            calls.append((url, params))
            return MOVEMENTS_PAYLOAD, {}

        client = OddsApiIoClient(api_key="k", fetch=fake_fetch)
        client.fetch_movements(_EVENT, bookmaker="Betclic PL", market="1x2")
        assert "marketLine" not in calls[0][1]

    def test_the_source_tag_marks_these_ticks_as_a_different_feed(self) -> None:
        assert SOURCE == "odds-api-io"
