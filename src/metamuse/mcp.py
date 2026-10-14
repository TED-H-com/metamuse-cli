"""MCP stdio server over the shared application functions."""

from __future__ import annotations

import json
import logging
import sys
import warnings
from collections.abc import Mapping
from contextlib import redirect_stdout
from importlib.resources import files
from typing import TYPE_CHECKING, Any

import anyio
import anyio.to_thread
from mcp import types
from mcp.server import Server
from mcp.server.lowlevel.server import OpenTelemetryMiddleware
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from metamuse import app
from metamuse import models as m
from metamuse.safe_log import known_job_id, log_safe_exception

if TYPE_CHECKING:
    from metamuse.app import AppContext

SERVER_NAME = "metamuse"
SERVER_VERSION = "0.1.0.dev0"


def _load_schema_resource(filename: str) -> dict[str, Any]:
    resource = files("metamuse.data").joinpath("schemas", filename)
    data = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise m.CoreError(m.ErrorCode.STORAGE, f"Schema resource {filename} must be an object")
    return data


def list_tools() -> list[types.Tool]:
    """Load the fifteen P0 tool definitions from packaged contract data."""
    envelope_schema = _load_schema_resource("envelope.json")
    if envelope_schema.get("type") != "object":
        raise m.CoreError(m.ErrorCode.STORAGE, "Schema envelope.json must be object-rooted")
    contract = _load_schema_resource("mcp-tools.json")
    entries = contract.get("tools")
    if not isinstance(entries, list):
        raise m.CoreError(m.ErrorCode.STORAGE, "Contract mcp-tools.json must define tools")

    tools: list[types.Tool] = []
    for entry in entries:
        name = entry["name"]
        input_schema = entry["inputSchema"]
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise m.CoreError(m.ErrorCode.STORAGE, f"Tool {name} inputSchema must be an object")
        output_schema = entry["outputSchema"]
        if not isinstance(output_schema, dict) or output_schema.get("type") != "object":
            raise m.CoreError(m.ErrorCode.STORAGE, f"Tool {name} outputSchema must be an object")
        tools.append(
            types.Tool(
                name=name,
                description=entry["description"],
                inputSchema=input_schema,
                outputSchema=output_schema,
                annotations=types.ToolAnnotations.model_validate(entry["annotations"]),
            )
        )
    return tools


def _envelope_to_result(ctx: AppContext, envelope: m.Envelope) -> types.CallToolResult:
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
        job_id = known_job_id(raw_args if isinstance(raw_args, Mapping) else None)
        try:
            envelope = fn(ctx, validated)
        except m.CoreError as exc:
            envelope = m.failure(exc)
        except ValidationError as exc:
            envelope = m.failure(m.validation_error(exc))
        except (ValueError, TypeError):
            envelope = m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid request"))
        except OSError as exc:
            envelope = m.failure(m.CoreError(m.ErrorCode.STORAGE, "Workspace I/O failed"))
            log_safe_exception(
                ctx.logger,
                exc,
                operation=name,
                code=m.ErrorCode.STORAGE.value,
                request_id=envelope.request_id,
                job_id=job_id,
            )
        except Exception as exc:
            envelope = m.failure(m.CoreError(m.ErrorCode.STORAGE, "Tool execution failed"))
            log_safe_exception(
                ctx.logger,
                exc,
                operation=name,
                code=m.ErrorCode.STORAGE.value,
                request_id=envelope.request_id,
                job_id=job_id,
            )

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
        server.middleware = [
            mw for mw in server.middleware if not isinstance(mw, OpenTelemetryMiddleware)
        ]
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
    ctx.non_interactive = True
    app.register_builtin_operations()
    _configure_stderr_diagnostics(ctx)
    anyio.run(_serve_stdio, ctx, stdin, stdout)
