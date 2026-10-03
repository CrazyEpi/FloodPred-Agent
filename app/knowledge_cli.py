"""Command-line interface for verified hybrid document retrieval."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .knowledge import DEFAULT_CATALOG, KnowledgeError, SOURCE_TYPES, render_hits, search
from .hybrid_retrieval import hybrid_search, make_request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FloodPred source-aware hybrid knowledge search")
    parser.add_argument("--question", help="Omit to enter a question interactively")
    parser.add_argument("--source-type", choices=sorted(SOURCE_TYPES))
    parser.add_argument("--version", help="Use only this exact catalog version")
    parser.add_argument("--checked-on-or-before", help="Only snapshots checked by this YYYY-MM-DD date")
    parser.add_argument("--semantic", action="store_true", help="Include local latent-semantic candidates (not proof of support)")
    parser.add_argument("--hybrid", action="store_true", help="Use raw question + bounded rewrites, BM25/LSA rank fusion")
    parser.add_argument("--fragment", help="An exact raw-question fragment, for hybrid search")
    parser.add_argument("--json", action="store_true", help="Show hybrid queries, filters, candidates and ranking trace")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    try:
        question = args.question if args.question is not None else input("请输入知识问题：")
        if args.hybrid:
            request = make_request(question, fragment=args.fragment,
                                   source_types=(args.source_type,) if args.source_type else None)
            result = hybrid_search(request, version=args.version,
                                   checked_on_or_before=args.checked_on_or_before, catalog=args.catalog)
            hits = result.hits
        else:
            if args.fragment or args.json:
                raise ValueError("--fragment 和 --json 需要 --hybrid。")
            hits = search(question, source_type=args.source_type, version=args.version,
                          checked_on_or_before=args.checked_on_or_before,
                          semantic=args.semantic, catalog=args.catalog)
    except (KnowledgeError, ValueError, OSError, KeyError, EOFError) as exc:
        print(f"检索失败：{exc}", file=sys.stderr)
        return 2
    if args.hybrid:
        if args.json:
            print(json.dumps(result.to_debug(), ensure_ascii=False, indent=2))
            return 0
        print("以下为原话混合检索候选；RRF 合并排名，不证明答案受到支持。")
    print(render_hits(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
