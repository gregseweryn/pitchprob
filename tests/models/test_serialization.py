"""Model parameter round-trips: fitted state must survive JSON persistence."""

import json

import numpy as np
import pytest

from pitchprob.models.dixon_coles import DixonColesModel
from pitchprob.models.elo import EloModel

from .test_dixon_coles import synthetic_matches
from .test_elo import synthetic_league


class TestDixonColesRoundTrip:
    def test_params_survive_json(self) -> None:
        model = DixonColesModel(half_life_days=None)
        model.fit(synthetic_matches(n_rounds=6))
        payload = json.loads(json.dumps(model.get_params()))
        restored = DixonColesModel.from_params(payload)

        assert restored.params.home_adv == pytest.approx(model.params.home_adv)
        assert restored.params.attack == pytest.approx(model.params.attack)
        m1 = model.score_matrix("A", "B")
        m2 = restored.score_matrix("A", "B")
        assert m1 == pytest.approx(m2)

    def test_config_survives(self) -> None:
        model = DixonColesModel(half_life_days=200.0, l2=0.01, max_goals=8)
        model.fit(synthetic_matches(n_rounds=4))
        restored = DixonColesModel.from_params(model.get_params())
        assert restored.half_life_days == 200.0
        assert restored.l2 == 0.01
        assert restored.max_goals == 8
        assert restored.score_matrix("A", "B").shape == (9, 9)


class TestEloRoundTrip:
    def test_params_survive_json(self) -> None:
        model = EloModel()
        model.fit(synthetic_league(n_rounds=10))
        payload = json.loads(json.dumps(model.get_params()))
        restored = EloModel.from_params(payload)

        assert restored.rating("A") == pytest.approx(model.rating("A"))
        p1 = model.match_probabilities("A", "D")
        p2 = restored.match_probabilities("A", "D")
        assert np.array([p1.home, p1.draw, p1.away]) == pytest.approx(
            [p2.home, p2.draw, p2.away]
        )
