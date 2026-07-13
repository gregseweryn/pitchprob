"""Adapter for understat.com league-season pages.

Each page embeds the season's matches as ``var datesData = JSON.parse('…')``
where the JSON is a single-quoted JS string full of ``\\xNN`` escapes. One
request per league-season yields per-match xG for both sides — 60 requests
cover our entire corpus.

Only played matches (``isResult: true``) are returned. Kickoff datetimes are
reduced to dates; the xG service matches with ±1 day tolerance because the
source timezone does not always agree with football-data.co.uk's match dates.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

SOURCE = "understat"
BASE_URL = "https://understat.com/league"

#: football-data division code -> Understat league slug
LEAGUE_SLUGS: dict[str, str] = {
    "E0": "EPL",
    "SP1": "La_liga",
    "D1": "Bundesliga",
    "I1": "Serie_A",
    "F1": "Ligue_1",
}

_DATES_DATA_RE = re.compile(r"datesData\s*=\s*JSON\.parse\('(.*?)'\)", re.DOTALL)

_XG_PRECISION = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class UnderstatMatch:
    match_date: date
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    xg_home: Decimal
    xg_away: Decimal


def league_url(league_code: str, start_year: int) -> str:
    return f"{BASE_URL}/{LEAGUE_SLUGS[league_code]}/{start_year}"


def _decode_js_string(raw: str) -> str:
    decoded = raw.encode("utf-8").decode("unicode_escape")
    try:
        # unicode_escape mangles non-ASCII bytes; the latin-1 round-trip
        # restores proper UTF-8 where present and is a no-op for pure ASCII.
        return decoded.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return decoded


def parse_league_page(content: bytes) -> tuple[UnderstatMatch, ...]:
    html = content.decode("utf-8", errors="replace")
    found = _DATES_DATA_RE.search(html)
    if found is None:
        raise ValueError("no datesData block found — Understat page layout changed?")
    entries = json.loads(_decode_js_string(found.group(1)))

    records: list[UnderstatMatch] = []
    for entry in entries:
        if not entry.get("isResult"):
            continue
        kickoff = datetime.strptime(entry["datetime"], "%Y-%m-%d %H:%M:%S")
        records.append(
            UnderstatMatch(
                match_date=kickoff.date(),
                home_team=str(entry["h"]["title"]),
                away_team=str(entry["a"]["title"]),
                home_goals=int(entry["goals"]["h"]),
                away_goals=int(entry["goals"]["a"]),
                xg_home=Decimal(str(entry["xG"]["h"])).quantize(_XG_PRECISION),
                xg_away=Decimal(str(entry["xG"]["a"])).quantize(_XG_PRECISION),
            )
        )
    return tuple(records)
