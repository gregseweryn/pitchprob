"""Backtest-harness orchestration tests (ADR 0010) on a synthetic seeded DB.

Sixty daily matches with a full odds book: pinnacle open/close for 1X2, OU 2.5
and AH, market-max open/close for 1X2 and OU (deliberately NO market-max AH —
that exercises the pinnacle fallback), and an AH closing line that moves away
from the opening line on every odd day (the CLV line-match rule).
"""

import csv
import io
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.data.orm import Base
from pitchprob.data.service import IngestionService
from pitchprob.services.harness import HarnessConfig, run_harness
from tests.data.test_football_data_adapter import MODERN_COLUMNS
from tests.data.test_normalize_and_service import FakeDownloader

TEAMS = ["Arsenal", "Burnley", "Chelsea", "Everton"]
START = date(2023, 8, 1)


def _row(i: int) -> dict[str, str]:
    when = START + timedelta(days=i)
    return {
        "Div": "E0",
        "Date": when.strftime("%d/%m/%Y"),
        "Time": "15:00",
        "HomeTeam": TEAMS[i % 4],
        "AwayTeam": TEAMS[(i + 1) % 4],
        "FTHG": str(i % 3),
        "FTAG": "1",
        "PSH": "2.60", "PSD": "3.40", "PSA": "2.90",
        "PSCH": "2.50", "PSCD": "3.30", "PSCA": "3.10",
        "MaxH": "2.70", "MaxD": "3.50", "MaxA": "3.00",
        "MaxCH": "2.65", "MaxCD": "3.45", "MaxCA": "3.05",
        "P>2.5": "1.90", "P<2.5": "2.00",
        "PC>2.5": "1.85", "PC<2.5": "2.05",
        "Max>2.5": "1.95", "Max<2.5": "2.05",
        "AHh": "-0.25",
        "PAHH": "1.95", "PAHA": "1.95",
        "AHCh": "-0.25" if i % 2 == 0 else "-0.50",
        "PCAHH": "1.90", "PCAHA": "2.00",
    }


def _csv(rows: list[dict[str, str]]) -> bytes:
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
        payload = _csv([_row(i) for i in range(60)])
        downloader = FakeDownloader(
            {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": payload}
        )
        IngestionService(session=session, downloader=downloader).ingest_season("E0", 2023)
        yield session


def _config(**overrides: object) -> HarnessConfig:
    defaults: dict[str, object] = {
        "league": "E0",
        "start": date(2023, 9, 1),
        "model": "poisson",
        "refit_days": 7,
        "min_train_matches": 10,
        "ev_threshold": -1.0,  # bet everything: assertions target plumbing
        "selector": "naive",
        "at": "open",
        "markets": ("1x2",),
    }
    defaults.update(overrides)
    return HarnessConfig(**defaults)  # type: ignore[arg-type]


class TestOpenMode:
    def test_prices_come_from_opening_best_book(self, seeded_session: Session) -> None:
        result = run_harness(seeded_session, _config())
        bets = result.bets
        assert bets is not None and len(bets) > 0
        assert set(bets.loc[bets["selection"] == "home", "price"]) == {2.70}
        assert set(bets.loc[bets["selection"] == "draw", "price"]) == {3.50}
        assert set(bets.loc[bets["selection"] == "away", "price"]) == {3.00}

    def test_clv_is_open_price_against_closing_fair(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(seeded_session, _config())
        shin = remove_overround_shin([2.50, 3.30, 3.10])
        bets = result.bets
        home = bets[bets["selection"] == "home"].iloc[0]
        assert home["clv"] == pytest.approx(2.70 * shin[0] - 1.0)

    def test_benchmark_against_opening_line_reported(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(seeded_session, _config())
        block = result.metrics["benchmark_open_subset"]
        assert block["n"] == result.metrics["n_predictions"]
        assert 0.0 < block["open_log_loss"] < 2.0
        assert 0.0 < block["model_log_loss"] < 2.0

    def test_roi_confidence_interval_reported(self, seeded_session: Session) -> None:
        result = run_harness(seeded_session, _config())
        ci = result.metrics["staking_flat"]["roi_ci"]
        assert ci["lo"] <= result.metrics["staking_flat"]["roi"] <= ci["hi"]

    def test_sharp_clv_reported_for_every_selector(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(seeded_session, _config())  # naive selector
        block = result.metrics["staking_flat"]
        shin_close = remove_overround_shin([2.50, 3.30, 3.10])
        bets = result.bets
        home = bets[bets["selection"] == "home"].iloc[0]
        # pinnacle open 2.60 against pinnacle close fair — not the best price
        assert home["clv_sharp"] == pytest.approx(2.60 * shin_close[0] - 1.0)
        assert "mean_clv_sharp" in block


class TestAuxiliaryMarkets:
    def test_ou_bets_settle_by_totals_rules(self, seeded_session: Session) -> None:
        result = run_harness(seeded_session, _config(markets=("1x2", "ou")))
        bets = result.bets
        ou = bets[bets["market"] == "ou"]
        assert len(ou) > 0
        assert set(ou.loc[ou["selection"] == "over", "price"]) == {1.95}
        # Day i=32: FTHG=2, FTAG=1, total 3 > 2.5 — over wins at 1.95.
        day = START + timedelta(days=32)
        over = ou[(ou["date"] == day) & (ou["selection"] == "over")].iloc[0]
        under = ou[(ou["date"] == day) & (ou["selection"] == "under")].iloc[0]
        assert over["gross_return"] == pytest.approx(1.95)
        assert under["gross_return"] == pytest.approx(0.0)

    def test_ah_falls_back_to_pinnacle_prices(self, seeded_session: Session) -> None:
        # No market-max AH is seeded: prices must be pinnacle's 1.95.
        result = run_harness(seeded_session, _config(markets=("ah",)))
        ah = result.bets[result.bets["market"] == "ah"]
        assert len(ah) > 0
        assert set(ah["price"]) == {1.95}

    def test_ah_quarter_line_draw_is_a_half_loss(self, seeded_session: Session) -> None:
        # Day i=31: FTHG=31%3=1, FTAG=1 — a draw. Home at -0.25 splits into
        # 0 (push) and -0.5 (loss): gross 0.5 regardless of price.
        result = run_harness(seeded_session, _config(markets=("ah",)))
        ah = result.bets[result.bets["market"] == "ah"]
        day = START + timedelta(days=31)
        home = ah[(ah["date"] == day) & (ah["selection"] == "home")].iloc[0]
        assert home["gross_return"] == pytest.approx(0.5)

    def test_ah_clv_requires_matching_closing_line(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(seeded_session, _config(markets=("ah",)))
        ah = result.bets[result.bets["market"] == "ah"]
        days_since = ah["date"].map(lambda d: (d - START).days)
        moved = ah[days_since % 2 == 1]
        held = ah[days_since % 2 == 0]
        assert len(moved) > 0 and len(held) > 0
        assert moved["clv"].isna().all()
        assert held["clv"].notna().all()
        coverage = result.metrics["clv_coverage"]["ah"]
        assert 0.3 < coverage < 0.7

    def test_close_mode_rejects_auxiliary_markets(
        self, seeded_session: Session
    ) -> None:
        with pytest.raises(ValueError):
            run_harness(seeded_session, _config(at="close", markets=("1x2", "ou")))

    def test_error_analysis_reported_per_market(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(seeded_session, _config(markets=("1x2", "ou")))
        analysis = result.metrics["error_analysis"]
        market_rows = analysis["market"]
        assert {row["segment"] for row in market_rows} == {"1x2", "ou"}
        for row in market_rows:
            assert isinstance(row["clv_significant"], bool)
            assert row["n"] >= 30


class TestMetaSelector:
    def test_meta_gate_bets_carry_predictions_and_sharp_clv(
        self, seeded_session: Session
    ) -> None:
        result = run_harness(
            seeded_session,
            _config(
                selector="meta",
                markets=("1x2", "ou"),
                meta_min_train=30,
                meta_refit_days=7,
                meta_buffer=-10.0,  # gate everything scoreable: plumbing test
            ),
        )
        bets = result.bets
        assert bets is not None and len(bets) > 0
        assert bets["predicted_clv"].notna().all()
        # sharp CLV per bet: pinnacle open price against pinnacle close fair
        shin_close = remove_overround_shin([2.50, 3.30, 3.10])
        home_1x2 = bets[(bets["market"] == "1x2") & (bets["selection"] == "home")]
        assert home_1x2["clv_sharp"].iloc[0] == pytest.approx(
            2.60 * shin_close[0] - 1.0
        )
        block = result.metrics["staking_flat"]
        assert "mean_clv_sharp" in block

    def test_meta_requires_open_snapshot(self, seeded_session: Session) -> None:
        with pytest.raises(ValueError):
            run_harness(seeded_session, _config(selector="meta", at="close"))


class TestCloseModeAndValidation:
    def test_close_mode_uses_closing_prices(self, seeded_session: Session) -> None:
        result = run_harness(
            seeded_session, _config(at="close", markets=("1x2",))
        )
        bets = result.bets
        assert set(bets.loc[bets["selection"] == "home", "price"]) == {2.65}
        assert "benchmark_subset" in result.metrics
        assert "staking_flat" in result.metrics

    def test_league_all_reports_per_league(self, seeded_session: Session) -> None:
        result = run_harness(seeded_session, _config(league="all"))
        per_league = result.metrics["per_league"]
        assert per_league["E0"]["n"] == result.metrics["n_predictions"]

    def test_unknown_at_raises(self, seeded_session: Session) -> None:
        with pytest.raises(ValueError):
            run_harness(seeded_session, _config(at="noon"))

    def test_unknown_market_raises(self, seeded_session: Session) -> None:
        with pytest.raises(ValueError):
            run_harness(seeded_session, _config(markets=("btts",)))

    def test_unknown_ablation_raises(self, seeded_session: Session) -> None:
        with pytest.raises(ValueError):
            run_harness(seeded_session, _config(ablate="weather"))


class TestCalibrationDefault:
    """Audit finding A9: an `ensemble-cal` run without --calibration used to
    default to isotonic — the method M4.5 measured as catastrophically
    overfitting ~190-match calibration holdouts. Temperature is the
    documented default and must stay the coded one."""

    def test_harness_config_defaults_to_temperature(self) -> None:
        assert HarnessConfig(start=date(2023, 9, 1)).calibration == "temperature"

    def test_model_factory_defaults_to_temperature(self) -> None:
        from pitchprob.services.harness import build_model_factory

        model = build_model_factory("ensemble-cal", half_life=390.0)()
        assert model.method == "temperature"
