"""Experiment registry and paired comparison (ADR 0010).

Comparisons must be *paired*: both configurations predict the identical
fixtures, per-match metric deltas are block-bootstrapped by week, and an
identical-vs-identical comparison must come out as an exact null.
"""

from datetime import date

import pytest
from sqlalchemy import select

from pitchprob.data.orm import Backtest
from pitchprob.services.experiments import (
    apply_overrides,
    compare_experiments,
    config_hash,
    run_experiment,
)
from pitchprob.services.harness import HarnessConfig

from .test_harness import seeded_session  # noqa: F401  (pytest fixture)


def _config(**overrides: object) -> HarnessConfig:
    defaults: dict[str, object] = {
        "league": "E0",
        "start": date(2023, 9, 1),
        "model": "poisson",
        "refit_days": 7,
        "min_train_matches": 10,
        "ev_threshold": -1.0,
        "selector": "naive",
        "at": "open",
        "markets": ("1x2",),
    }
    defaults.update(overrides)
    return HarnessConfig(**defaults)  # type: ignore[arg-type]


class TestConfigHash:
    def test_stable_for_equal_configs(self) -> None:
        assert config_hash(_config()) == config_hash(_config())

    def test_changes_with_any_field(self) -> None:
        assert config_hash(_config()) != config_hash(_config(refit_days=14))
        assert config_hash(_config()) != config_hash(_config(markets=("1x2", "ou")))


class TestApplyOverrides:
    def test_typed_field_coercion(self) -> None:
        base = _config()
        out = apply_overrides(
            base,
            ["refit_days=14", "ablate=absences", "markets=1x2,ou", "start=2023-10-01"],
        )
        assert out.refit_days == 14
        assert out.ablate == "absences"
        assert out.markets == ("1x2", "ou")
        assert out.start == date(2023, 10, 1)
        # base untouched (frozen dataclass semantics)
        assert base.refit_days == 7

    def test_dashes_accepted_in_keys(self) -> None:
        out = apply_overrides(_config(), ["refit-days=28"])
        assert out.refit_days == 28

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError):
            apply_overrides(_config(), ["weather=rainy"])

    def test_malformed_override_raises(self) -> None:
        with pytest.raises(ValueError):
            apply_overrides(_config(), ["refit_days"])


class TestRunExperiment:
    def test_stores_named_hashed_row(self, seeded_session) -> None:  # noqa: F811
        run_id, metrics = run_experiment(
            seeded_session, _config(), name="baseline-open"
        )
        row = seeded_session.execute(
            select(Backtest).where(Backtest.id == run_id)
        ).scalar_one()
        assert row.config["experiment"]["name"] == "baseline-open"
        assert row.config["experiment"]["hash"] == config_hash(_config())
        assert row.metrics["n_predictions"] == metrics["n_predictions"] > 0


class TestCompareExperiments:
    def test_identical_configs_are_an_exact_null(self, seeded_session) -> None:  # noqa: F811
        result = compare_experiments(
            seeded_session, _config(), _config(), n_boot=300
        )
        delta_ll = result.metrics["delta_log_loss"]
        assert delta_ll["point"] == pytest.approx(0.0, abs=1e-12)
        assert delta_ll["p_value"] == pytest.approx(1.0)
        assert result.metrics["delta_roi"]["point"] == pytest.approx(0.0, abs=1e-12)
        assert "null" in result.report.lower()

    def test_delta_orientation_is_a_minus_b(self, seeded_session) -> None:  # noqa: F811
        result = compare_experiments(
            seeded_session, _config(), _config(model="elo"), n_boot=200
        )
        expected = (
            result.metrics["a"]["model"]["log_loss"]
            - result.metrics["b"]["model"]["log_loss"]
        )
        assert result.metrics["delta_log_loss"]["point"] == pytest.approx(
            expected, abs=1e-9
        )
        assert result.metrics["n_paired"] > 0

    def test_report_is_markdown_with_metric_rows(self, seeded_session) -> None:  # noqa: F811
        result = compare_experiments(
            seeded_session, _config(), _config(model="elo"), n_boot=200
        )
        assert "| metric |" in result.report
        assert "log-loss" in result.report
        assert "ROI" in result.report

    def test_sharp_clv_delta_reported(self, seeded_session) -> None:  # noqa: F811
        """Audit A5 / ADR 0011: the comparison must delta the timing-only
        sharp label alongside exec CLV — the meta-gate false positive came
        from reading an exec-only delta as edge."""
        result = compare_experiments(
            seeded_session, _config(), _config(model="elo"), n_boot=200
        )
        assert "delta_clv_sharp" in result.metrics
        assert "mean CLV (sharp)" in result.report
        assert "mean CLV (exec)" in result.report

    def test_sharp_clv_delta_nulls_on_identical_configs(self, seeded_session) -> None:  # noqa: F811
        result = compare_experiments(seeded_session, _config(), _config(), n_boot=200)
        assert result.metrics["delta_clv_sharp"]["point"] == pytest.approx(
            0.0, abs=1e-12
        )
