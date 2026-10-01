"""Command-line interface for verified hybrid document retrieval."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .knowledge import DEFAULT_CATALOG, KnowledgeError, SOURCE_TYPES, render_hits, search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FloodPred source-aware hybrid knowledge search")
    parser.add_argument("--question", help="Omit to enter a question interactively")
    parser.add_argument("--source-type", choices=sorted(SOURCE_TYPES))
    parser.add_argument("--version", help="Use only this exact catalog version")
    parser.add_argument("--checked-on-or-before", help="Only snapshots checked by this YYYY-MM-DD date")
    parser.add_argument("--semantic", action="store_true", help="Include local latent-semantic candidates (not proof of support)")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    try:
        question = args.question if args.question is not None else input("请输入知识问题：")
        hits = search(question, source_type=args.source_type, version=args.version,
                      checked_on_or_before=args.checked_on_or_before,
                      semantic=args.semantic, catalog=args.catalog)
    except (KnowledgeError, ValueError, OSError, KeyError, EOFError) as exc:
        print(f"检索失败：{exc}", file=sys.stderr)
        return 2
    print(render_hits(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
