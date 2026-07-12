"""Walk-forward backtester tests.

The central guarantee — no lookahead — is verified with a recording model
that logs the maximum training date it saw for every prediction it makes.
"""

from datetime import date, timedelta

import pandas as pd

from pitchprob.evaluation.backtest import run_backtest
from pitchprob.models.base import OutcomeProbabilities


def league_frame(n_days: int = 100, start: date = date(2023, 8, 1)) -> pd.DataFrame:
    """One match per day between rotating teams; deterministic scores."""
    teams = ["A", "B", "C", "D"]
    rows = []
    for i in range(n_days):
        home = teams[i % 4]
        away = teams[(i + 1) % 4]
        rows.append(
            {
                "date": start + timedelta(days=i),
                "home_team": home,
                "away_team": away,
                "ft_home": i % 3,  # cycles 0,1,2 -> outcomes cycle too
                "ft_away": 1,
            }
        )
    return pd.DataFrame(rows)


class RecordingModel:
    """Uniform-probability model that records what it trained on."""

    def __init__(self, log: list[tuple[date, date]]) -> None:
        self.log = log
        self.max_train_date: date | None = None

    def fit(self, matches: pd.DataFrame) -> "RecordingModel":
        self.max_train_date = pd.to_datetime(matches["date"]).max().date()
        self.n_train = len(matches)
        return self

    def match_probabilities(self, home: str, away: str) -> OutcomeProbabilities:
        raise AssertionError("backtester should use predict hook")

    def predict_recorded(self, match_date: date) -> OutcomeProbabilities:
        assert self.max_train_date is not None
        self.log.append((self.max_train_date, match_date))
        return OutcomeProbabilities(home=1 / 3, draw=1 / 3, away=1 / 3)


class TestNoLookahead:
    def test_every_prediction_trained_strictly_on_the_past(self) -> None:
        log: list[tuple[date, date]] = []
        models: list[RecordingModel] = []

        def factory() -> RecordingModel:
            m = RecordingModel(log)
            models.append(m)
            return m

        matches = league_frame()
        result = run_backtest(
            matches,
            model_factory=factory,
            predict=lambda model, row: model.predict_recorded(row.date),
            start=date(2023, 9, 1),
            refit_every_days=7,
            min_train_matches=10,
        )
        assert len(result) > 0
        assert len(log) == len(result)
        for max_train, predicted in log:
            assert max_train < predicted


class TestBacktestMechanics:
    @staticmethod
    def _uniform_predict(model: object, row: object) -> OutcomeProbabilities:
        return OutcomeProbabilities(home=1 / 3, draw=1 / 3, away=1 / 3)

    def _run(self, **kwargs) -> pd.DataFrame:
        from pitchprob.models.base import OutcomeProbabilities as OP

        class Dummy:
            def fit(self, df: pd.DataFrame) -> "Dummy":
                return self

        defaults = {
            "model_factory": Dummy,
            "predict": lambda m, row: OP(1 / 3, 1 / 3, 1 / 3),
            "start": date(2023, 9, 1),
            "refit_every_days": 7,
            "min_train_matches": 5,
        }
        defaults.update(kwargs)
        return run_backtest(league_frame(), **defaults)

    def test_outcome_encoding(self) -> None:
        result = self._run()
        # ft_home cycles 0,1,2 vs ft_away=1: outcomes cycle away(2), draw(1), home(0)
        first = result.iloc[0]
        expected = {0: 2, 1: 1, 2: 0}[int(first["ft_home"])]
        assert int(first["outcome"]) == expected

    def test_probability_columns_present_and_normalized(self) -> None:
        result = self._run()
        sums = result[["p_home", "p_draw", "p_away"]].sum(axis=1)
        assert ((sums - 1.0).abs() < 1e-9).all()

    def test_no_predictions_before_start(self) -> None:
        result = self._run(start=date(2023, 10, 1))
        assert pd.to_datetime(result["date"]).min().date() >= date(2023, 10, 1)

    def test_min_train_matches_gate(self) -> None:
        """With start on day one there is no training data, so early windows
        must produce no predictions."""
        result = self._run(start=date(2023, 8, 1), min_train_matches=20)
        assert pd.to_datetime(result["date"]).min().date() >= date(2023, 8, 21)
