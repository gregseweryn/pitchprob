"""CLI smoke tests: ingest → train → predict → backtest against a temp SQLite
database with a faked downloader (fully offline)."""

import pytest
from typer.testing import CliRunner

import pitchprob.cli.main as cli_main
from pitchprob.core import db
from pitchprob.core.config import get_settings
from pitchprob.data.orm import Base

from ..services.test_prediction import _league_csv

runner = CliRunner()


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PITCHPROB_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("PITCHPROB_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    db.reset()
    Base.metadata.create_all(db.get_engine())

    payload = _league_csv(n_rounds=8)

    class OfflineDownloader:
        def get(self, url: str) -> bytes:
            if "2324/E0.csv" in url:
                return payload
            raise AssertionError(f"unexpected download: {url}")

    monkeypatch.setattr(cli_main, "_make_downloader", lambda: OfflineDownloader())
    yield
    db.reset()
    get_settings.cache_clear()


def test_ingest_reports_counts(cli_env) -> None:
    result = runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    assert result.exit_code == 0, result.output
    assert "E0" in result.output
    assert "matches" in result.output


def test_train_persists_model_run(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(cli_main.app, ["train", "--league", "E0"])
    assert result.exit_code == 0, result.output
    assert "dixon_coles" in result.output

    from sqlalchemy import func, select

    from pitchprob.data.orm import ModelRun

    with db.session_scope() as session:
        n = session.execute(select(func.count()).select_from(ModelRun)).scalar_one()
    assert n >= 1


def test_predict_prints_market_book(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(
        cli_main.app,
        ["predict", "--league", "E0", "--home", "Arsenal", "--away", "Chelsea",
         "--odds", "2.0,3.5,3.8"],
    )
    assert result.exit_code == 0, result.output
    assert "1X2" in result.output
    assert "Over" in result.output
    assert "Expected value" in result.output or "EV" in result.output


def test_predict_unknown_team_fails_cleanly(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(
        cli_main.app, ["predict", "--league", "E0", "--home", "Atlantis", "--away", "Chelsea"]
    )
    assert result.exit_code != 0
    assert "Atlantis" in result.output


def test_backtest_reports_metrics(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(
        cli_main.app,
        ["backtest", "--league", "E0", "--start", "2023-10-01",
         "--min-train-matches", "30", "--refit-days", "14"],
    )
    assert result.exit_code == 0, result.output
    assert "log_loss" in result.output
    assert "rps" in result.output
    # calibration must be reported for every class, not just home (M3 lesson:
    # calibration where you display is not calibration where you bet)
    assert "ece_home" in result.output
    assert "ece_draw" in result.output
    assert "ece_away" in result.output


def test_backtest_blended_selector(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(
        cli_main.app,
        ["backtest", "--league", "E0", "--start", "2023-10-01",
         "--min-train-matches", "30", "--refit-days", "14",
         "--selector", "blended", "--blend-weight", "0.4"],
    )
    assert result.exit_code == 0, result.output
    assert '"selector": "blended"' in result.output


def test_backtest_rejects_unknown_selector(cli_env) -> None:
    result = runner.invoke(
        cli_main.app, ["backtest", "--league", "E0", "--selector", "bogus"]
    )
    assert result.exit_code != 0


def test_coupon_with_explicit_fixtures(cli_env) -> None:
    runner.invoke(
        cli_main.app, ["ingest", "--league", "E0", "--from-year", "2023", "--to-year", "2023"]
    )
    result = runner.invoke(
        cli_main.app,
        ["coupon", "--tier", "high_risk",
         "--fixture", "Arsenal,Chelsea,E0",
         "--fixture", "Liverpool,Everton,E0",
         "--max-legs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert ("coupons" in result.output) or ("No coupons" in result.output)
    if "joint probability" in result.output:
        assert "+ " in result.output  # reasons rendered


def test_coupon_rejects_unknown_tier(cli_env) -> None:
    result = runner.invoke(
        cli_main.app, ["coupon", "--tier", "yolo", "--fixture", "A,B,E0"]
    )
    assert result.exit_code != 0


def test_record_odds_offline(cli_env, monkeypatch) -> None:
    from datetime import UTC, datetime

    from pitchprob.data.adapters.odds_api import OddsSnapshot, OddsTickRecord

    class FakeOddsClient:
        def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot:
            tick = OddsTickRecord(
                sport_key=sport_key,
                event_id="ev9",
                commence_time=datetime(2026, 8, 15, 14, 0, tzinfo=UTC),
                home_team="Arsenal",
                away_team="Leeds United",
                bookmaker="pinnacle",
                market="1x2",
                selection="home",
                line=None,
                price=1.65,
            )
            return OddsSnapshot(ticks=[tick], requests_remaining=490)

    monkeypatch.setenv("PITCHPROB_ODDS_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(cli_main, "_make_odds_client", lambda key: FakeOddsClient())
    result = runner.invoke(cli_main.app, ["record-odds", "--league", "E0"])
    assert result.exit_code == 0, result.output
    assert "recorded 1 ticks" in result.output
    assert "pinnacle" in result.output
    assert "490" in result.output


def test_record_odds_http_error_never_prints_the_key(cli_env, monkeypatch) -> None:
    """Audit finding A4: a 401/429 from The Odds API used to escape as a
    traceback whose URL carried the apiKey. The CLI must exit 1 with a
    scrubbed message instead."""
    import httpx

    class FailingOddsClient:
        def fetch_odds(self, sport_key: str, *, markets: list[str]):
            request = httpx.Request(
                "GET", "https://api.the-odds-api.com/v4/sports/soccer_epl/odds",
                params={"apiKey": "SECRET-KEY-123"},
            )
            response = httpx.Response(
                429, headers={"x-requests-remaining": "0"}, request=request
            )
            response.raise_for_status()
            raise AssertionError("unreachable")

    monkeypatch.setenv("PITCHPROB_ODDS_API_KEY", "SECRET-KEY-123")
    get_settings.cache_clear()
    monkeypatch.setattr(cli_main, "_make_odds_client", lambda key: FailingOddsClient())
    result = runner.invoke(cli_main.app, ["record-odds", "--league", "E0"])
    assert result.exit_code == 1
    assert "SECRET-KEY-123" not in result.output
    assert "429" in result.output
    assert "0" in result.output  # quota header still surfaced
    if result.exception is not None:  # no rendered traceback may carry the key
        import traceback

        rendered = "".join(traceback.format_exception(result.exception))
        assert "SECRET-KEY-123" not in rendered


def test_record_odds_requires_key(cli_env, monkeypatch) -> None:
    # empty beats delenv: the developer's real .env may carry a key and
    # pydantic-settings would fall back to it
    monkeypatch.setenv("PITCHPROB_ODDS_API_KEY", "")
    get_settings.cache_clear()
    result = runner.invoke(cli_main.app, ["record-odds", "--league", "E0"])
    assert result.exit_code == 1
    assert "PITCHPROB_ODDS_API_KEY" in result.output
