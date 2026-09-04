# Knowledge Brain Platform — Implementation Specification

**Document Type:** Implementation Specification
**Status:** Implemented — Slice 1 (Ingestion)
**Version:** 0.1
**Companion to:** *Knowledge Brain Platform — Architecture Specification* v0.1 (concept)

---

## 1. Purpose and Standing

The concept specification describes the target platform. This document describes
**what is actually built**, precisely enough to review, extend, or hand over.

Where the two disagree, the concept spec states intent and this document states
fact. Anything not described here is not implemented, regardless of what the
concept spec says.

### 1.1 Scope of this slice

| Concept layer | Status | Notes |
|---|---|---|
| **Ingestion** | Implemented | MCP-connected LangGraph agent, API-triggered, policy-gated |
| **Curation** | Not implemented | Target model (`KnowledgeObject`) defined; nothing populates it |
| **Publish** | Not implemented | — |
| **Navigation** | Partial | Raw read-back endpoints only; no console, SDK, or MCP server |

Cross-cutting concerns implemented in this slice: the **scope model**, the
**governance core**, the **canonical record model**, the **storage abstraction**,
the **model gateway**, and the **HTTP API contract**.

---

## 2. Component Architecture

```
                    HTTP API  (the platform contract)
                          │
                   Policy Engine          default-deny, IAM-shaped
                          │
                  Ingestion Agent (LangGraph harness)
                          │
            ┌─────────────┴──────────────┐
            │                            │
     LiteLLM Gateway            MCP Source Connector
     (model access)          (Jira · Confluence · GitHub · …)
                          │
        harvest → provenance-stamped RawRecord → RawStore
```

### 2.1 Module map

| Module | Responsibility |
|---|---|
| `kb_brain/scope.py` | The six-level scope model: parsing, validation, addressing |
| `kb_brain/canonical.py` | `RawRecord`, `Provenance`, `KnowledgeObject`, identity and hashing |
| `kb_brain/governance/policy.py` | Policy documents, evaluation, decisions |
| `kb_brain/mcp/registry.py` | Declarative source catalogue; per-source tool routing |
| `kb_brain/mcp/connector.py` | The only module that knows MCP exists |
| `kb_brain/llm/gateway.py` | Model access; the only module that knows the provider |
| `kb_brain/storage/base.py` | `RawStore` protocol — the storage contract |
| `kb_brain/storage/filesystem.py` | First `RawStore` implementation |
| `kb_brain/agents/ingestion/` | The ingestion harness (graph, state, prompts, harvest) |
| `kb_brain/api/app.py` | HTTP contract; maps platform errors to status codes |
| `kb_brain/platform.py` | Composition root — assembles everything from config |
| `kb_brain/cli.py` | Thin wrapper over the same objects the API drives |

**Dependency rule.** Modules depend on contracts, not implementations. Only
`connector.py` imports MCP; only `gateway.py` imports a model client; only
`filesystem.py` implements storage. Replacing any of the three is a
single-module change.

---

## 3. Scope Model

### 3.1 Grammar

A scope is a path of `level:key` segments, coarsest first:

```
application:abc
program:payments/application:abc
domain:finance/program:payments/application:abc/component:ledger-api
```

Levels, in hierarchy order:

| Depth | Level | Concept spec |
|---|---|---|
| 1 | `enterprise` | L1 |
| 2 | `domain` | L2 |
| 3 | `program` | L3 |
| 4 | `application` | L4 |
| 5 | `component` | L5 |
| 6 | `knowledge` | L6 |

### 3.2 Semantics and validation

- Keys normalise to lowercase; permitted characters are `[a-z0-9._-]`, and a key
  must begin with an alphanumeric.
- Segments must strictly descend in depth. `application:abc/program:payments`
  and `application:a/application:b` are both rejected.
- A scope need not start at `enterprise`; `application:abc` is a valid,
  fully-qualified reference.
- `leaf` is the most specific segment and determines harness behaviour.
- `ancestors()` yields every enclosing scope, coarsest first. This is what makes
  policy grants inheritable.
- `to_path()` renders `program/payments/application/abc` for storage layout.

Malformed input raises `ScopeError` (HTTP 400).

---

## 4. Canonical Data Model

### 4.1 RawRecord

The unit produced by ingestion: source material captured verbatim, not
interpreted.

| Field | Type | Notes |
|---|---|---|
| `record_id` | `str` | Deterministic; see §4.3 |
| `source` | `str` | Logical source (`jira`, `confluence`, `github`) |
| `external_id` | `str \| None` | The source system's own identifier |
| `title` | `str \| None` | Human-readable label, if one was discoverable |
| `scope` | `ScopeRef` | Where this belongs in the hierarchy |
| `content_type` | `json \| text \| markdown` | |
| `content` | `Any` | The payload as returned by the source |
| `content_hash` | `str` | SHA-256 over canonical JSON; change detection |
| `classification` | `public \| internal \| confidential \| restricted` | Defaults to `internal` |
| `provenance` | `Provenance` | Required — see §4.2 |
| `labels` | `dict[str, str]` | Currently carries the originating tool |

### 4.2 Provenance

Attached to every record without exception. This is what makes a downstream
answer defensible rather than merely plausible.

| Field | Meaning |
|---|---|
| `source` / `server` | Logical source and the MCP server that served the call |
| `tool` / `tool_args` | The exact capability invoked and its arguments |
| `retrieved_at` | UTC timestamp |
| `run_id` | The ingestion run that produced it |
| `principal` | The authorised identity under which it was pulled |

### 4.3 Record identity and idempotency

```
identity   = external_id or content_hash
record_id  = sha256(f"{source}:{scope}:{identity}")[:32]
```

Identity derives from the source system's own id when one exists. Re-running the
same ingestion therefore **converges** — records are overwritten in place rather
than accumulating duplicates. When a source offers no id, content is the
identity, so unchanged content is still stable across runs.

### 4.4 KnowledgeObject (defined, not populated)

The curated target: `id`, `scope`, `type`, `title`, `content`, `summary`,
`source_records`, `owner`, `classification`, `relationships`, `version`,
`confidence`, timestamps, `metadata`.

`KnowledgeType` covers fact, decision, architecture, process, business rule,
dependency, user flow, technical flow, defect, ownership, summary.

It is defined now so ingestion, storage, and the API already agree on the target
shape. **Nothing writes one yet.**

---

## 5. Governance

### 5.1 Model

```
Principal → Roles → Statements → Decision
```

A `Principal` has an `id`, a `kind` (`user` | `agent` | `service`), and roles.
Humans and agents are the same kind of object and are governed identically.

A `Statement` has an `effect` (`allow` | `deny`) and three glob-matched
dimensions:

| Dimension | Matches against |
|---|---|
| `actions` | `knowledge:ingest`, `knowledge:curate`, `knowledge:publish`, `knowledge:read`, `platform:admin` |
| `scopes` | The scope **or any ancestor** |
| `sources` | The logical source of the operation, when one applies |

### 5.2 Evaluation algorithm

1. Unknown principal → resolved to a role-less principal (not an error).
2. No roles → **deny** (`principal has no roles assigned`).
3. Any matching `deny` statement → **deny**, short-circuiting every allow.
4. Any matching `allow` statement → **allow**.
5. Otherwise → **deny** (`no matching allow statement (default deny)`).

Ancestor matching means a grant on `program:payments/*` covers every application
and component beneath that program without enumerating them.

Every evaluation returns a `PolicyDecision` recording principal, action, scope,
source, outcome, reason, and matched role — persisted on the run record.

### 5.3 Enforcement points

Authorization is enforced **twice**, deliberately:

1. **`IngestionAgent.run()`, before source discovery.** A denied principal must
   not cause the platform to open a session against a source system at all. This
   is the real gate; it raises `PolicyDenied` (HTTP 403) and writes a `denied`
   run record.
2. **The graph's `authorize` node.** Re-checks and records the decision in run
   state, so the graph is safe to invoke directly and the decision travels with
   the run.

Read-back endpoints authorize `knowledge:read` against the record's own scope.

---

## 6. Source Integration (MCP)

### 6.1 Registry schema — `config/mcp_servers.yaml`

```yaml
servers:
  <name>:
    description: str
    sources: [str] | {source: [tool-pattern]}   # see §6.2
    transport: stdio | streamable_http | sse
    enabled: bool                               # default true
    command: str                                # stdio
    args: [str]
    env: {KEY: "${VAR}" | "${VAR:-default}"}
    cwd: str
    url: str                                    # http transports
    headers: {Header: "${VAR}"}
    requires_env: [VAR]                         # readiness gate
    tools:
      allow: [pattern]                          # default ["*"]
      deny: [pattern]
```

Environment references are expanded at load time. `${VAR}` and `${VAR:-default}`
are supported; **nesting is not**.

### 6.2 Per-source tool routing

One server commonly fronts several logical sources — `mcp-atlassian` serves both
Jira and Confluence. The shorthand `sources: [github]` maps every tool to that
one source. The mapping form routes tools per source:

```yaml
sources:
  jira: ["jira_*"]
  confluence: ["confluence_*"]
```

Without this, `tools_for("jira")` returns the server's Confluence tools too and
every harvested record is stamped with the wrong origin. Provenance correctness
depends on this mapping being right.

### 6.3 Readiness

`requires_env` gates usability. A server missing credentials is reported as
`not_configured` with the exact missing variables — it does **not** raise. One
misconfigured source must never take the platform down.

`server_status()` reports per server: `enabled`, `ready`, `missing_env`,
`status` (`connected` | `not_configured` | `error`), tool count, and tool names.
Connection failures are unwrapped from `ExceptionGroup` so the reported cause is
actionable (`ProxyError: 403 Forbidden`, not "unhandled errors in a TaskGroup").

### 6.4 Write boundary

MCP servers advertise write tools (create issue, update page). The Knowledge
Brain is the governance boundary for writes, so `tools.allow` is a **read-only
allowlist** and `tools.deny` takes precedence within it. Filtered tools are never
bound to the model — the agent cannot call what it cannot see. The system prompt
additionally instructs against write tools as defence in depth.

### 6.5 Empty and unavailable sources

- Source with no configured server → `SourceUnavailable` (HTTP 424), naming the
  known sources.
- Source whose servers are all uncredentialed → `SourceUnavailable`, naming the
  missing variables per server.
- Partial failure across multiple servers → logged as a warning; usable tools are
  still returned.

---

## 7. Ingestion Harness

### 7.1 Graph

```
START ─▶ authorize ─┬─▶ END                    (policy denial)
                    └─▶ plan ─▶ reason ⇄ tools ─▶ harvest ─▶ persist ─▶ END
```

| Node | Contract |
|---|---|
| `authorize` | Evaluates `knowledge:ingest` per requested source; on denial sets `errors` and routes to END |
| `plan` | Builds the scope-aware system prompt and the task message; records available tool names |
| `reason` | One model call with tools bound; increments the step counter |
| `tools` | `ToolNode` with `handle_tool_errors=True` — a tool error becomes a message, not a crash |
| `harvest` | Converts tool output into `RawRecord`s; builds the tool-call audit trail |
| `persist` | Writes to the `RawStore`, unless `dry_run` |

`reason → tools` continues while the last message carries tool calls **and** the
step budget is unspent; otherwise it routes to `harvest`.

### 7.2 Budgets and termination

- `max_agent_steps` (default 12) bounds reasoning cycles. On exhaustion with
  calls still pending, the run logs a warning and harvests what it has rather
  than looping.
- LangGraph `recursion_limit` is set to `max_agent_steps * 2 + 10`, so the
  harness's own budget always trips first and produces a usable result.
- `max_records_per_run` (default 2000) caps captured records; the overflow is
  recorded as an error on the run rather than silently dropped.

### 7.3 Scope-aware prompting

The system prompt varies by `scope.level` — the harness principle expressed at
prompt level. Application scope is directed toward depth (architecture, flows,
APIs, rules, defects, ownership); program scope toward breadth and
cross-application relationships; domain toward business context; enterprise
toward strategy and standards.

The agent is instructed to retrieve only, never to summarise or interpret —
curation is a later stage, and interpreting here would discard evidence.

### 7.4 Harvest rules

- Tool output is parsed as JSON; on failure it is kept as text.
- Envelopes are **exploded** into individual records using the first list found
  under `issues`, `results`, `values`, `items`, `records`, `data`, `content`,
  `pages`. A search returning 40 issues becomes 40 addressable records, not one
  blob.
- `external_id` is taken from the first present of `key`, `id`, `issueKey`,
  `issue_key`, `pageId`, `page_id`, `number`, `sha`, `path`.
- `title` from the first present of `summary`, `title`, `name`, `subject`,
  `displayName`, `path`.
- Tool errors are collected into run errors and excluded from records.
- Each result is attributed to its true source via the tool→source routing.

### 7.5 Run statuses

| Status | Meaning |
|---|---|
| `succeeded` | Records captured, no errors |
| `partial` | Records captured, with errors |
| `failed` | Errors and no records |
| `empty` | No errors and no records — retrieval completed but captured nothing |
| `denied` | Blocked by policy before any source was contacted |

`empty` is distinct by design. An ingestion that captured nothing is a signal —
usually a bad objective, a wrong anchor, or a scope the source has nothing for —
and must never be reported as success.

### 7.6 Run audit record

Every run writes a `RunRecord`: run id, principal, scope, sources, objective,
status, timestamps, record ids, the tool-call trail (tool, source, arguments,
status, bytes, records produced), the policy decision, the model's closing
summary, and errors.

---

## 8. Model Access

All model traffic leaves through the **LiteLLM gateway**, so keys, budgets, rate
limits, and per-team spend stay central. Callers hold a LiteLLM virtual key, never
a provider credential.

The gateway speaks the OpenAI-compatible protocol; the OpenAI SDK client (via
`langchain-openai`) is the transport. Default model alias: `claude-opus-5`.

Each request carries `x-litellm-tags`:

```
principal:<id>, scope:<scope>, stage:ingestion
```

so spend and traces attribute to *who asked for what*, not to "the platform".

Swapping to a native provider SDK is a change to `llm/gateway.py` only; nothing
above it imports a model class.

---

## 9. Storage

### 9.1 Contract

`RawStore` (protocol): `write_records`, `list_records`, `get_record`,
`write_run`, `get_run`. Git, Postgres, object storage, and vector stores are all
implementations of this contract, not assumptions in the platform.

### 9.2 Filesystem implementation

```
data/raw/<scope path>/<source>/<record_id>.json
data/runs/<run_id>.json
```

Chosen first because it is inspectable — captured knowledge can be read with
`cat`. Blocking I/O runs on a worker thread.

---

## 10. API Contract

The API is the platform contract. Console, SDKs, MCP servers, and agent plugins
are all clients of it; none touches storage directly.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/v1/sources` | Configured sources and per-server readiness |
| `GET` | `/v1/sources/{source}/tools` | Capabilities the brain will use for a source |
| `POST` | `/v1/ingestions` | Trigger an ingestion run |
| `GET` | `/v1/ingestions/{run_id}` | Run status and audit trail |
| `GET` | `/v1/knowledge/raw` | Captured records for a scope |
| `GET` | `/v1/knowledge/raw/{record_id}` | One record with full provenance |

### 10.1 Triggering a run

```http
POST /v1/ingestions?wait=true
X-KB-Principal: platform-admin

{
  "scope": "program:payments/application:abc",
  "sources": ["jira", "confluence"],
  "objective": "Capture the open defects and the architecture page for ABC",
  "hints": {"jira_project": "ABC"},
  "max_records": 500,
  "dry_run": false
}
```

`wait=true` runs synchronously and returns the `IngestionResult`. Omitting it
returns a `run_id` immediately; poll `GET /v1/ingestions/{run_id}`.

`hints` are caller-supplied anchors (project keys, space keys, repositories, JQL)
that the agent is told to use rather than search for.

### 10.2 Error mapping

| Exception | Status | Code |
|---|---|---|
| `ScopeError` | 400 | `invalid_scope` |
| `PolicyDenied` | 403 | `policy_denied` |
| `SourceUnavailable` | 424 | `source_unavailable` |
| `IngestionFailed` | 502 | `ingestion_failed` — defined and mapped, not yet raised |
| `ConfigError` / `KBError` | 500 | `config_error` / `internal_error` |

### 10.3 Identity — known shim

Identity arrives in the `X-KB-Principal` header and is **trusted as sent**. This
is a deliberate, single-function shim (`resolve_principal`) to be replaced with
verified OIDC/JWT claims before any non-local deployment. Everything downstream
treats the principal as untrusted input and re-authorizes.

---

## 11. Configuration Reference

| Variable | Default | Purpose |
|---|---|---|
| `KB_LLM_BASE_URL` | `http://localhost:4000/v1` | LiteLLM gateway |
| `KB_LLM_API_KEY` | — | LiteLLM virtual key |
| `KB_LLM_MODEL` | `claude-opus-5` | Model alias registered in the gateway |
| `KB_LLM_TEMPERATURE` | `0.0` | |
| `KB_LLM_TIMEOUT_SECONDS` | `120` | |
| `KB_LLM_MAX_RETRIES` | `3` | |
| `KB_MCP_CONFIG_PATH` | `config/mcp_servers.yaml` | Source registry |
| `KB_POLICY_CONFIG_PATH` | `config/policies.yaml` | Governance |
| `KB_DATA_DIR` | `data/` | Storage root |
| `KB_MAX_AGENT_STEPS` | `12` | Reasoning cycles per run |
| `KB_MAX_RECORDS_PER_RUN` | `2000` | Record cap |
| `KB_API_HOST` / `KB_API_PORT` | `0.0.0.0` / `8000` | |
| `KB_DEFAULT_PRINCIPAL` | `anonymous` | Used when no identity header is sent |

Source credentials (`JIRA_URL`, `JIRA_API_TOKEN`, `GITHUB_TOKEN`, …) are read by
the **MCP servers**, not by the platform. The platform only checks their presence
via `requires_env`.

---

## 12. Extension Points

| To add… | Do this | Code change |
|---|---|---|
| A source system | Add a server entry to `config/mcp_servers.yaml` | None |
| A role or principal | Add to `config/policies.yaml` | None |
| A governed operation | Add to the `Action` enum, reference it in policy | Small |
| A storage backend | Implement `RawStore`, swap it in `platform.py` | One module |
| A model provider | Replace `llm/gateway.py` | One module |
| An interface (SDK, MCP server) | Call `KnowledgeBrain` / the API | New adapter only |
| A scope level | Add to `ScopeLevel` (order defines hierarchy) and `_SCOPE_GUIDANCE` | Small |

---

## 13. Verification

51 tests, all passing; lint clean.

| Suite | Covers |
|---|---|
| `test_scope.py` | Grammar, hierarchy validation, ancestors, path rendering |
| `test_policy.py` | Default-deny, deny-beats-allow, source and scope restriction, inheritance |
| `test_registry.py` | Env expansion, readiness gating, tool filters |
| `test_mcp_connector.py` | Live stdio MCP handshake, tool discovery and execution, per-source routing, write-tool filtering, error unwrapping |
| `test_ingestion_graph.py` | Capture, provenance, idempotency, dry-run, policy blocking, step budget, empty runs, audit records |
| `test_api.py` | Every endpoint, error mapping, governance over HTTP |

**What is proven vs. simulated.** Tests spawn a real MCP server over stdio
(`tests/fake_mcp_server.py`) and complete a real handshake — connectivity is
exercised, not mocked. Model reasoning is replayed from a script
(`tests/scripted_model.py`) so runs are deterministic and cost nothing.

The gateway path was additionally verified end-to-end against a live
OpenAI-compatible endpoint: API → policy → MCP discovery → gateway → tool calls →
MCP execution → harvest → persist → audit, with `x-litellm-tags` observed on the
request and records landing on disk with correct provenance.

---

## 14. Known Gaps and Non-Goals

**Gaps in this slice:**

1. **Identity is a trusted header.** See §10.3.
2. **Background runs are in-process.** The run registry is a dict — it does not
   survive a restart and does not work across replicas. A queue or LangGraph
   Platform persistence is needed before scaling out.
3. **No curation.** `KnowledgeObject` is defined; nothing populates it.
4. **Harness behaviour is prompt-level only.** Scope currently varies the prompt
   and the policy surface. Per-scope curation, publishing, and ownership rules
   are not implemented — the harness is not yet a configurable object.
5. **No classification logic.** Every record defaults to `internal`; nothing
   derives classification from content or source.
6. **Tool caching is process-lifetime.** Discovered tools are cached until
   `invalidate_cache()`; there is no TTL or change detection.

**Explicit non-goals for this slice:** publishing, search and vector indexes,
graph representation, the console, the SDK, and the outbound MCP server.

---

## 15. Traceability to the Concept Specification

| Principle | Status in this slice |
|---|---|
| 1 — Platform First | **Met.** The API is the contract; the CLI is a client of the same objects |
| 2 — Governance First | **Met for ingestion.** Authorization precedes source contact; publish is not yet built |
| 3 — Scope-Aware Intelligence | **Partial.** Scope drives prompting and policy; not yet curation or publishing |
| 4 — Harness-Driven Behaviour | **Partial.** Behaviour varies by scope, but the harness is not yet configurable |
| 5 — Canonical Knowledge | **Partial.** `RawRecord` is canonical and provenance-complete; `KnowledgeObject` is defined, unpopulated |
| 6 — Storage Independence | **Met.** Everything depends on the `RawStore` protocol |
| 7 — Reusable Plugins | **Met in structure.** Logic lives in `IngestionAgent`; API and CLI are thin adapters |
| 8 — Capability-Based Consumption | **Partial.** Consumers use governed endpoints; no MCP server or SDK yet |
| 9 — SME Knowledge Replication | **Not started.** Requires curation |

---

## 16. Next Slice

Curation is the natural next step: a scope-aware graph that reads `RawRecord`s
and produces `KnowledgeObject`s with relationships, ownership, and confidence —
turning captured material into the contextual knowledge that answers *"why does
this application work this way?"* rather than *"where is the document?"*
