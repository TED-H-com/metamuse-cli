"""Unexpected failures log class and location only (FR-020, AT-028)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "metamuse"
_LEAK = re.compile(
    r"exc_info\s*=\s*True|logger\.exception\(|logging\.exception\(|"
    r"traceback\.(print_exc|format_exc|print_exception|format_exception)\("
)

_INJECT = """
def fail(ctx, request=None):
    prefix = "SYNTH"
    cookie = f"{prefix}_MARKERCOOKIE"
    authorization = f"{prefix}_MARKERAUTHORIZATION"
    signed = f"{prefix}_MARKERSIGNEDURL"
    try:
        raise KeyError(signed)
    except KeyError as context:
        caused = ValueError(authorization)
        error = RuntimeError(cookie)
        error.__context__ = context
        error.__suppress_context__ = False
        raise error from caused
"""


def _markers() -> tuple[str, str, str]:
    prefix = "SYNTH"
    return (
        f"{prefix}_MARKERCOOKIE",
        f"{prefix}_MARKERAUTHORIZATION",
        f"{prefix}_MARKERSIGNEDURL",
    )


def _assert_absent(blobs: list[bytes | str]) -> None:
    markers = [item.encode() for item in _markers()]
    for blob in blobs:
        raw = blob if isinstance(blob, bytes) else blob.encode()
        for marker in markers:
            assert marker not in raw


def _workspace_bytes(workspace: Path) -> bytes:
    chunks: list[bytes] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        chunks.append(path.read_bytes())
    return b"\n".join(chunks)


def _write_wrapper(path: Path, patch: str) -> None:
    path.write_text(
        _INJECT
        + "\ndef main() -> None:\n"
        + patch
        + "\n    from metamuse.cli import main as cli_main\n"
        + "    raise SystemExit(cli_main())\n\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n",
        encoding="utf-8",
    )


def test_source_has_no_traceback_logging() -> None:
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if _LEAK.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_helper_records_classes_without_exception_text() -> None:
    prefix = "SYNTH"
    cookie = f"{prefix}_MARKERCOOKIE"
    authorization = f"{prefix}_MARKERAUTHORIZATION"
    signed = f"{prefix}_MARKERSIGNEDURL"
    try:
        try:
            raise KeyError(signed)
        except KeyError as context:
            caused = ValueError(authorization)
            error = RuntimeError(cookie)
            error.__context__ = context
            error.__suppress_context__ = False
            raise error from caused
    except RuntimeError as exc:
        caught = exc

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("metamuse.safe-log-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.ERROR)
    logger.addHandler(_Capture())
    from metamuse.safe_log import log_safe_exception

    log_safe_exception(
        logger,
        caught,
        operation="metamuse_inspect",
        code="E_STORAGE",
        request_id="req_" + "ab" * 16,
        job_id="job_" + "cd" * 16,
    )
    assert len(records) == 1
    record = records[0]
    assert not record.exc_info
    message = record.getMessage()
    assert "operation=metamuse_inspect" in message
    assert "code=E_STORAGE" in message
    assert "classes=RuntimeError,ValueError,KeyError" in message
    assert "frames=" in message
    assert re.search(r"frames=\S+:\d+:\w+", message)
    _assert_absent([message])
    unsafe = "not a job id"
    from metamuse.safe_log import known_job_id

    assert known_job_id({"job_id": unsafe, "question": cookie}) is None
    _assert_absent([message])


def test_mcp_stdio_unexpected_exception_does_not_log_markers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    wrapper = tmp_path / "inject_mcp.py"
    errlog_path = tmp_path / "stderr.log"
    _write_wrapper(
        wrapper,
        '    from metamuse import app\n    app.TOOL_FUNCTIONS["metamuse_inspect"] = fail',
    )

    async def scenario() -> dict[str, Any]:
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                str(wrapper),
                "--workspace",
                str(workspace),
                "--non-interactive",
                "mcp",
            ],
            env={
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.environ.get(
                    "PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src")
                ),
            },
            cwd=tmp_path,
        )
        with errlog_path.open("w+", encoding="utf-8") as errlog:
            async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert len(listed.tools) == 15
                    result = await session.call_tool("metamuse_inspect", {})
        assert result.is_error is True
        assert result.structured_content is not None
        block = result.content[0]
        text = block.text
        assert json.loads(text) == result.structured_content
        return {"structured": result.structured_content, "text": text}

    captured = anyio.run(scenario)
    envelope = captured["structured"]
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "E_STORAGE"
    assert envelope["error"]["message"] == "Tool execution failed"
    stderr = errlog_path.read_text(encoding="utf-8")
    assert "operation=metamuse_inspect" in stderr
    assert "code=E_STORAGE" in stderr
    assert "RuntimeError" in stderr
    assert "ValueError" in stderr
    assert "KeyError" in stderr
    _assert_absent(
        [
            json.dumps(envelope),
            captured["text"],
            stderr,
            _workspace_bytes(workspace),
            wrapper.read_text(encoding="utf-8"),
        ]
    )


def test_cli_unexpected_exception_does_not_log_markers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    wrapper = tmp_path / "inject_cli.py"
    _write_wrapper(wrapper, "    from metamuse import app\n    app.inspect_capabilities = fail")
    completed = subprocess.run(
        [
            sys.executable,
            str(wrapper),
            "--workspace",
            str(workspace),
            "--json",
            "doctor",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert completed.returncode == 7
    envelope = json.loads(completed.stdout)
    assert envelope["status"] == "error"
    assert envelope["error"]["code"] == "E_STORAGE"
    assert envelope["error"]["message"] == "Command execution failed"
    assert "operation=doctor" in completed.stderr
    assert "code=E_STORAGE" in completed.stderr
    assert "RuntimeError" in completed.stderr
    assert "ValueError" in completed.stderr
    assert "KeyError" in completed.stderr
    _assert_absent(
        [
            completed.stdout,
            completed.stderr,
            _workspace_bytes(workspace),
            wrapper.read_text(encoding="utf-8"),
        ]
    )
