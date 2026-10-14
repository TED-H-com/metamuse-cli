"""MCP stdio server over the shared application functions."""

from __future__ import annotations

import json
import logging
import sys
import warnings
from collections.abc import Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import anyio.to_thread
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from metamuse import app
from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext

SERVER_NAME = "metamuse"
SERVER_VERSION = "0.1.0.dev0"
SCHEMA_DIR = Path(__file__).resolve().parent / "data" / "schemas"
CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "design" / "contracts" / "mcp-tools.json"
)

# Hints only; never used for authorization decisions (SPEC §9, SDS §11).
TOOL_SPECS: tuple[tuple[str, str, dict[str, bool]], ...] = (
    (
        "metamuse_inspect",
        "本地能力檢查，不觸網。",
        {
            "read_only_hint": True,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    ),
    (
        "metamuse_project_create",
        "建立project。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_collect",
        "先保存job；遠端需grant；accepted不是完成。",
        {
            "read_only_hint": False,
            "open_world_hint": True,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_job_get",
        "讀取持久化job狀態。",
        {
            "read_only_hint": True,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    ),
    (
        "metamuse_job_list",
        "分頁列工作。",
        {
            "read_only_hint": True,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    ),
    (
        "metamuse_job_control",
        "本地恢復／取消；不重送不明外部行為。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_search",
        "project範圍的來源查詢。",
        {
            "read_only_hint": True,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    ),
    (
        "metamuse_packet_prepare",
        "固定來源版本的分享前預覽。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_muse_open",
        "主題本身會外送，遠端create不明要對帳。",
        {
            "read_only_hint": False,
            "open_world_hint": True,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_muse_send",
        "核准範圍內問題與packet；只提交，不宣告研究完成。",
        {
            "read_only_hint": False,
            "open_world_hint": True,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_muse_read",
        "預設本地，refresh需有效存取範圍；可回refresh_job_id。",
        {
            "read_only_hint": False,
            "open_world_hint": True,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_muse_close_exchange",
        "結算host輪次；不等於遠端取消。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_draft_save",
        "只保存host提供文字；更新要expected_revision。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
    (
        "metamuse_draft_preview",
        "檢查草稿，不發布。",
        {
            "read_only_hint": True,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": True,
        },
    ),
    (
        "metamuse_export",
        "本地artifact job，不上傳或發布。",
        {
            "read_only_hint": False,
            "open_world_hint": False,
            "destructive_hint": False,
            "idempotent_hint": False,
        },
    ),
)

TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    name: dict(annotations) for name, _, annotations in TOOL_SPECS
}

DEFAULT_NEXT_ACTIONS: dict[m.ErrorCode, str] = {
    m.ErrorCode.INPUT_INVALID: "Fix the request arguments to match inputSchema and retry.",
    m.ErrorCode.REFERENCE_INVALID: "Pass an existing resource ID belonging to the target project.",
    m.ErrorCode.SCOPE_REQUIRED: "Create or supply the required local permit via the CLI.",
    m.ErrorCode.SCOPE_DENIED: "Adjust workspace policy or request a permit covering this scope.",
    m.ErrorCode.AUTH_REQUIRED: "Import valid credentials via the local CLI before retrying.",
    m.ErrorCode.DEPENDENCY_MISSING: "Install the required external dependency and run inspect.",
    m.ErrorCode.UNSUPPORTED: (
        "Call metamuse_inspect to check capabilities implemented in this build."
    ),
    m.ErrorCode.PROTOCOL_MISMATCH: "Verify database schema and protocol compatibility.",
    m.ErrorCode.ACCESS_DENIED: "Check account access and permissions on the target platform.",
    m.ErrorCode.CHECKPOINT_REQUIRED: (
        "Resolve the platform checkpoint interactively before retrying."
    ),
    m.ErrorCode.RATE_LIMITED: "Wait before retrying the safe read operation.",
    m.ErrorCode.SELECTOR_DRIFT: "Update parser selectors to match the upstream page structure.",
    m.ErrorCode.NETWORK_TIMEOUT: "Retry the safe read or reconcile pending external submissions.",
    m.ErrorCode.JOB_BUSY: "Wait for the active executor or inspect the job with metamuse_job_get.",
    m.ErrorCode.IDEMPOTENCY_CONFLICT: (
        "Use a new request_key for a new mutation or pass the same payload."
    ),
    m.ErrorCode.EXTERNAL_STATE_UNKNOWN: "Reconcile the external state before attempting to resume.",
    m.ErrorCode.STORAGE: "Inspect workspace storage permissions, database integrity, and stderr.",
    m.ErrorCode.LIMIT_REACHED: "Reduce the requested scope or input byte size within limits.",
    m.ErrorCode.DATA_STALE: (
        "Read the latest revision and update expected_revision or prepare a new packet."
    ),
    m.ErrorCode.RESOURCE_UNAVAILABLE: (
        "Verify that the requested local object or subtitle resource exists."
    ),
}


def _load_schema(filename: str) -> dict[str, Any]:
    schema = json.loads((SCHEMA_DIR / filename).read_text(encoding="utf-8"))
    if schema.get("type") != "object":
        raise m.CoreError(m.ErrorCode.STORAGE, f"Schema {filename} must be object-rooted")
    return schema


def list_tools() -> list[types.Tool]:
    """Load the fifteen P0 tool definitions from the checked-in generated contracts."""
    envelope_schema = _load_schema("envelope.json")
    contract_by_name: dict[str, dict[str, Any]] = {}
    if CONTRACT_PATH.is_file():
        raw_contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        contract_by_name = {entry["name"]: entry for entry in raw_contract.get("tools", [])}

    tools: list[types.Tool] = []
    for name, description, spec_annotations in TOOL_SPECS:
        input_schema = _load_schema(f"{name}.input.json")
        output_schema = envelope_schema
        tool_description = description
        tool_annotations = types.ToolAnnotations(**spec_annotations)
        if name in contract_by_name:
            entry = contract_by_name[name]
            input_schema = entry["inputSchema"]
            output_schema = entry["outputSchema"]
            if output_schema.get("type") != "object":
                raise m.CoreError(
                    m.ErrorCode.STORAGE, f"Tool {name} outputSchema must be an object"
                )
            tool_description = entry.get("description", description)
            tool_annotations = types.ToolAnnotations.model_validate(entry["annotations"])
        tools.append(
            types.Tool(
                name=name,
                description=tool_description,
                inputSchema=input_schema,
                outputSchema=output_schema,
                annotations=tool_annotations,
            )
        )
    return tools


def _ensure_error_next_action(envelope: m.Envelope) -> m.Envelope:
    if (
        envelope.status == "error"
        and envelope.error is not None
        and envelope.error.next_action is None
    ):
        detail = envelope.error.model_copy(
            update={"next_action": DEFAULT_NEXT_ACTIONS[envelope.error.code]}
        )
        return envelope.model_copy(update={"error": detail})
    return envelope


def _envelope_to_result(ctx: AppContext, envelope: m.Envelope) -> types.CallToolResult:
    envelope = _ensure_error_next_action(envelope)
    for warning in envelope.warnings:
        ctx.logger.warning("%s", warning)
    text = m.canonical_json(envelope).decode("utf-8")
    structured = json.loads(text)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=structured,
        is_error=envelope.status == "error",
    )


def call_tool(
    ctx: AppContext, name: str, arguments: Mapping[str, Any] | None = None
) -> types.CallToolResult:
    """Validate input through the shared Pydantic model and call the shared app function."""
    with redirect_stdout(sys.stderr):
        if name not in m.TOOL_REQUEST_MODELS or name not in app.TOOL_FUNCTIONS:
            envelope = m.failure(
                m.CoreError(
                    m.ErrorCode.INPUT_INVALID,
                    "Unknown MCP tool",
                    next_action="Call tools/list to inspect the fifteen supported tools.",
                )
            )
            return _envelope_to_result(ctx, envelope)

        request_model = m.TOOL_REQUEST_MODELS[name]
        fn = app.TOOL_FUNCTIONS[name]
        raw_args: Any = {} if arguments is None else dict(arguments)
        try:
            validated = request_model.model_validate(raw_args, context={"limits": ctx.limits})
        except ValidationError as exc:
            envelope = m.failure(m.validation_error(exc))
        except (ValueError, TypeError):
            envelope = m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid request"))
        else:
            try:
                envelope = fn(ctx, validated)
            except m.CoreError as exc:
                envelope = m.failure(exc)
            except ValidationError as exc:
                envelope = m.failure(m.validation_error(exc))
            except (ValueError, TypeError):
                envelope = m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid request"))
            except OSError:
                ctx.logger.exception("Workspace I/O error in %s", name)
                envelope = m.failure(m.CoreError(m.ErrorCode.STORAGE, "Workspace I/O failed"))
            except Exception:
                ctx.logger.exception("Unhandled exception in %s", name)
                envelope = m.failure(m.CoreError(m.ErrorCode.STORAGE, "Tool execution failed"))

        return _envelope_to_result(ctx, envelope)


def _configure_stderr_diagnostics(ctx: AppContext) -> None:
    """Route all Python loggers and warnings to stderr so stdout carries only protocol frames."""
    root_logger = logging.getLogger()
    loggers = [
        root_logger,
        ctx.logger,
        *[
            candidate
            for candidate in logging.Logger.manager.loggerDict.values()
            if isinstance(candidate, logging.Logger)
        ],
    ]
    for lgr in loggers:
        for handler in lgr.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setStream(sys.stderr)
    if not root_logger.handlers and not ctx.logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        ctx.logger.addHandler(handler)

    def _showwarning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        sys.stderr.write(warnings.formatwarning(message, category, filename, lineno, line))
        sys.stderr.flush()

    warnings.showwarning = _showwarning


def create_server(ctx: AppContext) -> Server[Any]:
    """Construct the low-level MCP server bound to the shared workspace AppContext."""
    ctx.non_interactive = True
    app.register_builtin_operations()
    tools = list_tools()
    schemas = {tool.name: tool.input_schema for tool in tools}

    async def handle_list_tools(
        _req_ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list_tools())

    async def handle_call_tool(
        _req_ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        # Execute synchronous app logic on a worker thread with abandon_on_cancel=True:
        # 1) The event loop remains responsive to notifications/cancelled.
        # 2) Cancelling or abandoning a tools/call never cancels an accepted job in SQLite.
        return await anyio.to_thread.run_sync(
            lambda: call_tool(ctx, params.name, params.arguments),
            abandon_on_cancel=True,
        )

    server: Server[Any] = Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        get_tool_input_schema=schemas.get,
        on_list_tools=handle_list_tools,
        on_call_tool=handle_call_tool,
    )
    if not ctx.policy.app.telemetry_enabled:
        server.middleware.clear()
    return server


async def _serve_stdio(
    ctx: AppContext,
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> None:
    server = create_server(ctx)
    init_options = server.create_initialization_options()
    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        _configure_stderr_diagnostics(ctx)
        with redirect_stdout(sys.stderr):
            await server.run(read_stream, write_stream, init_options)


def run(
    ctx: AppContext,
    *,
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
) -> None:
    """Own protocol stdout; register builtins before tools or an attached worker start."""
    stdin_buf = getattr(sys.stdin, "buffer", None)
    stdout_buf = getattr(sys.stdout, "buffer", None)
    if stdin is None and (
        stdin_buf is None
        or stdin_buf is sys.stdin
        or stdout_buf is None
        or stdout_buf is sys.stdout
    ):
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
    ctx.non_interactive = True
    app.register_builtin_operations()
    _configure_stderr_diagnostics(ctx)
    anyio.run(_serve_stdio, ctx, stdin, stdout)
