"""End-to-end ingestion: scripted reasoning, real MCP tools, real persistence."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kb_brain.agents.ingestion import IngestionAgent, IngestionRequest
from kb_brain.errors import PolicyDenied
from kb_brain.scope import ScopeRef
from tests.scripted_model import ScriptedChatModel

SCOPE = ScopeRef.parse("program:payments/application:abc")


def _script() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "jira_search", "args": {"jql": "project = ABC and Bug"}, "id": "c1"},
                {"name": "confluence_get_page", "args": {"page_id": "ABC-ARCH"}, "id": "c2"},
            ],
        ),
        AIMessage(content="Pulled the ABC bug list from Jira and the architecture page."),
    ]


@pytest.fixture
def agent(connector, policy, store, settings) -> IngestionAgent:
    return IngestionAgent(
        connector=connector, policy=policy, store=store, settings=settings
    )


@pytest.fixture(autouse=True)
def _use_scripted_model(monkeypatch):
    model = ScriptedChatModel(script=_script())
    monkeypatch.setattr(
        "kb_brain.agents.ingestion.agent.build_chat_model", lambda *a, **k: model
    )
    return model


async def test_run_captures_and_persists_records(agent: IngestionAgent, store):
    request = IngestionRequest(
        scope=SCOPE,
        sources=["jira", "confluence"],
        objective="Capture known defects and the architecture page for ABC",
    )
    result = await agent.run(request, principal_id="admin")

    assert result.status == "succeeded"
    # The JQL matches two bugs; the envelope is exploded into one record per
    # issue, plus one record for the Confluence page.
    assert result.records_captured == 3
    assert result.records_written == 3
    assert "architecture page" in (result.summary or "")

    stored = await store.list_records(SCOPE)
    assert {record.external_id for record in stored} == {"ABC-101", "ABC-103", "ABC-ARCH"}
    assert {record.source for record in stored} == {"jira", "confluence"}


async def test_provenance_is_attached_to_every_record(agent: IngestionAgent, store):
    request = IngestionRequest(
        scope=SCOPE, sources=["jira", "confluence"], objective="defects"
    )
    result = await agent.run(request, principal_id="admin")

    stored = await store.list_records(SCOPE)
    assert stored
    for record in stored:
        assert record.provenance.principal == "admin"
        assert record.provenance.run_id == result.run_id
        assert record.provenance.tool in {"jira_search", "confluence_get_page"}
        assert record.content_hash


async def test_run_is_idempotent_across_repeats(agent: IngestionAgent, store):
    request = IngestionRequest(scope=SCOPE, sources=["jira"], objective="defects")
    await agent.run(request, principal_id="admin")
    first = len(await store.list_records(SCOPE, limit=500))
    await agent.run(request, principal_id="admin")
    second = len(await store.list_records(SCOPE, limit=500))
    assert first == second, "re-running must converge, not duplicate"


async def test_dry_run_captures_without_persisting(agent: IngestionAgent, store):
    request = IngestionRequest(
        scope=SCOPE, sources=["jira"], objective="defects", dry_run=True
    )
    result = await agent.run(request, principal_id="admin")
    assert result.records_captured > 0
    assert result.records_written == 0
    assert await store.list_records(SCOPE) == []


async def test_policy_blocks_run_before_any_source_is_touched(agent: IngestionAgent, store):
    request = IngestionRequest(
        scope=SCOPE, sources=["confluence"], objective="anything"
    )
    with pytest.raises(PolicyDenied, match="not permitted|may not ingest"):
        await agent.run(request, principal_id="jira-bot")

    # The denial is still auditable.
    assert await store.list_records(SCOPE) == []


async def test_run_audit_record_is_written(agent: IngestionAgent, store):
    request = IngestionRequest(
        scope=SCOPE, sources=["jira", "confluence"], objective="defects and architecture"
    )
    result = await agent.run(request, principal_id="admin")

    run = await store.get_run(result.run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.principal == "admin"
    assert [call["tool"] for call in run.tool_calls] == ["jira_search", "confluence_get_page"]


async def test_step_budget_halts_a_looping_model(connector, policy, store, settings, monkeypatch):
    """A model that never stops calling tools must still terminate and harvest."""
    looping = ScriptedChatModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[{"name": "jira_search", "args": {"jql": "Bug"}, "id": "loop"}],
            )
        ]
    )
    monkeypatch.setattr(
        "kb_brain.agents.ingestion.agent.build_chat_model", lambda *a, **k: looping
    )
    agent = IngestionAgent(connector=connector, policy=policy, store=store, settings=settings)
    result = await agent.run(
        IngestionRequest(scope=SCOPE, sources=["jira"], objective="loop forever"),
        principal_id="admin",
    )
    assert result.status in {"succeeded", "partial"}
    assert looping.calls <= settings.max_agent_steps + 1


async def test_empty_retrieval_is_not_reported_as_success(
    connector, policy, store, settings, monkeypatch
):
    """A run that captures nothing must say so rather than claim success."""
    silent = ScriptedChatModel(script=[AIMessage(content="Nothing matched that objective.")])
    monkeypatch.setattr(
        "kb_brain.agents.ingestion.agent.build_chat_model", lambda *a, **k: silent
    )
    agent = IngestionAgent(connector=connector, policy=policy, store=store, settings=settings)
    result = await agent.run(
        IngestionRequest(scope=SCOPE, sources=["jira"], objective="find nothing"),
        principal_id="admin",
    )
    assert result.status == "empty"
    assert result.records_captured == 0
