"""Market-book prediction service tests.

Seeds a small league through the real ingestion path, then asks for a full
market book. Values can't be hand-computed (they come from a fitted model),
so assertions target structure, coherence, and probability laws.
"""

import csv
import io

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Base
from pitchprob.data.service import IngestionService
from pitchprob.services.prediction import build_market_book

from ..data.test_football_data_adapter import BURNLEY_CITY, MODERN_COLUMNS
from ..data.test_normalize_and_service import FakeDownloader


def _league_csv(n_rounds: int = 6) -> bytes:
    """Small synthetic season in football-data format: 4 teams, deterministic
    but varied scores."""
    teams = ["Arsenal", "Chelsea", "Liverpool", "Everton"]
    rows = []
    day = 1
    for r in range(n_rounds):
        for i, home in enumerate(teams):
            for j, away in enumerate(teams):
                if home == away:
                    continue
                rows.append(
                    dict(
                        BURNLEY_CITY,
                        Date=f"{(day % 28) + 1:02d}/{8 + (day // 28):02d}/2023",
                        Time="15:00",
                        HomeTeam=home,
                        AwayTeam=away,
                        FTHG=str((i + r) % 4),
                        FTAG=str((j + r) % 3),
                    )
                )
                day += 1
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MODERN_COLUMNS, restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


@pytest.fixture()
def seeded_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        downloader = FakeDownloader(
            {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": _league_csv()}
        )
        IngestionService(session=session, downloader=downloader).ingest_season("E0", 2023)
        yield session


class TestMarketBook:
    def test_structure_and_probability_laws(self, seeded_session: Session) -> None:
        book = build_market_book(seeded_session, "E0", "Arsenal", "Chelsea")

        assert book["home_team"] == "Arsenal"
        assert book["league"] == "E0"

        for model_name in ("dixon_coles", "elo", "gbm", "ensemble"):
            p = book["markets"]["1x2"][model_name]
            assert p["home"] + p["draw"] + p["away"] == pytest.approx(1.0), model_name

        ou25 = book["markets"]["totals"]["2.5"]
        assert ou25["over"] + ou25["under"] == pytest.approx(1.0)

        btts = book["markets"]["btts"]
        assert btts["yes"] + btts["no"] == pytest.approx(1.0)

        assert book["expected_goals"]["home"] > 0
        assert len(book["markets"]["correct_score_top"]) == 5

        # goals markets derive from the Dixon-Coles matrix, so AH -0.5 home
        # must equal the DC match-win probability exactly
        dc_p = book["markets"]["1x2"]["dixon_coles"]
        ah = book["markets"]["asian_handicap"]["-0.5"]
        assert ah["home"] == pytest.approx(dc_p["home"], abs=1e-9)
        assert set(book["ensemble_weights"]) == {"dixon_coles", "elo", "gbm"}

    def test_unknown_team_raises(self, seeded_session: Session) -> None:
        with pytest.raises(UnknownTeamError):
            build_market_book(seeded_session, "E0", "Atlantis", "Chelsea")

    def test_ev_annotation_uses_ensemble_probabilities(self, seeded_session: Session) -> None:
        book = build_market_book(
            seeded_session, "E0", "Arsenal", "Chelsea",
            offered_1x2=(2.0, 3.5, 3.8),
        )
        value = book["value_analysis"]
        assert set(value) == {"home", "draw", "away"}
        p_home = book["markets"]["1x2"]["ensemble"]["home"]
        assert value["home"]["expected_value"] == pytest.approx(p_home * 2.0 - 1)
        assert value["home"]["kelly_fraction"] >= 0

    def test_fitted_models_are_cached_per_data_version(self, seeded_session: Session) -> None:
        from pitchprob.services.prediction import _cached_ensemble

        first = _cached_ensemble(seeded_session, "E0", 390.0)
        second = _cached_ensemble(seeded_session, "E0", 390.0)
        assert first is second

    def test_counts_markets_present(self, seeded_session: Session) -> None:
        book = build_market_book(seeded_session, "E0", "Arsenal", "Chelsea")
        counts = book["counts_markets"]
        assert "caveat" in counts

        corners = counts["corners"]
        assert corners["expected"]["home"] > 0
        assert corners["expected"]["total"] == pytest.approx(
            corners["expected"]["home"] + corners["expected"]["away"]
        )
        ou = corners["totals"]["9.5"]
        assert ou["over"] + ou["under"] == pytest.approx(1.0)

        cards = counts["cards"]
        assert cards["expected"]["total"] > 0
        ou_cards = cards["totals"]["3.5"]
        assert ou_cards["over"] + ou_cards["under"] == pytest.approx(1.0)
