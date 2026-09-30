"""D6: explicit routing; numbers from tools, definitions from documents."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

from .archive import DEFAULT_DB
from .knowledge import DEFAULT_CATALOG, ROOT, NoEvidence, search
from .rag import ChatClient, DeepSeekClient, IntentPlan, RAGError, explain_hit
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
    request_id: str
    question: str
    as_of_utc: str | None
    plan: RoutePlan
    forecast: ReplayCard | None = None
    water: Observation | None = None
    evaluation: EvaluationMetric | None = None
    knowledge: list[KnowledgeItem] = field(default_factory=list)
    response_mode: Literal["project", "greeting", "general", "clarify"] = "project"
    answer_text: str | None = None
    errors: list[str] = field(default_factory=list)
    trace: list[dict[str, str]] = field(default_factory=list)
    audit_status: str = "pending"


DEFAULT_AUDIT_LOG = ROOT / "logs" / "requests.jsonl"


def _friendly_utc(value: str) -> str:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return f"{timestamp.year}年{timestamp.month}月{timestamp.day}日 {timestamp:%H:%M}（UTC）"


def _volunteer_fact(value: str) -> str:
    """Presentation-only wording; the original verified fact stays in KnowledgeItem."""
    return (value.replace("（level 1）", "（第一档）")
                 .replace("（level 2）", "（第二档）")
                 .replace("（level 3）", "（第三档）")
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
}


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
    if "watch" in q or "观察等级" in q:
        if not official or "项目" in q or "内部" in q:
            add("internal_watch")
    if "warning" in q or "警告等级" in q or "洪水线" in q:
        if not official or "项目" in q or "内部" in q:
            add("internal_warning")
    if "floodalert" in q and (official or any(word in q for word in ("是什么", "含义", "定义"))):
        add("official_flood_alert")
    if "caution" in q or ("论文" in q and any(word in q for word in ("风险", "等级", "warning", "watch", "阈值"))):
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
    use_llm: bool = False,
    client: ChatClient | None = None,
    forecast_db: Path = DEFAULT_DB,
    sonar_db: Path = DEFAULT_SONAR_DB,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
    audit_log: Path | None = DEFAULT_AUDIT_LOG,
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
    routing_note: str | None = None
    proposed: IntentPlan | None = None
    provider = client if client is not None else (DeepSeekClient() if use_llm else None)
    if use_llm:
        planner = getattr(provider, "plan", None)
        classify = getattr(provider, "classify", None)
        if callable(planner):
            try:
                proposed = planner(question, tuple(_QUERIES))
            except RAGError as exc:
                routing_note = f"DeepSeek 理解问题时遇到困难：{exc}；仍按本地规则取证。"
                if re.fullmatch(r"\s*(你好|您好|嗨|hi|hello)[!！。.]?\s*", question, flags=re.IGNORECASE):
                    proposed = IntentPlan("greeting")
        elif callable(classify):
            try:
                semantic_concepts = tuple(classify(question, tuple(_QUERIES)))
            except RAGError as exc:
                routing_note = f"DeepSeek 主题识别未通过：{exc}；仍按本地规则取证。"
    local_plan: RoutePlan | None = None
    try:
        local_plan = plan_route(question, semantic_concepts=semantic_concepts)
    except UnsupportedRoute:
        pass
    if proposed and proposed.mode in ("greeting", "general") and local_plan is None:
        project_cues = ("floodpred", "洪水", "水位", "预警", "警报", "预测", "mae", "模型", "项目", "论文", "watch", "warning", "patchtst")
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
    mode = proposed.mode if proposed and not plan.steps else "project"
    result = QueryResult(request_id=request_id, question=question, as_of_utc=as_of_utc, plan=plan, response_mode=mode)
    if use_llm:
        result.trace.append({"step": "intent_plan", "status": "matched" if proposed or semantic_concepts else ("failed" if routing_note else "no_match"), "mode": mode, "concepts": ",".join(plan.knowledge_concepts), "routes": ",".join(plan.steps)})
    if routing_note:
        result.errors.append(routing_note)
    if not plan.steps:
        reply = getattr(provider, "reply", None)
        if callable(reply):
            try:
                result.answer_text = reply(question, mode)
                result.trace.append({"step": "chat_reply", "status": "ok", "mode": mode})
            except RAGError as exc:
                result.errors.append(f"DeepSeek 暂时没能回答：{exc}")
                result.trace.append({"step": "chat_reply", "status": "error", "mode": mode})
        if not result.answer_text and mode == "greeting":
            result.answer_text = "你好！这里可以回看过去某个时刻的洪水预测，也能查项目资料。你可以试着问：‘当时预测的最高水位是多少？’"
    evidence_bundle: list[dict[str, str]] = []
    for step in plan.steps:
        try:
            if step == "archived_forecast":
                result.forecast = archived_forecast_tool(ForecastArgs(as_of_utc=as_of_utc), forecast_db)
                result.trace.append({"step": step, "status": "ok", "run_id": result.forecast.run_id, "as_of_utc": result.forecast.as_of_utc, "generated_utc": result.forecast.forecast_generated_utc, "stored_at_utc": result.forecast.forecast_stored_at_utc})
                evidence_bundle.append({"id": f"forecast:{result.forecast.run_id}", "kind": "historical_forecast", "fact": f"回放时间 {_friendly_utc(result.forecast.as_of_utc)}；当时预测未来最高水位 {result.forecast.predicted_peak_m:.4f} m，预计到达时间 {_friendly_utc(result.forecast.predicted_peak_utc)}；内部等级 {_volunteer_fact(result.forecast.internal_risk_label)}。这是当时的预测，不是实测。", "source": f"归档预测 run_id {result.forecast.run_id}"})
            elif step == "historical_water":
                result.water = historical_water_tool(WaterArgs(as_of_utc=as_of_utc), sonar_db)
                result.trace.append({"step": step, "status": "ok", "observed_utc": result.water.observed_utc if result.water else "none", "stored_at_utc": result.water.stored_at_utc if result.water else "none"})
                if result.water:
                    evidence_bundle.append({"id": f"water:{result.water.observed_utc}", "kind": "historical_observation", "fact": f"当时已可见的实测水位 {result.water.water_m:.3f} m；观测时间 {_friendly_utc(result.water.observed_utc)}，入库时间 {_friendly_utc(result.water.stored_at_utc)}。", "source": "只读声纳归档"})
            elif step == "evaluation_metrics":
                result.evaluation = evaluation_metrics_tool(EvaluationArgs(scope=plan.evaluation_scope))
                result.trace.append({"step": step, "status": "ok", "scope": result.evaluation.scope, "version_utc": result.evaluation.generated_utc, "source_sha256": result.evaluation.source_sha256})
                evidence_bundle.append({"id": f"evaluation:{result.evaluation.scope}:{result.evaluation.source_sha256[:12]}", "kind": "hindsight_evaluation", "fact": f"事后批次评估 MAE {result.evaluation.mae_m:.6f} {result.evaluation.unit}；样本范围：{result.evaluation.sample_scope}；匹配预测目标点 {result.evaluation.matched_prediction_points}；版本生成时间 {_friendly_utc(result.evaluation.generated_utc)}。MAE 是点级平均绝对误差，通俗地说，就是把这一批预测和实测逐个比较后平均差了多少米；不是某一次预测峰值的误差，也不是洪水事件检出率。", "source": "已校验哈希的只读评估快照"})
            else:
                for concept in plan.knowledge_concepts:
                    query, source_type = _QUERIES[concept]
                    relevant = [
                        hit for hit in search(query, source_type=source_type, catalog=catalog, root=root)
                        if hit.record["concept"] == concept
                    ]
                    if len(relevant) != 1:
                        raise NoEvidence(f"{concept} 没有唯一、已核验的知识记录。")
                    hit = relevant[0]
                    explanation = None
                    if use_llm and hit.excerpt and not callable(getattr(provider, "synthesize", None)):
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
                    ))
                    evidence_bundle.append({"id": hit.record["id"], "kind": hit.record["source_type"], "fact": _volunteer_fact(hit.record["summary"]), "source": f"{hit.record['title']} § {hit.record['section']}"})
                    result.trace.append({"step": step, "status": "ok", "citation_id": hit.record["id"], "source_type": hit.record["source_type"], "source_locator": hit.locator, "version": hit.record["version"], "llm_status": "accepted" if explanation else ("link_only" if use_llm and not hit.excerpt else ("failed" if use_llm else "disabled"))})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.errors.append(f"{step}：{exc}")
            result.trace.append({"step": step, "status": "error", "error_type": type(exc).__name__})
        except Exception as exc:
            # Archive/knowledge errors are custom Exception subclasses.
            result.errors.append(f"{step}：{type(exc).__name__}: {exc}")
            result.trace.append({"step": step, "status": "error", "error_type": type(exc).__name__})
    synthesize = getattr(provider, "synthesize", None)
    if use_llm and callable(synthesize) and evidence_bundle and not result.errors:
        try:
            result.answer_text = synthesize(question, evidence_bundle)
            result.trace.append({"step": "grounded_reply", "status": "ok", "citation_ids": ",".join(item["id"] for item in evidence_bundle)})
        except RAGError as exc:
            result.errors.append(f"DeepSeek 没能把证据整理成回答：{exc}；下面仍保留查到的原始结果。")
            result.trace.append({"step": "grounded_reply", "status": "error"})
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
