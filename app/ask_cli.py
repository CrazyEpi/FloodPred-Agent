"""D6 command-line smoke entry point for the combined router."""

from __future__ import annotations

import argparse
import sys

from .router import UnsupportedRoute, run_query


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FloodPred historical replay and evidence query")
    parser.add_argument("--question", required=True)
    parser.add_argument("--as-of", help="UTC replay cutoff; required for prediction or historical water")
    parser.add_argument("--llm", action="store_true", help="Ask DeepSeek for a short document explanation")
    args = parser.parse_args(argv)
    try:
        result = run_query(args.question, as_of_utc=args.as_of, use_llm=args.llm)
    except UnsupportedRoute as exc:
        print(f"无法路由：{exc}", file=sys.stderr)
        return 2
    print("路径：" + " → ".join(result.plan.steps))
    if result.forecast:
        card = result.forecast
        print(f"历史回放预测峰值：{card.predicted_peak_m:.4f} m；目标时间：{card.predicted_peak_utc}")
        print(f"run_id：{card.run_id}；内部等级：{card.internal_risk_label}")
    if result.water:
        item = result.water
        print(f"当时已可见水位：{item.water_m:.3f} m；观测：{item.observed_utc}；入库：{item.stored_at_utc}")
    if result.evaluation:
        metric = result.evaluation
        print(f"{metric.scope} MAE：{metric.mae_m:.6f} {metric.unit}；评估版本：{metric.generated_utc}")
        print(f"样本范围：{metric.sample_scope}；匹配预测目标点：{metric.matched_prediction_points}")
        print(f"评估来源：{metric.source_locator}（事后评估，不是当时可用事实）")
    for item in result.knowledge:
        print(f"定义／局限：{item.fact} [{item.citation_id}]")
        if item.explanation:
            print(f"模型解释：{item.explanation}")
        print(f"来源：{item.locator}")
    for error in result.errors:
        print(f"错误：{error}", file=sys.stderr)
    print("提示：历史回放，非实时；内部风险等级不是官方 Flood Alert。")
    return 2 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
