"""Network proposals are data; this policy is the sole authority to permit them.

Step 1 only: no HTTP, no search, no changes to project evidence contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import re


@dataclass(frozen=True)
class NetworkHint:
    scope: str
    freshness: str
    local_possible: str
    needs_web: bool
    reason: str
    source_requirements: tuple[str, ...]
    proposed_by_model: bool = False


@dataclass(frozen=True)
class WebDecision:
    question_id: str
    hint: NetworkHint
    allowed: bool
    status: str
    topic: str | None
    domains: tuple[str, ...]

    def to_dict(self):
        return asdict(self)


# Exact hosts: neither the model nor a URL may add hosts or wildcard suffixes.
TOPIC_DOMAINS = {
    "housemill": ("housemill.org.uk", "www.housemill.org.uk", "historicengland.org.uk"),
    "official_flood": ("www.gov.uk", "check-for-flooding.service.gov.uk"),
    "project_research": ("www.ucl.ac.uk", "discovery.ucl.ac.uk", "github.com"),
}
_CURRENT = re.compile(r"最近|近期|最新|今天|本周|这周|今年|现在|目前|当前|latest|current|today", re.I)
_NUMERIC = re.compile(r"预测峰值|最高水位|当时水位|实测水位|run_id|MAE.{0,8}(?:多少|数值)|(?:多少|数值).{0,8}MAE", re.I)
_DANGER = re.compile(r"(?:现在|今天|目前|现场).{0,8}(?:安全|水位|警报)|安全吗|能去吗|实时(?:水位|水情|告警|警报|预警)|是否.{0,4}安全|能否.{0,4}(?:进入|下水)|发布.{0,5}(?:告警|警报)|登录|登陆|填写表单|操作账号|疏散|撤离|"
                     r"(?:现在|当前|今天|目前).{0,15}(?:有没有|有|发布|生效).{0,10}(?:警报|预警|flood alert|flood warning)|"
                     r"\b(?:safe|evacuate|log\s*in|sign\s*in)\b|live.{0,8}(?:water|alert)|publish.{0,8}alert", re.I)
_FORBID = re.compile(r"(?:不要|不许|禁止|别|不用|不需要|不允许).{0,6}(?:联网|上网|网络搜索|搜网页)|仅(?:用|查|使用)本地|只(?:用|查|使用)本地|不联网|no\s+(?:web|internet)|offline\s+only", re.I)
_SENSITIVE = re.compile(r"https?://|file://|[A-Z]:[\\/]|api[_ -]?key|bearer|密钥|密码|token\s*[=:]|run_id|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{1,3}(?:\.\d{1,3}){3}\b|\b[a-f0-9]{32,}\b", re.I)


def topic_for(text: str) -> str | None:
    q = text.casefold()
    if any(c in q for c in ("house mill", "housemill", "三磨坊")):
        return "housemill"
    if any(c in q for c in ("官方", "environment agency", "flood alert", "flood warning")) and any(
            c in q for c in ("洪水", "flood", "预警", "warning")):
        return "official_flood"
    if any(c in q for c in ("floodpred", "patchtst", "duncan wilson", "旧传感器")) and any(
            c in q for c in ("研究", "论文", "公开资料", "github", "来源")):
        return "project_research"
    return None


def local_hint(text: str) -> NetworkHint:
    topic = topic_for(text)
    fresh = "current" if _CURRENT.search(text) else "static"
    numeric = bool(_NUMERIC.search(text))
    project = numeric or bool(re.search(r"内部|服务端|论文中|毕业论文|旧传感器|floodpred|patchtst", text, re.I))
    scope = "project" if project else "public" if topic else "general"
    local = "yes" if project or (topic == "housemill" and fresh == "static") else "unknown"
    needed = bool(topic and not numeric and (fresh == "current" or re.search(r"联网|上网|搜网页|官方|公开资料", text)))
    reason = "fresh_information" if needed and fresh == "current" else "public_source" if needed else "local_first"
    sources = ("official_guidance",) if topic == "official_flood" else ("institution_website",) if topic == "housemill" else ("research_paper",) if topic == "project_research" else ()
    return NetworkHint(scope, fresh, local, needed, reason, sources)


def checked_hint(value: object, text: str) -> NetworkHint:
    keys = {"scope", "freshness", "local_possible", "needs_web", "reason", "source_requirements"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("联网建议字段不符合结构")
    if (value["scope"] not in ("project", "public", "general")
        or value["freshness"] not in ("static", "current", "unknown")
        or value["local_possible"] not in ("yes", "no", "unknown")
        or type(value["needs_web"]) is not bool
        or value["reason"] not in ("local_first", "fresh_information", "public_source", "local_gap", "user_requested")
        or not isinstance(value["source_requirements"], list)
        or len(value["source_requirements"]) > 3
        or any(s not in ("official_guidance", "institution_website", "research_paper") for s in value["source_requirements"])):
        raise ValueError("联网建议含未允许的值")
    base = local_hint(text)
    # Raw qualifiers and numerical project facts override model proposals.
    return NetworkHint(base.scope if base.scope != "general" else value["scope"],
                       "current" if base.freshness == "current" else value["freshness"],
                       "yes" if base.local_possible == "yes" else value["local_possible"],
                       value["needs_web"], value["reason"], tuple(value["source_requirements"]), True)


def decide_web(graph, *, allow_web: bool = False, local_missing: tuple[str, ...] = ()) -> tuple[WebDecision, ...]:
    if type(allow_web) is not bool:
        raise ValueError("allow_web 必须为布尔值")
    result = []
    for node in graph.nodes:
        text = " ".join(s.text for s in node.anchors)
        hint = node.network_request or local_hint(text)
        topic = topic_for(text)
        required = hint.needs_web or local_hint(text).needs_web or node.id in local_missing
        status = "eligible" if required else "local_first"
        if node.excluded:
            status = "excluded"
        elif _FORBID.search(graph.original_text):
            status = "user_forbidden"
        elif not allow_web:
            status = "network_disabled"
        elif graph.needs_clarification:
            status = "clarification_required"
        elif _DANGER.search(text):
            status = "out_of_scope_safety_or_action"
        elif _NUMERIC.search(text) or node.source_scope in ("internal", "thesis"):
            status = "project_source_only"
        elif re.search(r"https?://|file://", graph.original_text, re.I):
            status = "arbitrary_url_not_supported"
        elif topic is None:
            status = "outside_initial_scope"
        elif _SENSITIVE.search(text):
            status = "sensitive_query"
        result.append(WebDecision(node.id, hint, status == "eligible", status, topic,
                                  TOPIC_DOMAINS.get(topic, ()) ))
    return tuple(result)


def search_terms(fragment: str, decision: WebDecision) -> str:
    """Never send an entire mixed request or private local identifiers."""
    if (not decision.allowed or topic_for(fragment) != decision.topic or _SENSITIVE.search(fragment)
        or _NUMERIC.search(fragment) or _DANGER.search(fragment) or _FORBID.search(fragment)):
        raise ValueError("查询未被策略授权，或包含敏感信息")
    if not 2 <= len(fragment.strip()) <= 200:
        raise ValueError("查询须为 2–200 字")
    return fragment.strip()
