"""CLI smoke test for the speaking loop (ADR 0016) — temp SQLite, offline.

``--once --dry-run`` runs a single pass and prints alerts to stdout instead
of pushing to Telegram, so the whole loop is exercised without a token or a
network. The detection logic itself is covered in tests/services/test_watch.py;
this proves the command wires the pieces together and renders the alert.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from typer.testing import CliRunner

import pitchprob.cli.main as cli_main
from pitchprob.core import db
from pitchprob.core.config import get_settings
from pitchprob.data.orm import Base, OddsTick

runner = CliRunner()

_NOW = datetime.now(tz=UTC)
_KICKOFF = (_NOW + timedelta(days=2)).replace(microsecond=0)
_OBSERVED = _NOW - timedelta(hours=10)


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PITCHPROB_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("PITCHPROB_DATA_DIR", str(tmp_path / "data"))
    # Ensure no ambient Telegram creds leak in and change the branch taken.
    monkeypatch.delenv("PITCHPROB_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("PITCHPROB_TELEGRAM_CHAT_ID", raising=False)
    get_settings.cache_clear()
    db.reset()
    Base.metadata.create_all(db.get_engine())
    yield
    db.reset()
    get_settings.cache_clear()


def _seed(over_price: str = "2.40") -> None:
    with db.session_scope() as session:
        for selection in ("over", "under"):
            session.add(
                OddsTick(
                    source="the-odds-api",
                    sport_key="soccer_epl",
                    event_id="ev1",
                    commence_time=_KICKOFF,
                    home_team="Arsenal",
                    away_team="Everton",
                    bookmaker="pinnacle",
                    market="ou",
                    selection=selection,
                    line=Decimal("3.0"),
                    price=Decimal("1.89"),
                    observed_at=_OBSERVED,
                )
            )
        session.add(
            OddsTick(
                source="odds-api-io",
                sport_key="soccer_epl",
                event_id="feed1",
                commence_time=_KICKOFF,
                home_team="Arsenal",
                away_team="Everton",
                bookmaker="Betclic PL",
                market="ou",
                selection="over",
                line=Decimal("3.0"),
                price=Decimal(over_price),
                observed_at=_OBSERVED,
            )
        )


class TestWatchCommand:
    def test_once_dry_run_prints_the_lead(self, cli_env) -> None:
        _seed()
        result = runner.invoke(cli_main.app, ["watch", "--once", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Betclic PL" in result.output
        assert "LEAD" in result.output  # feed quote is an UNVERIFIED lead
        assert "scanned 1 events" in result.output

    def test_once_dry_run_is_silent_below_threshold(self, cli_env) -> None:
        _seed(over_price="2.02")  # tax-free Betclic: 0.5 x 2.02 - 1 = 0.01 < 0.02
        result = runner.invoke(cli_main.app, ["watch", "--once", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "Betclic PL" not in result.output
        assert "sent 0" in result.output

    def test_self_test_sends_a_marked_test_alert(self, cli_env) -> None:
        result = runner.invoke(cli_main.app, ["watch", "--self-test", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "TEST" in result.output
        assert "nie jest sygnał" in result.output.lower()
        assert "test alert sent" in result.output


class TestRiskReportNotify:
    def test_notify_dry_run_pushes_the_report(self, cli_env) -> None:
        result = runner.invoke(
            cli_main.app, ["risk", "report", "--notify", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "report pushed" in result.output
