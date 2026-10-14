"""INTEGRATION/OFFLINE/FIXTURE: human CLI approval, host MCP, no internal calls or SQL."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SECRET = "SYNTHETIC_JOURNEY_SECRET"
COOKIE = "hatch_sess=" + SECRET
CORPUS = Path(__file__).parent / "fixtures/corpus/import/comments_tree.jsonl"


def mock(data):
    assert data["backend"] == "fixture"
    assert data["mock"] is True
    assert data["research_complete"] is False


class Entrypoints:
    def __init__(self, tmp_path):
        self.root = tmp_path
        self.workspace = tmp_path / "workspace"
        self.inputs = tmp_path / "inputs"
        self.inputs.mkdir()
        self.command = Path(sys.executable).with_name("metamuse")
        assert self.command.is_file()
        self.transcript = []
        self.diagnostics = []
        self.server_number = 0
        self.deadline = time.monotonic() + 600

    def remaining(self, command):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError("Test deadline exceeded: " + command)
        return remaining

    @contextmanager
    def waiting(self, command):
        try:
            with anyio.fail_after(self.remaining(command)):
                yield
        except TimeoutError:
            raise AssertionError("Test deadline exceeded: " + command) from None

    def cli(self, *args, operator=False, code=0, stdin=None):
        command = [str(self.command), "--workspace", str(self.workspace), "--json"]
        if not operator:
            command.append("--non-interactive")
        command.extend(str(arg) for arg in args)
        label = shlex.join(command)
        try:
            result = subprocess.run(
                command,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self.remaining(label),
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError("Test deadline exceeded: " + label) from None
        assert result.returncode == code, (result.stdout, result.stderr)
        envelope = json.loads(result.stdout)
        self.transcript.append(
            {
                "command": command,
                "exit_code": result.returncode,
                "stderr": result.stderr,
                "envelope": envelope,
            }
        )
        self.diagnostics.append(result.stderr)
        self.honest(envelope)
        return envelope

    def configure(self):
        assert self.cli("init")["data"]["schema_version"] == 5
        path = self.workspace / "metamuse.toml"
        path.write_text(
            path.read_text()
            .replace("[app]\n", "[app]\nimport_roots = " + json.dumps([str(self.inputs)]) + "\n")
            .replace("[muse]\nenabled = false", '[muse]\nenabled = true\nbackend = "fixture"')
        )

    @staticmethod
    def honest(envelope):
        raw = json.dumps(envelope)
        assert SECRET not in raw and COOKIE not in raw
        assert "live_verified" not in raw
        assert envelope["status"] != "complete"
        data = envelope.get("data") or {}
        if envelope["status"] == "accepted":
            assert "worker_available" in data
            if not data["worker_available"]:
                assert data["state"] == "queued"
            else:
                assert data["attached_worker"] == "running"
        if "research_complete" in data:
            assert data["research_complete"] is False
        job = data.get("job", {})
        if (job.get("result") or {}).get("coverage_status") == "partial":
            assert job["state"] == "partial"

    def approve(self, preview, request, key, *, digest=None, operator=True, code=0, extra=()):
        # The human stores the exact request shown by the host, then runs the shipped command.
        path = Path(preview["request_file"])
        path.write_text(json.dumps(request, ensure_ascii=False))
        command = shlex.split(preview["next_action"])
        assert command[:1] == ["metamuse"] and "--non-interactive" not in command
        args = command[command.index("permit") :]
        args[args.index("--request-key") + 1] = key
        if digest is not None:
            args[args.index("--exact-digest") + 1] = digest
        envelope = self.cli(*args, *extra, operator=operator, code=code)
        if operator and code == 0:
            assert preview["summary"] in self.diagnostics[-1]
            for value in (preview["destination"], preview["action_digest"]):
                assert value in preview["summary"]
            if preview["question"] is not None:
                assert preview["question"] in preview["summary"]
                assert preview["packet_preview_command"] in preview["summary"]
        return envelope

    @asynccontextmanager
    async def server(self, *extra, env=None):
        self.server_number += 1
        log = self.root / f"mcp-{self.server_number}.log"
        params = StdioServerParameters(
            command=str(self.command),
            args=["--workspace", str(self.workspace), "--non-interactive", "mcp", *extra],
            env=env,
        )
        with (
            log.open("w+") as errlog,
            self.waiting("metamuse mcp " + " ".join(extra)),
        ):
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    with self.waiting("MCP initialize"):
                        try:
                            await session.initialize()
                        except TimeoutError:
                            raise AssertionError("Test deadline exceeded: MCP initialize") from None
                    yield session
        self.diagnostics.append(log.read_text())

    async def call(self, session, tool, request, *, error=False):
        label = "MCP metamuse_" + tool
        try:
            with anyio.fail_after(self.remaining(label)):
                result = await session.call_tool("metamuse_" + tool, request)
        except TimeoutError:
            raise AssertionError("Test deadline exceeded: " + label) from None
        assert result.is_error is error, json.dumps(result.structured_content, ensure_ascii=False)
        envelope = result.structured_content
        assert json.loads(result.content[0].text) == envelope
        self.honest(envelope)
        self.transcript.append(
            {"tool": "metamuse_" + tool, "request": request, "envelope": envelope}
        )
        return envelope

    async def terminal(self, session, job_id):
        with self.waiting("MCP metamuse_job_get " + job_id):
            while True:
                data = (await self.call(session, "job_get", {"job_id": job_id}))["data"]
                if data["job"]["state"] in (
                    "succeeded",
                    "partial",
                    "failed",
                    "cancelled",
                    "awaiting_user",
                ):
                    assert data["job"]["state"] in ("succeeded", "partial"), json.dumps(
                        data, ensure_ascii=False
                    )
                    return data
                await anyio.sleep(0.05)

    async def approved_call(self, session, operation, request, key):
        tool = operation.replace(".", "_")
        denied = await self.call(session, tool, request, error=True)
        assert denied["error"]["code"] == "E_SCOPE_REQUIRED"
        assert denied["data"] is None
        preview = denied["approval"]
        assert preview["provider"] == preview["account"] == "fixture"
        grant = self.approve(preview, request, key)["data"]["id"]
        field = "share_grant_id" if operation == "muse.send" else "access_grant_id"
        return await self.call(session, tool, {**request, field: grant})

    def hygiene(self):
        for raw in self.diagnostics:
            assert SECRET not in raw and COOKIE not in raw
        for directory in (self.workspace / "exports", self.workspace / "logs"):
            if directory.exists():
                for path in directory.rglob("*"):
                    if path.is_file():
                        raw = path.read_bytes()
                        assert SECRET.encode() not in raw and COOKIE.encode() not in raw
                        assert b"live_verified" not in raw


def verify_bundle_or_markdown(workspace, data, observations, text):
    directory = workspace / data["artifact"]
    manifest = json.loads((directory / "manifest.json").read_bytes())
    assert manifest == data["manifest"]
    mock(manifest)
    expected = {o["id"]: o["observation_digest"] for o in observations}
    assert {e["ref"]["id"]: e["observation_digest"] for e in manifest["muse_evidence"]} == expected
    assert manifest["redaction_notice"]
    assert manifest["text_policy"] == (
        "redacted" if data["format"] == "bundle" else "original_private"
    )
    assert {p.name for p in directory.iterdir()} == {
        "manifest.json",
        *(f["name"] for f in manifest["files"]),
    }
    for member in manifest["files"]:
        raw = (directory / member["name"]).read_bytes()
        assert len(raw) == member["bytes"] and hashlib.sha256(raw).hexdigest() == member["sha256"]
        assert SECRET.encode() not in raw and COOKIE.encode() not in raw
        assert b"live_verified" not in raw
        if member["name"].endswith(".jsonl"):
            records = [json.loads(line) for line in raw.splitlines()]
            derived = [
                r for r in records if r["ref"]["kind"] in ("draft_revision", "muse_observation")
            ]
            assert len(derived) == 3
            for record in derived:
                mock(record)
                mock(record["source"])
            assert next(r for r in derived if r["ref"]["kind"] == "draft_revision")["text"] == text
        else:
            body = raw.decode()
            if text in body or "Fixture reply" in body:
                assert '"backend":"fixture"' in body
                assert '"mock":true' in body
                assert '"research_complete":false' in body


def test_mcp_only_journey_followup_restart_and_export(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    corpus = entry.inputs / "comments.jsonl"
    corpus.write_text(
        "\n".join(CORPUS.read_text().splitlines()[:3])
        + "\n{bad\n"
        + json.dumps(
            {
                "platform": "generic",
                "kind": "post",
                "native_scope": "journey",
                "native_id": "hygiene",
                "text": "Cookie: " + COOKIE,
            }
        )
        + "\n"
    )

    async def scenario():
        exchanges = []
        observations = []
        job_snapshots = {}
        async with entry.server() as session:
            inspected = (await entry.call(session, "inspect", {}))["data"]
            assert (
                inspected["worker_available"] is True and inspected["attached_worker"] == "running"
            )
            tools = (await session.list_tools()).tools
            assert len(tools) == 15 and not any("permit" in tool.name for tool in tools)
            project = (
                await entry.call(
                    session,
                    "project_create",
                    {"name": "Synthetic journey", "request_key": "project"},
                )
            )["data"]["id"]
            collected = await entry.call(
                session,
                "collect",
                {
                    "project_id": project,
                    "inputs": [{"kind": "local_file", "path": str(corpus), "format": "jsonl"}],
                    "resources": ["post", "comments"],
                    "limits": {
                        "max_urls": 10,
                        "max_items": 500,
                        "max_bytes": 1048576,
                        "wall_clock_seconds": 60,
                    },
                    "access_grant_id": None,
                    "request_key": "collect",
                },
            )
            assert (
                collected["status"] == "accepted" and collected["data"]["worker_available"] is True
            )
            imported = await entry.terminal(session, collected["data"]["job_id"])
            assert (
                imported["job"]["state"] == "partial"
                and imported["job"]["coverage_status"] == "partial"
            )
            search = (
                await entry.call(session, "search", {"project_id": project, "query": "這是"})
            )["data"]
            refs = [hit["ref"] for hit in search["results"]]
            assert len(refs) == 3
            request = {"project_id": project, "selection": refs, "request_key": "packet"}
            packet = (await entry.call(session, "packet_prepare", request))["data"]
            # Existing tool replays the fixed packet and its full pre-share preview.
            preview = (await entry.call(session, "packet_prepare", request))["data"]
            assert preview == packet
            assert hashlib.sha256(packet["rendered_text"].encode()).hexdigest() == packet["digest"]
            assert packet["redaction_notice"]
            opened = (
                await entry.approved_call(
                    session,
                    "muse.open",
                    {
                        "project_id": project,
                        "topic": "Synthetic activity review",
                        "request_key": "open",
                    },
                    "approve-open",
                )
            )["data"]
            await entry.terminal(session, opened["job_id"])
            discussion = opened["discussion_id"]
            for number, question in enumerate(
                ("List evidence and gaps.", "Follow up: which gaps remain after that answer?"), 1
            ):
                sending = {
                    "discussion_id": discussion,
                    "packet_id": packet["id"],
                    "question": question,
                    "request_key": f"send-{number}",
                }
                before = (await entry.call(session, "job_list", {"project_id": project}))["data"][
                    "jobs"
                ]
                denied = await entry.call(session, "muse_send", sending, error=True)
                approval = denied["approval"]
                assert (
                    approval["packet_id"] == packet["id"]
                    and approval["packet_digest"] == packet["digest"]
                )
                assert approval["question"] == question and approval["packet_utf8_bytes"] == len(
                    packet["rendered_text"].encode()
                )
                assert "packet preview" in approval["packet_preview_command"]
                after = (await entry.call(session, "job_list", {"project_id": project}))["data"][
                    "jobs"
                ]
                assert {j["id"] for j in before} == {j["id"] for j in after}
                changed = {**sending, "question": question + " Changed by model text."}
                entry.approve(approval, changed, f"mismatch-{number}", code=3)
                entry.approve(approval, sending, f"noninteractive-{number}", operator=False, code=3)
                grant = entry.approve(approval, sending, f"approve-send-{number}")["data"]["id"]
                bad = await entry.call(
                    session, "muse_send", {**changed, "share_grant_id": grant}, error=True
                )
                assert (
                    bad["error"]["code"] == "E_SCOPE_DENIED"
                    and bad["error"]["message"] == "Permit does not cover this action"
                    and bad["approval"] is None
                )
                sent = (
                    await entry.call(session, "muse_send", {**sending, "share_grant_id": grant})
                )["data"]
                assert sent["discussion_id"] == discussion
                job_snapshots[sent["job_id"]] = await entry.terminal(session, sent["job_id"])
                exchange = sent["exchange_id"]
                exchanges.append(exchange)
                refreshed = (
                    await entry.approved_call(
                        session,
                        "muse.read",
                        {"exchange_id": exchange, "refresh": True},
                        f"approve-read-{number}",
                    )
                )["data"]
                await entry.terminal(session, refreshed["refresh_job_id"])
                readback = (await entry.call(session, "muse_read", {"exchange_id": exchange}))[
                    "data"
                ]
                mock(readback)
                assert (
                    readback["association"] == "progress"
                    and readback["exchange"]["state"] == "sent"
                )
                observed = next(
                    o
                    for o in readback["observations"]
                    if o["raw_event"].get("payload", {}).get("role") == "assistant"
                    and o["raw_event"]["message_id"].startswith(
                        readback["exchange"]["request_marker"]
                    )
                )
                mock(observed)
                assert observed["raw_event"]["payload"]["status"] == "running"
                observations.append(observed)
                closed = await entry.call(
                    session,
                    "muse_close_exchange",
                    {
                        "exchange_id": exchange,
                        "outcome": "needs_more_data",
                        "request_key": f"close-{number}",
                    },
                )
                assert closed["data"]["research_complete"] is False
            before_restart = [
                (await entry.call(session, "muse_read", {"exchange_id": exchange}))["data"]
                for exchange in exchanges
            ]
        async with entry.server() as session:
            for exchange, prior in zip(exchanges, before_restart, strict=True):
                restored = (await entry.call(session, "muse_read", {"exchange_id": exchange}))[
                    "data"
                ]
                assert restored == prior
                assert {o["id"] for o in observations} <= {
                    o["id"] for o in restored["observations"]
                }
            refresh = (
                await entry.approved_call(
                    session,
                    "muse.read",
                    {"exchange_id": exchanges[-1], "refresh": True},
                    "approve-restart-read",
                )
            )["data"]
            await entry.terminal(session, refresh["refresh_job_id"])
            restored = (await entry.call(session, "muse_read", {"exchange_id": exchanges[-1]}))[
                "data"
            ]
            assert restored["observations"] == before_restart[-1]["observations"]
            assert restored["exchange"] == before_restart[-1]["exchange"]
            for job_id, snapshot in job_snapshots.items():
                assert (await entry.call(session, "job_get", {"job_id": job_id}))[
                    "data"
                ] == snapshot
                assert all(step["attempts"] == 1 for step in snapshot["steps"])
            text = "Host activity review cites both exchanges.\n" + "\n".join(
                o["id"] for o in observations
            )
            saved = (
                await entry.call(
                    session,
                    "draft_save",
                    {
                        "project_id": project,
                        "text": text,
                        "platform": "generic",
                        "facts_refs": [*refs, *(o["evidence_ref"] for o in observations)],
                        "request_key": "draft",
                    },
                )
            )["data"]
            mock(saved)
            assert len(saved["muse_evidence"]) == 2
            preview = (
                await entry.call(
                    session, "draft_preview", {"draft_id": saved["draft_id"], "revision": 1}
                )
            )["data"]
            mock(preview)
            assert preview["text"] == text
            for format_name in ("bundle", "markdown"):
                exported = (
                    await entry.call(
                        session,
                        "export",
                        {
                            "project_id": project,
                            "selection": [
                                {"kind": "draft_revision", "id": saved["draft_id"], "revision": 1}
                            ],
                            "format": format_name,
                            "request_key": "export-" + format_name,
                        },
                    )
                )["data"]
                verify_bundle_or_markdown(entry.workspace, exported, observations, text)

    anyio.run(scenario)
    entry.hygiene()
    transcript_path = os.environ.get("JOURNEY_TRANSCRIPT_PATH")
    if transcript_path:
        Path(transcript_path).write_text(json.dumps(entry.transcript, ensure_ascii=False, indent=2))
