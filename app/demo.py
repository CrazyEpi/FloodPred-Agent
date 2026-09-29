"""Interactive or one-shot command-line entry point for the D3 replay demo."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import sys

from .archive import ArchiveError, DEFAULT_DB
from .replay import DEFAULT_SONAR_DB, answer_question, render_card


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D3 no-LLM flood forecast replay demo")
    parser.add_argument("--as-of", required=True, help="Replay cutoff, ISO 8601 with timezone")
    parser.add_argument("--question", help="Omit to enter the question interactively")
    parser.add_argument("--forecast-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sonar-db", type=Path, default=DEFAULT_SONAR_DB)
    args = parser.parse_args(argv)
    try:
        question = args.question if args.question is not None else input("请输入问题：")
        card = answer_question(question, args.as_of, args.forecast_db, args.sonar_db)
    except (ArchiveError, ValueError, OSError, sqlite3.DatabaseError) as exc:
        print(f"无法生成回放卡：{exc}", file=sys.stderr)
        return 2
    except EOFError:
        print("未输入问题", file=sys.stderr)
        return 2
    print(render_card(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
