"""Sequential, bounded LangGraph orchestration of existing evidence operations."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Literal, TypedDict, get_type_hints

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from . import investigation_core as core
from .investigation_state import (
    InvestigationRuntime, InvestigationState, snapshot_budget,
)
from .router import QueryResult


# Resolve only at this adapter boundary. The independent contract module keeps
# its TYPE_CHECKING imports and remains usable without LangGraph or business IO.
GraphState = TypedDict(
    "GraphState",
    get_type_hints(InvestigationState, localns={"QueryResult": QueryResult}),
)
GRAPH_RECURSION_LIMIT = 64


def _result_copy(state: GraphState) -> QueryResult:
    if state["result"] is None:
        raise RuntimeError("Evidence node requires a QueryResult")
    return deepcopy(state["result"])


def initialize(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    if state["request"].use_llm:
        runtime.context.budget.reserve_llm(runtime.context.provider)
    return {}


def first_pass(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result, available, attempted = core.first_pass(state["request"], runtime.context)
    return {"result": result, "llm_available": available, "attempted": attempted}


def decide(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result = _result_copy(state)
    budget = runtime.context.budget
    before = state["before_fingerprint"]
    if not result.plan.steps:
        result.investigation_stop_reason = (
            "needs_clarification" if result.answer_status == "needs_clarification" else "chat_mode"
        )
    elif budget.exceeded:
        result.investigation_stop_reason = budget.exceeded
    elif result.investigation_stop_reason is not None:
        pass  # A preceding node already selected a terminal condition.
    elif result.investigation_rounds >= budget.limits.max_rounds:
        # Match for/else exhaustion: final-round conflicts are not reclassified
        # as a new iteration's source_conflict. Finalize still prefers complete
        # evidence when no budget was exceeded.
        result.investigation_stop_reason = "round_limit"
    else:
        gaps = core.missing_items(result)
        if not gaps:
            result.investigation_stop_reason = "evidence_complete"
        elif all(row.status == core.CONFLICT for row in gaps):
            result.investigation_stop_reason = "source_conflict"
        else:
            before = core._check_fingerprint(result)
    return {"result": result, "before_fingerprint": before}


def after_decide(state: GraphState) -> Literal["followup", "finalize"]:
    return "followup" if state["result"].investigation_stop_reason is None else "finalize"


def followup(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result = _result_copy(state)
    context = runtime.context
    attempted = set(state["attempted"])
    round_number = result.investigation_rounds + 1
    previous_web_runs = len(result.web_runs)
    try:
        # Exactly the old outer-loop check; do not add clock reads in routers.
        context.budget.check_time()
        executed = core._fill_gaps(
            result, round_number, context.budget, catalog=context.catalog,
            root=context.root, forecast_db=context.forecast_db,
            sonar_db=context.sonar_db, provider=context.provider,
            use_llm=state["llm_available"], attempted=attempted,
            evaluation_paths=context.evaluation_paths,
            **({"allow_web": True} if state["request"].allow_web else {}),
        )
    except core.BudgetExceeded as exc:
        result.errors.append(str(exc))
        result.investigation_stop_reason = context.budget.exceeded
        if state["request"].allow_web and any(run.get("operation") in ("fetch", "search") for run in result.web_runs[previous_web_runs:]):
            result.investigation_rounds = round_number
            core.assess_evidence(result, root=context.root)
    else:
        if executed:
            result.investigation_rounds = round_number
        else:
            result.investigation_stop_reason = "partial_evidence"
    available = state["llm_available"]
    if any(entry.get("step") == "web_extract" and entry.get("error_type") in ("LLMRequestError", "LLMNotConfigured") for entry in result.trace):
        available = False
    return {"result": result, "attempted": frozenset(attempted), "llm_available": available}


def after_followup(state: GraphState) -> Literal["recheck", "finalize"]:
    return "recheck" if state["result"].investigation_stop_reason is None else "finalize"


def recheck(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result = _result_copy(state)
    core.assess_evidence(result, root=runtime.context.root)
    result.trace.append({
        "step": "investigation_round", "round": str(result.investigation_rounds),
        "status": "gap_only", "missing_nodes": ",".join(row.node_id for row in core.missing_items(result)),
    })
    if core._check_fingerprint(result) == state["before_fingerprint"]:
        result.investigation_stop_reason = "no_support_gain"
    return {"result": result}


def finalize(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result = core.finalize_result(
        _result_copy(state), runtime.context, llm_available=state["llm_available"],
    )
    return {"result": result}


def audit(state: GraphState, runtime: Runtime[InvestigationRuntime]) -> dict:
    result = core.audit_result(_result_copy(state), state["request"], runtime.context)
    # This is the only new remaining() read, after every budget-dependent
    # decision and call. It cannot move an injected clock before a guard.
    return {"result": result, "budget_snapshot": snapshot_budget(runtime.context.budget)}


@lru_cache(maxsize=1)
def get_investigation_graph():
    """Cache topology only. Context, clients, budgets and evidence are per run."""
    return build_investigation_graph()


def build_investigation_graph(*, checkpointer=None, node_wrapper=None, interrupt_after=None):
    """Build the same topology for ephemeral or explicitly persisted runs."""
    builder = StateGraph(GraphState, context_schema=InvestigationRuntime)
    for name, action in (
        ("initialize", initialize), ("first_pass", first_pass), ("decide", decide),
        ("followup", followup), ("recheck", recheck), ("finalize", finalize), ("audit", audit),
    ):
        builder.add_node(name, node_wrapper(name, action) if node_wrapper else action)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "first_pass")
    builder.add_edge("first_pass", "decide")
    builder.add_conditional_edges("decide", after_decide)
    builder.add_conditional_edges("followup", after_followup)
    builder.add_edge("recheck", "decide")
    builder.add_edge("finalize", "audit")
    builder.add_edge("audit", END)
    return builder.compile(checkpointer=checkpointer, interrupt_after=interrupt_after)


def invoke_investigation_graph(graph, state: InvestigationState,
                               runtime: InvestigationRuntime) -> InvestigationState:
    # Even a host-level tracing setting must not export questions or evidence.
    with tracing_context(enabled=False):
        return graph.invoke(
            state, config={"recursion_limit": GRAPH_RECURSION_LIMIT, "callbacks": []},
            context=runtime,
        )
