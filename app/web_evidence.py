"""Bounded web evidence: immutable fetched sources, never search-snippet facts.

Public summaries are extractive. The optional model selects source paragraphs;
it cannot submit new factual prose. Definition comparisons use reviewed rules.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import re
import time
from urllib.parse import urlsplit

from .web_policy import TOPIC_DOMAINS, decide_web
from .web_tools import (NetworkBudget, PagePreview, WebToolError, _HTMLText, _api_key,
                        fetch_public_page, search_public_web, validate_url)

OFFICIAL_URL = "https://www.gov.uk/guidance/flood-alerts-and-warnings-what-they-are-and-what-to-do"
SEEDS = {
    "official_flood": (OFFICIAL_URL,),
    "housemill": ("https://housemill.org.uk/",),
    "project_research": ("https://github.com/djdunc/housemill",),
}


@dataclass(frozen=True)
class WebSnapshot:
    id: str
    topic: str
    node_ids: tuple[str, ...]
    page: PagePreview
    selected_paragraphs: tuple[int, ...] = ()


def public_query(node, topic: str) -> str:
    """Public templates only: no full mixed request or private identifiers."""
    if topic == "official_flood":
        return "Environment Agency flood alert flood warning definitions England"
    if topic == "housemill":
        return "House Mill " + ("events dates" if re.search(r"活动|最近|近期|最新|本周|今天|今年", node.anchors[0].text) else "history background")
    return "FloodPred PatchTST research paper" if any("floodpred" in s.text.casefold() or "patchtst" in s.text.casefold() for s in node.subjects) else "Duncan Wilson House Mill research paper"


def temporal_rejection(result, node, fetched_at: str | None = None) -> str | None:
    day = (fetched_at or datetime.now(timezone.utc).isoformat())[:10]
    if result.knowledge_checked_on_or_before and result.knowledge_checked_on_or_before < day:
        return "当前网页无法证明在指定文档截止日期前已有这份内容。"
    if node.temporal_scope in ("historical", "mixed_historical_and_evaluation"):
        return "当前网页不能证明所问历史时刻的公开资料状态。"
    return None


def _github_registered(url: str) -> bool:
    return urlsplit(url).hostname != "github.com" or urlsplit(url).path.rstrip("/").startswith("/djdunc/housemill") and (
        urlsplit(url).path.rstrip("/") == "/djdunc/housemill" or urlsplit(url).path.startswith("/djdunc/housemill/"))


def validate_snapshot(snapshot: WebSnapshot) -> bool:
    """Recheck saved HTML and extracted text without silently refetching a URL."""
    try:
        page = snapshot.page
        _, clean = validate_url(page.url, TOPIC_DOMAINS[snapshot.topic])
        if clean != page.url or not _github_registered(clean) or not page.raw_body or len(page.raw_body) > 1_000_000:
            return False
        digest = hashlib.sha256(page.raw_body).hexdigest()
        identity = hashlib.sha256((clean + digest).encode()).hexdigest()
        if digest != page.raw_sha256 or snapshot.id != "web:" + identity:
            return False
        # The fetcher decodes using the Content-Type charset; preserve that basis.
        # HTML snapshots are stored as bytes alongside the decoding charset.
        charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", page.content_type)
        parser = _HTMLText()
        parser.feed(page.raw_body.decode(charset[1] if charset else "utf-8", errors="replace"))
        parser.flush()
        if tuple(parser.paragraphs[:100]) != page.paragraphs or parser.published_at != page.published_at:
            return False
        if " ".join(parser.title_parts).strip()[:300] != page.title:
            return False
        if tuple(dict.fromkeys(parser.dates)) != page.dates:
            return False
        fetched = datetime.fromisoformat(page.fetched_at_utc.replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            return False
        if page.published_at and datetime.fromisoformat(page.published_at.replace("Z", "+00:00")).date() > fetched.date():
            return False
        for date in page.dates:
            datetime.fromisoformat(date.replace("Z", "+00:00"))
        from .evidence_gate import _injection
        return bool(page.paragraphs) and not _injection(" ".join(page.paragraphs))
    except (ValueError, KeyError, TypeError, UnicodeError, LookupError):
        return False


class _SharedNetworkBudget(NetworkBudget):
    def __init__(self, global_budget, calls, spent):
        self.global_budget = global_budget
        super().__init__(max_requests=4, max_seconds=max(.001, 20 - spent), calls=calls)

    def remaining(self):
        remaining = self.global_budget.remaining()
        if remaining <= 0:
            self.global_budget.check_time()
        return min(remaining, super().remaining())

    def reserve(self):
        self.remaining()
        if self.calls >= self.max_requests:
            raise WebToolError("network_call_limit：网络调用预算已耗尽")
        self.global_budget.reserve_tool("web:request")
        self.calls += 1


def _registered_seeds(topic, catalog):
    urls = list(SEEDS[topic])
    from .knowledge import _load, KnowledgeError
    try:
        records = _load(catalog)
    except (OSError, ValueError, KeyError, TypeError, KnowledgeError):
        records = []
    for record in records:
        for key in ("source_ref", "origin_url"):
            value = record.get(key, "")
            try:
                _, clean = validate_url(value, TOPIC_DOMAINS[topic])
                if topic == "official_flood" and not urlsplit(clean).path.startswith("/guidance/"):
                    continue
                if _github_registered(clean) and clean not in urls:
                    urls.append(clean)
            except WebToolError:
                pass
    # Research first uses actual registered institution/paper pages when present.
    return sorted(urls, key=lambda u: urlsplit(u).hostname == "github.com") if topic == "project_research" else urls


def fill_web_gaps(result, budget, *, catalog, provider=None, use_llm=False) -> bool:
    """One round; failed reservations count, and URLs never repeat in a run."""
    from .evidence_gate import CONFLICT, missing_items
    gaps = {row.node_id for row in missing_items(result) if row.status != CONFLICT}
    decisions = decide_web(result.question_graph, allow_web=True, local_missing=tuple(gaps))
    result.web_decisions = [d.to_dict() for d in decisions]
    nodes = {n.id: n for n in result.question_graph.nodes}
    requests = [run for run in result.web_runs if run.get("operation") in ("fetch", "search")]
    spent = sum(run.get("seconds", 0) for run in requests)
    if len(requests) >= 4 or spent >= 20:
        return False
    network = _SharedNetworkBudget(budget, len(requests), spent)
    attempted_urls = {run.get("url") for run in requests}
    executed = False
    targets = [d for d in decisions if d.allowed and d.question_id in gaps
               and nodes[d.question_id].question_type not in ("relation", "comparison", "cause")]
    for target_index, decision in enumerate(targets):
        node = nodes[decision.question_id]
        if (not decision.allowed or node.id not in gaps or node.question_type in ("relation", "comparison", "cause")):
            continue
        reason = temporal_rejection(result, node)
        if reason:
            result.web_runs.append({"node_id": node.id, "status": "time_scope_rejected", "reason": reason})
            for row in result.question_evidence:
                if row.node_id == node.id and reason not in row.reasons:
                    row.reasons.append(reason)
            continue
        # Search cannot send an original mixed clause; this is a public template.
        query = public_query(node, decision.topic)
        node_start = network.calls
        node_limit = max(1, (4 - network.calls) // (len(targets) - target_index))
        result.web_evidence = [replace(s, node_ids=tuple(dict.fromkeys((*s.node_ids, node.id))))
                               if s.topic == decision.topic else s for s in result.web_evidence]
        urls = _registered_seeds(decision.topic, catalog)
        if decision.topic == "housemill" and re.search(r"背景|历史|是什么", node.anchors[0].text):
            urls.sort(key=lambda u: "historicengland.org.uk" not in u)
        searched = False
        followed = False
        while urls or not searched:
            if any(node.id in snapshot.node_ids and claims_for_node(result, node, snapshot) for snapshot in result.web_evidence):
                break
            if network.calls >= 4 or network.calls - node_start >= node_limit:
                break
            if not urls:
                searched = True
                try:
                    configured = bool(_api_key())
                except WebToolError:
                    configured = False
                if not configured or any(run.get("query") == query for run in requests):
                    result.web_runs.append({"node_id": node.id, "status": "search_unavailable_or_attempted"})
                    break
                started = time.monotonic()
                before = network.calls
                run = {"operation": "search", "node_id": node.id, "query": query}
                try:
                    hits = search_public_web(query, decision=decision, budget=network)
                    urls.extend(hit.url for hit in hits if _github_registered(hit.url))
                    run["status"] = "candidates_only"
                except WebToolError as exc:
                    run.update(status="failed", reason=str(exc))
                finally:
                    if network.calls > before:
                        run["seconds"] = time.monotonic() - started
                        result.web_runs.append(run); requests.append(run); executed = True
                if not urls:
                    break
            url = urls.pop(0)
            try:
                _, url = validate_url(url, decision.domains)
            except WebToolError:
                continue
            if url in attempted_urls or not _github_registered(url):
                continue
            started, before = time.monotonic(), network.calls
            run = {"operation": "fetch", "node_id": node.id, "url": url}
            try:
                page = fetch_public_page(url, decision=decision, budget=network)
                identity = hashlib.sha256((page.url + page.raw_sha256).encode()).hexdigest()
                snapshot = WebSnapshot("web:" + identity, decision.topic, (node.id,), page)
                if not validate_snapshot(snapshot):
                    raise WebToolError("网页原文或快照完整性检查未通过")
                result.web_evidence.append(snapshot)
                run.update(status="fetched_candidate", snapshot_id=snapshot.id)
                if not followed and not claims_for_node(result, node, snapshot):
                    # One link level, actual hrefs only; prefer a relevant public page.
                    links = [link for link in page.links if _github_registered(link) and re.search(
                        r"event|news|visit|history|research|publication|paper|flood|warning|article|eprint", link, re.I)]
                    urls[:0] = links[:2]
                    followed = True
            except WebToolError as exc:
                run.update(status="failed", reason=str(exc))
            finally:
                if network.calls > before:
                    run["seconds"] = time.monotonic() - started
                    result.web_runs.append(run); requests.append(run); attempted_urls.add(url); executed = True
            if network.calls >= 4:
                break
    # An optional model selects existing paragraph indices, never free factual text.
    selector = getattr(provider, "select_public_paragraphs", None)
    extracted = {sid for run in result.web_runs if run.get("operation") == "extract" for sid in run["snapshot_ids"]}
    candidates = [s for s in result.web_evidence if s.topic != "official_flood" and s.id not in extracted
                  and relevant_paragraphs(nodes[s.node_ids[0]], s)]
    if use_llm and candidates and callable(selector):
        result.web_runs.append({"operation": "extract", "snapshot_ids": [s.id for s in candidates]})
        try:
            budget.reserve_llm(provider)
            choices = selector([{"snapshot_id": s.id, "public_question": public_query(nodes[s.node_ids[0]], s.topic), "paragraphs": [
                {"index": i, "text": p} for i, p in relevant_paragraphs(nodes[s.node_ids[0]], s)]} for s in candidates])
            allowed = {s.id: {i for i, _ in relevant_paragraphs(nodes[s.node_ids[0]], s)} for s in candidates}
            if (not isinstance(choices, list) or len(choices) > len(candidates)
                or any(not isinstance(row, dict) or set(row) != {"snapshot_id", "indices"} for row in choices)):
                raise ValueError("网页段落选择结构无效")
            chosen = {}
            for row in choices:
                sid, indices = row["snapshot_id"], row["indices"]
                if sid not in allowed or sid in chosen or not isinstance(indices, list) or len(indices) > 3 or any(type(i) is not int or i not in allowed[sid] for i in indices):
                    raise ValueError("网页段落选择超出允许范围")
                chosen[sid] = tuple(dict.fromkeys(indices))
            result.web_evidence = [replace(s, selected_paragraphs=chosen.get(s.id, s.selected_paragraphs)) for s in result.web_evidence]
        except Exception as exc:
            result.trace.append({"step": "web_extract", "status": "extractive_fallback", "error_type": type(exc).__name__})
    return executed


def _recent_date(value, question, reference_day=None):
    try:
        day = datetime.fromisoformat(value[:10]).date()
    except (ValueError, TypeError):
        return False
    today = datetime.fromisoformat(reference_day).date() if reference_day else datetime.now(timezone.utc).date()
    if "今年" in question:
        return day.year == today.year
    if "今天" in question:
        return day == today
    if "本周" in question:
        monday = today - timedelta(days=today.weekday())
        return monday <= day < monday + timedelta(days=7)
    return abs((day - today).days) <= 31


def _explicit_dates(text):
    dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)
    for date in re.findall(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}\b", text, re.I):
        date = re.sub(r"(?<=\d)(?:st|nd|rd|th)\b", "", date)
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try:
                dates.append(datetime.strptime(date, fmt).date().isoformat())
                break
            except ValueError:
                pass
    return dates


def relevant_paragraphs(node, snapshot):
    question = node.anchors[0].text
    current = node.temporal_scope == "public_current" or bool(re.search(r"最新|最近|近期|今年|今天|本周", question))
    events = bool(re.search(r"活动|开放|参观|events?|visit", question, re.I))
    date_scope = [span.text for span in node.qualifiers if re.fullmatch(r"\d{4}-\d{2}-\d{2}", span.text)]
    selected = []
    for i, paragraph in enumerate(snapshot.page.paragraphs):
        if len(paragraph) < 25 or len(paragraph) > 1800:
            continue
        if snapshot.topic == "housemill":
            pattern = r"event|visit|open|tour|concert|exhibition|活动|参观" if events or current else r"house mill|three mills|tidal|mill|磨坊"
        else:
            pattern = r"research|abstract|paper|publication|patchtst|floodpred|wilson|研究|论文|摘要"
        if not re.search(pattern, paragraph, re.I):
            continue
        attribute_patterns = {
            "limitations": r"limit|weakness|challenge|局限|限制",
            "method": r"method|model|architect|predict|sensor|方法|模型",
            "placement": r"located|address|bromley|位置|地址|位于",
        }
        if node.requested_attribute in attribute_patterns and not re.search(attribute_patterns[node.requested_attribute], paragraph, re.I):
            continue
        if node.requested_attribute == "value":
            if re.search(r"票|价格|费用|多少钱", question) and not re.search(r"price|cost|£|ticket|票|价格", paragraph, re.I):
                continue
            if not re.search(r"\d", paragraph):
                continue
        if snapshot.topic == "project_research":
            subjects = " ".join(s.text for s in node.subjects).casefold()
            context = (paragraph + " " + snapshot.page.title).casefold()
            required = [word for word in ("floodpred", "patchtst") if word in subjects]
            if required and not any(word in context for word in required):
                continue
        dates = _explicit_dates(paragraph)
        if snapshot.topic == "project_research":
            dates = [date for date in dates if date <= snapshot.page.fetched_at_utc[:10]]
        # Current claims require explicit dated content, not merely today's fetch.
        if current and not any(_recent_date(d, question, snapshot.page.fetched_at_utc[:10]) for d in dates):
            if snapshot.topic != "project_research" or not _recent_date(snapshot.page.published_at, question, snapshot.page.fetched_at_utc[:10]):
                continue
        if date_scope and not all(day in paragraph for day in date_scope):
            continue
        selected.append((i, paragraph))
    return selected[:6]


def claims_for_node(result, node, snapshot):
    from .evidence_gate import Citation, VerifiedClaim, _valid_claim
    if node.id not in snapshot.node_ids or not validate_snapshot(snapshot) or temporal_rejection(result, node, snapshot.page.fetched_at_utc):
        return []
    permission = next((d for d in decide_web(result.question_graph, allow_web=True, local_missing=(node.id,)) if d.question_id == node.id), None)
    if permission is None or not permission.allowed or permission.topic != snapshot.topic:
        return []
    if node.question_type in ("relation", "comparison", "cause"):
        return []
    claims = []
    source_type = {"official_flood": "official_public_guidance", "housemill": "heritage_public", "project_research": "research_paper"}[snapshot.topic]
    def add(index, quote, text):
        citation = Citation(f"{snapshot.id}:p{index}", f"{snapshot.page.url}#paragraph-{index}",
                            snapshot.page.fetched_at_utc, source_type, quote, snapshot.page.raw_sha256,
                            "public_page_fetched_now_not_historical")
        claim = VerifiedClaim(f"{node.id}-web-{len(claims)+1}", node.id, text, (citation,))
        if _valid_claim(claim):
            claims.append(claim)
    if snapshot.topic == "official_flood":
        if node.requested_attribute != "meaning_and_scope":
            return []
        if node.question_type != "definition" and re.search(r"多久|提前|多长|阈值|多少|几点|多久|何时|为什么|原因", node.anchors[0].text):
            return []
        page_text = " ".join(snapshot.page.paragraphs)
        if (urlsplit(snapshot.page.url).hostname != "www.gov.uk" or not urlsplit(snapshot.page.url).path.startswith("/guidance/") or not re.search(r"Environment Agency", page_text)
            or not re.search(r"Applies to England|In England", page_text)
            or re.search(r"苏格兰|威尔士|北爱尔兰|Scotland|Wales|Northern Ireland", node.anchors[0].text, re.I)):
            return []
        definitions = (("flood alert", "Flood Alert", "可能发生洪水", r"flood alert\s*(?:means that|[–—-])\s*flooding is possible"),
                       ("flood warning", "Flood Warning", "预计会发生洪水", r"(?<!severe )flood warning\s*(?:means that|[–—-])\s*flooding is expected"),
                       ("severe flood warning", "Severe Flood Warning", "洪水可能危及生命并严重扰乱社区", r"severe flood warning\s*(?:means that|[–—-])\s*flooding could (?:cause danger|be a risk) to life and significant disruption to communities"))
        subject = " ".join(s.text for s in node.subjects).casefold()
        for name, label, meaning, pattern in definitions:
            desired = ("severe flood warning" if "severe" in subject else "flood warning" if "warning" in subject else "flood alert" if "alert" in subject else None)
            if desired and name != desired:
                continue
            index = next((i for i, p in enumerate(snapshot.page.paragraphs) if re.search(pattern, p, re.I)), None)
            if index is not None:
                # Include the publisher and jurisdiction paragraphs in the exact quote.
                quote = "\n".join(p for i, p in enumerate(snapshot.page.paragraphs) if i == index or re.search(r"Environment Agency|Applies to England|In England", p))
                add(index, quote, f"英格兰 Environment Agency 的 {label} 表示{meaning}。")
    else:
        paragraphs = relevant_paragraphs(node, snapshot)
        if snapshot.selected_paragraphs and node.id == snapshot.node_ids[0]:
            paragraphs = [(i, p) for i, p in paragraphs if i in snapshot.selected_paragraphs]
        for i, quote in paragraphs[:3]:
            label = "House Mill 公开页面" if snapshot.topic == "housemill" else "研究来源页面"
            add(i, quote, f"所查{label}记载：{quote}")
    return claims


def verify_web_citation(result, node_id, citation):
    if not result.question_graph:
        return False
    node = next((n for n in result.question_graph.nodes if n.id == node_id), None)
    if node is None:
        return False
    allowed = {node.id, *node.depends_on}
    return any(citation == c for snapshot in result.web_evidence
               for endpoint in result.question_graph.nodes if endpoint.id in allowed
               for claim in claims_for_node(result, endpoint, snapshot) for c in claim.citations)
