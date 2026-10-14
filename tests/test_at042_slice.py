"""AT-042: installed CLI processes and real MCP stdio, explicit offline fixture config."""

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from metamuse import models as m

CORPUS = Path(__file__).parent / "fixtures/corpus/import/comments_tree.jsonl"
SECRET = "SYNTHETIC_SLICE_SECRET"
COOKIE = f"hatch_sess={SECRET}"
HOST_TEXT = "=活動回顧（host 撰寫）\r\n留言與子回覆仍有缺口。😀 e\u0301\n"
FORMATS = ("bundle", "jsonl", "markdown", "text", "csv")


def assert_mock(data):
    assert data["backend"] == "fixture", "missing fixture backend label"
    assert data["mock"] is True, "missing mock label"
    assert data["research_complete"] is False, "missing research completion label"


def assert_provenance(data, observation):
    assert_mock(data)
    evidence = data["muse_evidence"]
    assert len(evidence) == 1
    assert_mock(evidence[0])
    assert evidence[0]["ref"] == observation["evidence_ref"]
    assert evidence[0]["observation_digest"] == observation["observation_digest"]


def assert_same_result(first, second):
    # Each frontend assigns a new request ID; application data/semantics are identical.
    assert {k: v for k, v in first.items() if k != "request_id"} == {
        k: v for k, v in second.items() if k != "request_id"
    }


def verify_export(workspace, data, observation):
    directory = workspace / data["artifact"]
    manifest = json.loads((directory / "manifest.json").read_bytes())
    assert manifest == data["manifest"]
    assert_provenance(manifest, observation)
    assert manifest["text_policy"] == (
        "redacted" if data["format"] == "bundle" else "original_private"
    )
    assert manifest["redaction_notice"]
    assert (
        manifest["redactions"] is not None
        if data["format"] == "bundle"
        else manifest["redactions"] is None
    )
    assert {p.name for p in directory.iterdir()} == {
        "manifest.json",
        *(member["name"] for member in manifest["files"]),
    }
    for member in manifest["files"]:
        raw = (directory / member["name"]).read_bytes()
        assert len(raw) == member["bytes"]
        assert hashlib.sha256(raw).hexdigest() == member["sha256"]
        assert SECRET.encode() not in raw and COOKIE.encode() not in raw
        assert b"live_verified" not in raw
        if member["name"].endswith(".jsonl"):
            records = [json.loads(line) for line in raw.splitlines()]
            derived = [
                r for r in records if r["ref"]["kind"] in ("draft_revision", "muse_observation")
            ]
            assert len(derived) == 2
            for record in derived:
                assert_provenance(record, observation)
                assert_provenance(record["source"], observation) if record["ref"][
                    "kind"
                ] == "draft_revision" else assert_mock(record["source"])
            assert (
                next(r for r in derived if r["ref"]["kind"] == "draft_revision")["text"]
                == HOST_TEXT
            )
            assert (
                next(r for r in derived if r["ref"]["kind"] == "muse_observation")["text"]
                == "Fixture reply, part one."
            )
        elif member["name"].endswith(".csv"):
            rows = list(csv.DictReader(io.StringIO(raw.decode(), newline="")))
            draft = next(row for row in rows if row["kind"] == "draft_revision")
            assert draft["text"] == "'" + HOST_TEXT
            for row in rows:
                if row["kind"] in ("draft_revision", "muse_observation"):
                    assert_mock(json.loads(row["source"]))
        else:
            # Every derived text/Markdown member has attribution beside its body.
            text = raw.decode()
            if HOST_TEXT in text or "Fixture reply, part one." in text:
                assert '"backend":"fixture"' in text
                assert '"mock":true' in text
                assert '"research_complete":false' in text


class Journey:
    def __init__(self, tmp_path):
        self.workspace = tmp_path / "workspace"
        self.inputs = tmp_path / "inputs"
        self.inputs.mkdir()
        self.command = Path(sys.executable).with_name("metamuse")
        assert self.command.is_file(), "Exercise the installed entry point"
        self.transcript = []
        self.stderr = []

    def json_file(self, name, value):
        path = self.inputs / name
        path.write_bytes(m.canonical_json(value))
        return str(path)

    def cli(self, *args, operator=False, exit_code=0):
        command = [str(self.command), "--workspace", str(self.workspace), "--json"]
        if not operator:
            command.append("--non-interactive")
        command.extend(str(arg) for arg in args)
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        assert result.returncode == exit_code, (args, result.stdout, result.stderr)
        envelope = json.loads(result.stdout)
        m.Envelope.model_validate(envelope)
        self.transcript.append({"command": command, "envelope": envelope})
        self.stderr.append(result.stderr)
        self.honest(envelope)
        return envelope

    @staticmethod
    def honest(envelope):
        raw = m.canonical_json(envelope)
        assert SECRET.encode() not in raw and COOKIE.encode() not in raw
        assert b"live_verified" not in raw
        data = envelope["data"] or {}
        if envelope["status"] == "accepted":
            assert data["state"] == "queued" and data["worker_available"] is False
        if "research_complete" in data:
            assert data["research_complete"] is False
        if (data.get("job", {}).get("result") or {}).get("coverage_status") == "partial":
            assert data["job"]["state"] == "partial"

    def preview_request(self, operation, request):
        if operation == "muse.open":
            return self.cli(
                "muse",
                "open",
                "--project",
                request["project_id"],
                "--topic",
                request["topic"],
                "--request-key",
                request["request_key"],
                exit_code=3,
            )["approval"]
        if operation == "muse.send":
            question = self.inputs / "approval-question.txt"
            question.write_bytes(request["question"].encode())
            return self.cli(
                "muse",
                "send",
                "--discussion",
                request["discussion_id"],
                "--packet",
                request["packet_id"],
                "--question-file",
                question,
                "--request-key",
                request["request_key"],
                exit_code=3,
            )["approval"]
        return self.cli(
            "muse",
            "read",
            "--exchange",
            request["exchange_id"],
            "--refresh",
            exit_code=3,
        )["approval"]

    def approve(self, operation, request, preview, key, **kwargs):
        assert preview["operation"] == operation
        assert preview["provider"] == preview["account"] == "fixture"
        return self.cli(
            "permit",
            "approve",
            "--operation",
            operation,
            "--request-file",
            self.json_file(key + "-request.json", request),
            "--exact-digest",
            preview["action_digest"],
            "--request-key",
            key,
            operator=True,
            **kwargs,
        )["data"]["id"]

    def open_request(self, project, key):
        request = {"project_id": project, "topic": "Synthetic activity review", "request_key": key}
        preview = self.preview_request("muse.open", request)
        request["access_grant_id"] = self.approve("muse.open", request, preview, key + "-grant")
        return request

    def send_request(self, project, discussion, packet, key):
        request = {
            "discussion_id": discussion,
            "packet_id": packet["id"],
            "question": "List evidence and gaps.",
            "request_key": key,
        }
        preview = self.preview_request("muse.send", request)
        request["share_grant_id"] = self.approve("muse.send", request, preview, key + "-grant")
        return request

    def read_request(self, project, discussion, exchange, key):
        request = {"exchange_id": exchange, "refresh": True}
        preview = self.preview_request("muse.read", request)
        request["access_grant_id"] = self.approve("muse.read", request, preview, key + "-grant")
        return request

    def worker(self, job_id):
        before = self.cli("jobs", "get", job_id)["data"]
        assert before["job"]["state"] == "queued"
        assert all(step["attempts"] == 0 for step in before["steps"])
        assert self.cli("worker", "run", "--once")["data"]["steps_processed"] == 1
        after = self.cli("jobs", "get", job_id)["data"]
        assert after["job"]["state"] == "succeeded", after

    @staticmethod
    def observation(envelope):
        data = envelope["data"]
        assert_mock(data)
        assert data["association"] == "progress"
        assert data["exchange"]["state"] == "sent"
        observation = next(
            o
            for o in data["observations"]
            if o["raw_event"].get("payload", {}).get("role") == "assistant"
        )
        assert_mock(observation)
        assert observation["raw_event"]["payload"]["status"] == "running"
        return observation


def test_at042_cli_and_mcp_stdio_journey(tmp_path):
    journey = Journey(tmp_path)
    assert journey.cli("init")["data"]["schema_version"] == 4
    config = journey.workspace / "metamuse.toml"
    config.write_text(
        config.read_text()
        .replace("[app]\n", "[app]\nimport_roots = " + json.dumps([str(journey.inputs)]) + "\n")
        .replace("[muse]\nenabled = false", '[muse]\nenabled = true\nbackend = "fixture"')
    )
    doctor = journey.cli("doctor")["data"]
    assert doctor["network_mode"] == "offline" and doctor["worker_available"] is False
    assert all(
        row["verification"] == "fixture_passed"
        for row in doctor["capabilities"]
        if row["name"].startswith("metamuse_muse_")
    )
    project = journey.cli(
        "project", "create", "--name", "Synthetic slice", "--request-key", "project"
    )["data"]["id"]
    corpus = journey.inputs / "comments.jsonl"
    # Real synthetic corpus, including comment + reply. One invalid row proves partial honesty.
    corpus.write_text(
        "\n".join(CORPUS.read_text().splitlines()[:3])
        + "\n{bad\n"
        + json.dumps(
            {
                "platform": "generic",
                "kind": "post",
                "native_id": "hygiene",
                "native_scope": "slice",
                "text": f"hygiene Cookie: {COOKIE}",
                "upstream_collection_method": "synthetic_fixture",
            }
        )
        + "\n"
    )
    imported = journey.cli(
        "add",
        "--project",
        project,
        "--file",
        corpus,
        "--kind",
        "jsonl",
        "--request-key",
        "import",
        "--run",
        exit_code=5,
    )
    assert imported["status"] == "partial"
    job = journey.cli("jobs", "get", imported["data"]["job_id"])["data"]["job"]
    assert job["state"] == "partial" and job["result"]["coverage_status"] == "partial"
    search = journey.cli("search", "--project", project, "--query", "這是")["data"]
    refs = [hit["ref"] for hit in search["results"]]
    assert len(refs) == 3
    selection = journey.json_file("selection.json", refs)
    packet = journey.cli(
        "packet",
        "prepare",
        "--project",
        project,
        "--selection-file",
        selection,
        "--request-key",
        "packet",
    )["data"]
    assert packet["selection"] == refs and packet["redaction_notice"]
    assert hashlib.sha256(packet["rendered_text"].encode()).hexdigest() == packet["digest"]
    # Guard challenge is never printed by search or private exports; packet preview redacts it.
    rows = job["result"]["data"]["steps"][0]["data"]["rows"]
    dirty = rows[-1]
    assert dirty["status"] == "committed"
    challenge = journey.json_file(
        "challenge.json",
        [{"kind": "item_revision", "id": dirty["item_id"], "revision": dirty["revision"]}],
    )
    redacted = journey.cli(
        "packet",
        "prepare",
        "--project",
        project,
        "--selection-file",
        challenge,
        "--request-key",
        "redaction",
    )["data"]
    assert redacted["redactions"] and "[REDACTED]" in redacted["rendered_text"]

    opening = journey.open_request(project, "cli-open")
    opened = journey.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        opening["topic"],
        "--access-grant",
        opening["access_grant_id"],
        "--request-key",
        opening["request_key"],
    )["data"]
    journey.worker(opened["job_id"])
    sending = journey.send_request(project, opened["discussion_id"], packet, "cli-send")
    question = journey.inputs / "question.txt"
    question.write_bytes(sending["question"].encode())
    sent = journey.cli(
        "muse",
        "send",
        "--discussion",
        opened["discussion_id"],
        "--packet",
        packet["id"],
        "--question-file",
        question,
        "--share-grant",
        sending["share_grant_id"],
        "--request-key",
        sending["request_key"],
    )["data"]
    assert sent["mock"] is True
    assert journey.cli("muse", "read", "--exchange", sent["exchange_id"])["data"]["messages"] == []
    journey.worker(sent["job_id"])
    reading = journey.read_request(
        project, opened["discussion_id"], sent["exchange_id"], "cli-read"
    )
    refresh = journey.cli(
        "muse",
        "read",
        "--exchange",
        sent["exchange_id"],
        "--refresh",
        "--access-grant",
        reading["access_grant_id"],
    )["data"]
    journey.worker(refresh["refresh_job_id"])
    observation = journey.observation(
        journey.cli("muse", "read", "--exchange", sent["exchange_id"])
    )
    closed = journey.cli(
        "muse",
        "close-exchange",
        sent["exchange_id"],
        "--outcome",
        "needs_more_data",
        "--request-key",
        "cli-close",
    )
    assert closed["data"]["research_complete"] is False
    text_file = journey.inputs / "draft.txt"
    text_file.write_bytes(HOST_TEXT.encode())
    facts = journey.json_file("facts.json", [*refs, observation["evidence_ref"]])
    draft = journey.cli(
        "draft",
        "save",
        "--project",
        project,
        "--text-file",
        text_file,
        "--platform",
        "generic",
        "--facts-file",
        facts,
        "--request-key",
        "cli-draft",
    )["data"]
    assert draft["text"] == HOST_TEXT
    assert_provenance(draft, observation)
    preview = journey.cli("draft", "preview", draft["draft_id"], "--revision", "1")["data"]
    assert_provenance(preview, observation)
    export_refs = [{"kind": "draft_revision", "id": draft["draft_id"], "revision": 1}]
    refs_file = journey.json_file("export.json", export_refs)
    for format_name in FORMATS:
        exported = journey.cli(
            "export",
            "--project",
            project,
            "--refs-file",
            refs_file,
            "--format",
            format_name,
            "--request-key",
            "cli-export-" + format_name,
        )
        assert exported["status"] == "ok"
        verify_export(journey.workspace, exported["data"], observation)

    async def mcp_half():
        params = StdioServerParameters(
            env={
                **os.environ,
                "PYTHONPATH": os.environ.get(
                    "PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src")
                ),
            },
            command=str(journey.command),
            args=["--workspace", str(journey.workspace), "--non-interactive", "mcp", "--no-worker"],
        )
        log = tmp_path / "mcp-stderr.log"
        with log.open("w+") as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(name, request):
                        result = await session.call_tool("metamuse_" + name, request)
                        assert result.is_error is False
                        envelope = result.structured_content
                        assert json.loads(result.content[0].text) == envelope
                        m.Envelope.model_validate(envelope)
                        journey.honest(envelope)
                        journey.transcript.append(
                            {"tool": "metamuse_" + name, "arguments": request, "envelope": envelope}
                        )
                        return envelope

                    inspected = await call("inspect", {})
                    assert {
                        k: v
                        for k, v in inspected["data"].items()
                        if k not in ("attached_worker", "worker_next_action")
                    } == journey.cli("doctor")["data"]
                    opened = (await call("muse_open", journey.open_request(project, "mcp-open")))[
                        "data"
                    ]
                    journey.worker(opened["job_id"])
                    sent = (
                        await call(
                            "muse_send",
                            journey.send_request(
                                project, opened["discussion_id"], packet, "mcp-send"
                            ),
                        )
                    )["data"]
                    assert (await call("muse_read", {"exchange_id": sent["exchange_id"]}))["data"][
                        "messages"
                    ] == []
                    journey.worker(sent["job_id"])
                    refreshed = (
                        await call(
                            "muse_read",
                            journey.read_request(
                                project, opened["discussion_id"], sent["exchange_id"], "mcp-read"
                            ),
                        )
                    )["data"]
                    journey.worker(refreshed["refresh_job_id"])
                    read_request = {"exchange_id": sent["exchange_id"]}
                    read_envelope = await call("muse_read", read_request)
                    assert_same_result(
                        read_envelope,
                        journey.cli("muse", "read", "--exchange", sent["exchange_id"]),
                    )
                    observation = journey.observation(read_envelope)
                    await call(
                        "muse_close_exchange",
                        {
                            "exchange_id": sent["exchange_id"],
                            "outcome": "needs_more_data",
                            "request_key": "mcp-close",
                        },
                    )
                    saved = (
                        await call(
                            "draft_save",
                            {
                                "project_id": project,
                                "text": HOST_TEXT,
                                "platform": "generic",
                                "facts_refs": [*refs, observation["evidence_ref"]],
                                "request_key": "mcp-draft",
                            },
                        )
                    )["data"]
                    assert_provenance(saved, observation)
                    preview = await call(
                        "draft_preview", {"draft_id": saved["draft_id"], "revision": 1}
                    )
                    assert_provenance(preview["data"], observation)
                    assert_same_result(
                        preview,
                        journey.cli("draft", "preview", saved["draft_id"], "--revision", "1"),
                    )
                    for format_name in FORMATS:
                        request = {
                            "project_id": project,
                            "selection": [
                                {"kind": "draft_revision", "id": saved["draft_id"], "revision": 1}
                            ],
                            "format": format_name,
                            "request_key": "mcp-export-" + format_name,
                        }
                        exported = await call("export", request)
                        verify_export(journey.workspace, exported["data"], observation)
                        # CLI replays the MCP receipt and exact artifact through the same app.
                        assert_same_result(
                            exported,
                            journey.cli(
                                "export",
                                "--project",
                                project,
                                "--refs-file",
                                journey.json_file("mcp-export.json", request["selection"]),
                                "--format",
                                format_name,
                                "--request-key",
                                request["request_key"],
                            ),
                        )
        journey.stderr.append(log.read_text())

    anyio.run(mcp_half)
    for directory in (journey.workspace / "exports", journey.workspace / "logs"):
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file():
                    raw = path.read_bytes()
                    assert SECRET.encode() not in raw and COOKIE.encode() not in raw
                    assert b"live_verified" not in raw
    assert SECRET not in "\n".join(journey.stderr)
    path = os.environ.get("AT042_TRANSCRIPT_PATH")
    if path:
        Path(path).write_bytes(m.canonical_json(journey.transcript))
