"""Graph state for the ingestion harness."""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from ...canonical import RawRecord
from ...scope import ScopeRef


class IngestionRequest(BaseModel):
    """What a caller asks the brain to pull, and into which scope."""

    scope: ScopeRef
    sources: list[str] = Field(min_length=1, description="e.g. ['jira', 'confluence']")
    objective: str = Field(
        description="What to retrieve, in plain language. The agent plans tool calls from this."
    )
    hints: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional source-specific anchors: project keys, space keys, repos, JQL.",
    )
    max_records: int | None = None
    dry_run: bool = Field(
        default=False, description="Run discovery and retrieval but skip persistence."
    )


class IngestionState(TypedDict, total=False):
    """LangGraph state. Messages accumulate; everything else is replaced."""

    request: IngestionRequest
    principal: str
    run_id: str
    messages: Annotated[list[AnyMessage], add_messages]
    tool_names: list[str]
    steps: int
    records: list[RawRecord]
    tool_calls: list[dict[str, Any]]
    written: list[str]
    summary: str
    errors: list[str]
