"""API-Football adapter/client tests (fully offline; ADR 0008)."""

import json
from datetime import date

import httpx
import pytest

from pitchprob.data.adapters.api_football import (
    FREE_SEASONS,
    LEAGUE_IDS,
    ApiFootballClient,
    ApiFootballError,
    injuries_params,
    parse_injuries,
)

PAYLOAD = {
    "get": "injuries",
    "errors": [],
    "results": 3,
    "response": [
        {
            "player": {"id": 1, "name": "J. Butland", "reason": "Wrist Injury"},
            "team": {"id": 52, "name": "Crystal Palace"},
            "fixture": {"id": 10, "date": "2022-08-05T19:00:00+00:00"},
            "league": {"id": 39, "season": 2022},
        },
        {
            "player": {"id": 2, "name": "M. Olise", "reason": "Foot Injury"},
            "team": {"id": 52, "name": "Crystal Palace"},
            "fixture": {"id": 10, "date": "2022-08-05T19:00:00+00:00"},
            "league": {"id": 39, "season": 2022},
        },
        {   # record without a fixture date must be skipped, not crash
            "player": {"id": 3, "name": "A. Ghost", "reason": None},
            "team": {"id": 42, "name": "Arsenal"},
            "fixture": {"id": None, "date": None},
            "league": {"id": 39, "season": 2022},
        },
    ],
}


class TestParseInjuries:
    def test_parses_dated_records(self) -> None:
        records = parse_injuries(json.dumps(PAYLOAD).encode())
        assert len(records) == 2
        first = records[0]
        assert first.team_name == "Crystal Palace"
        assert first.player_name == "J. Butland"
        assert first.match_date == date(2022, 8, 5)
        assert first.reason == "Wrist Injury"
        assert first.season == 2022

    def test_api_errors_raise(self) -> None:
        bad = {"errors": {"plan": "Free plans do not have access to this season."}}
        with pytest.raises(ApiFootballError, match="plan"):
            parse_injuries(json.dumps(bad).encode())


class TestParamsAndGuards:
    def test_league_mapping(self) -> None:
        assert injuries_params("E0", 2022) == {"league": 39, "season": 2022}
        assert injuries_params("D1", 2024) == {"league": 78, "season": 2024}
        assert set(LEAGUE_IDS) == {"E0", "SP1", "D1", "I1", "F1"}

    def test_season_guard(self) -> None:
        with pytest.raises(ValueError, match="2022"):
            injuries_params("E0", 2026)
        assert range(2022, 2025) == FREE_SEASONS

    def test_unknown_league(self) -> None:
        with pytest.raises(KeyError):
            injuries_params("XX", 2022)


class TestClient:
    def _client(self, tmp_path, handler) -> ApiFootballClient:
        transport = httpx.MockTransport(handler)
        return ApiFootballClient(key="k", cache_dir=tmp_path, transport=transport)

    def test_sends_key_header_and_caches(self, tmp_path) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.headers.get("x-apisports-key"))
            return httpx.Response(200, json=PAYLOAD)

        client = self._client(tmp_path, handler)
        first = client.get_json("injuries", {"league": 39, "season": 2022})
        second = client.get_json("injuries", {"league": 39, "season": 2022})
        assert calls == ["k"]  # second hit served from cache
        assert first == second

    def test_http_error_raises(self, tmp_path) -> None:
        client = self._client(
            tmp_path, lambda request: httpx.Response(499, json={})
        )
        with pytest.raises(httpx.HTTPStatusError):
            client.get_json("injuries", {"league": 39, "season": 2022})

    def test_missing_key_raises(self, tmp_path) -> None:
        with pytest.raises(ApiFootballError, match="key"):
            ApiFootballClient(key=None, cache_dir=tmp_path)
