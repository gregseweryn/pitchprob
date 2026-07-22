"""Bookmaker promotions as data, with a kill switch (ADR 0017).

Betclic can withdraw "Bez Podatku 2.0" on 24h notice (§8) or exclude the
player (§4), so the promo's terms live here as a registry entry — one env
var (``PITCHPROB_DISABLED_PROMOS=betclic``) flips every surface back to the
bare x0.88 without touching verdict logic. No surface hard-codes "Betclic":
the effective-price router looks the book up by canonical name and prices
whatever the registry says.

The regime router degrades only downward (1.0 → 0.94 → 0.88): it may
understate a payout — a book we know nothing about is taxed, a spent limit
is the reduced regime — but it never claims a milder tax than the account
state supports. §3 ust. 4 makes the tax-free test stake-independent: any
remaining allowance qualifies the whole bet, so ``auto_terms`` needs only
the allowance, never the stake.

The x0.94 arm assumes a qualifying-AKO share below 50% (§3 ust. 11 pkt 1) —
true by construction for the single-only measurement season. An account
actually holding a >=50% share would pay x1.0 on everything; we understate
it, which is the permitted direction.
"""

from dataclasses import dataclass
from decimal import Decimal

from pitchprob.betting.effective import (
    BETCLIC_REDUCED_TAX_MULTIPLIER,
    BETCLIC_TAX_FREE_LIMIT,
    PromoTerms,
    TaxFreeAllowance,
)


@dataclass(frozen=True, slots=True)
class BookPromo:
    """One book's standing tax promotion, as the regulamin defines it."""

    #: Canonical key — the name the ledger and env config use.
    book: str
    #: Every spelling the feeds and the operator produce for this book,
    #: already canonicalised (the feed says "Betclic PL", the CLI "betclic").
    aliases: frozenset[str]
    #: Cumulative turnover under which every bet pays x1.0 (§3 ust. 3-4).
    tax_free_limit: Decimal
    #: Multiplier past the limit for singles / non-qualifying AKO
    #: (§3 ust. 11 pkt 1, qualifying-AKO share <50%).
    post_limit_multiplier: Decimal


BETCLIC_BEZ_PODATKU = BookPromo(
    book="betclic",
    aliases=frozenset({"betclic"}),
    tax_free_limit=BETCLIC_TAX_FREE_LIMIT,
    post_limit_multiplier=BETCLIC_REDUCED_TAX_MULTIPLIER,
)

PROMOS: tuple[BookPromo, ...] = (BETCLIC_BEZ_PODATKU,)


def canonical_bookmaker(name: str) -> str:
    """The registry/ledger key for any spelling of a bookmaker's name.

    The odds-api.io feed suffixes its Polish books with " PL" ("Betclic PL");
    the operator types lowercase short names. Team names have their own maps
    in ``data.normalize`` — bookmakers only need case, whitespace and the
    country suffix folded.
    """
    canonical = name.strip().lower()
    return canonical.removesuffix(" pl").strip()


def parse_disabled(csv: str) -> frozenset[str]:
    """Canonical book keys from the ``PITCHPROB_DISABLED_PROMOS`` CSV value."""
    return frozenset(
        canonical_bookmaker(part) for part in csv.split(",") if part.strip()
    )


def active_promo(
    name: str, *, disabled: frozenset[str] = frozenset()
) -> BookPromo | None:
    """The standing promo at this book, or None (= bare x0.88 pricing)."""
    key = canonical_bookmaker(name)
    if key in disabled:
        return None
    for promo in PROMOS:
        if key in promo.aliases:
            return promo
    return None


def auto_terms(promo: BookPromo, allowance: TaxFreeAllowance) -> PromoTerms:
    """The promo's current tax regime, derived from the allowance state.

    Any remaining allowance → x1.0 on the whole bet (§3 ust. 4); a spent
    limit → the reduced post-limit multiplier. Never upward.
    """
    if allowance.remaining > 0:
        return PromoTerms(tax_free=True, tax_multiplier=Decimal("1"))
    return PromoTerms(tax_multiplier=promo.post_limit_multiplier)
