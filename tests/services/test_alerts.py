"""Alert payload + notifier tests (P0 "speaking loop", ADR 0016).

The loop's whole value is that it *speaks* — so the message is a contract,
not a convenience. Two invariants are under test here: the operator-facing
copy is Polish and carries the scanner caveats *in the message body* (ADR
0004, honesty-as-payload), and the Telegram bot token never reaches a log
or an exception (same scrubbing discipline as the odds-api key).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from pitchprob.services.alerts import (
    Alert,
    StdoutNotifier,
    TelegramNotifier,
    format_play_alert,
)
from pitchprob.services.scanner import SCANNER_CAVEATS

_COMMENCE = datetime(2026, 8, 21, 19, 0, tzinfo=UTC)


def _alert(*, verdict: str = "PLAY", promo_value: float | None = None) -> Alert:
    return Alert(
        event_id="ev1",
        home_team="Arsenal",
        away_team="Coventry City",
        commence_time=_COMMENCE,
        market="ou",
        selection="over",
        line=Decimal("3.0"),
        bookmaker="Betclic PL",
        verdict=verdict,
        edge=0.042,
        price_quoted=Decimal("2.10"),
        price_effective=Decimal("2.10"),
        promo_value=promo_value,
        anchor_price=Decimal("1.95"),
        anchor_age=timedelta(hours=10),
        suggested_stake_pln=Decimal("5"),
        caveats=SCANNER_CAVEATS,
    )


class TestFormatting:
    def test_play_alert_is_polish_and_carries_every_caveat(self) -> None:
        text = format_play_alert(_alert())
        assert text.splitlines()[0].startswith("🟢 GRAJ")
        assert "Arsenal" in text and "Coventry City" in text
        assert "Betclic PL" in text
        assert "przewaga" in text.lower()
        assert "+4.2%" in text  # signed edge percentage
        assert "Pinnacle" in text or "pinnacle" in text
        # honesty-as-payload: the caveats travel *in the message*, not in a
        # footer the client is trusted to add.
        for caveat in SCANNER_CAVEATS:
            assert caveat in text

    def test_unverified_alert_is_a_lead_not_an_authorisation(self) -> None:
        text = format_play_alert(_alert(verdict="UNVERIFIED"))
        header = text.splitlines()[0]
        assert "GRAJ" not in header  # not authorised — a lead, not a bet
        assert "LEAD" in header
        # it must tell the operator to go look at the book's own screen
        assert "sprawd" in header.lower()  # "sprawdź"

    def test_promo_value_is_shown_when_present(self) -> None:
        text = format_play_alert(_alert(promo_value=0.136))
        assert "promo" in text.lower()
        assert "13.6" in text

    def test_suggested_stake_and_anchor_age_are_shown(self) -> None:
        text = format_play_alert(_alert())
        assert "5" in text  # suggested stake PLN
        assert "10" in text  # anchor age hours


class TestTelegramNotifier:
    def test_token_never_appears_in_a_scrubbed_error(self) -> None:
        token = "123456:SECRET-TOKEN-VALUE"

        def _boom(url: str, payload: dict[str, str]) -> None:
            request = httpx.Request("POST", url)
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("boom", request=request, response=response)

        notifier = TelegramNotifier(token, "chat1", send=_boom)
        with pytest.raises(RuntimeError) as excinfo:
            notifier.send("hello")
        assert token not in str(excinfo.value)
        assert "SECRET-TOKEN-VALUE" not in str(excinfo.value)

    def test_send_posts_the_text_to_the_chat(self) -> None:
        seen: dict[str, str] = {}

        def _capture(url: str, payload: dict[str, str]) -> None:
            seen["url"] = url
            seen["chat_id"] = payload["chat_id"]
            seen["text"] = payload["text"]

        notifier = TelegramNotifier("tok", "chat1", send=_capture)
        notifier.send("cześć")
        assert seen["chat_id"] == "chat1"
        assert seen["text"] == "cześć"
        assert seen["url"].endswith("/sendMessage")


class TestStdoutNotifier:
    def test_writes_the_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        StdoutNotifier().send("linia alertu")
        assert "linia alertu" in capsys.readouterr().out
