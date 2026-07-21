"""PL value-scanner tests — offline, in-memory SQLite.

The scanner's thesis (Phase 5, audit Etap 3): the model does NOT outpredict
the market, so the primary anchor is the live Pinnacle fair from the tape;
the model fair is secondary, informational only. All comparisons run on
*effective* prices (x0.88 taxed / x1.0 tax-free promo), and every verdict
carries the anchor's freshness — a daily tape means the anchor can be hours
old, and the verdict must say so rather than pretend liquidity.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.betting.effective import PromoTerms
from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.scanner import OperatorQuote, scan

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_T2 = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
_NOW = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)  # anchor 10h old


def _tick(
    *,
    selection: str,
    price: str,
    market: str = "1x2",
    line: str | None = None,
    event_id: str = "ev1",
    home: str = "Arsenal",
    away: str = "Coventry City",
    observed_at: datetime = _T2,
    commence: datetime = _KICKOFF,
) -> OddsTick:
    return OddsTick(
        sport_key="soccer_epl",
        event_id=event_id,
        commence_time=commence,
        home_team=home,
        away_team=away,
        bookmaker="pinnacle",
        market=market,
        selection=selection,
        line=None if line is None else Decimal(line),
        price=Decimal(price),
        observed_at=observed_at,
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _seed_1x2(session: Session) -> None:
    for selection, price in zip(
        ("home", "draw", "away"), ("2.00", "3.70", "3.60"), strict=True
    ):
        session.add(_tick(selection=selection, price=price))
    session.flush()


def _seed_symmetric_totals(session: Session) -> None:
    for selection in ("over", "under"):
        session.add(
            _tick(selection=selection, price="1.89", market="ou", line="3.0")
        )
    session.flush()


class TestScanVerdicts:
    def test_tax_free_edge_plays_taxed_same_price_does_not(
        self, session: Session
    ) -> None:
        _seed_1x2(session)
        result = scan(
            session,
            query="arsenal",
            market="1x2",
            selection="home",
            quotes=[
                OperatorQuote("sts", Decimal("2.30")),
                OperatorQuote(
                    "betclic", Decimal("2.30"), promo=PromoTerms(tax_free=True)
                ),
            ],
            now=_NOW,
        )
        fair_home = remove_overround_shin([2.00, 3.70, 3.60])[0]
        assert result.anchor is not None
        assert result.anchor.probabilities["home"] == pytest.approx(fair_home)
        assert result.anchor_age == timedelta(hours=10)

        # sorted by edge desc: the tax-free quote must lead
        betclic, sts = result.verdicts
        assert betclic.bookmaker == "betclic"
        assert betclic.edge == pytest.approx(fair_home * 2.30 - 1.0)
        assert betclic.verdict == "PLAY"
        assert sts.edge == pytest.approx(fair_home * 2.30 * 0.88 - 1.0)
        assert sts.verdict == "NO BET"

    def test_min_edge_gate_on_exact_numbers(self, session: Session) -> None:
        _seed_symmetric_totals(session)  # Shin fair exactly 0.5
        result = scan(
            session,
            query="arsenal",
            market="ou",
            selection="over",
            line=Decimal("3.0"),
            quotes=[
                OperatorQuote(
                    "betclic", Decimal("2.10"), promo=PromoTerms(tax_free=True)
                ),
                OperatorQuote(
                    "fortuna", Decimal("2.02"), promo=PromoTerms(tax_free=True)
                ),
            ],
            now=_NOW,
        )
        play, no_bet = result.verdicts
        assert play.edge == pytest.approx(0.05)  # 0.5 x 2.10 - 1
        assert play.verdict == "PLAY"
        assert no_bet.edge == pytest.approx(0.01)  # below the 2% gate
        assert no_bet.verdict == "NO BET"

    def test_boost_is_priced_as_an_instrument(self, session: Session) -> None:
        _seed_symmetric_totals(session)
        result = scan(
            session,
            query="arsenal",
            market="ou",
            selection="over",
            line=Decimal("3.0"),
            quotes=[
                OperatorQuote(
                    "sts",
                    Decimal("2.10"),
                    promo=PromoTerms(boosted_price=Decimal("2.40")),
                )
            ],
            now=_NOW,
        )
        (verdict,) = result.verdicts
        # boosted, still taxed: 0.5 x (2.40 x 0.88) - 1 = 0.056
        assert verdict.edge == pytest.approx(0.056)
        assert verdict.verdict == "PLAY"
        assert verdict.evaluation is not None
        # the promo itself is worth 0.132 over the bare taxed quote
        assert verdict.evaluation.promo_value == pytest.approx(0.132)

    def test_stale_anchor_downgrades_the_verdict(self, session: Session) -> None:
        _seed_symmetric_totals(session)
        result = scan(
            session,
            query="arsenal",
            market="ou",
            selection="over",
            line=Decimal("3.0"),
            quotes=[
                OperatorQuote(
                    "betclic", Decimal("2.30"), promo=PromoTerms(tax_free=True)
                )
            ],
            # 32h after the last snapshot (tape missed a day), still pre-match
            now=_T2 + timedelta(hours=32),
        )
        (verdict,) = result.verdicts
        assert verdict.edge == pytest.approx(0.15)
        assert verdict.verdict == "STALE"
        assert result.anchor_age == timedelta(hours=32)

    def test_no_anchor_yields_no_edge_and_says_so(self, session: Session) -> None:
        _seed_symmetric_totals(session)  # only line 3.0 on tape
        result = scan(
            session,
            query="arsenal",
            market="ou",
            selection="over",
            line=Decimal("2.5"),
            quotes=[OperatorQuote("betclic", Decimal("2.30"))],
            now=_NOW,
        )
        assert result.anchor is None
        (verdict,) = result.verdicts
        assert verdict.edge is None
        assert verdict.verdict == "NO ANCHOR"

    def test_model_fair_is_secondary_and_does_not_flip_the_verdict(
        self, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        result = scan(
            session,
            query="arsenal",
            market="ou",
            selection="over",
            line=Decimal("3.0"),
            quotes=[
                OperatorQuote(
                    "betclic", Decimal("2.02"), promo=PromoTerms(tax_free=True)
                )
            ],
            model_probability=0.60,
            now=_NOW,
        )
        (verdict,) = result.verdicts
        # model says +21.2% — but the market anchor gates the verdict
        assert verdict.edge_model == pytest.approx(0.60 * 2.02 - 1.0)
        assert verdict.verdict == "NO BET"


class TestEventResolution:
    def test_query_must_match_exactly_one_upcoming_event(
        self, session: Session
    ) -> None:
        _seed_1x2(session)
        session.add(
            _tick(
                selection="home", price="2.5", event_id="ev2",
                home="Arsenal", away="Everton",
                commence=_KICKOFF + timedelta(days=1),
            )
        )
        session.flush()
        with pytest.raises(ValueError, match="ambiguous"):
            scan(
                session,
                query="arsenal",
                market="1x2",
                selection="home",
                quotes=[OperatorQuote("sts", Decimal("2.30"))],
                now=_NOW,
            )
        with pytest.raises(ValueError, match="no upcoming"):
            scan(
                session,
                query="legia",
                market="1x2",
                selection="home",
                quotes=[OperatorQuote("sts", Decimal("2.30"))],
                now=_NOW,
            )

    def test_event_id_bypasses_the_query(self, session: Session) -> None:
        _seed_1x2(session)
        result = scan(
            session,
            event_id="ev1",
            market="1x2",
            selection="home",
            quotes=[OperatorQuote("sts", Decimal("2.30"))],
            now=_NOW,
        )
        assert result.home_team == "Arsenal"
        assert result.commence_time == _KICKOFF
