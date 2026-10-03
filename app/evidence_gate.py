"""Per-question evidence contracts and claim-level, fail-closed answer gate.

No vector score or LLM confidence can authorize a fact. This small-corpus gate
uses reviewed attribute contracts, exact sources, and bounded derivations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
from typing import TYPE_CHECKING

from .archive import parse_utc
from .hybrid_retrieval import make_request
from .knowledge import Hit, KnowledgeError, _source_evidence
from .question_graph import QuestionItem, normalize_input
from .support_profiles import PROFILES, REVIEWED_EXCERPTS

if TYPE_CHECKING:
    from .router import QueryResult

DIRECT = "direct_support"
DERIVED = "explicit_derivation"
RELATED = "related_only"
UNSUPPORTED = "not_supported"
CONFLICT = "conflict"
STATUS_LABELS = {DIRECT: "直接支持", DERIVED: "可明确推导", RELATED: "仅相关",
                 UNSUPPORTED: "不支持", CONFLICT: "存在冲突"}
SUPPORTED = {DIRECT, DERIVED}


@dataclass(frozen=True)
class Citation:
    id: str
    locator: str
    version: str
    source_type: str
    quote: str
    sha256: str | None = None
    time_scope: str = "document_context_not_replay_availability"
    unit: str | None = None


@dataclass(frozen=True)
class VerifiedClaim:
    id: str
    node_id: str
    text: str
    citations: tuple[Citation, ...]
    status: str = DIRECT
    derivation: str | None = None


@dataclass
class QuestionEvidence:
    node_id: str
    question: str
    attribute: str
    status: str = UNSUPPORTED
    claim_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    candidate_ids: list[str] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    excluded: bool = False

    def to_dict(self) -> dict:
        return {**asdict(self), "status_label": STATUS_LABELS[self.status]}


def _numbers(text: str) -> set[Decimal]:
    # IDs/dates are checked separately; English number words in source prose
    # have an explicit conversion, not invented numerical evidence.
    text = re.sub(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z)?|[a-f0-9]{64}", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    for word, value in {"one": "1", "two": "2", "seven": "7", "six": "6"}.items():
        text = re.sub(rf"\b{word}\b", value, text, flags=re.I)
    return {Decimal(value) for value in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text)}


def _unit_values(text: str) -> set[tuple[Decimal, str]]:
    aliases = {"米": "m", "m": "m", "分钟": "min", "minute": "min", "minutes": "min",
               "小时": "h", "hour": "h", "hours": "h", "%": "%", "天": "d", "day": "d", "days": "d"}
    for word, value in {"one": "1", "two": "2", "seven": "7"}.items():
        text = re.sub(rf"\b{word}\b", value, text, flags=re.I)
    return {(Decimal(value), aliases[unit.casefold()]) for value, unit in re.findall(
        r"(\d+(?:\.\d+)?)\s*[-]?\s*(minutes|minute|hours|hour|days|day|分钟|小时|天|米|m\b|%)", text, re.I)}


def _injection(text: str) -> bool:
    return bool(re.search(r"忽略.{0,12}(指令|规则|系统)|(?:ignore|override).{0,25}(?:instructions|system)|"
                          r"(?:显示|泄露|输出).{0,12}(?:密钥|api.?key)|drop\s+table|delete\s+from", text, re.I))


def _valid_claim(claim: VerifiedClaim) -> bool:
    if not claim.citations or claim.status not in SUPPORTED:
        return False
    if any(not c.id or not c.locator or not c.version or not c.quote.strip() for c in claim.citations):
        return False
    source = " ".join(c.quote for c in claim.citations)
    if _injection(source):
        return False
    if not _numbers(claim.text) <= _numbers(source):
        return False
    if not _unit_values(claim.text) <= _unit_values(source):
        return False
    return not bool(re.search(r"保证安全|已经发布.{0,8}(官方|警报)|现在.{0,8}(运行正常|设备在线)", claim.text))


def _topics(node: QuestionItem) -> set[str]:
    subject = normalize_input(" ".join(span.text for span in node.subjects))
    anchor = normalize_input(node.anchors[0].text)
    topics: set[str] = set()
    if re.search(r"watch|caution|观察等级|留意等级", subject):
        topics.update(("internal_watch", "thesis_risk_terms"))
    if re.search(r"warning|警告等级|洪水线", subject) or ("4.43" in anchor and "分级" in anchor):
        topics.update(("internal_warning", "thesis_risk_terms"))
    if re.search(r"flood\s*alert|官方预警", subject):
        topics.add("official_flood_alert")
    if re.search(r"旧|以前|早期|原来|之前|duncan|声纳|传感|水位仪|水位设备|测水设备|探头", subject):
        topics.add("housemill_old_sensor")
    if subject == "floodpred":
        topics.add("thesis_forecast_design")
        if re.search(r"旧|早期", anchor) and re.search(r"house\s*mill|监测|传感", anchor):
            # The bridge paragraph itself explicitly describes the new
            # pipeline; it can support this endpoint, not just the relation.
            topics.add("housemill_project_connection")
    if re.search(r"floodpred.*(?:预测|模型)|预测方法|论文", subject) and (
            "方法" in anchor or any(cue in anchor for cue in ("输入", "七天", "96", "怎么预测"))):
        topics.add("thesis_forecast_design")
    if re.search(r"patchtst|模型|论文", subject):
        if any(cue in anchor for cue in ("结构", "改进", "huber", "分类头", "设计")):
            topics.add("thesis_model_design")
        if any(cue in anchor for cue in ("局限", "限制", "不足", "检出", "洪水", "验证")):
            topics.update(("thesis_live_limit", "highwater_evaluation_limit"))
        if any(cue in anchor for cue in ("离线", "召回", "86.19")):
            topics.add("thesis_offline_events")
        if any(cue in anchor for cue in ("快涨", "快速", "突然上涨", "滞后", "慢半拍")):
            topics.add("thesis_rapid_rise")
    if re.search(r"house\s*mill|三磨坊", subject):
        topics.add("housemill_heritage")
        if re.search(r"旧|早期", anchor) and re.search(r"监测|传感|声纳", anchor):
            topics.discard("housemill_heritage")
            topics.add("housemill_old_sensor")
        if any(cue in anchor for cue in ("潮汐", "潮水", "木梁", "进水", "淹水", "为何", "为什么")):
            topics.add("housemill_flood_context")
    if re.search(r"136|42|53|研究发现|旧研究", subject) or ("论文" in subject and re.search(r"136|42|53", anchor)):
        topics.add("housemill_study_findings")
    if "志愿者" in subject or "触水时长" in subject:
        topics.add("housemill_volunteer_need")
    if re.search(r"floodpred|预测方法", subject) and re.search(r"旧|早期|承接|接续", subject):
        topics.add("housemill_project_connection")
    if (node.question_type in ("relation", "comparison") and "floodpred" in subject
        and re.search(r"house\s*mill|旧|早期", subject) and re.search(r"监测|旧|承接", anchor)):
        topics.add("housemill_project_connection")
    if re.search(r"线上|部署期|实时", anchor) and re.search(r"洪水.*检出|检出.*洪水|验证.*洪水|洪水.*验证", anchor):
        topics.add("thesis_live_limit")
    return topics


def _tool_claim(result: QueryResult, node: QuestionItem) -> VerifiedClaim | None:
    subject = normalize_input(" ".join(span.text for span in node.subjects))
    # A relationship must not be approved simply because one endpoint exists.
    if node.question_type in ("relation", "comparison"):
        return None
    facts = result.evidence_bundle
    if re.search(r"mae|平均绝对误差", subject):
        metric = result.evaluation
        if metric is None or not math.isfinite(metric.mae_m) or metric.unit != "m" or metric.matched_prediction_points <= 0:
            return None
        try:
            parse_utc(metric.generated_utc)
        except ValueError:
            return None
        if not metric.sample_scope or not metric.source_sha256 or not metric.source_locator:
            return None
        text = (f"这批预测的平均误差（MAE）是 {metric.mae_m:.6f} m，共 {metric.matched_prediction_points:,} 个匹配预测目标点。"
                f"样本范围：{metric.sample_scope.replace('Watch', 'Caution')} 评估版本：{metric.generated_utc}。"
                "这是事后评估，不是独立洪水事件统计，也不是这一次峰值的实际误差。")
        evidence = next((e for e in facts if e["kind"] == "hindsight_evaluation"), None)
        if not evidence:
            return None
        citation = Citation(evidence["id"], metric.source_locator, metric.generated_utc,
                            "evaluation_report", evidence["fact"] + " " + metric.generated_utc,
                            metric.source_sha256, "hindsight_evaluation", "m")
    elif re.search(r"预测峰值|预测最高|当时预测|风险卡", subject) or (subject == "预测" and node.requested_attribute == "value"):
        card = result.forecast
        if card is None or not math.isfinite(card.predicted_peak_m) or card.mode != "historical_replay":
            return None
        cutoff = parse_utc(card.as_of_utc)
        if any(parse_utc(value) > cutoff for value in (card.forecast_generated_utc, card.forecast_stored_at_utc)):
            return None
        text = (f"当时预计最高水位是 {card.predicted_peak_m:.4f} m，预计到达时间是 {card.predicted_peak_utc}（UTC）。"
                f"这是历史预测，不是实测或实时水情；run_id：{card.run_id}。")
        evidence = next((e for e in facts if e["kind"] == "historical_forecast"), None)
        if not evidence:
            return None
        citation = Citation(evidence["id"], f"归档 forecast_runs/forecast_points:run_id={card.run_id}",
                            card.forecast_generated_utc, "historical_forecast", evidence["fact"] + " " + card.predicted_peak_utc,
                            time_scope="known_at_replay", unit="m")
    elif re.search(r"历史水位|当时水位|实测水位|观测水位|声纳水位", subject):
        water = result.water
        if not water or not result.as_of_utc or not math.isfinite(water.water_m):
            return None
        if any(parse_utc(value) > parse_utc(result.as_of_utc) for value in (water.observed_utc, water.stored_at_utc)):
            return None
        evidence = next((e for e in facts if e["kind"] == "historical_observation"), None)
        if not evidence:
            return None
        text = f"当时可见的实测水位是 {water.water_m:.3f} m，观测时间 {water.observed_utc}，入库时间 {water.stored_at_utc}；不是现在的水位。"
        citation = Citation(evidence["id"], f"声纳归档 sonar_readings:{water.observed_utc}", water.stored_at_utc,
                            "historical_observation", evidence["fact"], time_scope="known_at_replay", unit="m")
    else:
        return None
    claim = VerifiedClaim(f"{node.id}-tool", node.id, text, (citation,))
    return claim if _valid_claim(claim) else None


def assess_evidence(result: QueryResult, *, root: Path) -> None:
    """Revalidate real source paragraphs, and build a state for EVERY node."""
    if not result.question_graph or not result.plan.steps:
        return
    claims: list[VerifiedClaim] = []
    rows: list[QuestionEvidence] = []
    verified: dict[str, tuple[Hit, str]] = {}
    rejected: dict[str, str] = {}
    for hit in result.document_candidates:
        for record in (hit.record, *hit.equivalent_records):
            try:
                locator, excerpt = _source_evidence(record, root)
                if not excerpt:
                    rejected[record["id"]] = "只有链接／目录摘要，没有可核对的原文。"
                    continue
                if _injection(excerpt):
                    rejected[record["id"]] = "来源含提示注入指令，不能用于结论。"
                    continue
                verified[record["id"]] = (replace(hit, record=record, locator=locator, excerpt=excerpt), excerpt)
            except (KnowledgeError, OSError, KeyError, ValueError):
                rejected[record["id"]] = "来源缺失、定位无效或完整性检查失败。"
    conflicts = [conflict for run in result.retrieval_runs for conflict in run.get("conflicts", ())]
    for node in result.question_graph.nodes:
        row = QuestionEvidence(node.id, node.anchors[0].text, node.requested_attribute, excluded=node.excluded)
        rows.append(row)
        if node.excluded:
            row.reasons.append("用户明确排除，本项不取证、不计入缺口。")
            continue
        row.checks = {key: False for key in ("locator", "integrity", "version", "time_scope", "units", "requested_attribute")}
        try:
            tool_claim = _tool_claim(result, node)
        except (ValueError, OSError, KeyError, TypeError):
            tool_claim = None
        if tool_claim:
            claims.append(tool_claim)
            row.status, row.claim_ids = DIRECT, [tool_claim.id]
            row.checks = dict.fromkeys(row.checks, True)
            if re.search(r"mae|平均绝对误差", " ".join(s.text for s in node.subjects), re.I) and re.search(r"当时|已经知道|已知", node.anchors[0].text):
                row.status = RELATED
                row.reasons.append("拿到的是事后评估，不能把它当成回放时刻已知的 MAE。")
            continue
        topics = _topics(node)
        if re.fullmatch(r"(?:那|它)?为什么(?:会)?受(?:潮汐|潮水)影响", normalize_input(node.anchors[0].text)):
            preceding = [n for n in result.question_graph.nodes if int(n.id[1:]) < int(node.id[1:])]
            if preceding and any(re.search(r"house\s*mill|三磨坊", s.text, re.I) for s in preceding[-1].subjects):
                topics.add("housemill_flood_context")
        explicit = make_request(result.question, fragment=node.anchors[0].text, node=node).source_types
        node_conflicts = [c for c in conflicts if c.get("concept") in topics and
                          (explicit is None or c.get("source_type") in explicit)]
        if node_conflicts:
            row.status = CONFLICT
            row.conflicts = list({str(c): c for c in node_conflicts}.values())
            row.reasons.append("同一主题存在多个有效版本，不能由模型自行选择。")
            continue
        # Internal Watch and thesis Caution may use different labels, but a
        # contradictory threshold must not be silently explained as an alias.
        threshold_sources = []
        if topics & {"internal_watch", "thesis_risk_terms"}:
            for item_id, (hit, quote) in verified.items():
                record = hit.record
                if (record["concept"] not in {"internal_watch", "thesis_risk_terms"}
                    or (explicit is not None and record["source_type"] not in explicit)):
                    continue
                line = next((line for line in quote.splitlines() if re.search(r"Watch|Caution", line)), "")
                number = re.search(r"\d+\.\d+", line)
                if number:
                    threshold_sources.append({"id": item_id, "version": record["version"], "locator": hit.locator,
                                              "threshold_m": str(Decimal(number.group()))})
            if len({source["threshold_m"] for source in threshold_sources}) > 1:
                row.status, row.conflicts = CONFLICT, threshold_sources
                row.reasons.append("来源中的 Caution／Watch 阈值不一致，不能由模型自行裁决。")
                continue
        for item_id, (hit, quote) in verified.items():
            record = hit.record
            profile = PROFILES.get(record["concept"])
            if record["concept"] not in topics or (explicit is not None and record["source_type"] not in explicit):
                continue
            row.candidate_ids.append(item_id)
            if row.status != DIRECT:
                row.status = RELATED
            row.checks.update(locator=True, integrity=True, version=bool(record.get("version")))
            if profile is None or node.requested_attribute not in profile.attributes:
                row.reasons.append("段落相关，但没有回答本项所求属性。")
                continue
            if hashlib.sha256(quote.encode("utf-8")).hexdigest() != REVIEWED_EXCERPTS.get(record["concept"]):
                row.reasons.append("这段原文不属于已复核的支持契约；新版本须重新核对，不能套用旧结论。")
                continue
            if re.search(r"尺寸|经纬度|功耗|多少钱|成本|价格|序列号|电池容量|保修|疏散|保证安全", node.anchors[0].text):
                row.reasons.append("所求的细分属性／现场决策不在该来源的可支持范围内。")
                continue
            if any(anchor.casefold() not in quote.casefold() for anchor in profile.anchors):
                row.reasons.append("原文未满足该事实的支持契约，不能靠目录摘要补全。")
                continue
            if re.search(r"当时已知|当时就知道|当时已经知道|当时可用|当时能查到", node.anchors[0].text):
                row.reasons.append("没有资料在回放时刻已可用的直接记录；核对／版本日期不能证明这一点。")
                continue
            if node.temporal_scope == "ambiguous_current" or (node.temporal_scope == "current_project" and node.requested_attribute == "placement"):
                row.reasons.append("历史资料不能证明当前设备位置或运行状态。")
                continue
            citation = Citation(item_id, hit.locator, record["version"], record["source_type"], quote, record.get("sha256"))
            claim = VerifiedClaim(f"{node.id}-{len(row.claim_ids)+1}", node.id, profile.text, (citation,))
            if not _valid_claim(claim):
                row.reasons.append("结论中的数字／单位与原文不一致，拒绝放行。")
                continue
            claims.append(claim)
            row.claim_ids.append(claim.id)
            row.status = DIRECT
            row.checks = dict.fromkeys(row.checks, True)
        if row.status == UNSUPPORTED:
            relevant_rejections = [reason for item_id, reason in rejected.items()
                                   if any(record["id"] == item_id and record["concept"] in topics
                                          for hit in result.document_candidates for record in (hit.record, *hit.equivalent_records))]
            label = ("MAE 评估指标（版本、样本范围、单位）" if any("mae" in s.text.casefold() for s in node.subjects) else
                     "英国官方定义的原文" if "official_flood_alert" in topics else "回答此属性所需的证据")
            row.reasons.extend(relevant_rejections or [f"还没有取得{label}。"])
        # Relationship approval happens below, only AFTER endpoint checks.
    by_node = {row.node_id: row for row in rows}
    for node in result.question_graph.nodes:
        row = by_node[node.id]
        if node.excluded or node.question_type not in ("relation", "comparison") or row.status == CONFLICT:
            continue
        endpoint_rows = [by_node.get(dep) for dep in node.depends_on]
        endpoints_ok = len(endpoint_rows) >= 2 and all(endpoint and endpoint.status in SUPPORTED for endpoint in endpoint_rows)
        subjects = normalize_input(" ".join(span.text for span in node.subjects))
        # A direct relationship source is necessary for project inheritance.
        if "housemill_project_connection" in _topics(node):
            direct = [claim for claim in claims if claim.node_id == node.id and
                      any("connection" in c.id for c in claim.citations)]
            if direct and endpoints_ok:
                if re.search(r"同一(?:套|台|批|个)|实体设备|硬件.*继承|继承.*设备|沿用.*设备|相同.*(?:设备|硬件)", node.anchors[0].text):
                    row.status = RELATED
                    row.reasons.append("资料只明确承接实现思路，没有证明沿用了同一套实体设备。")
                else:
                    row.status = DIRECT
                continue
            # Do not combine two endpoint observations into an inheritance claim.
            row.claim_ids = []
            claims = [claim for claim in claims if claim.node_id != node.id]
            row.status = RELATED if endpoints_ok else UNSUPPORTED
            row.reasons.append("两端事实不等于直接关系证据；旧项目与当前项目的承接仍需直接记载。")
            continue
        if endpoints_ok and re.search(r"预测峰值|预测最高", subjects) and re.search(r"mae|平均绝对误差", subjects):
            citations = tuple(dict.fromkeys(c for claim in claims if claim.node_id in node.depends_on for c in claim.citations))
            text = "峰值是一条预测里的最高水位；MAE 是事后对一批预测算出的平均误差，两者不是同一层级，不能拿它当这次峰值的误差，也不能用它直接构造这次预测的置信区间。"
            claim = VerifiedClaim(f"{node.id}-derive", node.id, text, citations, DERIVED,
                                  "一次预测的极值与批次绝对误差均值的定义／统计分母不同；不作单次误差或区间计算。")
            row.status, row.claim_ids = DERIVED, [claim.id]
            row.checks = dict.fromkeys(row.checks, True)
            claims.append(claim)
        elif row.status != DIRECT:
            row.status = RELATED if endpoints_ok else UNSUPPORTED
            row.reasons.append("缺少直接比较／关系材料；不能仅凭两个相关段落生成关系结论。")
    # Remove duplicate facts for the same node, but never hide source conflicts.
    unique = {}
    for claim in claims:
        key = (claim.node_id, claim.text, tuple(c.id for c in claim.citations))
        unique[key] = claim
    for row in rows:
        if row.status in SUPPORTED:
            row.reasons = []
        elif "official_flood_alert" in _topics(next(node for node in result.question_graph.nodes if node.id == row.node_id)):
            row.reasons.insert(0, "英国官方定义缺少可核对的原文。")
    result.verified_claims = list(unique.values())
    result.question_evidence = rows
    result.source_rejections = rejected
    result.trace.append({"step": "evidence_check", "status": "checked", "nodes": str(len(rows)),
                         "supported": str(sum(row.status in SUPPORTED for row in rows if not row.excluded)),
                         "conflicts": str(sum(row.status == CONFLICT for row in rows)),
                         "claim_ids": ",".join(claim.id for claim in result.verified_claims)})


def missing_items(result: QueryResult) -> list[QuestionEvidence]:
    return [row for row in result.question_evidence if not row.excluded and row.status not in SUPPORTED]


def render_checked_answer(result: QueryResult, *, selected: list[tuple[str, int]] | None = None) -> None:
    """Render only closed-world checked claims with citations beside each claim.

    An LLM may order claims or pick an approved phrasing; it cannot add text.
    """
    if not result.plan.steps:
        return
    claims = [claim for claim in result.verified_claims if _valid_claim(claim)]
    by_id = {claim.id: claim for claim in claims}
    if selected is not None:
        if (len(selected) != len(claims) or {cid for cid, _ in selected} != set(by_id)
                or any(type(choice) is not int or choice not in (0, 1) for _, choice in selected)):
            raise ValueError("模型的结论选择／引用映射未通过校验。")
        claims = [by_id[cid] for cid, _ in selected]
    gaps = missing_items(result)
    stop_note = {"tool_limit": "只读查询次数已用完，不再继续补查。",
                 "time_limit": "调查时间已用完，不再继续补查。",
                 "llm_limit": "模型调用次数已用完，使用已核验的表述。",
                 "round_limit": "已到调查轮数上限，不再继续补查。",
                 "no_support_gain": "补查没有取得能支持更多结论的资料，已停止重复查询。"}.get(result.investigation_stop_reason, "")
    if not claims:
        result.answer_text = None
        result.answer_status = "fallback"
        missing = "；".join(f"{row.node_id}：{row.question}（{'；'.join(row.reasons)}）" for row in gaps)
        result.fallback_text = "这次没有足够的已核验资料形成项目结论。缺少：" + (missing or "可核对的事实原文") + "。" + stop_note + "可以缩小问题或补充原始资料后再查。"
        result.trace.append({"step": "safe_fallback", "status": "no_verified_evidence"})
        return
    lines, seen = [], set()
    for claim in claims:
        key = (claim.text, tuple(c.id for c in claim.citations))
        if key in seen:
            continue
        seen.add(key)
        label = "有限推导：" if claim.status == DERIVED else ""
        citations = "、".join(c.id for c in claim.citations)
        choice = dict(selected or ()).get(claim.id, 0)
        prefix = "简单说，" if choice == 1 else ""
        lines.append(f"{label}{prefix}{claim.text}【{claim.id} → {citations}】")
    if gaps:
        lines.insert(0, "目前能确认的局部结论是：")
        for row in gaps:
            lines.append(f"尚不能确认 {row.node_id}‘{row.question}’：{'；'.join(reason.rstrip('。') for reason in dict.fromkeys(row.reasons))}。")
        lines.append("因此不能据此得出整个问题的完整结论。这里不是实时水情或官方警报。")
        if stop_note:
            lines.append(stop_note)
        result.answer_status = "partial_verified"
        result.trace.append({"step": "partial_answer", "status": "verified_subset", "missing_nodes": ",".join(r.node_id for r in gaps)})
    else:
        result.answer_status = "verified"
    if any(row.status == CONFLICT for row in gaps):
        lines.append("存在冲突的资料已列在证据区；请核对版本，不由模型自行裁决。")
    result.answer_text = "\n\n".join(lines)
    result.fallback_text = None


def verify_final_claims(result: QueryResult, root: Path) -> None:
    """Recheck citation ID/version/quote/hash after an optional model call."""
    records = {record["id"]: record for hit in result.document_candidates
               for record in (hit.record, *hit.equivalent_records)}
    valid, failed_nodes = [], set()
    for claim in result.verified_claims:
        okay = _valid_claim(claim)
        for citation in claim.citations:
            if citation.source_type in {"historical_forecast", "historical_observation", "evaluation_report"} and citation.id.startswith(("forecast:", "water:", "evaluation:")):
                # Frozen, validated tool output. Do not run a hidden extra SQL tool.
                if citation.source_type == "evaluation_report":
                    metric = result.evaluation
                    if not metric or citation.version != metric.generated_utc or citation.unit != "m":
                        okay = False
                    else:
                        try:
                            if hashlib.sha256(Path(metric.source_locator.split("#", 1)[0]).read_bytes()).hexdigest() != metric.source_sha256:
                                okay = False
                        except OSError:
                            okay = False
                continue
            record = records.get(citation.id)
            if not record or record["version"] != citation.version or record.get("sha256") != citation.sha256:
                okay = False
                continue
            try:
                locator, excerpt = _source_evidence(record, root)
                if locator != citation.locator or not excerpt or citation.quote not in excerpt or _injection(excerpt):
                    okay = False
            except (KnowledgeError, OSError, ValueError, KeyError):
                okay = False
        if okay:
            valid.append(claim)
        else:
            failed_nodes.add(claim.node_id)
    result.verified_claims = valid
    for row in result.question_evidence:
        if row.node_id in failed_nodes:
            row.status = UNSUPPORTED
            row.claim_ids = []
            row.reasons.append("最终引用／数字／单位／版本／源文件复核失败。")
    if result.question_graph:
        by_node = {row.node_id: row for row in result.question_evidence}
        for node in result.question_graph.nodes:
            if node.depends_on and any(by_node[dep].status not in SUPPORTED for dep in node.depends_on):
                row = by_node[node.id]
                if row.status in SUPPORTED:
                    row.status, row.claim_ids = RELATED, []
                    row.reasons.append("前置问题未完成复核，不能放行关系结论。")
                    result.verified_claims = [claim for claim in result.verified_claims if claim.node_id != node.id]


def finalize_answer(result: QueryResult, *, root: Path, provider=None, reserve_llm=None) -> None:
    """No free-form project prose is accepted from the model.

    Select/ordering JSON can only refer to already checked claims. Legacy
    providers can still be observed in tests, but their free text is discarded.
    """
    if not result.plan.steps:
        return
    verify_final_claims(result, root)
    selection = None
    if provider is not None and result.verified_claims:
        selector = getattr(provider, "select_claims", None)
        legacy = getattr(provider, "synthesize", None)
        if callable(selector) or (callable(legacy) and not missing_items(result) and not result.errors):
            try:
                if reserve_llm:
                    reserve_llm(provider)
                if callable(selector):
                    selection = selector(result.question, result.verified_claims)
                    # Validate the closed selection BEFORE emitting any text.
                    allowed = {claim.id for claim in result.verified_claims}
                    if (not isinstance(selection, list) or len(selection) != len(allowed)
                        or {cid for cid, choice in selection} != allowed
                        or any(type(choice) is not int or choice not in (0, 1) for cid, choice in selection)):
                        raise ValueError("模型选择了未知／重复结论或不允许的表达。")
                else:
                    legacy(result.question, result.evidence_bundle)
                    result.warnings.append("旧版自由文本生成结果未用于结论；已改用程序核验的表达。")
                result.trace.append({"step": "grounded_reply", "status": "checked_selection" if selection is not None else "deterministic"})
            except Exception as exc:
                # Budget failures are surfaced without making another API call.
                selection = None
                result.warnings.append(f"表达整理未完成（{type(exc).__name__}）；使用已核验的通俗表述。")
                result.trace.append({"step": "grounded_reply", "status": "rejected", "error_type": type(exc).__name__})
    verify_final_claims(result, root)
    if selection is not None and {cid for cid, choice in selection} != {c.id for c in result.verified_claims}:
        selection = None
    render_checked_answer(result, selected=selection)
    # Raw catalog summaries and unchecked explanations are not final facts.
    from .router import KnowledgeItem
    old_items = {item.citation_id: item for item in result.knowledge}
    source_texts: dict[str, list[str]] = {}
    citations: dict[str, Citation] = {}
    for claim in result.verified_claims:
        if claim.status != DIRECT:
            continue
        for citation in claim.citations:
            if citation.id in {record["id"] for hit in result.document_candidates for record in (hit.record, *hit.equivalent_records)}:
                source_texts.setdefault(citation.id, []).append(claim.text)
                citations[citation.id] = citation
    records = {record["id"]: record for hit in result.document_candidates for record in (hit.record, *hit.equivalent_records)}
    result.knowledge = []
    for item_id in sorted(source_texts, key=lambda cid: cid not in old_items):
        texts = source_texts[item_id]
        citation = citations[item_id]
        if item_id in old_items:
            result.knowledge.append(replace(old_items[item_id], fact=" ".join(dict.fromkeys(texts)), explanation=None))
        else:
            result.knowledge.append(KnowledgeItem(records[item_id]["title"], " ".join(dict.fromkeys(texts)),
                citation.source_type, item_id, citation.locator, version=citation.version, source_sha256=citation.sha256))
    result.trace.append({"step": "answer_validation", "status": result.answer_status or "none",
                         "claim_map": json.dumps({claim.id: [c.id for c in claim.citations]
                                                  for claim in result.verified_claims}, ensure_ascii=False)})
