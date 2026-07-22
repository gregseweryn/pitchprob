"""Alert payload and delivery for the "speaking loop" (P0, ADR 0016).

The research engine could always *answer* a question; what it could not do
was *speak* — nothing reached the operator's phone unprompted. This module
is the mouth. It defines the ``Alert`` payload the watch loop emits, renders
it as Polish operator copy (PRODUCT.md: verdict *values* stay English, the
prose is Polish), and delivers it through a ``Notifier`` — Telegram by
default, stdout for dry runs and tests.

Two disciplines carry over from the rest of the repo:

- **Honesty-as-payload (ADR 0004).** The scanner caveats are rendered *in
  the message body*, never assumed to live in a client footer. A verdict
  that arrives without the sentences that qualify it is a verdict that lies
  by omission.
- **Key scrubbing (ADR 0012/0014).** The Telegram bot token sits in the
  request *path* (``/bot<token>/sendMessage``), so an httpx error message
  would leak it verbatim. Every failure is scrubbed to a status line before
  it can reach a log or a traceback.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

import httpx

from pitchprob.services.scanner import SCANNER_CAVEATS, Verdict

__all__ = [
    "Alert",
    "Notifier",
    "StdoutNotifier",
    "TelegramNotifier",
    "format_play_alert",
]


@dataclass(frozen=True, slots=True)
class Alert:
    """One actionable line the loop decided is worth the operator's phone.

    Only ``PLAY`` and ``UNVERIFIED`` verdicts become alerts — a ``NO BET``,
    ``STALE`` or ``NO ANCHOR`` is silence by design. ``suggested_stake_pln``
    is the flat stake that fits the risk limits as of detection; the loop
    never emits an alert it could not have bet (ADR 0015).
    """

    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    market: str
    selection: str
    line: Decimal | None
    bookmaker: str
    verdict: Verdict
    edge: float
    price_quoted: Decimal
    price_effective: Decimal
    promo_value: float | None
    anchor_price: Decimal
    anchor_age: timedelta
    suggested_stake_pln: Decimal
    caveats: tuple[str, ...] = SCANNER_CAVEATS


class Notifier(Protocol):
    """Anything that can deliver one rendered message to the operator."""

    def send(self, text: str) -> None: ...


#: Market/selection → Polish operator label. The verdict *values* stay
#: English (PRODUCT.md), but everything the operator reads is Polish.
_SELECTION_PL: dict[tuple[str, str], str] = {
    ("1x2", "home"): "1 (gospodarz)",
    ("1x2", "draw"): "X (remis)",
    ("1x2", "away"): "2 (gość)",
    ("ou", "over"): "gole powyżej",
    ("ou", "under"): "gole poniżej",
    ("ah", "home"): "handicap gospodarz",
    ("ah", "away"): "handicap gość",
    ("corners_ou", "over"): "rożne powyżej",
    ("corners_ou", "under"): "rożne poniżej",
    ("corners_ah", "home"): "rożne handicap gospodarz",
    ("corners_ah", "away"): "rożne handicap gość",
}


def _selection_label(market: str, selection: str, line: Decimal | None) -> str:
    label = _SELECTION_PL.get((market, selection), f"{market} {selection}")
    return f"{label} {line}" if line is not None else label


def _age(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600.0
    return f"{hours:.0f} h"


def format_play_alert(alert: Alert) -> str:
    """Render one alert as Polish operator copy, caveats included.

    ``UNVERIFIED`` is rendered as a *lead*, not an authorisation: the feed
    has not been validated against the book's own screen (ADR 0014), so the
    operator is told to go and look rather than to bet.
    """
    if alert.verdict == "PLAY":
        header = "🟢 GRAJ"
    else:  # UNVERIFIED
        header = (
            "🟡 LEAD (niezweryfikowany feed — sprawdź kurs na stronie buka "
            "przed zakładem)"
        )
    lines = [
        header,
        f"{alert.home_team} vs {alert.away_team}",
        f"początek: {alert.commence_time:%Y-%m-%d %H:%M} UTC",
        f"rynek: {_selection_label(alert.market, alert.selection, alert.line)}",
        f"bukmacher: {alert.bookmaker}",
        f"kurs: {alert.price_quoted} (efektywny {alert.price_effective})",
        f"przewaga: {100 * alert.edge:+.1f}% vs Pinnacle fair",
        f"kotwica: pinnacle {alert.anchor_price} · wiek {_age(alert.anchor_age)}",
    ]
    if alert.promo_value is not None:
        lines.append(f"promocja wnosi: {100 * alert.promo_value:+.1f} pp EV")
    lines.append(
        f"sugerowana stawka: {alert.suggested_stake_pln} zł (flat, w limitach ryzyka)"
    )
    lines.append("—")
    lines.extend(alert.caveats)
    return "\n".join(lines)


class StdoutNotifier:
    """Prints the message. The default for ``--dry-run`` and for tests."""

    def send(self, text: str) -> None:
        print(text)


#: (url, json payload) -> None. Injected in tests; the default posts via httpx.
SendFn = Callable[[str, dict[str, str]], None]

_TELEGRAM_BASE = "https://api.telegram.org"


def _default_send(url: str, payload: dict[str, str]) -> None:
    # httpx logs full request URLs at INFO, and the bot token rides in the
    # URL path — same discipline as the odds adapters.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    response = httpx.post(url, json=payload, timeout=30.0)
    response.raise_for_status()


def _scrub_telegram_error(exc: httpx.HTTPError) -> str:
    """A key-free description of a Telegram delivery failure.

    The bot token is in the request path, so neither ``str(exc)`` nor the
    URL may surface. Status code is safe and is the only fact that helps:
    401/404 = bad token, 400 = malformed chat id or text.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"Telegram delivery failed: HTTP {exc.response.status_code} "
            "(URL withheld — it carries the bot token)"
        )
    return (
        f"Telegram delivery failed: {type(exc).__name__} "
        "(URL withheld — it carries the bot token)"
    )


class TelegramNotifier:
    """Delivers alerts to one Telegram chat via a bot.

    Both the token and chat id come from the environment
    (``PITCHPROB_TELEGRAM_BOT_TOKEN`` / ``PITCHPROB_TELEGRAM_CHAT_ID``); this
    class only holds them. Delivery failures are re-raised *scrubbed* so a
    bad token in a cron log never becomes a leaked credential.
    """

    def __init__(self, token: str, chat_id: str, *, send: SendFn | None = None) -> None:
        self._token = token
        self._chat_id = chat_id
        self._send = send if send is not None else _default_send

    def send(self, text: str) -> None:
        url = f"{_TELEGRAM_BASE}/bot{self._token}/sendMessage"
        try:
            self._send(url, {"chat_id": self._chat_id, "text": text})
        except httpx.HTTPError as exc:
            raise RuntimeError(_scrub_telegram_error(exc)) from None
