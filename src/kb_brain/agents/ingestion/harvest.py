"""Turn raw MCP tool output into provenance-stamped records.

Source systems return wildly different shapes. This module normalises them into
:class:`RawRecord` without interpreting the content -- extraction of meaning is
curation's job, and doing it here would silently discard evidence.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from ...canonical import ContentType, Provenance, RawRecord
from ...scope import ScopeRef

# Keys that commonly hold the collection in a source-system envelope.
_COLLECTION_KEYS = ("issues", "results", "values", "items", "records", "data", "content", "pages")
# Keys that commonly carry a stable external identifier.
_ID_KEYS = ("key", "id", "issueKey", "issue_key", "pageId", "page_id", "number", "sha", "path")
# Keys that commonly carry a human-readable label.
_TITLE_KEYS = ("summary", "title", "name", "subject", "displayName", "path")


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    return None


def _message_text(message: ToolMessage) -> str:
    """MCP results arrive as a string or as a list of content blocks."""
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return str(content)


def _explode(payload: Any) -> list[Any]:
    """Split an envelope into individual items, or keep it whole.

    A search result holding 40 Jira issues should become 40 records, not one
    blob -- otherwise nothing downstream can address a single issue.
    """
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in _COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and value:
                return value
    return [payload]


def tool_call_index(messages: list[AnyMessage]) -> dict[str, dict[str, Any]]:
    """Map tool_call_id -> {name, args} so results can be attributed to a call."""
    index: dict[str, dict[str, Any]] = {}
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls or []:
                index[call["id"]] = {"name": call["name"], "args": call.get("args", {})}
    return index


def harvest_records(
    messages: list[AnyMessage],
    *,
    scope: ScopeRef,
    run_id: str,
    principal: str,
    tool_sources: dict[str, str],
    max_records: int,
) -> tuple[list[RawRecord], list[dict[str, Any]], list[str]]:
    """Extract records, a tool-call audit trail, and any tool errors."""
    index = tool_call_index(messages)
    records: list[RawRecord] = []
    audit: list[dict[str, Any]] = []
    errors: list[str] = []

    for message in messages:
        if not isinstance(message, ToolMessage):
            continue

        call = index.get(message.tool_call_id, {})
        tool_name = call.get("name") or message.name or "unknown"
        tool_args = call.get("args", {})
        source = tool_sources.get(tool_name, "unknown")
        text = _message_text(message)

        entry: dict[str, Any] = {
            "tool": tool_name,
            "source": source,
            "args": tool_args,
            "status": message.status or "success",
            "bytes": len(text),
        }

        if message.status == "error":
            errors.append(f"{tool_name}: {text[:500]}")
            entry["error"] = text[:500]
            audit.append(entry)
            continue

        provenance = Provenance(
            source=source,
            server=source,
            tool=tool_name,
            tool_args=tool_args,
            run_id=run_id,
            principal=principal,
        )

        try:
            payload: Any = json.loads(text)
            content_type = ContentType.JSON
        except (json.JSONDecodeError, TypeError):
            payload = text
            content_type = ContentType.TEXT

        items = _explode(payload) if content_type is ContentType.JSON else [payload]
        produced = 0
        for item in items:
            if len(records) >= max_records:
                errors.append(
                    f"record cap of {max_records} reached; remaining output from "
                    f"{tool_name} was not captured"
                )
                break
            external_id = _first_str(item, _ID_KEYS) if isinstance(item, dict) else None
            title = _first_str(item, _TITLE_KEYS) if isinstance(item, dict) else None
            records.append(
                RawRecord.build(
                    source=source,
                    scope=scope,
                    content=item,
                    provenance=provenance,
                    external_id=external_id,
                    title=title,
                    content_type=(
                        content_type if isinstance(item, (dict, list)) else ContentType.TEXT
                    ),
                    labels={"tool": tool_name},
                )
            )
            produced += 1

        entry["records"] = produced
        audit.append(entry)

    return records, audit, errors
