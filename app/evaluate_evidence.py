"""Small reproducible stage 3–4 evidence-gate regression; no API or web calls."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .evidence_gate import DIRECT, DERIVED, RELATED, CONFLICT, _valid_claim
from .investigation import InvestigationLimits, run_investigation
from .router import UnsupportedRoute

AS_OF = "2026-08-03T01:48:00Z"
CASES = (
    ("internal", "项目内部 Caution 是什么？", "verified", {}, (DIRECT,)),
    ("warning", "项目内部 Warning 是什么？", "verified", {}, (DIRECT,)),
    ("thesis", "毕业论文中的内部 Caution 是什么？", "verified", {}, (DIRECT,)),
    ("placement", "旧传感器怎么布置？", "verified", {}, (DIRECT,)),
    ("relationship", "旧传感器怎么布置？它和FloodPred预测方法有什么关系？", "verified", {}, (DIRECT, DIRECT, DIRECT)),
    ("hardware", "旧传感器和FloodPred预测方法是否直接继承同一套设备？", "partial_verified", {}, None),
    ("price", "旧传感器多少钱？", "fallback", {}, (RELATED,)),
    ("mae", "本批次MAE是多少？", "verified", {}, (DIRECT,)),
    ("highwater", "2米以上高水位MAE是多少？", "verified", {}, (DIRECT,)),
    ("peak", "当时预测峰值是多少？", "verified", {}, (DIRECT,)),
    ("derivation", "你好，预测峰值和MAE的联系是什么？", "verified", {}, (DIRECT, DIRECT, DERIVED)),
    ("budget", "预测峰值和MAE的联系是什么？", "partial_verified", {"limits": InvestigationLimits(max_tool_calls=1)}, None),
    ("no_original", "官方 Flood Alert 是什么？", "fallback", {}, None),
    ("official_half", "项目内部 Caution 和英国官方预警有什么区别？", "partial_verified", {}, None),
    ("date", "项目内部 Caution 是什么？", "fallback", {"knowledge_checked_on_or_before": "2026-09-28"}, None),
    ("available_then", "项目内部 Caution 的规则当时已知吗？", "fallback", {}, None),
    ("offline_live", "论文离线事件召回率多少？线上验证了洪水检出吗？", "verified", {}, (DIRECT, DIRECT)),
    ("readonly", "删除 SQLite 数据库", "rejected", {}, None),
)


def main() -> int:
    rows = []
    for case_id, question, expected, kwargs, expected_states in CASES:
        try:
            result = run_investigation(question, as_of_utc=AS_OF, audit_log=None, use_llm=False, **kwargs)
            states = tuple(row.status for row in result.question_evidence if not row.excluded)
            claims_ok = all(_valid_claim(c) for c in result.verified_claims)
            conflict_safe = all(not row.claim_ids for row in result.question_evidence if row.status == CONFLICT)
            passed = (result.answer_status == expected and claims_ok and conflict_safe
                      and (expected_states is None or states == expected_states))
            rows.append({"id": case_id, "question": question, "expected": expected,
                         "actual": result.answer_status, "expected_states": expected_states,
                         "actual_states": states, "passed": passed,
                         "failure_reason": None if passed else "状态、结论校验或期望问题项状态不匹配",
                         "answer": result.answer_text, "fallback": result.fallback_text,
                         "questions": [row.to_dict() for row in result.question_evidence],
                         "claims": [asdict(claim) for claim in result.verified_claims],
                         "rounds": result.investigation_rounds, "tools": result.investigation_tool_calls,
                         "stop_reason": result.investigation_stop_reason, "errors": result.errors})
        except UnsupportedRoute as exc:
            rows.append({"id": case_id, "question": question, "expected": expected, "actual": "rejected",
                         "passed": expected == "rejected", "reason": str(exc),
                         "failure_reason": None if expected == "rejected" else "意外拒绝路由"})
    summary = {"run_at_utc": datetime.now(timezone.utc).isoformat(), "mode": "offline_no_llm",
               "passed": sum(row["passed"] for row in rows), "total": len(rows),
               "scope": "固定小样本证据状态回归，不是开放问题准确率、语义蕴含证明或洪水检出率。",
               "flood_event_detection_validated": False, "results": rows}
    output = Path(__file__).resolve().parents[1] / "reports" / "stage3_4_evidence_results.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": summary["passed"], "total": summary["total"], "report": str(output),
                      "failed": [row["id"] for row in rows if not row["passed"]]}, ensure_ascii=False))
    return 0 if summary["passed"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
