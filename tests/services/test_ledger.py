"""Pick-ledger tests — offline, in-memory SQLite.

The ledger is the syndicate program's decision variable (CLV, never backtest
ROI), so the numbers here are hand-computed to the grosz: effective prices
under the 12% tax, gross returns, and the per-bet clv_exec / clv_sharp
decomposition against a symmetric (exactly-fair) closing book.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.betting.risk import RiskLimits
from pitchprob.data.orm import Base, League, Match, OddsTick, Pick, Season, Team
from pitchprob.services.ledger import (
    attach_clv,
    auto_settle,
    ledger_summary,
    log_pick,
    settle_pick,
    tax_free_allowance,
)

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_T1 = datetime(2026, 8, 19, 8, 0, tzinfo=UTC)
_T2 = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
_PLACED = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
_AFTER_MATCH = _KICKOFF + timedelta(hours=3)


def _tick(
    *,
    selection: str,
    price: str,
    observed_at: datetime,
    market: str = "ou",
    line: str | None = "3.0",
    event_id: str = "ev1",
) -> OddsTick:
    return OddsTick(
        sport_key="soccer_epl",
        event_id=event_id,
        commence_time=_KICKOFF,
        home_team="Arsenal",
        away_team="Coventry City",
        bookmaker="pinnacle",
        market=market,
        selection=selection,
        line=None if line is None else Decimal(line),
        price=Decimal(price),
        observed_at=observed_at,
    )


def _seed_symmetric_totals(session: Session) -> None:
    for when in (_T1, _T2):
        for selection in ("over", "under"):
            session.add(_tick(selection=selection, price="1.89", observed_at=when))
    session.flush()


def _seed_1x2(session: Session) -> None:
    for when, prices in [
        (_T1, ("2.10", "3.60", "3.40")),
        (_T2, ("2.00", "3.70", "3.60")),
    ]:
        for selection, price in zip(("home", "draw", "away"), prices, strict=True):
            session.add(
                _tick(
                    selection=selection, price=price, observed_at=when,
                    market="1x2", line=None,
                )
            )
    session.flush()


#: These tests predate the Phase 3 risk layer and exercise ledger mechanics
#: — tax-free allowance, settlement, the CLV decomposition — which
#: deliberately stack several bets on one fixture and use stakes far outside
#: the 2-5 PLN band. They opt out of the limits explicitly rather than being
#: rewritten around them; the limits themselves are covered in
#: tests/betting/test_risk.py and tests/services/test_ledger_risk.py.
_UNLIMITED = RiskLimits(
    bankroll_pln=Decimal("500"),
    min_stake_pln=Decimal("0.01"),
    max_stake_pln=Decimal("100000"),
    max_match_stake_pln=Decimal("100000"),
    max_daily_stake_pln=Decimal("100000"),
    max_open_picks=100000,
    max_drawdown_pln=Decimal("100000"),
)


def _log_over_pick(session: Session, **overrides: object) -> Pick:
    params: dict[str, object] = {
        "limits": _UNLIMITED,
        "home_team": "Arsenal",
        "away_team": "Coventry City",
        "kickoff_utc": _KICKOFF,
        "market": "ou",
        "selection": "over",
        "line": Decimal("3.0"),
        "bookmaker": "betclic",
        "stake_pln": Decimal("5"),
        "price_quoted": Decimal("2.10"),
        "tax_free": True,
        "placed_at": _PLACED,
        "event_id": "ev1",
    }
    params.update(overrides)
    return log_pick(session, **params)  # type: ignore[arg-type]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestLogPick:
    def test_tax_free_pick_keeps_quoted_price_and_anchors_on_tape(
        self, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        pick = _log_over_pick(session)
        assert pick.price_effective == Decimal("2.10")
        # Sharp anchor: latest Pinnacle quote at placement time, with its
        # freshness timestamp — placed after T2, so T2 wins.
        assert pick.price_sharp == Decimal("1.89")
        assert pick.sharp_observed_at is not None
        assert pick.sharp_observed_at.replace(tzinfo=UTC) == _T2

    def test_taxed_pick_pays_088_of_quoted(self, session: Session) -> None:
        pick = _log_over_pick(
            session, bookmaker="sts", tax_free=False, event_id=None
        )
        # 2.10 * 0.88 = 1.8480 exactly
        assert pick.price_effective == Decimal("1.8480")
        assert pick.price_sharp is None
        assert pick.sharp_observed_at is None

    def test_sharp_anchor_never_looks_ahead_of_placement(
        self, session: Session
    ) -> None:
        _seed_1x2(session)
        pick = _log_over_pick(
            session,
            market="1x2",
            selection="home",
            line=None,
            placed_at=_T1 + timedelta(hours=2),
        )
        assert pick.price_sharp == Decimal("2.10")  # T1 price, not T2's 2.00

    def test_line_mismatch_leaves_anchor_empty(self, session: Session) -> None:
        _seed_symmetric_totals(session)  # tape quotes line 3.0 only
        pick = _log_over_pick(session, line=Decimal("2.5"))
        assert pick.price_sharp is None

    def test_tax_free_allowance_is_tracked_per_bookmaker(
        self, session: Session
    ) -> None:
        _log_over_pick(session, stake_pln=Decimal("4"))
        _log_over_pick(session, stake_pln=Decimal("3"))
        allowance = tax_free_allowance(session, "betclic")
        assert allowance.used == Decimal("7")
        assert allowance.remaining == Decimal("993")
        # taxed picks at the same book do not consume the allowance
        _log_over_pick(session, stake_pln=Decimal("5"), tax_free=False)
        assert tax_free_allowance(session, "betclic").used == Decimal("7")

    def test_tax_free_beyond_the_allowance_is_refused(
        self, session: Session
    ) -> None:
        _log_over_pick(session, stake_pln=Decimal("998"))
        with pytest.raises(ValueError, match="allowance"):
            _log_over_pick(session, stake_pln=Decimal("5"))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"market": "1x2", "selection": "over", "line": None},
            {"market": "ou", "selection": "over", "line": None},
            {"market": "1x2", "selection": "home", "line": Decimal("1.5")},
            {"market": "ah", "selection": "draw", "line": Decimal("-1.0")},
            {"stake_pln": Decimal("0")},
            {"stake_pln": Decimal("-2")},
            {"price_quoted": Decimal("1.0")},
        ],
    )
    def test_invalid_bets_are_rejected(
        self, session: Session, overrides: dict[str, object]
    ) -> None:
        with pytest.raises(ValueError):
            _log_over_pick(session, **overrides)


class TestSettlement:
    def test_taxed_1x2_win_pays_effective_price(self, session: Session) -> None:
        pick = _log_over_pick(
            session,
            market="1x2",
            selection="home",
            line=None,
            bookmaker="sts",
            tax_free=False,
            price_quoted=Decimal("2.05"),
            event_id=None,
        )
        settle_pick(pick, ft_home=2, ft_away=1, settled_at=_AFTER_MATCH)
        # 5 PLN x (2.05 x 0.88) = 5 x 1.804 = 9.02 PLN
        assert pick.gross_return_pln == Decimal("9.02")
        assert (pick.ft_home, pick.ft_away) == (2, 1)

    def test_lost_pick_returns_zero(self, session: Session) -> None:
        pick = _log_over_pick(session)  # over 3.0
        settle_pick(pick, ft_home=1, ft_away=1, settled_at=_AFTER_MATCH)
        assert pick.gross_return_pln == Decimal("0.00")

    def test_push_returns_the_full_stake(self, session: Session) -> None:
        pick = _log_over_pick(session)  # over 3.0, stake 5
        settle_pick(pick, ft_home=2, ft_away=1, settled_at=_AFTER_MATCH)
        assert pick.gross_return_pln == Decimal("5.00")

    def test_lineless_row_refuses_to_settle(self, session: Session) -> None:
        """log_pick cannot produce this; a row from elsewhere must not
        settle money on a guessed line."""
        pick = _log_over_pick(session)
        pick.line = None
        with pytest.raises(ValueError, match="no line"):
            settle_pick(pick, ft_home=2, ft_away=2, settled_at=_AFTER_MATCH)

    def test_quarter_line_ah_half_push(self, session: Session) -> None:
        pick = _log_over_pick(
            session,
            market="ah",
            selection="home",
            line=Decimal("-0.25"),
            tax_free=True,
        )
        # 1:1 -> half the stake pushes (returns), half loses: 2.50 PLN
        settle_pick(pick, ft_home=1, ft_away=1, settled_at=_AFTER_MATCH)
        assert pick.gross_return_pln == Decimal("2.50")

    def test_auto_settle_resolves_results_from_matches(
        self, session: Session
    ) -> None:
        league = League(code="E0", name="Premier League", country="England")
        session.add(league)
        session.flush()
        season = Season(league_id=league.id, label="2026/27", start_year=2026)
        arsenal = Team(canonical_name="Arsenal", country="England")
        coventry = Team(canonical_name="Coventry City", country="England")
        session.add_all([season, arsenal, coventry])
        session.flush()
        session.add(
            Match(
                season_id=season.id,
                match_date=_KICKOFF.date(),
                kickoff_utc=_KICKOFF,
                home_team_id=arsenal.id,
                away_team_id=coventry.id,
                ft_home=3,
                ft_away=1,
            )
        )
        session.flush()

        settled_pick = _log_over_pick(session)  # over 3.0 -> 4 goals, win
        future_pick = _log_over_pick(
            session, kickoff_utc=_KICKOFF + timedelta(days=7)
        )
        orphan_pick = _log_over_pick(
            session,
            home_team="Everton",
            away_team="Leeds United",
            event_id=None,
        )

        summary = auto_settle(session, now=_AFTER_MATCH)
        assert summary.settled == 1
        assert summary.pending == 1  # the orphan: kicked off but unmatched
        assert summary.unmatched == ["Everton vs Leeds United"]
        assert settled_pick.gross_return_pln == Decimal("10.50")  # 5 x 2.10
        assert future_pick.gross_return_pln is None
        assert orphan_pick.gross_return_pln is None


class TestAttachClv:
    def test_clv_decomposition_against_symmetric_close(
        self, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        tax_free = _log_over_pick(session)
        taxed = _log_over_pick(
            session, bookmaker="sts", tax_free=False, stake_pln=Decimal("4")
        )
        summary = attach_clv(session, now=_AFTER_MATCH)
        assert summary.attached == 2

        # Symmetric 1.89/1.89 book -> Shin fair is exactly 0.5.
        assert tax_free.closing_fair_prob == pytest.approx(0.5)
        assert tax_free.closing_observed_at is not None
        # clv_exec: 2.10 x 0.5 - 1 = +0.05 (the promo carries the value)
        assert tax_free.clv_exec == pytest.approx(0.05)
        # clv_sharp: 1.89 x 0.5 - 1 = -0.055 (no timing edge vs Pinnacle)
        assert tax_free.clv_sharp == pytest.approx(-0.055)

        # Taxed exec price 1.848: clv_exec = 1.848 x 0.5 - 1 = -0.076 —
        # the 12% tax is visible in the executed CLV, by design.
        assert taxed.clv_exec == pytest.approx(-0.076)

    def test_unresolved_picks_are_counted_not_guessed(
        self, session: Session
    ) -> None:
        # Tape only has post-kickoff ticks -> no closing snapshot exists.
        for selection in ("over", "under"):
            session.add(
                _tick(
                    selection=selection, price="1.89",
                    observed_at=_KICKOFF + timedelta(minutes=10),
                )
            )
        session.flush()
        _log_over_pick(session)
        no_event = _log_over_pick(session, event_id=None)
        summary = attach_clv(session, now=_AFTER_MATCH)
        assert summary.attached == 0
        assert summary.skipped_no_closing == 1
        assert summary.skipped_no_event == 1
        assert no_event.clv_exec is None

    def test_future_picks_are_untouched(self, session: Session) -> None:
        _seed_symmetric_totals(session)
        pick = _log_over_pick(session)
        summary = attach_clv(session, now=_PLACED)  # before kickoff
        assert summary.attached == 0
        assert pick.clv_exec is None


class TestLedgerSummary:
    def test_hand_computed_totals(self, session: Session) -> None:
        _seed_symmetric_totals(session)
        over = _log_over_pick(session)  # 5 PLN tax-free @2.10, anchored
        loser = _log_over_pick(
            session,
            market="1x2",
            selection="home",
            line=None,
            bookmaker="sts",
            tax_free=False,
            stake_pln=Decimal("4"),
            price_quoted=Decimal("3.00"),
            event_id=None,
        )
        settle_pick(over, ft_home=3, ft_away=1, settled_at=_AFTER_MATCH)
        settle_pick(loser, ft_home=0, ft_away=2, settled_at=_AFTER_MATCH)
        attach_clv(session, now=_AFTER_MATCH)

        summary = ledger_summary(session)
        assert summary["n_picks"] == 2
        assert summary["n_settled"] == 2
        assert summary["total_staked_pln"] == pytest.approx(9.0)
        assert summary["total_returned_pln"] == pytest.approx(10.50)
        assert summary["profit_pln"] == pytest.approx(1.50)
        assert summary["roi"] == pytest.approx(1.50 / 9.0)
        assert summary["n_with_clv"] == 1
        assert summary["mean_clv_exec"] == pytest.approx(0.05)
        assert summary["mean_clv_sharp"] == pytest.approx(-0.055)
        # Decomposition: exec - sharp = venue/shopping value (the promo).
        assert summary["mean_shopping_value"] == pytest.approx(0.105)

    def test_empty_ledger_reports_zeros_without_dividing(
        self, session: Session
    ) -> None:
        summary = ledger_summary(session)
        assert summary["n_picks"] == 0
        assert summary["roi"] is None
        assert summary["mean_clv_exec"] is None

    def test_picks_persist_via_orm(self, session: Session) -> None:
        _log_over_pick(session)
        rows = session.execute(select(Pick)).scalars().all()
        assert len(rows) == 1
        assert rows[0].bookmaker == "betclic"
