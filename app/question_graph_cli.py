"""Inspect decomposition alone, without database or document queries."""

from __future__ import annotations

import argparse
import json

from .question_graph import GraphValidationError, build_question_graph
from .rag import DeepSeekClient, RAGError
from .router import UnsupportedRoute, _QUERIES, _reject_unsafe_intent


def main() -> int:
    parser = argparse.ArgumentParser(description="FloodPred 原话拆题与限定词核对")
    parser.add_argument("question")
    parser.add_argument("--llm", action="store_true", help="使用已有 DeepSeek 配置提议问题图")
    parser.add_argument("--thinking", action="store_true")
    args = parser.parse_args()
    try:
        _reject_unsafe_intent(args.question)
        graph = build_question_graph(args.question)
    except (UnsupportedRoute, GraphValidationError) as exc:
        print(f"无法拆题：{exc}")
        return 2
    error = None
    if args.llm:
        try:
            plan = DeepSeekClient(thinking_enabled=args.thinking, request_timeout_seconds=20).plan(args.question, tuple(_QUERIES))
            graph = plan.question_graph or graph
        except RAGError as exc:
            error = str(exc)
    print(json.dumps({"question_graph": graph.to_dict(), "llm_error": error}, ensure_ascii=False, indent=2))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
