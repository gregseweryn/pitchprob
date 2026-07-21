"""Raw-Understat snapshot archive (resilience task, audit 2026-07).

After the FBref/Opta cutoff Understat is the only free xG source for the
top-5 leagues; the archive must be append-only and deduplicated so a source
outage can never take the historical raw material with it, and weekly
re-runs over finished seasons never accrete duplicate files.
"""

import gzip
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, Match
from pitchprob.data.service import IngestionService
from pitchprob.data.understat_archive import (
    latest_snapshot,
    read_snapshot,
    snapshot_payload,
)
from pitchprob.data.xg_service import XgUpdateService

from .test_normalize_and_service import (
    ARSENAL_FOREST,
    BURNLEY_CITY,
    FakeDownloader,
    _modern_csv,
)
from .test_understat import API_URL, _api_payload

T0 = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


class TestSnapshotPayload:
    def test_writes_dated_gzip_that_round_trips(self, tmp_path) -> None:
        path = snapshot_payload(tmp_path, "E0", 2023, b'{"dates": []}', now=T0)
        assert path is not None
        assert path.name == "E0_2023_20260720T080000Z.json.gz"
        assert gzip.decompress(path.read_bytes()) == b'{"dates": []}'

    def test_identical_content_is_not_stored_twice(self, tmp_path) -> None:
        first = snapshot_payload(tmp_path, "E0", 2023, b"same", now=T0)
        second = snapshot_payload(tmp_path, "E0", 2023, b"same", now=T1)
        assert first is not None and second is None
        assert list(tmp_path.iterdir()) == [first]

    def test_changed_content_appends_and_preserves_history(self, tmp_path) -> None:
        first = snapshot_payload(tmp_path, "E0", 2023, b"v1", now=T0)
        second = snapshot_payload(tmp_path, "E0", 2023, b"v2", now=T1)
        assert first is not None and second is not None
        assert read_snapshot(first) == b"v1"  # append-only: v1 survives
        assert read_snapshot(second) == b"v2"
        assert latest_snapshot(tmp_path, "E0", 2023) == second

    def test_league_seasons_are_deduplicated_independently(self, tmp_path) -> None:
        assert snapshot_payload(tmp_path, "E0", 2023, b"same", now=T0) is not None
        assert snapshot_payload(tmp_path, "SP1", 2023, b"same", now=T0) is not None
        assert snapshot_payload(tmp_path, "E0", 2024, b"same", now=T0) is not None


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


class TestXgServiceArchiving:
    def test_update_snapshots_the_raw_response(
        self, seeded_session: Session, tmp_path
    ) -> None:
        service = XgUpdateService(
            session=seeded_session,
            downloader=FakeDownloader({API_URL: _api_payload()}),
            snapshot_dir=tmp_path,
        )
        report = service.update_league_season("E0", 2023)
        assert report.matched > 0  # annotation still works
        stored = latest_snapshot(tmp_path, "E0", 2023)
        assert stored is not None
        assert read_snapshot(stored) == _api_payload()

    def test_unparseable_payload_is_archived_before_the_parser_raises(
        self, seeded_session: Session, tmp_path
    ) -> None:
        service = XgUpdateService(
            session=seeded_session,
            downloader=FakeDownloader({API_URL: b"<html>layout changed</html>"}),
            snapshot_dir=tmp_path,
        )
        with pytest.raises(ValueError):
            service.update_league_season("E0", 2023)
        stored = latest_snapshot(tmp_path, "E0", 2023)
        assert stored is not None
        assert read_snapshot(stored) == b"<html>layout changed</html>"

    def test_no_snapshot_dir_means_no_archiving(
        self, seeded_session: Session, tmp_path
    ) -> None:
        service = XgUpdateService(
            session=seeded_session,
            downloader=FakeDownloader({API_URL: _api_payload()}),
        )
        service.update_league_season("E0", 2023)
        assert list(tmp_path.iterdir()) == []
        # sanity: the annotation itself really ran
        assert (
            seeded_session.execute(
                select(Match).where(Match.xg_home.is_not(None))
            ).scalars().first()
            is not None
        )
