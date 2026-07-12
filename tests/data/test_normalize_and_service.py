"""Tests for team-name canonicalization and the ingestion service."""

import csv
import io
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from pitchprob.data.normalize import canonical_team_name
from pitchprob.data.orm import Base, Match, OddsQuote, Team
from pitchprob.data.service import IngestionService

from .test_football_data_adapter import BURNLEY_CITY, MODERN_COLUMNS


class TestCanonicalTeamName:
    def test_known_abbreviations_are_expanded(self) -> None:
        assert canonical_team_name("Man City") == "Manchester City"
        assert canonical_team_name("Man United") == "Manchester United"
        assert canonical_team_name("Nott'm Forest") == "Nottingham Forest"
        assert canonical_team_name("Ath Madrid") == "Atletico Madrid"
        assert canonical_team_name("Paris SG") == "Paris Saint-Germain"

    def test_unknown_names_pass_through(self) -> None:
        assert canonical_team_name("Arsenal") == "Arsenal"


def _modern_csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MODERN_COLUMNS, restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


ARSENAL_FOREST = dict(
    BURNLEY_CITY,
    Date="12/08/2023",
    Time="15:00",
    HomeTeam="Arsenal",
    AwayTeam="Nott'm Forest",
    FTHG="2",
    FTAG="1",
    FTR="H",
)


class FakeDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def get(self, url: str) -> bytes:
        self.calls.append(url)
        return self.payloads[url]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_ingest_season_end_to_end(session: Session) -> None:
    payload = _modern_csv([BURNLEY_CITY, ARSENAL_FOREST])
    downloader = FakeDownloader(
        {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
    )
    service = IngestionService(session=session, downloader=downloader)

    report = service.ingest_season("E0", 2023)

    assert report.matches_inserted == 2
    assert report.matches_updated == 0
    assert report.quarantined == 0
    assert report.odds_quotes > 0

    # canonical names applied, aliases preserved
    names = set(session.execute(select(Team.canonical_name)).scalars())
    assert "Manchester City" in names
    assert "Nottingham Forest" in names
    assert "Arsenal" in names

    n_matches = session.execute(select(func.count()).select_from(Match)).scalar_one()
    assert n_matches == 2


def test_ingest_season_is_idempotent(session: Session) -> None:
    payload = _modern_csv([BURNLEY_CITY, ARSENAL_FOREST])
    downloader = FakeDownloader(
        {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
    )
    service = IngestionService(session=session, downloader=downloader)

    service.ingest_season("E0", 2023)
    odds_before = session.execute(select(func.count()).select_from(OddsQuote)).scalar_one()

    report2 = service.ingest_season("E0", 2023)
    assert report2.matches_inserted == 0
    assert report2.matches_updated == 2

    n_matches = session.execute(select(func.count()).select_from(Match)).scalar_one()
    odds_after = session.execute(select(func.count()).select_from(OddsQuote)).scalar_one()
    assert n_matches == 2
    assert odds_after == odds_before


def test_ingest_unknown_league_raises(session: Session) -> None:
    service = IngestionService(session=session, downloader=FakeDownloader({}))
    with pytest.raises(KeyError):
        service.ingest_season("XX9", 2023)


def test_match_date_derives_season_membership(session: Session) -> None:
    """A May fixture belongs to the season that started the previous year."""
    may_row = dict(BURNLEY_CITY, Date="19/05/2024", Time="16:00")
    payload = _modern_csv([may_row])
    downloader = FakeDownloader(
        {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
    )
    service = IngestionService(session=session, downloader=downloader)
    service.ingest_season("E0", 2023)

    match = session.execute(select(Match)).scalar_one()
    assert match.match_date == date(2024, 5, 19)
