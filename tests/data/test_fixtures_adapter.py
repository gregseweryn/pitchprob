"""Fixtures adapter tests: football-data.co.uk/fixtures.csv rows have the
league/date/team columns and odds but no full-time result."""

import csv
import io
from datetime import date

from pitchprob.data.adapters.football_data_co_uk import FIXTURES_URL, parse_fixtures_csv

FIXTURE_COLUMNS = [
    "Div", "Date", "Time", "HomeTeam", "AwayTeam",
    "B365H", "B365D", "B365A", "B365>2.5", "B365<2.5",
]


def _fixtures_csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIXTURE_COLUMNS, restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


ROWS = [
    {
        "Div": "E0", "Date": "15/08/2026", "Time": "12:30",
        "HomeTeam": "Arsenal", "AwayTeam": "Chelsea",
        "B365H": "2.10", "B365D": "3.50", "B365A": "3.40",
        "B365>2.5": "1.80", "B365<2.5": "2.00",
    },
    {
        "Div": "SP1", "Date": "16/08/2026", "Time": "20:00",
        "HomeTeam": "Barcelona", "AwayTeam": "Sevilla",
        "B365H": "1.40", "B365D": "4.80", "B365A": "7.50",
        "B365>2.5": "1.60", "B365<2.5": "2.30",
    },
    {   # league we do not model — must be skipped, not crash
        "Div": "SC0", "Date": "16/08/2026", "Time": "16:00",
        "HomeTeam": "Celtic", "AwayTeam": "Rangers",
        "B365H": "1.90", "B365D": "3.60", "B365A": "4.00",
        "B365>2.5": "", "B365<2.5": "",
    },
]


class TestParseFixtures:
    def test_url(self) -> None:
        assert FIXTURES_URL == "https://www.football-data.co.uk/fixtures.csv"

    def test_known_leagues_only(self) -> None:
        fixtures = parse_fixtures_csv(_fixtures_csv(ROWS))
        assert len(fixtures) == 2
        assert {f.league_code for f in fixtures} == {"E0", "SP1"}

    def test_fields_and_odds(self) -> None:
        fixtures = parse_fixtures_csv(_fixtures_csv(ROWS))
        arsenal = next(f for f in fixtures if f.home_team == "Arsenal")
        assert arsenal.away_team == "Chelsea"
        assert arsenal.match_date == date(2026, 8, 15)
        assert arsenal.odds_1x2 == (2.10, 3.50, 3.40)

    def test_missing_odds_yield_none(self) -> None:
        rows = [dict(ROWS[0], B365H="", B365D="", B365A="")]
        fixtures = parse_fixtures_csv(_fixtures_csv(rows))
        assert fixtures[0].odds_1x2 is None

    def test_rows_without_teams_are_skipped(self) -> None:
        rows = [*ROWS, {"Div": "E0", "Date": "17/08/2026"}]
        fixtures = parse_fixtures_csv(_fixtures_csv(rows))
        assert len(fixtures) == 2
