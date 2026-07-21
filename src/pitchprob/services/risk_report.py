"""The weekly "what the tape says" report (Phase 3, ADR 0015).

One page an operator reads on a Monday: what last week cost, what the whole
ledger says about CLV, how much tax-free allowance is left, which limits
were overridden, and how close the drawdown breaker is.

Two horizons on purpose. The **money** section covers the last seven days,
because that is the question "how did the week go". The **CLV** section
covers the entire ledger, because CLV is the program's decision variable and
it needs every observation it can get — reporting a season's CLV over one
week is how a bad week gets published as a bad thesis.

The report's hardest job is being honest when there is nothing to say, which
is most of the season. Confidence intervals come from the same weekly block
bootstrap the harness uses, and **a mean over fewer than ``MIN_BLOCKS_FOR_CI``
weeks gets no interval at all**: resampling a single block returns that block
every time, so the "95% CI" would have zero width — the most confident-looking
output in the system, produced by the least evidence.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pitchprob.betting.risk import DEFAULT_LIMITS, RiskLimits, breaker_tripped
from pitchprob.data.orm import Pick
from pitchprob.evaluation.significance import block_bootstrap_mean, week_block_labels
from pitchprob.services.ledger import realized_drawdown, tax_free_allowance
from pitchprob.services.tape import as_utc

#: Distinct ISO weeks required before a bootstrap interval is published.
#: Below this the interval is an artefact of having one block, not a
#: statement about uncertainty.
MIN_BLOCKS_FOR_CI = 4

#: Books whose tax-free allowance is worth tracking (Betclic's "Gra bez
#: podatku 2.0" is the one the program actually uses).
TRACKED_TAX_FREE_BOOKS = ("betclic",)


@dataclass(slots=True)
class WeeklyReport:
    metrics: dict[str, Any]
    report: str


def _interval(
    values: list[float], weeks: list[Any], *, n_boot: int
) -> dict[str, float] | None:
    """A block-bootstrap CI, or None when the evidence cannot support one."""
    if len(set(weeks)) < MIN_BLOCKS_FOR_CI:
        return None
    summary = block_bootstrap_mean(values, weeks, n_boot=n_boot)
    return {"lo": summary.lo, "hi": summary.hi, "p_value": summary.p_value}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def weekly_report(
    session: Session,
    *,
    now: datetime,
    days: int = 7,
    limits: RiskLimits = DEFAULT_LIMITS,
    n_boot: int = 10_000,
) -> WeeklyReport:
    """Assemble the week's report from the ledger and the tape."""
    picks = list(session.execute(select(Pick)).scalars().all())
    window_start = now - timedelta(days=days)
    in_window = [p for p in picks if as_utc(p.placed_at) >= window_start]

    settled_window = [
        p
        for p in in_window
        if p.settled_at is not None and p.gross_return_pln is not None
    ]
    window_profit = sum(
        (
            (p.gross_return_pln or Decimal("0")) - p.stake_pln
            for p in settled_window
        ),
        Decimal("0"),
    )

    with_exec = [p for p in picks if p.clv_exec is not None]
    with_sharp = [p for p in picks if p.clv_sharp is not None]
    paired = [
        p for p in picks if p.clv_exec is not None and p.clv_sharp is not None
    ]
    exec_weeks = list(week_block_labels(as_utc(p.kickoff_utc).date() for p in with_exec))
    sharp_weeks = list(
        week_block_labels(as_utc(p.kickoff_utc).date() for p in with_sharp)
    )
    paired_weeks = list(week_block_labels(as_utc(p.kickoff_utc).date() for p in paired))

    exec_values = [float(p.clv_exec) for p in with_exec if p.clv_exec is not None]
    sharp_values = [float(p.clv_sharp) for p in with_sharp if p.clv_sharp is not None]
    shopping_values = [
        float(p.clv_exec) - float(p.clv_sharp)
        for p in paired
        if p.clv_exec is not None and p.clv_sharp is not None
    ]

    drawdown = realized_drawdown(session, bankroll=limits.bankroll_pln)
    tripped = breaker_tripped(drawdown, limits=limits)

    metrics: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "window_days": days,
        "window": {
            "n_picks": len(in_window),
            "n_settled": len(settled_window),
            "staked_pln": float(
                sum((p.stake_pln for p in in_window), Decimal("0"))
            ),
            "profit_pln": float(window_profit),
        },
        "clv": {
            "n": len(with_exec),
            "n_blocks": len(set(exec_weeks)),
            "mean_exec": _mean(exec_values),
            "mean_sharp": _mean(sharp_values),
            "mean_shopping": _mean(shopping_values),
            "exec_ci": _interval(exec_values, exec_weeks, n_boot=n_boot),
            "sharp_ci": _interval(sharp_values, sharp_weeks, n_boot=n_boot),
            "shopping_ci": _interval(shopping_values, paired_weeks, n_boot=n_boot),
        },
        "tax_free": {
            book: {
                "used_pln": float(tax_free_allowance(session, book).used),
                "remaining_pln": float(tax_free_allowance(session, book).remaining),
            }
            for book in TRACKED_TAX_FREE_BOOKS
        },
        "overrides": [
            {
                "pick_id": p.id,
                "placed_at": as_utc(p.placed_at).isoformat(),
                "fixture": f"{p.home_team} vs {p.away_team}",
                "stake_pln": float(p.stake_pln),
                "risk_note": p.risk_note,
            }
            for p in picks
            if p.risk_override
        ],
        "drawdown": {
            "equity_pln": float(drawdown.equity_pln),
            "peak_equity_pln": float(drawdown.peak_equity_pln),
            "drawdown_pln": float(drawdown.drawdown_pln),
            "limit_pln": float(limits.max_drawdown_pln),
            "breaker_tripped": tripped,
        },
    }
    return WeeklyReport(metrics=metrics, report=_render(metrics))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{100 * value:+.2f}%"


def _ci_text(interval: dict[str, float] | None) -> str:
    if interval is None:
        return "no CI (too few weeks)"
    return (
        f"95% CI [{100 * interval['lo']:+.2f}%, {100 * interval['hi']:+.2f}%], "
        f"p={interval['p_value']:.3f}"
    )


def _render(metrics: dict[str, Any]) -> str:
    window = metrics["window"]
    clv = metrics["clv"]
    drawdown = metrics["drawdown"]
    lines = [
        f"# What the tape says — {metrics['generated_at'][:10]}",
        "",
        f"## Last {metrics['window_days']} days",
        "",
    ]
    if window["n_picks"] == 0:
        lines.append("- no picks placed")
    else:
        lines += [
            f"- {window['n_picks']} picks, {window['staked_pln']:.2f} PLN staked",
            f"- {window['n_settled']} settled, "
            f"realized {window['profit_pln']:+.2f} PLN",
        ]

    lines += ["", "## CLV — whole ledger (the decision variable)", ""]
    if clv["n"] == 0:
        lines.append(
            "- no picks carry CLV yet (they need a kickoff and a tape close)"
        )
    else:
        lines += [
            f"- n={clv['n']} bets across {clv['n_blocks']} ISO weeks",
            f"- sharp CLV (timing): {_pct(clv['mean_sharp'])} — "
            f"{_ci_text(clv['sharp_ci'])}",
            f"- exec CLV (PLN-real): {_pct(clv['mean_exec'])} — "
            f"{_ci_text(clv['exec_ci'])}",
            f"- shopping/promo component: {_pct(clv['mean_shopping'])} — "
            f"{_ci_text(clv['shopping_ci'])}",
            "",
            "Read sharp first: it is the only timing number. Exec minus sharp",
            "is what the venue and the promo contributed, which under the",
            "Phase 0-2a verdicts is the only place value has been shown to live.",
        ]
        if clv["n_blocks"] < MIN_BLOCKS_FOR_CI:
            lines += [
                "",
                f"**This is not a finding.** Fewer than {MIN_BLOCKS_FOR_CI} "
                "weeks of bets: the means above are point estimates and no "
                "interval is publishable, because a bootstrap over one block "
                "returns that block every time.",
            ]

    lines += ["", "## Tax-free allowance", ""]
    for book, state in metrics["tax_free"].items():
        lines.append(
            f"- {book}: {state['used_pln']:.2f} PLN used, "
            f"{state['remaining_pln']:.2f} PLN remaining"
        )

    lines += ["", "## Risk", ""]
    lines.append(
        f"- equity {drawdown['equity_pln']:.2f} PLN "
        f"(peak {drawdown['peak_equity_pln']:.2f}), drawdown "
        f"{drawdown['drawdown_pln']:.2f} of {drawdown['limit_pln']:.2f} PLN"
    )
    if drawdown["breaker_tripped"]:
        lines.append(
            "- **circuit breaker tripped**: `pick log` refuses new bets until "
            "the drawdown recovers, or until you override it deliberately."
        )
    else:
        lines.append("- circuit breaker open")

    overrides = metrics["overrides"]
    if overrides:
        lines += ["", f"{len(overrides)} pick(s) placed as a risk override:", ""]
        for entry in overrides:
            lines.append(
                f"- #{entry['pick_id']} {entry['fixture']} "
                f"({entry['stake_pln']:.2f} PLN) — {entry['risk_note']}"
            )
    else:
        lines.append("- no limit overrides on record")
    return "\n".join(lines)
