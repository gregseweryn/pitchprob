"""Adapter tests for football-data.co.uk CSV parsing.

Fixtures are generated programmatically to mirror the real column layouts:
the modern (2019+) layout with kickoff Time and closing-odds columns, and the
legacy layout with 2-digit years and Betbrain (BbMx/BbAv) aggregate columns.
"""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal

from pitchprob.data.adapters.football_data_co_uk import csv_url, parse_csv, season_code

# Kept as a CSV-header string on purpose: it mirrors the real file layout.
MODERN_COLUMNS = [
    "Div",
    "Date",
    "Time",
    "HomeTeam",
    "AwayTeam",
    "FTHG",
    "FTAG",
    "FTR",
    "HTHG",
    "HTAG",
    "HTR",
    "HS",
    "AS",
    "HST",
    "AST",
    "HF",
    "AF",
    "HC",
    "AC",
    "HY",
    "AY",
    "HR",
    "AR",
    "B365H",
    "B365D",
    "B365A",
    "PSH",
    "PSD",
    "PSA",
    "MaxH",
    "MaxD",
    "MaxA",
    "AvgH",
    "AvgD",
    "AvgA",
    "B365>2.5",
    "B365<2.5",
    "P>2.5",
    "P<2.5",
    "Max>2.5",
    "Max<2.5",
    "Avg>2.5",
    "Avg<2.5",
    "AHh",
    "B365AHH",
    "B365AHA",
    "PAHH",
    "PAHA",
    "B365CH",
    "B365CD",
    "B365CA",
    "PSCH",
    "PSCD",
    "PSCA",
    "MaxCH",
    "MaxCD",
    "MaxCA",
    "AvgCH",
    "AvgCD",
    "AvgCA",
    "B365C>2.5",
    "B365C<2.5",
    "PC>2.5",
    "PC<2.5",
    "AHCh",
    "B365CAHH",
    "B365CAHA",
    "PCAHH",
    "PCAHA",
]

BURNLEY_CITY = {
    "Div": "E0",
    "Date": "12/08/2023",
    "Time": "12:30",
    "HomeTeam": "Burnley",
    "AwayTeam": "Man City",
    "FTHG": "0",
    "FTAG": "3",
    "FTR": "A",
    "HTHG": "0",
    "HTAG": "2",
    "HTR": "A",
    "HS": "6",
    "AS": "17",
    "HST": "1",
    "AST": "8",
    "HF": "10",
    "AF": "11",
    "HC": "2",
    "AC": "7",
    "HY": "1",
    "AY": "2",
    "HR": "0",
    "AR": "0",
    "B365H": "9.00",
    "B365D": "5.75",
    "B365A": "1.30",
    "PSH": "9.87",
    "PSD": "5.95",
    "PSA": "1.31",
    "MaxH": "10.00",
    "MaxD": "6.00",
    "MaxA": "1.32",
    "AvgH": "9.20",
    "AvgD": "5.70",
    "AvgA": "1.30",
    "B365>2.5": "1.62",
    "B365<2.5": "2.30",
    "P>2.5": "1.65",
    "P<2.5": "2.36",
    "Max>2.5": "1.68",
    "Max<2.5": "2.40",
    "Avg>2.5": "1.63",
    "Avg<2.5": "2.31",
    "AHh": "1.75",
    "B365AHH": "2.05",
    "B365AHA": "1.85",
    "PAHH": "2.06",
    "PAHA": "1.87",
    "B365CH": "8.50",
    "B365CD": "5.50",
    "B365CA": "1.33",
    "PSCH": "9.10",
    "PSCD": "5.80",
    "PSCA": "1.34",
    "MaxCH": "9.50",
    "MaxCD": "6.00",
    "MaxCA": "1.35",
    "AvgCH": "8.80",
    "AvgCD": "5.60",
    "AvgCA": "1.33",
    "B365C>2.5": "1.60",
    "B365C<2.5": "2.35",
    "PC>2.5": "1.63",
    "PC<2.5": "2.38",
    "AHCh": "2.00",
    "B365CAHH": "1.90",
    "B365CAHA": "2.02",
    "PCAHH": "1.89",
    "PCAHA": "2.03",
}


def _modern_csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MODERN_COLUMNS, restval="")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def test_season_code_and_url() -> None:
    assert season_code(2023) == "2324"
    assert season_code(1999) == "9900"
    assert csv_url(2023, "E0") == "https://www.football-data.co.uk/mmz4281/2324/E0.csv"


def test_parse_modern_row_core_fields() -> None:
    result = parse_csv(_modern_csv([BURNLEY_CITY]), start_year=2023)
    assert not result.quarantined
    (rec,) = result.records
    assert rec.home_team == "Burnley"
    assert rec.away_team == "Man City"
    assert rec.match_date == date(2023, 8, 12)
    # 12:30 UK time in August (BST, UTC+1) -> 11:30 UTC
    assert rec.kickoff_utc == datetime(2023, 8, 12, 11, 30, tzinfo=UTC)
    assert (rec.ft_home, rec.ft_away) == (0, 3)
    assert (rec.ht_home, rec.ht_away) == (0, 2)
    assert (rec.shots_home, rec.shots_away) == (6, 17)
    assert (rec.corners_home, rec.corners_away) == (2, 7)
    assert (rec.reds_home, rec.reds_away) == (0, 0)


def _quotes(rec, bookmaker: str, market: str, is_closing: bool):
    return {
        (q.selection, q.line): q.price
        for q in rec.odds
        if q.bookmaker == bookmaker and q.market == market and q.is_closing == is_closing
    }


def test_parse_modern_row_odds() -> None:
    result = parse_csv(_modern_csv([BURNLEY_CITY]), start_year=2023)
    (rec,) = result.records

    b365_open = _quotes(rec, "bet365", "1x2", is_closing=False)
    assert b365_open[("home", Decimal("0"))] == Decimal("9.00")
    assert b365_open[("away", Decimal("0"))] == Decimal("1.30")

    ps_close = _quotes(rec, "pinnacle", "1x2", is_closing=True)
    assert ps_close[("draw", Decimal("0"))] == Decimal("5.80")

    ou_close = _quotes(rec, "pinnacle", "ou", is_closing=True)
    assert ou_close[("over", Decimal("2.5"))] == Decimal("1.63")
    assert ou_close[("under", Decimal("2.5"))] == Decimal("2.38")

    ah_open = _quotes(rec, "bet365", "ah", is_closing=False)
    assert ah_open[("home", Decimal("1.75"))] == Decimal("2.05")

    ah_close = _quotes(rec, "pinnacle", "ah", is_closing=True)
    assert ah_close[("home", Decimal("2.00"))] == Decimal("1.89")

    market_open = _quotes(rec, "market_max", "1x2", is_closing=False)
    assert market_open[("home", Decimal("0"))] == Decimal("10.00")


def test_missing_goals_row_is_quarantined() -> None:
    bad = dict(BURNLEY_CITY, FTHG="", Date="13/08/2023")
    result = parse_csv(_modern_csv([BURNLEY_CITY, bad]), start_year=2023)
    assert len(result.records) == 1
    assert len(result.quarantined) == 1
    assert "FTHG" in result.quarantined[0].reason


def test_blank_rows_are_skipped_silently() -> None:
    content = _modern_csv([BURNLEY_CITY]) + b"\r\n,,,,\r\n"
    result = parse_csv(content, start_year=2023)
    assert len(result.records) == 1
    assert not result.quarantined


def test_legacy_format_two_digit_years_and_betbrain() -> None:
    legacy_cols = [
        "Div",
        "Date",
        "HomeTeam",
        "AwayTeam",
        "FTHG",
        "FTAG",
        "FTR",
        "B365H",
        "B365D",
        "B365A",
        "BbMxH",
        "BbAvH",
        "BbMxD",
        "BbAvD",
        "BbMxA",
        "BbAvA",
        "BbMx>2.5",
        "BbAv>2.5",
        "BbMx<2.5",
        "BbAv<2.5",
        "BbAHh",
        "BbMxAHH",
        "BbAvAHH",
        "BbMxAHA",
        "BbAvAHA",
    ]
    row = {
        "Div": "E0",
        "Date": "19/08/00",
        "HomeTeam": "Charlton",
        "AwayTeam": "Man City",
        "FTHG": "4",
        "FTAG": "0",
        "FTR": "H",
        "B365H": "2.20",
        "B365D": "3.25",
        "B365A": "3.10",
        "BbMxH": "2.30",
        "BbAvH": "2.20",
        "BbMxD": "3.30",
        "BbAvD": "3.20",
        "BbMxA": "3.20",
        "BbAvA": "3.05",
        "BbMx>2.5": "2.10",
        "BbAv>2.5": "2.00",
        "BbMx<2.5": "1.85",
        "BbAv<2.5": "1.80",
        "BbAHh": "-0.25",
        "BbMxAHH": "1.95",
        "BbAvAHH": "1.90",
        "BbMxAHA": "2.00",
        "BbAvAHA": "1.93",
    }
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=legacy_cols)
    writer.writeheader()
    writer.writerows([row])
    # legacy files are latin-1 encoded
    result = parse_csv(buf.getvalue().encode("latin-1"), start_year=2000)
    (rec,) = result.records
    assert rec.match_date == date(2000, 8, 19)
    assert rec.kickoff_utc is None
    assert rec.shots_home is None

    max_1x2 = _quotes(rec, "market_max", "1x2", is_closing=False)
    assert max_1x2[("home", Decimal("0"))] == Decimal("2.30")
    ah = _quotes(rec, "market_avg", "ah", is_closing=False)
    assert ah[("home", Decimal("-0.25"))] == Decimal("1.90")
    assert not _quotes(rec, "pinnacle", "1x2", is_closing=False)


def test_latin1_fallback_decoding() -> None:
    content = _modern_csv([BURNLEY_CITY]).replace(b"Burnley", b"Burnl\xe9y")
    result = parse_csv(content, start_year=2023)
    (rec,) = result.records
    assert rec.home_team == "Burnléy"
