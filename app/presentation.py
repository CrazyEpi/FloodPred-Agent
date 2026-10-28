"""Pure UI formatting; evidence objects and backend answers remain unchanged."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .evidence_gate import Citation
    from .router import QueryResult


SOURCE_LABELS = {
    "historical_forecast": "归档预测（只读）",
    "historical_water": "历史声纳观测（只读）",
    "historical_observation": "历史声纳观测（只读）",
    "evaluation_report": "项目评估报告",
    "internal_project": "项目服务端文档", "thesis": "毕业论文",
    "official_public_guidance": "英国官方公开资料",
    "research_paper": "House Mill 历史研究论文",
    "prior_project": "早期传感器项目仓库", "heritage_public": "Historic England 建筑档案",
}

# Fixed product boilerplate only; evidence gaps and substantive limitations stay.
_DISPLAY_BOILERPLATE = (
    "这是内部等级，不是官方警报。",
    "这是内部规则，不代表官方发布了警报。",
    "这是历史预测，不是实测或实时水情。",
    "这是当时的预测，不是实测。",
    "这里不是实时水情或官方警报。",
    "这里说的是预测方法，不是当前水情。",
    "这是建筑背景，不是当前洪水状态。",
    "这是历史研究背景，不是今天的情况。",
)


class ResultPresentation:
    """Number actual cited sources in claim order, deduplicating source versions."""

    def __init__(self, result: QueryResult):
        self.result = result
        self.sources: list[Citation] = []
        self._citations: list[Citation] = []
        self.numbers: dict[str, int] = {}
        identities: dict[tuple, int] = {}
        self.titles = {item.citation_id: item.title for item in result.knowledge}
        for hit in result.document_candidates:
            for record in (hit.record, *hit.equivalent_records):
                self.titles.setdefault(record["id"], record["title"])
        for claim in result.verified_claims:
            for citation in claim.citations:
                self._citations.append(citation)
                # Document paragraphs from one file/version share a source number.
                location = re.sub(r":\d+(?:-\d+)?(?: § .*)?$", "", citation.locator)
                identity = (location, citation.version, citation.sha256)
                if identity not in identities:
                    self.sources.append(citation)
                    identities[identity] = len(self.sources)
                self.numbers[citation.id] = identities[identity]

    def reference(self, citations) -> str:
        numbers = dict.fromkeys(self.numbers[c.id] for c in citations)
        return "〔来源 " + "、".join(map(str, numbers)) + "〕"

    def title(self, citation: Citation) -> str:
        return self.titles.get(citation.id, SOURCE_LABELS.get(citation.source_type, "项目资料"))

    def text(self, value: str) -> str:
        """Translate only metadata known in this result, never arbitrary hex strings."""
        for claim in self.result.verified_claims:
            original = f"【{claim.id} → {'、'.join(c.id for c in claim.citations)}】"
            value = value.replace(original, self.reference(claim.citations))
        if self.result.forecast:
            run_id = re.escape(self.result.forecast.run_id)
            value = re.sub(rf"[；;]?\s*run_id\s*[:：=]\s*`?{run_id}`?", "", value)
        # Locators/hashes can also occur in gap explanations or source rejection messages.
        replacements = {}
        for citation in self._citations:
            replacements[citation.locator] = self.title(citation)
            replacements[citation.id] = f"来源 {self.numbers[citation.id]}"
            if citation.sha256:
                replacements[citation.sha256] = "（来源指纹）"
        for hit in self.result.document_candidates:
            replacements[hit.locator] = hit.record["title"]
            for field in ("sha256", "origin_sha256"):
                if hit.record.get(field):
                    replacements[hit.record[field]] = "（来源指纹）"
        if self.result.forecast:
            replacements[self.result.forecast.run_id] = "（预测批次）"
        for old, new in sorted(replacements.items(), key=lambda item: -len(item[0])):
            if old:
                value = value.replace(old, new)
        nodes = {row.node_id: f"问题 {index}" for index, row in enumerate(self.result.question_evidence, 1)}
        nodes.update({claim.id: f"结论 {index}" for index, claim in enumerate(self.result.verified_claims, 1)})
        for old, new in sorted(nodes.items(), key=lambda item: -len(item[0])):
            value = re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", lambda _: new, value)
        for phrase in _DISPLAY_BOILERPLATE:
            value = value.replace(phrase, "")
        return value
