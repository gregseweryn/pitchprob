"""odds-api.io feed poller tests (ADR 0016) — offline, fake client.

The poller is the fuel line for the speaking loop: sweep upcoming PL
fixtures, keep those inside the kickoff window, append each selected book's
quotes to the tape as source=odds-api-io. Two things are load-bearing: the
window filter (so a far-future fixture is not priced), and the request cap
(free tier is 100 req/h, so a sweep must be bounded and say when it stopped).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.recorder import record_oddsio_snapshot

_NOW = datetime(2026, 8, 20, 18, 0, tzinfo=UTC)


def _payload(event_id: str, date_iso: str) -> dict[str, object]:
    return {
        "id": event_id,
        "home": "Arsenal",
        "away": "Everton",
        "date": date_iso,
        "league": {"slug": "soccer_epl"},
        "bookmakers": {
            "Betclic PL": [
                {"name": "Totals", "odds": [{"hdp": 3.0, "over": 2.40, "under": 1.55}]}
            ]
        },
    }


class _FakeClient:
    def __init__(
        self, payloads: dict[str, dict[str, object]], selected: list[str] | None = None
    ) -> None:
        self._payloads = payloads
        self._selected = selected if selected is not None else ["Betclic PL"]
        self.fetch_calls: list[str] = []
        self.fetch_books: list[list[str]] = []

    def selected_bookmakers(self) -> object:
        return {"bookmakers": self._selected, "count": len(self._selected)}

    def list_events(
        self, *, sport: str = "football", league: str | None = None
    ) -> list[dict[str, object]]:
        return list(self._payloads.values())

    def fetch_odds(self, event_id: str, *, bookmakers: list[str]) -> object:
        self.fetch_calls.append(event_id)
        self.fetch_books.append(bookmakers)
        return self._payloads[event_id]


@pytest.fixture()
def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_only_in_window_fixtures_are_priced(session: Session) -> None:
    client = _FakeClient(
        {
            "e1": _payload("e1", "2026-08-21T19:00:00Z"),  # +25h, in window
            "e2": _payload("e2", "2026-09-05T19:00:00Z"),  # far future, out
        },
        selected=["Betclic PL"],
    )
    # books=None -> the poller uses the key's selected books, never a hardcoded
    # default that could name an unselected book and 403 the whole sweep.
    # anchor_only=False here: this test isolates the window/book logic.
    summary = record_oddsio_snapshot(
        session, client, within_hours=48, anchor_only=False, now=_NOW
    )
    assert summary.events_in_window == 1
    assert summary.events_priced == 1
    assert client.fetch_calls == ["e1"]  # e2 never requested
    assert client.fetch_books == [["Betclic PL"]]  # resolved from selection
    rows = session.execute(select(OddsTick)).scalars().all()
    assert len(rows) == 2  # over + under
    assert {r.source for r in rows} == {"odds-api-io"}
    assert {r.bookmaker for r in rows} == {"Betclic PL"}
    assert {r.market for r in rows} == {"ou"}
    assert summary.stop_reason is None


def test_request_cap_bounds_the_sweep(session: Session) -> None:
    payloads = {
        f"e{i}": _payload(f"e{i}", (_NOW + timedelta(hours=i)).isoformat())
        for i in range(1, 6)
    }
    client = _FakeClient(payloads)
    summary = record_oddsio_snapshot(
        session,
        client,
        books=["Betclic PL"],
        within_hours=48,
        max_events=2,
        anchor_only=False,
        now=_NOW,
    )
    assert summary.events_requested == 2
    assert len(client.fetch_calls) == 2
    assert summary.stop_reason is not None


def test_anchor_only_skips_fixtures_the_tape_cannot_anchor(
    session: Session,
) -> None:
    # Two in-window feed fixtures; only one has a Pinnacle anchor on the tape.
    session.add(
        OddsTick(
            source="the-odds-api",
            sport_key="soccer_epl",
            event_id="anchor1",
            commence_time=datetime(2026, 8, 21, 19, 0, tzinfo=UTC),
            home_team="Arsenal",
            away_team="Everton",
            bookmaker="pinnacle",
            market="1x2",
            selection="home",
            line=None,
            price=Decimal("2.00"),
            observed_at=_NOW,
        )
    )
    session.flush()
    client = _FakeClient(
        {
            "e1": _payload("e1", "2026-08-21T19:00:00Z"),  # Arsenal vs Everton
            "e2": {  # a friendly the tape never anchors
                **_payload("e2", "2026-08-21T19:00:00Z"),
                "home": "Some FC",
                "away": "Other FC",
            },
        }
    )
    summary = record_oddsio_snapshot(
        session, client, books=["Betclic PL"], within_hours=48, now=_NOW
    )
    assert client.fetch_calls == ["e1"]  # e2 (no anchor) never requested
    assert summary.events_in_window == 1
