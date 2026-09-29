"""D5 command line entry point for DeepSeek-assisted knowledge answers."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .knowledge import DEFAULT_CATALOG, KnowledgeError, ROOT
from .rag import RAGError, answer_knowledge, render_answer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D5 evidence-gated DeepSeek knowledge answer")
    parser.add_argument("--question", help="Omit to type a question interactively")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        question = args.question if args.question is not None else input("请输入知识问题：")
        answer = answer_knowledge(question, catalog=args.catalog, root=args.root)
    except (KnowledgeError, RAGError, ValueError, OSError, KeyError, EOFError) as exc:
        print(f"无法回答：{exc}", file=sys.stderr)
        return 2
    print(render_answer(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
