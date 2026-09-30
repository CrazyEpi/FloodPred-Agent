"""Focused D6 route and exact-metric checks; no LLM/network required."""

from __future__ import annotations

from pathlib import Path
import unittest

from app.read_tools import EvaluationArgs, ForecastArgs, evaluation_metrics_tool
from app.router import UnsupportedRoute, plan_route, run_query


AS_OF = "2026-08-03T01:48:00Z"


class RouterTests(unittest.TestCase):
    def test_three_query_classes_and_mixed_route(self) -> None:
        self.assertEqual(plan_route("当时预测峰值是多少").steps, ("archived_forecast",))
        self.assertEqual(plan_route("项目内部 Watch 是什么").steps, ("knowledge",))
        self.assertEqual(plan_route("MAE 是多少").steps, ("evaluation_metrics",))
        self.assertEqual(
            plan_route("当时预测峰值、历史水位、MAE 和项目内部 Watch 是什么").steps,
            ("archived_forecast", "historical_water", "evaluation_metrics", "knowledge"),
        )

    def test_real_mixed_answer_uses_each_expected_source(self) -> None:
        result = run_query("当时预测峰值、历史水位、MAE 和项目内部 Watch 是什么", as_of_utc=AS_OF)
        self.assertFalse(result.errors)
        self.assertAlmostEqual(result.forecast.predicted_peak_m, 3.7832)
        self.assertAlmostEqual(result.water.water_m, 1.14)
        self.assertAlmostEqual(result.evaluation.mae_m, 0.212531)
        self.assertEqual(result.evaluation.unit, "m")
        self.assertEqual(result.evaluation.matched_prediction_points, 76091)
        self.assertTrue("README.md:13-20" in result.knowledge[0].locator or "internal_watch.md:1-8" in result.knowledge[0].locator)

    def test_mae_is_exact_snapshot_with_version_scope_and_unit(self) -> None:
        overall = evaluation_metrics_tool(EvaluationArgs(scope="overall"))
        high = evaluation_metrics_tool(EvaluationArgs(scope="high_water_2m"))
        self.assertEqual((overall.mae_m, overall.generated_utc, overall.unit),
                         (0.212531, "2026-08-10T02:02:33.544996Z", "m"))
        self.assertEqual((high.mae_m, high.matched_prediction_points), (0.204788, 22658))
        self.assertIn("≥2.00 m", high.sample_scope)
        with self.assertRaises(FileNotFoundError):
            evaluation_metrics_tool(EvaluationArgs(), overall_path=Path("nonexistent-summary.json"))

    def test_wrong_or_missing_parameters_fail_without_guessing(self) -> None:
        with self.assertRaises(UnsupportedRoute):
            plan_route("明天适合去野餐吗")
        with self.assertRaises(ValueError):
            ForecastArgs(as_of_utc="2026-08-03T01:48:00")
        result = run_query("当时预测峰值是多少", as_of_utc=None)
        self.assertIsNone(result.forecast)
        self.assertTrue(result.errors)


if __name__ == "__main__":
    unittest.main()
