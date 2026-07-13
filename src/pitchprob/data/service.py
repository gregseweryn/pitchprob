"""Ingestion orchestration: download → parse → normalize → persist.

The service is idempotent end-to-end: re-ingesting a season updates existing
matches in place and replaces their odds. The HTTP downloader is injected
(``Downloader`` protocol) so tests run fully offline.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from pitchprob.data.adapters.football_data_co_uk import SOURCE, csv_url, parse_csv
from pitchprob.data.normalize import canonical_team_name
from pitchprob.data.repository import (
    LeagueRepository,
    MatchRepository,
    OddsRepository,
    SeasonRepository,
    TeamRepository,
)

logger = logging.getLogger(__name__)

#: League registry for milestone 1 (football-data.co.uk division codes).
LEAGUES: dict[str, tuple[str, str]] = {
    "E0": ("Premier League", "England"),
    "SP1": ("La Liga", "Spain"),
    "D1": ("Bundesliga", "Germany"),
    "I1": ("Serie A", "Italy"),
    "F1": ("Ligue 1", "France"),
}


class Downloader(Protocol):
    def get(self, url: str) -> bytes: ...


class HttpDownloader:
    """httpx-based downloader with an optional on-disk cache.

    Past-season files never change, so cache hits are safe; pass
    ``refresh=True`` to force re-downloads (e.g. for the current season).
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        refresh: bool = False,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.timeout = timeout
        self.headers = headers

    def get(self, url: str) -> bytes:
        cache_path: Path | None = None
        if self.cache_dir is not None:
            cache_path = self.cache_dir / url.removeprefix("https://").replace("/", "_")
            if cache_path.exists() and not self.refresh:
                return cache_path.read_bytes()
        response = httpx.get(
            url, timeout=self.timeout, follow_redirects=True, headers=self.headers
        )
        response.raise_for_status()
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(response.content)
        return response.content


@dataclass
class SeasonIngestReport:
    league_code: str
    start_year: int
    matches_inserted: int = 0
    matches_updated: int = 0
    odds_quotes: int = 0
    quarantined: int = 0
    quarantine_reasons: list[str] = field(default_factory=list)


class IngestionService:
    def __init__(self, *, session: Session, downloader: Downloader) -> None:
        self.session = session
        self.downloader = downloader

    def ingest_season(self, league_code: str, start_year: int) -> SeasonIngestReport:
        league_name, country = LEAGUES[league_code]
        report = SeasonIngestReport(league_code=league_code, start_year=start_year)

        league = LeagueRepository.get_or_create(
            self.session, code=league_code, name=league_name, country=country
        )
        season = SeasonRepository.get_or_create(
            self.session, league_id=league.id, start_year=start_year
        )

        content = self.downloader.get(csv_url(start_year, league_code))
        result = parse_csv(content, start_year=start_year)
        report.quarantined = len(result.quarantined)
        report.quarantine_reasons = [
            f"row {q.row_number}: {q.reason}" for q in result.quarantined
        ]
        for line in report.quarantine_reasons:
            logger.warning("%s %s quarantined %s", league_code, season.label, line)

        for rec in result.records:
            home = TeamRepository.resolve_or_create(
                self.session, source=SOURCE, alias=rec.home_team, country=country,
                canonical_name=canonical_team_name(rec.home_team),
            )
            away = TeamRepository.resolve_or_create(
                self.session, source=SOURCE, alias=rec.away_team, country=country,
                canonical_name=canonical_team_name(rec.away_team),
            )
            match, created = MatchRepository.upsert(
                self.session,
                season_id=season.id,
                match_date=rec.match_date,
                home_team_id=home.id,
                away_team_id=away.id,
                ft_home=rec.ft_home,
                ft_away=rec.ft_away,
                kickoff_utc=rec.kickoff_utc,
                ht_home=rec.ht_home,
                ht_away=rec.ht_away,
                shots_home=rec.shots_home,
                shots_away=rec.shots_away,
                shots_on_target_home=rec.shots_on_target_home,
                shots_on_target_away=rec.shots_on_target_away,
                corners_home=rec.corners_home,
                corners_away=rec.corners_away,
                yellows_home=rec.yellows_home,
                yellows_away=rec.yellows_away,
                reds_home=rec.reds_home,
                reds_away=rec.reds_away,
                fouls_home=rec.fouls_home,
                fouls_away=rec.fouls_away,
            )
            if created:
                report.matches_inserted += 1
            else:
                report.matches_updated += 1
            report.odds_quotes += OddsRepository.replace_for_match(
                self.session,
                match_id=match.id,
                quotes=[
                    {
                        "bookmaker": q.bookmaker,
                        "market": q.market,
                        "selection": q.selection,
                        "price": q.price,
                        "line": q.line,
                        "is_closing": q.is_closing,
                    }
                    for q in rec.odds
                ],
            )

        self.session.commit()
        logger.info(
            "%s %s: +%d matches, ~%d updated, %d odds quotes, %d quarantined",
            league_code, season.label, report.matches_inserted,
            report.matches_updated, report.odds_quotes, report.quarantined,
        )
        return report

    def ingest_range(
        self, league_codes: list[str], start_years: list[int]
    ) -> list[SeasonIngestReport]:
        return [
            self.ingest_season(code, year)
            for code in league_codes
            for year in start_years
        ]
