"""Market-book prediction service tests.

Seeds a small league through the real ingestion path, then asks for a full
market book. Fitted-model values can't be hand-computed, so those assertions
target structure, coherence, and probability laws; the value-analysis math is
pure and gets hand-computed cases directly.
"""

import csv
import io
import math
from typing import ClassVar

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Base
from pitchprob.data.service import IngestionService
from pitchprob.services.prediction import build_market_book, value_analysis_1x2

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

    def test_value_analysis_runs_on_the_betting_path(self, seeded_session: Session) -> None:
        """EV comes from Dixon-Coles blended with the Shin-de-margined offered
        prices (ADR 0006) — never raw ensemble EV (the M2 longshot trap;
        audit finding A3)."""
        offered = (2.0, 3.5, 3.8)
        book = build_market_book(
            seeded_session, "E0", "Arsenal", "Chelsea", offered_1x2=offered,
        )
        value = book["value_analysis"]
        assert set(value) == {"home", "draw", "away"}

        dc = book["markets"]["1x2"]["dixon_coles"]
        shin = dict(
            zip(("home", "draw", "away"), remove_overround_shin(offered), strict=True)
        )
        for selection, price in zip(("home", "draw", "away"), offered, strict=True):
            entry = value[selection]
            assert entry["offered_price"] == price
            assert entry["model_probability"] == pytest.approx(dc[selection])
            assert entry["market_probability"] == pytest.approx(shin[selection])
            lo, hi = sorted((dc[selection], shin[selection]))
            assert lo - 1e-9 <= entry["p_bet"] <= hi + 1e-9
            assert entry["expected_value"] == pytest.approx(entry["p_bet"] * price - 1)
            assert entry["fair_price"] == pytest.approx(1.0 / entry["p_bet"])
            assert entry["kelly_fraction"] >= 0

    def test_fitted_models_are_cached_per_data_version(self, seeded_session: Session) -> None:
        from pitchprob.services.prediction import _cached_ensemble

        first = _cached_ensemble(seeded_session, "E0", 390.0)
        second = _cached_ensemble(seeded_session, "E0", 390.0)
        assert first is second


class TestValueAnalysis1x2:
    """Hand-computed cases for the pure value-analysis math (finding A3).

    Prices (2.0, 4.0, 4.0) form a fair book (implied 0.5 + 0.25 + 0.25 = 1),
    so Shin de-margin returns the implied probabilities unchanged and the
    market anchor is exact by hand. At blend weight 0.5 the log-linear pool
    reduces to a normalized geometric mean:
    p_bet = sqrt(p*q) / (sqrt(p*q) + sqrt((1-p)*(1-q))).
    """

    MODEL: ClassVar[dict[str, float]] = {"home": 0.6, "draw": 0.25, "away": 0.15}
    PRICES = (2.0, 4.0, 4.0)

    def test_hand_computed_home(self) -> None:
        value = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=0.5)
        home = value["home"]
        # p_bet = sqrt(0.6*0.5) / (sqrt(0.6*0.5) + sqrt(0.4*0.5))
        p_bet = math.sqrt(0.3) / (math.sqrt(0.3) + math.sqrt(0.2))
        assert home["model_probability"] == pytest.approx(0.6)
        assert home["market_probability"] == pytest.approx(0.5)
        assert home["p_bet"] == pytest.approx(p_bet)
        assert home["p_bet"] == pytest.approx(0.5505102572)  # digits by hand
        assert home["expected_value"] == pytest.approx(p_bet * 2.0 - 1)
        assert home["expected_value"] == pytest.approx(0.1010205144)
        assert home["fair_price"] == pytest.approx(1.0 / p_bet)
        # quarter-Kelly: 0.25 * edge / (price - 1) = 0.25 * 0.1010205144 / 1.0
        assert home["kelly_fraction"] == pytest.approx(0.0252551286)

    def test_hand_computed_draw_model_equals_market(self) -> None:
        # model 0.25 = market 0.25: any blend returns 0.25; EV = 0.25*4 - 1 = 0
        value = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=0.5)
        draw = value["draw"]
        assert draw["p_bet"] == pytest.approx(0.25)
        assert draw["expected_value"] == pytest.approx(0.0, abs=1e-9)
        assert draw["kelly_fraction"] == pytest.approx(0.0, abs=1e-9)

    def test_hand_computed_away_negative_edge(self) -> None:
        value = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=0.5)
        away = value["away"]
        # p_bet = sqrt(0.15*0.25) / (sqrt(0.15*0.25) + sqrt(0.85*0.75))
        p_bet = math.sqrt(0.0375) / (math.sqrt(0.0375) + math.sqrt(0.6375))
        assert away["p_bet"] == pytest.approx(p_bet)
        assert away["expected_value"] == pytest.approx(p_bet * 4.0 - 1)
        assert away["expected_value"] < 0
        assert away["kelly_fraction"] == 0.0  # no stake on a negative edge

    def test_weight_one_reduces_to_model_naive_ev(self) -> None:
        value = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=1.0)
        assert value["home"]["p_bet"] == pytest.approx(0.6)
        assert value["home"]["expected_value"] == pytest.approx(0.6 * 2.0 - 1)

    def test_default_weight_is_adr_0006_blend(self) -> None:
        # the default must anchor to the market (0.4), not naive model EV
        default = value_analysis_1x2(self.MODEL, self.PRICES)
        explicit = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=0.4)
        assert default["home"]["p_bet"] == pytest.approx(explicit["home"]["p_bet"])
        naive = value_analysis_1x2(self.MODEL, self.PRICES, blend_weight=1.0)
        assert default["home"]["p_bet"] != pytest.approx(naive["home"]["p_bet"])

    def test_margined_book_anchors_to_shin(self) -> None:
        # a real book with margin: the anchor must be Shin de-margined, which
        # is hand-tested in tests/betting/test_odds_math.py
        prices = (1.9, 3.6, 3.7)
        value = value_analysis_1x2(self.MODEL, prices, blend_weight=0.5)
        shin = remove_overround_shin(prices)
        assert value["home"]["market_probability"] == pytest.approx(shin[0])
        assert value["draw"]["market_probability"] == pytest.approx(shin[1])
        assert value["away"]["market_probability"] == pytest.approx(shin[2])


class TestCountsMarkets:
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
