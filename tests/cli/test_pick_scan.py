"""CLI tests for the forward pick ledger and the PL value scanner
(Phase 5 part 3) — temp SQLite, tape seeded directly, fully offline."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

import pitchprob.cli.main as cli_main
from pitchprob.core import db
from pitchprob.core.config import get_settings
from pitchprob.data.orm import Base, OddsTick, Pick

runner = CliRunner()

_NOW = datetime.now(tz=UTC)
_KICKOFF = (_NOW + timedelta(days=2)).replace(microsecond=0)
_OBSERVED = _NOW - timedelta(hours=10)


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PITCHPROB_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    monkeypatch.setenv("PITCHPROB_DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    db.reset()
    Base.metadata.create_all(db.get_engine())
    yield
    db.reset()
    get_settings.cache_clear()


def _seed_totals_tape(observed_at: datetime = _OBSERVED) -> None:
    """Symmetric Pinnacle ou book (1.89/1.89 -> Shin fair exactly 0.5)."""
    with db.session_scope() as session:
        for selection in ("over", "under"):
            session.add(
                OddsTick(
                    sport_key="soccer_epl",
                    event_id="ev1",
                    commence_time=_KICKOFF,
                    home_team="Arsenal",
                    away_team="Coventry City",
                    bookmaker="pinnacle",
                    market="ou",
                    selection=selection,
                    line=Decimal("3.0"),
                    price=Decimal("1.89"),
                    observed_at=observed_at,
                )
            )


class TestScanCommand:
    def test_verdicts_effective_prices_and_freshness(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "scan", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--quote", "betclic:2.10", "--quote", "sts:2.05",
                "--tax-free", "betclic",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Arsenal vs Coventry City" in result.output
        # tax-free Betclic: 0.5 x 2.10 - 1 = +5.0% -> PLAY, listed first
        lines = result.output.splitlines()
        betclic_line = next(line for line in lines if "betclic" in line)
        sts_line = next(line for line in lines if "sts" in line)
        assert "PLAY" in betclic_line and "+5.0%" in betclic_line
        # taxed STS: 0.5 x 2.05 x 0.88 - 1 = -9.8% -> NO BET
        assert "NO BET" in sts_line and "-9.8%" in sts_line
        assert "1.80" in sts_line  # effective price shown
        assert lines.index(betclic_line) < lines.index(sts_line)
        # anchor freshness surfaced (10h-old snapshot)
        assert "10h ago" in result.output

    def test_boost_reports_promo_value(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "scan", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--quote", "sts:2.10", "--boost", "sts:2.40",
            ],
        )
        assert result.exit_code == 0, result.output
        # 0.5 x 2.40 x 0.88 - 1 = +5.6% and the promo adds 13.2pp
        assert "+5.6%" in result.output
        assert "promo +13.2pp" in result.output

    def test_line_mismatch_reports_no_anchor(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "scan", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "2.5",
                "--quote", "betclic:2.30",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "NO ANCHOR" in result.output

    def test_bad_quote_format_fails_cleanly(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "scan", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--quote", "betclic",
            ],
        )
        assert result.exit_code != 0
        assert "book:price" in result.output


class TestPickCommands:
    def test_log_resolves_event_and_anchors(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "pick", "log", "--match", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--book", "betclic", "--stake", "5", "--price", "2.10",
                "--tax-free",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "effective 2.10" in result.output
        assert "sharp anchor: pinnacle 1.89" in result.output
        assert "tax-free allowance remaining: 995" in result.output
        with db.session_scope() as session:
            pick = session.execute(select(Pick)).scalars().one()
            assert pick.event_id == "ev1"
            assert pick.price_sharp == Decimal("1.89")

    def test_log_warns_outside_program_stakes(self, cli_env) -> None:
        _seed_totals_tape()
        result = runner.invoke(
            cli_main.app,
            [
                "pick", "log", "--match", "arsenal",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--book", "sts", "--stake", "50", "--price", "2.10",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "outside the 2-5 PLN" in result.output

    def test_log_refuses_tax_free_beyond_allowance(self, cli_env) -> None:
        _seed_totals_tape()
        base = [
            "pick", "log", "--match", "arsenal",
            "--market", "ou", "--selection", "over", "--line", "3.0",
            "--book", "betclic", "--price", "2.10", "--tax-free",
        ]
        assert runner.invoke(
            cli_main.app, [*base, "--stake", "998"]
        ).exit_code == 0
        result = runner.invoke(cli_main.app, [*base, "--stake", "5"])
        assert result.exit_code != 0
        assert "allowance" in result.output

    def test_settle_and_list_close_the_loop(self, cli_env) -> None:
        # A finished fixture: kickoff yesterday, tape closing 2 days ago.
        past_kickoff = (_NOW - timedelta(days=1)).replace(microsecond=0)
        with db.session_scope() as session:
            for selection in ("over", "under"):
                session.add(
                    OddsTick(
                        sport_key="soccer_epl",
                        event_id="ev1",
                        commence_time=past_kickoff,
                        home_team="Arsenal",
                        away_team="Coventry City",
                        bookmaker="pinnacle",
                        market="ou",
                        selection=selection,
                        line=Decimal("3.0"),
                        price=Decimal("1.89"),
                        observed_at=_NOW - timedelta(days=2),
                    )
                )
        # explicit-entry path: the fixture is no longer "upcoming", so the
        # operator passes teams/kickoff/event id directly
        logged = runner.invoke(
            cli_main.app,
            [
                "pick", "log",
                "--home", "Arsenal", "--away", "Coventry City",
                "--kickoff", past_kickoff.isoformat(), "--event", "ev1",
                "--market", "ou", "--selection", "over", "--line", "3.0",
                "--book", "betclic", "--stake", "5", "--price", "2.10",
                "--tax-free",
            ],
        )
        assert logged.exit_code == 0, logged.output
        # manual settlement by id (result not in `matches`); the command
        # must still attach CLV from the tape closing
        result = runner.invoke(
            cli_main.app, ["pick", "settle", "--id", "1", "--result", "3:1"]
        )
        assert result.exit_code == 0, result.output
        assert "settled pick #1" in result.output

        listing = runner.invoke(cli_main.app, ["pick", "list"])
        assert listing.exit_code == 0, listing.output
        # 5 PLN x 2.10 = 10.50 returned; CLV exec +5.0%, sharp -5.5%
        assert "10.50" in listing.output
        assert "+5.0%" in listing.output
        assert "-5.5%" in listing.output
        assert "shopping" in listing.output

    def test_settle_auto_reports_unmatched(self, cli_env) -> None:
        _seed_totals_tape(observed_at=_NOW - timedelta(days=3))
        with db.session_scope() as session:
            session.add(
                Pick(
                    created_at=_NOW,
                    event_id=None,
                    home_team="Everton",
                    away_team="Leeds United",
                    kickoff_utc=_NOW - timedelta(days=1),
                    market="1x2",
                    selection="home",
                    line=None,
                    bookmaker="sts",
                    stake_pln=Decimal("5"),
                    price_quoted=Decimal("2.50"),
                    tax_free=False,
                    price_effective=Decimal("2.20"),
                    placed_at=_NOW - timedelta(days=2),
                )
            )
        result = runner.invoke(cli_main.app, ["pick", "settle"])
        assert result.exit_code == 0, result.output
        assert "Everton vs Leeds United" in result.output
