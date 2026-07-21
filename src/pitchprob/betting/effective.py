"""Effective odds under the Polish 12% turnover tax, and promo pricing.

Polish-licensed bookmakers collect a 12% tax on the stake at bet placement,
so a winning bet returns ``quoted x 0.88`` per unit staked — the tax sits in
the price, not in the bankroll. Every comparison against a fair anchor must
therefore run on *effective* prices (audit 2026-07, Etap 3.2). Under a
tax-free promotion (Betclic "Gra bez podatku 2.0": the first 1,000 PLN of
stakes are unconditionally tax-free) the multiplier is 1.0, which is worth
+13.6% of payout — more than any realistic model edge.

Promotions are priced as instruments: ``promo_ev`` values a quote with and
without its promo against the same fair probability, so a boost or tax-free
offer gets an EV number instead of a feeling. Price arithmetic is Decimal
throughout (exact); probabilities and EV are floats, as everywhere else in
the betting layer.
"""

from dataclasses import dataclass
from decimal import Decimal

#: Polish turnover tax on the stake (ustawa o grach hazardowych, art. 73).
TAX_RATE = Decimal("0.12")

#: Payout multiplier of a taxed bet: quoted price x 0.88.
TAX_MULTIPLIER = Decimal("1") - TAX_RATE

#: "Gra bez podatku 2.0": stakes are unconditionally tax-free up to this
#: cumulative turnover; beyond it a >=50%-odds AKO condition applies, which
#: singles cannot meet — so the ledger treats the limit as hard.
BETCLIC_TAX_FREE_LIMIT = Decimal("1000")


def effective_price(quoted: Decimal, *, tax_free: bool = False) -> Decimal:
    """Decimal price actually paid out per unit staked at a Polish book.

    A taxed favourite below ~1.14 pays back less than the stake even when it
    wins; the returned price is allowed to fall below 1.0 so that EV math
    shows it rather than hiding it.
    """
    if quoted <= 1:
        raise ValueError(f"decimal price must exceed 1.0, got {quoted}")
    return quoted if tax_free else quoted * TAX_MULTIPLIER


@dataclass(frozen=True, slots=True)
class TaxFreeAllowance:
    """Running total against a tax-free turnover limit.

    A bet qualifies only if it fits the remaining allowance *in full* — a
    coupon cannot be split between tax regimes at settlement, so a straddling
    stake is treated as taxed.
    """

    limit: Decimal
    used: Decimal

    @property
    def remaining(self) -> Decimal:
        return max(self.limit - self.used, Decimal("0"))

    def covers(self, stake: Decimal) -> bool:
        return stake <= self.remaining


@dataclass(frozen=True, slots=True)
class PromoTerms:
    """One promotion attached to a quote.

    ``payout_haircut`` discounts the gross payout when winnings arrive as a
    conditioned bonus (freebet, rollover) instead of cash — estimating that
    discount is the operator's judgment; 1.0 means unconditional cash.
    ``max_stake`` caps the stake the promo applies to; it does not change
    per-unit EV (flat 2-5 PLN stakes sit far below typical caps) and is
    carried for reporting only.
    """

    tax_free: bool = False
    boosted_price: Decimal | None = None
    max_stake: Decimal | None = None
    payout_haircut: float = 1.0


@dataclass(frozen=True, slots=True)
class PromoEvaluation:
    """A quote priced with and without its promotion, per unit staked."""

    price_effective_base: Decimal
    price_effective_promo: Decimal
    ev_base: float
    ev_promo: float

    @property
    def promo_value(self) -> float:
        """EV the promotion adds over betting the bare taxed quote."""
        return self.ev_promo - self.ev_base


def promo_ev(
    fair_probability: float,
    quoted: Decimal,
    *,
    promo: PromoTerms | None = None,
) -> PromoEvaluation:
    """Price a quote (and its promotion, if any) against a fair probability.

    The base leg is always the bare taxed quote — that is what the selection
    costs without the promo, so ``promo_value`` isolates the instrument.
    """
    if not 0.0 < fair_probability < 1.0:
        raise ValueError(
            f"fair probability must lie in (0, 1), got {fair_probability}"
        )
    terms = promo if promo is not None else PromoTerms()
    if not 0.0 < terms.payout_haircut <= 1.0:
        raise ValueError(
            f"payout haircut must lie in (0, 1], got {terms.payout_haircut}"
        )
    if terms.boosted_price is not None and terms.boosted_price < quoted:
        raise ValueError(
            f"boosted price {terms.boosted_price} below quoted {quoted}"
        )
    base = effective_price(quoted)
    promo_quote = terms.boosted_price if terms.boosted_price is not None else quoted
    promo_price = effective_price(promo_quote, tax_free=terms.tax_free)
    ev_base = fair_probability * float(base) - 1.0
    ev_promo = fair_probability * float(promo_price) * terms.payout_haircut - 1.0
    return PromoEvaluation(
        price_effective_base=base,
        price_effective_promo=promo_price,
        ev_base=ev_base,
        ev_promo=ev_promo,
    )
