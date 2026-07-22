"""Watch-loop tests — the "speaking loop" (P0, ADR 0016).

The loop is feed-driven and silent by default: it enumerates upcoming
fixtures the tape anchors (Pinnacle), pulls Polish-book quotes from the
odds-api.io feed, and speaks *only* when an effective edge clears the
threshold, the anchor is fresh, and the bet fits the risk limits. Feed
quotes are unvalidated, so they surface as UNVERIFIED leads, never as an
authorised PLAY (ADR 0014). Everything else is silence — the value of an
alert is that it is rare.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, OddsTick, Pick, SentAlert
from pitchprob.services.watch import find_alerts, run_watch

_KICKOFF = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)
_OBS = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)
_NOW = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)  # anchor 10h old, KO ~25h out


class _FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


def _tick(
    *,
    source: str,
    bookmaker: str,
    selection: str,
    price: str,
    market: str = "ou",
    line: str | None = "3.0",
    event_id: str,
    observed_at: datetime = _OBS,
    home: str = "Arsenal",
    away: str = "Everton",
) -> OddsTick:
    return OddsTick(
        source=source,
        sport_key="soccer_epl",
        event_id=event_id,
        commence_time=_KICKOFF,
        home_team=home,
        away_team=away,
        bookmaker=bookmaker,
        market=market,
        selection=selection,
        line=None if line is None else Decimal(line),
        price=Decimal(price),
        observed_at=observed_at,
    )


def _seed_anchor(session: Session, *, observed_at: datetime = _OBS) -> None:
    """Pinnacle ou 3.0 at 1.89/1.89 → Shin fair exactly 0.5 per side."""
    for selection in ("over", "under"):
        session.add(
            _tick(
                source="the-odds-api",
                bookmaker="pinnacle",
                selection=selection,
                price="1.89",
                event_id="ev1",
                observed_at=observed_at,
            )
        )


def _seed_feed(
    session: Session,
    *,
    over_price: str = "2.40",
    bookmaker: str = "Betclic PL",
) -> None:
    """One Polish book on the feed, over 3.0 — a different event id and the
    same teams, matched on canonical names like the scanner does."""
    session.add(
        _tick(
            source="odds-api-io",
            bookmaker=bookmaker,
            selection="over",
            price=over_price,
            event_id="feed1",
        )
    )


def _seed_spent_allowance(session: Session) -> None:
    """A settled 1,000 PLN of tax-free turnover at Betclic, a week back —
    far enough that no exposure limit sees it, so only the allowance does."""
    session.add(
        Pick(
            created_at=_NOW - timedelta(days=7),
            home_team="Someone",
            away_team="Else",
            kickoff_utc=_NOW - timedelta(days=6),
            market="1x2",
            selection="home",
            bookmaker="betclic",
            stake_pln=Decimal("1000"),
            price_quoted=Decimal("2.0"),
            tax_free=True,
            price_effective=Decimal("2.0"),
            placed_at=_NOW - timedelta(days=7),
            settled_at=_NOW - timedelta(days=6),
            gross_return_pln=Decimal("2000"),
        )
    )


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestDetection:
    def test_feed_edge_becomes_one_unverified_alert(self, session: Session) -> None:
        _seed_anchor(session)
        _seed_feed(session)
        session.flush()
        alerts = find_alerts(session, now=_NOW, min_edge=0.02)
        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.verdict == "UNVERIFIED"  # feed is never an auto-PLAY
        assert alert.bookmaker == "Betclic PL"
        assert alert.market == "ou"
        assert alert.selection == "over"
        assert alert.line == Decimal("3.0")
        # Fresh ledger → the whole Bez Podatku allowance remains → Betclic
        # prices tax-free automatically: 0.5 x 2.40 x 1.0 - 1 = 0.20 (the
        # x0.88 reading understated this by 12pp of payout).
        assert alert.edge == pytest.approx(0.20)
        assert alert.price_effective == Decimal("2.40")
        assert alert.anchor_price == Decimal("1.89")
        assert alert.suggested_stake_pln == Decimal("5")

    def test_spent_allowance_degrades_betclic_to_094(self, session: Session) -> None:
        _seed_anchor(session)
        _seed_feed(session)
        _seed_spent_allowance(session)
        session.flush()
        alerts = find_alerts(session, now=_NOW, min_edge=0.02)
        assert len(alerts) == 1
        # Limit spent → §3 ust. 11 pkt 1: singles pay x0.94.
        # 0.5 x 2.40 x 0.94 - 1 = 0.128
        assert alerts[0].edge == pytest.approx(0.128)
        assert alerts[0].price_effective == Decimal("2.2560")

    def test_kill_switch_prices_betclic_bare_taxed(self, session: Session) -> None:
        _seed_anchor(session)
        _seed_feed(session)
        session.flush()
        alerts = find_alerts(
            session,
            now=_NOW,
            min_edge=0.02,
            disabled_promos=frozenset({"betclic"}),
        )
        assert len(alerts) == 1
        # §8 kill switch: promo off → 0.5 x 2.40 x 0.88 - 1 = 0.056
        assert alerts[0].edge == pytest.approx(0.056)

    def test_unregistered_book_stays_bare_taxed(self, session: Session) -> None:
        _seed_anchor(session)
        _seed_feed(session, bookmaker="STS PL")
        session.flush()
        alerts = find_alerts(session, now=_NOW, min_edge=0.02)
        assert len(alerts) == 1
        # No promo in the registry for STS → conservative x0.88.
        assert alerts[0].edge == pytest.approx(0.056)

    def test_below_threshold_is_silent(self, session: Session) -> None:
        _seed_anchor(session)
        # Tax-free Betclic: 0.5 x 2.02 - 1 = 0.01 < 0.02
        _seed_feed(session, over_price="2.02")
        session.flush()
        assert find_alerts(session, now=_NOW, min_edge=0.02) == []

    def test_stale_anchor_is_silent(self, session: Session) -> None:
        # Anchor 34h before now: the scanner downgrades PLAY/UNVERIFIED to
        # STALE, which is not alertable.
        _seed_anchor(session, observed_at=_NOW - timedelta(hours=34))
        _seed_feed(session)
        session.flush()
        assert find_alerts(session, now=_NOW, min_edge=0.02) == []

    def test_no_feed_is_silent(self, session: Session) -> None:
        _seed_anchor(session)  # anchor only, nothing to verdict
        session.flush()
        assert find_alerts(session, now=_NOW, min_edge=0.02) == []

    def test_exhausted_daily_limit_suppresses_the_alert(
        self, session: Session
    ) -> None:
        _seed_anchor(session)
        _seed_feed(session)
        # A 25 PLN pick placed today exhausts the 5%-of-500 daily cap, so no
        # flat stake fits and the loop stays silent rather than alerting a
        # bet it could not place.
        session.add(
            Pick(
                created_at=_NOW,
                home_team="Someone",
                away_team="Else",
                kickoff_utc=_KICKOFF,
                market="1x2",
                selection="home",
                bookmaker="sts",
                stake_pln=Decimal("25"),
                price_quoted=Decimal("2.0"),
                tax_free=False,
                price_effective=Decimal("1.76"),
                placed_at=_NOW,
            )
        )
        session.flush()
        assert find_alerts(session, now=_NOW, min_edge=0.02) == []


class TestDelivery:
    def test_run_watch_sends_once_then_dedupes(self, session: Session) -> None:
        _seed_anchor(session)
        _seed_feed(session)
        session.flush()
        notifier = _FakeNotifier()

        first = run_watch(session, notifier, now=_NOW, min_edge=0.02)
        assert len(first.alerts) == 1
        assert len(notifier.messages) == 1
        assert "Betclic PL" in notifier.messages[0]
        assert "LEAD" in notifier.messages[0]  # UNVERIFIED renders as a lead
        # The auto-applied regime is named in the payload, with the order to
        # confirm it on the coupon — the regime is an assumption until seen.
        assert "Bez Podatku" in notifier.messages[0]
        assert (
            session.execute(select(func.count()).select_from(SentAlert)).scalar_one()
            == 1
        )

        second = run_watch(session, notifier, now=_NOW + timedelta(minutes=15),
                           min_edge=0.02)
        assert second.alerts == []
        assert second.suppressed_duplicates == 1
        assert len(notifier.messages) == 1  # not pinged again
