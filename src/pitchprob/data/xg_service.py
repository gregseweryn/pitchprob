"""xG annotation: Understat league pages → existing match rows.

Strictly an *annotator*: it never creates teams or matches. Team names resolve
through (1) the understat alias cache, (2) the explicit override map, (3) an
exact canonical-name match, (4) token-normalized matching against canonical
names and known aliases. Successful non-cache resolutions are written back to
``team_aliases`` so subsequent runs take the fast path. Unresolved names and
unmatched fixtures are reported, never silently dropped.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.data.adapters.understat import (
    SOURCE,
    UnderstatMatch,
    league_url,
    parse_league_page,
)
from pitchprob.data.normalize import normalized_tokens, understat_canonical
from pitchprob.data.orm import League, Match, Season, Team, TeamAlias
from pitchprob.data.service import Downloader

logger = logging.getLogger(__name__)


def resolve_understat_team(session: Session, name: str) -> Team | None:
    cached = session.execute(
        select(Team)
        .join(TeamAlias, TeamAlias.team_id == Team.id)
        .where(TeamAlias.source == SOURCE, TeamAlias.alias == name)
    ).scalar_one_or_none()
    if cached is not None:
        return cached

    canonical = understat_canonical(name)
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
class XgUpdateReport:
    league_code: str
    start_year: int
    parsed: int = 0
    matched: int = 0
    unmatched_matches: int = 0
    unmatched_teams: set[str] = field(default_factory=set)


class XgUpdateService:
    def __init__(self, *, session: Session, downloader: Downloader) -> None:
        self.session = session
        self.downloader = downloader

    def _match_index(self, league_code: str, start_year: int) -> dict[tuple[int, int, date], Match]:
        rows = self.session.execute(
            select(Match)
            .join(Season, Match.season_id == Season.id)
            .join(League, Season.league_id == League.id)
            .where(League.code == league_code, Season.start_year == start_year)
        ).scalars()
        return {(m.home_team_id, m.away_team_id, m.match_date): m for m in rows}

    def update_league_season(self, league_code: str, start_year: int) -> XgUpdateReport:
        report = XgUpdateReport(league_code=league_code, start_year=start_year)
        records = parse_league_page(self.downloader.get(league_url(league_code, start_year)))
        report.parsed = len(records)
        index = self._match_index(league_code, start_year)

        for record in records:
            self._apply(record, index, report)

        self.session.commit()
        logger.info(
            "%s %d xG: %d/%d matched, %d unmatched, unknown teams: %s",
            league_code, start_year, report.matched, report.parsed,
            report.unmatched_matches, sorted(report.unmatched_teams) or "-",
        )
        return report

    def _apply(
        self,
        record: UnderstatMatch,
        index: dict[tuple[int, int, date], Match],
        report: XgUpdateReport,
    ) -> None:
        home = resolve_understat_team(self.session, record.home_team)
        away = resolve_understat_team(self.session, record.away_team)
        if home is None or away is None:
            if home is None:
                report.unmatched_teams.add(record.home_team)
            if away is None:
                report.unmatched_teams.add(record.away_team)
            report.unmatched_matches += 1
            return

        match = next(
            (
                index[key]
                for offset in (0, -1, 1)
                if (key := (home.id, away.id, record.match_date + timedelta(days=offset)))
                in index
            ),
            None,
        )
        if match is None:
            report.unmatched_matches += 1
            return
        match.xg_home = record.xg_home
        match.xg_away = record.xg_away
        report.matched += 1
