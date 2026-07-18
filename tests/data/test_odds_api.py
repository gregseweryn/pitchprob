"""The Odds API adapter tests — offline, with a canned v4 payload.

Conventions pinned here: h2h→1x2 with Draw mapped to "draw", totals→ou with
the shared point as the line, spreads→ah with BOTH selections carrying the
*home* handicap (football-data convention, so the tape joins cleanly against
the historical odds table).
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from pitchprob.data.adapters.odds_api import (
    SPORT_KEYS,
    OddsApiClient,
    parse_events,
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
