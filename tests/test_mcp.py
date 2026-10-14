"""Offline integration tests for the MCP stdio server over shared application functions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from mcp import types
from mcp.client.session import LATEST_HANDSHAKE_VERSION, ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from metamuse import app, mcp
from metamuse import models as m
from metamuse.schemas import schema_for

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_TOOLS = json.loads((ROOT / "docs/design/contracts/mcp-tools.json").read_text())["tools"]
ENVELOPE_SCHEMA = json.loads((ROOT / "docs/design/contracts/envelope.schema.json").read_text())
ENVELOPE_VALIDATOR = Draft202012Validator(ENVELOPE_SCHEMA)


def _metamuse_command(
    workspace: Path, *, extra_env: dict[str, str] | None = None
) -> StdioServerParameters:
    script = Path(sys.executable).with_name("metamuse")
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.environ.get("PYTHONPATH", str(ROOT / "src")),
    }
    if extra_env:
        env.update(extra_env)
    if script.is_file():
        return StdioServerParameters(
            command=str(script),
            args=["--workspace", str(workspace), "mcp"],
            env=env,
        )
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "metamuse.cli", "--workspace", str(workspace), "mcp"],
        env=env,
    )


def _assert_envelope_result(
    result: types.CallToolResult, *, expected_error: bool
) -> dict[str, Any]:
    assert result.is_error is expected_error
    assert result.structured_content is not None
    assert isinstance(result.structured_content, dict)
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    parsed_text = json.loads(block.text)
    assert parsed_text == result.structured_content
    assert m.canonical_json(result.structured_content).decode("utf-8") == block.text
    ENVELOPE_VALIDATOR.validate(result.structured_content)
    m.Envelope.model_validate(result.structured_content)
    return result.structured_content


def test_mcp_stdio_initialize_and_tools_list(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    errlog_path = tmp_path / "stderr.log"

    async def scenario() -> None:
        params = _metamuse_command(workspace)
        with errlog_path.open("w+", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    init_result = await session.initialize()
                    assert init_result.protocol_version == LATEST_HANDSHAKE_VERSION == "2025-11-25"
                    assert init_result.server_info.name == "metamuse"
                    assert init_result.server_info.version == "0.1.0.dev0"

                    tools_result = await session.list_tools()
                    tools = tools_result.tools
                    assert len(tools) == 15

                    expected_names = [entry["name"] for entry in CONTRACT_TOOLS]
                    actual_names = [tool.name for tool in tools]
                    assert actual_names == expected_names == list(m.TOOL_REQUEST_MODELS)

                    forbidden = {
                        "auth",
                        "permit",
                        "publish",
                        "raw",
                        "metamuse_auth_import",
                        "metamuse_permit_create",
                    }
                    assert forbidden.isdisjoint(actual_names)

                    contract_by_name = {entry["name"]: entry for entry in CONTRACT_TOOLS}
                    read_only_tools = set()
                    open_world_tools = set()
                    idempotent_tools = set()

                    for tool in tools:
                        contract_entry = contract_by_name[tool.name]
                        expected_input = schema_for(m.TOOL_REQUEST_MODELS[tool.name])
                        checked_in_input = json.loads(
                            (
                                ROOT / "src/metamuse/data/schemas" / f"{tool.name}.input.json"
                            ).read_text()
                        )
                        assert tool.input_schema == contract_entry["inputSchema"]
                        assert tool.input_schema == checked_in_input
                        assert tool.input_schema == expected_input

                        assert tool.output_schema is not None
                        assert tool.output_schema.get("type") == "object"
                        assert tool.output_schema == contract_entry["outputSchema"]
                        assert tool.output_schema == ENVELOPE_SCHEMA

                        assert tool.annotations is not None
                        dumped_annotations = tool.annotations.model_dump(
                            by_alias=True, exclude_none=True
                        )
                        assert dumped_annotations == contract_entry["annotations"]
                        assert tool.annotations.destructive_hint is False
                        if tool.annotations.read_only_hint:
                            read_only_tools.add(tool.name)
                        if tool.annotations.open_world_hint:
                            open_world_tools.add(tool.name)
                        if tool.annotations.idempotent_hint:
                            idempotent_tools.add(tool.name)

                    expected_read_only = {
                        "metamuse_inspect",
                        "metamuse_job_get",
                        "metamuse_job_list",
                        "metamuse_search",
                        "metamuse_draft_preview",
                    }
                    assert read_only_tools == expected_read_only
                    assert idempotent_tools == expected_read_only
                    assert open_world_tools == {
                        "metamuse_collect",
                        "metamuse_muse_open",
                        "metamuse_muse_send",
                        "metamuse_muse_read",
                    }
                    for non_read_only in (
                        "metamuse_muse_open",
                        "metamuse_muse_send",
                        "metamuse_muse_read",
                        "metamuse_collect",
                        "metamuse_project_create",
                        "metamuse_job_control",
                        "metamuse_packet_prepare",
                        "metamuse_muse_close_exchange",
                        "metamuse_draft_save",
                        "metamuse_export",
                    ):
                        assert non_read_only not in read_only_tools

    anyio.run(scenario)


def test_mcp_stdio_calls_state_handles_and_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    errlog_path = tmp_path / "stderr.log"

    async def scenario() -> None:
        params = _metamuse_command(workspace)
        with errlog_path.open("w+", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    await session.list_tools()

                    # 1. Local read (inspect) returns matching structuredContent + text block.
                    inspect_res = await session.call_tool("metamuse_inspect", {})
                    inspect_env = _assert_envelope_result(inspect_res, expected_error=False)
                    assert inspect_env["status"] == "ok"
                    assert inspect_env["error"] is None
                    assert inspect_env["data"]["network_mode"] == "offline"
                    assert inspect_env["data"]["worker_available"] is True
                    assert inspect_env["data"]["attached_worker"] == "running"
                    assert len(inspect_env["data"]["capabilities"]) == 15

                    # 2. project_create followed by calls omitting project_id must NOT reuse it.
                    proj_res = await session.call_tool(
                        "metamuse_project_create",
                        {"name": "Synthetic Project", "request_key": "proj-key-1"},
                    )
                    proj_env = _assert_envelope_result(proj_res, expected_error=False)
                    assert proj_env["status"] == "ok"
                    project_id = proj_env["data"]["id"]
                    assert project_id.startswith("prj_")

                    # Omitting project_id on metamuse_job_list must fail validation.
                    omitted_jobs_res = await session.call_tool("metamuse_job_list", {})
                    omitted_jobs_env = _assert_envelope_result(
                        omitted_jobs_res, expected_error=True
                    )
                    assert omitted_jobs_env["status"] == "error"
                    assert omitted_jobs_env["data"] is None
                    assert omitted_jobs_env["error"]["code"] == "E_INPUT_INVALID"
                    assert omitted_jobs_env["error"]["retry_class"] == "never"
                    assert isinstance(omitted_jobs_env["error"]["next_action"], str)
                    assert omitted_jobs_env["error"]["next_action"]
                    assert "project_id" in omitted_jobs_env["error"]["message"]

                    # Omitting project_id on metamuse_search also fails validation.
                    omitted_search_res = await session.call_tool(
                        "metamuse_search", {"query": "繁中"}
                    )
                    omitted_search_env = _assert_envelope_result(
                        omitted_search_res, expected_error=True
                    )
                    assert omitted_search_env["error"]["code"] == "E_INPUT_INVALID"
                    assert omitted_search_env["error"]["retry_class"] == "never"
                    assert omitted_search_env["error"]["next_action"]
                    assert "project_id" in omitted_search_env["error"]["message"]

                    # Passing project_id explicitly to metamuse_job_list succeeds.
                    explicit_jobs_res = await session.call_tool(
                        "metamuse_job_list", {"project_id": project_id}
                    )
                    explicit_jobs_env = _assert_envelope_result(
                        explicit_jobs_res, expected_error=False
                    )
                    assert explicit_jobs_env["status"] == "ok"
                    assert explicit_jobs_env["data"] == {"jobs": []}

                    # 3. Input-validation error (extra field, without echoing secret values).
                    secret = "SyntheticSecretMustNotEcho"
                    invalid_res = await session.call_tool(
                        "metamuse_project_create",
                        {"name": "Valid", "request_key": "k2", "approved": True, secret: secret},
                    )
                    invalid_env = _assert_envelope_result(invalid_res, expected_error=True)
                    assert invalid_env["error"]["code"] == "E_INPUT_INVALID"
                    assert invalid_env["error"]["retry_class"] == "never"
                    assert isinstance(invalid_env["error"]["next_action"], str)
                    assert invalid_env["error"]["next_action"]
                    assert secret not in invalid_res.content[0].text

                    # 4. Domain errors reach client with code, retry_class, next_action, isError.
                    # Collect is implemented; a local file with no import root is a scope error.
                    unsupported_res = await session.call_tool(
                        "metamuse_collect",
                        {
                            "project_id": project_id,
                            "inputs": [
                                {
                                    "kind": "local_file",
                                    "path": "synthetic.jsonl",
                                    "format": "jsonl",
                                }
                            ],
                            "resources": ["post"],
                            "limits": {
                                "max_urls": 10,
                                "max_items": 500,
                                "max_bytes": 1024,
                                "wall_clock_seconds": 60,
                            },
                            "access_grant_id": None,
                            "request_key": "collect-1",
                        },
                    )
                    unsupported_env = _assert_envelope_result(unsupported_res, expected_error=True)
                    assert unsupported_env["error"]["code"] == "E_SCOPE_REQUIRED"
                    assert unsupported_env["error"]["retry_class"] == "user_action"
                    assert isinstance(unsupported_env["error"]["next_action"], str)
                    assert "import_roots" in unsupported_env["error"]["next_action"]

                    ref_err_res = await session.call_tool(
                        "metamuse_job_get", {"job_id": "job_missing_fixture"}
                    )
                    ref_err_env = _assert_envelope_result(ref_err_res, expected_error=True)
                    assert ref_err_env["error"]["code"] == "E_REFERENCE_INVALID"
                    assert ref_err_env["error"]["retry_class"] == "never"
                    assert isinstance(ref_err_env["error"]["next_action"], str)
                    assert ref_err_env["error"]["next_action"]

    anyio.run(scenario)


def test_mcp_domain_error_scope_required_noninteractive(
    ctx: app.AppContext, monkeypatch: Any
) -> None:
    from metamuse import muse

    def interactive_open(context: app.AppContext, request: m.MuseOpenRequest) -> m.Envelope:
        context.require_interactive()
        return m.Envelope(status="ok", data={})

    monkeypatch.setattr(muse, "open_muse_discussion", interactive_open)
    mcp.create_server(ctx)
    result = mcp.call_tool(
        ctx,
        "metamuse_muse_open",
        {
            "project_id": "prj_fixture",
            "topic": "Synthetic topic",
            "access_grant_id": "pmt_fixture",
            "request_key": "open-1",
        },
    )
    env = _assert_envelope_result(result, expected_error=True)
    assert env["error"]["code"] == "E_SCOPE_REQUIRED"
    assert env["error"]["retry_class"] == "user_action"
    assert isinstance(env["error"]["next_action"], str)
    # The registered preview checks actual policy. Its specific refusal now
    # propagates instead of being replaced by generic interactive/permit guidance.
    assert env["error"]["message"] == "Muse is disabled"
    assert env["error"]["next_action"] == (
        "Enable Muse explicitly; configure a binding or select the fixture backend"
    )
    assert env["approval"] is None


def test_mcp_package_data_only_and_annotations_match_contract() -> None:
    mcp_source = Path(mcp.__file__).read_text(encoding="utf-8")
    assert "docs/" not in mcp_source
    assert "docs" not in mcp_source
    assert not hasattr(mcp, "TOOL_SPECS")
    assert not hasattr(mcp, "TOOL_ANNOTATIONS")
    assert not hasattr(mcp, "DEFAULT_NEXT_ACTIONS")
    assert not hasattr(mcp, "CONTRACT_PATH")

    tools = mcp.list_tools()
    assert len(tools) == len(CONTRACT_TOOLS) == 15
    for tool, contract_entry in zip(tools, CONTRACT_TOOLS, strict=True):
        assert tool.name == contract_entry["name"]
        assert tool.description == contract_entry["description"]
        assert tool.input_schema == contract_entry["inputSchema"]
        assert tool.output_schema == contract_entry["outputSchema"]
        assert tool.annotations is not None
        assert (
            tool.annotations.model_dump(by_alias=True, exclude_none=True)
            == contract_entry["annotations"]
        )


def test_cli_json_and_mcp_structured_content_equal_on_errors(tmp_path: Path, capsys: Any) -> None:
    from metamuse import cli

    workspace = tmp_path / "workspace"
    errlog_path = tmp_path / "stderr.log"

    def run_cli_json(command: list[str]) -> tuple[int, dict[str, Any]]:
        code = cli.main(["--workspace", str(workspace), "--json", "--non-interactive", *command])
        captured = capsys.readouterr()
        return code, json.loads(captured.out)

    # 1. Input validation error on CLI --json
    cli_in_code, cli_in_env = run_cli_json(
        ["project", "create", "--name", "Fixture", "--request-key", ""]
    )
    assert cli_in_code == 2
    assert cli_in_env["status"] == "error"
    assert cli_in_env["error"]["code"] == "E_INPUT_INVALID"

    # 2. Domain error (E_REFERENCE_INVALID) on CLI --json
    cli_dom_code, cli_dom_env = run_cli_json(["jobs", "get", "job_missing_fixture"])
    assert cli_dom_code == 2
    assert cli_dom_env["status"] == "error"
    assert cli_dom_env["error"]["code"] == "E_REFERENCE_INVALID"

    # Compare against both direct mcp.call_tool and stdio MCP session.call_tool
    ctx = app.AppContext.open(workspace)
    direct_in_env = _assert_envelope_result(
        mcp.call_tool(ctx, "metamuse_project_create", {"name": "Fixture", "request_key": ""}),
        expected_error=True,
    )
    assert {**cli_in_env, "request_id": direct_in_env["request_id"]} == direct_in_env

    direct_dom_env = _assert_envelope_result(
        mcp.call_tool(ctx, "metamuse_job_get", {"job_id": "job_missing_fixture"}),
        expected_error=True,
    )
    assert {**cli_dom_env, "request_id": direct_dom_env["request_id"]} == direct_dom_env

    async def scenario() -> None:
        params = _metamuse_command(workspace)
        with errlog_path.open("w+", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    mcp_in_res = await session.call_tool(
                        "metamuse_project_create", {"name": "Fixture", "request_key": ""}
                    )
                    mcp_in_env = _assert_envelope_result(mcp_in_res, expected_error=True)
                    assert {**cli_in_env, "request_id": mcp_in_env["request_id"]} == mcp_in_env

                    mcp_dom_res = await session.call_tool(
                        "metamuse_job_get", {"job_id": "job_missing_fixture"}
                    )
                    mcp_dom_env = _assert_envelope_result(mcp_dom_res, expected_error=True)
                    assert {**cli_dom_env, "request_id": mcp_dom_env["request_id"]} == mcp_dom_env

    anyio.run(scenario)


def test_mcp_otel_middleware_removed_by_type_only(ctx: app.AppContext, monkeypatch: Any) -> None:
    from mcp.server.lowlevel.server import OpenTelemetryMiddleware, Server

    sentinel_middleware = lambda req_ctx, call_next: call_next(req_ctx)  # noqa: E731
    orig_init = Server.__init__

    def patched_init(self: Server[Any], *args: Any, **kwargs: Any) -> None:
        orig_init(self, *args, **kwargs)
        self.middleware.append(sentinel_middleware)

    monkeypatch.setattr(Server, "__init__", patched_init)
    ctx.policy.app.telemetry_enabled = False
    server = mcp.create_server(ctx)
    assert not any(isinstance(mw, OpenTelemetryMiddleware) for mw in server.middleware)
    assert sentinel_middleware in server.middleware


def test_mcp_raw_pipe_stdout_cleanliness_with_logs_and_warnings(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    (hook_dir / "sitecustomize.py").write_text(
        "import logging, os, sys, traceback, warnings\n"
        "from metamuse import app, content\n"
        "_orig_inspect = app.inspect_capabilities\n"
        "def _noisy_inspect(ctx, request=None):\n"
        "    ctx.logger.warning('synthetic-ctx-logger-warning')\n"
        "    logging.getLogger('metamuse.thirdparty').error('synthetic-library-logger-error')\n"
        "    warnings.warn('synthetic-runtime-warning', RuntimeWarning)\n"
        "    print('synthetic-stray-stdout-print')\n"
        "    sys.stdout.write('synthetic-sys-stdout-write\\n')\n"
        "    os.write(1, b'synthetic-fd1-write\\n')\n"
        "    res = _orig_inspect(ctx, request)\n"
        "    w = [*res.warnings, 'synthetic-envelope-warning']\n"
        "    return res.model_copy(update={'warnings': w})\n"
        "app.inspect_capabilities = _noisy_inspect\n"
        "app.TOOL_FUNCTIONS['metamuse_inspect'] = _noisy_inspect\n"
        "def _crashing_search(ctx, request):\n"
        "    traceback.print_stack(file=sys.stdout)\n"
        "    raise RuntimeError('synthetic-unhandled-exception')\n"
        "content.search_evidence = _crashing_search\n",
        encoding="utf-8",
    )

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = (
        f"{hook_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(hook_dir)
    )
    env = {**os.environ, "PYTHONPATH": pythonpath, "PYTHONUNBUFFERED": "1"}
    script = Path(sys.executable).with_name("metamuse")
    cmd = (
        [str(script), "--workspace", str(workspace), "mcp"]
        if script.is_file()
        else [sys.executable, "-m", "metamuse.cli", "--workspace", str(workspace), "mcp"]
    )

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    stdout_lines: list[str] = []

    def send_and_recv(message: dict[str, Any], *, expect_reply: bool = True) -> None:
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        if expect_reply:
            line = proc.stdout.readline()
            assert line, "Expected JSON-RPC response line on stdout"
            stdout_lines.append(line.rstrip("\r\n"))

    send_and_recv(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "raw-pipe-client", "version": "0.1.0"},
            },
        }
    )
    send_and_recv(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        expect_reply=False,
    )
    send_and_recv({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    send_and_recv(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "metamuse_inspect", "arguments": {}},
        }
    )
    send_and_recv(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "metamuse_search",
                "arguments": {"project_id": "prj_fixture", "query": "test"},
            },
        }
    )

    proc.stdin.close()
    remaining_stdout = proc.stdout.read()
    stderr_text = proc.stderr.read()
    proc.wait(timeout=15)
    assert proc.returncode == 0
    if remaining_stdout:
        stdout_lines.extend(remaining_stdout.splitlines())

    assert len(stdout_lines) == 4
    parsed_messages = []
    for line in stdout_lines:
        parsed = json.loads(line)
        types.jsonrpc_message_adapter.validate_json(line, by_name=False)
        parsed_messages.append(parsed)

    assert [msg["id"] for msg in parsed_messages] == [1, 2, 3, 4]
    assert parsed_messages[0]["result"]["protocolVersion"] == "2025-11-25"
    assert len(parsed_messages[1]["result"]["tools"]) == 15
    assert parsed_messages[2]["result"]["isError"] is False
    assert parsed_messages[3]["result"]["isError"] is True
    assert parsed_messages[3]["result"]["structuredContent"]["error"]["code"] == "E_STORAGE"

    stdout_text = "\n".join(stdout_lines)
    noise_markers = (
        "synthetic-ctx-logger-warning",
        "synthetic-library-logger-error",
        "synthetic-runtime-warning",
        "synthetic-stray-stdout-print",
        "synthetic-sys-stdout-write",
        "synthetic-fd1-write",
    )
    for marker in noise_markers:
        assert marker not in stdout_text
        assert marker in stderr_text
    # The exception text stays off both streams. The safe log still names the tool.
    assert "synthetic-unhandled-exception" not in stdout_text
    assert "synthetic-unhandled-exception" not in stderr_text
    assert "operation=metamuse_search" in stderr_text
    assert "code=E_STORAGE" in stderr_text
    assert "RuntimeError" in stderr_text
    assert (
        "synthetic-envelope-warning"
        in parsed_messages[2]["result"]["structuredContent"]["warnings"]
    )
    assert "synthetic-envelope-warning" in stderr_text


def test_mcp_cancelled_call_leaves_accepted_job_queued(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    hook_dir = tmp_path / "cancel_hook"
    hook_dir.mkdir()
    accepted_file = tmp_path / "accepted_job.txt"
    release_file = tmp_path / "release_job.txt"
    errlog_path = tmp_path / "stderr.log"

    (hook_dir / "sitecustomize.py").write_text(
        "import time\n"
        "from pathlib import Path\n"
        "from metamuse import collect, jobs, models as m\n"
        f"ACCEPTED_FILE = Path({str(accepted_file)!r})\n"
        f"RELEASE_FILE = Path({str(release_file)!r})\n"
        "def _register(registry=None):\n"
        "    target = jobs.OPERATIONS if registry is None else registry\n"
        "    if 'collect.local' not in target:\n"
        "        jobs.register_operation(\n"
        "            'collect.local',\n"
        "            lambda step: m.JobResult(coverage_status='bounded'),\n"
        "            'none',\n"
        "            request_model=m.CollectRequest,\n"
        "            registry=target,\n"
        "        )\n"
        "def _submit_collect(ctx, request):\n"
        "    _register()\n"
        "    env = jobs.submit_job(\n"
        "        ctx.store,\n"
        "        'collect.local',\n"
        "        request.request_key,\n"
        "        request.model_dump(mode='json'),\n"
        "        project_id=request.project_id,\n"
        "        steps=[jobs.StepSpec(target={'index': 0})],\n"
        "    )\n"
        "    ACCEPTED_FILE.write_text(env.data['job_id'], encoding='utf-8')\n"
        "    deadline = time.monotonic() + 2.0\n"
        "    while not RELEASE_FILE.exists() and time.monotonic() < deadline:\n"
        "        time.sleep(0.02)\n"
        "    return env\n"
        "collect.register_operations = _register\n"
        "collect.submit_collect = _submit_collect\n",
        encoding="utf-8",
    )

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = (
        f"{hook_dir}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else str(hook_dir)
    )

    async def scenario() -> None:
        params = _metamuse_command(workspace, extra_env={"PYTHONPATH": pythonpath})
        params.args.append("--no-worker")
        with errlog_path.open("w+", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()

                    proj_res = await session.call_tool(
                        "metamuse_project_create",
                        {"name": "Cancel Project", "request_key": "cancel-proj-1"},
                    )
                    project_id = _assert_envelope_result(proj_res, expected_error=False)["data"][
                        "id"
                    ]

                    async def trigger_cancel(scope: anyio.CancelScope) -> None:
                        while not accepted_file.exists():
                            await anyio.sleep(0.01)
                        scope.cancel()
                        release_file.write_text("released", encoding="utf-8")

                    with anyio.CancelScope() as cancel_scope:
                        async with anyio.create_task_group() as tg:
                            tg.start_soon(trigger_cancel, cancel_scope)
                            await session.call_tool(
                                "metamuse_collect",
                                {
                                    "project_id": project_id,
                                    "inputs": [
                                        {
                                            "kind": "local_file",
                                            "path": "synthetic.jsonl",
                                            "format": "jsonl",
                                        }
                                    ],
                                    "resources": ["post"],
                                    "limits": {
                                        "max_urls": 10,
                                        "max_items": 500,
                                        "max_bytes": 1024,
                                        "wall_clock_seconds": 60,
                                    },
                                    "access_grant_id": None,
                                    "request_key": "collect-cancel-test",
                                },
                            )
                    assert cancel_scope.cancelled_caught
                    job_id = accepted_file.read_text(encoding="utf-8").strip()
                    assert job_id.startswith("job_")

                    # Inspect the accepted job: cancelling tools/call must NOT cancel it.
                    job_res = await session.call_tool("metamuse_job_get", {"job_id": job_id})
                    job_env = _assert_envelope_result(job_res, expected_error=False)
                    assert job_env["data"]["job"]["id"] == job_id
                    assert job_env["data"]["job"]["state"] == "queued"
                    assert job_env["data"]["job"]["cancel_requested"] is False
                    assert job_env["data"]["steps"][0]["state"] == "queued"

                    # Explicitly cancelling through metamuse_job_control cancels the queued job.
                    ctrl_res = await session.call_tool(
                        "metamuse_job_control",
                        {
                            "job_id": job_id,
                            "operation": "cancel",
                            "request_key": "explicit-cancel-1",
                        },
                    )
                    ctrl_env = _assert_envelope_result(ctrl_res, expected_error=False)
                    assert ctrl_env["data"]["job_id"] == job_id
                    assert ctrl_env["data"]["state"] == "cancelled"
                    assert ctrl_env["data"]["cancel_requested"] is True

    anyio.run(scenario)
