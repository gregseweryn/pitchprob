"""Team-name canonicalization.

football-data.co.uk abbreviates many club names; other sources (Understat,
API-Football — coming in later milestones) each spell them differently again.
Canonical names are the join key across sources, so ingestion always writes
the canonical name to ``teams`` and preserves the source spelling in
``team_aliases``.

The override map contains only names that differ from their canonical form;
everything else passes through unchanged.
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

_CANONICAL_OVERRIDES: dict[str, str] = {
    # England
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Newcastle": "Newcastle United",
    "Nott'm Forest": "Nottingham Forest",
    "QPR": "Queens Park Rangers",
    "Sheffield United": "Sheffield United",
    "Sheffield Weds": "Sheffield Wednesday",
    "Tottenham": "Tottenham Hotspur",
    "West Brom": "West Bromwich Albion",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
    "Leeds": "Leeds United",
    "Leicester": "Leicester City",
    "Norwich": "Norwich City",
    "Ipswich": "Ipswich Town",
    "Luton": "Luton Town",
    # Spain
    "Ath Bilbao": "Athletic Bilbao",
    "Ath Madrid": "Atletico Madrid",
    "Atletico Madrid B": "Atletico Madrid B",
    "Betis": "Real Betis",
    "Celta": "Celta Vigo",
    "Espanol": "Espanyol",
    "La Coruna": "Deportivo La Coruna",
    "Sociedad": "Real Sociedad",
    "Vallecano": "Rayo Vallecano",
    # Germany
    "Bayern Munich": "Bayern Munich",
    "Dortmund": "Borussia Dortmund",
    "Ein Frankfurt": "Eintracht Frankfurt",
    "FC Koln": "FC Cologne",
    "Fortuna Dusseldorf": "Fortuna Dusseldorf",
    "Greuther Furth": "Greuther Furth",
    "Hertha": "Hertha Berlin",
    "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia Monchengladbach",
    "Mainz": "Mainz 05",
    "RB Leipzig": "RB Leipzig",
    "Stuttgart": "VfB Stuttgart",
    # Italy
    "Milan": "AC Milan",
    "Roma": "AS Roma",
    "Spal": "SPAL",
    "Verona": "Hellas Verona",
    # France
    "Clermont": "Clermont Foot",
    "Paris SG": "Paris Saint-Germain",
    "St Etienne": "Saint-Etienne",
}


def canonical_team_name(alias: str) -> str:
    return _CANONICAL_OVERRIDES.get(alias, alias)


#: Understat spelling -> our canonical name, for names that neither match
#: exactly nor survive token normalization. Extended from real unmatched-team
#: reports; keep alphabetical within league blocks.
_UNDERSTAT_OVERRIDES: dict[str, str] = {
    # England
    "Leeds": "Leeds United",
    "Leicester": "Leicester City",
    "Luton": "Luton Town",
    "Ipswich": "Ipswich Town",
    "Norwich": "Norwich City",
    "Tottenham": "Tottenham Hotspur",
    "West Ham": "West Ham United",
    # Spain
    "Athletic Club": "Athletic Bilbao",
    "SD Huesca": "Huesca",
    "Sporting Gijon": "Sp Gijon",
    "Real Valladolid": "Valladolid",
    "Real Oviedo": "Oviedo",
    # Germany
    "Arminia Bielefeld": "Bielefeld",
    "Borussia M.Gladbach": "Borussia Monchengladbach",
    "FC Heidenheim": "Heidenheim",
    "Fortuna Duesseldorf": "Fortuna Dusseldorf",
    "Greuther Fuerth": "Greuther Furth",
    "Hamburger SV": "Hamburg",
    "Hannover 96": "Hannover",
    "Nuernberg": "Nurnberg",
    "RasenBallsport Leipzig": "RB Leipzig",
    "St. Pauli": "St Pauli",
    # Italy
    "Parma Calcio 1913": "Parma",
    "Roma": "AS Roma",
    "SPAL 2013": "SPAL",
    # France
    "Paris Saint Germain": "Paris Saint-Germain",
    "SC Bastia": "Bastia",
    "GFC Ajaccio": "Ajaccio GFCO",  # Gazélec — distinct from AC "Ajaccio"
}


def understat_canonical(name: str) -> str:
    return _UNDERSTAT_OVERRIDES.get(name, name)


#: API-Football team names that differ from our canonical spellings beyond
#: what token normalization bridges. Extended iteratively from ingestion
#: reports, exactly like the Understat map.
_API_FOOTBALL_OVERRIDES: dict[str, str] = {
    # England
    "Athletic Club": "Athletic Bilbao",
    "Sheffield Utd": "Sheffield United",
    "Wolves": "Wolverhampton Wanderers",
    # France
    "Estac Troyes": "Troyes",
    "Paris Saint Germain": "Paris Saint-Germain",
    "Stade Brestois 29": "Brest",
    # Germany
    "1. FC Heidenheim": "Heidenheim",
    "1.FC Köln": "FC Cologne",
    "Bayern München": "Bayern Munich",
    "Borussia Mönchengladbach": "Borussia Monchengladbach",
    "FC Köln": "FC Cologne",
    "FSV Mainz 05": "Mainz 05",
    "SC Freiburg": "Freiburg",
    "SV Darmstadt 98": "Darmstadt",
    "SV Elversberg": "Elversberg",
    "VfL BOCHUM": "Bochum",
    "VfL Bochum": "Bochum",
    "Vfl Bochum": "Bochum",
    "VfL Wolfsburg": "Wolfsburg",
}


def api_football_canonical(name: str) -> str:
    return _API_FOOTBALL_OVERRIDES.get(name, name)


#: The Odds API (odds tape, ADR 0012) team names that differ from canonical
#: spellings. The tape stores raw naming by design; this map is applied at
#: analysis time (pick auto-settlement) and grows iteratively from the
#: ledger's "unmatched" reports, exactly like the other source maps.
_ODDS_API_OVERRIDES: dict[str, str] = {
    # England
    "AFC Bournemouth": "Bournemouth",
    "Brighton and Hove Albion": "Brighton",
    # Spain
    "Atlético Madrid": "Atletico Madrid",
    "Real Betis Balompié": "Real Betis",
    # Germany
    "Bayern München": "Bayern Munich",
    "Borussia Mönchengladbach": "Borussia Monchengladbach",
    "FC Köln": "FC Cologne",
    "1. FSV Mainz 05": "Mainz 05",
    "VfL Wolfsburg": "Wolfsburg",
    "VfL Bochum": "Bochum",
    "SC Freiburg": "Freiburg",
    # Italy
    "Inter Milan": "Inter",
    # France
    "Paris Saint Germain": "Paris Saint-Germain",
}


def odds_api_canonical(name: str) -> str:
    return _ODDS_API_OVERRIDES.get(name, name)


#: Below this score a candidate is noise, not a suggestion. Set so that a
#: genuinely foreign name (no shared tokens, weak string similarity) can
#: never clear it: with zero token overlap the score is capped at 0.4.
_MIN_SUGGESTION_SCORE = 0.45


def suggest_canonical(
    name: str, candidates: Sequence[str], *, limit: int = 3
) -> list[tuple[str, float]]:
    """Ranked canonical-name candidates for an unmatched source name.

    The season procedure behind it (runbook): ``pick settle`` lists tape
    names it could not bridge to ``matches``; this turns each into a short
    ranked list so extending ``_ODDS_API_OVERRIDES`` is a lookup, not a
    hunt. Advisory only — the operator confirms before the map grows, which
    is why the score travels with the name.

    Scoring: shared normalized tokens dominate (0.6 weight — "Manchester
    City" and "Man City" share "city", and that is stronger evidence than
    any string distance), with a difflib ratio on the normalized strings as
    the tiebreaker (0.4). Token overlap is measured against the *query's*
    tokens, because the tape name is usually the longer, fuller form.
    """
    query_tokens = set(normalized_tokens(name))
    query_joined = " ".join(normalized_tokens(name))
    scored: list[tuple[str, float]] = []
    for candidate in candidates:
        candidate_tokens = set(normalized_tokens(candidate))
        overlap = (
            len(query_tokens & candidate_tokens) / len(query_tokens)
            if query_tokens
            else 0.0
        )
        ratio = SequenceMatcher(
            None, query_joined, " ".join(normalized_tokens(candidate))
        ).ratio()
        score = 0.6 * overlap + 0.4 * ratio
        if score >= _MIN_SUGGESTION_SCORE:
            scored.append((candidate, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


_NOISE_TOKENS = frozenset(
    {"fc", "cf", "sd", "sv", "ac", "as", "calcio", "1913", "2013", "04", "05", "96", "1899"}
)


def normalized_tokens(name: str) -> tuple[str, ...]:
    """Punctuation-free, lowercased, noise-word-free token tuple used as a
    last-resort cross-source join key ("Parma Calcio 1913" == "Parma")."""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return tuple(token for token in cleaned.split() if token not in _NOISE_TOKENS)
