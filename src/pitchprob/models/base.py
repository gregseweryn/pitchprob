"""Shared model types and training-frame validation."""

from dataclasses import dataclass
from typing import Any

import pandas as pd

#: Canonical training-frame columns for all models.
REQUIRED_COLUMNS = ("date", "home_team", "away_team", "ft_home", "ft_away")

#: Pre-match passthrough columns (ADR 0008): known before kickoff but not
#: derivable from match history, so prediction-time callers must forward them
#: explicitly — history-based feature state cannot reconstruct them, and
#: silently dropping them breaks train/serve parity (the M5/A1 defect).
ABSENCE_COLUMNS = ("absences_home", "absences_away")


def absences_from_row(row: Any) -> dict[str, float]:
    """Absence passthrough values a prediction row carries, as keyword
    arguments for ``match_probabilities_at``. Missing columns are simply
    absent from the result (legacy three-argument models stay callable);
    NaN values pass through untouched — NaN means "no coverage", and the
    model must see it rather than a fabricated healthy squad."""
    out: dict[str, float] = {}
    for column in ABSENCE_COLUMNS:
        value = getattr(row, column, None)
        if value is not None:
            out[column] = float(value)
    return out


@dataclass(frozen=True, slots=True)
class OutcomeProbabilities:
    home: float
    draw: float
    away: float


def validate_matches(matches: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in matches.columns]
    if missing:
        raise ValueError(f"training frame missing columns: {missing}")
    if len(matches) == 0:
        raise ValueError("training frame is empty")
