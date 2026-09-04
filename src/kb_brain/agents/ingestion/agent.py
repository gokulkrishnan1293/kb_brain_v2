"""Ingestion agent: the composition root for the ingestion harness.

Wires governance, source discovery, the model gateway and storage into one
runnable unit. Every interface (HTTP API, CLI, MCP server, SDK) drives this
same object, so the business logic exists exactly once.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ...canonical import RawRecord, new_run_id, utcnow
from ...errors import PolicyDenied
from ...governance import Action, PolicyEngine
from ...llm.gateway import build_chat_model
from ...mcp.connector import SourceConnector
from ...settings import Settings, get_settings
from ...storage.base import RawStore, RunRecord
from .graph import build_ingestion_graph
from .state import IngestionRequest, IngestionState

logger = logging.getLogger(__name__)


class IngestionResult(BaseModel):
    """What a caller gets back from a run."""

    run_id: str
    status: str
    principal: str
    scope: str
    sources: list[str]
    records_captured: int = 0
    records_written: int = 0
    record_ids: list[str] = Field(default_factory=list)
    tools_available: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    errors: list[str] = Field(default_factory=list)


class IngestionAgent:
    def __init__(
        self,
        *,
        connector: SourceConnector,
        policy: PolicyEngine,
        store: RawStore,
        settings: Settings | None = None,
    ) -> None:
        self._connector = connector
        self._policy = policy
        self._store = store
        self._settings = settings or get_settings()

    async def run(self, request: IngestionRequest, *, principal_id: str) -> IngestionResult:
        settings = self._settings
        principal = self._policy.resolve_principal(principal_id)
        run_id = new_run_id()
        scope_label = request.scope.render()

        # Gate before connecting. A denied principal must not cause the platform
        # to open a session against a source system at all.
        for source in request.sources:
            decision = self._policy.authorize(
                principal, Action.INGEST, request.scope, source=source
            )
            if not decision.allowed:
                await self._record_run(
                    run_id,
                    principal.id,
                    request,
                    status="denied",
                    errors=[decision.reason],
                    policy_decision=decision.model_dump(mode="json"),
                )
                raise PolicyDenied(
                    f"{principal.id} may not ingest '{source}' into {scope_label}: "
                    f"{decision.reason}",
                    reason=decision.reason,
                )

        tools, tool_sources = await self._discover(request.sources)

        model = build_chat_model(
            settings,
            tags=[
                f"principal:{principal.id}",
                f"scope:{scope_label}",
                "stage:ingestion",
            ],
        )
        graph = build_ingestion_graph(
            model=model,
            tools=tools,
            store=self._store,
            policy=self._policy,
            principal=principal,
            tool_sources=tool_sources,
            max_steps=settings.max_agent_steps,
            max_records=settings.max_records_per_run,
        )

        initial: IngestionState = {
            "request": request,
            "principal": principal.id,
            "run_id": run_id,
            "messages": [],
            "errors": [],
        }

        logger.info(
            "ingestion start run=%s principal=%s scope=%s sources=%s tools=%d",
            run_id, principal.id, scope_label, request.sources, len(tools),
        )

        # Each tool cycle is two graph steps (reason + tools); add headroom for
        # the fixed nodes so LangGraph's guard never fires before ours does.
        final = await graph.ainvoke(
            initial, config={"recursion_limit": settings.max_agent_steps * 2 + 10}
        )

        records: list[RawRecord] = final.get("records", [])
        written: list[str] = final.get("written", [])
        errors: list[str] = final.get("errors", [])
        if errors and not records:
            status = "failed"
        elif errors:
            status = "partial"
        elif not records:
            # Retrieval completed but returned nothing. Never report this as
            # success: an ingestion that captured no knowledge is a signal, not
            # a result -- usually a bad objective, a wrong anchor, or a scope
            # the source has nothing for.
            status = "empty"
        else:
            status = "succeeded"

        await self._record_run(
            run_id,
            principal.id,
            request,
            status=status,
            errors=errors,
            record_ids=written or [record.record_id for record in records],
            tool_calls=final.get("tool_calls", []),
            summary=final.get("summary"),
        )

        return IngestionResult(
            run_id=run_id,
            status=status,
            principal=principal.id,
            scope=scope_label,
            sources=request.sources,
            records_captured=len(records),
            records_written=len(written),
            record_ids=written,
            tools_available=final.get("tool_names", []),
            tool_calls=final.get("tool_calls", []),
            summary=final.get("summary"),
            errors=errors,
        )

    # -- helpers --------------------------------------------------------------

    async def _discover(self, sources: list[str]) -> tuple[list[BaseTool], dict[str, str]]:
        """Collect tools and remember which source each tool belongs to.

        The mapping is what lets a harvested record name its true origin instead
        of the request's first source.
        """
        tools: dict[str, BaseTool] = {}
        tool_sources: dict[str, str] = {}
        for source in sources:
            for tool in await self._connector.tools_for(source):
                tools.setdefault(tool.name, tool)
                tool_sources.setdefault(tool.name, source)
        return list(tools.values()), tool_sources

    async def _record_run(
        self,
        run_id: str,
        principal_id: str,
        request: IngestionRequest,
        *,
        status: str,
        errors: list[str] | None = None,
        record_ids: list[str] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        summary: str | None = None,
        policy_decision: dict[str, Any] | None = None,
    ) -> None:
        await self._store.write_run(
            RunRecord(
                run_id=run_id,
                principal=principal_id,
                scope=request.scope,
                sources=request.sources,
                objective=request.objective,
                status=status,
                finished_at=utcnow(),
                record_ids=record_ids or [],
                tool_calls=tool_calls or [],
                policy_decision=policy_decision,
                summary=summary,
                errors=errors or [],
            )
        )
