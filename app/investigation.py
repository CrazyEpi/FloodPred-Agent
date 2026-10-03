"""Bounded, read-only evidence investigation on top of the existing tools.

This is deliberately not an open-ended ReAct loop. A model may propose a
follow-up, but only the executor chooses allowlisted tools and enforces the
budget. All later observations remain separate from the replay time gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
from typing import Callable

from .archive import DEFAULT_DB
from .knowledge import DEFAULT_CATALOG, ROOT
from .rag import ChatClient, DeepSeekClient, IntentPlan, RAGError
from .question_graph import excluded_concepts, excluded_routes
from .replay import DEFAULT_SONAR_DB
from .router import (
    DEFAULT_AUDIT_LOG, QueryResult, RoutePlan, UnsupportedRoute, _QUERIES,
    _write_audit, run_query,
)


@dataclass(frozen=True)
class InvestigationLimits:
    max_rounds: int = 3
    max_tool_calls: int = 8
    max_llm_calls: int = 4
    max_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not 2 <= self.max_rounds <= 3 or not 1 <= self.max_tool_calls <= 12:
            raise ValueError("Investigation must use 2–3 rounds and 1–12 read-only tool calls")
        if not 2 <= self.max_llm_calls <= 6 or not 1 <= self.max_seconds <= 180:
            raise ValueError("Invalid LLM or time budget")


class BudgetExceeded(Exception):
    pass


class _Budget:
    def __init__(self, limits: InvestigationLimits, clock: Callable[[], float]):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.tool_calls = 0
        self.llm_calls = 0
        self.exceeded: str | None = None

    def remaining(self) -> float:
        return self.limits.max_seconds - (self.clock() - self.started)

    def check_time(self) -> None:
        if self.remaining() <= 0:
            self.exceeded = "time_limit"
            raise BudgetExceeded("调查已达到时间上限；停止补查，不用猜测填补缺口。")

    def reserve_tool(self, tool: str) -> None:
        self.check_time()
        if self.tool_calls >= self.limits.max_tool_calls:
            self.exceeded = "tool_limit"
            raise BudgetExceeded("调查已达到只读工具调用上限；停止补查。")
        self.tool_calls += 1

    def reserve_llm(self, provider: object) -> None:
        self.check_time()
        if self.llm_calls >= self.limits.max_llm_calls:
            self.exceeded = "llm_limit"
            raise BudgetExceeded("调查已达到 DeepSeek 调用上限；保留已核验的资料。")
        self.llm_calls += 1
        if isinstance(provider, DeepSeekClient):
            # A network call cannot outlive the remaining investigation budget.
            provider.request_timeout_seconds = min(20.0, max(1.0, self.remaining()))


def _relation_cues(question: str) -> bool:
    q = question.casefold()
    return any(cue in q for cue in ("区别", "关系", "联系", "比较", "为什么", "原因", "是否一样", "分别", "一起"))


def _relevant_concept(question: str, concept: str) -> bool:
    """A model cannot add a topic with no domain cue in the user's request."""
    q = question.casefold().replace(" ", "")
    cues = {
        "internal_watch": ("watch", "caution", "留意", "观察等级"),
        "internal_warning": ("warning", "警告等级", "洪水线"),
        "official_flood_alert": ("官方", "floodalert", "environmentagency"),
        "highwater_evaluation_limit": ("局限", "限制", "高水位", "召回", "检出"),
        "thesis_forecast_design": ("论文", "七天", "96个", "patchtst"),
        "thesis_risk_terms": ("论文", "阈值", "等级"),
        "thesis_offline_events": ("离线", "论文", "事件召回"),
        "thesis_live_limit": ("线上", "部署", "真实洪水", "检出"),
        "thesis_rapid_rise": ("快速上涨", "快涨", "突然上涨", "滞后"),
        "thesis_model_design": ("patchtst", "模型结构", "分类头"),
        "housemill_heritage": ("housemill", "三磨坊", "历史建筑"),
        "housemill_flood_context": ("housemill", "潮汐", "木梁"),
        "housemill_old_sensor": ("旧传感器", "旧声纳", "旧监测", "duncan", "水位仪"),
        "housemill_study_findings": ("136", "42", "53分钟", "旧研究"),
        "housemill_volunteer_need": ("志愿者", "旧界面", "触水时长"),
        "housemill_project_connection": ("floodpred", "旧项目", "旧系统", "承接"),
    }
    return concept in cues and any(cue in q for cue in cues[concept])


def _relevant_route(question: str, route: str) -> bool:
    q = question.casefold().replace(" ", "")
    cues = {
        "archived_forecast": ("预测峰值", "预测最高", "当时预测", "最高水位", "run_id"),
        "historical_water": ("历史水位", "当时水位", "实测水位", "观测水位", "声纳水位"),
        "evaluation_metrics": ("mae", "误差", "评估", "准不准", "模型表现"),
    }
    return route in cues and any(cue in q for cue in cues[route])


def _deterministic_gaps(question: str, result: QueryResult) -> tuple[str, ...]:
    """Only narrow, explainable recovery rules; no guessed numerical facts."""
    q = question.casefold()
    gaps: list[str] = []
    if ("官方" in q and any(cue in q for cue in ("内部", "caution", "watch", "warning"))
        and _relation_cues(question) and "official_flood_alert" not in result.plan.knowledge_concepts):
        gaps.append("official_flood_alert")
    if (any(cue in q for cue in ("旧传感器", "旧监测", "旧系统", "早期监测"))
        and any(cue in q for cue in ("floodpred", "本项目", "预测项目"))
        and _relation_cues(question)
        and "housemill_project_connection" not in result.plan.knowledge_concepts):
        gaps.append("housemill_project_connection")
    return tuple(gaps)


def _attempted(plan: RoutePlan) -> set[str]:
    return {step for step in plan.steps if step != "knowledge"} | {f"knowledge:{item}" for item in plan.knowledge_concepts}


def _merge(target: QueryResult, addition: QueryResult) -> None:
    if addition.forecast is not None:
        target.forecast = addition.forecast
    if addition.water is not None:
        target.water = addition.water
    if addition.evaluation is not None:
        target.evaluation = addition.evaluation
    known_citations = {item.citation_id for item in target.knowledge}
    target.knowledge.extend(item for item in addition.knowledge if item.citation_id not in known_citations)
    known_evidence = {item["id"] for item in target.evidence_bundle}
    target.evidence_bundle.extend(item for item in addition.evidence_bundle if item["id"] not in known_evidence)
    target.errors.extend(addition.errors)
    target.warnings.extend(addition.warnings)
    target.trace.extend(addition.trace)
    from .hybrid_retrieval import merge_candidates
    merge_candidates(target.document_candidates, addition.document_candidates)
    target.retrieval_runs.extend(addition.retrieval_runs)
    order = ("archived_forecast", "historical_water", "evaluation_metrics", "knowledge")
    steps = set(target.plan.steps) | set(addition.plan.steps)
    concepts = list(target.plan.knowledge_concepts)
    concepts.extend(item for item in addition.plan.knowledge_concepts if item not in concepts)
    target.plan = RoutePlan(tuple(step for step in order if step in steps), tuple(concepts),
                            target.plan.evaluation_scope or addition.plan.evaluation_scope)


def _evidence_gaps(result: QueryResult) -> list[str]:
    """Requested result coverage, not vector similarity or model confidence."""
    gaps = []
    if "archived_forecast" in result.plan.steps and result.forecast is None:
        gaps.append("归档预测")
    if "historical_water" in result.plan.steps and result.water is None:
        gaps.append("历史水位")
    if "evaluation_metrics" in result.plan.steps and result.evaluation is None:
        gaps.append("评估指标")
    if "knowledge" in result.plan.steps and len(result.knowledge) < len(result.plan.knowledge_concepts):
        gaps.append("文档依据")
    for concept in _deterministic_gaps(result.question, result):
        label = {"official_flood_alert": "英国官方定义",
                 "housemill_project_connection": "旧项目承接资料"}.get(concept)
        if label and label not in gaps:
            gaps.append(label)
    return gaps


def _resolve_incomplete_result(result: QueryResult) -> None:
    """Use verified residual facts first; abstain only when none can be used."""
    if not result.plan.steps:
        return
    reason = result.investigation_stop_reason
    budget_stop = reason in {"tool_limit", "time_limit", "round_limit", "llm_limit"}
    gaps = _evidence_gaps(result)
    if result.answer_text and not result.errors and not gaps:
        return
    if not budget_stop and not result.errors and not gaps:
        return
    if gaps and reason == "evidence_complete":
        reason = result.investigation_stop_reason = "partial_evidence"
    reason_text = {"tool_limit": "只读查询次数", "time_limit": "调查时间", "round_limit": "调查轮数",
                   "llm_limit": "DeepSeek 调用次数"}.get(reason)
    limitation = f"且已达到{reason_text}上限" if reason_text else "相关取证仍未完成"
    facts = [item for item in result.evidence_bundle if item.get("id") and item.get("fact")]
    if facts:
        # This is deliberately extractive. It never asks an LLM to complete a
        # missing relation after the budget has stopped the investigation.
        parts = [f"【{item['id']}】{item['fact']}" for item in facts]
        missing = "、".join(gaps) if gaps else "剩余核对步骤"
        result.answer_text = (
            "目前能确认的局部结论是：" + "；".join(parts) + "。"
            f"但{missing}仍未确认，{limitation}；"
            "因此不能据此得出整个问题的完整结论。这里不是实时水情或官方警报。"
        )
        result.answer_status = "partial_verified"
        result.fallback_text = None
        result.trace.append({"step": "partial_answer", "status": "verified_subset", "reason": reason,
                             "missing": ",".join(gaps), "citation_ids": ",".join(item["id"] for item in facts)})
        return
    missing = "、".join(gaps) if gaps else "可核验资料"
    result.fallback_text = (
        f"这次没有足够的已核验资料形成结论：{missing}未取得，{limitation}。"
        "请缩小问题或核对来源后再查；这里不是实时水情或官方警报。"
    )
    result.answer_text = None
    result.answer_status = "fallback"
    result.trace.append({"step": "safe_fallback", "status": "no_verified_evidence", "reason": reason,
                         "missing": ",".join(gaps)})


def _followup_plan(
    question: str, result: QueryResult, attempted: set[str], provider: object | None,
    budget: _Budget, use_llm: bool,
) -> RoutePlan | None:
    proposals = list(_deterministic_gaps(question, result))
    proposed_steps: tuple[str, ...] = ()
    proposed_scope: str | None = None
    reviewer = getattr(provider, "review", None)
    if (use_llm and callable(reviewer) and _relation_cues(question)
        and budget.llm_calls < budget.limits.max_llm_calls - 1):
        try:
            budget.reserve_llm(provider)
            review: IntentPlan = reviewer(
                question, result.evidence_bundle, sorted(attempted), tuple(_QUERIES),
            )
            if review.mode == "project":
                proposals.extend(review.concepts)
                proposed_steps = review.steps
                proposed_scope = review.evaluation_scope
            result.trace.append({"step": "gap_review", "status": "ok", "proposed_concepts": ",".join(review.concepts), "proposed_tools": ",".join(review.steps)})
        except (RAGError, BudgetExceeded, ValueError, TypeError) as exc:
            if isinstance(exc, BudgetExceeded):
                result.errors.append(str(exc))
                result.trace.append({"step": "gap_review", "status": "budget_stop", "error_type": type(exc).__name__})
                return None
            result.warnings.append(f"缺口检查未完成：{exc}；仍使用本地规则。")
            result.trace.append({"step": "gap_review", "status": "failed", "error_type": type(exc).__name__})
    suppressed_topics = excluded_concepts(result.question_graph) if result.question_graph else set()
    suppressed_routes = excluded_routes(result.question_graph) if result.question_graph else set()
    concepts = [item for item in dict.fromkeys(proposals)
                if item in _QUERIES and item not in suppressed_topics
                and _relevant_concept(question, item) and f"knowledge:{item}" not in attempted]
    routes = [item for item in dict.fromkeys(proposed_steps)
              if item in ("archived_forecast", "historical_water", "evaluation_metrics")
              and item not in suppressed_routes and _relevant_route(question, item) and item not in attempted]
    available = budget.limits.max_tool_calls - budget.tool_calls
    if available <= 0 and (routes or concepts):
        budget.exceeded = "tool_limit"
        result.errors.append("仍有相关证据缺口，但已达到只读工具调用上限；不继续补查。")
        return None
    routes = routes[:available]
    concepts = concepts[:max(0, available - len(routes))]
    if not routes and not concepts:
        return None
    if "evaluation_metrics" in routes:
        proposed_scope = "high_water_2m" if any(cue in question.casefold() for cue in ("高水位", "2米以上", "2m以上")) else "overall"
    else:
        proposed_scope = None
    ordered = tuple(item for item in ("archived_forecast", "historical_water", "evaluation_metrics") if item in routes)
    return RoutePlan(ordered + (("knowledge",) if concepts else ()), tuple(concepts), proposed_scope)


def run_investigation(
    question: str,
    *,
    as_of_utc: str | None = None,
    knowledge_checked_on_or_before: str | None = None,
    use_llm: bool = False,
    thinking_enabled: bool | None = None,
    client: ChatClient | None = None,
    forecast_db: Path = DEFAULT_DB,
    sonar_db: Path = DEFAULT_SONAR_DB,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
    audit_log: Path | None = DEFAULT_AUDIT_LOG,
    limits: InvestigationLimits = InvestigationLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> QueryResult:
    """Plan, retrieve, inspect gaps, and re-retrieve at most twice."""
    budget = _Budget(limits, clock)
    provider = client if client is not None else (DeepSeekClient(thinking_enabled=thinking_enabled is not False) if use_llm else None)
    llm_available = use_llm
    if use_llm:
        budget.reserve_llm(provider)
    try:
        result = run_query(
            question, as_of_utc=as_of_utc, knowledge_checked_on_or_before=knowledge_checked_on_or_before,
            use_llm=use_llm,
            thinking_enabled=thinking_enabled, client=provider,
            forecast_db=forecast_db, sonar_db=sonar_db, catalog=catalog, root=root,
            audit_log=None, generate_answer=False, tool_guard=budget.reserve_tool,
        )
    except UnsupportedRoute as exc:
        if audit_log is not None:
            _write_audit({
                "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                "route": [], "rounds": 0, "tool_calls": budget.tool_calls,
                "status": "rejected", "reason": str(exc),
            }, audit_log)
        raise
    result.investigation_rounds = 1 if result.plan.steps else 0
    if use_llm and any(item.get("step") == "chat_reply" for item in result.trace):
        budget.llm_calls += 1
    result.trace.insert(0, {"step": "investigation_round", "round": "1", "status": "executed", "plan": ",".join(result.plan.steps), "concepts": ",".join(result.plan.knowledge_concepts)})
    planner_errors = [item for item in result.errors if item.startswith("DeepSeek 理解问题时遇到困难") or item.startswith("DeepSeek 主题识别未通过")]
    for item in planner_errors:
        result.errors.remove(item)
        result.warnings.append(item)
    planner_error_types = {item.get("error_type") for item in result.trace if item.get("step") == "intent_plan" and item.get("status") == "failed"}
    if planner_error_types & {"LLMRequestError", "LLMNotConfigured"}:
        llm_available = False
    attempted = _attempted(result.plan)
    if not result.plan.steps:
        result.investigation_stop_reason = ("needs_clarification" if result.answer_status == "needs_clarification" else "chat_mode")
    else:
        for round_number in range(2, limits.max_rounds + 1):
            if budget.exceeded:
                break
            try:
                budget.check_time()
            except BudgetExceeded as exc:
                result.errors.append(str(exc))
                break
            followup = _followup_plan(question, result, attempted, provider, budget, llm_available)
            if followup is None:
                result.investigation_stop_reason = "evidence_complete" if not result.errors else "partial_evidence"
                break
            extra = run_query(
                question, as_of_utc=as_of_utc, knowledge_checked_on_or_before=knowledge_checked_on_or_before,
                use_llm=False,
                forecast_db=forecast_db, sonar_db=sonar_db, catalog=catalog, root=root,
                audit_log=None, forced_plan=followup, generate_answer=False,
                tool_guard=budget.reserve_tool,
            )
            attempted.update(_attempted(followup))
            _merge(result, extra)
            result.investigation_rounds = round_number
            result.trace.append({"step": "investigation_round", "round": str(round_number), "status": "executed", "plan": ",".join(followup.steps), "concepts": ",".join(followup.knowledge_concepts)})
        else:
            result.investigation_stop_reason = "round_limit"
    if budget.exceeded:
        result.investigation_stop_reason = budget.exceeded
    if result.plan.steps and llm_available and result.evidence_bundle and not result.errors and not budget.exceeded:
        synthesize = getattr(provider, "synthesize", None)
        if callable(synthesize):
            try:
                budget.reserve_llm(provider)
                answer = synthesize(question, result.evidence_bundle)
                budget.check_time()
                result.answer_text = answer
                result.trace.append({"step": "grounded_reply", "status": "ok", "citation_ids": ",".join(item["id"] for item in result.evidence_bundle)})
            except (RAGError, BudgetExceeded) as exc:
                result.errors.append(f"证据整理未完成：{exc}；仍展示已核验资料。")
                result.trace.append({"step": "grounded_reply", "status": "failed", "error_type": type(exc).__name__})
    if budget.exceeded:
        result.investigation_stop_reason = budget.exceeded
    _resolve_incomplete_result(result)
    if result.answer_status == "candidate_only" and not result.evidence_bundle:
        result.investigation_stop_reason = "candidates_only"
    traces = getattr(provider, "reasoning_traces", None)
    if isinstance(traces, list):
        result.reasoning_traces = list(traces)
    result.investigation_tool_calls = budget.tool_calls
    result.investigation_llm_calls = budget.llm_calls
    if audit_log is not None:
        try:
            _write_audit({
                "request_id": result.request_id,
                "at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                "as_of_utc": as_of_utc,
                "knowledge_checked_on_or_before": knowledge_checked_on_or_before,
                "route": result.plan.steps,
                "rounds": result.investigation_rounds,
                "tool_calls": budget.tool_calls,
                "llm_calls": budget.llm_calls,
                "stop_reason": result.investigation_stop_reason,
                "fallback": bool(result.fallback_text),
                "answer_status": result.answer_status,
                "status": "partial_or_error" if result.errors else "ok",
                "run_id": result.forecast.run_id if result.forecast else None,
                "citation_ids": [item.citation_id for item in result.knowledge],
                "trace": result.trace,
                "error_count": len(result.errors),
            }, audit_log)
            result.audit_status = "written"
        except OSError as exc:
            result.audit_status = "failed"
            result.errors.append(f"调查审计日志写入失败：{type(exc).__name__}。")
    else:
        result.audit_status = "disabled"
    return result
