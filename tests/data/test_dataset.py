"""DB → DataFrame read-model tests (SQLite, seeded through the real ingestion
service so canonical naming and odds layout match production)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.dataset import load_closing_odds_frame, load_matches_frame
from pitchprob.data.orm import Base
from pitchprob.data.service import IngestionService

from .test_normalize_and_service import ARSENAL_FOREST, BURNLEY_CITY, FakeDownloader, _modern_csv


@pytest.fixture()
def seeded_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        payload = _modern_csv([BURNLEY_CITY, ARSENAL_FOREST])
        downloader = FakeDownloader(
            {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
        )
        IngestionService(session=session, downloader=downloader).ingest_season("E0", 2023)
        yield session


class TestMatchesFrame:
    def test_columns_and_canonical_names(self, seeded_session: Session) -> None:
        frame = load_matches_frame(seeded_session, league_code="E0")
        assert list(frame.columns) == ["date", "home_team", "away_team", "ft_home", "ft_away"]
        assert len(frame) == 2
        assert "Manchester City" in set(frame["away_team"])
        assert "Nottingham Forest" in set(frame["away_team"])

    def test_league_filter_excludes_other_leagues(self, seeded_session: Session) -> None:
        frame = load_matches_frame(seeded_session, league_code="SP1")
        assert len(frame) == 0

    def test_sorted_by_date(self, seeded_session: Session) -> None:
        frame = load_matches_frame(seeded_session, league_code="E0")
        assert list(frame["date"]) == sorted(frame["date"])


class TestMatchesFrameWithStats:
    def test_stats_columns_present(self, seeded_session: Session) -> None:
        frame = load_matches_frame(seeded_session, league_code="E0", include_stats=True)
        expected_extra = {
            "league", "shots_home", "shots_away",
            "shots_on_target_home", "shots_on_target_away",
            "corners_home", "corners_away", "xg_home", "xg_away",
        }
        assert expected_extra <= set(frame.columns)
        assert set(frame["league"]) == {"E0"}
        # fixture CSV carries shots; xG has not been ingested -> NaN floats
        assert frame["shots_home"].notna().all()
        assert frame["xg_home"].isna().all()

    def test_default_excludes_stats(self, seeded_session: Session) -> None:
        frame = load_matches_frame(seeded_session, league_code="E0")
        assert "xg_home" not in frame.columns
        assert list(frame.columns) == ["date", "home_team", "away_team", "ft_home", "ft_away"]


class TestClosingOddsFrame:
    def test_pivoted_1x2_closing_prices(self, seeded_session: Session) -> None:
        frame = load_closing_odds_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle"
        )
        assert len(frame) == 2
        row = frame[frame["home_team"] == "Burnley"].iloc[0]
        assert row["price_home"] == pytest.approx(9.10)
        assert row["price_draw"] == pytest.approx(5.80)
        assert row["price_away"] == pytest.approx(1.34)

    def test_missing_bookmaker_yields_empty(self, seeded_session: Session) -> None:
        frame = load_closing_odds_frame(
            seeded_session, league_code="E0", bookmaker="nonexistent"
        )
        assert len(frame) == 0
