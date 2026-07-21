"""Append-only archive of raw Understat responses (resilience, audit 2026-07).

Since Opta cut FBref off advanced data in January 2026, Understat is the
only free xG source for the top-5 leagues — a single point of failure for
the whole feature layer. The mutable download cache in ``data/raw`` is not
an archive: ``--refresh`` overwrites it in place. This module keeps a dated,
gzipped, append-only copy of every *distinct* payload the xG service sees,
so a source outage or layout change can never take the raw material with it.

Files are ``data/understat/{league}_{season}_{UTC timestamp}.json.gz`` and
are tracked in git like the odds tape (ADR 0012): past seasons stabilize
into exactly one snapshot, the current season accretes one file per change.
"""

import gzip
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_SUBDIR = "understat"

_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _snapshots(directory: Path, league_code: str, start_year: int) -> list[Path]:
    return sorted(directory.glob(f"{league_code}_{start_year}_*.json.gz"))


def latest_snapshot(
    directory: Path, league_code: str, start_year: int
) -> Path | None:
    """Newest stored snapshot for one league-season, by filename timestamp."""
    existing = _snapshots(directory, league_code, start_year)
    return existing[-1] if existing else None


def read_snapshot(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


def snapshot_payload(
    directory: Path,
    league_code: str,
    start_year: int,
    content: bytes,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Archive one raw response; returns the written path, or ``None`` when
    the newest stored snapshot already holds byte-identical content (weekly
    re-runs over finished seasons must not accrete duplicates)."""
    newest = latest_snapshot(directory, league_code, start_year)
    if newest is not None and read_snapshot(newest) == content:
        return None
    stamp = (now if now is not None else datetime.now(tz=UTC)).strftime(_STAMP_FORMAT)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{league_code}_{start_year}_{stamp}.json.gz"
    path.write_bytes(gzip.compress(content))
    return path
