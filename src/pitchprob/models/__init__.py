"""Probability models. Goal-space models emit score matrices (ADR 0002);
outcome-space models emit 1X2 probabilities directly."""

from pitchprob.models.base import OutcomeProbabilities, validate_matches
from pitchprob.models.dixon_coles import (
    DixonColesModel,
    DixonColesParams,
    IndependentPoissonModel,
    time_decay_weights,
)
from pitchprob.models.elo import EloModel

__all__ = [
    "DixonColesModel",
    "DixonColesParams",
    "EloModel",
    "IndependentPoissonModel",
    "OutcomeProbabilities",
    "time_decay_weights",
    "validate_matches",
]
