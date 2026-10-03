"""D6: explicit routing; numbers from tools, definitions from documents."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Callable, Literal
from uuid import uuid4

from .archive import DEFAULT_DB
from .knowledge import DEFAULT_CATALOG, ROOT, Hit, KnowledgeError, NoEvidence, SourceIntegrityError, search
from .hybrid_retrieval import document_requests, hybrid_search, make_request, merge_candidates, request_for_concept, safe_trace
from .rag import ChatClient, DeepSeekClient, IntentPlan, RAGError, explain_hit
from .question_graph import QuestionGraph, build_question_graph, excluded_concepts, excluded_routes
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
    version: str | None = None
    source_sha256: str | None = None
    retrieval_methods: tuple[str, ...] = ()


@dataclass
class QueryResult:
    request_id: str
    question: str
    as_of_utc: str | None
    plan: RoutePlan
    knowledge_checked_on_or_before: str | None = None
    forecast: ReplayCard | None = None
    water: Observation | None = None
    evaluation: EvaluationMetric | None = None
    knowledge: list[KnowledgeItem] = field(default_factory=list)
    response_mode: Literal["project", "greeting", "general", "clarify"] = "project"
    answer_text: str | None = None
    answer_status: str | None = None
    fallback_text: str | None = None
    reasoning_traces: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trace: list[dict[str, str]] = field(default_factory=list)
    evidence_bundle: list[dict[str, str]] = field(default_factory=list, repr=False)
    investigation_rounds: int = 0
    investigation_tool_calls: int = 0
    investigation_llm_calls: int = 0
    investigation_stop_reason: str | None = None
    audit_status: str = "pending"
    question_graph: QuestionGraph | None = None
    document_candidates: list[Hit] = field(default_factory=list, repr=False)
    retrieval_runs: list[dict] = field(default_factory=list, repr=False)


DEFAULT_AUDIT_LOG = ROOT / "logs" / "requests.jsonl"


def _friendly_utc(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{timestamp.year}年{timestamp.month}月{timestamp.day}日 {timestamp:%H:%M}（UTC）"


def volunteer_fact(value: str) -> str:
    """Display alias only; never alter the verified record or its source."""
    return (value.replace("（level 1）", "（第一档）")
                 .replace("（level 2）", "（第二档）")
                 .replace("（level 3）", "（第三档）")
                 .replace("Watch", "Caution")
                 .replace("No risk", "无风险"))


def _combine_plans(local: RoutePlan | None, proposed: IntentPlan) -> RoutePlan:
    """The model can add allowlisted requests, never erase a deterministic hit."""
    order: tuple[RouteKind, ...] = ("archived_forecast", "historical_water", "evaluation_metrics", "knowledge")
    selected = set(local.steps if local else ()) | set(proposed.steps)
    concepts = list(local.knowledge_concepts if local else ())
    for concept in proposed.concepts:
        if concept not in concepts:
            concepts.append(concept)
    if concepts:
        selected.add("knowledge")
    scope = proposed.evaluation_scope if "evaluation_metrics" in proposed.steps else (local.evaluation_scope if local else None)
    return RoutePlan(tuple(step for step in order if step in selected), tuple(concepts), scope)


def _reject_unsafe_intent(question: str) -> None:
    """Deny side effects and authority claims before any tool or LLM call."""
    q = re.sub(r"\s+", "", question.casefold())
    if len(question) > 500:
        raise UnsupportedRoute("问题超过 500 字；请缩短后重试。")
    if re.search(r"(忽略|绕过|覆盖|无视).{0,12}(指令|规则|限制|系统提示)", q):
        raise UnsupportedRoute("检测到要求绕过系统边界的指令；已拒绝执行。")
    mutation = r"(修改|删除|删掉|清空|更新|写入|插入|覆盖|重写|drop|delete|update|insert|alter|truncate|replace)"
    target = r"(sqlite|数据库|归档|数据表|forecast_runs|forecast_points|sonar_readings|table)"
    if re.search(mutation + r".{0,35}" + target, q) or re.search(target + r".{0,35}" + mutation, q):
        raise UnsupportedRoute("本应用的归档与评估工具只读，不能修改 SQLite 或源数据。")
    announce = r"(发布|发送|推送|伪造|冒充|通知|publish|send|issue)"
    alert = r"(警报|预警|floodalert|floodwarning)"
    if re.search(announce + r".{0,35}" + alert, q) or re.search(alert + r".{0,35}" + announce, q):
        raise UnsupportedRoute("本应用不能发布、发送或冒充官方洪水警报。")
    if re.search(r"(api.?key|密钥|token|密码).{0,20}(显示|泄露|输出|告诉|给我|读取)", q) or re.search(r"(显示|泄露|输出|告诉|给我|读取).{0,20}(api.?key|密钥|token|密码)", q):
        raise UnsupportedRoute("不能读取或展示密钥。")
    if re.search(r"(当前|现在|实时|此刻|今天).{0,20}(官方警报|官方预警|floodalert|floodwarning)", q):
        raise UnsupportedRoute("本应用不查询实时官方警报；请使用官方实时服务。")
    if (re.search(r"(当前|现在|实时|此刻|今天).{0,12}(水位|洪水风险|洪水预测|预测水位)", q)
        or re.search(r"(水位|洪水风险|洪水预测|预测水位).{0,12}(当前|现在|实时|此刻|今天)", q)) and not any(
        cue in q for cue in ("历史", "当时", "回放", "过去", "事后")
    ):
        raise UnsupportedRoute("这里没有实时水位或当前风险数据，只能回看历史记录；请不要把回放结果当作现在的情况。")
    if any(any(word in clause for word in ("现在", "目前", "当前", "今天", "如今", "还在", "仍在"))
           and any(word in clause for word in ("传感器", "感应器", "水位仪", "声纳", "sonar", "探头", "旧设备"))
           and any(word in clause for word in ("布置", "安装", "在哪", "放哪", "位置", "运行", "在线", "还用", "还在", "仍在"))
           for clause in re.split(r"[，,；;。！？!?\n]", q)):
        raise UnsupportedRoute("现有资料只记录早期传感器的历史布置，不能确认设备现在的位置或运行状态。可以问‘旧传感器当时怎么布置的？’")


def _write_audit(record: dict[str, object], path: Path) -> None:
    """Append metadata only; never persist question text, evidence or API key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


_QUERIES = {
    "internal_watch": ("项目内部Watch", "internal_project"),
    "internal_warning": ("项目内部Warning", "internal_project"),
    "highwater_evaluation_limit": ("模型限制", "evaluation_report"),
    "official_flood_alert": ("官方Flood Alert", "official_public_guidance"),
    "thesis_forecast_design": ("论文预测方法", "thesis"),
    "thesis_risk_terms": ("论文预警等级", "thesis"),
    "thesis_offline_events": ("离线洪水检出", "thesis"),
    "thesis_live_limit": ("线上洪水检出", "thesis"),
    "thesis_rapid_rise": ("快速上涨", "thesis"),
    "thesis_model_design": ("PatchTST改进", "thesis"),
    "housemill_heritage": ("House Mill是什么", "heritage_public"),
    "housemill_flood_context": ("House Mill洪水背景", "research_paper"),
    "housemill_old_sensor": ("House Mill旧传感器", "prior_project"),
    "housemill_study_findings": ("House Mill研究发现", "research_paper"),
    "housemill_volunteer_need": ("志愿者需要什么信息", "research_paper"),
    "housemill_project_connection": ("旧项目和FloodPred关系", "thesis"),
}


def _old_sensor_placement_query(question: str) -> bool:
    """Recognize conversational references, while requiring device + placement context."""
    q = re.sub(r"\s+", "", question.casefold())
    explicit = ("旧传感器", "旧声纳", "旧监测", "早期监测", "sonarbox", "树莓派传感器")
    if any(word in q for word in explicit):
        return True
    device = ("传感器", "感应器", "水位仪", "声纳", "sonar", "探头", "测水设备", "水位设备", "设备", "仪器")
    placement = ("布置", "安装", "怎么装", "放哪", "放在", "放到", "摆放", "位置", "架设", "部署", "装在哪", "放哪里", "在哪儿")
    context = ("旧", "以前", "之前", "当时", "早期", "原来", "先前", "duncan", "wilson", "housemill", "三磨坊")
    if any(word in q for word in placement):
        for alias in ("旧传感器", "旧声纳", "水位仪", "声纳探头"):
            if len(q) >= len(alias) and any(
                q[index] == alias[0]
                and SequenceMatcher(None, q[index:index + len(alias)], alias).ratio() >= 0.75
                for index in range(len(q) - len(alias) + 1)
            ):
                return True
    specific_device = any(word in q for word in device[:-2])
    return (specific_device and any(word in q for word in placement)
            or any(word in q for word in context) and any(word in q for word in device) and any(word in q for word in placement))


def plan_route(question: str, *, semantic_concepts: tuple[str, ...] = ()) -> RoutePlan:
    _reject_unsafe_intent(question)
    q = re.sub(r"\s+", "", question.casefold())
    if not q:
        raise UnsupportedRoute("请输入问题。")
    forecast = any(word in q for word in ("预测峰值", "预测最高", "当时预测", "run_id", "风险卡", "预测会涨到", "预测能到多高"))
    water = any(word in q for word in ("历史水位", "当时水位", "实测水位", "观测水位", "声纳水位", "那时水位"))
    metrics = any(word in q for word in ("mae", "平均绝对误差", "评估指标", "预测误差", "模型误差"))
    concepts: list[str] = []

    def add(concept: str) -> None:
        if concept not in concepts:
            concepts.append(concept)

    official = "官方" in q or "environmentagency" in q
    if "watch" in q or "caution" in q or "观察等级" in q or "留意等级" in q:
        if not official or "项目" in q or "内部" in q:
            add("internal_watch")
    if "warning" in q or "警告等级" in q or "洪水线" in q:
        if not official or "项目" in q or "内部" in q:
            add("internal_warning")
    if "floodalert" in q and (official or any(word in q for word in ("是什么", "含义", "定义"))):
        add("official_flood_alert")
    if "论文" in q and any(word in q for word in ("caution", "风险", "等级", "warning", "watch", "阈值")):
        add("thesis_risk_terms")
    if "模型" in q and any(word in q for word in ("局限", "限制", "不足", "短板")):
        add("highwater_evaluation_limit")
        add("thesis_live_limit")
    if any(word in q for word in ("快速上涨", "快涨", "突然上涨", "涨水延迟", "相位滞后", "跟不上", "30-60分钟")):
        add("thesis_rapid_rise")
    if (any(word in q for word in ("离线", "历史事件", "86.19")) and any(word in q for word in ("洪水", "召回", "检出", "事件", "准确", "表现", "86.19"))):
        add("thesis_offline_events")
    if (any(word in q for word in ("线上", "部署", "实时", "真实洪水")) and any(word in q for word in ("洪水", "召回", "检出", "评估", "验证", "表现"))):
        add("thesis_live_limit")
    if ("论文" in q and any(word in q for word in ("怎么预测", "输入", "24小时", "数据", "96"))) or any(word in q for word in ("七天数据", "96个预测点")):
        add("thesis_forecast_design")
    if "patchtst" in q or ("模型" in q and any(word in q for word in ("怎么设计", "结构", "改进", "分类头", "huber"))):
        add("thesis_model_design")
    if any(word in q for word in ("召回率", "洪水检出")) and not any(concept in concepts for concept in ("thesis_offline_events", "thesis_live_limit")):
        add("highwater_evaluation_limit")
    housemill = any(word in q for word in ("housemill", "三磨坊"))
    if housemill and "是什么关系" not in q and any(word in q for word in ("是什么", "背景", "历史", "哪里", "什么地方", "建于", "建筑")):
        add("housemill_heritage")
    if housemill and any(word in q for word in ("洪水背景", "为何淹水", "为什么淹水", "进水", "潮汐", "木梁", "地板", "为什么要监测")):
        add("housemill_flood_context")
    if _old_sensor_placement_query(question) or (housemill and any(word in q for word in ("传感器", "声纳", "sonar", "duncanwilson"))):
        add("housemill_old_sensor")
    if any(word in q for word in ("136次", "42次", "136和42", "136与42", "旧监测结果", "旧研究发现", "53分钟")) or (housemill and "研究发现" in q):
        add("housemill_study_findings")
    if any(word in q for word in ("志愿者需要什么", "旧界面", "触水时长")) or (housemill and "志愿者" in q):
        add("housemill_volunteer_need")
    if any(word in q for word in ("旧项目和floodpred", "housemill与floodpred", "旧传感器与预测", "旧系统和floodpred", "如何接续旧系统")) or (housemill and "floodpred" in q and any(word in q for word in ("旧", "监测", "关系", "关联", "承接"))):
        add("housemill_project_connection")
    for concept in semantic_concepts:
        if concept in _QUERIES:
            add(concept)
    if not any((forecast, water, metrics, concepts)):
        raise UnsupportedRoute("这句我暂时没找到可靠的取证路径。可以问预测峰值、历史水位、MAE、内部等级，或论文中的方法与评估。")
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


def run_query(
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
    forced_plan: RoutePlan | None = None,
    generate_answer: bool = True,
    tool_guard: Callable[[str], None] | None = None,
) -> QueryResult:
    request_id = uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        _reject_unsafe_intent(question)
    except UnsupportedRoute as exc:
        if audit_log is not None:
            _write_audit({"request_id": request_id, "at_utc": timestamp, "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(), "route": [], "status": "rejected", "reason": str(exc)}, audit_log)
        raise
    semantic_concepts: tuple[str, ...] = ()
    question_graph = build_question_graph(question)
    routing_note: str | None = None
    routing_error_type: str | None = None
    proposed: IntentPlan | None = None
    provider = client if client is not None else (DeepSeekClient(thinking_enabled=thinking_enabled is not False) if use_llm else None)
    if isinstance(provider, DeepSeekClient) and thinking_enabled is not None:
        provider.thinking_enabled = thinking_enabled
    reasoning_store = getattr(provider, "reasoning_traces", None)
    if isinstance(reasoning_store, list):
        reasoning_store.clear()
    if forced_plan is not None:
        if use_llm or not isinstance(forced_plan, RoutePlan):
            raise ValueError("Forced plans must be a RoutePlan without an LLM planner")
        if (not forced_plan.steps or any(step not in ("archived_forecast", "historical_water", "evaluation_metrics", "knowledge") for step in forced_plan.steps)
            or any(concept not in _QUERIES for concept in forced_plan.knowledge_concepts)
            or ("knowledge" in forced_plan.steps) != bool(forced_plan.knowledge_concepts)
            or ("evaluation_metrics" in forced_plan.steps) != (forced_plan.evaluation_scope in ("overall", "high_water_2m"))):
            raise ValueError("Forced plan failed the read-only tool whitelist")
    if use_llm and forced_plan is None:
        planner = getattr(provider, "plan", None)
        classify = getattr(provider, "classify", None)
        if callable(planner):
            try:
                proposed = planner(question, tuple(_QUERIES))
                if proposed.question_graph is not None:
                    if (not isinstance(proposed.question_graph, QuestionGraph)
                        or proposed.question_graph.original_text != question):
                        raise RAGError("问题图与本次用户原话不一致。")
                    question_graph = proposed.question_graph
            except RAGError as exc:
                proposed = None
                routing_note = f"DeepSeek 理解问题时遇到困难：{exc}；仍按本地规则取证。"
                routing_error_type = type(exc).__name__
                if re.fullmatch(r"\s*(你好|您好|嗨|hi|hello)[!！。.]?\s*", question, flags=re.IGNORECASE):
                    proposed = IntentPlan("greeting")
        elif callable(classify):
            try:
                semantic_concepts = tuple(classify(question, tuple(_QUERIES)))
            except RAGError as exc:
                routing_note = f"DeepSeek 主题识别未通过：{exc}；仍按本地规则取证。"
                routing_error_type = type(exc).__name__
    local_plan: RoutePlan | None = None
    if forced_plan is not None:
        local_plan = forced_plan
    else:
        try:
            local_plan = plan_route(question, semantic_concepts=semantic_concepts)
        except UnsupportedRoute:
            pass
        if local_plan is None and not question_graph.needs_clarification:
            q = question.casefold()
            # A document-shaped project request can discover catalog candidates
            # even when no preset concept resolves. It cannot become an answer.
            domain = any(cue in q for cue in ("floodpred", "house mill", "housemill", "旧传感器", "测水", "论文", "潮水", "潮汐"))
            document_request = any(cue in q for cue in ("布置", "摆放", "安装", "位置", "哪里", "方法", "背景", "需要", "研究", "输入", "论文"))
            if domain and document_request:
                local_plan = RoutePlan(("knowledge",), (), None)
    if proposed and proposed.mode in ("greeting", "general") and local_plan is None:
        project_cues = ("floodpred", "洪水", "水位", "预警", "警报", "预测", "mae", "模型", "项目", "论文", "watch", "caution", "warning", "patchtst")
        if any(cue in question.casefold() for cue in project_cues):
            proposed = IntentPlan("clarify")
    try:
        if proposed and proposed.mode == "project":
            plan = _combine_plans(local_plan, proposed)
        elif local_plan is not None:
            plan = local_plan
        elif proposed and proposed.mode in ("greeting", "general", "clarify"):
            plan = RoutePlan((), (), None)
        else:
            raise UnsupportedRoute("这句我暂时没找到可靠的取证路径。可以问预测峰值、历史水位、MAE、内部等级，或论文中的方法与评估。")
    except UnsupportedRoute as exc:
        if audit_log is not None:
            _write_audit({"request_id": request_id, "at_utc": timestamp, "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(), "route": [], "status": "rejected", "reason": str(exc)}, audit_log)
        raise UnsupportedRoute(f"{exc} {routing_note or ''}".strip()) from exc
    suppressed = excluded_routes(question_graph)
    suppressed_topics = excluded_concepts(question_graph)
    if suppressed or suppressed_topics:
        concepts = tuple(item for item in plan.knowledge_concepts if item not in suppressed_topics)
        steps = tuple(step for step in plan.steps if step not in suppressed
                      and (step != "knowledge" or concepts))
        plan = RoutePlan(steps, concepts, None if "evaluation_metrics" in suppressed else plan.evaluation_scope)
    if question_graph.needs_clarification:
        plan = RoutePlan((), (), None)
    # Explicit source requests override a concept's historical default route.
    # Do not let an internal Watch concept abort a request for thesis Caution.
    if "knowledge" in plan.steps:
        compatible = []
        for concept in plan.knowledge_concepts:
            query, source_hint = _QUERIES[concept]
            request = request_for_concept(question, question_graph, concept, query)
            if request.source_types is None or source_hint in request.source_types:
                compatible.append(concept)
        plan = RoutePlan(plan.steps, tuple(compatible), plan.evaluation_scope)
    clarification_text = (question_graph.clarifications[0] if question_graph.needs_clarification else
                          "你排除了这次可查的内容。你想改查哪一部分？" if (suppressed or suppressed_topics) and not plan.steps else None)
    mode = ("clarify" if clarification_text else
            proposed.mode if proposed and not plan.steps else "project")
    result = QueryResult(request_id=request_id, question=question, as_of_utc=as_of_utc, plan=plan,
                         knowledge_checked_on_or_before=knowledge_checked_on_or_before,
                         response_mode=mode, question_graph=question_graph)
    result.trace.append({"step": "question_graph", "status": "clarify" if question_graph.needs_clarification else "checked",
                         "origin": question_graph.origin, "nodes": str(len(question_graph.nodes)),
                         "ambiguous_references": str(sum(ref.status == "ambiguous" for ref in question_graph.coreferences)),
                         "suppressed_routes": ",".join(sorted(suppressed)),
                         "suppressed_concepts": ",".join(sorted(suppressed_topics))})
    result.warnings.extend(question_graph.validation_notes)
    if use_llm:
        result.trace.append({"step": "intent_plan", "status": "matched" if proposed or semantic_concepts else ("failed" if routing_note else "no_match"), "mode": mode, "concepts": ",".join(plan.knowledge_concepts), "routes": ",".join(plan.steps), "error_type": routing_error_type or ""})
    if routing_note:
        result.errors.append(routing_note)
    if not plan.steps:
        reply = getattr(provider, "reply", None)
        if clarification_text:
            result.answer_text = clarification_text
            result.answer_status = "needs_clarification"
        elif callable(reply):
            try:
                result.answer_text = reply(question, mode)
                result.trace.append({"step": "chat_reply", "status": "ok", "mode": mode})
            except RAGError as exc:
                result.errors.append(f"DeepSeek 暂时没能回答：{exc}")
                result.trace.append({"step": "chat_reply", "status": "error", "mode": mode})
        if not result.answer_text and mode == "greeting":
            result.answer_text = "你好！这里可以回看过去某个时刻的洪水预测，也能查项目资料。你可以试着问：‘当时预测的最高水位是多少？’"
    evidence_bundle: list[dict[str, str]] = []
    # One logical read-only knowledge call can batch bounded local node queries.
    # Extra nodes discover candidates only; they never enter the answer bundle.
    graph_requests = document_requests(question_graph)
    graph_batch_done = False
    def collect_candidates(request):
        candidates = hybrid_search(request, checked_on_or_before=knowledge_checked_on_or_before,
                                   catalog=catalog, root=root)
        merge_candidates(result.document_candidates, candidates.hits)
        result.retrieval_runs.append(candidates.to_debug())
        result.trace.append(safe_trace(candidates))
        return candidates

    def collect_graph_batch(primary):
        nonlocal graph_batch_done
        if graph_batch_done:
            return
        graph_batch_done = True
        extra_requests = [request for request in graph_requests
                          if request.fragment != primary.fragment or request.source_types != primary.source_types]
        for request in extra_requests[:5]:
            try:
                collect_candidates(request)
            except (OSError, ValueError, KnowledgeError) as exc:
                result.warnings.append(f"问题 {request.node_id} 的候选补充检索未完成：{type(exc).__name__}。")
                result.trace.append({"step": "hybrid_retrieval", "node_id": request.node_id or "",
                                     "status": "candidate_error", "error_type": type(exc).__name__})
    for step in plan.steps:
        try:
            if step == "archived_forecast":
                if tool_guard is not None:
                    tool_guard(step)
                result.forecast = archived_forecast_tool(ForecastArgs(as_of_utc=as_of_utc), forecast_db)
                result.trace.append({"step": step, "status": "ok", "run_id": result.forecast.run_id, "as_of_utc": result.forecast.as_of_utc, "generated_utc": result.forecast.forecast_generated_utc, "stored_at_utc": result.forecast.forecast_stored_at_utc})
                evidence_bundle.append({"id": f"forecast:{result.forecast.run_id}", "kind": "historical_forecast", "fact": f"回放时间 {_friendly_utc(result.forecast.as_of_utc)}；当时预测未来最高水位 {result.forecast.predicted_peak_m:.4f} m，预计到达时间 {_friendly_utc(result.forecast.predicted_peak_utc)}；内部等级 {volunteer_fact(result.forecast.internal_risk_label)}。这是当时的预测，不是实测。", "source": f"归档预测 run_id {result.forecast.run_id}"})
            elif step == "historical_water":
                if tool_guard is not None:
                    tool_guard(step)
                result.water = historical_water_tool(WaterArgs(as_of_utc=as_of_utc), sonar_db)
                result.trace.append({"step": step, "status": "ok", "observed_utc": result.water.observed_utc if result.water else "none", "stored_at_utc": result.water.stored_at_utc if result.water else "none"})
                if result.water:
                    evidence_bundle.append({"id": f"water:{result.water.observed_utc}", "kind": "historical_observation", "fact": f"当时已可见的实测水位 {result.water.water_m:.3f} m；观测时间 {_friendly_utc(result.water.observed_utc)}，入库时间 {_friendly_utc(result.water.stored_at_utc)}。", "source": "只读声纳归档"})
            elif step == "evaluation_metrics":
                if tool_guard is not None:
                    tool_guard(step)
                result.evaluation = evaluation_metrics_tool(EvaluationArgs(scope=plan.evaluation_scope))
                result.trace.append({"step": step, "status": "ok", "scope": result.evaluation.scope, "version_utc": result.evaluation.generated_utc, "source_sha256": result.evaluation.source_sha256})
                evidence_bundle.append({"id": f"evaluation:{result.evaluation.scope}:{result.evaluation.source_sha256[:12]}", "kind": "hindsight_evaluation", "fact": f"事后批次评估 MAE {result.evaluation.mae_m:.6f} {result.evaluation.unit}；样本范围：{volunteer_fact(result.evaluation.sample_scope)}；匹配预测目标点 {result.evaluation.matched_prediction_points}；版本生成时间 {_friendly_utc(result.evaluation.generated_utc)}。MAE 是点级平均绝对误差，通俗地说，就是把这一批预测和实测逐个比较后平均差了多少米；不是某一次预测峰值的误差，也不是洪水事件检出率。", "source": "已校验哈希的只读评估快照"})
            else:
                if not plan.knowledge_concepts:
                    if tool_guard is not None:
                        tool_guard("knowledge:document_search")
                    request = graph_requests[0] if graph_requests else make_request(question)
                    collect_candidates(request)
                    collect_graph_batch(request)
                    result.answer_status = "candidate_only"
                for concept in plan.knowledge_concepts:
                    if tool_guard is not None:
                        tool_guard(f"knowledge:{concept}")
                    query, _legacy_source_hint = _QUERIES[concept]
                    request = request_for_concept(question, question_graph, concept, query)
                    candidates = collect_candidates(request)
                    collect_graph_batch(request)
                    relevant = [replace(hit, record=record) for hit in candidates.hits
                                for record in (hit.record, *hit.equivalent_records)
                                if record["concept"] == concept]
                    if len(relevant) != 1:
                        if any(item["concept"] == concept for item in candidates.quarantined):
                            raise SourceIntegrityError(f"{concept} 的目标来源缺失或未通过哈希检查。")
                        raise NoEvidence(f"{concept} 没有唯一、已核验的知识记录。")
                    hit = relevant[0]
                    explanation = None
                    if generate_answer and use_llm and hit.excerpt and not callable(getattr(provider, "synthesize", None)):
                        try:
                            generated = explain_hit(question, hit, concept=concept, client=client)
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
                        version=hit.record["version"],
                        source_sha256=hit.record.get("sha256"),
                        retrieval_methods=hit.retrieval_methods,
                    ))
                    source_note = "；原始术语 Watch，界面别名 Caution" if "Watch" in hit.record["summary"] else ""
                    evidence_bundle.append({"id": hit.record["id"], "kind": hit.record["source_type"], "fact": volunteer_fact(hit.record["summary"]), "source": f"{hit.record['title']} § {hit.record['section']}{source_note}", "excerpt": hit.excerpt or ""})
                    result.trace.append({"step": step, "status": "ok", "citation_id": hit.record["id"], "source_type": hit.record["source_type"], "source_locator": hit.locator, "version": hit.record["version"], "llm_status": "accepted" if explanation else ("link_only" if use_llm and not hit.excerpt else ("failed" if use_llm else "disabled"))})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.errors.append(f"{step}：{exc}")
            result.trace.append({"step": step, "status": "error", "error_type": type(exc).__name__})
        except Exception as exc:
            # Archive/knowledge errors are custom Exception subclasses.
            result.errors.append(f"{step}：{type(exc).__name__}: {exc}")
            result.trace.append({"step": step, "status": "error", "error_type": type(exc).__name__})
    result.evidence_bundle = evidence_bundle
    synthesize = getattr(provider, "synthesize", None)
    if generate_answer and use_llm and callable(synthesize) and evidence_bundle and not result.errors:
        try:
            result.answer_text = synthesize(question, evidence_bundle)
            result.trace.append({"step": "grounded_reply", "status": "ok", "citation_ids": ",".join(item["id"] for item in evidence_bundle)})
        except RAGError as exc:
            result.errors.append(f"DeepSeek 没能把证据整理成回答：{exc}；下面仍保留查到的原始结果。")
            result.trace.append({"step": "grounded_reply", "status": "error"})
    if isinstance(reasoning_store, list):
        result.reasoning_traces = list(reasoning_store)
    if audit_log is not None:
        try:
            _write_audit({"request_id": request_id, "at_utc": timestamp, "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(), "as_of_utc": as_of_utc, "route": plan.steps, "status": "partial_or_error" if result.errors else "ok", "run_id": result.forecast.run_id if result.forecast else None, "citation_ids": [item.citation_id for item in result.knowledge], "trace": result.trace, "error_count": len(result.errors)}, audit_log)
            result.audit_status = "written"
        except OSError as exc:
            result.audit_status = "failed"
            result.errors.append(f"请求审计日志写入失败：{type(exc).__name__}；结果仍可查看，但本次请求无持久审计记录。")
    else:
        result.audit_status = "disabled"
    return result
