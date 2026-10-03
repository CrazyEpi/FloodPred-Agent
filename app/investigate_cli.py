"""Small command-line entry point for the bounded investigation demo."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from .investigation import InvestigationLimits, run_investigation
from .router import UnsupportedRoute


def main() -> int:
    parser = argparse.ArgumentParser(description="FloodPred bounded, read-only investigation")
    parser.add_argument("question", help="Question to investigate")
    parser.add_argument("--as-of", default="2026-08-03T01:48:00Z", help="Historical replay cutoff in UTC")
    parser.add_argument("--knowledge-checked-on-or-before", help="Only use knowledge snapshots checked by YYYY-MM-DD")
    parser.add_argument("--llm", action="store_true", help="Use configured DeepSeek for planning/review/explanation")
    parser.add_argument("--thinking", action="store_true", help="Enable DeepSeek thinking for the initial plan and final explanation")
    parser.add_argument("--max-tool-calls", type=int, choices=range(1, 13), default=8,
                        help="Read-only tool budget, 1–12 (default: 8)")
    args = parser.parse_args()
    try:
        result = run_investigation(args.question, as_of_utc=args.as_of,
                                   knowledge_checked_on_or_before=args.knowledge_checked_on_or_before,
                                   use_llm=args.llm, thinking_enabled=args.thinking,
                                   limits=InvestigationLimits(max_tool_calls=args.max_tool_calls))
    except UnsupportedRoute as exc:
        print(f"拒绝：{exc}")
        return 2
    print(json.dumps({
        "answer": result.answer_text,
        "answer_status": result.answer_status,
        "fallback": result.fallback_text,
        "rounds": result.investigation_rounds,
        "read_only_tool_calls": result.investigation_tool_calls,
        "llm_calls": result.investigation_llm_calls,
        "stop_reason": result.investigation_stop_reason,
        "run_id": result.forecast.run_id if result.forecast else None,
        "sources": [{"id": item.citation_id, "fact": item.fact, "locator": item.locator} for item in result.knowledge],
        "errors": result.errors,
        "warnings": result.warnings,
        "question_graph": result.question_graph.to_dict() if result.question_graph else None,
        "question_evidence": [row.to_dict() for row in result.question_evidence],
        "verified_claims": [asdict(claim) for claim in result.verified_claims],
        "source_rejections": result.source_rejections,
        "retrieval_runs": result.retrieval_runs,
        "trace": result.trace,
    }, ensure_ascii=False, indent=2))
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
