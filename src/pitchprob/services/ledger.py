"""The forward pick ledger (Phase 5 part 3): real bets, real CLV.

One row per real-money bet the operator places at a Polish book. The ledger
is the program's decision variable — CLV against the tape's Pinnacle close,
never backtest ROI (syndicate plan, ADR 0010/0011) — so every pick carries
the two-label decomposition per bet:

- ``clv_sharp``  = price_sharp x Shin(close) - 1 — pure *timing*: did the
  sharp line move for or against the bet after placement? ``price_sharp``
  is the Pinnacle quote at bet time, auto-filled from the tape.
- ``clv_exec``   = price_effective x Shin(close) - 1 — the PLN-real number:
  executed price under the recorded tax regime (``tax_multiplier``: x1.0
  tax-free, x0.94 past the Betclic limit, x0.88 bare tax — ADR 0017).
  ``clv_exec - clv_sharp`` is the venue/shopping component — under
  the Phase 0-2a verdicts (no timing edge) it is the only place value can
  live, which is exactly what the decomposition makes visible.

Settlement mirrors the backtest contract (gross return per unit stake,
``betting.settlement``) on the *effective* price; pushes return the full
stake (Polish books refund the taxed stake on voids). Auto-settlement joins
the tape's raw team naming to canonical ``matches`` rows at analysis time —
unmatched picks are reported, never guessed (quarantine-not-drop).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pitchprob.betting.effective import (
    BETCLIC_TAX_FREE_LIMIT,
    TAX_MULTIPLIER,
    TaxFreeAllowance,
    effective_price,
)
from pitchprob.betting.promos import active_promo, auto_terms, canonical_bookmaker
from pitchprob.betting.risk import (
    DEFAULT_BANKROLL_PLN,
    DEFAULT_LIMITS,
    DrawdownState,
    ExposureState,
    RiskLimits,
    RiskRefusal,
    breaker_tripped,
    check_exposure,
    describe_violations,
    drawdown_state,
)
from pitchprob.betting.settlement import (
    AhSide,
    MatchSelection,
    TotalsSelection,
    settle_1x2,
    settle_asian_handicap,
    settle_totals,
)
from pitchprob.data.normalize import canonical_team_name, odds_api_canonical
from pitchprob.data.orm import Match, Pick, Team
from pitchprob.services.tape import (
    as_utc,
    closing_fair,
    fair_at,
    validate_market_selection,
)

_CENTS = Decimal("0.01")

#: Payload contract (ADR 0004): the ledger's numbers cannot be rendered
#: anywhere without the sentences that make them readable.
LEDGER_CAVEATS: tuple[str, ...] = (
    "Zmienną decyzyjną jest CLV, nie ROI. Przy kilkudziesięciu małych "
    "zakładach ROI to niemal wyłącznie wariancja; sygnał niesie wartość "
    "wobec kursu zamknięcia.",
    "Czytaj CLV ostre przed wykonanym. Ostre to samo wyczucie czasu — cena "
    "Pinnacle'a w chwili zakładu wobec ceny zamknięcia. Ich różnica to wkład "
    "bukmachera i promocji, a według werdyktów faz 0-2a to jedyne miejsce, "
    "gdzie wykazano jakąkolwiek wartość.",
    "\"Zamknięcie\" taśmy to ostatni dzienny snapshot przed gwizdkiem i bywa "
    "o kilka godzin za wczesny; każdy zakład niesie ten znacznik czasu.",
    "Stawki to 2-5 zł przy umownym banku 500 zł. To sezon pomiarowy: wynikiem "
    "jest dowód na temat CLV, nie przychód.",
    "Reżim podatkowy zapisuje się automatycznie ze stanu limitu \"Bez "
    "Podatku\" (x1,0 w ramach limitu, x0,94 po nim dla singli, x0,88 bez "
    "promocji) i liczy się wyłącznie z zakładów w tym rejestrze — kupon "
    "postawiony poza nim rozjeżdża stan limitu. Rozstrzygający jest reżim "
    "widoczny na kuponie u bukmachera; ofertę można odwołać z 24-godzinnym "
    "wyprzedzeniem (§8 regulaminu).",
)


@dataclass(frozen=True, slots=True)
class SettlementSummary:
    settled: int
    pending: int
    unmatched: list[str]


@dataclass(frozen=True, slots=True)
class ClvSummary:
    attached: int
    skipped_no_event: int
    skipped_no_closing: int


def _validate_bet(
    market: str, selection: str, line: Decimal | None, stake: Decimal, quoted: Decimal
) -> None:
    validate_market_selection(market, selection, line)
    if stake <= 0:
        raise ValueError(f"stake must be positive, got {stake}")
    if quoted <= 1:
        raise ValueError(f"decimal price must exceed 1.0, got {quoted}")


def tax_free_allowance(
    session: Session,
    bookmaker: str,
    *,
    limit: Decimal = BETCLIC_TAX_FREE_LIMIT,
) -> TaxFreeAllowance:
    """Tax-free turnover already consumed at ``bookmaker`` by logged picks.

    Matching is canonical: the operator types "betclic", the odds-api.io
    feed says "Betclic PL", and both must count against the same allowance —
    a literal comparison would silently split the limit in two. Grouping is
    Python-side; the picks table is a season of hand-sized bets.
    """
    key = canonical_bookmaker(bookmaker)
    rows = session.execute(
        select(Pick.bookmaker, func.sum(Pick.stake_pln))
        .where(Pick.tax_free.is_(True))
        .group_by(Pick.bookmaker)
    ).all()
    used = sum(
        (Decimal(total) for book, total in rows if canonical_bookmaker(book) == key),
        Decimal("0"),
    )
    return TaxFreeAllowance(limit=limit, used=used)


def exposure_state(
    session: Session,
    *,
    event_id: str | None,
    home_team: str,
    away_team: str,
    kickoff_utc: datetime,
    placed_at: datetime,
) -> ExposureState:
    """What the ledger already carries, as of a candidate bet (ADR 0015).

    The fixture is identified by tape event id when there is one and by
    teams-plus-kickoff-date otherwise: manually logged picks carry no event
    id, and without the fallback the one-bet-per-fixture rule would have a
    hole exactly where the operator types things by hand.

    The daily window is the UTC calendar day of *placement* — the day the
    money left, not the day the matches are played.
    """
    match_filter = (
        Pick.event_id == event_id
        if event_id is not None
        else (
            (Pick.home_team == home_team)
            & (Pick.away_team == away_team)
            & (Pick.kickoff_utc >= kickoff_utc - timedelta(days=1))
            & (Pick.kickoff_utc <= kickoff_utc + timedelta(days=1))
        )
    )
    match_stake = session.execute(
        select(func.coalesce(func.sum(Pick.stake_pln), 0)).where(match_filter)
    ).scalar_one()

    day_start = as_utc(placed_at).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_stake = session.execute(
        select(func.coalesce(func.sum(Pick.stake_pln), 0)).where(
            Pick.placed_at >= day_start,
            Pick.placed_at < day_start + timedelta(days=1),
        )
    ).scalar_one()

    open_picks = session.execute(
        select(func.count()).select_from(Pick).where(Pick.settled_at.is_(None))
    ).scalar_one()

    return ExposureState(
        match_stake_pln=Decimal(match_stake),
        daily_stake_pln=Decimal(daily_stake),
        open_picks=int(open_picks),
    )


def realized_drawdown(
    session: Session, *, bankroll: Decimal = DEFAULT_BANKROLL_PLN
) -> DrawdownState:
    """Peak-to-current drawdown of realized P&L across settled picks.

    Open picks are excluded on purpose: an unsettled bet has no realized
    result, and treating it as a loss would trip the breaker on positions
    that may still win.
    """
    rows = session.execute(
        select(Pick.settled_at, Pick.gross_return_pln, Pick.stake_pln).where(
            Pick.settled_at.is_not(None), Pick.gross_return_pln.is_not(None)
        )
    ).all()
    return drawdown_state(
        [
            (as_utc(settled_at), Decimal(gross) - Decimal(stake))
            for settled_at, gross, stake in rows
        ],
        bankroll=bankroll,
    )


def _risk_check(
    session: Session,
    *,
    event_id: str | None,
    home_team: str,
    away_team: str,
    kickoff_utc: datetime,
    placed_at: datetime,
    stake_pln: Decimal,
    limits: RiskLimits,
) -> str | None:
    """The reason this bet should not be placed, or None. Never raises.

    Returning the reason rather than raising is what lets ``log_pick``
    record it on an overridden pick: the same sentence the operator was
    shown when they chose to bypass it.
    """
    reasons: list[str] = []
    state = exposure_state(
        session,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_utc=kickoff_utc,
        placed_at=placed_at,
    )
    violations = check_exposure(state, stake_pln, limits=limits)
    if violations:
        reasons.append(describe_violations(violations))
    drawdown = realized_drawdown(session, bankroll=limits.bankroll_pln)
    if breaker_tripped(drawdown, limits=limits):
        reasons.append(
            f"drawdown circuit breaker: realized drawdown "
            f"{drawdown.drawdown_pln:.2f} PLN has reached the stop "
            f"{limits.max_drawdown_pln:.2f} PLN "
            f"(equity {drawdown.equity_pln:.2f} of peak "
            f"{drawdown.peak_equity_pln:.2f})"
        )
    return "; ".join(reasons) if reasons else None


def log_pick(
    session: Session,
    *,
    home_team: str,
    away_team: str,
    kickoff_utc: datetime,
    market: str,
    selection: str,
    bookmaker: str,
    stake_pln: Decimal,
    price_quoted: Decimal,
    line: Decimal | None = None,
    tax_free: bool | None = None,
    placed_at: datetime | None = None,
    event_id: str | None = None,
    notes: str | None = None,
    limits: RiskLimits = DEFAULT_LIMITS,
    override_risk: bool = False,
    disabled_promos: frozenset[str] = frozenset(),
) -> Pick:
    """Record one executed bet; auto-fill the sharp anchor from the tape.

    The tax regime records itself (ADR 0017): with ``tax_free=None`` the
    promo registry plus the allowance state derive the multiplier — x1.0
    while any Bez Podatku allowance remains (§3 ust. 4: a straddling stake
    qualifies in full), x0.94 past the limit for singles (§3 ust. 11 pkt 1),
    x0.88 at a book with no promo or one killed via ``disabled_promos``.
    The regulamin applies the promo to every qualifying bet automatically,
    so auto *is* the faithful record. An explicit ``tax_free`` wins — the
    operator saw the coupon, the router did not — and forcing ``True`` past
    a spent limit is refused rather than silently mispricing the payout.

    The Phase 3 risk layer (ADR 0015) gates the write: exposure limits and
    the drawdown circuit breaker raise ``RiskRefusal`` and no row is
    created. ``override_risk`` bypasses them deliberately — and is stamped
    on the pick together with the reason, so the weekly report can count
    overrides instead of the operator having to remember them.
    """
    _validate_bet(market, selection, line, stake_pln, price_quoted)
    if tax_free is None:
        promo = active_promo(bookmaker, disabled=disabled_promos)
        if promo is None:
            multiplier = TAX_MULTIPLIER
        else:
            terms = auto_terms(
                promo,
                tax_free_allowance(
                    session, promo.book, limit=promo.tax_free_limit
                ),
            )
            assert terms.tax_multiplier is not None  # auto_terms always sets it
            multiplier = terms.tax_multiplier
    elif tax_free:
        allowance = tax_free_allowance(session, bookmaker)
        if not allowance.covers(stake_pln):
            raise ValueError(
                f"the tax-free allowance at {bookmaker} is spent "
                f"(limit {allowance.limit} PLN); past it Betclic singles pay "
                "x0.94 (§3 ust. 11 pkt 1) — log the pick without --tax-free "
                "and the regime derives itself"
            )
        multiplier = Decimal("1")
    else:
        multiplier = TAX_MULTIPLIER
    when = placed_at if placed_at is not None else datetime.now(tz=UTC)
    refusal = _risk_check(
        session,
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_utc=kickoff_utc,
        placed_at=when,
        stake_pln=stake_pln,
        limits=limits,
    )
    if refusal is not None and not override_risk:
        raise RiskRefusal(
            f"{refusal}. Place it anyway only on purpose: override_risk=True "
            "(CLI: --override-risk), which marks the pick permanently."
        )
    price_sharp: Decimal | None = None
    sharp_observed_at: datetime | None = None
    if event_id is not None:
        anchor = fair_at(
            session, event_id=event_id, market=market, line=line, at=when
        )
        if anchor is not None:
            price_sharp = anchor.prices[selection]
            sharp_observed_at = anchor.observed_at
    pick = Pick(
        created_at=datetime.now(tz=UTC),
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        kickoff_utc=kickoff_utc,
        market=market,
        selection=selection,
        line=line,
        bookmaker=bookmaker,
        stake_pln=stake_pln,
        price_quoted=price_quoted,
        tax_free=multiplier == Decimal("1"),
        tax_multiplier=multiplier,
        price_effective=effective_price(price_quoted, multiplier=multiplier),
        placed_at=when,
        price_sharp=price_sharp,
        sharp_observed_at=sharp_observed_at,
        notes=notes,
        # A clean pick is never marked as an override, even when the flag
        # was passed: the mark means "this bet broke a limit", not "the
        # operator had the flag switched on".
        risk_override=refusal is not None,
        risk_note=refusal,
    )
    session.add(pick)
    session.flush()
    return pick


def _unit_return(pick: Pick, ft_home: int, ft_away: int) -> float:
    price = float(pick.price_effective)
    if pick.market == "1x2":
        return settle_1x2(
            cast(MatchSelection, pick.selection), ft_home, ft_away, price
        )
    if pick.line is None:
        # log_pick enforces this; a row that reaches settlement without a
        # line came from elsewhere, and money must not be settled on a guess.
        raise ValueError(
            f"pick {pick.id} on market {pick.market!r} has no line"
        )
    if pick.market == "ou":
        return settle_totals(
            cast(TotalsSelection, pick.selection),
            ft_home + ft_away,
            pick.line,
            price,
        )
    return settle_asian_handicap(
        cast(AhSide, pick.selection), ft_home, ft_away, pick.line, price
    )


def settle_pick(
    pick: Pick, *, ft_home: int, ft_away: int, settled_at: datetime
) -> None:
    """Fill realized settlement from a final score (gross, PLN, to the grosz)."""
    unit = _unit_return(pick, ft_home, ft_away)
    pick.ft_home = ft_home
    pick.ft_away = ft_away
    pick.gross_return_pln = (
        pick.stake_pln * Decimal(str(unit))
    ).quantize(_CENTS, rounding=ROUND_HALF_UP)
    pick.settled_at = settled_at


def _resolve_match(session: Session, pick: Pick) -> Match | None:
    """Tape naming -> canonical teams -> the finished match row, if ingested."""
    home = canonical_team_name(odds_api_canonical(pick.home_team))
    away = canonical_team_name(odds_api_canonical(pick.away_team))
    home_row = session.execute(
        select(Team).where(Team.canonical_name == home)
    ).scalar_one_or_none()
    away_row = session.execute(
        select(Team).where(Team.canonical_name == away)
    ).scalar_one_or_none()
    if home_row is None or away_row is None:
        return None
    kickoff_date = as_utc(pick.kickoff_utc).date()
    for offset in (0, -1, 1):  # UTC kickoff vs local match_date can differ
        match = (
            session.execute(
                select(Match).where(
                    Match.home_team_id == home_row.id,
                    Match.away_team_id == away_row.id,
                    Match.match_date == kickoff_date + timedelta(days=offset),
                )
            )
            .scalars()
            .first()
        )
        if match is not None:
            return match
    return None


def auto_settle(session: Session, *, now: datetime) -> SettlementSummary:
    """Settle every kicked-off, unsettled pick whose result is in ``matches``.

    Picks that cannot be matched (result not ingested yet, or a tape team
    name the canonical maps do not bridge) are listed, not guessed — the
    operator settles them manually or extends the odds-api name map.
    """
    picks = session.execute(select(Pick).where(Pick.settled_at.is_(None))).scalars()
    settled = 0
    pending = 0
    unmatched: list[str] = []
    for pick in picks:
        if as_utc(pick.kickoff_utc) >= now:
            continue
        match = _resolve_match(session, pick)
        if match is None:
            pending += 1
            description = f"{pick.home_team} vs {pick.away_team}"
            if description not in unmatched:
                unmatched.append(description)
            continue
        settle_pick(
            pick, ft_home=match.ft_home, ft_away=match.ft_away, settled_at=now
        )
        settled += 1
    return SettlementSummary(settled=settled, pending=pending, unmatched=unmatched)


def attach_clv(session: Session, *, now: datetime) -> ClvSummary:
    """Fill closing-fair CLV (both labels) for kicked-off picks from the tape.

    ``closing_observed_at`` records how stale the tape's "close" actually
    was — the daily snapshot can sit hours before kickoff, and that honesty
    timestamp travels with every CLV number downstream.
    """
    picks = session.execute(select(Pick).where(Pick.clv_exec.is_(None))).scalars()
    attached = 0
    skipped_no_event = 0
    skipped_no_closing = 0
    for pick in picks:
        if as_utc(pick.kickoff_utc) >= now:
            continue
        if pick.event_id is None:
            skipped_no_event += 1
            continue
        quote = closing_fair(
            session, event_id=pick.event_id, market=pick.market, line=pick.line
        )
        if quote is None:
            skipped_no_closing += 1
            continue
        fair = quote.probabilities[pick.selection]
        pick.closing_fair_prob = fair
        pick.closing_observed_at = quote.observed_at
        pick.clv_exec = float(pick.price_effective) * fair - 1.0
        if pick.price_sharp is not None:
            pick.clv_sharp = float(pick.price_sharp) * fair - 1.0
        attached += 1
    return ClvSummary(
        attached=attached,
        skipped_no_event=skipped_no_event,
        skipped_no_closing=skipped_no_closing,
    )


def ledger_summary(session: Session) -> dict[str, Any]:
    """Ledger roll-up: settlement money and the CLV decomposition means.

    ``profit_pln``/``roi`` cover settled picks only; CLV means cover picks
    with the respective label attached. ``mean_shopping_value`` is the paired
    mean of ``clv_exec - clv_sharp`` — the venue/promo component of CLV.
    """
    picks = list(session.execute(select(Pick)).scalars().all())
    settled = [p for p in picks if p.settled_at is not None]
    staked_settled = sum((p.stake_pln for p in settled), Decimal("0"))
    returned = sum(
        (p.gross_return_pln for p in settled if p.gross_return_pln is not None),
        Decimal("0"),
    )
    execs = [p.clv_exec for p in picks if p.clv_exec is not None]
    sharps = [p.clv_sharp for p in picks if p.clv_sharp is not None]
    paired = [
        p.clv_exec - p.clv_sharp
        for p in picks
        if p.clv_exec is not None and p.clv_sharp is not None
    ]

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "n_picks": len(picks),
        "n_settled": len(settled),
        "total_staked_pln": float(sum((p.stake_pln for p in picks), Decimal("0"))),
        "total_returned_pln": float(returned),
        "profit_pln": float(returned - staked_settled),
        "roi": (
            float((returned - staked_settled) / staked_settled)
            if staked_settled > 0
            else None
        ),
        "n_with_clv": len(execs),
        "mean_clv_exec": mean(execs),
        "mean_clv_sharp": mean(sharps),
        "mean_shopping_value": mean(paired),
    }
