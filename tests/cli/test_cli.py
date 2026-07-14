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
