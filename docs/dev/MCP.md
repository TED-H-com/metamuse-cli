# MetaMuse MCP Stdio Server (`src/metamuse/mcp.py`)

This document describes the B14 (round 1) Model Context Protocol (MCP) stdio server implementation for MetaMuse (`FR-001`, `FR-018`, `AT-026` stdio subset).

---

## 1. Entry Point and Shared Application Seam

The MCP server runs in-process over standard input/output when invoked via:

```bash
metamuse --workspace <path> mcp
```

`cli.py` constructs an `app.AppContext` (`store`, `jobs`, `config`, `workspace`) and calls `mcp.run(ctx)`. `mcp.py` contains no domain or business logic:

1. **Tool catalogue and schemas**: Loaded at import time from the checked-in generated contracts (`docs/design/contracts/mcp-tools.json` and `src/metamuse/data/schemas/<op>.input.json`), with `src/metamuse/data/schemas/envelope.json` as the object-rooted `outputSchema` for every tool.
2. **Input validation**: Every `tools/call` validates its arguments through `models.TOOL_REQUEST_MODELS[name].model_validate(arguments)`. Validation failures are converted via `models.validation_error(exc)` into a standard `E_INPUT_INVALID` `Envelope`.
3. **Dispatch**: Validated request models are passed to `app.TOOL_FUNCTIONS[name](ctx, req)`, the exact same `@application`-decorated entry points used by the CLI.
4. **Result envelope**: Every tool returns a single `CallToolResult` containing:
   - `structuredContent`: the serialized `Envelope` dict (`schema_version`, `operation`, `request_id`, `status`, `summary`, `data`, `refs`, `warnings`, `error`).
   - `content`: a single `TextContent(type="text", text=...)` carrying the identical canonical JSON serialization of that `Envelope`.
   - `isError`: `True` when `envelope.status == "error"`, `False` otherwise (`ok` or `accepted`).
   - When `envelope.status == "error"`, `envelope.error` always includes `code`, `retry_class`, and a non-null `next_action`.

---

## 2. SDK and Protocol Version

- **Dependency**: `mcp==2.3.0` (`mcp-types==2.3.0`, MIT License).
- **Negotiated protocol version**: `2025-11-25` (`mcp.client.session.LATEST_HANDSHAKE_VERSION`) when paired with the official MCP Python SDK stdio client (`mcp.client.stdio.stdio_client` + `ClientSession.initialize()`).
- **Dual-era support**: `mcp.server.lowlevel.server.Server` (`serve_dual_era_loop`) supports MCP protocol versions `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`, and `2026-07-28` (`mcp.types.LATEST_PROTOCOL_VERSION`).

---

## 3. Registered P0 Tools and Annotation Table

Exactly the 15 P0 tools defined in `docs/design/contracts/mcp-tools.json` are registered. Human-only interactive commands (`auth *`, `permit *`, `publish`, `gateway`, `mcp`) are never exposed as MCP tools.

Tool annotations are **advisory hints only** and are **never used for authorization**. Authorization and scope checks (`collect`, `muse:send`, `publish:*`) are enforced by the core store/policy layer.

- `read_only_hint=True` is set **only** for the 5 purely local read operations (`metamuse_inspect`, `metamuse_job_get`, `metamuse_job_list`, `metamuse_search`, `metamuse_draft_preview`).
- Any tool that can open a remote session, send messages, refresh or read remote history (`metamuse_muse_open`, `metamuse_muse_send`, `metamuse_muse_read`, `metamuse_collect`), or mutate local workspace state (`metamuse_project_create`, `metamuse_job_control`, `metamuse_ingest_add`, `metamuse_pack_build`, `metamuse_draft_save`, `metamuse_review_record`) sets `read_only_hint=False`.

| Tool Name | Operation | Request Model | `readOnlyHint` | `openWorldHint` | `destructiveHint` | `idempotentHint` | Classification / Rationale |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| `metamuse_inspect` | `inspect` | `InspectRequest` | `True` | `False` | `False` | `True` | Purely local read of workspace, schema, and health metadata. |
| `metamuse_project_create` | `project.create` | `ProjectCreateRequest` | `False` | `False` | `False` | `False` | Local state write: creates a new project row in SQLite. |
| `metamuse_job_get` | `job.get` | `JobGetRequest` | `True` | `False` | `False` | `True` | Purely local read of a job record by `job_id`. |
| `metamuse_job_list` | `job.list` | `JobListRequest` | `True` | `False` | `False` | `True` | Purely local read of jobs for an explicit `project_id`. |
| `metamuse_job_control` | `job.control` | `JobControlRequest` | `False` | `False` | `True` | `False` | Mutates job lifecycle state (`pause`, `resume`, `cancel`, `retry`). |
| `metamuse_collect` | `collect` | `CollectRequest` | `False` | `True` | `False` | `False` | Enqueues/runs external X collection and persists collected posts/jobs. |
| `metamuse_ingest_add` | `ingest.add` | `IngestAddRequest` | `False` | `False` | `False` | `False` | Local state write: ingests source artifacts and FTS chunks into the store. |
| `metamuse_search` | `search` | `SearchRequest` | `True` | `False` | `False` | `True` | Purely local FTS5/filtered search over stored project items. |
| `metamuse_pack_build` | `pack.build` | `PackBuildRequest` | `False` | `False` | `False` | `False` | Local state write: persists an immutable context pack record. |
| `metamuse_muse_open` | `muse.open` | `MuseOpenRequest` | `False` | `True` | `False` | `False` | Opens or binds a Muse conversation thread and persists thread state. |
| `metamuse_muse_send` | `muse.send` | `MuseSendRequest` | `False` | `True` | `False` | `False` | Sends a prompt to the remote Muse provider and records messages/jobs. |
| `metamuse_muse_read` | `muse.read` | `MuseReadRequest` | `False` | `True` | `False` | `False` | Reads/refreshes Muse thread history and reconciles local thread state. |
| `metamuse_draft_save` | `draft.save` | `DraftSaveRequest` | `False` | `False` | `False` | `False` | Local state write: creates an immutable draft revision in the store. |
| `metamuse_draft_preview` | `draft.preview` | `DraftPreviewRequest` | `True` | `False` | `False` | `True` | Purely local read/validation of a stored draft revision against channel constraints. |
| `metamuse_review_record` | `review.record` | `ReviewRecordRequest` | `False` | `False` | `False` | `False` | Local state write: records a review decision for a draft revision. |

---

## 4. Stdout Cleanliness

The MCP stdio transport requires that `stdout` carry **only** newline-delimited JSON-RPC 2.0 protocol messages:

1. Before entering the MCP session loop, `mcp.run(ctx)` captures the binary protocol output stream (`sys.stdout.buffer`) for the MCP transport, duplicates `sys.stderr`'s file descriptor onto fd `1` (`os.dup2(stderr_fd, stdout_fd)` when supported by the OS streams), and rebinds Python's `sys.stdout = sys.stderr`.
2. Any `print(...)`, `sys.stdout.write(...)`, `warnings.warn(...)`, root/module `logging` output, or uncaught traceback from feature code or third-party libraries is routed to `stderr`.
3. Every tool call executes inside an extra `_redirect_stdout_to_stderr()` guard on the worker thread so `sys.stdout` remains bound to `sys.stderr` throughout synchronous handler execution.

---

## 5. Explicit State Handles and Job Cancellation Semantics

1. **No implicit session state**: The MCP server holds no per-connection "current project", "current thread", or "current draft". Every tool call must pass `project_id`, `job_id`, `thread_id`, or `revision_id` explicitly as defined by its request schema. Creating a project with `metamuse_project_create` never sets a default `project_id` for subsequent calls.
2. **Client cancellation vs. job lifecycle**: Each `tools/call` executes the synchronous application handler in a worker thread (`anyio.to_thread.run_sync(..., abandon_on_cancel=True)`). If an MCP client cancels or abandons an in-flight `tools/call` request, the server never cancels an already-accepted background job in SQLite. Only an explicit `metamuse_job_control` call with `action="cancel"` transitions a job to `canceled`.

---

## 6. Pending Items (Out of Scope for B14 Round 1)

- **Attached background worker**: In round 1, `metamuse mcp` does not start an attached background worker loop; operations that enqueue jobs return `status="accepted"` with `worker_available: false` and `state: "queued"` honestly when no worker is active.
- **MCP resources**: Resource endpoints (`resources/list`, `resources/read`) are not registered in round 1; all P0 interactions go through the 15 tools.
- **External MCP host pairing**: Live host configuration and pairing with Claude Code, Codex, Grok, or Antigravity are deferred to a separately authorized round.
