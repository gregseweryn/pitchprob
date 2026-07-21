"""The pre-round freshness gate (season runbook) — offline, in-memory SQLite.

`pitchprob status` is what the operator runs before touching the scanner on
a match day. Its one design rule: every check names the command that fixes
it, because a gate that says "stale" without saying "run X" just moves the
debugging to the worst possible moment — ten minutes before kickoff.

Thresholds are pinned at their boundaries, in both directions: a gate with
an off-by-one at the threshold silently waves through exactly the case it
exists to catch.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, League, Match, OddsTick, Pick, Season, Team
from pitchprob.services.freshness import (
    MAX_RESULTS_AGE_DAYS,
    MAX_TAPE_AGE,
    season_status,
)
from pitchprob.services.scanner import DEFAULT_MAX_ANCHOR_AGE

_NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)  # a Tuesday mid-season


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_match(
    session: Session, *, days_ago: float, with_xg: bool = True
) -> None:
    league = session.query(League).filter_by(code="E0").one_or_none()
    if league is None:
        league = League(code="E0", name="Premier League", country="England")
        session.add(league)
        session.flush()
        session.add(Season(league_id=league.id, label="2026/27", start_year=2026))
        session.add_all(
            [
                Team(canonical_name="Arsenal", country="England"),
                Team(canonical_name="Coventry City", country="England"),
            ]
        )
        session.flush()
    season = session.query(Season).one()
    home, away = session.query(Team).order_by(Team.id).all()[:2]
    when = _NOW - timedelta(days=days_ago)
    session.add(
        Match(
            season_id=season.id,
            match_date=when.date(),
            kickoff_utc=when,
            home_team_id=home.id,
            away_team_id=away.id,
            ft_home=2,
            ft_away=1,
            xg_home=Decimal("1.8") if with_xg else None,
            xg_away=Decimal("0.9") if with_xg else None,
        )
    )
    session.flush()


def _seed_tick(
    session: Session, *, observed_hours_ago: float, kickoff_in_hours: float = 48
) -> None:
    session.add(
        OddsTick(
            sport_key="soccer_epl",
            event_id=f"ev{observed_hours_ago}-{kickoff_in_hours}",
            commence_time=_NOW + timedelta(hours=kickoff_in_hours),
            home_team="Arsenal",
            away_team="Coventry City",
            bookmaker="pinnacle",
            market="1x2",
            selection="home",
            line=None,
            price=Decimal("2.00"),
            observed_at=_NOW - timedelta(hours=observed_hours_ago),
        )
    )
    session.flush()


def _by_name(status) -> dict[str, object]:
    return {check.name: check for check in status.checks}


class TestThresholdConstants:
    def test_the_tape_threshold_is_the_scanner_threshold(self) -> None:
        """One number, two gates: if the scanner calls a 30h anchor STALE,
        the pre-round check must not call the same tape fresh."""
        assert MAX_TAPE_AGE == DEFAULT_MAX_ANCHOR_AGE

    def test_the_results_threshold_covers_a_weekly_round_plus_slack(self) -> None:
        assert MAX_RESULTS_AGE_DAYS == 8


class TestResultsCheck:
    def test_a_recent_round_passes(self, session: Session) -> None:
        _seed_match(session, days_ago=3)
        check = _by_name(season_status(session, now=_NOW))["results"]
        assert check.ok is True

    def test_exactly_at_the_threshold_still_passes(self, session: Session) -> None:
        _seed_match(session, days_ago=8)
        assert _by_name(season_status(session, now=_NOW))["results"].ok is True

    def test_one_day_past_the_threshold_is_stale_and_names_the_fix(
        self, session: Session
    ) -> None:
        _seed_match(session, days_ago=9)
        check = _by_name(season_status(session, now=_NOW))["results"]
        assert check.ok is False
        assert check.action is not None and "ingest" in check.action
        assert "--refresh" in check.action

    def test_an_empty_database_is_stale_not_a_crash(self, session: Session) -> None:
        check = _by_name(season_status(session, now=_NOW))["results"]
        assert check.ok is False
        assert "no matches" in check.detail


class TestXgCheck:
    def test_full_coverage_passes(self, session: Session) -> None:
        _seed_match(session, days_ago=3, with_xg=True)
        assert _by_name(season_status(session, now=_NOW))["xg"].ok is True

    def test_a_recent_match_without_xg_demands_a_refresh(
        self, session: Session
    ) -> None:
        _seed_match(session, days_ago=3, with_xg=False)
        check = _by_name(season_status(session, now=_NOW))["xg"]
        assert check.ok is False
        assert check.action is not None and "xg" in check.action

    def test_old_gaps_do_not_nag(self, session: Session) -> None:
        """Missing xG from years past is a known corpus property (99.98%
        coverage, not 100%), not a weekly action item."""
        _seed_match(session, days_ago=45, with_xg=False)
        _seed_match(session, days_ago=3, with_xg=True)
        assert _by_name(season_status(session, now=_NOW))["xg"].ok is True


class TestTapeCheck:
    def test_a_fresh_snapshot_with_upcoming_fixtures_passes(
        self, session: Session
    ) -> None:
        _seed_tick(session, observed_hours_ago=9)
        check = _by_name(season_status(session, now=_NOW))["tape"]
        assert check.ok is True

    def test_exactly_thirty_hours_still_passes(self, session: Session) -> None:
        """Mirrors the scanner: an anchor of exactly max age is still fresh
        (`age <= max` there), so the same instant cannot fail here."""
        _seed_tick(session, observed_hours_ago=30)
        assert _by_name(season_status(session, now=_NOW))["tape"].ok is True

    def test_past_thirty_hours_is_stale(self, session: Session) -> None:
        _seed_tick(session, observed_hours_ago=30.5)
        check = _by_name(season_status(session, now=_NOW))["tape"]
        assert check.ok is False
        assert check.action is not None
        assert "import-tape" in check.action

    def test_a_tape_with_no_upcoming_fixtures_cannot_anchor_a_scan(
        self, session: Session
    ) -> None:
        _seed_tick(session, observed_hours_ago=9, kickoff_in_hours=-24)
        check = _by_name(season_status(session, now=_NOW))["tape"]
        assert check.ok is False
        assert "upcoming" in check.detail


class TestLedgerCheck:
    def _pick(self, session: Session, *, kicked_off_hours_ago: float, **kw) -> Pick:
        when = _NOW - timedelta(hours=kicked_off_hours_ago)
        pick = Pick(
            created_at=when,
            event_id=kw.get("event_id", "ev1"),
            home_team="Arsenal",
            away_team="Coventry City",
            kickoff_utc=when,
            market="1x2",
            selection="home",
            line=None,
            bookmaker="sts",
            stake_pln=Decimal("5"),
            price_quoted=Decimal("2.10"),
            tax_free=False,
            price_effective=Decimal("1.848"),
            placed_at=when - timedelta(hours=4),
            settled_at=kw.get("settled_at"),
            gross_return_pln=kw.get("gross_return_pln"),
            clv_exec=kw.get("clv_exec"),
        )
        session.add(pick)
        session.flush()
        return pick

    def test_no_picks_is_a_clean_pass(self, session: Session) -> None:
        assert _by_name(season_status(session, now=_NOW))["ledger"].ok is True

    def test_a_pick_kicked_off_yesterday_and_unsettled_demands_action(
        self, session: Session
    ) -> None:
        self._pick(session, kicked_off_hours_ago=25)
        check = _by_name(season_status(session, now=_NOW))["ledger"]
        assert check.ok is False
        assert check.action is not None and "pick settle" in check.action

    def test_a_pick_that_just_kicked_off_gets_a_grace_day(
        self, session: Session
    ) -> None:
        """Results land in football-data's files on the Fri/Tue cadence;
        nagging two hours after the final whistle would make the gate cry
        wolf, and a gate that cries wolf gets ignored by September."""
        self._pick(session, kicked_off_hours_ago=2)
        assert _by_name(season_status(session, now=_NOW))["ledger"].ok is True

    def test_a_settled_pick_without_clv_still_flags(self, session: Session) -> None:
        self._pick(
            session,
            kicked_off_hours_ago=30,
            settled_at=_NOW - timedelta(hours=20),
            gross_return_pln=Decimal("0"),
        )
        check = _by_name(season_status(session, now=_NOW))["ledger"]
        assert check.ok is False
        assert "CLV" in check.detail

    def test_a_fully_processed_pick_passes(self, session: Session) -> None:
        self._pick(
            session,
            kicked_off_hours_ago=30,
            settled_at=_NOW - timedelta(hours=20),
            gross_return_pln=Decimal("9.24"),
            clv_exec=0.01,
        )
        assert _by_name(season_status(session, now=_NOW))["ledger"].ok is True


class TestOverall:
    def test_all_ok_only_when_every_check_passes(self, session: Session) -> None:
        _seed_match(session, days_ago=3)
        _seed_tick(session, observed_hours_ago=9)
        status = season_status(session, now=_NOW)
        assert status.all_ok is True
        assert {c.name for c in status.checks} == {"results", "xg", "tape", "ledger"}

    def test_one_stale_check_fails_the_gate(self, session: Session) -> None:
        _seed_match(session, days_ago=3)
        _seed_tick(session, observed_hours_ago=40)
        assert season_status(session, now=_NOW).all_ok is False


class TestSchemaBehindMigrations:
    def test_a_missing_table_is_a_failing_check_not_a_traceback(self) -> None:
        """Found live on the corpus DB (2026-07-21): a database behind the
        migration chain made the gate crash — the one tool that must never
        fail unhelpfully ten minutes before kickoff. A missing table is a
        freshness problem like any other, and its fix has a name."""
        engine = create_engine("sqlite+pysqlite:///:memory:")
        # everything except the ledger tables: the corpus scenario
        Base.metadata.create_all(
            engine,
            tables=[
                table
                for name, table in Base.metadata.tables.items()
                if name not in ("picks", "quote_checks")
            ],
        )
        with Session(engine) as session:
            status = season_status(session, now=_NOW)
        ledger = _by_name(status)["ledger"]
        assert ledger.ok is False
        assert "migration" in ledger.detail
        assert ledger.action is not None and "alembic upgrade head" in ledger.action
        assert status.all_ok is False
