"""Extractive question planning. Graphs are requests, never factual answers.

Keep raw character spans, derive qualifiers in Python, and accept only bounded
model proposals anchored to the input. This module never retrieves or executes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
from typing import Any
import unicodedata


MAX_NODES = 6
KINDS = frozenset({"fact", "definition", "relation", "comparison", "cause"})
RELATION = re.compile(r"关系|联系|关联|区别|比较|是否一样|相同|一样|不等于|等于|不是|继承|沿用|同一(?:套|台|批|个)", re.I)
PREDICATE = re.compile(r"怎么布置|如何布置|布置|安装|放哪\S{0,2}|在哪里|在哪儿|哪里|位置|"
                       r"有什么关系|什么关系|联系|关联|有什么区别|区别|是否一样|比较|继承|沿用|同一(?:套|台|批|个)|"
                       r"为什么|为何|怎么预测|如何预测|方法|结构|是多少|多少|多高|"
                       r"是什么|什么意思|含义|定义|局限|限制|准不准", re.I)
QUALIFIER = re.compile(r"不是|不能|不要|不用|不需要|并非|没有|别查|未验证|不|"
                       r"当时|历史|回放|过去|旧|早期|以前|现在|目前|当前|实时|"
                       r"内部|官方|论文|毕业论文|高水位|2\s*(?:米|m)以上|"
                       r"\d{4}-\d{2}-\d{2}(?:T[\d:]+Z)?", re.I)
ENTITY = re.compile(
    r"FloodPred\s*(?:项目)?(?:的)?\s*(?:预测方法|预测模型|预测|模型)|"
    r"(?:旧|新|早期|以前的|原来的)\s*(?:传感器|声纳|水位仪|监测设备|监测系统|系统)|"
    r"(?:现在的|现有的|目前的)?\s*(?:预测峰值|预测最高水位|预测最高|预测方法|预测)|"
    r"(?:历史|当时|实测|观测|声纳)水位|平均绝对误差|MAE|Caution|Watch|Warning|"
    r"Flood\s*Alert|官方预警|英国官方预警|FloodPred|House\s*Mill|三磨坊|"
    r"PatchTST|Duncan\s*Wilson|\d+(?:\.\d+)?\s*(?:次|分钟)|模型|论文|志愿者|它们|它|这个设备|这套系统", re.I)


class GraphValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSpan:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Coreference:
    mention: SourceSpan
    candidates: tuple[SourceSpan, ...]
    status: str  # resolved or ambiguous


@dataclass(frozen=True)
class QuestionItem:
    id: str
    anchors: tuple[SourceSpan, ...]
    subjects: tuple[SourceSpan, ...]
    requested_attribute: str
    question_type: str
    temporal_scope: str
    source_scope: str
    qualifiers: tuple[SourceSpan, ...]
    requested_spans: tuple[SourceSpan, ...] = ()
    depends_on: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    excluded: bool = False


@dataclass(frozen=True)
class QuestionGraph:
    original_text: str
    normalized_text: str
    nodes: tuple[QuestionItem, ...]
    coreferences: tuple[Coreference, ...] = ()
    clarifications: tuple[str, ...] = ()
    origin: str = "local"
    validation_notes: tuple[str, ...] = ()

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarifications)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "needs_clarification": self.needs_clarification}


def normalize_input(text: str) -> str:
    """Normalize a copy, preserving negation, numbers, time and authority words."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


def _span(text: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(start, end, text[start:end])


def _unique(spans: list[SourceSpan]) -> tuple[SourceSpan, ...]:
    seen: set[str] = set()
    result = []
    for span in spans:
        key = normalize_input(span.text)
        if key not in seen:
            seen.add(key)
            result.append(span)
    return tuple(result)


def _clauses(text: str) -> list[SourceSpan]:
    # Decimal points are not sentence boundaries. The original spans survive.
    result = []
    for match in re.finditer(r"[^，,；;。！？!?\n]+", text):
        start, end = match.span()
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        fragment = text[start:end]
        # Split coordinated commands, while keeping entity conjunctions in a
        # relation intact ("峰值和MAE" is not two disconnected sentences).
        boundaries = [start] + [start + boundary.start() for boundary in re.finditer(
            r"(?:并且|同时|另外|然后|以及|但是|但|而|并|再|只)(?=(?:请|帮我)?(?:查|看|解释|介绍|告诉|回答|了解|说))",
            fragment)] + [end]
        for left, right in zip(boundaries, boundaries[1:]):
            part = text[left:right].strip()
            if part and not re.fullmatch(r"你好|您好|嗨|hello|hi|对吗|是吗|好吗|可以吗|谢谢|感谢|请解释一下", part, re.I):
                result.append(_span(text, left, right))
    return result


def _entity_spans(text: str, anchor: SourceSpan) -> list[SourceSpan]:
    normalized = []
    offsets = []
    for index, char in enumerate(anchor.text):
        for expanded in unicodedata.normalize("NFKC", char).casefold():
            normalized.append(expanded)
            offsets.append(index)
    found = [_span(text, anchor.start + offsets[match.start()], anchor.start + offsets[match.end() - 1] + 1)
             for match in ENTITY.finditer("".join(normalized))]
    if len(found) > 1:
        # Author and document names are often context, not additional questions.
        found = [item for item in found if normalize_input(item.text) not in ("论文", "duncan wilson")]
    return found


def _attribute(anchor: str, kind: str) -> str:
    if kind == "relation":
        return "connection"
    if kind == "comparison":
        return "comparison"
    if kind == "cause":
        return "cause"
    if re.search(r"布置|安装|放哪|在哪|哪里|位置", anchor):
        return "placement"
    if re.search(r"怎么预测|如何预测|方法|结构|设计", anchor):
        return "method"
    if re.search(r"多少|多高|数值|run_id|风险卡", anchor, re.I):
        return "value"
    if re.search(r"局限|限制|不足|短板", anchor):
        return "limitations"
    return "meaning_and_scope"


def _scopes(anchor: SourceSpan, subjects: tuple[SourceSpan, ...]) -> tuple[str, str]:
    q = normalize_input(anchor.text)
    names = " ".join(normalize_input(item.text) for item in subjects)
    if re.search(r"旧|早期|以前|原来", names) or re.search(r"当时|历史|回放|过去|以前|早期|原来", q):
        temporal = "historical"
    elif re.search(r"实时|当前|现在|目前", q):
        temporal = "current_project" if re.search(r"方法|模型|结构|设计|floodpred", q) else "ambiguous_current"
    else:
        temporal = "unspecified"
    scope_text = q
    if len(subjects) == 1 and anchor.start <= subjects[0].start:
        subject = subjects[0]
        prefix = anchor.text[:subject.end - anchor.start]
        authorities = list(re.finditer(r"内部|官方", prefix))
        if authorities:
            scope_text = authorities[-1].group() + " " + normalize_input(subject.text)
    internal = "内部" in scope_text
    official = "官方" in scope_text or "flood alert" in scope_text or "floodalert" in scope_text
    source = "mixed" if internal and official else ("internal" if internal else "official" if official else
             "thesis" if "论文" in q else "prior_project" if re.search(r"旧|早期", names) else "unspecified")
    return temporal, source


def _make_item(text: str, anchor: SourceSpan, subjects: tuple[SourceSpan, ...], kind: str,
               item_id: str = "", dependencies: tuple[str, ...] = ()) -> QuestionItem:
    qualifiers = tuple(_span(text, anchor.start + match.start(), anchor.start + match.end())
                       for match in QUALIFIER.finditer(anchor.text))
    attribute = _attribute(anchor.text, kind)
    temporal, source = _scopes(anchor, subjects)
    requirements = ["locatable_source", "matching_scope"]
    if kind in ("relation", "comparison"):
        requirements += ["both_endpoints", "explicit_bridge_or_labeled_derivation"]
    if any(normalize_input(subject.text) in ("mae", "平均绝对误差") for subject in subjects):
        requirements += ["metric_definition", "evaluation_version", "sample_scope", "unit",
                         "aggregate_mae_is_not_single_peak_error"]
        if len(subjects) == 1:
            temporal = "evaluation_snapshot" if attribute == "value" else "conceptual"
        elif temporal == "historical":
            temporal = "mixed_historical_and_evaluation"
        requirements.append("hindsight_not_available_at_replay")
    if temporal == "historical":
        requirements.append("historical_not_current")
    requested = tuple(_span(text, anchor.start + match.start(), anchor.start + match.end())
                      for match in PREDICATE.finditer(anchor.text))
    return QuestionItem(item_id, (anchor,), subjects, attribute, kind, temporal, source,
                        qualifiers, requested, dependencies, tuple(requirements), _explicit_exclusion(anchor.text))


def _explicit_exclusion(text: str) -> bool:
    # A quoted/mentioned instruction is data ("别人说不要查MAE是什么意思").
    return bool(re.match(r"^(?:请|麻烦|这次|先|我希望|希望你|我想|我|帮我|你|还是|只|并|再|然后|另外|同时|但是|但|而)*"
                         r"(?:不要|不用|别查|不需要)", text.strip()))


def _merge_nodes(items: list[QuestionItem]) -> tuple[QuestionItem, ...]:
    result: list[QuestionItem] = []
    mapping: dict[str, str] = {}
    keys: dict[tuple, int] = {}
    for item in items:
        derived_scopes = bool(item.depends_on) and item.question_type in ("relation", "comparison", "cause")
        atomic_kind = "atomic" if item.question_type in ("fact", "definition") else item.question_type
        key = (tuple(normalize_input(span.text) for span in item.subjects), item.requested_attribute,
               atomic_kind, "dependency_scopes" if derived_scopes else item.temporal_scope,
               "dependency_scopes" if derived_scopes else item.source_scope, item.excluded,
               tuple(span.text for span in item.qualifiers if re.search(r"不是|不能|并非|没有|未", span.text)))
        if key in keys:
            index = keys[key]
            previous = result[index]
            mapping[item.id] = previous.id
            result[index] = replace(previous, anchors=tuple(dict.fromkeys(previous.anchors + item.anchors)),
                                    qualifiers=tuple(dict.fromkeys(previous.qualifiers + item.qualifiers)))
        else:
            item_id = f"q{len(result) + 1}"
            mapping[item.id] = item_id
            keys[key] = len(result)
            result.append(replace(item, id=item_id))
    # Map old IDs after deduplication. All dependencies must point backwards.
    for index, item in enumerate(result):
        original = next(candidate for candidate in items if mapping[candidate.id] == item.id)
        if any(dep not in mapping for dep in original.depends_on):
            raise GraphValidationError("依赖指向了不存在的问题项。")
        dependencies = tuple(dict.fromkeys(mapping[dep] for dep in original.depends_on))
        if item.id in dependencies or any(int(dep[1:]) >= index + 1 for dep in dependencies):
            raise GraphValidationError("循环依赖或前向依赖。")
        endpoint_nodes = [result[int(dep[1:]) - 1] for dep in dependencies]
        temporal = item.temporal_scope
        source = item.source_scope
        if len(endpoint_nodes) >= 2:
            if len({endpoint.temporal_scope for endpoint in endpoint_nodes}) > 1:
                temporal = "mixed"
            if len({endpoint.source_scope for endpoint in endpoint_nodes}) > 1:
                source = "mixed"
        result[index] = replace(item, depends_on=dependencies, temporal_scope=temporal, source_scope=source)
    return tuple(result)


def build_question_graph(text: str) -> QuestionGraph:
    if not isinstance(text, str) or not text.strip() or len(text) > 500:
        raise GraphValidationError("问题须为 1–500 字的非空文字。")
    items: list[QuestionItem] = []
    references: list[Coreference] = []
    clarifications: list[str] = []
    previous: tuple[SourceSpan, ...] = ()
    for anchor in _clauses(text):
        if re.search(r"(?:不是|并非|没有说).{0,4}(?:不要|不用|不需要|别查)", anchor.text):
            clarifications.append("这句话有多层否定。你希望查询这项资料，还是排除它？")
        subjects: list[SourceSpan] = []
        for mention in _entity_spans(text, anchor):
            if mention.text in ("它", "它们", "这个设备", "这套系统"):
                candidates = _unique(subjects) or previous
                resolved = len(candidates) == 1 or (mention.text == "它们" and bool(candidates))
                references.append(Coreference(mention, candidates, "resolved" if resolved else "ambiguous"))
                if resolved:
                    subjects.extend(candidates)
                else:
                    clarifications.append(f"你说的‘{mention.text}’具体指哪个对象？")
                continue
            subjects.append(mention)
        chosen = _unique(subjects)
        if not chosen:
            # An anchored unknown subject can later be refined by DeepSeek.
            if not references or references[-1].status != "ambiguous":
                items.append(_make_item(text, anchor, (anchor,), "fact", f"l{len(items)}"))
            continue
        relation = bool(RELATION.search(anchor.text))
        kind = "comparison" if re.search(r"区别|比较|是否一样|相同|一样|不等于|等于|不是", anchor.text) else "relation"
        endpoint_ids = []
        for subject in chosen:
            prior = next((item for item in reversed(items)
                          if item.subjects == (subject,) and not item.excluded
                          and item.question_type in ("fact", "definition")), None)
            if relation and subject.start < anchor.start and prior:
                endpoint_ids.append(prior.id)
                continue
            item_id = f"l{len(items)}"
            item = _make_item(text, anchor, (subject,), "definition" if relation else "fact", item_id)
            # A relationship request asks for endpoint meanings, not invented values.
            if relation:
                attribute = "method" if re.search(r"方法|结构|设计", subject.text) else "meaning_and_scope"
                item = replace(item, requested_attribute=attribute)
            items.append(item)
            endpoint_ids.append(item_id)
        if relation and len(chosen) >= 2:
            items.append(_make_item(text, anchor, chosen, kind, f"l{len(items)}", tuple(endpoint_ids)))
        elif relation:
            clarifications.append("你想比较或联系哪两个对象？")
        elif re.search(r"为什么|为何", anchor.text):
            items.append(_make_item(text, anchor, chosen, "cause", f"l{len(items)}", tuple(endpoint_ids)))
        if any(item.temporal_scope == "ambiguous_current" for item in items if item.anchors == (anchor,)):
            clarifications.append("你说的‘现在’是指 FloodPred 的预测方法，还是当前实时水情或设备状态？")
        previous = chosen
    nodes = _merge_nodes(items)
    if len(nodes) > MAX_NODES:
        clarifications.append("这次涉及的内容有点多，请先选两三个最想了解的问题。")
        nodes = nodes[:MAX_NODES]
    return QuestionGraph(text, normalize_input(text), nodes, tuple(references),
                         tuple(dict.fromkeys(clarifications)))


def _find_phrase(text: str, phrase: object, anchor: SourceSpan | None = None) -> SourceSpan:
    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 500:
        raise GraphValidationError("原文片段格式无效。")
    start, end = (anchor.start, anchor.end) if anchor else (0, len(text))
    matches = list(re.finditer(re.escape(phrase), text[start:end]))
    if not matches:
        # Only orthographic normalization, never fuzzy semantic substitution.
        # Every accepted position is mapped back to exact raw characters.
        normalized, offsets = [], []
        for index, char in enumerate(text[start:end]):
            for expanded in unicodedata.normalize("NFKC", char).casefold():
                if not expanded.isspace():
                    normalized.append(expanded)
                    offsets.append(index)
        needle = re.sub(r"\s+", "", normalize_input(phrase))
        normalized_matches = list(re.finditer(re.escape(needle), "".join(normalized))) if needle else []
        if len(normalized_matches) == 1:
            match = normalized_matches[0]
            return _span(text, start + offsets[match.start()], start + offsets[match.end() - 1] + 1)
    if len(matches) != 1:
        raise GraphValidationError("片段须逐字存在且定位唯一；重复片段请使用更完整的原文。")
    match = matches[0]
    return _span(text, start + match.start(), start + match.end())


def checked_question_graph(text: str, proposal: object) -> QuestionGraph:
    """A model may refine grouping, but cannot erase local qualifiers/ambiguity."""
    local = build_question_graph(text)
    if not isinstance(proposal, dict) or set(proposal) != {"nodes"}:
        raise GraphValidationError("问题图必须只包含 nodes。")
    values = proposal["nodes"]
    if not isinstance(values, list) or not 0 <= len(values) <= MAX_NODES:
        raise GraphValidationError("问题项数量超过上限。")
    model_items = []
    ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"id", "matched_phrase", "subjects", "kind", "depends_on"}:
            raise GraphValidationError("问题项字段不符合结构。")
        item_id, kind = value["id"], value["kind"]
        dependencies, subjects = value["depends_on"], value["subjects"]
        if (not isinstance(item_id, str) or not re.fullmatch(r"q[1-6]", item_id) or item_id in ids
            or not isinstance(kind, str) or kind not in KINDS or not isinstance(dependencies, list)
            or any(not isinstance(dep, str) or dep not in ids for dep in dependencies)
            or not isinstance(subjects, list) or not 1 <= len(subjects) <= 4):
            raise GraphValidationError("问题项 ID、类型、对象或依赖无效。")
        anchor = _find_phrase(text, value["matched_phrase"])
        extracted = []
        for phrase in subjects:
            try:
                subject = _find_phrase(text, phrase, anchor)
            except GraphValidationError:
                subject = _find_phrase(text, phrase)
                if not any(ref.status == "resolved" and anchor.start <= ref.mention.start < anchor.end
                           and subject in ref.candidates for ref in local.coreferences):
                    raise GraphValidationError("跨片段对象没有已确认的代词指向。")
            reference = next((ref for ref in local.coreferences if ref.mention == subject), None)
            if reference:
                if reference.status == "resolved":
                    extracted.extend(reference.candidates)
                continue
            extracted.append(subject)
        if not extracted:
            continue
        if kind in ("relation", "comparison") and (not RELATION.search(anchor.text) or len(dependencies) < 2):
            raise GraphValidationError("关系问题须有原文关系词和至少两个依赖。")
        if kind in ("relation", "comparison"):
            endpoints = {normalize_input(span.text) for previous in model_items
                         if previous.id in {"m" + dep for dep in dependencies} for span in previous.subjects}
            if len(extracted) < 2 or not {normalize_input(span.text) for span in extracted} <= endpoints:
                raise GraphValidationError("关系对象与依赖节点的对象不一致。")
        if kind == "cause" and not re.search(r"为什么|为何|原因", anchor.text):
            raise GraphValidationError("原因问题缺少原文依据。")
        item = _make_item(text, anchor, _unique(extracted), kind, "m" + item_id,
                          tuple("m" + dep for dep in dependencies))
        if kind in ("fact", "definition") and RELATION.search(anchor.text):
            attribute = "method" if any(re.search(r"方法|结构|设计", span.text) for span in extracted) else "meaning_and_scope"
            item = replace(item, requested_attribute=attribute)
        parents = [candidate for candidate in local.nodes
                   if candidate.subjects == item.subjects and candidate.requested_attribute == item.requested_attribute
                   and any(parent.start <= anchor.start and anchor.end <= parent.end for parent in candidate.anchors)]
        if len(parents) == 1:
            parent = parents[0]
            item = replace(item, temporal_scope=parent.temporal_scope, source_scope=parent.source_scope,
                           qualifiers=tuple(dict.fromkeys(parent.qualifiers + item.qualifiers)),
                           evidence_requirements=tuple(dict.fromkeys(parent.evidence_requirements + item.evidence_requirements)),
                           excluded=parent.excluded or item.excluded)
        model_items.append(item)
        ids.add(item_id)
    # Local nodes preserve omitted qualifiers and decomposition; validated model
    # nodes contribute only when anchored and not already represented.
    refined_anchors = {item.anchors[0] for item in model_items}
    required_ids = {dep for item in local.nodes for dep in item.depends_on}
    local_items = [item for item in local.nodes
                   if not (item.id not in required_ids and item.subjects == item.anchors
                           and item.anchors[0] in refined_anchors)]
    nodes = _merge_nodes(local_items + model_items)
    notes = ()
    if len(nodes) > MAX_NODES:
        nodes = local.nodes
        notes = ("模型拆题与本地拆题合并后超过上限，保留本地问题图。",)
    return replace(local, nodes=nodes, origin="deepseek_checked", validation_notes=notes)


def excluded_routes(graph: QuestionGraph) -> set[str]:
    """Only explicit exclusions suppress an existing numerical route."""
    routes = set()
    for node in graph.nodes:
        if not node.excluded:
            continue
        names = " ".join(normalize_input(span.text) for span in node.subjects)
        if "mae" in names or "平均绝对误差" in names:
            routes.add("evaluation_metrics")
        if "预测" in names:
            routes.add("archived_forecast")
        if any(cue in names for cue in ("历史水位", "当时水位", "实测水位", "声纳水位")):
            routes.add("historical_water")
    return routes


def excluded_concepts(graph: QuestionGraph) -> set[str]:
    concepts = set()
    for node in graph.nodes:
        if not node.excluded:
            continue
        names = " ".join(normalize_input(span.text) for span in node.subjects)
        if "caution" in names or "watch" in names:
            concepts.add("internal_watch")
        if "warning" in names and node.source_scope != "official":
            concepts.add("internal_warning")
        if "flood alert" in names or "官方预警" in names:
            concepts.add("official_flood_alert")
        if re.search(r"旧传感器|旧声纳|早期监测", names):
            concepts.add("housemill_old_sensor")
    return concepts
