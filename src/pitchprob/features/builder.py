"""Streaming pre-match feature construction.

One chronological pass maintains per-team state (rolling windows, venue
windows, rest dates, Elo). For every match the emitted feature row reflects
*only prior matches* — the update happens strictly after emission, which is
what the truncation-invariance test pins down.

Missing history yields NaN, never a fabricated default: the downstream GBM
handles NaN natively, and imputation would blur exactly the "newly promoted /
early season" uncertainty the model should see.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from pitchprob.models.base import validate_matches
from pitchprob.models.elo import elo_update

_PER_SIDE = (
    "ppg10", "gf10", "ga10",
    "shots_for10", "shots_against10",
    "sot_for10", "sot_against10",
    "corners_for10", "corners_against10",
    "xg_for10", "xg_against10",
    "venue_ppg5", "venue_gf5", "venue_ga5",
    "rest_days", "elo",
)

#: Numeric model inputs, in stable order. League (categorical) and the meta
#: columns (date, teams) ride along in the frame but are not listed here.
FEATURE_COLUMNS: list[str] = (
    [f"h_{name}" for name in _PER_SIDE]
    + [f"a_{name}" for name in _PER_SIDE]
    + ["elo_diff"]
)

META_COLUMNS = ["date", "league", "home_team", "away_team"]

#: (record key, per-side stat name) for the overall rolling window.
_OVERALL_KEYS = (
    ("points", "ppg10"),
    ("gf", "gf10"),
    ("ga", "ga10"),
    ("shots_for", "shots_for10"),
    ("shots_against", "shots_against10"),
    ("sot_for", "sot_for10"),
    ("sot_against", "sot_against10"),
    ("corners_for", "corners_for10"),
    ("corners_against", "corners_against10"),
    ("xg_for", "xg_for10"),
    ("xg_against", "xg_against10"),
)

_REST_DAYS_CAP = 30.0


@dataclass
class _TeamState:
    overall: deque[dict[str, float]]
    at_home: deque[dict[str, float]]
    at_away: deque[dict[str, float]]
    last_date: date | None = None
    elo: float = 1500.0


@dataclass
class FeatureState:
    """Snapshot of all team states after consuming a set of matches."""

    teams: dict[str, _TeamState] = field(default_factory=dict)


def _mean(records: deque[dict[str, float]], key: str) -> float:
    values = [r[key] for r in records if not np.isnan(r[key])]
    return float(np.mean(values)) if values else float("nan")


def _num(row: dict[str, Any], column: str) -> float:
    value = row.get(column)
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


class FeatureBuilder:
    def __init__(self, *, overall_window: int = 10, venue_window: int = 5,
                 elo_k: float = 20.0, elo_home_advantage: float = 60.0) -> None:
        self.overall_window = overall_window
        self.venue_window = venue_window
        self.elo_k = elo_k
        self.elo_home_advantage = elo_home_advantage

    # ------------------------------------------------------------ internals

    def _state_for(self, state: FeatureState, team: str) -> _TeamState:
        if team not in state.teams:
            state.teams[team] = _TeamState(
                overall=deque(maxlen=self.overall_window),
                at_home=deque(maxlen=self.venue_window),
                at_away=deque(maxlen=self.venue_window),
            )
        return state.teams[team]

    def _side_features(self, ts: _TeamState, venue: str, as_of: date) -> dict[str, float]:
        features = {stat: _mean(ts.overall, key) for key, stat in _OVERALL_KEYS}
        venue_records = ts.at_home if venue == "home" else ts.at_away
        features["venue_ppg5"] = _mean(venue_records, "points")
        features["venue_gf5"] = _mean(venue_records, "gf")
        features["venue_ga5"] = _mean(venue_records, "ga")
        features["rest_days"] = (
            float(min((as_of - ts.last_date).days, _REST_DAYS_CAP))
            if ts.last_date is not None
            else float("nan")
        )
        features["elo"] = ts.elo
        return features

    def _emit(
        self, state: FeatureState, home_team: str, away_team: str, as_of: date
    ) -> dict[str, float]:
        home_state = self._state_for(state, home_team)
        away_state = self._state_for(state, away_team)
        row: dict[str, float] = {}
        for name, value in self._side_features(home_state, "home", as_of).items():
            row[f"h_{name}"] = value
        for name, value in self._side_features(away_state, "away", as_of).items():
            row[f"a_{name}"] = value
        row["elo_diff"] = home_state.elo - away_state.elo
        return row

    def _update(self, state: FeatureState, row: dict[str, Any]) -> None:
        home = self._state_for(state, str(row["home_team"]))
        away = self._state_for(state, str(row["away_team"]))
        hg, ag = int(row["ft_home"]), int(row["ft_away"])
        match_date: date = row["date"]

        home_points = 3.0 if hg > ag else (1.0 if hg == ag else 0.0)
        away_points = 3.0 - home_points if home_points != 1.0 else 1.0

        home_record = {
            "points": home_points, "gf": float(hg), "ga": float(ag),
            "shots_for": _num(row, "shots_home"), "shots_against": _num(row, "shots_away"),
            "sot_for": _num(row, "shots_on_target_home"),
            "sot_against": _num(row, "shots_on_target_away"),
            "corners_for": _num(row, "corners_home"),
            "corners_against": _num(row, "corners_away"),
            "xg_for": _num(row, "xg_home"), "xg_against": _num(row, "xg_away"),
        }
        away_record = {
            "points": away_points, "gf": float(ag), "ga": float(hg),
            "shots_for": _num(row, "shots_away"), "shots_against": _num(row, "shots_home"),
            "sot_for": _num(row, "shots_on_target_away"),
            "sot_against": _num(row, "shots_on_target_home"),
            "corners_for": _num(row, "corners_away"),
            "corners_against": _num(row, "corners_home"),
            "xg_for": _num(row, "xg_away"), "xg_against": _num(row, "xg_home"),
        }
        home.overall.append(home_record)
        home.at_home.append(home_record)
        away.overall.append(away_record)
        away.at_away.append(away_record)
        home.last_date = match_date
        away.last_date = match_date
        home.elo, away.elo = elo_update(
            home.elo, away.elo, home_goals=hg, away_goals=ag,
            k=self.elo_k, home_advantage=self.elo_home_advantage,
        )

    @staticmethod
    def _normalize(matches: pd.DataFrame) -> list[dict[str, Any]]:
        validate_matches(matches)
        frame = matches.copy()
        frame["date"] = pd.to_datetime(frame["date"]).dt.date
        frame = frame.sort_values("date", kind="stable")
        if "league" not in frame.columns:
            frame["league"] = ""
        return frame.to_dict("records")

    # ------------------------------------------------------------------ api

    def build_training_frame(
        self, matches: pd.DataFrame
    ) -> tuple[pd.DataFrame, "np.typing.NDArray[np.int64]"]:
        """One row of pre-match features per match, plus outcome targets
        (0 home / 1 draw / 2 away)."""
        rows = self._normalize(matches)
        state = FeatureState()
        feature_rows: list[dict[str, Any]] = []
        outcomes: list[int] = []
        for row in rows:
            features = self._emit(state, str(row["home_team"]), str(row["away_team"]),
                                  row["date"])
            features.update(
                {
                    "date": row["date"],
                    "league": str(row["league"]),
                    "home_team": str(row["home_team"]),
                    "away_team": str(row["away_team"]),
                }
            )
            feature_rows.append(features)
            hg, ag = int(row["ft_home"]), int(row["ft_away"])
            outcomes.append(0 if hg > ag else (1 if hg == ag else 2))
            self._update(state, row)
        X = pd.DataFrame(feature_rows, columns=META_COLUMNS + FEATURE_COLUMNS)
        return X, np.array(outcomes, dtype=np.int64)

    def snapshot(self, matches: pd.DataFrame) -> FeatureState:
        """Consume all matches; return the resulting state for prediction."""
        state = FeatureState()
        for row in self._normalize(matches):
            self._update(state, row)
        return state

    def features_for(
        self,
        state: FeatureState,
        *,
        home_team: str,
        away_team: str,
        league: str,
        as_of: date,
    ) -> pd.DataFrame:
        """Single prediction-time feature row for an upcoming fixture."""
        features: dict[str, Any] = self._emit(state, home_team, away_team, as_of)
        features.update(
            {"date": as_of, "league": league, "home_team": home_team, "away_team": away_team}
        )
        return pd.DataFrame([features], columns=META_COLUMNS + FEATURE_COLUMNS)
