"""Worker failure diagnostics are visible and sanitized without ending MCP."""

import logging
import sqlite3

import pytest

from metamuse import app, cli, jobs, mcp, store
from metamuse import models as m


@pytest.mark.parametrize("method", ["_start", "run"])
@pytest.mark.parametrize("code", [m.ErrorCode.PROTOCOL_MISMATCH, m.ErrorCode.STORAGE, None])
def test_attached_failure_logs_code_and_frames_and_accepts_queued_job(
    ctx, monkeypatch, caplog, method, code
):
    app.register_builtin_operations()
    project_id = app.create_project(ctx, {"name": "fixture", "request_key": "project"}).data["id"]

    def broken(executor):
        if code:
            raise m.CoreError(code, "private-marker /private/path SQL row-content")
        raise RuntimeError("private-marker /private/path SQL row-content")

    monkeypatch.setattr(jobs.Executor, method, broken)
    attached = mcp._AttachedWorker(ctx, True)
    ctx.worker_status = attached.status
    with caplog.at_level(logging.ERROR, logger=ctx.logger.name):
        attached.thread.start()
        try:
            attached.thread.join(5)
            assert not attached.thread.is_alive()
            expected = (code or m.ErrorCode.STORAGE).value
            for data in [
                attached.status(),
                cli._dispatch(ctx, cli.parser().parse_args(["doctor"])).data,
                mcp.call_tool(ctx, "metamuse_inspect", {}).structured_content["data"],
            ]:
                assert data["attached_worker"] == "failed"
                assert data["worker_error_code"] == expected
                assert data["worker_available"] is False
            lines = [
                r.getMessage() for r in caplog.records if "mcp.attached_worker" in r.getMessage()
            ]
            assert len(lines) == 1
            assert f"code={expected}" in lines[0]
            assert "classes=" + ("CoreError" if code else "RuntimeError") in lines[0]
            assert "frames=" in lines[0] and ":broken" in lines[0]
            assert "private-marker" not in caplog.text
            assert "/" not in lines[0] and "row-content" not in lines[0]
            assert not hasattr(attached, "start_error")
            result = mcp.call_tool(
                ctx,
                "metamuse_collect",
                {
                    "project_id": project_id,
                    "inputs": [{"kind": "text", "text": "Synthetic queued text"}],
                    "resources": ["post"],
                    "limits": {
                        "max_urls": 10,
                        "max_items": 500,
                        "max_bytes": 1024,
                        "wall_clock_seconds": 60,
                    },
                    "access_grant_id": None,
                    "request_key": "after-worker-failure",
                },
            ).structured_content
            assert result["status"] == "accepted", result
            assert result["data"]["worker_available"] is False
            assert result["data"]["attached_worker"] == "failed"
            job = app.get_job(ctx, {"job_id": result["data"]["job_id"]}).data
            assert job["job"]["state"] == "queued" and job["steps"][0]["attempts"] == 0
        finally:
            attached.stop()
            attached.thread.join(10)
    assert not attached.thread.is_alive()


def test_stop_during_startup_never_reports_running(ctx, monkeypatch):
    states = []

    class Observed(mcp._AttachedWorker):
        def __setattr__(self, key, value):
            if key == "state":
                states.append(value)
            super().__setattr__(key, value)

    def start(executor):
        executor.request_stop()
        raise store._StorageBusy(sqlite3.SQLITE_BUSY)

    monkeypatch.setattr(jobs.Executor, "_start", start)
    attached = Observed(ctx, True)
    attached.thread.start()
    try:
        attached.thread.join(5)
        assert not attached.thread.is_alive()
        assert attached.executor.epoch is None
        assert states == ["starting", "stopped"]
        assert not attached.status()["worker_available"]
    finally:
        attached.stop()
        attached.thread.join(10)
