"""Walk-forward backtesting engine.

The only evaluation regime this project supports (ADR 0004): train strictly
on matches dated before the prediction window, predict the window, roll
forward. Refits happen every ``refit_every_days``; windows with insufficient
training history produce no predictions rather than degraded ones.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol, cast

import pandas as pd

from pitchprob.markets import match_odds
from pitchprob.models.base import OutcomeProbabilities, validate_matches


class _Fittable(Protocol):
    def fit(self, matches: pd.DataFrame) -> Any: ...


#: (fitted model, prediction row) -> 1X2 probabilities. The row is a pandas
#: itertuple with at least .date, .home_team, .away_team.
PredictFn = Callable[[Any, Any], OutcomeProbabilities]


def outcome_index(ft_home: int, ft_away: int) -> int:
    """0 = home win, 1 = draw, 2 = away win."""
    if ft_home > ft_away:
        return 0
    if ft_home == ft_away:
        return 1
    return 2


def default_predict(model: Any, row: Any) -> OutcomeProbabilities:
    """Route through the score matrix for goal models, or use the model's own
    1X2 output for outcome-space models."""
    if hasattr(model, "score_matrix"):
        mo = match_odds(model.score_matrix(row.home_team, row.away_team))
        return OutcomeProbabilities(home=mo.home, draw=mo.draw, away=mo.away)
    probs: OutcomeProbabilities = model.match_probabilities(row.home_team, row.away_team)
    return probs


@dataclass(frozen=True, slots=True)
class _Window:
    start: date
    end: date  # exclusive


def run_backtest(
    matches: pd.DataFrame,
    *,
    model_factory: Callable[[], _Fittable],
    start: date,
    refit_every_days: int = 7,
    min_train_matches: int = 380,
    predict: PredictFn | None = None,
) -> pd.DataFrame:
    """Return one row per out-of-sample prediction with columns
    ``date, home_team, away_team, ft_home, ft_away, p_home, p_draw, p_away,
    outcome``."""
    validate_matches(matches)
    predict_fn = predict if predict is not None else default_predict

    frame = matches.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.date
    frame = frame.sort_values("date", kind="stable").reset_index(drop=True)
    last_date = frame["date"].max()

    records: list[dict[str, Any]] = []
    window_start = start
    while window_start <= last_date:
        window = _Window(window_start, window_start + timedelta(days=refit_every_days))
        train = frame[frame["date"] < window.start]
        test = frame[(frame["date"] >= window.start) & (frame["date"] < window.end)]
        if len(test) > 0 and len(train) >= min_train_matches:
            model = model_factory()
            model.fit(train)
            for raw_row in test.itertuples(index=False):
                row = cast(Any, raw_row)  # pandas named tuples are untyped
                probs = predict_fn(model, row)
                ft_home, ft_away = int(row.ft_home), int(row.ft_away)
                records.append(
                    {
                        "date": row.date,
                        "home_team": row.home_team,
                        "away_team": row.away_team,
                        "ft_home": ft_home,
                        "ft_away": ft_away,
                        "p_home": probs.home,
                        "p_draw": probs.draw,
                        "p_away": probs.away,
                        "outcome": outcome_index(ft_home, ft_away),
                    }
                )
        window_start = window.end

    columns = [
        "date", "home_team", "away_team", "ft_home", "ft_away",
        "p_home", "p_draw", "p_away", "outcome",
    ]
    return pd.DataFrame(records, columns=columns)
