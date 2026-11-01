"""Compatible investigation entrypoint backed by the LangGraph executor."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

from .archive import DEFAULT_DB
from .knowledge import DEFAULT_CATALOG, ROOT
from .rag import ChatClient
from .replay import DEFAULT_SONAR_DB
from .router import DEFAULT_AUDIT_LOG, QueryResult
from .investigation_core import (
    BudgetExceeded, InvestigationLimits, _Budget, _gap_request, initialize_runtime,
)
from .investigation_state import InvestigationRequest, initial_investigation_state


def run_investigation(
    question: str,
    *,
    as_of_utc: str | None = None,
    knowledge_checked_on_or_before: str | None = None,
    use_llm: bool = False,
    thinking_enabled: bool | None = None,
    allow_web: bool = False,
    client: ChatClient | None = None,
    forecast_db: Path = DEFAULT_DB,
    sonar_db: Path = DEFAULT_SONAR_DB,
    catalog: Path = DEFAULT_CATALOG,
    root: Path = ROOT,
    audit_log: Path | None = DEFAULT_AUDIT_LOG,
    limits: InvestigationLimits = InvestigationLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> QueryResult:
    """Execute the bounded graph while retaining the existing public contract."""
    if type(allow_web) is not bool:
        raise ValueError("allow_web 必须为布尔值")
    from .investigation_graph import get_investigation_graph, invoke_investigation_graph

    graph = get_investigation_graph()
    request = InvestigationRequest(question, as_of_utc, knowledge_checked_on_or_before,
                                   use_llm, thinking_enabled, allow_web)
    runtime = initialize_runtime(
        request, client=client, forecast_db=forecast_db, sonar_db=sonar_db,
        catalog=catalog, root=root, audit_log=audit_log, limits=limits, clock=clock,
    )
    state = invoke_investigation_graph(graph, initial_investigation_state(request), runtime)
    result = state["result"]
    if result is None:
        raise RuntimeError("Investigation graph ended without a QueryResult")
    return result


def start_persisted_investigation(*args, **kwargs):
    """Opt-in local persistence; ordinary run_investigation stays ephemeral."""
    from .investigation_checkpoint import start_persisted_investigation as start
    return start(*args, **kwargs)


def resume_investigation(*args, **kwargs):
    """Resume only the saved request, time scope and remaining budget."""
    from .investigation_checkpoint import resume_investigation as resume
    return resume(*args, **kwargs)
