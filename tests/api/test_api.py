"""API tests with an overridden session dependency (seeded in-memory SQLite)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from pitchprob.api.main import create_app
from pitchprob.api.routes import get_db
from pitchprob.data.orm import Base
from pitchprob.data.service import IngestionService

from ..data.test_normalize_and_service import FakeDownloader
from ..services.test_prediction import _league_csv


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        downloader = FakeDownloader(
            {"https://www.football-data.co.uk/mmz4281/2324/E0.csv": _league_csv()}
        )
        IngestionService(session=session, downloader=downloader).ingest_season("E0", 2023)

        app = create_app()
        app.dependency_overrides[get_db] = lambda: session
        yield TestClient(app)


class TestHealth:
    def test_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestLeagues:
    def test_lists_ingested_league(self, client: TestClient) -> None:
        response = client.get("/v1/leagues")
        assert response.status_code == 200
        leagues = response.json()
        assert len(leagues) == 1
        assert leagues[0]["code"] == "E0"
        assert leagues[0]["match_count"] > 0


class TestMatches:
    def test_paginated_listing(self, client: TestClient) -> None:
        response = client.get("/v1/matches", params={"league": "E0", "limit": 5})
        assert response.status_code == 200
        matches = response.json()
        assert len(matches) == 5
        first = matches[0]
        assert {"date", "home_team", "away_team", "ft_home", "ft_away"} <= set(first)


class TestTeams:
    def test_lists_canonical_names(self, client: TestClient) -> None:
        response = client.get("/v1/leagues/E0/teams")
        assert response.status_code == 200
        teams = response.json()
        assert "Arsenal" in teams
        assert teams == sorted(teams)

    def test_unknown_league_is_404(self, client: TestClient) -> None:
        response = client.get("/v1/leagues/XX/teams")
        assert response.status_code == 404


class TestCoupons:
    def test_generates_coupons_with_reasons_and_caveat(self, client: TestClient) -> None:
        response = client.post(
            "/v1/coupons",
            json={
                "tier": "high_risk",
                "max_legs": 2,
                "fixtures": [
                    {"league": "E0", "home_team": "Arsenal", "away_team": "Chelsea"},
                    {"league": "E0", "home_team": "Liverpool", "away_team": "Everton"},
                ],
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["tier"] == "high_risk"
        assert "independen" in payload["caveat"].lower()
        for coupon in payload["coupons"]:
            assert 0.20 <= coupon["joint_probability"] <= 0.40
            for leg in coupon["legs"]:
                assert leg["reasons"]

    def test_unknown_tier_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/coupons",
            json={"tier": "yolo", "fixtures": [
                {"league": "E0", "home_team": "Arsenal", "away_team": "Chelsea"}
            ]},
        )
        assert response.status_code == 422

    def test_unknown_team_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/v1/coupons",
            json={"tier": "safe", "fixtures": [
                {"league": "E0", "home_team": "Atlantis", "away_team": "Chelsea"}
            ]},
        )
        assert response.status_code == 404


class TestCors:
    def test_dashboard_origin_allowed(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


class TestPredictions:
    def test_full_market_book(self, client: TestClient) -> None:
        response = client.post(
            "/v1/predictions",
            json={"league": "E0", "home_team": "Arsenal", "away_team": "Chelsea"},
        )
        assert response.status_code == 200
        book = response.json()
        one_x_two = book["markets"]["1x2"]["dixon_coles"]
        assert one_x_two["home"] + one_x_two["draw"] + one_x_two["away"] == pytest.approx(1.0)
        assert "disclaimer" in book

    def test_unknown_team_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/v1/predictions",
            json={"league": "E0", "home_team": "Atlantis", "away_team": "Chelsea"},
        )
        assert response.status_code == 404

    def test_unknown_league_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/v1/predictions",
            json={"league": "XX", "home_team": "Arsenal", "away_team": "Chelsea"},
        )
        assert response.status_code == 404
