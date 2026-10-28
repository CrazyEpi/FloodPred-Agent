"""Independent contracts used by the outer investigation graph.

This module neither executes a workflow nor provides runtime validation,
serialization, or checkpoint persistence. Business imports stay type-only.
QueryResult owns evidence and answers; the live budget owns all reservations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypedDict

if TYPE_CHECKING:
    from .investigation_core import InvestigationLimits
    from .rag import ChatClient
    from .router import QueryResult


EvidenceFingerprint: TypeAlias = tuple[tuple[str, str, tuple[str, ...], str], ...]


@dataclass(frozen=True)
class InvestigationRequest:
    """Keep original input; validation remains with the existing entrypoints."""

    question: str
    as_of_utc: str | None = None
    knowledge_checked_on_or_before: str | None = None
    use_llm: bool = False
    thinking_enabled: bool | None = None


class BudgetHandle(Protocol):
    """Read/execute surface already supplied by investigation._Budget.

    Read-only properties prevent consumers from assigning counters through
    this interface. They do not freeze the underlying live budget.
    """

    @property
    def limits(self) -> InvestigationLimits: ...

    @property
    def tool_calls(self) -> int: ...

    @property
    def llm_calls(self) -> int: ...

    @property
    def exceeded(self) -> str | None: ...

    def remaining(self) -> float: ...

    def check_time(self) -> None: ...

    def reserve_tool(self, tool: str) -> None: ...

    def reserve_llm(self, provider: object) -> None: ...


@dataclass(frozen=True)
class BudgetSnapshot:
    """An observation, never a second budget or a reservation capability."""

    tool_calls: int
    llm_calls: int
    remaining_seconds: float
    exceeded: str | None


class InvestigationState(TypedDict):
    """Per-run state with explicit replacement updates and no append reducers.

    Result fields (including request_id, rounds and stop_reason) are not
    duplicated here. QueryResult is mutable: future nodes must also manage
    that ownership explicitly; TypedDict does not isolate historic snapshots.
    """

    request: InvestigationRequest
    result: QueryResult | None
    attempted: frozenset[str]
    before_fingerprint: EvidenceFingerprint | None
    llm_available: bool
    budget_snapshot: BudgetSnapshot | None


@dataclass(frozen=True)
class InvestigationRuntime:
    """Injected, ephemeral dependencies; excluded from future checkpoints.

    Limits and clock belong to budget. Paths are inert configuration, not
    open connections. Freezing this container does not freeze its provider or
    budget, whose existing behavior and validation remain authoritative.
    """

    provider: ChatClient | None
    budget: BudgetHandle
    forecast_db: Path
    sonar_db: Path
    catalog: Path
    root: Path
    audit_log: Path | None
    evaluation_paths: tuple[Path, Path] | None = None


def initial_investigation_state(request: InvestigationRequest) -> InvestigationState:
    """Allocate a fresh state without generating IDs, doing IO or using budget."""
    return {
        "request": request,
        "result": None,
        "attempted": frozenset(),
        "before_fingerprint": None,
        "llm_available": request.use_llm,
        "budget_snapshot": None,
    }


def snapshot_budget(budget: BudgetHandle) -> BudgetSnapshot:
    """Read counters and remaining time without checking/reserving/resetting.

    Remaining time may be negative while exceeded is still None: only the
    live budget's check/reserve methods mark exhaustion. Reads assume the
    current sequential executor, not an atomic snapshot of parallel calls.
    """
    return BudgetSnapshot(
        tool_calls=budget.tool_calls,
        llm_calls=budget.llm_calls,
        remaining_seconds=budget.remaining(),
        exceeded=budget.exceeded,
    )
