"""Tests for team-name canonicalization and the ingestion service."""

import csv
import io
import typing
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from pitchprob.data.normalize import (
    canonical_team_name,
    odds_api_canonical,
    suggest_canonical,
)
from pitchprob.data.orm import Base, Match, OddsQuote, Team
from pitchprob.data.service import HttpDownloader, IngestionService

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


class TestOddsApiCanonical:
    def test_known_tape_spellings_map_to_canonical(self) -> None:
        assert odds_api_canonical("Brighton and Hove Albion") == "Brighton"
        assert odds_api_canonical("AFC Bournemouth") == "Bournemouth"
        assert odds_api_canonical("Inter Milan") == "Inter"
        assert odds_api_canonical("Paris Saint Germain") == "Paris Saint-Germain"

    def test_full_names_already_canonical_pass_through(self) -> None:
        # The Odds API mostly uses full club names, which *are* canonical.
        assert odds_api_canonical("Wolverhampton Wanderers") == (
            "Wolverhampton Wanderers"
        )
        assert odds_api_canonical("Arsenal") == "Arsenal"


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


class TestHttpDownloaderRefresh:
    """The weekly-refresh contract (season runbook): a cache hit must be
    the default — past seasons never change and WSL's TLS flakes are real —
    and `refresh=True` must actually reach the network, because the current
    season's file grows every round and a permanent cache would serve
    August forever."""

    URL = "https://www.football-data.co.uk/mmz4281/2627/E0.csv"

    def _patch_get(self, monkeypatch, calls: list[str], body: bytes) -> None:
        class FakeResponse:
            content = body

            def raise_for_status(self) -> None:
                return None

        def fake_get(url: str, **kwargs):
            calls.append(url)
            return FakeResponse()

        monkeypatch.setattr("pitchprob.data.service.httpx.get", fake_get)

    def test_the_cache_serves_without_touching_the_network(
        self, tmp_path, monkeypatch
    ) -> None:
        calls: list[str] = []
        self._patch_get(monkeypatch, calls, b"round one")
        downloader = HttpDownloader(cache_dir=tmp_path)
        assert downloader.get(self.URL) == b"round one"
        assert downloader.get(self.URL) == b"round one"
        assert len(calls) == 1  # second read came from disk

    def test_refresh_bypasses_the_cache_and_rewrites_it(
        self, tmp_path, monkeypatch
    ) -> None:
        calls: list[str] = []
        self._patch_get(monkeypatch, calls, b"round one")
        HttpDownloader(cache_dir=tmp_path).get(self.URL)

        self._patch_get(monkeypatch, calls, b"rounds one and two")
        refreshed = HttpDownloader(cache_dir=tmp_path, refresh=True)
        assert refreshed.get(self.URL) == b"rounds one and two"
        assert len(calls) == 2
        # and the *new* content is what later cached reads serve
        assert HttpDownloader(cache_dir=tmp_path).get(self.URL) == (
            b"rounds one and two"
        )
        assert len(calls) == 2


class TestSuggestCanonical:
    """Season procedure: `pick settle` lists unmatched tape names; the
    suggester ranks canonical candidates so extending _ODDS_API_OVERRIDES
    is a lookup, not a hunt. Advisory output — the operator confirms."""

    CANDIDATES: typing.ClassVar[list[str]] = [
        "Wolves", "Man City", "Brighton", "Nott'm Forest", "Newcastle",
        "Arsenal", "Real Betis", "Ath Bilbao",
    ]

    def test_shared_tokens_beat_string_similarity(self) -> None:
        ranked = suggest_canonical("Manchester City", self.CANDIDATES)
        assert ranked[0][0] == "Man City"

    def test_apostrophes_and_noise_words_do_not_block_a_match(self) -> None:
        ranked = suggest_canonical("Nottingham Forest FC", self.CANDIDATES)
        assert ranked[0][0] == "Nott'm Forest"

    def test_a_name_with_no_plausible_candidate_returns_nothing(self) -> None:
        assert suggest_canonical("Deportivo Riestra", self.CANDIDATES) == []

    def test_at_most_three_suggestions(self) -> None:
        ranked = suggest_canonical("Newcastle United", self.CANDIDATES, limit=3)
        assert 1 <= len(ranked) <= 3
        assert ranked[0][0] == "Newcastle"

    def test_scores_are_descending_and_bounded(self) -> None:
        ranked = suggest_canonical("Real Betis Balompie", self.CANDIDATES)
        scores = [score for _name, score in ranked]
        assert scores == sorted(scores, reverse=True)
        assert all(0.0 < score <= 1.0 for score in scores)
