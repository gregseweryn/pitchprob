"""The speaking loop (P0, ADR 0016): find the alertable, and only that.

The research engine could always answer a question; this is the piece that
*asks the questions itself*, on a schedule, and speaks when — and only when —
the answer is worth the operator's phone. It is deliberately built out of
parts already proven elsewhere, adding no new judgment of its own:

- **The sharp price decides, never the model** (ADR 0004/0011). Each
  candidate is verdicted by ``scanner.scan``, whose anchor is the tape's
  Shin-de-margined Pinnacle fair.
- **Feed-driven, so silent by default.** Polish-book prices come from the
  odds-api.io feed (``scanner.feed_quotes``); with no feed, there is nothing
  to verdict and the loop says nothing. Feed prices are unvalidated, so they
  surface as ``UNVERIFIED`` leads — never an auto-``PLAY`` (ADR 0014).
- **Never alert a bet it could not place** (ADR 0015). A candidate is
  dropped unless a flat stake fits the exposure limits and the drawdown
  breaker is open.
- **Say a standing edge once** (``sent_alerts``). Re-pinging the same lead
  every pass trains the operator to ignore alerts, which defeats alerting.

An alert is rare on purpose. Most passes of this loop produce nothing, and
that is the system working — the 12% turnover tax sits in the prices, so
value is the exception, not the rule.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.betting.risk import (
    DEFAULT_LIMITS,
    RiskLimits,
    breaker_tripped,
    check_exposure,
)
from pitchprob.data.adapters.odds_api_io import SOURCE as FEED_SOURCE
from pitchprob.data.normalize import canonical_team_name, odds_api_canonical
from pitchprob.data.orm import OddsTick, SentAlert
from pitchprob.services.alerts import Alert, Notifier, format_play_alert
from pitchprob.services.ledger import exposure_state, realized_drawdown
from pitchprob.services.scanner import (
    DEFAULT_MAX_ANCHOR_AGE,
    DEFAULT_MIN_EDGE,
    feed_quotes,
    scan,
)
from pitchprob.services.tape import MARKET_SELECTIONS, SHARP_BOOKMAKER, as_utc

#: How far ahead the loop looks for fixtures to verdict. Wide enough to catch
#: the whole upcoming round; the tape only anchors fixtures Pinnacle prices,
#: so this bounds work, not opportunity.
DEFAULT_WINDOW = timedelta(hours=72)

#: The only two verdicts worth a push. NO BET / STALE / NO ANCHOR are silence.
_ALERTABLE = frozenset({"PLAY", "UNVERIFIED"})

_GROSZ = Decimal("0.01")

_AlertKey = tuple[str, str, str, Decimal | None, str]


@dataclass(frozen=True, slots=True)
class _AnchorEvent:
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime


@dataclass(frozen=True, slots=True)
class WatchResult:
    """What one pass of the loop did: what it sent, and what it held back."""

    alerts: list[Alert]
    suppressed_duplicates: int
    events_scanned: int


def _upcoming_anchor_events(
    session: Session, *, now: datetime, window: timedelta
) -> list[_AnchorEvent]:
    """Fixtures the tape anchors (Pinnacle) that kick off inside the window."""
    rows = session.execute(
        select(
            OddsTick.event_id,
            OddsTick.home_team,
            OddsTick.away_team,
            OddsTick.commence_time,
        )
        .where(OddsTick.bookmaker == SHARP_BOOKMAKER)
        .distinct()
    ).all()
    horizon = now + window
    events: dict[str, _AnchorEvent] = {}
    for event_id, home, away, commence in rows:
        kickoff = as_utc(commence)
        if not (now < kickoff <= horizon):
            continue
        events[event_id] = _AnchorEvent(event_id, home, away, kickoff)
    return sorted(events.values(), key=lambda e: (e.commence_time, e.event_id))


def _feed_selections(
    session: Session,
    *,
    home_team: str,
    away_team: str,
    kickoff: datetime,
    at: datetime,
) -> list[tuple[str, str, Decimal | None]]:
    """(market, selection, line) tuples the feed carries for this fixture.

    Fixtures are joined on canonical team names and a kickoff within a day —
    the same discipline ``feed_quotes`` uses, so a fixture the maps cannot
    bridge yields nothing rather than a mismatched anchor.
    """
    home = canonical_team_name(odds_api_canonical(home_team))
    away = canonical_team_name(odds_api_canonical(away_team))
    rows = session.execute(
        select(
            OddsTick.market,
            OddsTick.selection,
            OddsTick.line,
            OddsTick.home_team,
            OddsTick.away_team,
            OddsTick.observed_at,
        )
        .where(
            OddsTick.source == FEED_SOURCE,
            OddsTick.commence_time >= kickoff - timedelta(days=1),
            OddsTick.commence_time <= kickoff + timedelta(days=1),
        )
        .distinct()
    ).all()
    combos: set[tuple[str, str, Decimal | None]] = set()
    for market, selection, line, home_raw, away_raw, observed_at in rows:
        if as_utc(observed_at) > at:
            continue
        if market not in MARKET_SELECTIONS:
            continue
        if canonical_team_name(odds_api_canonical(home_raw)) != home:
            continue
        if canonical_team_name(odds_api_canonical(away_raw)) != away:
            continue
        combos.add((market, selection, line))
    return sorted(combos, key=lambda c: (c[0], c[1], str(c[2])))


def _suggested_stake(
    session: Session,
    *,
    event: _AnchorEvent,
    now: datetime,
    limits: RiskLimits,
) -> Decimal | None:
    """The largest flat stake that fits the exposure limits, or None.

    None means "this bet could not be placed right now" — one bet per
    fixture already taken, the day's cap spent, or too many open picks — and
    the loop treats that as a reason to stay silent, not to alert anyway.
    """
    state = exposure_state(
        session,
        event_id=event.event_id,
        home_team=event.home_team,
        away_team=event.away_team,
        kickoff_utc=event.commence_time,
        placed_at=now,
    )
    if state.open_picks + 1 > limits.max_open_picks:
        return None
    headroom = min(
        limits.max_stake_pln,
        limits.max_match_stake_pln - state.match_stake_pln,
        limits.max_daily_stake_pln - state.daily_stake_pln,
    )
    if headroom < limits.min_stake_pln:
        return None
    stake = headroom.quantize(_GROSZ, rounding=ROUND_DOWN)
    if check_exposure(state, stake, limits=limits):
        return None
    return stake


def _sent_keys(session: Session) -> set[_AlertKey]:
    rows = session.execute(
        select(
            SentAlert.event_id,
            SentAlert.market,
            SentAlert.selection,
            SentAlert.line,
            SentAlert.bookmaker,
        )
    ).all()
    return {(e, m, s, ln, b) for e, m, s, ln, b in rows}


def _detect(
    session: Session,
    *,
    now: datetime,
    min_edge: float,
    window: timedelta,
    markets: frozenset[str] | None,
    books: frozenset[str] | None,
    limits: RiskLimits,
    max_anchor_age: timedelta,
    disabled_promos: frozenset[str],
) -> tuple[list[Alert], int, int]:
    """(new alerts, duplicates suppressed, events scanned)."""
    if breaker_tripped(
        realized_drawdown(session, bankroll=limits.bankroll_pln), limits=limits
    ):
        return [], 0, 0

    allowed_markets = markets if markets is not None else frozenset(MARKET_SELECTIONS)
    already = _sent_keys(session)
    seen_this_pass: set[_AlertKey] = set()
    alerts: list[Alert] = []
    suppressed = 0
    events = _upcoming_anchor_events(session, now=now, window=window)

    for event in events:
        stake: Decimal | None = None
        stake_computed = False
        for market, selection, line in _feed_selections(
            session,
            home_team=event.home_team,
            away_team=event.away_team,
            kickoff=event.commence_time,
            at=now,
        ):
            if market not in allowed_markets:
                continue
            quotes = feed_quotes(
                session,
                home_team=event.home_team,
                away_team=event.away_team,
                kickoff=event.commence_time,
                market=market,
                selection=selection,
                line=line,
                at=now,
            )
            if books is not None:
                quotes = [q for q in quotes if q.bookmaker in books]
            if not quotes:
                continue
            try:
                result = scan(
                    session,
                    event_id=event.event_id,
                    market=market,
                    selection=selection,
                    line=line,
                    quotes=quotes,
                    now=now,
                    min_edge=min_edge,
                    max_anchor_age=max_anchor_age,
                    disabled_promos=disabled_promos,
                )
            except ValueError:
                continue
            if result.anchor is None or result.anchor_age is None:
                continue
            # Risk is per fixture, so compute the stake once and reuse it.
            if not stake_computed:
                stake = _suggested_stake(
                    session, event=event, now=now, limits=limits
                )
                stake_computed = True
            if stake is None:
                continue
            anchor_price = result.anchor.prices[selection]
            for verdict in result.verdicts:
                if verdict.verdict not in _ALERTABLE or verdict.edge is None:
                    continue
                if verdict.edge < min_edge:
                    continue
                key: _AlertKey = (
                    event.event_id,
                    market,
                    selection,
                    line,
                    verdict.bookmaker,
                )
                if key in already:
                    suppressed += 1
                    continue
                if key in seen_this_pass:
                    continue
                seen_this_pass.add(key)
                promo_value = (
                    verdict.evaluation.promo_value
                    if verdict.evaluation is not None and verdict.promo is not None
                    else None
                )
                price_effective = (
                    verdict.evaluation.price_effective_promo
                    if verdict.evaluation is not None
                    else verdict.price_quoted
                )
                # The regime the router applied, for the message: exact
                # multiplier when set, 1.0 from the tax-free sugar, None for
                # the bare x0.88 default.
                tax_multiplier: Decimal | None = None
                if verdict.promo is not None:
                    if verdict.promo.tax_multiplier is not None:
                        tax_multiplier = verdict.promo.tax_multiplier
                    elif verdict.promo.tax_free:
                        tax_multiplier = Decimal("1")
                alerts.append(
                    Alert(
                        event_id=event.event_id,
                        home_team=event.home_team,
                        away_team=event.away_team,
                        commence_time=event.commence_time,
                        market=market,
                        selection=selection,
                        line=line,
                        bookmaker=verdict.bookmaker,
                        verdict=verdict.verdict,
                        edge=verdict.edge,
                        price_quoted=verdict.price_quoted,
                        price_effective=price_effective,
                        promo_value=promo_value,
                        anchor_price=anchor_price,
                        anchor_age=result.anchor_age,
                        suggested_stake_pln=stake,
                        tax_multiplier=tax_multiplier,
                    )
                )
    return alerts, suppressed, len(events)


def find_alerts(
    session: Session,
    *,
    now: datetime | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    window: timedelta = DEFAULT_WINDOW,
    markets: frozenset[str] | None = None,
    books: frozenset[str] | None = None,
    limits: RiskLimits = DEFAULT_LIMITS,
    max_anchor_age: timedelta = DEFAULT_MAX_ANCHOR_AGE,
    disabled_promos: frozenset[str] = frozenset(),
) -> list[Alert]:
    """Every fresh, risk-fitting, above-threshold lead not already sent."""
    when = now if now is not None else datetime.now(tz=UTC)
    alerts, _, _ = _detect(
        session,
        now=when,
        min_edge=min_edge,
        window=window,
        markets=markets,
        books=books,
        limits=limits,
        max_anchor_age=max_anchor_age,
        disabled_promos=disabled_promos,
    )
    return alerts


def run_watch(
    session: Session,
    notifier: Notifier,
    *,
    now: datetime | None = None,
    min_edge: float = DEFAULT_MIN_EDGE,
    window: timedelta = DEFAULT_WINDOW,
    markets: frozenset[str] | None = None,
    books: frozenset[str] | None = None,
    limits: RiskLimits = DEFAULT_LIMITS,
    max_anchor_age: timedelta = DEFAULT_MAX_ANCHOR_AGE,
    disabled_promos: frozenset[str] = frozenset(),
    record: bool = True,
) -> WatchResult:
    """One pass: detect, deliver, remember.

    A delivery that fails is *not* recorded, so the next pass retries it —
    a dropped Telegram message must not silently become a lead the operator
    never heard. Recording happens only after a successful send.
    """
    when = now if now is not None else datetime.now(tz=UTC)
    alerts, suppressed, scanned = _detect(
        session,
        now=when,
        min_edge=min_edge,
        window=window,
        markets=markets,
        books=books,
        limits=limits,
        max_anchor_age=max_anchor_age,
        disabled_promos=disabled_promos,
    )
    sent: list[Alert] = []
    for alert in alerts:
        try:
            notifier.send(format_play_alert(alert))
        except RuntimeError:
            continue
        if record:
            session.add(
                SentAlert(
                    event_id=alert.event_id,
                    market=alert.market,
                    selection=alert.selection,
                    line=alert.line,
                    bookmaker=alert.bookmaker,
                    verdict=alert.verdict,
                    edge=alert.edge,
                    sent_at=when,
                )
            )
        sent.append(alert)
    if record:
        session.flush()
    return WatchResult(
        alerts=sent, suppressed_duplicates=suppressed, events_scanned=scanned
    )
