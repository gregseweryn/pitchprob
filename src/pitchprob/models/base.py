"""Shared model types and training-frame validation."""

from dataclasses import dataclass

import pandas as pd

#: Canonical training-frame columns for all models.
REQUIRED_COLUMNS = ("date", "home_team", "away_team", "ft_home", "ft_away")


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
