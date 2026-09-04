"""Storage contracts.

Git, Postgres, object storage, a vector store -- all implementation details.
The platform depends on this protocol so the backing store can change without
touching ingestion, curation or any consumer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from ..canonical import RawRecord, utcnow
from ..scope import ScopeRef


class RunRecord(BaseModel):
    """The audit trail for one ingestion run."""

    run_id: str
    principal: str
    scope: ScopeRef
    sources: list[str]
    objective: str
    status: str = "running"
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    record_ids: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    policy_decision: dict[str, Any] | None = None
    summary: str | None = None
    errors: list[str] = Field(default_factory=list)


class RawStore(Protocol):
    """Persistence for captured source material and run audit records."""

    async def write_records(self, records: list[RawRecord]) -> list[str]: ...

    async def list_records(self, scope: ScopeRef, *, limit: int = 100) -> list[RawRecord]: ...

    async def get_record(self, record_id: str) -> RawRecord | None: ...

    async def write_run(self, run: RunRecord) -> None: ...

    async def get_run(self, run_id: str) -> RunRecord | None: ...
