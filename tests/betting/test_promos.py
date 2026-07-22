"""The promo registry as data (ADR 0017).

Betclic can withdraw "Bez Podatku 2.0" on 24h notice (§8) or exclude the
player (§4), so promo terms are configuration with a kill switch, not
constants inside verdict logic. The regime router degrades only downward
(1.0 → 0.94 → 0.88) — it may understate a payout, never overstate one.
"""

from decimal import Decimal

from pitchprob.betting.effective import TaxFreeAllowance
from pitchprob.betting.promos import (
    BETCLIC_BEZ_PODATKU,
    PROMOS,
    active_promo,
    auto_terms,
    canonical_bookmaker,
    parse_disabled,
)


class TestCanonicalBookmaker:
    def test_feed_naming_maps_to_the_ledger_key(self) -> None:
        # The odds-api.io feed says "Betclic PL"; the operator types "betclic".
        assert canonical_bookmaker("Betclic PL") == "betclic"
        assert canonical_bookmaker("betclic") == "betclic"

    def test_strips_case_and_whitespace(self) -> None:
        assert canonical_bookmaker("  STS PL ") == "sts"
        assert canonical_bookmaker("eFortuna PL") == "efortuna"


class TestRegistry:
    def test_betclic_terms_match_the_regulamin(self) -> None:
        assert BETCLIC_BEZ_PODATKU.book == "betclic"
        assert BETCLIC_BEZ_PODATKU.tax_free_limit == Decimal("1000")
        assert BETCLIC_BEZ_PODATKU.post_limit_multiplier == Decimal("0.94")
        assert BETCLIC_BEZ_PODATKU in PROMOS

    def test_active_promo_resolves_aliases(self) -> None:
        assert active_promo("Betclic PL") is BETCLIC_BEZ_PODATKU
        assert active_promo("betclic") is BETCLIC_BEZ_PODATKU

    def test_unknown_book_has_no_promo(self) -> None:
        # A book outside the registry prices at the bare x0.88 — the
        # conservative direction when we know nothing.
        assert active_promo("STS PL") is None

    def test_kill_switch_disables_the_promo(self) -> None:
        # §8: the offer can be withdrawn on 24h notice; one env var flips
        # every surface back to x0.88 without touching logic.
        assert active_promo("Betclic PL", disabled=frozenset({"betclic"})) is None

    def test_parse_disabled_reads_the_csv_env_value(self) -> None:
        assert parse_disabled("") == frozenset()
        assert parse_disabled("betclic") == frozenset({"betclic"})
        assert parse_disabled(" Betclic , sts ") == frozenset({"betclic", "sts"})


class TestAutoTerms:
    def test_remaining_allowance_means_tax_free(self) -> None:
        allowance = TaxFreeAllowance(limit=Decimal("1000"), used=Decimal("998"))
        terms = auto_terms(BETCLIC_BEZ_PODATKU, allowance)
        assert terms.tax_multiplier == Decimal("1")
        assert terms.tax_free is True

    def test_spent_allowance_degrades_to_the_reduced_regime(self) -> None:
        allowance = TaxFreeAllowance(limit=Decimal("1000"), used=Decimal("1000"))
        terms = auto_terms(BETCLIC_BEZ_PODATKU, allowance)
        assert terms.tax_multiplier == Decimal("0.94")
        assert terms.tax_free is False
