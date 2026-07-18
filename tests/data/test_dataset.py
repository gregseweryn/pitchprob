"""DB → DataFrame read-model tests (SQLite, seeded through the real ingestion
service so canonical naming and odds layout match production)."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.dataset import (
    load_closing_odds_frame,
    load_matches_frame,
    load_odds_snapshot_frame,
)
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
            "corners_home", "corners_away",
            "yellows_home", "yellows_away", "reds_home", "reds_away",
            "xg_home", "xg_away",
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


class TestOddsSnapshotFrame:
    """The generalized loader behind bet-at-open evaluation (ADR 0010):
    any (bookmaker, market, snapshot) pivoted wide, with exact Decimal lines."""

    def test_1x2_opening_prices(self, seeded_session: Session) -> None:
        frame = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle",
            market="1x2", closing=False,
        )
        assert len(frame) == 2
        row = frame[frame["home_team"] == "Burnley"].iloc[0]
        assert row["price_home"] == pytest.approx(9.87)
        assert row["price_draw"] == pytest.approx(5.95)
        assert row["price_away"] == pytest.approx(1.31)
        assert "match_id" in frame.columns
        assert "line" not in frame.columns

    def test_1x2_closing_matches_legacy_loader(self, seeded_session: Session) -> None:
        new = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle",
            market="1x2", closing=True,
        )
        legacy = load_closing_odds_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle"
        )
        merged = new.merge(legacy, on=["date", "home_team", "away_team"])
        assert len(merged) == len(legacy) == 2
        for column in ("price_home", "price_draw", "price_away"):
            assert (merged[f"{column}_x"] == merged[f"{column}_y"]).all()

    def test_ou_opening_carries_exact_line(self, seeded_session: Session) -> None:
        frame = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle",
            market="ou", closing=False,
        )
        row = frame[frame["home_team"] == "Burnley"].iloc[0]
        assert row["line"] == Decimal("2.5")
        assert row["price_over"] == pytest.approx(1.65)
        assert row["price_under"] == pytest.approx(2.36)

    def test_ah_line_can_differ_between_open_and_close(
        self, seeded_session: Session
    ) -> None:
        opening = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle",
            market="ah", closing=False,
        )
        closing = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="pinnacle",
            market="ah", closing=True,
        )
        open_row = opening[opening["home_team"] == "Burnley"].iloc[0]
        close_row = closing[closing["home_team"] == "Burnley"].iloc[0]
        assert open_row["line"] == Decimal("1.75")
        assert open_row["price_home"] == pytest.approx(2.06)
        assert open_row["price_away"] == pytest.approx(1.87)
        assert close_row["line"] == Decimal("2")
        assert close_row["price_home"] == pytest.approx(1.89)
        assert close_row["price_away"] == pytest.approx(2.03)

    def test_incomplete_books_are_dropped(self, seeded_session: Session) -> None:
        # bet365 AH closing exists in the fixture; delete one leg and the
        # remaining half-book must not surface as a NaN row.
        from sqlalchemy import delete

        from pitchprob.data.orm import OddsQuote

        seeded_session.execute(
            delete(OddsQuote).where(
                OddsQuote.bookmaker == "bet365",
                OddsQuote.market == "ah",
                OddsQuote.is_closing.is_(True),
                OddsQuote.selection == "away",
            )
        )
        frame = load_odds_snapshot_frame(
            seeded_session, league_code="E0", bookmaker="bet365",
            market="ah", closing=True,
        )
        assert len(frame) == 0

    def test_unknown_market_raises(self, seeded_session: Session) -> None:
        with pytest.raises(ValueError):
            load_odds_snapshot_frame(seeded_session, market="btts")

    def test_missing_bookmaker_yields_typed_empty(
        self, seeded_session: Session
    ) -> None:
        frame = load_odds_snapshot_frame(
            seeded_session, bookmaker="nonexistent", market="ah", closing=False
        )
        assert len(frame) == 0
        assert list(frame.columns) == [
            "match_id", "date", "home_team", "away_team",
            "line", "price_home", "price_away",
        ]
