"""The canonical knowledge model.

Ingestion produces ``RawRecord``s: source payloads captured verbatim with full
provenance. Curation (a later layer) turns those into ``KnowledgeObject``s.
Markdown, embeddings, graphs and search indexes are all derived from the
canonical object -- never the other way round.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .scope import ScopeRef


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


def content_hash(payload: Any) -> str:
    """Stable hash of a payload, used for idempotency and change detection."""
    if isinstance(payload, str):
        blob = payload.encode("utf-8")
    else:
        blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ContentType(StrEnum):
    JSON = "json"
    TEXT = "text"
    MARKDOWN = "markdown"


class Classification(StrEnum):
    """Handling class. Policy decides what each class permits."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Provenance(BaseModel):
    """Where a record came from and under whose authority it was pulled.

    This is what makes an answer defensible: every downstream claim can be
    traced back to a source system, a tool call, and an authorized principal.
    """

    source: str = Field(description="Logical source, e.g. 'jira', 'confluence', 'github'.")
    server: str = Field(description="MCP server that served the call.")
    tool: str = Field(description="MCP tool invoked.")
    tool_args: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: datetime = Field(default_factory=utcnow)
    run_id: str
    principal: str


class RawRecord(BaseModel):
    """A single unit of source material, captured but not yet curated."""

    record_id: str
    source: str
    external_id: str | None = None
    title: str | None = None
    scope: ScopeRef
    content_type: ContentType = ContentType.JSON
    content: Any
    content_hash: str
    classification: Classification = Classification.INTERNAL
    provenance: Provenance
    labels: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        *,
        source: str,
        scope: ScopeRef,
        content: Any,
        provenance: Provenance,
        external_id: str | None = None,
        title: str | None = None,
        content_type: ContentType = ContentType.JSON,
        classification: Classification = Classification.INTERNAL,
        labels: dict[str, str] | None = None,
    ) -> RawRecord:
        digest = content_hash(content)
        # Identity is the source's own id when it has one, so re-runs update in
        # place instead of accumulating duplicates; otherwise fall back to content.
        identity = external_id or digest
        seed = f"{source}:{scope.render()}:{identity}".encode()
        record_id = hashlib.sha256(seed).hexdigest()[:32]
        return cls(
            record_id=record_id,
            source=source,
            external_id=external_id,
            title=title,
            scope=scope,
            content_type=content_type,
            content=content,
            content_hash=digest,
            classification=classification,
            provenance=provenance,
            labels=labels or {},
        )


class KnowledgeType(StrEnum):
    FACT = "fact"
    DECISION = "decision"
    ARCHITECTURE = "architecture"
    PROCESS = "process"
    BUSINESS_RULE = "business_rule"
    DEPENDENCY = "dependency"
    USER_FLOW = "user_flow"
    TECHNICAL_FLOW = "technical_flow"
    DEFECT = "defect"
    OWNERSHIP = "ownership"
    SUMMARY = "summary"


class Relationship(BaseModel):
    predicate: str = Field(description="e.g. 'depends_on', 'owned_by', 'implements'.")
    target: str = Field(description="Knowledge object id or scope reference.")
    confidence: float = 1.0


class KnowledgeObject(BaseModel):
    """The canonical curated unit. Populated by the curation layer.

    Defined here so ingestion, storage and the API already agree on the target
    shape; the curation graph is the next slice to build.
    """

    id: str = Field(default_factory=lambda: f"ko_{uuid4().hex[:16]}")
    scope: ScopeRef
    type: KnowledgeType
    title: str
    content: str
    summary: str | None = None
    source_records: list[str] = Field(
        default_factory=list, description="RawRecord ids this was curated from."
    )
    owner: str | None = None
    classification: Classification = Classification.INTERNAL
    relationships: list[Relationship] = Field(default_factory=list)
    version: int = 1
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
