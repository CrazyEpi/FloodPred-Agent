"""Local persisted investigation, resume and genuine two-process replay demo."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from .investigation import InvestigationLimits, start_persisted_investigation, resume_investigation
from .investigation_checkpoint import DEFAULT_STATE_DB, CheckpointError, InvestigationInterrupted
from .router import UnsupportedRoute


def summary(run):
    result = run.result
    return {"execution_id": run.execution_id, "resume_token": run.resume_token,
            "status": run.status, "next_nodes": list(run.next_nodes), "deadline_utc": run.deadline_utc,
            "tool_calls": run.tool_calls, "llm_calls": run.llm_calls,
            "as_of_utc": result.as_of_utc if result else None,
            "knowledge_checked_on_or_before": result.knowledge_checked_on_or_before if result else None,
            "answer_status": result.answer_status if result else None,
            "answer": result.answer_text if result else None,
            "fallback": result.fallback_text if result else None,
            "stop_reason": result.investigation_stop_reason if result else None,
            "run_id": result.forecast.run_id if result and result.forecast else None,
            "rounds": result.investigation_rounds if result else 0,
            "warnings": result.warnings if result else [],
            "web_sources": [{"url": item.page.url, "title": item.page.title,
                             "fetched_at_utc": item.page.fetched_at_utc}
                            for item in result.web_evidence] if result else []}


def main() -> int:
    parser = argparse.ArgumentParser(description="FloodPred local checkpoint and recovery")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("question")
    start.add_argument("--as-of", default="2026-08-03T01:48:00Z")
    start.add_argument("--knowledge-checked-on-or-before")
    start.add_argument("--pause-after", choices=("first_pass", "followup"))
    start.add_argument("--max-seconds", type=float, default=120)
    start.add_argument("--allow-web", action="store_true")
    start.add_argument("--max-tool-calls", type=int, default=8)
    start.add_argument("--max-llm-calls", type=int, default=4)
    start.add_argument("--llm", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("token")
    demo = commands.add_parser("demo")
    for command in (start, resume, demo):
        command.add_argument("--state-db", type=Path, default=DEFAULT_STATE_DB)
    args = parser.parse_args()
    try:
        if args.command == "resume":
            run = resume_investigation(args.token, state_db=args.state_db)
        elif args.command == "start":
            run = start_persisted_investigation(
                args.question, state_db=args.state_db, as_of_utc=args.as_of,
                knowledge_checked_on_or_before=args.knowledge_checked_on_or_before,
                use_llm=args.llm, allow_web=args.allow_web, pause_after=args.pause_after,
                limits=InvestigationLimits(max_seconds=args.max_seconds,
                    max_tool_calls=args.max_tool_calls, max_llm_calls=args.max_llm_calls),
            )
        else:
            run = start_persisted_investigation(
                "项目内部 Caution 和英国官方预警有什么区别？", state_db=args.state_db,
                as_of_utc="2026-08-03T01:48:00Z", pause_after="first_pass",
            )
            child = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.checkpoint_cli", "resume",
                                    run.resume_token, "--state-db", str(args.state_db.resolve())],
                                   capture_output=True, text=True, encoding="utf-8", timeout=60)
            if child.returncode:
                raise CheckpointError("恢复子进程失败：" + child.stderr[-500:])
            after = json.loads(child.stdout)
            print(json.dumps({"separate_process": True, "before": summary(run), "after": after,
                              "same_execution": run.execution_id == after["execution_id"],
                              "same_replay_time": run.result.as_of_utc == after["as_of_utc"],
                              "budget_not_reset": after["tool_calls"] >= run.tool_calls}, ensure_ascii=False, indent=2))
            return 0
    except InvestigationInterrupted as exc:
        print(json.dumps({"status": "interrupted", "resume_token": exc.token, "error": str(exc)}, ensure_ascii=False))
        return 3
    except (CheckpointError, UnsupportedRoute, ValueError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary(run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
