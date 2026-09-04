# Knowledge Brain Platform — Backend

A governed, scope-aware enterprise knowledge platform. This repository currently
implements the **first slice**: the ingestion layer — a LangGraph agent that
connects to enterprise sources over **MCP**, pulls source material under policy,
and persists it with full provenance. It is triggered over an **HTTP API**.

> The Brain is not the storage. The Brain is the intelligence, governance,
> context, and contracts around the knowledge.

## What exists today

| Layer | Status |
|---|---|
| **Ingestion** | Built — MCP-connected LangGraph agent, API-triggered |
| **Curation** | Canonical `KnowledgeObject` defined; graph not built |
| **Publish** | Not started |
| **Navigation** | Raw read-back endpoints only |

## Architecture

```
        HTTP API  (the platform contract — console, SDK, MCP, agents are clients)
             │
       Policy Engine        identity → role → statement → decision   (default-deny)
             │
      Ingestion Agent (LangGraph)
             │
   ┌─────────┴──────────┐
   │                    │
 LiteLLM gateway    MCP Source Connector
 (model access)     (Jira · Confluence · GitHub · …)
             │
   harvest → provenance-stamped RawRecord → RawStore
```

The ingestion graph:

```
authorize ─▶ plan ─▶ reason ⇄ tools ─▶ harvest ─▶ persist
    │
    └─▶ END (policy denial — no source system is ever contacted)
```

### Design commitments

- **Governance precedes retrieval.** The policy check runs before the platform
  opens a session against any source system.
- **Scope drives behaviour, not just filing.** The system prompt changes with
  the scope level: an application scope is told to pursue depth, a program scope
  breadth and cross-system relationships.
- **The brain is the write boundary.** MCP servers expose write tools; the
  registry's `tools.allow` list means the agent never sees them.
- **Provenance on every record.** Source, server, tool, tool arguments,
  timestamp, run id and principal — so any downstream claim is traceable.
- **Sources are configuration.** Adding a source is a YAML entry; the agent
  discovers its tools at runtime.
- **Storage is replaceable.** Everything depends on the `RawStore` protocol.
  The filesystem implementation was chosen because it is inspectable.

## Quick start

```bash
uv venv --python 3.11 && uv pip install -e ".[dev]"
cp .env.example .env      # set KB_LLM_BASE_URL / KB_LLM_API_KEY and source creds
```

Run the API:

```bash
.venv/bin/kb serve            # or: uvicorn kb_brain.api.app:app --reload
```

Check which sources are reachable:

```bash
curl localhost:8000/v1/sources
.venv/bin/kb sources list     # same data, as a table
```

Trigger an ingestion run:

```bash
curl -X POST "localhost:8000/v1/ingestions?wait=true" \
  -H "X-KB-Principal: platform-admin" \
  -H "Content-Type: application/json" \
  -d '{
        "scope": "program:payments/application:abc",
        "sources": ["jira", "confluence"],
        "objective": "Capture the open defects and the architecture page for application ABC",
        "hints": {"jira_project": "ABC"}
      }'
```

Drop `?wait=true` to get a `run_id` back immediately and poll
`GET /v1/ingestions/{run_id}`.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/sources` | Configured sources and per-server readiness |
| `GET` | `/v1/sources/{source}/tools` | Capabilities the brain will use |
| `POST` | `/v1/ingestions` | Trigger the LangGraph ingestion run |
| `GET` | `/v1/ingestions/{run_id}` | Run status and audit trail |
| `GET` | `/v1/knowledge/raw` | Captured records for a scope |
| `GET` | `/v1/knowledge/raw/{id}` | One record with full provenance |

## Scopes

Wire format is a path of `level:key` segments, coarsest first:

```
application:abc
program:payments/application:abc
domain:finance/program:payments/application:abc/component:ledger-api
```

Levels: `enterprise · domain · program · application · component · knowledge`.
A policy grant on `program:payments/*` covers every application beneath it.

## Configuration

- **`config/mcp_servers.yaml`** — the source registry. A server that fronts
  several logical sources (mcp-atlassian serves both Jira and Confluence) maps
  each source to its tool patterns, so records are attributed correctly.
  `requires_env` gates readiness: an uncredentialed source is *reported*, not
  fatal.
- **`config/policies.yaml`** — roles, statements and principals. Default-deny;
  an explicit `deny` beats every `allow`.
- **`.env`** — gateway URL/key, agent limits, and the credentials the MCP
  servers themselves read.

Model access goes through the LiteLLM gateway so keys, budgets and per-team
spend stay central. Requests carry `x-litellm-tags` with the principal and
scope, so spend is attributable to *who asked for what*, not to "the platform".

## Tests

```bash
.venv/bin/python -m pytest -q     # 51 tests
```

The suite spawns a real MCP server over stdio (`tests/fake_mcp_server.py`) and
completes a real handshake — connectivity is exercised, not mocked. Model
reasoning is replayed from a script so runs are deterministic and cost nothing.

To exercise the fixture source outside the test suite, point `KB_PYTHON` at an
interpreter that has this project installed:

```bash
export KB_PYTHON="$PWD/.venv/bin/python"
```

## Specification

`docs/IMPLEMENTATION_SPEC.md` specifies what is built: contracts, data model,
governance semantics, config schema, API surface, extension points, and
traceability back to the concept spec's nine principles.

## Known gaps

- **Identity is a shim.** `X-KB-Principal` is trusted as sent. Replace
  `resolve_principal` in `src/kb_brain/api/app.py` with verified OIDC/JWT claims
  before any non-local deployment. Everything downstream already re-authorizes.
- **Background runs are in-process.** The run registry is a dict; it does not
  survive a restart and does not work across replicas. Move to a queue or
  LangGraph Platform persistence before scaling out.
- **No curation.** `KnowledgeObject` is defined but nothing populates it.
- **Harness behaviour is prompt-level.** Scope currently changes the prompt and
  the policy surface. Per-scope curation and publishing rules are still to come.
