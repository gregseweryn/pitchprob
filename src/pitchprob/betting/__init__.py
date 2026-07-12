"""Betting mathematics: odds ↔ probabilities, overround removal, EV, staking."""

from pitchprob.betting.odds_math import (
    fair_odds,
    implied_probabilities,
    overround,
    remove_overround_multiplicative,
    remove_overround_shin,
)
from pitchprob.betting.staking import expected_value, kelly_fraction

__all__ = [
    "expected_value",
    "fair_odds",
    "implied_probabilities",
    "kelly_fraction",
    "overround",
    "remove_overround_multiplicative",
    "remove_overround_shin",
]
