"""Injuries schema + ingestion tests (SQLite; offline client fake)."""

import json
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from pitchprob.data.injury_service import InjuryUpdateService
from pitchprob.data.orm import Base, Injury
from pitchprob.data.repository import InjuryRepository, TeamRepository
from pitchprob.data.service import IngestionService

from .test_football_data_adapter import BURNLEY_CITY
from .test_normalize_and_service import ARSENAL_FOREST, FakeDownloader, _modern_csv


@pytest.fixture()
def seeded_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payload = _modern_csv([BURNLEY_CITY, ARSENAL_FOREST])
        IngestionService(
            session=session,
            downloader=FakeDownloader(
                {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
            ),
        ).ingest_season("E0", 2023)
        yield session


class TestInjuryRepository:
    def test_upsert_is_idempotent(self, seeded_session: Session) -> None:
        team = TeamRepository.resolve(
            seeded_session, source="football-data", alias="Arsenal"
        )
        for _ in range(2):
            InjuryRepository.upsert(
                seeded_session, team_id=team.id, match_date=date(2023, 8, 12),
                player_name="B. Saka", reason="Knock", season=2023,
            )
        n = seeded_session.execute(select(func.count()).select_from(Injury)).scalar_one()
        assert n == 1

    def test_absence_counts_with_tolerance(self, seeded_session: Session) -> None:
        team = TeamRepository.resolve(
            seeded_session, source="football-data", alias="Arsenal"
        )
        for player, day in (("A", 12), ("B", 12), ("C", 13)):
            InjuryRepository.upsert(
                seeded_session, team_id=team.id, match_date=date(2023, 8, day),
                player_name=player, reason=None, season=2023,
            )
        # exact date + neighbouring day both count (timezone skew, ADR 0008)
        n = InjuryRepository.count_absences(
            seeded_session, team_id=team.id, match_date=date(2023, 8, 12)
        )
        assert n == 3


class _FakeClient:
    """Serves canned /injuries payloads keyed by (league, season)."""

    def __init__(self, payloads: dict[tuple[int, int], dict]) -> None:
        self.payloads = payloads

    def get_bytes(self, path: str, params: dict[str, int]) -> bytes:
        assert path == "injuries"
        return json.dumps(self.payloads[(params["league"], params["season"])]).encode()


def _payload(*records: tuple[str, str, str]) -> dict:
    return {
        "errors": [],
        "response": [
            {
                "player": {"name": player, "reason": "Injury"},
                "team": {"name": team},
                "fixture": {"date": f"{when}T15:00:00+00:00"},
                "league": {"id": 39, "season": 2022},
            }
            for team, player, when in records
        ],
    }


class TestInjuryUpdateService:
    def test_ingests_and_resolves_teams(self, seeded_session: Session) -> None:
        client = _FakeClient(
            {(39, 2022): _payload(("Arsenal", "B. Saka", "2022-08-05"))}
        )
        service = InjuryUpdateService(session=seeded_session, client=client)
        report = service.update_league_season("E0", 2022)

        assert report.parsed == 1
        assert report.inserted == 1
        assert not report.unmatched_teams
        n = seeded_session.execute(select(func.count()).select_from(Injury)).scalar_one()
        assert n == 1

    def test_idempotent(self, seeded_session: Session) -> None:
        client = _FakeClient(
            {(39, 2022): _payload(("Arsenal", "B. Saka", "2022-08-05"))}
        )
        service = InjuryUpdateService(session=seeded_session, client=client)
        service.update_league_season("E0", 2022)
        report2 = service.update_league_season("E0", 2022)
        assert report2.inserted == 0
        n = seeded_session.execute(select(func.count()).select_from(Injury)).scalar_one()
        assert n == 1

    def test_unknown_team_reported_not_crashed(self, seeded_session: Session) -> None:
        client = _FakeClient(
            {(39, 2022): _payload(("Atlantis United", "A. Ghost", "2022-08-05"))}
        )
        service = InjuryUpdateService(session=seeded_session, client=client)
        report = service.update_league_season("E0", 2022)
        assert report.inserted == 0
        assert "Atlantis United" in report.unmatched_teams

    def test_override_map_applies(self, seeded_session: Session) -> None:
        TeamRepository.resolve_or_create(
            seeded_session, source="football-data", alias="Wolves",
            country="England", canonical_name="Wolverhampton Wanderers",
        )
        client = _FakeClient(
            {(39, 2022): _payload(("Wolves", "P. Neto", "2022-08-06"))}
        )
        report = InjuryUpdateService(session=seeded_session, client=client).update_league_season(
            "E0", 2022
        )
        assert report.inserted == 1
        assert not report.unmatched_teams
