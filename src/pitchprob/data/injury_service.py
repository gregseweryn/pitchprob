"""Injury annotation: API-Football /injuries → the injuries table (ADR 0008).

Strictly an annotator on the free-tier research corpus (seasons 2022-2024):
it never creates teams. Name resolution follows the same ladder as the xG
service — alias cache, override map, exact canonical, token-normalized
fallback — with successful resolutions written back to ``team_aliases``
under source ``api-football``. Unresolved names are reported, never dropped
silently.
"""

import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.api_football import injuries_params, parse_injuries
from pitchprob.data.normalize import api_football_canonical, normalized_tokens
from pitchprob.data.orm import Team, TeamAlias
from pitchprob.data.repository import InjuryRepository

logger = logging.getLogger(__name__)

SOURCE = "api-football"


class InjuriesClient(Protocol):
    def get_bytes(self, path: str, params: dict[str, int]) -> bytes: ...


def resolve_api_football_team(session: Session, name: str) -> Team | None:
    cached = session.execute(
        select(Team)
        .join(TeamAlias, TeamAlias.team_id == Team.id)
        .where(TeamAlias.source == SOURCE, TeamAlias.alias == name)
    ).scalar_one_or_none()
    if cached is not None:
        return cached

    canonical = api_football_canonical(name)
    team = session.execute(
        select(Team).where(Team.canonical_name == canonical)
    ).scalar_one_or_none()

    if team is None:
        target = normalized_tokens(canonical)
        for candidate in session.execute(select(Team)).scalars():
            if normalized_tokens(candidate.canonical_name) == target:
                team = candidate
                break
    if team is None:
        target = normalized_tokens(canonical)
        for alias_row in session.execute(select(TeamAlias)).scalars():
            if normalized_tokens(alias_row.alias) == target:
                team = session.get(Team, alias_row.team_id)
                break

    if team is not None:
        session.add(TeamAlias(team_id=team.id, source=SOURCE, alias=name))
        session.flush()
    return team


@dataclass
class InjuryUpdateReport:
    league_code: str
    season: int
    parsed: int = 0
    inserted: int = 0
    unmatched_teams: set[str] = field(default_factory=set)


class InjuryUpdateService:
    def __init__(self, *, session: Session, client: InjuriesClient) -> None:
        self.session = session
        self.client = client

    def update_league_season(self, league_code: str, season: int) -> InjuryUpdateReport:
        report = InjuryUpdateReport(league_code=league_code, season=season)
        params = injuries_params(league_code, season)
        records = parse_injuries(self.client.get_bytes("injuries", params))
        report.parsed = len(records)

        for record in records:
            team = resolve_api_football_team(self.session, record.team_name)
            if team is None:
                report.unmatched_teams.add(record.team_name)
                continue
            created = InjuryRepository.upsert(
                self.session,
                team_id=team.id,
                match_date=record.match_date,
                player_name=record.player_name,
                reason=record.reason,
                season=record.season,
            )
            report.inserted += int(created)

        self.session.commit()
        logger.info(
            "%s %d injuries: %d parsed, %d inserted, unknown teams: %s",
            league_code, season, report.parsed, report.inserted,
            sorted(report.unmatched_teams) or "-",
        )
        return report
