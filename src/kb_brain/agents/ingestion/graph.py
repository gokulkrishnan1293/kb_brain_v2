"""The ingestion graph.

    authorize -> plan -> reason <-> tools -> harvest -> persist

``authorize`` runs first and inside the graph so the decision is part of the
run's recorded state. Tools are bound at build time because the harness only
ever exposes the tools the requested sources actually offer.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from ...canonical import RawRecord
from ...governance import Action, PolicyEngine, Principal
from ...storage.base import RawStore
from .harvest import harvest_records
from .prompts import TASK_PROMPT, build_system_prompt
from .state import IngestionState

logger = logging.getLogger(__name__)


def build_ingestion_graph(
    *,
    model: BaseChatModel,
    tools: list[BaseTool],
    store: RawStore,
    policy: PolicyEngine,
    principal: Principal,
    tool_sources: dict[str, str],
    max_steps: int,
    max_records: int,
):
    """Compile the ingestion harness for one set of discovered tools."""

    bound_model = model.bind_tools(tools) if tools else model

    # -- nodes ---------------------------------------------------------------

    def authorize(state: IngestionState) -> dict[str, Any]:
        request = state["request"]
        for source in request.sources:
            decision = policy.authorize(
                principal, Action.INGEST, request.scope, source=source
            )
            if not decision.allowed:
                # Stop before touching any source system: governance precedes
                # retrieval, not the other way round.
                return {
                    "errors": [
                        f"policy denied ingest of '{source}' into "
                        f"{request.scope.render()}: {decision.reason}"
                    ],
                    "summary": "denied by policy",
                }
        return {"errors": []}

    def plan(state: IngestionState) -> dict[str, Any]:
        request = state["request"]
        system = build_system_prompt(
            scope=request.scope.render(),
            scope_level=request.scope.level,
            sources=request.sources,
            hints=request.hints,
            max_steps=max_steps,
        )
        task = TASK_PROMPT.format(objective=request.objective, scope=request.scope.render())
        return {
            "messages": [SystemMessage(content=system), HumanMessage(content=task)],
            "tool_names": [tool.name for tool in tools],
            "steps": 0,
        }

    async def reason(state: IngestionState) -> dict[str, Any]:
        response = await bound_model.ainvoke(state["messages"])
        return {"messages": [response], "steps": state.get("steps", 0) + 1}

    async def harvest(state: IngestionState) -> dict[str, Any]:
        request = state["request"]
        cap = min(request.max_records or max_records, max_records)
        records, audit, errors = harvest_records(
            state["messages"],
            scope=request.scope,
            run_id=state["run_id"],
            principal=state["principal"],
            tool_sources=tool_sources,
            max_records=cap,
        )
        final = state["messages"][-1]
        summary = final.content if isinstance(final, AIMessage) and final.content else ""
        return {
            "records": records,
            "summary": summary if isinstance(summary, str) else str(summary),
            "errors": [*state.get("errors", []), *errors],
            "tool_calls": audit,
        }

    async def persist(state: IngestionState) -> dict[str, Any]:
        records: list[RawRecord] = state.get("records", [])
        if state["request"].dry_run:
            return {"written": []}
        written = await store.write_records(records)
        return {"written": written}

    # -- routing -------------------------------------------------------------

    def after_authorize(state: IngestionState) -> Literal["plan", "__end__"]:
        return END if state.get("errors") else "plan"

    def after_reason(state: IngestionState) -> Literal["tools", "harvest"]:
        last = state["messages"][-1]
        has_calls = isinstance(last, AIMessage) and bool(last.tool_calls)
        if has_calls and state.get("steps", 0) < max_steps:
            return "tools"
        if has_calls:
            # Budget exhausted mid-plan: harvest what we have rather than
            # looping. The run reports the truncation.
            logger.warning("Ingestion hit the %d-step budget with tool calls pending", max_steps)
        return "harvest"

    # -- wiring --------------------------------------------------------------

    graph = StateGraph(IngestionState)
    graph.add_node("authorize", authorize)
    graph.add_node("plan", plan)
    graph.add_node("reason", reason)
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_node("harvest", harvest)
    graph.add_node("persist", persist)

    graph.add_edge(START, "authorize")
    graph.add_conditional_edges("authorize", after_authorize, {"plan": "plan", END: END})
    graph.add_edge("plan", "reason")
    graph.add_conditional_edges("reason", after_reason, {"tools": "tools", "harvest": "harvest"})
    graph.add_edge("tools", "reason")
    graph.add_edge("harvest", "persist")
    graph.add_edge("persist", END)

    return graph.compile()
