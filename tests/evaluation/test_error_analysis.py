"""Error-analysis tests: BH step-up hand-computed (textbook example),
segment derivation, and the aggregation contract."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from pitchprob.evaluation.error_analysis import (
    analyze_segments,
    benjamini_hochberg,
    derive_segments,
)


class TestBenjaminiHochberg:
    def test_textbook_step_up(self) -> None:
        # Classic example: m=8, q=0.05 — only the two smallest survive.
        p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.060, 0.074, 0.205])
        rejected = benjamini_hochberg(p, q=0.05)
        assert rejected.tolist() == [True, True, False, False, False, False, False, False]

    def test_order_independence(self) -> None:
        p = np.array([0.205, 0.001, 0.060, 0.008])
        rejected = benjamini_hochberg(p, q=0.05)
        assert rejected.tolist() == [False, True, False, True]

    def test_all_null_rejects_nothing(self) -> None:
        assert not benjamini_hochberg(np.array([0.5, 0.8, 0.9]), q=0.05).any()

    def test_empty_input(self) -> None:
        assert benjamini_hochberg(np.array([]), q=0.05).tolist() == []


class TestDeriveSegments:
    def test_price_band_and_season(self) -> None:
        log = pd.DataFrame(
            [
                {"date": date(2021, 8, 15), "price": 1.4, "pnl": 0.4, "stake": 1.0,
                 "clv": 0.01},
                {"date": date(2022, 5, 1), "price": 6.5, "pnl": -1.0, "stake": 1.0,
                 "clv": -0.02},
            ]
        )
        out = derive_segments(log)
        assert out["season"].tolist() == ["2021/22", "2021/22"]
        assert out["price_band"].tolist() == ["1.00-1.50", "5.00-8.00"]

    def test_original_columns_preserved(self) -> None:
        log = pd.DataFrame(
            [{"date": date(2023, 1, 2), "price": 2.0, "pnl": 1.0, "stake": 1.0,
              "clv": 0.0, "market": "ah"}]
        )
        out = derive_segments(log)
        assert out["market"].tolist() == ["ah"]


class TestAnalyzeSegments:
    def _log(self) -> pd.DataFrame:
        # deterministic: 10 weeks, two markets; 'ou' always loses, '1x2' flat
        rows = []
        for week in range(10):
            for market, pnl in (("1x2", 0.1 if week % 2 == 0 else -0.1), ("ou", -0.5)):
                rows.append(
                    {
                        "date": date(2023, 8, 7) + pd.Timedelta(weeks=week).to_pytimedelta(),
                        "price": 2.0,
                        "pnl": pnl,
                        "stake": 1.0,
                        "clv": 0.001 if market == "1x2" else -0.05,
                        "market": market,
                    }
                )
        return pd.DataFrame(rows)

    def test_reports_each_segment_with_flags(self) -> None:
        table = analyze_segments(
            self._log(), group_by=["market"], n_boot=500, min_n=5, seed=1
        )
        assert set(table["segment"]) == {"1x2", "ou"}
        ou = table[table["segment"] == "ou"].iloc[0]
        assert ou["n"] == 10
        assert ou["roi"] == pytest.approx(-0.5)
        assert ou["mean_clv"] == pytest.approx(-0.05)
        # A constant -5% CLV across 10 weeks is as significant as the
        # bootstrap can certify; BH must keep it.
        assert bool(ou["clv_significant"])

    def test_min_n_filters_thin_segments(self) -> None:
        log = self._log().iloc[:3]
        table = analyze_segments(log, group_by=["market"], n_boot=200, min_n=5, seed=1)
        assert len(table) == 0
