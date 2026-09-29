"""D6: explicit routing; numbers from tools, definitions from documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Literal

from .archive import DEFAULT_DB
from .knowledge import DEFAULT_CATALOG, ROOT, NoEvidence, search
from .rag import ChatClient, RAGError, answer_knowledge
from .read_tools import (
    EvaluationArgs,
    EvaluationMetric,
    ForecastArgs,
    WaterArgs,
    archived_forecast_tool,
    evaluation_metrics_tool,
    historical_water_tool,
)
from .replay import DEFAULT_SONAR_DB, Observation, ReplayCard


RouteKind = Literal["archived_forecast", "historical_water", "evaluation_metrics", "knowledge"]


class UnsupportedRoute(ValueError):
    pass


@dataclass(frozen=True)
class RoutePlan:
    steps: tuple[RouteKind, ...]
    knowledge_concepts: tuple[str, ...]
    evaluation_scope: Literal["overall", "high_water_2m"] | None


@dataclass(frozen=True)
class KnowledgeItem:
    title: str
    fact: str
    source_type: str
    citation_id: str
    locator: str
    explanation: str | None = None


@dataclass
class QueryResult:
    question: str
    as_of_utc: str | None
    plan: RoutePlan
    forecast: ReplayCard | None = None
    water: Observation | None = None
    evaluation: EvaluationMetric | None = None
    knowledge: list[KnowledgeItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    trace: list[dict[str, str]] = field(default_factory=list)


def plan_route(question: str) -> RoutePlan:
    q = re.sub(r"\s+", "", question.casefold())
    if not q:
        raise UnsupportedRoute("请输入问题。")
    forecast = any(word in q for word in ("预测峰值", "预测最高", "当时预测", "run_id", "风险卡", "内部等级"))
    water = any(word in q for word in ("历史水位", "当时水位", "实测水位", "观测水位", "声纳水位"))
    metrics = any(word in q for word in ("mae", "平均绝对误差", "评估指标"))
    concepts = []
    if "watch" in q and any(word in q for word in ("内部", "项目", "定义", "是什么", "含义")):
        concepts.append("internal_watch")
    if ("模型" in q and any(word in q for word in ("局限", "限制"))) or any(
        word in q for word in ("召回率", "洪水检出")
    ):
        concepts.append("highwater_evaluation_limit")
    if "floodalert" in q and any(word in q for word in ("官方", "是什么", "定义", "含义")):
        concepts.append("official_flood_alert")
    if not any((forecast, water, metrics, concepts)):
        raise UnsupportedRoute("目前仅支持归档预测、历史水位、MAE、内部 Watch、模型局限和官方 Flood Alert 定义。")
    steps: list[RouteKind] = []
    if forecast:
        steps.append("archived_forecast")
    if water:
        steps.append("historical_water")
    if metrics:
        steps.append("evaluation_metrics")
    if concepts:
        steps.append("knowledge")
    high_water = any(word in q for word in ("2m以上", "2米以上", "2m及以上", "2米及以上", "高水位"))
    return RoutePlan(
        steps=tuple(steps),
        knowledge_concepts=tuple(concepts),
        evaluation_scope=("high_water_2m" if high_water else "overall") if metrics else None,
    )


_QUERIES = {
    "internal_watch": ("项目内部Watch", "internal_project", "项目内部 Watch 是什么"),
    "highwater_evaluation_limit": ("模型限制", "evaluation_report", "模型局限是什么"),
    "official_flood_alert": ("官方Flood Alert", "official_public_guidance", None),
}


def run_query(
    question: str,
    *,
    as_of_utc: str | None = None,
    use_llm: bool = False,
    client: ChatClient | None = None,
    forecast_db: Path = DEFAULT_DB,
    sonar_db: Path = DEFAULT_SONAR_DB,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
) -> QueryResult:
    plan = plan_route(question)
    result = QueryResult(question=question, as_of_utc=as_of_utc, plan=plan)
    for step in plan.steps:
        try:
            if step == "archived_forecast":
                result.forecast = archived_forecast_tool(ForecastArgs(as_of_utc=as_of_utc), forecast_db)
            elif step == "historical_water":
                result.water = historical_water_tool(WaterArgs(as_of_utc=as_of_utc), sonar_db)
            elif step == "evaluation_metrics":
                result.evaluation = evaluation_metrics_tool(EvaluationArgs(scope=plan.evaluation_scope))
            else:
                for concept in plan.knowledge_concepts:
                    query, source_type, canonical_question = _QUERIES[concept]
                    relevant = [
                        hit for hit in search(query, source_type=source_type, catalog=catalog, root=root)
                        if hit.record["concept"] == concept
                    ]
                    if len(relevant) != 1:
                        raise NoEvidence(f"{concept} 没有唯一、已核验的知识记录。")
                    hit = relevant[0]
                    explanation = None
                    if use_llm and canonical_question:
                        try:
                            generated = answer_knowledge(
                                canonical_question, client=client, catalog=catalog, root=root
                            )
                            explanation = generated.explanation
                        except RAGError as exc:
                            result.errors.append(f"DeepSeek 解释未通过：{exc}；以下只展示已核验的文档事实。")
                    result.knowledge.append(KnowledgeItem(
                        title=hit.record["title"],
                        fact=hit.record["summary"],
                        source_type=hit.record["source_type"],
                        citation_id=hit.record["id"],
                        locator=hit.locator,
                        explanation=explanation,
                    ))
            result.trace.append({"step": step, "status": "ok"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.errors.append(f"{step}：{exc}")
            result.trace.append({"step": step, "status": "error"})
        except Exception as exc:
            # Archive/knowledge errors are custom Exception subclasses.
            result.errors.append(f"{step}：{type(exc).__name__}: {exc}")
            result.trace.append({"step": step, "status": "error"})
    return result
