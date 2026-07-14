"""API-Football (api-sports.io) adapter — free-tier research scope (ADR 0008).

Verified constraints on the Free plan: 100 requests/day, seasons 2022-2024
only. The adapter therefore guards seasons explicitly, caches every response
on disk (historical seasons are immutable), and network access happens only
through :class:`ApiFootballClient`, which tests replace via ``transport``.
"""

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from pitchprob.core.errors import PitchprobError

BASE_URL = "https://v3.football.api-sports.io"

#: football-data division code -> API-Football league id
LEAGUE_IDS: dict[str, int] = {
    "E0": 39,
    "SP1": 140,
    "D1": 78,
    "I1": 135,
    "F1": 61,
}

#: Season start-years the Free plan can access ("try from 2022 to 2024",
#: verbatim from the API's own error message, verified 2026-07).
FREE_SEASONS = range(2022, 2025)


class ApiFootballError(PitchprobError):
    """The API answered with an application-level error payload."""


@dataclass(frozen=True, slots=True)
class InjuryRecord:
    team_name: str
    player_name: str
    match_date: date
    reason: str | None
    season: int


def injuries_params(league_code: str, season: int) -> dict[str, int]:
    league_id = LEAGUE_IDS[league_code]
    if season not in FREE_SEASONS:
        raise ValueError(
            f"season {season} is outside the free-tier window "
            f"({FREE_SEASONS.start}-{FREE_SEASONS.stop - 1}); see ADR 0008"
        )
    return {"league": league_id, "season": season}


def _check_errors(payload: dict[str, Any]) -> None:
    # the API sends errors: [] when healthy, errors: {...} on failure
    if payload.get("errors"):
        raise ApiFootballError(f"API-Football error: {payload['errors']}")


def parse_injuries(content: bytes) -> tuple[InjuryRecord, ...]:
    payload = json.loads(content)
    _check_errors(payload)
    records: list[InjuryRecord] = []
    for item in payload.get("response") or []:
        raw_date = (item.get("fixture") or {}).get("date")
        if not raw_date:
            continue  # unscheduled/withdrawn record carries no signal
        records.append(
            InjuryRecord(
                team_name=str(item["team"]["name"]),
                player_name=str(item["player"]["name"]),
                match_date=datetime.fromisoformat(raw_date).date(),
                reason=item["player"].get("reason"),
                season=int(item["league"]["season"]),
            )
        )
    return tuple(records)


class ApiFootballClient:
    """Thin cached GET client. Cache key = path + sorted params; historical
    free-tier data never changes, so cache hits cost zero quota."""

    def __init__(
        self,
        *,
        key: str | None,
        cache_dir: Path | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not key:
            raise ApiFootballError(
                "no API-Football key configured; set PITCHPROB_API_FOOTBALL_KEY"
            )
        self.key = key
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.transport = transport

    def get_bytes(self, path: str, params: dict[str, int]) -> bytes:
        query = urlencode(sorted(params.items()))
        cache_path: Path | None = None
        if self.cache_dir is not None:
            cache_path = self.cache_dir / f"api-football_{path}_{query}.json"
            if cache_path.exists():
                return cache_path.read_bytes()

        with httpx.Client(
            base_url=BASE_URL,
            headers={"x-apisports-key": self.key},
            timeout=self.timeout,
            transport=self.transport,
        ) as client:
            response = client.get(f"/{path}", params=params)
        response.raise_for_status()

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(response.content)
        return response.content

    def get_json(self, path: str, params: dict[str, int]) -> dict[str, Any]:
        payload: dict[str, Any] = json.loads(self.get_bytes(path, params))
        _check_errors(payload)
        return payload
