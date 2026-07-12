"""Team-name canonicalization.

football-data.co.uk abbreviates many club names; other sources (Understat,
API-Football — coming in later milestones) each spell them differently again.
Canonical names are the join key across sources, so ingestion always writes
the canonical name to ``teams`` and preserves the source spelling in
``team_aliases``.

The override map contains only names that differ from their canonical form;
everything else passes through unchanged.
"""

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
