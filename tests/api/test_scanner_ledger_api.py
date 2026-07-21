"""Scanner and ledger endpoints — the dashboard's data contract.

The assertion that matters most across this file is the boring one: every
response carries its caveats as payload (ADR 0004). A surface that renders
these numbers cannot render them bare, because the sentences arrive in the
same JSON.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from pitchprob.api.main import create_app
from pitchprob.api.routes import get_db
from pitchprob.data.orm import Base, OddsTick
from pitchprob.services.ledger import log_pick

_NOW = datetime.now(tz=UTC)
_KICKOFF = _NOW + timedelta(days=2)
_OBSERVED = _NOW - timedelta(hours=10)


def _seed_symmetric_totals(session: Session) -> None:
    """A 1.89/1.89 book: Shin fair is exactly 0.5, so every edge below is
    checkable by hand."""
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
                observed_at=_OBSERVED,
            )
        )
    session.flush()


@pytest.fixture()
def session():
    # StaticPool: TestClient serves requests on another thread, and without
    # a shared connection each thread would open its *own* empty :memory:
    # database — the tests that never touch the session first would then see
    # no tables at all.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client(session: Session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    return TestClient(app)


class TestScannerEvents:
    def test_lists_upcoming_tape_fixtures(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        response = client.get("/v1/scanner/events")
        assert response.status_code == 200
        (event,) = response.json()
        assert event["event_id"] == "ev1"
        assert event["home_team"] == "Arsenal"

    def test_an_empty_tape_lists_nothing_rather_than_erroring(
        self, client: TestClient
    ) -> None:
        response = client.get("/v1/scanner/events")
        assert response.status_code == 200
        assert response.json() == []


class TestScan:
    def _scan(self, client: TestClient, **overrides):
        body = {
            "market": "ou",
            "selection": "over",
            "line": "3.0",
            "event_id": "ev1",
            "quotes": [{"bookmaker": "betclic", "price": "2.10", "tax_free": True}],
        }
        body.update(overrides)
        return client.post("/v1/scanner/scan", json=body)

    def test_tax_free_edge_is_hand_computable(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        payload = self._scan(client).json()
        (verdict,) = payload["verdicts"]
        # fair 0.5 x price 2.10 (untaxed under the promo) - 1 = +5%
        assert verdict["edge"] == pytest.approx(0.05)
        assert verdict["verdict"] == "PLAY"
        assert Decimal(verdict["price_effective"]) == Decimal("2.10")
        assert verdict["tax_free"] is True

    def test_a_taxed_quote_at_the_same_price_loses(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        payload = self._scan(
            client,
            quotes=[{"bookmaker": "sts", "price": "2.10", "tax_free": False}],
        ).json()
        (verdict,) = payload["verdicts"]
        # 0.5 x (2.10 x 0.88) - 1 = -0.076
        assert verdict["edge"] == pytest.approx(-0.076)
        assert verdict["verdict"] == "NO BET"
        assert Decimal(verdict["price_effective"]) == Decimal("1.8480")

    def test_the_anchor_carries_its_freshness(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        anchor = self._scan(client).json()["anchor"]
        assert anchor["bookmaker"] == "pinnacle"
        assert anchor["fair_probability"] == pytest.approx(0.5)
        assert anchor["age_hours"] == pytest.approx(10.0, abs=0.1)

    def test_a_line_the_tape_does_not_quote_is_no_anchor(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        payload = self._scan(client, line="2.5").json()
        assert payload["anchor"] is None
        (verdict,) = payload["verdicts"]
        assert verdict["verdict"] == "NO ANCHOR"
        assert verdict["edge"] is None

    def test_an_unverified_feed_quote_cannot_reach_play(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        payload = self._scan(
            client,
            quotes=[
                {
                    "bookmaker": "Betclic PL",
                    "price": "2.10",
                    "tax_free": True,
                    "source": "feed",
                }
            ],
        ).json()
        (verdict,) = payload["verdicts"]
        assert verdict["source"] == "feed"
        assert verdict["verdict"] == "UNVERIFIED"

    def test_a_boost_reports_the_promo_value_separately(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        payload = self._scan(
            client,
            quotes=[
                {"bookmaker": "sts", "price": "2.10", "boosted_price": "2.40"}
            ],
        ).json()
        (verdict,) = payload["verdicts"]
        assert verdict["boosted"] is True
        assert verdict["promo_value"] == pytest.approx(0.132)

    def test_an_unknown_fixture_is_a_client_error(self, client: TestClient) -> None:
        response = self._scan(client, event_id="nope")
        assert response.status_code == 400
        assert "nope" in response.json()["detail"]

    def test_a_bad_selection_for_the_market_is_rejected(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        assert self._scan(client, selection="home").status_code == 400

    def test_caveats_travel_with_the_verdicts(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        caveats = self._scan(client).json()["caveats"]
        assert len(caveats) >= 4
        joined = " ".join(caveats)
        assert "effective prices" in joined
        assert "12%" in joined
        assert "STALE" in joined


class TestLedger:
    def _log(self, session: Session) -> None:
        _seed_symmetric_totals(session)
        log_pick(
            session,
            home_team="Arsenal",
            away_team="Coventry City",
            kickoff_utc=_KICKOFF,
            market="ou",
            selection="over",
            line=Decimal("3.0"),
            bookmaker="betclic",
            stake_pln=Decimal("5"),
            price_quoted=Decimal("2.10"),
            tax_free=True,
            placed_at=_NOW,
            event_id="ev1",
        )

    def test_picks_carry_the_clv_decomposition_field(
        self, client: TestClient, session: Session
    ) -> None:
        self._log(session)
        payload = client.get("/v1/ledger").json()
        (pick,) = payload["picks"]
        assert pick["bookmaker"] == "betclic"
        assert Decimal(pick["price_effective"]) == Decimal("2.10")
        assert Decimal(pick["price_sharp"]) == Decimal("1.890")
        # not kicked off yet: CLV is genuinely absent, not zero
        assert pick["clv_exec"] is None
        assert pick["clv_shopping"] is None
        assert pick["risk_override"] is False

    def test_the_weekly_report_rides_along(
        self, client: TestClient, session: Session
    ) -> None:
        self._log(session)
        weekly = client.get("/v1/ledger").json()["weekly"]
        assert weekly["window"]["n_picks"] == 1
        assert weekly["drawdown"]["breaker_tripped"] is False
        assert weekly["tax_free"]["betclic"]["used_pln"] == pytest.approx(5.0)
        # too few weeks to publish an interval — the report says so
        assert weekly["clv"]["exec_ci"] is None

    def test_an_empty_ledger_is_a_valid_response(self, client: TestClient) -> None:
        payload = client.get("/v1/ledger").json()
        assert payload["picks"] == []
        assert payload["summary"]["n_picks"] == 0
        assert payload["caveats"]

    def test_caveats_lead_with_clv_not_roi(self, client: TestClient) -> None:
        joined = " ".join(client.get("/v1/ledger").json()["caveats"])
        assert "decision variable" in joined
        assert "clv_sharp before clv_exec" in joined


class TestLogPickEndpoint:
    """Logging a bet from the browser.

    The load-bearing property: the risk layer binds here exactly as it does
    in the CLI. If the endpoint could write a pick the CLI would refuse,
    the dashboard would be a way around the project's own safeguards —
    worse than having no dashboard.
    """

    def _body(self, **overrides):
        body = {
            "home_team": "Arsenal",
            "away_team": "Coventry City",
            "kickoff_utc": _KICKOFF.isoformat(),
            "market": "ou",
            "selection": "over",
            "line": "3.0",
            "bookmaker": "betclic",
            "stake_pln": "5",
            "price_quoted": "2.10",
            "tax_free": True,
            "event_id": "ev1",
        }
        body.update(overrides)
        return body

    def test_a_clean_pick_is_stored_with_its_sharp_anchor(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        response = client.post("/v1/ledger/picks", json=self._body())
        assert response.status_code == 201, response.text
        pick = response.json()
        assert Decimal(pick["price_effective"]) == Decimal("2.10")
        assert Decimal(pick["price_sharp"]) == Decimal("1.890")
        assert pick["risk_override"] is False
        assert client.get("/v1/ledger").json()["summary"]["n_picks"] == 1

    def test_a_second_bet_on_the_same_fixture_is_refused_with_the_reason(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        assert client.post("/v1/ledger/picks", json=self._body()).status_code == 201
        clash = client.post(
            "/v1/ledger/picks",
            json=self._body(market="1x2", selection="home", line=None),
        )
        # 409, not 400: the request is well-formed, the ledger's state
        # forbids it — and the operator needs to know *which* limit.
        assert clash.status_code == 409
        assert "max_match_stake" in clash.json()["detail"]

    def test_the_refused_bet_leaves_no_row(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        client.post("/v1/ledger/picks", json=self._body())
        client.post(
            "/v1/ledger/picks",
            json=self._body(market="1x2", selection="home", line=None),
        )
        assert client.get("/v1/ledger").json()["summary"]["n_picks"] == 1

    def test_a_stake_outside_the_band_is_refused(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        response = client.post("/v1/ledger/picks", json=self._body(stake_pln="50"))
        assert response.status_code == 409
        assert "max_stake" in response.json()["detail"]

    def test_an_override_is_stored_and_marked(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        response = client.post(
            "/v1/ledger/picks", json=self._body(stake_pln="50", override_risk=True)
        )
        assert response.status_code == 201
        pick = response.json()
        assert pick["risk_override"] is True
        assert pick["risk_note"] is not None and "max_stake" in pick["risk_note"]

    def test_a_malformed_bet_is_a_400_not_a_409(
        self, client: TestClient, session: Session
    ) -> None:
        """Wrong selection for the market is the caller's mistake, not a
        limit — the two must not be confused, or the UI cannot tell the
        operator whether to fix the form or accept the refusal."""
        _seed_symmetric_totals(session)
        response = client.post(
            "/v1/ledger/picks", json=self._body(selection="home")
        )
        assert response.status_code == 400

    def test_the_tax_free_allowance_still_guards(
        self, client: TestClient, session: Session
    ) -> None:
        _seed_symmetric_totals(session)
        client.post(
            "/v1/ledger/picks",
            json=self._body(stake_pln="998", override_risk=True),
        )
        response = client.post("/v1/ledger/picks", json=self._body())
        assert response.status_code == 400
        assert "allowance" in response.json()["detail"]
