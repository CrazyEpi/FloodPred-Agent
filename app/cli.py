"""Small command-line smoke interface for D1-D2 archival queries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import ArchiveError, DEFAULT_DB, get_archived_forecast, get_forecast_as_of


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read a historical flood forecast")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--run-id", help="Exact 64-character forecast run ID")
    selection.add_argument("--as-of", help="UTC ISO 8601 replay time")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Read-only archive DB")
    parser.add_argument("--include-points", action="store_true", help="Show all 96 points")
    args = parser.parse_args(argv)
    try:
        result = (
            get_archived_forecast(args.run_id, args.db)
            if args.run_id
            else get_forecast_as_of(args.as_of, args.db)
        )
    except (ArchiveError, ValueError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}), file=sys.stderr)
        return 2
    if not args.include_points:
        result.pop("points")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
