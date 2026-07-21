"""Recorder service tests — offline, in-memory SQLite, fake client."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api import (
    EventListing,
    EventSummary,
    OddsSnapshot,
    OddsTickRecord,
)
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.recorder import (
    import_tape,
    record_corners_to_csv,
    record_snapshot,
    record_snapshot_to_csv,
)

_COMMENCE = datetime(2026, 8, 15, 14, 0, tzinfo=UTC)


def _tick(bookmaker: str, selection: str, price: float) -> OddsTickRecord:
    return OddsTickRecord(
        sport_key="soccer_epl",
        event_id="ev1",
        commence_time=_COMMENCE,
        home_team="Arsenal",
        away_team="Leeds United",
        bookmaker=bookmaker,
        market="1x2",
        selection=selection,
        line=None,
        price=price,
    )


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot:
        self.calls.append((sport_key, markets))
        return OddsSnapshot(
            ticks=[
                replace(_tick("pinnacle", "home", 1.65), sport_key=sport_key),
                replace(_tick("betclic", "home", 1.62), sport_key=sport_key),
            ],
            requests_remaining=470,
        )


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestRecordSnapshot:
    def test_inserts_ticks_with_one_observed_at(self, session: Session) -> None:
        client = FakeClient()
        when = datetime(2026, 8, 14, 9, 30, tzinfo=UTC)
        summary = record_snapshot(
            session, client, leagues=["E0", "D1"],
            markets=["h2h"], observed_at=when,
        )
        rows = session.execute(select(OddsTick)).scalars().all()
        assert len(rows) == 4  # 2 ticks x 2 leagues
        # SQLite returns tz-naive datetimes (Postgres keeps the offset);
        # stored values are UTC by construction, so compare in UTC terms.
        assert all(
            row.observed_at.replace(tzinfo=UTC) == when for row in rows
        )
        assert all(row.source == "the-odds-api" for row in rows)
        assert rows[0].price == Decimal("1.650")
        assert summary.ticks_inserted == 4
        assert summary.events_seen == 2
        assert summary.bookmakers == ["betclic", "pinnacle"]
        assert summary.requests_remaining == 470
        # sport keys resolved from league codes
        assert [call[0] for call in client.calls] == [
            "soccer_epl", "soccer_germany_bundesliga",
        ]

    def test_unknown_league_raises_before_any_request(
        self, session: Session
    ) -> None:
        client = FakeClient()
        with pytest.raises(ValueError):
            record_snapshot(session, client, leagues=["XX"], markets=["h2h"])
        assert client.calls == []


class TestCsvTape:
    def test_csv_roundtrip_via_import(self, session: Session, tmp_path) -> None:
        client = FakeClient()
        when = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
        summary, path = record_snapshot_to_csv(
            client, leagues=["E0"], markets=["h2h"],
            directory=tmp_path, observed_at=when,
        )
        assert summary.ticks_inserted == 2
        assert path is not None and path.name == "20260814T080000Z.csv.gz"

        files, ticks = import_tape(session, tmp_path)
        assert (files, ticks) == (1, 2)
        rows = session.execute(select(OddsTick)).scalars().all()
        assert len(rows) == 2
        assert rows[0].price == Decimal("1.65")
        assert rows[0].home_team == "Arsenal"

        # idempotent: the same snapshot never imports twice
        files_again, ticks_again = import_tape(session, tmp_path)
        assert (files_again, ticks_again) == (0, 0)
        assert len(session.execute(select(OddsTick)).scalars().all()) == 2

    def test_empty_snapshot_writes_no_file(self, tmp_path) -> None:
        class EmptyClient:
            def fetch_odds(self, sport_key: str, *, markets: list[str]) -> OddsSnapshot:
                return OddsSnapshot(ticks=[], requests_remaining=400)

        summary, path = record_snapshot_to_csv(
            EmptyClient(), leagues=["E0"], markets=["h2h"], directory=tmp_path
        )
        assert summary.ticks_inserted == 0
        assert path is None
        assert list(tmp_path.iterdir()) == []


_NOW = datetime(2026, 8, 14, 20, 0, tzinfo=UTC)


class FakeCornersClient:
    """Fixture list is free; each priced fixture costs what it returns.

    ``priced`` names the fixtures that actually have corners quoted — the
    rest come back empty and, per the API's billing rule, free.
    """

    def __init__(self, *, remaining: int = 400, priced: set[str] | None = None):
        self.remaining = remaining
        self.priced = {"in-window"} if priced is None else priced
        self.listed: list[str] = []
        self.priced_calls: list[str] = []

    def fetch_events(self, sport_key: str) -> EventListing:
        self.listed.append(sport_key)
        return EventListing(
            events=[
                # 3h after `now`: inside any sane window
                EventSummary("in-window", _NOW + timedelta(hours=3), "A", "B"),
                # 5 days out: corners are not priced this early (measured)
                EventSummary("far", _NOW + timedelta(days=5), "C", "D"),
                # already kicked off: never worth a credit
                EventSummary("started", _NOW - timedelta(hours=1), "E", "F"),
            ],
            requests_remaining=self.remaining,
        )

    def fetch_event_odds(
        self, sport_key: str, event_id: str, *, markets: list[str]
    ) -> OddsSnapshot:
        self.priced_calls.append(event_id)
        if event_id not in self.priced:
            return OddsSnapshot(
                ticks=[], requests_remaining=self.remaining, credits_spent=0
            )
        self.remaining -= 2
        return OddsSnapshot(
            ticks=[
                replace(
                    _tick("pinnacle", "over", 1.75),
                    event_id=event_id,
                    sport_key=sport_key,
                    market="corners_ou",
                    line=Decimal("10.5"),
                ),
            ],
            requests_remaining=self.remaining,
            credits_spent=2,
        )


class TestCornersRecorder:
    def test_only_fixtures_inside_the_window_cost_a_credit(self, tmp_path) -> None:
        client = FakeCornersClient()
        summary, path = record_corners_to_csv(
            client, leagues=["E0"], within_hours=26,
            directory=tmp_path, now=_NOW,
        )
        assert client.listed == ["soccer_epl"]
        # the 5-day fixture and the started one are never requested
        assert client.priced_calls == ["in-window"]
        assert summary.credits_spent == 2
        assert summary.ticks_inserted == 1
        assert path is not None

    def test_unpriced_fixture_is_free_and_does_not_stop_the_run(
        self, tmp_path
    ) -> None:
        client = FakeCornersClient(priced=set())
        summary, path = record_corners_to_csv(
            client, leagues=["E0"], within_hours=26,
            directory=tmp_path, now=_NOW,
        )
        assert client.priced_calls == ["in-window"]
        assert summary.credits_spent == 0
        assert summary.ticks_inserted == 0
        assert path is None

    def test_quota_reserve_stops_before_any_paid_request(self, tmp_path) -> None:
        """The corners pilot must never starve the main tape: below the
        reserve it looks (free) and then declines to spend."""
        client = FakeCornersClient(remaining=55)
        summary, _path = record_corners_to_csv(
            client, leagues=["E0"], within_hours=26, reserve=60,
            directory=tmp_path, now=_NOW,
        )
        assert client.priced_calls == []
        assert summary.credits_spent == 0
        assert summary.stop_reason is not None
        assert "reserve" in summary.stop_reason

    def test_max_credits_is_a_hard_ceiling(self, tmp_path) -> None:
        client = FakeCornersClient(
            priced={"in-window", "second"}
        )
        original = client.fetch_events

        def two_in_window(sport_key: str) -> EventListing:
            listing = original(sport_key)
            return EventListing(
                events=[
                    *listing.events,
                    EventSummary("second", _NOW + timedelta(hours=4), "G", "H"),
                ],
                requests_remaining=client.remaining,
            )

        client.fetch_events = two_in_window  # type: ignore[method-assign]
        summary, _ = record_corners_to_csv(
            client, leagues=["E0"], within_hours=26, max_credits=2,
            directory=tmp_path, now=_NOW,
        )
        assert client.priced_calls == ["in-window"]
        assert summary.credits_spent == 2
        assert summary.stop_reason is not None
        assert "max-credits" in summary.stop_reason

    def test_corners_ticks_roundtrip_through_the_tape(
        self, session: Session, tmp_path
    ) -> None:
        record_corners_to_csv(
            FakeCornersClient(), leagues=["E0"], within_hours=26,
            directory=tmp_path, now=_NOW,
        )
        files, ticks = import_tape(session, tmp_path)
        assert (files, ticks) == (1, 1)
        row = session.execute(select(OddsTick)).scalars().one()
        assert row.market == "corners_ou"
        assert row.line == Decimal("10.5")
        assert row.observed_at.replace(tzinfo=UTC) == _NOW
