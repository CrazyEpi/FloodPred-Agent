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
from .evidence_gate import (CONFLICT, assess_evidence, finalize_answer,
                            missing_items, _topics)
from .hybrid_retrieval import (QueryVariant, RetrievalRequest, hybrid_search, make_request,
                               merge_candidates, safe_trace)
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
    merge_candidates(target.document_candidates, addition.document_candidates)
    target.retrieval_runs.extend(addition.retrieval_runs)
    order = ("archived_forecast", "historical_water", "evaluation_metrics", "knowledge")
    steps = set(target.plan.steps) | set(addition.plan.steps)
    concepts = list(target.plan.knowledge_concepts)
    concepts.extend(item for item in addition.plan.knowledge_concepts if item not in concepts)
    target.plan = RoutePlan(tuple(step for step in order if step in steps), tuple(concepts),
                            target.plan.evaluation_scope or addition.plan.evaluation_scope)


def _gap_request(result: QueryResult, row, round_number: int) -> RetrievalRequest:
    node = next(node for node in result.question_graph.nodes if node.id == row.node_id)
    request = make_request(result.question, fragment=node.anchors[0].text, node=node)
    subjects = " ".join(span.text for span in node.subjects)
    if node.question_type in ("relation", "comparison"):
        focus = ("直接关系 采集实现 承接 开源监测" if round_number == 2 else
                 "论文原文 系统实现 关系依据 不是同一套设备")
    elif "official_flood_alert" in _topics(node):
        focus = "官方定义 原文 适用地区 发布机构" if round_number == 2 else "Flood Alert 原文具体章节"
    else:
        focus = f"{node.requested_attribute} 原文具体记载" if round_number == 2 else "属性 章节 原文边界"
    # This is a gap query, not an answer or a fabricated relationship. Raw input
    # and the exact node fragment remain the primary channels.
    query = QueryVariant("gap_refinement", subjects + " " + node.anchors[0].text + " " + focus, 1.0)
    return RetrievalRequest(request.original, request.fragment, (*request.queries[:4], query),
                            request.node_id, request.source_types)


def _check_fingerprint(result: QueryResult) -> tuple:
    return tuple((row.node_id, row.status, tuple(row.claim_ids), str(row.conflicts))
                 for row in result.question_evidence)


def _fill_gaps(result: QueryResult, round_number: int, budget: _Budget, *, catalog: Path,
               root: Path, forecast_db: Path, sonar_db: Path, provider, use_llm: bool,
               attempted: set[str]) -> bool:
    gaps = [row for row in missing_items(result) if row.status != CONFLICT]
    if not gaps:
        return False
    # A model can suggest which MISSING topics to inspect first, never approve
    # evidence or add unrelated tools. It does not choose arbitrary query text.
    reviewer = getattr(provider, "review", None)
    if use_llm and callable(reviewer) and budget.llm_calls < budget.limits.max_llm_calls - 1:
        try:
            budget.reserve_llm(provider)
            checked_facts = [{"id": claim.id, "fact": claim.text, "status": claim.status,
                              "source": ",".join(c.id for c in claim.citations)} for claim in result.verified_claims]
            review = reviewer(result.question, checked_facts, sorted(attempted), tuple(_QUERIES))
            priorities = set(review.concepts) if isinstance(review, IntentPlan) and review.mode == "project" else set()
            gaps.sort(key=lambda row: not bool(priorities & _topics(next(n for n in result.question_graph.nodes if n.id == row.node_id))))
            result.trace.append({"step": "gap_review", "status": "priority_only", "nodes": ",".join(row.node_id for row in gaps)})
        except (RAGError, ValueError, TypeError, BudgetExceeded) as exc:
            result.warnings.append(f"缺口排序未完成（{type(exc).__name__}）；仍使用程序识别的缺口。")
            if isinstance(exc, BudgetExceeded):
                return False
    executed = False
    for row in gaps[:3]:
        budget.check_time()
        node = next(n for n in result.question_graph.nodes if n.id == row.node_id)
        subject = " ".join(s.text.casefold() for s in node.subjects)
        numeric_tool = ("evaluation_metrics" if ("mae" in subject or "平均绝对误差" in subject) else
                        "archived_forecast" if "预测峰值" in subject else
                        "historical_water" if any(w in subject for w in ("历史水位", "实测水位", "当时水位")) else None)
        if numeric_tool:
            # Do not retry failed archive/metric reads with relaxed timestamps.
            if numeric_tool in attempted:
                continue
            scope = (result.plan.evaluation_scope or "overall") if numeric_tool == "evaluation_metrics" else None
            plan = RoutePlan((numeric_tool,), (), scope)
            extra = run_query(result.question, as_of_utc=result.as_of_utc,
                              knowledge_checked_on_or_before=result.knowledge_checked_on_or_before,
                              forecast_db=forecast_db, sonar_db=sonar_db, catalog=catalog, root=root,
                              forced_plan=plan, audit_log=None, generate_answer=False, tool_guard=budget.reserve_tool)
            _merge(result, extra)
            attempted.add(numeric_tool)
            executed = True
            continue
        request = _gap_request(result, row, round_number)
        signature = hashlib.sha256(repr((row.node_id, request.source_types, request.queries)).encode()).hexdigest()
        if signature in attempted:
            continue
        budget.reserve_tool("knowledge:gap_search")
        attempted.add(signature)
        executed = True
        try:
            candidates = hybrid_search(request, checked_on_or_before=result.knowledge_checked_on_or_before,
                                       catalog=catalog, root=root, allow_conflicts=True)
            merge_candidates(result.document_candidates, candidates.hits)
            debug = candidates.to_debug()
            debug["investigation_round"] = round_number
            result.retrieval_runs.append(debug)
            trace = safe_trace(candidates)
            trace.update(round=str(round_number), reason="unmet_question_attribute")
            result.trace.append(trace)
        except Exception as exc:
            result.warnings.append(f"{row.node_id} 补查未取得可用原文（{type(exc).__name__}）。")
            result.trace.append({"step": "gap_search", "node_id": row.node_id, "status": "failed",
                                 "round": str(round_number), "error_type": type(exc).__name__})
        budget.check_time()
    return executed


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
            gaps = missing_items(result)
            if not gaps:
                result.investigation_stop_reason = "evidence_complete"
                break
            if all(row.status == CONFLICT for row in gaps):
                result.investigation_stop_reason = "source_conflict"
                break
            before = _check_fingerprint(result)
            try:
                budget.check_time()
                executed = _fill_gaps(result, round_number, budget, catalog=catalog, root=root,
                                      forecast_db=forecast_db, sonar_db=sonar_db, provider=provider,
                                      use_llm=llm_available, attempted=attempted)
            except BudgetExceeded as exc:
                result.errors.append(str(exc))
                break
            if not executed:
                result.investigation_stop_reason = "partial_evidence"
                break
            result.investigation_rounds = round_number
            assess_evidence(result, root=root)
            result.trace.append({"step": "investigation_round", "round": str(round_number), "status": "gap_only",
                                 "missing_nodes": ",".join(row.node_id for row in missing_items(result))})
            if _check_fingerprint(result) == before:
                result.investigation_stop_reason = "no_support_gain"
                break
        else:
            result.investigation_stop_reason = "round_limit"
    if budget.exceeded:
        result.investigation_stop_reason = budget.exceeded
    if not missing_items(result) and result.plan.steps and not budget.exceeded:
        result.investigation_stop_reason = "evidence_complete"
    finalize_answer(result, root=root,
                    provider=provider if llm_available and not budget.exceeded else None,
                    reserve_llm=budget.reserve_llm)
    if budget.exceeded:
        result.investigation_stop_reason = budget.exceeded
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
