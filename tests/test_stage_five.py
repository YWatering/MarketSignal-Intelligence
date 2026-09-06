from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from forecasting import ForecastError, run_forecast_analysis


def _prices(count: int = 190) -> list[dict[str, object]]:
    start = datetime(2025, 1, 2)
    close = 100.0
    rows: list[dict[str, object]] = []
    for index in range(count):
        current = start + timedelta(days=index)
        close *= 1.0 + (0.003 if index % 7 == 0 else -0.001 if index % 11 == 0 else 0.0005)
        rows.append(
            {
                "date": current.strftime("%Y-%m-%d"),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000000 + index * 1000,
                "source": "AKShare online test",
                "market": "cn_a",
                "currency": "CNY",
            }
        )
    return rows


def _predictable_prices(count: int = 260) -> list[dict[str, object]]:
    start = datetime(2024, 1, 2)
    close = 100.0
    previous_return = 0.0
    rows: list[dict[str, object]] = []
    for index in range(count):
        current_return = 0.003 * (1 if index % 2 == 0 else -1) + 0.55 * previous_return
        close *= 1.0 + current_return
        previous_return = current_return
        current = start + timedelta(days=index)
        rows.append(
            {
                "date": current.strftime("%Y-%m-%d"),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1000000 + index * 1000,
                "source": "AKShare online test",
                "market": "cn_a",
                "currency": "CNY",
            }
        )
    return rows


class StageFiveForecastTests(unittest.TestCase):
    def test_selection_evaluates_candidates_robustness_and_costs(self) -> None:
        result = run_forecast_analysis(
            _prices(),
            [],
            [],
            [],
            minimum_history=120,
            validation_points=30,
            robustness_windows=(20, 40),
            market="cn_a",
            currency="CNY",
        )
        self.assertEqual(
            {
                "last_close_baseline",
                "moving_average_baseline",
                "historical_mean_baseline",
                "exponential_smoothing_baseline",
                "multisignal_ridge",
            },
            set(result["model_names"]),
        )
        self.assertEqual(1, sum(row["selected"] for row in result["evaluations"]))
        self.assertTrue(all("selection_reason" in row for row in result["evaluations"]))
        self.assertTrue(all(row["robustness_status"] == "measured" for row in result["robustness"]))
        self.assertEqual(
            {"no_cost", "slippage_only", "base_cost"},
            {row["scenario"] for row in result["cost_evaluations"]},
        )
        self.assertTrue(
            all(
                row["cumulative_net_return_pct"] <= row["cumulative_gross_return_pct"] + 1e-9
                for row in result["cost_evaluations"]
            )
        )

    def test_selection_rejects_invalid_stage_five_settings(self) -> None:
        with self.assertRaisesRegex(ForecastError, "weights"):
            run_forecast_analysis(
                _prices(), [], [], [], price_weight=0, direction_weight=0, market="cn_a", currency="CNY"
            )
        with self.assertRaisesRegex(ForecastError, "windows"):
            run_forecast_analysis(
                _prices(), [], [], [], robustness_windows=(9,), market="cn_a", currency="CNY"
            )
        with self.assertRaisesRegex(ForecastError, "cannot be negative"):
            run_forecast_analysis(
                _prices(), [], [], [], slippage_bps=-1, market="cn_a", currency="CNY"
            )

    def test_ridge_can_win_when_real_signal_is_predictable(self) -> None:
        result = run_forecast_analysis(
            _predictable_prices(),
            [],
            [],
            [],
            minimum_history=120,
            validation_points=60,
            robustness_windows=(20, 40, 80),
            market="cn_a",
            currency="CNY",
        )
        self.assertEqual("multisignal_ridge", result["selected_model"])
        selected = next(row for row in result["evaluations"] if row["selected"])
        self.assertGreaterEqual(selected["price_rmse_improvement_pct"], 2.0)
        self.assertGreaterEqual(selected["direction_improvement_points"], 5.0)


if __name__ == "__main__":
    unittest.main()
