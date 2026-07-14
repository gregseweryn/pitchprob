"""Repositories: all persistence access for the data bounded context.

Every write path is idempotent so that re-running ingestion is always safe:

- ``get_or_create`` for reference entities (leagues, seasons, teams),
- natural-key ``upsert`` for matches,
- delete-and-insert ``replace_for_match`` for odds (a match's odds are a unit).

Implementations use portable SQLAlchemy Core/ORM only (no dialect-specific
``ON CONFLICT``) so the same code runs on Postgres in production and SQLite in
unit tests. Ingestion volume (~35k matches) does not justify dialect forks.
"""

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pitchprob.core.errors import UnknownTeamError
from pitchprob.data.orm import Injury, League, Match, OddsQuote, Season, Team, TeamAlias

# Optional per-match statistics that may arrive (or improve) on re-ingest.
_MATCH_OPTIONAL_FIELDS = (
    "kickoff_utc",
    "ht_home",
    "ht_away",
    "shots_home",
    "shots_away",
    "shots_on_target_home",
    "shots_on_target_away",
    "corners_home",
    "corners_away",
    "yellows_home",
    "yellows_away",
    "reds_home",
    "reds_away",
    "fouls_home",
    "fouls_away",
)


class LeagueRepository:
    @staticmethod
    def get_or_create(session: Session, *, code: str, name: str, country: str) -> League:
        league = session.execute(select(League).where(League.code == code)).scalar_one_or_none()
        if league is None:
            league = League(code=code, name=name, country=country)
            session.add(league)
            session.flush()
        return league


class SeasonRepository:
    @staticmethod
    def get_or_create(session: Session, *, league_id: int, start_year: int) -> Season:
        season = session.execute(
            select(Season).where(Season.league_id == league_id, Season.start_year == start_year)
        ).scalar_one_or_none()
        if season is None:
            label = f"{start_year}/{str(start_year + 1)[-2:]}"
            season = Season(league_id=league_id, start_year=start_year, label=label)
            session.add(season)
            session.flush()
        return season


class TeamRepository:
    @staticmethod
    def create_with_alias(
        session: Session, *, canonical_name: str, country: str, source: str, alias: str
    ) -> Team:
        team = Team(canonical_name=canonical_name, country=country)
        session.add(team)
        session.flush()
        session.add(TeamAlias(team_id=team.id, source=source, alias=alias))
        session.flush()
        return team

    @staticmethod
    def resolve(session: Session, *, source: str, alias: str) -> Team:
        team = session.execute(
            select(Team)
            .join(TeamAlias, TeamAlias.team_id == Team.id)
            .where(TeamAlias.source == source, TeamAlias.alias == alias)
        ).scalar_one_or_none()
        if team is None:
            raise UnknownTeamError(f"No team for alias {alias!r} from source {source!r}")
        return team

    @staticmethod
    def resolve_or_create(
        session: Session,
        *,
        source: str,
        alias: str,
        country: str,
        canonical_name: str | None = None,
    ) -> Team:
        """Resolve an alias; on miss, attach it to a team of the same canonical
        name if one exists (cross-source join), otherwise create the team."""
        try:
            return TeamRepository.resolve(session, source=source, alias=alias)
        except UnknownTeamError:
            pass
        name = canonical_name or alias
        team = session.execute(
            select(Team).where(Team.canonical_name == name)
        ).scalar_one_or_none()
        if team is None:
            team = Team(canonical_name=name, country=country)
            session.add(team)
            session.flush()
        session.add(TeamAlias(team_id=team.id, source=source, alias=alias))
        session.flush()
        return team


class MatchRepository:
    @staticmethod
    def upsert(
        session: Session,
        *,
        season_id: int,
        match_date: date,
        home_team_id: int,
        away_team_id: int,
        ft_home: int,
        ft_away: int,
        kickoff_utc: datetime | None = None,
        **stats: int | None,
    ) -> tuple[Match, bool]:
        """Insert or update a match by natural key. Optional statistics only
        overwrite existing values when provided (non-None)."""
        unknown = set(stats) - set(_MATCH_OPTIONAL_FIELDS)
        if unknown:
            raise TypeError(f"Unknown match fields: {sorted(unknown)}")

        match = session.execute(
            select(Match).where(
                Match.season_id == season_id,
                Match.match_date == match_date,
                Match.home_team_id == home_team_id,
                Match.away_team_id == away_team_id,
            )
        ).scalar_one_or_none()
        created = match is None
        if match is None:
            match = Match(
                season_id=season_id,
                match_date=match_date,
                home_team_id=home_team_id,
                away_team_id=away_team_id,
                ft_home=ft_home,
                ft_away=ft_away,
            )
            session.add(match)
        else:
            match.ft_home = ft_home
            match.ft_away = ft_away
        if kickoff_utc is not None:
            match.kickoff_utc = kickoff_utc
        for field, value in stats.items():
            if value is not None:
                setattr(match, field, value)
        session.flush()
        return match, created


class InjuryRepository:
    @staticmethod
    def upsert(
        session: Session,
        *,
        team_id: int,
        match_date: date,
        player_name: str,
        reason: str | None,
        season: int,
    ) -> bool:
        """Insert by natural key (team, date, player); returns True when a
        new row was created."""
        existing = session.execute(
            select(Injury).where(
                Injury.team_id == team_id,
                Injury.match_date == match_date,
                Injury.player_name == player_name,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.reason = reason
            existing.season = season
            session.flush()
            return False
        session.add(
            Injury(
                team_id=team_id,
                match_date=match_date,
                player_name=player_name,
                reason=reason,
                season=season,
            )
        )
        session.flush()
        return True

    @staticmethod
    def count_absences(session: Session, *, team_id: int, match_date: date) -> int:
        """Listed absences for a team around a date (±1 day: the source and
        football-data disagree on timezones exactly like Understat does)."""
        low, high = match_date - timedelta(days=1), match_date + timedelta(days=1)
        return int(
            session.execute(
                select(func.count())
                .select_from(Injury)
                .where(
                    Injury.team_id == team_id,
                    Injury.match_date >= low,
                    Injury.match_date <= high,
                )
            ).scalar_one()
        )


class OddsRepository:
    @staticmethod
    def replace_for_match(
        session: Session, *, match_id: int, quotes: Iterable[Mapping[str, Any]]
    ) -> int:
        """Replace all odds for a match. A match's odds set is treated as one
        unit, which makes re-ingestion trivially idempotent."""
        session.execute(delete(OddsQuote).where(OddsQuote.match_id == match_id))
        count = 0
        for q in quotes:
            session.add(
                OddsQuote(
                    match_id=match_id,
                    bookmaker=q["bookmaker"],
                    market=q["market"],
                    line=q.get("line", Decimal(0)),
                    selection=q["selection"],
                    price=q["price"],
                    is_closing=q.get("is_closing", False),
                )
            )
            count += 1
        session.flush()
        return count
