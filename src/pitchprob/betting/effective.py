"""Effective odds under the Polish 12% turnover tax, and promo pricing.

Polish-licensed bookmakers collect a 12% tax on the stake at bet placement,
so a winning bet returns ``quoted x 0.88`` per unit staked — the tax sits in
the price, not in the bankroll. Every comparison against a fair anchor must
therefore run on *effective* prices (audit 2026-07, Etap 3.2).

Betclic's "Bez Podatku 2.0" (regulamin in force since 2026-05-21) defines
three payout regimes, all expressed here as one multiplier on the quoted
price:

- **x1.0** while cumulative turnover since the offer started is under
  1,000 PLN. §3 ust. 4: if *any* allowance remains before the bet, the
  whole bet qualifies regardless of its stake — a straddling stake is
  tax-free in full, not taxed.
- **x0.94** past the limit, for singles and non-qualifying accumulators,
  when qualifying AKO (>=2 legs at odds >=1.20 each) make up <50% of the
  rolling last 5,000 bets / 12 months (§3 ust. 11 pkt 1) — a 6% tax, not
  the statutory 12%. With a qualifying-AKO share >=50%, everything pays
  x1.0 (§3 ust. 11 pkt 2).
- **x0.88** with no promotion at all — the offer can be withdrawn on 24h
  notice (§8) or the player excluded (§4), so the promo terms live in
  configuration (``betting.promos``), never hard-coded into a verdict.

Removing the tax is worth +13.6% of payout — more than any realistic model
edge. Promotions are priced as instruments: ``promo_ev`` values a quote with
and without its promo against the same fair probability, so a boost or
tax-free offer gets an EV number instead of a feeling. Price arithmetic is
Decimal throughout (exact); probabilities and EV are floats, as everywhere
else in the betting layer.
"""

from dataclasses import dataclass
from decimal import Decimal

#: Polish turnover tax on the stake (ustawa o grach hazardowych, art. 73).
TAX_RATE = Decimal("0.12")

#: Payout multiplier of a taxed bet: quoted price x 0.88.
TAX_MULTIPLIER = Decimal("1") - TAX_RATE

#: "Bez Podatku 2.0": every bet placed while any of this cumulative turnover
#: remains pays x1.0 (§3 ust. 3-4 — a straddling stake qualifies in full).
BETCLIC_TAX_FREE_LIMIT = Decimal("1000")

#: Past the limit, Betclic singles (and non-qualifying AKO) pay a reduced 6%
#: tax — Wskaźnik 0,94 (§3 ust. 11 pkt 1, qualifying-AKO share <50%). The
#: single-only measurement season sits in this bucket once the limit is
#: spent; x0.88 would understate Betclic payouts by 6pp.
BETCLIC_REDUCED_TAX_MULTIPLIER = Decimal("0.94")


def effective_price(
    quoted: Decimal,
    *,
    tax_free: bool = False,
    multiplier: Decimal | None = None,
) -> Decimal:
    """Decimal price actually paid out per unit staked at a Polish book.

    ``multiplier`` names the payout regime exactly (1.0 / 0.94 / 0.88) and
    wins over the ``tax_free`` sugar when both are given. A taxed favourite
    below ~1.14 pays back less than the stake even when it wins; the returned
    price is allowed to fall below 1.0 so that EV math shows it rather than
    hiding it.
    """
    if quoted <= 1:
        raise ValueError(f"decimal price must exceed 1.0, got {quoted}")
    if multiplier is None:
        multiplier = Decimal("1") if tax_free else TAX_MULTIPLIER
    elif not Decimal("0") < multiplier <= Decimal("1"):
        raise ValueError(
            f"payout multiplier must lie in (0, 1], got {multiplier}"
        )
    return quoted * multiplier


@dataclass(frozen=True, slots=True)
class TaxFreeAllowance:
    """Running total against a tax-free turnover limit.

    §3 ust. 4 of "Bez Podatku 2.0": a bet qualifies for the x1.0 payout if
    *any* allowance remains before it is placed — the whole stake qualifies
    even when it straddles the limit, so ``covers`` ignores the stake size
    and only asks whether the limit is spent.
    """

    limit: Decimal
    used: Decimal

    @property
    def remaining(self) -> Decimal:
        return max(self.limit - self.used, Decimal("0"))

    def covers(self, stake: Decimal) -> bool:
        return self.remaining > 0


@dataclass(frozen=True, slots=True)
class PromoTerms:
    """One promotion attached to a quote.

    ``tax_multiplier`` names the payout regime exactly (1.0 tax-free, 0.94
    reduced past the Betclic limit, 0.88 bare tax); ``None`` derives it from
    the ``tax_free`` sugar, which stays for compatibility. When set, the
    multiplier wins — the regime router (``betting.promos``) resolves to a
    single number and the boolean must not contradict it.
    ``payout_haircut`` discounts the gross payout when winnings arrive as a
    conditioned bonus (freebet, rollover) instead of cash — estimating that
    discount is the operator's judgment; 1.0 means unconditional cash.
    ``max_stake`` caps the stake the promo applies to; it does not change
    per-unit EV (flat 2-5 PLN stakes sit far below typical caps) and is
    carried for reporting only.
    """

    tax_free: bool = False
    tax_multiplier: Decimal | None = None
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
    promo_price = effective_price(
        promo_quote, tax_free=terms.tax_free, multiplier=terms.tax_multiplier
    )
    ev_base = fair_probability * float(base) - 1.0
    ev_promo = fair_probability * float(promo_price) * terms.payout_haircut - 1.0
    return PromoEvaluation(
        price_effective_base=base,
        price_effective_promo=promo_price,
        ev_base=ev_base,
        ev_promo=ev_promo,
    )
