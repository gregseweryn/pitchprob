"""Effective-odds and promo-EV math (Phase 5 part 3).

Every number below is hand-computed. The Polish 12% turnover tax multiplies
gross payouts by 0.88, so it composes with the quoted price exactly — all
price arithmetic is Decimal, floats appear only in probabilities and EV.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitchprob.betting.effective import (
    BETCLIC_TAX_FREE_LIMIT,
    TAX_MULTIPLIER,
    PromoEvaluation,
    PromoTerms,
    TaxFreeAllowance,
    effective_price,
    promo_ev,
)


class TestEffectivePrice:
    def test_taxed_price_is_quoted_times_088(self) -> None:
        # 2.05 * 0.88 = 1.8040 exactly
        assert effective_price(Decimal("2.05")) == Decimal("1.8040")

    def test_tax_free_price_is_quoted_unchanged(self) -> None:
        assert effective_price(Decimal("2.05"), tax_free=True) == Decimal("2.05")

    def test_short_price_can_fall_below_even_money(self) -> None:
        # 1.10 * 0.88 = 0.9680 — a taxed favourite pays back less than the
        # stake even when it wins; the math must not hide that.
        assert effective_price(Decimal("1.10")) == Decimal("0.9680")

    @pytest.mark.parametrize("quoted", ["1.0", "0.5", "-2.0"])
    def test_rejects_non_positive_margin_prices(self, quoted: str) -> None:
        with pytest.raises(ValueError):
            effective_price(Decimal(quoted))

    @given(
        st.decimals(
            min_value=Decimal("1.01"),
            max_value=Decimal("100"),
            places=3,
            allow_nan=False,
        )
    )
    def test_taxed_is_exactly_088_of_quoted(self, quoted: Decimal) -> None:
        eff = effective_price(quoted)
        assert eff == quoted * TAX_MULTIPLIER
        assert eff < quoted
        assert effective_price(quoted, tax_free=True) == quoted


class TestTaxFreeAllowance:
    def test_betclic_limit_constant(self) -> None:
        assert Decimal("1000") == BETCLIC_TAX_FREE_LIMIT

    def test_covers_when_stake_fits(self) -> None:
        allowance = TaxFreeAllowance(limit=Decimal("1000"), used=Decimal("995"))
        assert allowance.covers(Decimal("5"))
        assert allowance.remaining == Decimal("5")

    def test_does_not_cover_when_stake_straddles_the_limit(self) -> None:
        # The whole bet must fit: a coupon cannot be split between the
        # tax-free and taxed regimes at settlement.
        allowance = TaxFreeAllowance(limit=Decimal("1000"), used=Decimal("998"))
        assert not allowance.covers(Decimal("5"))

    def test_exhausted_allowance_has_zero_remaining(self) -> None:
        allowance = TaxFreeAllowance(limit=Decimal("1000"), used=Decimal("1200"))
        assert allowance.remaining == Decimal("0")
        assert not allowance.covers(Decimal("2"))


class TestPromoEv:
    def test_no_promo_evaluates_the_taxed_price(self) -> None:
        # fair p = 0.5, quoted 2.10: effective 1.848, EV = 0.5*1.848 - 1 = -0.076
        result = promo_ev(0.5, Decimal("2.10"))
        assert result.price_effective_base == Decimal("1.8480")
        assert result.price_effective_promo == Decimal("1.8480")
        assert result.ev_base == pytest.approx(-0.076)
        assert result.ev_promo == pytest.approx(-0.076)
        assert result.promo_value == pytest.approx(0.0)

    def test_tax_free_promo_recovers_the_tax(self) -> None:
        # Same price under "Gra bez podatku": EV = 0.5*2.10 - 1 = +0.05;
        # the promo is worth the full 12 points of payout: 0.05-(-0.076)=0.126
        result = promo_ev(0.5, Decimal("2.10"), promo=PromoTerms(tax_free=True))
        assert result.price_effective_promo == Decimal("2.10")
        assert result.ev_promo == pytest.approx(0.05)
        assert result.promo_value == pytest.approx(0.126)

    def test_boost_prices_the_boosted_quote(self) -> None:
        # Boost 2.10 -> 2.40, still taxed: eff = 2.112, EV = 0.5*2.112-1 = 0.056
        result = promo_ev(
            0.5, Decimal("2.10"), promo=PromoTerms(boosted_price=Decimal("2.40"))
        )
        assert result.price_effective_promo == Decimal("2.1120")
        assert result.ev_promo == pytest.approx(0.056)
        assert result.promo_value == pytest.approx(0.132)

    def test_boost_with_tax_free_composes(self) -> None:
        # Boost to 2.40 AND tax-free: EV = 0.5*2.40 - 1 = +0.20
        result = promo_ev(
            0.5,
            Decimal("2.10"),
            promo=PromoTerms(tax_free=True, boosted_price=Decimal("2.40")),
        )
        assert result.ev_promo == pytest.approx(0.20)

    def test_payout_haircut_discounts_conditioned_winnings(self) -> None:
        # Winnings paid as a conditioned bonus valued at 80 groszy per złoty:
        # EV = 0.5 * 2.40 * 0.8 - 1 = -0.04
        result = promo_ev(
            0.5,
            Decimal("2.10"),
            promo=PromoTerms(
                tax_free=True,
                boosted_price=Decimal("2.40"),
                payout_haircut=0.8,
            ),
        )
        assert result.ev_promo == pytest.approx(-0.04)

    def test_boost_below_quoted_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            promo_ev(
                0.5, Decimal("2.10"), promo=PromoTerms(boosted_price=Decimal("2.00"))
            )

    @pytest.mark.parametrize("probability", [0.0, 1.0, -0.2, 1.7])
    def test_rejects_degenerate_probabilities(self, probability: float) -> None:
        with pytest.raises(ValueError):
            promo_ev(probability, Decimal("2.10"))

    @pytest.mark.parametrize("haircut", [0.0, -0.5, 1.2])
    def test_rejects_bad_haircut(self, haircut: float) -> None:
        with pytest.raises(ValueError):
            promo_ev(
                0.5, Decimal("2.10"), promo=PromoTerms(payout_haircut=haircut)
            )

    @given(
        p=st.floats(min_value=0.01, max_value=0.99),
        quoted=st.decimals(
            min_value=Decimal("1.05"),
            max_value=Decimal("50"),
            places=2,
            allow_nan=False,
        ),
    )
    def test_tax_free_promo_value_is_never_negative(
        self, p: float, quoted: Decimal
    ) -> None:
        """Removing the tax (with cash payout) can only add EV."""
        result = promo_ev(p, quoted, promo=PromoTerms(tax_free=True))
        assert result.promo_value >= 0.0
        assert isinstance(result, PromoEvaluation)

    @given(p=st.floats(min_value=0.01, max_value=0.99))
    def test_ev_is_monotone_in_fair_probability(self, p: float) -> None:
        lower = promo_ev(p * 0.9, Decimal("2.50"))
        higher = promo_ev(p, Decimal("2.50"))
        assert higher.ev_base > lower.ev_base
