"""Movement-study runner (Phase 1): plumbing checks on the synthetic DB.

The geometric primitives are hand-computed in tests/evaluation/test_movement;
here we verify the study joins the right frames, reports coherent
aggregates, and renders the bucket table.
"""

from datetime import date

import numpy as np
import pytest

from pitchprob.betting.odds_math import remove_overround_shin
from pitchprob.services.movement_study import run_movement_study

from .test_harness import seeded_session  # noqa: F401  (pytest fixture)


@pytest.fixture()
def study(seeded_session):  # noqa: F811
    return run_movement_study(
        seeded_session,
        league="E0",
        start=date(2023, 9, 1),
        model="poisson",
        refit_days=7,
        min_train_matches=10,
        n_buckets=2,
    )


class TestMovementStudy:
    def test_covers_joined_predictions(self, study) -> None:
        metrics = study.metrics
        assert metrics["n"] > 0
        buckets = metrics["toward_model_buckets"]
        assert sum(row["n"] for row in buckets) == metrics["n"]

    def test_probabilities_are_probabilities(self, study) -> None:
        metrics = study.metrics
        assert 0.0 <= metrics["p_toward_truth"] <= 1.0
        assert 0.0 <= metrics["p_toward_model"] <= 1.0
        assert metrics["mean_movement_l1"] > 0.0

    def test_truth_agreement_matches_primitive_recomputation(self, study) -> None:
        # The synthetic book moves identically every match (open 2.60/3.40/2.90
        # → close 2.50/3.30/3.10), so P(moved toward truth) is determined by
        # the outcome cycle alone and can be recomputed from the primitives.
        open_fair = np.array(remove_overround_shin([2.60, 3.40, 2.90]))
        close_fair = np.array(remove_overround_shin([2.50, 3.30, 3.10]))
        movement = close_fair - open_fair
        outcomes = study.metrics["outcome_counts"]
        expected = 0.0
        total = sum(outcomes.values())
        for index, key in enumerate(("home", "draw", "away")):
            onehot = np.zeros(3)
            onehot[index] = 1.0
            if float(movement @ (onehot - open_fair)) > 0:
                expected += outcomes[key] / total
        assert study.metrics["p_toward_truth"] == pytest.approx(expected)

    def test_report_renders_bucket_table(self, study) -> None:
        assert "| bucket |" in study.report
        assert "toward model" in study.report.lower()
        assert "mean CLV-like drift" not in study.report  # no invented metrics
