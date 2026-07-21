"""The pre-round freshness gate: is the data current enough to act on?

The season runbook's contract is one command, `pitchprob status`, run
before any scan or bet on a match day. Four checks, each an honest version
of a question the operator would otherwise answer by feel:

- **results** — has a round of final scores landed recently? Auto-settle
  reads the ``matches`` table; betting on Saturday with results last
  ingested two weeks ago means last week's picks are still unsettled and
  the drawdown breaker is flying blind.
- **xg** — do recent matches carry xG? Stale xG quietly degrades the
  (informational) model fair without any error surfacing.
- **tape** — is the sharp anchor usable? The same 30-hour threshold as the
  scanner (one constant, imported, not copied): if a scan would say STALE,
  the gate must not say fresh. A tape with no *upcoming* fixtures fails
  too — a fresh snapshot of finished matches anchors nothing.
- **ledger** — are there kicked-off picks that still need settling or CLV?

Every failing check names the command that fixes it. A gate that says
"stale" without saying "run X" just moves the debugging to ten minutes
before kickoff — the moment the operator has least of.

Deliberately DB-only: no network, so the gate itself can never hang or
lie because an API was slow. API credit levels are printed by every
recorder run and are not re-checked here.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pitchprob.data.orm import Match, OddsTick, Pick
from pitchprob.services.scanner import DEFAULT_MAX_ANCHOR_AGE
from pitchprob.services.tape import as_utc

#: A weekly round plus one day of slack. football-data.co.uk refreshes its
#: season files on a Friday/Tuesday cadence (audit A2), so eight days is
#: the longest gap a healthy weekly refresh can produce.
MAX_RESULTS_AGE_DAYS = 8

#: The scanner's own staleness bar (ADR 0013). Imported, not copied: one
#: number, two gates, no drift.
MAX_TAPE_AGE = DEFAULT_MAX_ANCHOR_AGE

#: Recent window for the xG coverage check. Older gaps are corpus history
#: (99.98% coverage, not 100%) and not a weekly action item.
XG_WINDOW_DAYS = 30

#: Grace period before an unsettled kicked-off pick counts as a problem.
#: Results land on the source's Fri/Tue cadence; nagging two hours after
#: the final whistle would make the gate cry wolf, and a gate that cries
#: wolf gets ignored by September.
SETTLE_GRACE = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str
    #: The command that fixes it; None when ok.
    action: str | None = None


@dataclass(frozen=True, slots=True)
class SeasonStatus:
    generated_at: datetime
    checks: list[Check]

    @property
    def all_ok(self) -> bool:
        return all(check.ok for check in self.checks)


def _results_check(session: Session, now: datetime) -> Check:
    newest = session.execute(select(func.max(Match.match_date))).scalar_one()
    if newest is None:
        return Check(
            name="results",
            ok=False,
            detail="no matches ingested at all",
            action="uv run pitchprob ingest --all",
        )
    age_days = (now.date() - newest).days
    if age_days <= MAX_RESULTS_AGE_DAYS:
        return Check(
            name="results",
            ok=True,
            detail=f"newest match {newest.isoformat()} ({age_days}d ago)",
        )
    return Check(
        name="results",
        ok=False,
        detail=(
            f"newest match {newest.isoformat()} is {age_days}d old "
            f"(threshold {MAX_RESULTS_AGE_DAYS}d)"
        ),
        action=(
            "uv run pitchprob ingest --all --from-year 2026 --to-year 2026 "
            "--refresh"
        ),
    )


def _xg_check(session: Session, now: datetime) -> Check:
    window_start = now.date() - timedelta(days=XG_WINDOW_DAYS)
    missing = session.execute(
        select(func.count())
        .select_from(Match)
        .where(Match.match_date >= window_start, Match.xg_home.is_(None))
    ).scalar_one()
    if missing == 0:
        return Check(
            name="xg",
            ok=True,
            detail=f"every match of the last {XG_WINDOW_DAYS}d carries xG",
        )
    return Check(
        name="xg",
        ok=False,
        detail=f"{missing} match(es) in the last {XG_WINDOW_DAYS}d without xG",
        action="uv run pitchprob xg --all --from-year 2026 --to-year 2026 --refresh",
    )


def _tape_check(session: Session, now: datetime) -> Check:
    newest = session.execute(select(func.max(OddsTick.observed_at))).scalar_one()
    if newest is None:
        return Check(
            name="tape",
            ok=False,
            detail="the odds tape is empty",
            action="git pull, then: uv run pitchprob import-tape",
        )
    age = now - as_utc(newest)
    upcoming = session.execute(
        select(func.count(func.distinct(OddsTick.event_id))).where(
            OddsTick.commence_time > now
        )
    ).scalar_one()
    hours = age.total_seconds() / 3600
    if age > MAX_TAPE_AGE:
        return Check(
            name="tape",
            ok=False,
            detail=(
                f"latest snapshot is {hours:.0f}h old — every scan would "
                "verdict STALE"
            ),
            action=(
                "git pull, then: uv run pitchprob import-tape (and check the "
                "record-odds workflow if the gap persists)"
            ),
        )
    if upcoming == 0:
        return Check(
            name="tape",
            ok=False,
            detail="fresh snapshot but no upcoming fixtures on the tape",
            action="git pull, then: uv run pitchprob import-tape",
        )
    return Check(
        name="tape",
        ok=True,
        detail=f"snapshot {hours:.0f}h old, {upcoming} upcoming fixture(s)",
    )


def _ledger_check(session: Session, now: datetime) -> Check:
    cutoff = now - SETTLE_GRACE
    picks = list(
        session.execute(select(Pick).where(Pick.kickoff_utc < cutoff)).scalars()
    )
    unsettled = sum(1 for pick in picks if pick.settled_at is None)
    missing_clv = sum(
        1
        for pick in picks
        if pick.clv_exec is None and pick.event_id is not None
    )
    if unsettled == 0 and missing_clv == 0:
        return Check(
            name="ledger",
            ok=True,
            detail="no kicked-off picks awaiting settlement or CLV",
        )
    parts: list[str] = []
    if unsettled:
        parts.append(f"{unsettled} pick(s) unsettled >24h after kickoff")
    if missing_clv:
        parts.append(f"{missing_clv} pick(s) without CLV")
    return Check(
        name="ledger",
        ok=False,
        detail="; ".join(parts),
        action=(
            "uv run pitchprob import-tape, then: uv run pitchprob pick settle"
        ),
    )


def _guarded(
    name: str,
    check: Callable[[Session, datetime], Check],
    session: Session,
    now: datetime,
) -> Check:
    """A database behind the migration chain is a freshness problem, not a
    crash. Found live on the corpus DB: the gate — the one tool that must
    never fail unhelpfully ten minutes before kickoff — raised a traceback
    because ``picks`` did not exist yet. Its fix has a name like every
    other stale state, so it gets reported like every other stale state.
    """
    try:
        return check(session, now)
    except OperationalError:
        session.rollback()
        return Check(
            name=name,
            ok=False,
            detail="schema is behind the migration chain (missing table/column)",
            action="uv run alembic upgrade head",
        )


def season_status(session: Session, *, now: datetime) -> SeasonStatus:
    """Run every gate check against the local database."""
    named: list[tuple[str, Callable[[Session, datetime], Check]]] = [
        ("results", _results_check),
        ("xg", _xg_check),
        ("tape", _tape_check),
        ("ledger", _ledger_check),
    ]
    return SeasonStatus(
        generated_at=now,
        checks=[_guarded(name, check, session, now) for name, check in named],
    )
