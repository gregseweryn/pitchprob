"""Recorder service tests — offline, in-memory SQLite, fake client."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.odds_api import OddsSnapshot, OddsTickRecord
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.recorder import record_snapshot

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
