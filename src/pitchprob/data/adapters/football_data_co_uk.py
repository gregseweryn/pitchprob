"""Adapter for football-data.co.uk season CSV files.

Files live at ``https://www.football-data.co.uk/mmz4281/{season}/{div}.csv``
(``season`` like ``2324``). Column meanings are documented in the site's
``notes.txt``. Realities this adapter absorbs:

- utf-8 vs latin-1 encodings (older files are latin-1),
- ``dd/mm/yyyy`` vs ``dd/mm/yy`` dates; kickoff Time (UK local) only from ~2019,
- Betbrain aggregate columns (``BbMx*``/``BbAv*``) in legacy files vs
  ``Max*``/``Avg*`` in modern ones,
- closing-odds columns (``*C*``) only in modern files,
- trailing blank columns and fully blank rows.

Rows that fail hard validation (date, teams, full-time goals) are quarantined
with a reason — never silently dropped.
"""

import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pitchprob.data.domain import MatchRecord, OddsQuoteRecord, ParseResult, QuarantinedRow

SOURCE = "football-data"
BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Kickoff times on football-data.co.uk are UK local time for all leagues.
_UK_TZ = ZoneInfo("Europe/London")

_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y")

_STAT_COLUMNS: dict[str, str] = {
    "HS": "shots_home",
    "AS": "shots_away",
    "HST": "shots_on_target_home",
    "AST": "shots_on_target_away",
    "HC": "corners_home",
    "AC": "corners_away",
    "HY": "yellows_home",
    "AY": "yellows_away",
    "HR": "reds_home",
    "AR": "reds_away",
    "HF": "fouls_home",
    "AF": "fouls_away",
}


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def csv_url(start_year: int, div: str) -> str:
    return f"{BASE_URL}/{season_code(start_year)}/{div}.csv"


@dataclass(frozen=True, slots=True)
class _OddsSpec:
    bookmaker: str
    market: str
    selection: str
    is_closing: bool
    price_cols: tuple[str, ...]  # first present, non-empty column wins
    line: Decimal | None = None  # fixed line (e.g. 2.5) …
    line_cols: tuple[str, ...] = ()  # … or dynamic line column (Asian handicap)


def _build_odds_specs() -> tuple[_OddsSpec, ...]:
    specs: list[_OddsSpec] = []

    # 1X2 --------------------------------------------------------------
    books_1x2: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "bet365": (("B365",), ("B365C",)),
        "pinnacle": (("PS",), ("PSC",)),
        "market_max": (("Max", "BbMx"), ("MaxC",)),
        "market_avg": (("Avg", "BbAv"), ("AvgC",)),
    }
    for book, (open_prefixes, close_prefixes) in books_1x2.items():
        for selection, suffix in (("home", "H"), ("draw", "D"), ("away", "A")):
            specs.append(
                _OddsSpec(book, "1x2", selection, False,
                          tuple(p + suffix for p in open_prefixes))
            )
            specs.append(
                _OddsSpec(book, "1x2", selection, True,
                          tuple(p + suffix for p in close_prefixes))
            )

    # Over/Under 2.5 -----------------------------------------------------
    books_ou: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "bet365": (("B365",), ("B365C",)),
        "pinnacle": (("P",), ("PC",)),
        "market_max": (("Max", "BbMx"), ("MaxC",)),
        "market_avg": (("Avg", "BbAv"), ("AvgC",)),
    }
    for book, (open_prefixes, close_prefixes) in books_ou.items():
        for selection, symbol in (("over", ">2.5"), ("under", "<2.5")):
            specs.append(
                _OddsSpec(book, "ou", selection, False,
                          tuple(p + symbol for p in open_prefixes), line=Decimal("2.5"))
            )
            specs.append(
                _OddsSpec(book, "ou", selection, True,
                          tuple(p + symbol for p in close_prefixes), line=Decimal("2.5"))
            )

    # Asian handicap (line applies to the home side) ---------------------
    open_line = ("AHh", "BbAHh")
    close_line = ("AHCh",)
    books_ah: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "bet365": (("B365AH",), ("B365CAH",)),
        "pinnacle": (("PAH",), ("PCAH",)),
        "market_max": (("MaxAH", "BbMxAH"), ("MaxCAH",)),
        "market_avg": (("AvgAH", "BbAvAH"), ("AvgCAH",)),
    }
    for book, (open_prefixes, close_prefixes) in books_ah.items():
        for selection, suffix in (("home", "H"), ("away", "A")):
            specs.append(
                _OddsSpec(book, "ah", selection, False,
                          tuple(p + suffix for p in open_prefixes), line_cols=open_line)
            )
            specs.append(
                _OddsSpec(book, "ah", selection, True,
                          tuple(p + suffix for p in close_prefixes), line_cols=close_line)
            )
    return tuple(specs)


_ODDS_SPECS = _build_odds_specs()


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _cell(row: dict[str, str | None], col: str) -> str:
    value = row.get(col)
    return value.strip() if isinstance(value, str) else ""


def _int_or_none(row: dict[str, str | None], col: str) -> int | None:
    text = _cell(row, col)
    if not text:
        return None
    try:
        return int(float(text))  # a few legacy files carry "3.0"
    except ValueError:
        return None


def _decimal_or_none(text: str) -> Decimal | None:
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _parse_date(text: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_kickoff(match_date: date, time_text: str) -> datetime | None:
    if not time_text:
        return None
    try:
        hh, mm = time_text.split(":")
        local = datetime.combine(match_date, time(int(hh), int(mm)), tzinfo=_UK_TZ)
    except ValueError:
        return None
    return local.astimezone(UTC)


def _parse_odds(row: dict[str, str | None]) -> tuple[OddsQuoteRecord, ...]:
    quotes: list[OddsQuoteRecord] = []
    for spec in _ODDS_SPECS:
        price = next(
            (p for c in spec.price_cols if (p := _decimal_or_none(_cell(row, c))) is not None),
            None,
        )
        if price is None:
            continue
        if spec.line_cols:
            line = next(
                (ln for c in spec.line_cols
                 if (ln := _decimal_or_none(_cell(row, c))) is not None),
                None,
            )
            if line is None:
                continue  # a handicap price without its line is meaningless
        else:
            line = spec.line if spec.line is not None else Decimal(0)
        quotes.append(
            OddsQuoteRecord(
                bookmaker=spec.bookmaker,
                market=spec.market,
                selection=spec.selection,
                price=price,
                line=line,
                is_closing=spec.is_closing,
            )
        )
    return tuple(quotes)


def parse_csv(content: bytes, *, start_year: int) -> ParseResult:
    """Parse one season file into validated match records + quarantined rows.

    ``start_year`` is currently only contextual (the caller maps records to a
    season); it is part of the signature so future validation can cross-check
    dates against the expected season window.
    """
    del start_year  # reserved for future season-window validation
    reader = csv.DictReader(io.StringIO(_decode(content)))
    records: list[MatchRecord] = []
    quarantined: list[QuarantinedRow] = []

    def quarantine(row: dict[str, str | None], row_number: int, reason: str) -> None:
        raw = ",".join(v or "" for k, v in row.items() if k is not None)
        quarantined.append(QuarantinedRow(row_number=row_number, reason=reason, raw=raw[:200]))

    for row_number, row in enumerate(reader, start=1):
        if not any(_cell(row, c) for c in ("Div", "Date", "HomeTeam", "AwayTeam")):
            continue  # blank/padding row

        match_date = _parse_date(_cell(row, "Date"))
        if match_date is None:
            quarantine(row, row_number, f"unparseable Date: {_cell(row, 'Date')!r}")
            continue
        home, away = _cell(row, "HomeTeam"), _cell(row, "AwayTeam")
        if not home or not away:
            quarantine(row, row_number, "missing HomeTeam/AwayTeam")
            continue
        ft_home = _int_or_none(row, "FTHG")
        ft_away = _int_or_none(row, "FTAG")
        if ft_home is None or ft_away is None:
            missing = "FTHG" if ft_home is None else "FTAG"
            quarantine(row, row_number, f"missing/invalid {missing}")
            continue

        stats = {field: _int_or_none(row, col) for col, field in _STAT_COLUMNS.items()}
        records.append(
            MatchRecord(
                match_date=match_date,
                kickoff_utc=_parse_kickoff(match_date, _cell(row, "Time")),
                home_team=home,
                away_team=away,
                ft_home=ft_home,
                ft_away=ft_away,
                ht_home=_int_or_none(row, "HTHG"),
                ht_away=_int_or_none(row, "HTAG"),
                odds=_parse_odds(row),
                **stats,
            )
        )

    return ParseResult(records=tuple(records), quarantined=tuple(quarantined))
