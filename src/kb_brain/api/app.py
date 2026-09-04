"""HTTP API -- the platform contract.

Everything a consumer can do goes through here: the console, SDKs, MCP servers
and agent plugins are all clients of these endpoints. Storage is never touched
directly by a consumer.

Identity currently arrives in the ``X-KB-Principal`` header. That is a
deliberate, clearly-marked shim: replace :func:`resolve_principal` with real
OIDC/JWT verification before this leaves a development environment. Everything
downstream already treats the principal as untrusted input and re-authorizes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..agents.ingestion import IngestionRequest, IngestionResult
from ..canonical import new_run_id
from ..errors import KBError, ScopeError
from ..platform import KnowledgeBrain, get_brain
from ..scope import ScopeRef
from ..settings import get_settings

logger = logging.getLogger(__name__)


# --- request/response models -------------------------------------------------


class IngestBody(BaseModel):
    """Wire form of an ingestion request. ``scope`` is the string form."""

    scope: str = Field(examples=["program:payments/application:abc"])
    sources: list[str] = Field(min_length=1, examples=[["jira", "confluence"]])
    objective: str = Field(
        examples=["Pull the architecture pages and open defects for application ABC"]
    )
    hints: dict[str, Any] = Field(default_factory=dict, examples=[{"jira_project": "ABC"}])
    max_records: int | None = None
    dry_run: bool = False

    def to_request(self) -> IngestionRequest:
        return IngestionRequest(
            scope=ScopeRef.parse(self.scope),
            sources=self.sources,
            objective=self.objective,
            hints=self.hints,
            max_records=self.max_records,
            dry_run=self.dry_run,
        )


class RunAccepted(BaseModel):
    run_id: str
    status: Literal["accepted"] = "accepted"
    poll: str


class RunStatus(BaseModel):
    run_id: str
    status: str
    principal: str
    scope: str
    sources: list[str]
    objective: str
    record_ids: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    summary: str | None = None
    errors: list[str] = Field(default_factory=list)


# --- dependencies ------------------------------------------------------------


def resolve_principal(x_kb_principal: str | None = Header(default=None)) -> str:
    """SHIM: trust the caller's claimed identity.

    Replace with verified token claims. Kept as a single function so there is
    exactly one place to change.
    """
    return x_kb_principal or get_settings().default_principal


def brain_dependency() -> KnowledgeBrain:
    return get_brain()


# --- app ---------------------------------------------------------------------


def create_app(brain: KnowledgeBrain | None = None) -> FastAPI:
    app = FastAPI(
        title="Knowledge Brain Platform",
        version="0.1.0",
        summary="Governed, scope-aware enterprise knowledge platform.",
    )

    if brain is not None:
        app.dependency_overrides[brain_dependency] = lambda: brain

    # In-process run registry. Swap for a queue/worker before running this
    # anywhere with more than one replica.
    background: dict[str, asyncio.Task[IngestionResult]] = {}
    app.state.background_runs = background

    @app.exception_handler(KBError)
    async def _kb_error_handler(_request, exc: KBError):  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.code, "detail": str(exc)},
        )

    @app.get("/health", tags=["platform"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "knowledge-brain"}

    # -- source discovery ----------------------------------------------------

    @app.get("/v1/sources", tags=["sources"])
    async def list_sources(
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> dict[str, Any]:
        """Configured sources and whether each MCP server is actually reachable."""
        return {
            "sources": brain.registry.sources(),
            "servers": await brain.connector.server_status(),
        }

    @app.get("/v1/sources/{source}/tools", tags=["sources"])
    async def list_source_tools(
        source: str,
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> dict[str, Any]:
        """The capabilities the brain is willing to use against a source."""
        tools = await brain.connector.tools_for(source)
        return {
            "source": source,
            "tools": [
                {"name": tool.name, "description": (tool.description or "").strip()[:400]}
                for tool in tools
            ],
        }

    # -- ingestion -----------------------------------------------------------

    @app.post(
        "/v1/ingestions",
        tags=["ingestion"],
        response_model=None,
        status_code=202,
        summary="Trigger an ingestion run (the LangGraph harness)",
    )
    async def create_ingestion(
        body: IngestBody,
        wait: bool = Query(
            default=False,
            description="Run synchronously and return the result instead of a run id.",
        ),
        principal: str = Depends(resolve_principal),
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> IngestionResult | RunAccepted:
        try:
            request = body.to_request()
        except ScopeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if wait:
            return await brain.ingestion.run(request, principal_id=principal)

        run_id = new_run_id()
        task = asyncio.create_task(brain.ingestion.run(request, principal_id=principal))
        background[run_id] = task
        task.add_done_callback(lambda t: _log_background(run_id, t))
        return RunAccepted(run_id=run_id, poll=f"/v1/ingestions/{run_id}")

    @app.get("/v1/ingestions/{run_id}", tags=["ingestion"], response_model=None)
    async def get_ingestion(
        run_id: str,
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> RunStatus | dict[str, Any]:
        task = background.get(run_id)
        if task is not None and not task.done():
            return {"run_id": run_id, "status": "running"}
        if task is not None:
            exc = task.exception()
            if exc is not None:
                return {"run_id": run_id, "status": "failed", "errors": [str(exc)]}
            result = task.result()
            # The agent mints its own run id; surface both so the audit record
            # is findable.
            return {"run_id": run_id, "agent_run_id": result.run_id, **result.model_dump()}

        stored = await brain.store.get_run(run_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
        return RunStatus(
            run_id=stored.run_id,
            status=stored.status,
            principal=stored.principal,
            scope=stored.scope.render(),
            sources=stored.sources,
            objective=stored.objective,
            record_ids=stored.record_ids,
            tool_calls=stored.tool_calls,
            summary=stored.summary,
            errors=stored.errors,
        )

    # -- knowledge readback --------------------------------------------------

    @app.get("/v1/knowledge/raw", tags=["knowledge"])
    async def list_raw(
        scope: str = Query(examples=["application:abc"]),
        limit: int = Query(default=50, ge=1, le=500),
        principal: str = Depends(resolve_principal),
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> dict[str, Any]:
        from ..governance import Action

        try:
            scope_ref = ScopeRef.parse(scope)
        except ScopeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        decision = brain.policy.authorize(principal, Action.READ, scope_ref)
        decision.raise_for_denial()

        records = await brain.store.list_records(scope_ref, limit=limit)
        return {
            "scope": scope_ref.render(),
            "count": len(records),
            "records": [
                {
                    "record_id": record.record_id,
                    "source": record.source,
                    "external_id": record.external_id,
                    "title": record.title,
                    "retrieved_at": record.provenance.retrieved_at,
                    "tool": record.provenance.tool,
                }
                for record in records
            ],
        }

    @app.get("/v1/knowledge/raw/{record_id}", tags=["knowledge"])
    async def get_raw(
        record_id: str,
        principal: str = Depends(resolve_principal),
        brain: KnowledgeBrain = Depends(brain_dependency),
    ) -> dict[str, Any]:
        from ..governance import Action

        record = await brain.store.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown record '{record_id}'")
        decision = brain.policy.authorize(principal, Action.READ, record.scope)
        decision.raise_for_denial()
        return record.model_dump(mode="json")

    return app


def _log_background(run_id: str, task: asyncio.Task[IngestionResult]) -> None:
    if task.cancelled():
        logger.warning("ingestion run %s cancelled", run_id)
        return
    exc = task.exception()
    if exc is not None:
        logger.exception("ingestion run %s failed", run_id, exc_info=exc)


app = create_app()
