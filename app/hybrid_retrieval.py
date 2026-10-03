"""Raw-request retrieval: BM25 and local TF-IDF/LSA, fused by ranks only.

Returns source-checked candidates, not a proof that any claim is supported.
No LLM calls, network search, mutable index, or additional Python dependency.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date
import hashlib
import math
from pathlib import Path
import re
from typing import Any

from .knowledge import (DEFAULT_CATALOG, ROOT, SOURCE_TYPES, Hit, NoEvidence,
                        SourceIntegrityError, VersionConflict, _load, _normal,
                        _semantic_rank, _snapshot_date, _source_evidence, _tokens)
from .question_graph import MAX_NODES, QuestionGraph, QuestionItem, normalize_input


RRF_K = 60
MAX_QUERIES = 5
CHANNEL_TOP_K = 5
MAX_HITS = 8
ALIASES = {
    "布点": "布置 位置", "安装点": "布置 位置", "摆放": "布置 位置",
    "摆在哪": "布置 位置", "摆在哪里": "布置 位置", "测水的东西": "传感器",
    "测水设备": "传感器 声纳", "水位设备": "传感器 声纳",
    "平均偏差": "平均绝对误差 MAE", "潮水": "潮汐",
    "caution": "Watch", "传感噐": "传感器", "怎摸": "怎么",
}
CONCEPT_CUES = {
    "internal_watch": ("watch", "caution", "观察等级", "留意等级"),
    "internal_warning": ("warning", "洪水线", "警告等级"),
    "official_flood_alert": ("flood alert", "floodalert", "官方预警", "官方"),
    "highwater_evaluation_limit": ("限制", "局限", "高水位", "检出", "召回"),
    "thesis_forecast_design": ("预测方法", "输入", "七天", "96", "数据"),
    "thesis_risk_terms": ("caution", "warning", "阈值", "等级"),
    "thesis_offline_events": ("离线", "历史事件", "86.19", "召回"),
    "thesis_live_limit": ("线上", "部署期", "真实洪水", "线上验证", "限制", "局限"),
    "thesis_rapid_rise": ("快速上涨", "快涨", "突然上涨", "滞后"),
    "thesis_model_design": ("patchtst", "模型", "分类头", "huber"),
    "housemill_heritage": ("house mill", "housemill", "三磨坊", "历史", "背景"),
    "housemill_flood_context": ("潮汐", "潮水", "木梁", "淹水", "进水"),
    "housemill_old_sensor": ("旧传感器", "旧声纳", "旧监测", "测水", "水位设备", "安装点", "传感器"),
    "housemill_study_findings": ("136", "42", "53分钟", "研究发现", "旧监测结果"),
    "housemill_volunteer_need": ("志愿者", "旧界面", "触水时长"),
    "housemill_project_connection": ("关系", "联系", "接续", "承接"),
}
DOMAIN_CUES = ("floodpred", "house mill", "housemill", "洪水", "水位", "传感器", "测水",
               "潮汐", "潮水", "布点", "安装点", "mae", "watch", "caution", "warning",
               "patchtst", "志愿者", "磨坊", "论文", "预测", "偏差", "误差", "声纳",
               "探头", "设备", "传感", "duncan", "flood alert", "项目", "阈值", "分级")


@dataclass(frozen=True)
class QueryVariant:
    role: str
    text: str
    weight: float


@dataclass(frozen=True)
class RetrievalRequest:
    original: str
    fragment: str
    queries: tuple[QueryVariant, ...]
    node_id: str | None = None
    source_types: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RetrievalResult:
    request: RetrievalRequest
    hits: tuple[Hit, ...]
    eligible_count: int
    verified_count: int
    quarantined: tuple[dict[str, str], ...] = ()
    version: str | None = None
    checked_on_or_before: str | None = None
    conflicts: tuple[dict, ...] = ()

    def to_debug(self) -> dict[str, Any]:
        return {
            "node_id": self.request.node_id,
            "queries": [vars(query) for query in self.request.queries],
            "filters": {"source_types": self.request.source_types, "version": self.version,
                        "checked_on_or_before": self.checked_on_or_before},
            "time_note": "核对日期不是发表日期；没有证明资料在回放时刻已可用。",
            "eligible_count": self.eligible_count, "verified_count": self.verified_count,
            "quarantined": self.quarantined,
            "conflicts": self.conflicts,
            "candidates": [{"id": hit.record["id"], "concept": hit.record["concept"],
                            "equivalent_record_ids": [record["id"] for record in hit.equivalent_records],
                            "equivalent_concepts": [record["concept"] for record in hit.equivalent_records],
                            "source_type": hit.record["source_type"], "version": hit.record["version"],
                            "locator": hit.locator, "sha256": hit.record.get("sha256"),
                            "origin_sha256": hit.record.get("origin_sha256"),
                            "origin_url": hit.record.get("origin_url"),
                            "pdf_pages": hit.record.get("pdf_pages"),
                            "checked_on": hit.record.get("checked_on"),
                            "filter_snapshot_date": (_snapshot_date(hit.record).isoformat()
                                                     if _snapshot_date(hit.record) else None),
                            "filter_date_basis": ("checked_on" if hit.record.get("checked_on") else
                                                  "version_stamp" if _snapshot_date(hit.record) else "unknown"),
                            "published_on": hit.record.get("published_on"),
                            "has_local_excerpt": hit.excerpt is not None,
                            "fusion_score": hit.fusion_score, "votes": hit.ranking_trace}
                           for hit in self.hits],
            "support_status": "candidate_only",
        }


def _explicit_sources(fragment: str, node: QuestionItem | None) -> tuple[str, ...] | None:
    q = normalize_input(fragment)
    def positive(pattern: str) -> bool:
        return any(not re.search(r"(?:不是|并非|不要|不用|不查|不在|不需要|别查)[^，,。;；?!！？]{0,6}$",
                                 q[:match.start()]) for match in re.finditer(pattern, q))
    paper = positive(r"论文|dissertation|thesis")
    internal = positive(r"内部")
    official = positive(r"官方|flood\s*alert")
    # Only an explicit user source qualifier is a hard filter. Inferred "old"
    # context never restricts candidates to prior_project.
    if re.search(r"wilson|zhang|cupum", q) and paper:
        return ("research_paper",)
    if positive(r"毕业论文|毕业设计|dissertation|thesis"):
        return ("thesis",)
    if paper:
        return ("thesis", "research_paper")
    if positive(r"github|旧项目仓库|早期项目仓库"):
        return ("prior_project",)
    if node and node.source_scope == "internal" and internal:
        return ("internal_project",)
    if node and node.source_scope == "official" and official:
        return ("official_public_guidance",)
    if internal and official:
        return None
    if internal:
        return ("internal_project",)
    if official:
        return ("official_public_guidance",)
    return None


def make_request(original: str, *, fragment: str | None = None,
                 node: QuestionItem | None = None, supplementary: str | None = None,
                 source_types: tuple[str, ...] | None = None) -> RetrievalRequest:
    if not isinstance(original, str) or not original.strip() or len(original) > 500:
        raise ValueError("原话须为 1–500 字。")
    fragment = fragment or original
    if fragment not in original:
        raise ValueError("问题片段必须逐字来自原话。")
    queries = [QueryVariant("original", original, 0.8), QueryVariant("fragment", fragment, 1.0)]
    # Two deterministic rewrites: allowlisted alias expansion and extractive
    # subject/predicate/qualifier focus, never model-generated facts.
    expansions = [expansion for alias, expansion in ALIASES.items() if alias in normalize_input(fragment)]
    if expansions:
        queries.append(QueryVariant("rewrite", fragment + " " + " ".join(dict.fromkeys(expansions)), 0.65))
    if node:
        if any(span.text != original[span.start:span.end]
               for span in (*node.subjects, *node.requested_spans, *node.qualifiers)):
            raise ValueError("改写所用的对象和限定词必须定位回本次原话。")
        focused = " ".join(dict.fromkeys(span.text for span in (*node.subjects, *node.requested_spans, *node.qualifiers)))
        if focused:
            queries.append(QueryVariant("rewrite", focused, 0.65))
    if supplementary:
        queries.append(QueryVariant("supplementary", supplementary, 0.25))
    unique: dict[str, QueryVariant] = {}
    for query in queries:
        key = normalize_input(query.text)
        previous = unique.get(key)
        if previous is None or query.weight > previous.weight:
            unique[key] = query
    # Identical raw and fragment queries are executed once; the role says both.
    if normalize_input(original) == normalize_input(fragment):
        key = normalize_input(original)
        unique[key] = QueryVariant("original+fragment", fragment, 1.0)
    if source_types is None:
        source_types = _explicit_sources(fragment, node)
    if source_types is not None and (not source_types or any(kind not in SOURCE_TYPES for kind in source_types)):
        raise ValueError("来源类型过滤无效。")
    return RetrievalRequest(original, fragment, tuple(unique.values())[:MAX_QUERIES],
                            node.id if node else None, source_types)


def request_for_concept(question: str, graph: QuestionGraph, concept: str,
                        supplementary: str) -> RetrievalRequest:
    cues = CONCEPT_CUES.get(concept, ())
    nodes = [node for node in graph.nodes if not node.excluded]
    def score(node: QuestionItem) -> int:
        subjects = " ".join(normalize_input(span.text) for span in node.subjects)
        anchor = " ".join(normalize_input(span.text) for span in node.anchors)
        return sum(4 for cue in cues if cue in subjects) + sum(1 for cue in cues if cue in anchor)
    # A relationship concept uses the relationship node, rather than a single
    # endpoint; unrelated clauses are not allowed to impose its source filter.
    if concept == "housemill_project_connection":
        relations = [node for node in nodes if node.question_type == "relation"]
        if relations:
            nodes = relations
    node = max(nodes, key=score, default=None)
    if node is not None and score(node) == 0:
        node = None
    fragment = node.anchors[0].text if node else question
    return make_request(question, fragment=fragment, node=node, supplementary=supplementary)


def document_requests(graph: QuestionGraph) -> tuple[RetrievalRequest, ...]:
    """At most six question-node requests; pure numerical nodes stay in tools.

    Unmapped document questions still produce candidates, not new factual
    answers. A resolved pronoun may use its earlier raw subject in a rewrite.
    """
    requests = []
    seen = set()
    numeric = re.compile(r"^(?:预测峰值|预测最高水位|预测最高|(?:历史|当时|实测|观测|声纳)水位|mae|平均绝对误差)$", re.I)
    for node in graph.nodes:
        if node.excluded:
            continue
        subjects = [normalize_input(span.text) for span in node.subjects]
        if subjects and all(numeric.fullmatch(subject) for subject in subjects):
            continue
        fragment = node.anchors[0].text
        context = normalize_input(fragment + " " + " ".join(subjects))
        if not any(cue in context for cue in DOMAIN_CUES):
            continue
        request = make_request(graph.original_text, fragment=fragment, node=node)
        signature = (request.source_types, tuple(query.text for query in request.queries))
        if signature not in seen:
            requests.append(request)
            seen.add(signature)
    return tuple(requests[:MAX_NODES])


def _document_text(record: dict[str, Any], excerpt: str | None) -> str:
    return " ".join((record["title"], record["section"], record["summary"], *record["keywords"], excerpt or ""))


def candidate_key(hit: Hit) -> tuple:
    record = hit.record
    return (record["source_type"], record["version"], record["source_ref"], record.get("sha256"),
            record.get("line_start"), record.get("line_end"), tuple(record.get("pdf_pages", ())),
            record["section"] if hit.excerpt is None else None)


def merge_candidates(existing: list[Hit], additions: tuple[Hit, ...] | list[Hit]) -> None:
    """Deduplicate the same source paragraph without losing its concept IDs."""
    indices = {candidate_key(hit): index for index, hit in enumerate(existing)}
    for hit in additions:
        key = candidate_key(hit)
        if key not in indices:
            indices[key] = len(existing)
            existing.append(hit)
            continue
        index = indices[key]
        previous = existing[index]
        records = {record["id"]: record for record in (*previous.equivalent_records,
                                                       hit.record, *hit.equivalent_records)
                   if record["id"] != previous.record["id"]}
        existing[index] = replace(previous, equivalent_records=tuple(records.values()))


def _bm25(query: str, records: list[dict[str, Any]], texts: list[str]) -> dict[str, float]:
    documents = [Counter(_tokens(normalize_input(text))) for text in texts]
    average_length = sum(sum(document.values()) for document in documents) / max(1, len(documents))
    document_frequency = Counter(term for document in documents for term in document)
    terms = set(_tokens(normalize_input(query)))
    scores = {}
    for record, document in zip(records, documents):
        length = sum(document.values())
        score = 0.0
        for term in terms:
            frequency = document[term]
            if frequency:
                idf = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                score += idf * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / max(1, average_length)))
        # Exact aliases improve lexical ordering, not semantic support status.
        aliases = [len(_normal(alias)) for alias in record["keywords"]
                   if _normal(alias) and _normal(alias) in _normal(query)]
        scores[record["id"]] = score + max(aliases, default=0) / 5
    return scores


def reciprocal_rank_fusion(rankings: list[tuple[str, str, float, list[tuple[str, float]]]]) -> tuple[dict[str, float], dict[str, list[dict]]]:
    scores: dict[str, float] = {}
    votes: dict[str, list[dict]] = {}
    for query_id, channel, weight, ranking in rankings:
        for rank, (item_id, raw_score) in enumerate(ranking, 1):
            contribution = weight / (RRF_K + rank)
            scores[item_id] = scores.get(item_id, 0.0) + contribution
            votes.setdefault(item_id, []).append({"query_id": query_id, "channel": channel,
                                                  "rank": rank, "weight": weight,
                                                  "raw_score": raw_score, "contribution": contribution})
    return scores, votes


def hybrid_search(request: RetrievalRequest, *, version: str | None = None,
                  checked_on_or_before: str | None = None, top_k: int = MAX_HITS,
                  allow_conflicts: bool = False,
                  catalog: Path = DEFAULT_CATALOG, root: Path = ROOT) -> RetrievalResult:
    if not 1 <= top_k <= MAX_HITS:
        raise ValueError("top_k 须为 1–8。")
    cutoff = date.fromisoformat(checked_on_or_before) if checked_on_or_before else None
    eligible = [record for record in _load(catalog) if record["status"] == "active"
                and (request.source_types is None or record["source_type"] in request.source_types)
                and (version is None or record["version"] == version)
                and (cutoff is None or (snapshot := _snapshot_date(record)) is not None and snapshot <= cutoff)]
    primary_context = " ".join(normalize_input(query.text) for query in request.queries
                               if query.role != "supplementary")
    if not any(cue in primary_context for cue in DOMAIN_CUES):
        raise NoEvidence("原话没有可识别的项目资料线索；预设词不能单独制造命中。")
    grounded, records, texts, quarantined = {}, [], [], []
    for record in eligible:
        try:
            locator, excerpt = _source_evidence(record, root)
        except (SourceIntegrityError, OSError, KeyError, ValueError) as exc:
            quarantined.append({"id": record["id"], "concept": record["concept"], "error_type": type(exc).__name__})
            continue
        grounded[record["id"]] = (locator, excerpt)
        records.append(record)
        texts.append(_document_text(record, excerpt))
    if not records:
        if quarantined:
            raise SourceIntegrityError("所有符合过滤条件的来源均缺失或未通过完整性检查。")
        raise NoEvidence("来源、版本或核对日期过滤后没有候选资料。")
    # Unknown technical names in the focused question cannot be erased by the
    # preset supplementary query. Application context and greetings are ignored.
    # Known endpoints may need separate documents (MAE + Watch, internal +
    # official). Requiring all of them in every candidate loses this evidence.
    # Unknown ASCII identifiers, however, cannot be replaced by preset concepts.
    contextual = {"floodpred", "hello", "please", "tell", "about", "what", "how", "the", "and", "project",
                  "mae", "watch", "caution", "warning", "patchtst", "flood", "alert", "floodalert",
                  "house", "mill", "housemill", "duncan", "wilson", "zhang", "cupum",
                  "github", "thesis", "dissertation"}
    identifiers = re.findall(r"\b(?:[A-Z][A-Z0-9_]{2,}|[A-Z][a-z]+[A-Z][a-zA-Z]*)\b", request.fragment)
    if re.search(r"\bsop\b", request.fragment, re.I):
        identifiers.append("sop")
    required = {word.casefold() for word in identifiers} - contextual
    semantic_records = [{**record, "summary": text} for record, text in zip(records, texts)]
    rankings = []
    for index, query in enumerate(request.queries):
        lexical = _bm25(query.text, records, texts)
        semantic = _semantic_rank(query.text, semantic_records)
        for channel, values, minimum in (("keyword", lexical, 0.0), ("semantic_candidate", semantic, 0.30)):
            ranking = [(record["id"], values.get(record["id"], 0.0)) for record, text in zip(records, texts)
                       if values.get(record["id"], 0.0) > minimum
                       and all(word in normalize_input(text) for word in required)]
            ranking.sort(key=lambda item: (-item[1], item[0]))
            rankings.append((f"{index}:{query.role}", channel, query.weight, ranking[:CHANNEL_TOP_K]))
    fused, votes = reciprocal_rank_fusion(rankings)
    if not fused:
        raise NoEvidence("原话及受约束改写没有召回可定位资料；不凭预设词补出答案。")
    versions: dict[tuple[str, str], set[str]] = {}
    matched = {(record["source_type"], record["concept"]) for record in records if record["id"] in fused}
    for record in eligible:
        key = record["source_type"], record["concept"]
        if key in matched:
            versions.setdefault(key, set()).add(record["version"])
    conflict_keys = {key for key, choices in versions.items() if len(choices) > 1}
    if conflict_keys and not allow_conflicts:
        raise VersionConflict("候选概念存在多个有效版本；请明确版本或人工核对。")
    conflicts = tuple({"concept": record["concept"], "source_type": record["source_type"],
                       "id": record["id"], "version": record["version"],
                       "locator": grounded[record["id"]][0], "reason": "multiple_active_versions"}
                      for record in records if (record["source_type"], record["concept"]) in conflict_keys)
    by_id = {record["id"]: record for record in records}
    hits = []
    for item_id in sorted(fused, key=lambda item: (-fused[item], item)):
        record = by_id[item_id]
        locator, excerpt = grounded[item_id]
        evidence = votes[item_id]
        lexical_scores = [vote["raw_score"] for vote in evidence if vote["channel"] == "keyword"]
        semantic_scores = [vote["raw_score"] for vote in evidence if vote["channel"] == "semantic_candidate"]
        hits.append(Hit(record, int(max(lexical_scores, default=0)), locator, excerpt,
                        tuple(dict.fromkeys(vote["channel"] for vote in evidence)),
                        max(semantic_scores, default=None), fused[item_id], tuple(evidence)))
    paragraphs: list[Hit] = []
    merge_candidates(paragraphs, hits)
    return RetrievalResult(request, tuple(paragraphs[:top_k]), len(eligible), len(records), tuple(quarantined),
                           version, checked_on_or_before, conflicts)


def safe_trace(result: RetrievalResult) -> dict[str, str]:
    """Persist counters/hashes, never raw queries, rewrites, or source bodies."""
    return {"step": "hybrid_retrieval", "status": "candidates", "support_status": "candidate_only",
            "node_id": result.request.node_id or "", "queries": str(len(result.request.queries)),
            "query_roles": ",".join(query.role for query in result.request.queries),
            "query_hashes": ",".join(hashlib.sha256(query.text.encode("utf-8")).hexdigest() for query in result.request.queries),
            "source_types": ",".join(result.request.source_types or ()),
            "candidate_ids": ",".join(hit.record["id"] for hit in result.hits),
            "eligible_count": str(result.eligible_count), "quarantined_count": str(len(result.quarantined))}
