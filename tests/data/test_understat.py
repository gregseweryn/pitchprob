"""Understat adapter and xG-update service tests (fully offline).

The page fixture reproduces Understat's real embedding: a ``datesData``
variable whose JSON is a single-quoted JS string full of ``\\xNN`` escapes.
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.understat import league_url, parse_league_page
from pitchprob.data.orm import Base, Match
from pitchprob.data.service import IngestionService
from pitchprob.data.xg_service import XgUpdateService, resolve_understat_team

from .test_football_data_adapter import BURNLEY_CITY
from .test_normalize_and_service import ARSENAL_FOREST, FakeDownloader, _modern_csv


def _encode_understat(payload: str) -> str:
    """Mimic Understat's escaping: quotes/brackets/braces become \\xNN."""
    table = {'"': "\\x22", "[": "\\x5B", "]": "\\x5D", "{": "\\x7B", "}": "\\x7D"}
    return "".join(table.get(ch, ch) for ch in payload)


UNDERSTAT_JSON = (
    '[{"id":"21001","isResult":true,'
    '"h":{"id":"89","title":"Burnley","short_title":"BUR"},'
    '"a":{"id":"88","title":"Manchester City","short_title":"MCI"},'
    '"goals":{"h":"0","a":"3"},"xG":{"h":"0.31","a":"2.84"},'
    '"datetime":"2023-08-12 19:30:00"},'
    '{"id":"21002","isResult":true,'
    '"h":{"id":"83","title":"Arsenal","short_title":"ARS"},'
    '"a":{"id":"90","title":"Nottingham Forest","short_title":"NOT"},'
    '"goals":{"h":"2","a":"1"},"xG":{"h":"1.53","a":"0.62"},'
    '"datetime":"2023-08-12 15:00:00"},'
    '{"id":"21003","isResult":false,'
    '"h":{"id":"91","title":"Chelsea","short_title":"CHE"},'
    '"a":{"id":"92","title":"Liverpool","short_title":"LIV"},'
    '"goals":{"h":null,"a":null},"xG":{"h":null,"a":null},'
    '"datetime":"2023-08-13 16:30:00"}]'
)

PAGE = (
    "<html><body><script>\n"
    f"var datesData = JSON.parse('{_encode_understat(UNDERSTAT_JSON)}');\n"
    "</script></body></html>"
).encode("utf-8")


class TestParseLeaguePage:
    def test_url(self) -> None:
        assert league_url("E0", 2023) == "https://understat.com/league/EPL/2023"
        assert league_url("SP1", 2019) == "https://understat.com/league/La_liga/2019"

    def test_parses_results_only(self) -> None:
        records = parse_league_page(PAGE)
        assert len(records) == 2  # the unplayed fixture is excluded

    def test_fields(self) -> None:
        rec = parse_league_page(PAGE)[0]
        assert rec.home_team == "Burnley"
        assert rec.away_team == "Manchester City"
        assert rec.match_date == date(2023, 8, 12)
        assert rec.xg_home == Decimal("0.31")
        assert rec.xg_away == Decimal("2.84")
        assert (rec.home_goals, rec.away_goals) == (0, 3)

    def test_missing_datesdata_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_league_page(b"<html>nope</html>")


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


class TestTeamResolution:
    def test_exact_canonical(self, seeded_session: Session) -> None:
        team = resolve_understat_team(seeded_session, "Arsenal")
        assert team is not None and team.canonical_name == "Arsenal"

    def test_override_map(self, seeded_session: Session) -> None:
        # football-data alias "Man City" was canonicalized to "Manchester City";
        # Understat says "Manchester City" -> exact; but "Athletic Club" needs
        # the override map (seed the target team first).
        from pitchprob.data.repository import TeamRepository

        TeamRepository.resolve_or_create(
            seeded_session, source="football-data", alias="Ath Bilbao",
            country="Spain", canonical_name="Athletic Bilbao",
        )
        team = resolve_understat_team(seeded_session, "Athletic Club")
        assert team is not None and team.canonical_name == "Athletic Bilbao"

    def test_token_normalization_fallback(self, seeded_session: Session) -> None:
        from pitchprob.data.repository import TeamRepository

        TeamRepository.resolve_or_create(
            seeded_session, source="football-data", alias="Parma",
            country="Italy", canonical_name="Parma",
        )
        team = resolve_understat_team(seeded_session, "Parma Calcio 1913")
        assert team is not None and team.canonical_name == "Parma"

    def test_unknown_returns_none(self, seeded_session: Session) -> None:
        assert resolve_understat_team(seeded_session, "Atlantis United") is None

    def test_resolution_is_cached_as_alias(self, seeded_session: Session) -> None:
        resolve_understat_team(seeded_session, "Arsenal")
        from pitchprob.data.orm import TeamAlias

        alias = seeded_session.execute(
            select(TeamAlias).where(
                TeamAlias.source == "understat", TeamAlias.alias == "Arsenal"
            )
        ).scalar_one_or_none()
        assert alias is not None


class TestXgUpdateService:
    def test_updates_matching_fixtures(self, seeded_session: Session) -> None:
        downloader = FakeDownloader(
            {"https://understat.com/league/EPL/2023": PAGE}
        )
        service = XgUpdateService(session=seeded_session, downloader=downloader)
        report = service.update_league_season("E0", 2023)

        assert report.parsed == 2
        assert report.matched == 2
        assert report.unmatched_matches == 0
        assert not report.unmatched_teams

        burnley = seeded_session.execute(
            select(Match).where(Match.ft_away == 3)
        ).scalar_one()
        assert float(burnley.xg_home) == pytest.approx(0.31)
        assert float(burnley.xg_away) == pytest.approx(2.84)

    def test_idempotent(self, seeded_session: Session) -> None:
        downloader = FakeDownloader({"https://understat.com/league/EPL/2023": PAGE})
        service = XgUpdateService(session=seeded_session, downloader=downloader)
        service.update_league_season("E0", 2023)
        report2 = service.update_league_season("E0", 2023)
        assert report2.matched == 2

    def test_unknown_team_is_reported_not_crashed(self, seeded_session: Session) -> None:
        bogus_json = UNDERSTAT_JSON.replace("Burnley", "Atlantis United")
        page = (
            "<script>var datesData = JSON.parse('"
            + _encode_understat(bogus_json)
            + "');</script>"
        ).encode()
        service = XgUpdateService(
            session=seeded_session,
            downloader=FakeDownloader({"https://understat.com/league/EPL/2023": page}),
        )
        report = service.update_league_season("E0", 2023)
        assert report.matched == 1
        assert "Atlantis United" in report.unmatched_teams

    def test_date_tolerance_one_day(self, seeded_session: Session) -> None:
        """Understat datetimes can land on the neighbouring day (timezones)."""
        shifted = UNDERSTAT_JSON.replace("2023-08-12 19:30:00", "2023-08-13 00:30:00")
        page = (
            "<script>var datesData = JSON.parse('"
            + _encode_understat(shifted)
            + "');</script>"
        ).encode()
        service = XgUpdateService(
            session=seeded_session,
            downloader=FakeDownloader({"https://understat.com/league/EPL/2023": page}),
        )
        report = service.update_league_season("E0", 2023)
        assert report.matched == 2
