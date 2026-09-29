"""Command-line interface for the D4 document retrieval slice."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .knowledge import DEFAULT_CATALOG, KnowledgeError, SOURCE_TYPES, render_hits, search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D4 source-aware knowledge search")
    parser.add_argument("--question", help="Omit to enter a question interactively")
    parser.add_argument("--source-type", choices=sorted(SOURCE_TYPES))
    parser.add_argument("--version", help="Use only this exact catalog version")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args(argv)
    try:
        question = args.question if args.question is not None else input("请输入知识问题：")
        hits = search(question, source_type=args.source_type, version=args.version, catalog=args.catalog)
    except (KnowledgeError, ValueError, OSError, KeyError, EOFError) as exc:
        print(f"检索失败：{exc}", file=sys.stderr)
        return 2
    print(render_hits(hits))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
