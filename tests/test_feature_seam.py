import json
from importlib import import_module

import pytest

from metamuse import app, cli, jobs
from metamuse import models as m

REF = {"kind": "item_revision", "id": "itm_fixture", "revision": 1}
PROJECT = "prj_fixture"
KEY = "fixture-key"
COLLECT = {
    "project_id": PROJECT,
    "inputs": [{"kind": "local_file", "path": "synthetic.jsonl", "format": "jsonl"}],
    "resources": ["comments"],
    "limits": {
        "max_urls": 10,
        "max_items": 500,
        "max_bytes": 1073741824,
        "wall_clock_seconds": 900,
    },
    "access_grant_id": None,
    "request_key": KEY,
}
FEATURES = [
    ("collect", "submit_collect", m.CollectRequest, COLLECT),
    ("content", "search_evidence", m.SearchRequest, {"project_id": PROJECT, "query": "繁中"}),
    (
        "content",
        "prepare_packet",
        m.PacketPrepareRequest,
        {"project_id": PROJECT, "selection": [REF], "request_key": KEY},
    ),
    (
        "content",
        "save_draft",
        m.DraftSaveRequest,
        {
            "project_id": PROJECT,
            "text": "原文😀\r\n",
            "platform": "generic",
            "facts_refs": [],
            "request_key": KEY,
        },
    ),
    ("content", "preview_draft", m.DraftPreviewRequest, {"draft_id": "drf_fixture", "revision": 1}),
    (
        "content",
        "export_objects",
        m.ExportRequest,
        {"project_id": PROJECT, "selection": [REF], "format": "jsonl", "request_key": KEY},
    ),
    (
        "muse",
        "open_muse_discussion",
        m.MuseOpenRequest,
        {
            "project_id": PROJECT,
            "topic": "Fixture",
            "access_grant_id": "pmt_fixture",
            "request_key": KEY,
        },
    ),
    (
        "muse",
        "submit_muse_exchange",
        m.MuseSendRequest,
        {
            "discussion_id": "dsc_fixture",
            "packet_id": "pkt_fixture",
            "question": "Fixture?",
            "share_grant_id": "pmt_fixture",
            "request_key": KEY,
        },
    ),
    (
        "muse",
        "read_muse_exchange",
        m.MuseReadRequest,
        {"exchange_id": "exc_fixture", "refresh": True},
    ),
    (
        "muse",
        "close_muse_exchange",
        m.MuseCloseExchangeRequest,
        {"exchange_id": "exc_fixture", "outcome": "answered", "request_key": KEY},
    ),
]


@pytest.mark.parametrize("module_name,function,model,payload", FEATURES)
def test_each_application_delegates_with_validated_request(
    ctx, monkeypatch, module_name, function, model, payload
):
    module = import_module(f"metamuse.{module_name}")
    seen = []

    def delegate(context, request):
        assert context is ctx and isinstance(request, model)
        seen.append(request)
        # Test delegation independently of whether this feature has been implemented.
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")

    monkeypatch.setattr(module, function, delegate)
    result = getattr(app, function)(ctx, payload)
    assert result.error.code == m.ErrorCode.UNSUPPORTED
    assert result.error.message == "not implemented in this build"
    assert len(seen) == 1
    bad = getattr(app, function)(ctx, {**payload, "approved": True})
    assert bad.error.code == m.ErrorCode.INPUT_INVALID and len(seen) == 1


FILE_COMMANDS = [
    ["draft", "save", "--project", PROJECT, "--platform", "generic", "--text-file"],
    [
        "muse",
        "send",
        "--discussion",
        "dsc_fixture",
        "--packet",
        "pkt_fixture",
        "--share-grant",
        "pmt_fixture",
        "--question-file",
    ],
    ["packet", "prepare", "--project", PROJECT, "--selection-file"],
    ["export", "--project", PROJECT, "--format", "jsonl", "--refs-file"],
]


@pytest.mark.parametrize("command", FILE_COMMANDS)
@pytest.mark.parametrize(
    "content,code,exit_status",
    [(b"x" * 17, "E_LIMIT_REACHED", 5), (b"\xff", "E_INPUT_INVALID", 2)],
)
def test_cli_input_file_boundaries(
    ctx, tmp_path, monkeypatch, capsys, command, content, code, exit_status
):
    _bind_context(ctx, monkeypatch)
    ctx.limits = m.Limits(text_max_bytes=16, question_max_utf8_bytes=16)
    path = tmp_path / "input"
    path.write_bytes(content)
    assert cli.main(["--json", *command, str(path), "--request-key", KEY]) == exit_status
    response = json.loads(capsys.readouterr().out)
    assert response["error"]["code"] == code
    # File validation happens before any feature function is called.
    assert response["error"]["message"] != "not implemented in this build"


@pytest.mark.parametrize(
    "content", [b"{", b"[NaN]", b"[Infinity]", b"[1e400]", b'{"a":1,"a":2}', b"{}"]
)
@pytest.mark.parametrize("command", FILE_COMMANDS[2:])
def test_cli_rejects_invalid_selection_json(ctx, tmp_path, monkeypatch, capsys, command, content):
    _bind_context(ctx, monkeypatch)
    path = tmp_path / "selection.json"
    path.write_bytes(content)
    assert cli.main(["--json", *command, str(path), "--request-key", KEY]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_INPUT_INVALID"


def test_file_limits_count_utf8_bytes_and_preserve_contents(ctx, tmp_path, monkeypatch, capsys):
    from metamuse import muse

    _bind_context(ctx, monkeypatch)
    ctx.limits = m.Limits(question_max_utf8_bytes=4)
    path = tmp_path / "question.txt"
    path.write_bytes("😀".encode())
    seen = []

    def send(context, request):
        seen.append(request.question)
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")

    monkeypatch.setattr(muse, "submit_muse_exchange", send)
    command = ["--json", *FILE_COMMANDS[1], str(path), "--request-key", KEY]
    assert cli.main(command) == 4
    capsys.readouterr()
    assert seen == ["😀"]
    path.write_bytes("😀a".encode())
    assert cli.main(command) == 5
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_LIMIT_REACHED"
    assert seen == ["😀"]


@pytest.mark.parametrize("content", [b"\xff", b"x" * 17])
@pytest.mark.parametrize("flag", ["--facts-file", "--scope-file"])
def test_optional_json_file_limits(ctx, tmp_path, monkeypatch, capsys, content, flag):
    _bind_context(ctx, monkeypatch)
    ctx.limits = m.Limits(text_max_bytes=16)
    path = tmp_path / "input.json"
    path.write_bytes(content)
    text = tmp_path / "text.txt"
    text.write_text("synthetic", encoding="utf-8")
    if flag == "--facts-file":
        command = [*FILE_COMMANDS[0], str(text), flag, str(path)]
    else:
        command = ["permit", "create", "--project", PROJECT, "--kind", "share", flag, str(path)]
    code = 2 if content == b"\xff" else 5
    assert cli.main(["--json", *command, "--request-key", KEY]) == code
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_permit_scope_rejects_float_overflow(ctx, tmp_path, monkeypatch, capsys):
    _bind_context(ctx, monkeypatch)
    path = tmp_path / "scope.json"
    path.write_text('{"limit":1e400}', encoding="utf-8")
    assert (
        cli.main(
            [
                "--json",
                "permit",
                "create",
                "--project",
                PROJECT,
                "--kind",
                "share",
                "--scope-file",
                str(path),
                "--request-key",
                KEY,
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_INPUT_INVALID"


@pytest.mark.parametrize(
    "source",
    [
        ["--file", "synthetic.jsonl"],
        ["--url", "https://example.invalid/post"],
        ["--url", "https://example.invalid/post", "--kind", "jsonl", "--resources", "post"],
    ],
)
def test_add_requires_unambiguous_typed_source(ctx, monkeypatch, capsys, source):
    _bind_context(ctx, monkeypatch)
    assert cli.main(["--json", "add", "--project", PROJECT, *source, "--request-key", KEY]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_INPUT_INVALID"


def _fake_collect(ctx, monkeypatch, handler, *, steps=1):
    from metamuse import collect

    _bind_context(ctx, monkeypatch)
    monkeypatch.setattr(jobs, "OPERATIONS", {})
    registered = []
    accepted = []

    def register(registry=None):
        registered.append(registry)
        target = jobs.OPERATIONS if registry is None else registry
        if "fixture.collect" not in target:
            jobs.register_operation(
                "fixture.collect", handler, "none", request_model=m.CollectRequest, registry=target
            )

    # Exercise the real fixed import hook rather than replacing app.register_builtin_operations.
    monkeypatch.setattr(collect, "register_operations", register)

    def submit(context, request):
        assert registered  # registration precedes submission, including without --run
        result = jobs.submit_job(
            context.store,
            "fixture.collect",
            request.request_key,
            request.model_dump(mode="json"),
            project_id=request.project_id,
            steps=[jobs.StepSpec(target={"batch": sequence}) for sequence in range(steps)],
        )
        accepted.append(result)
        return result

    monkeypatch.setattr(collect, "submit_collect", submit)
    return registered, accepted


def _add_args(project, *, run=True):
    return [
        "--json",
        "add",
        "--project",
        project,
        "--file",
        "synthetic.jsonl",
        "--kind",
        "jsonl",
        "--request-key",
        KEY,
        *(["--run"] if run else []),
    ]


def test_add_run_registers_and_finishes_all_target_steps(ctx, project, monkeypatch, capsys):
    seen = []
    later = []

    def handler(step):
        seen.append(step.target["batch"])
        step.commit_checkpoint({"done": True})
        if step.target["batch"] == 0:
            later.append(
                jobs.submit_job(
                    ctx.store,
                    "fixture.collect",
                    "later",
                    {},
                    registry={"fixture.collect": jobs.Operation(handler, "none")},
                ).data["job_id"]
            )
        return m.JobResult(coverage_status="scope_satisfied", observed_scope={"batches": 1})

    registered, accepted = _fake_collect(ctx, monkeypatch, handler, steps=2)
    assert cli.main(_add_args(project)) == 0
    response = json.loads(capsys.readouterr().out)
    assert registered == [None] and seen == [0, 1]
    assert response["status"] == "ok" and response["data"]["state"] == "succeeded"
    assert response["data"]["job_id"] == accepted[0].data["job_id"]
    assert response["data"]["job"]["coverage_status"] == "scope_satisfied"
    assert not response["data"]["worker_available"]
    assert ctx.store.get(m.Job, later[0]).state == "queued"  # stop at this job, not drain queue
    assert not jobs.worker_available(ctx.store)


@pytest.mark.parametrize(
    "error,state,status",
    [(m.ErrorCode.STORAGE, "failed", 7), (m.ErrorCode.AUTH_REQUIRED, "awaiting_user", 3)],
)
def test_add_run_stops_on_failure_or_user_action(
    ctx, project, monkeypatch, capsys, error, state, status
):
    calls = []

    def handler(step):
        calls.append(step.job.id)
        raise m.CoreError(error, "Synthetic failure", next_action="Inspect synthetic state")

    _, accepted = _fake_collect(ctx, monkeypatch, handler)
    assert cli.main(_add_args(project)) == status
    response = json.loads(capsys.readouterr().out)
    assert response["error"]["code"] == error and len(calls) == 1
    assert ctx.store.get(m.Job, accepted[0].data["job_id"]).state == state
    assert not jobs.worker_available(ctx.store)


def test_add_run_partial_job_preserves_coverage(ctx, project, monkeypatch, capsys):
    _fake_collect(
        ctx,
        monkeypatch,
        lambda step: m.JobResult(
            coverage_status="partial",
            stop_reason="fixture_limit",
            missing_fields=["text"],
            observed_scope={"items": 1},
        ),
    )
    assert cli.main(_add_args(project)) == 5
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "partial" and response["data"]["state"] == "partial"
    assert response["data"]["job"]["missing_fields"] == ["text"]


def test_add_run_with_existing_executor_retains_accepted_queue(ctx, project, monkeypatch, capsys):
    called = []
    _, accepted = _fake_collect(ctx, monkeypatch, lambda step: called.append(step))
    with jobs.Executor(ctx.store):
        assert cli.main(_add_args(project)) == 0
        output = capsys.readouterr()
        response = json.loads(output.out)
        assert response["status"] == "accepted" and response["data"]["state"] == "queued"
        assert response["data"]["worker_available"]
        assert "queued" in output.err and "Another executor" in response["warnings"][0]
        assert ctx.store.get(m.Job, accepted[0].data["job_id"]).state == "queued"
        assert called == []


def test_add_without_run_registers_but_does_not_execute(ctx, project, monkeypatch, capsys):
    called = []
    registered, accepted = _fake_collect(ctx, monkeypatch, lambda step: called.append(step))
    assert cli.main(_add_args(project, run=False)) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "accepted" and registered == [None] and not called
    assert ctx.store.get(m.Job, accepted[0].data["job_id"]).state == "queued"


def test_unsupported_add_run_does_not_start_executor(ctx, monkeypatch, capsys):
    _bind_context(ctx, monkeypatch)
    monkeypatch.setattr(
        cli, "Executor", lambda store: pytest.fail("Unsupported add started worker")
    )
    assert cli.main(_add_args(PROJECT)) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_REFERENCE_INVALID"


def test_mcp_lifecycle_registers_and_owns_stdout(ctx, monkeypatch, capsys):
    from metamuse import mcp

    _bind_context(ctx, monkeypatch)
    seen = []
    monkeypatch.setattr(app, "register_builtin_operations", lambda: seen.append("register"))

    def serve(context):
        assert context is ctx and seen == ["register"]
        print('{"jsonrpc":"2.0","id":1,"result":{}}')

    monkeypatch.setattr(mcp, "run", serve)
    assert cli.main(["mcp"]) == 0
    assert capsys.readouterr().out == '{"jsonrpc":"2.0","id":1,"result":{}}\n'


def test_human_only_requests_are_not_mcp_tools():
    assert len(m.TOOL_REQUEST_MODELS) == len(app.TOOL_FUNCTIONS) == 15
    assert m.AuthImportRequest not in m.TOOL_REQUEST_MODELS.values()
    assert m.PermitCreateRequest not in m.TOOL_REQUEST_MODELS.values()


def test_feature_capabilities_aggregate_without_granting_verification(ctx, monkeypatch):
    from metamuse import collect, content, muse

    monkeypatch.setattr(collect, "IMPLEMENTED_TOOLS", frozenset({"metamuse_collect"}))
    monkeypatch.setattr(content, "IMPLEMENTED_TOOLS", frozenset({"metamuse_search"}))
    monkeypatch.setattr(muse, "IMPLEMENTED_TOOLS", frozenset({"metamuse_muse_send"}))
    for module in (collect, content, muse):
        monkeypatch.setattr(module, "capability_status", lambda ctx: {}, raising=False)
    rows = {row["name"]: row for row in app.inspect_capabilities(ctx).data["capabilities"]}
    for name in ("metamuse_collect", "metamuse_search", "metamuse_muse_send"):
        assert rows[name]["implementation"] == "implemented"
        assert rows[name]["verification"] == "unverified"
        assert rows[name]["permission"] == "not_configured" and not rows[name]["effective"]
    assert rows["metamuse_export"]["implementation"] == "absent"
    monkeypatch.setattr(
        content,
        "capability_status",
        lambda context: {
            "metamuse_search": {
                "verification": "fixture_passed",
                "environment": "ready",
                "permission": "granted_scope",
            }
        },
        raising=False,
    )
    row = next(
        row
        for row in app.inspect_capabilities(ctx).data["capabilities"]
        if row["name"] == "metamuse_search"
    )
    assert row["effective"]


def test_builtin_registration_fixed_modules_and_optional_hooks(monkeypatch):
    seen = []
    for module in app._feature_modules():

        def register(registry, name=module.__name__):
            seen.append((name, registry))

        monkeypatch.setattr(module, "register_operations", register)
    registry = {}
    app.register_builtin_operations(registry)
    assert [name for name, _ in seen] == [
        "metamuse.collect",
        "metamuse.media",
        "metamuse.content",
        "metamuse.safety",
        "metamuse.muse",
    ]
    assert all(target is registry for _, target in seen)
    from metamuse import media

    monkeypatch.delattr(media, "register_operations")
    seen.clear()
    app.register_builtin_operations()
    assert len(seen) == 4 and all(target is None for _, target in seen)


def test_worker_registers_before_executor(ctx, monkeypatch):
    seen = []
    monkeypatch.setattr(app.AppContext, "open", staticmethod(lambda workspace: ctx))
    monkeypatch.setattr(app, "register_builtin_operations", lambda: seen.append("register"))

    class Worker:
        def __init__(self, store, *, non_interactive):
            assert seen == ["register"] and store is ctx.store
            assert not non_interactive

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def run(self, *, once):
            assert once
            seen.append("run")
            return 0

        def unavailable_operations(self):
            return False

    monkeypatch.setattr(cli, "Executor", Worker)
    result = cli.dispatch(cli.parser().parse_args(["worker", "run", "--once"]))
    assert result.status == "ok" and seen == ["register", "run"]


def _bind_context(ctx, monkeypatch):
    monkeypatch.setattr(app.AppContext, "open", staticmethod(lambda workspace: ctx))


# Expected request contents are independent of the CLI mapping implementation.
COMMANDS = [
    (
        ["project", "create", "--name", "Fixture", "--request-key", KEY],
        "app",
        "create_project",
        m.ProjectCreateRequest,
        {"name": "Fixture", "request_key": KEY},
    ),
    (["doctor"], "app", "inspect_capabilities", m.InspectRequest, {}),
    (
        ["jobs", "list", "--project", PROJECT, "--limit", "7", "--cursor", "fixture-cursor"],
        "app",
        "list_jobs",
        m.JobListQuery,
        {"project_id": PROJECT, "limit": 7, "cursor": "fixture-cursor"},
    ),
    (["jobs", "get", "job_fixture"], "app", "get_job", m.JobGetRequest, {"job_id": "job_fixture"}),
    (
        ["jobs", "resume", "job_fixture", "--request-key", KEY],
        "app",
        "control_job",
        m.JobControlRequest,
        {"job_id": "job_fixture", "operation": "resume", "request_key": KEY},
    ),
    (
        ["jobs", "cancel", "job_fixture", "--request-key", KEY],
        "app",
        "control_job",
        m.JobControlRequest,
        {"job_id": "job_fixture", "operation": "cancel", "request_key": KEY},
    ),
    (
        [
            "add",
            "--project",
            PROJECT,
            "--file",
            "synthetic.jsonl",
            "--kind",
            "jsonl",
            "--request-key",
            KEY,
        ],
        "collect",
        "submit_collect",
        m.CollectRequest,
        {
            "project_id": PROJECT,
            "inputs": [{"kind": "local_file", "path": "synthetic.jsonl", "format": "jsonl"}],
            "resources": ["post", "comments"],
            "limits": COLLECT["limits"],
            "access_grant_id": None,
            "request_key": KEY,
        },
    ),
    (
        [
            "add",
            "--project",
            PROJECT,
            "--url",
            "https://example.org/fixture",
            "--resources",
            "post, comments",
            "--access-grant",
            "pmt_fixture",
            "--request-key",
            KEY,
        ],
        "collect",
        "submit_collect",
        m.CollectRequest,
        {
            "project_id": PROJECT,
            "inputs": [{"kind": "url", "url": "https://example.org/fixture"}],
            "resources": ["post", "comments"],
            "limits": COLLECT["limits"],
            "access_grant_id": "pmt_fixture",
            "request_key": KEY,
        },
    ),
    (
        ["search", "--project", PROJECT, "--query", "繁中", "--cursor", "cursor_fixture"],
        "content",
        "search_evidence",
        m.SearchRequest,
        {"project_id": PROJECT, "query": "繁中", "cursor": "cursor_fixture"},
    ),
    (
        [
            "packet",
            "prepare",
            "--project",
            PROJECT,
            "--selection-file",
            "@refs",
            "--request-key",
            KEY,
        ],
        "content",
        "prepare_packet",
        m.PacketPrepareRequest,
        {"project_id": PROJECT, "selection": [REF], "request_key": KEY},
    ),
    (
        [
            "muse",
            "open",
            "--project",
            PROJECT,
            "--topic",
            "Fixture",
            "--access-grant",
            "pmt_fixture",
            "--request-key",
            KEY,
        ],
        "muse",
        "open_muse_discussion",
        m.MuseOpenRequest,
        {
            "project_id": PROJECT,
            "topic": "Fixture",
            "access_grant_id": "pmt_fixture",
            "request_key": KEY,
        },
    ),
    (
        [
            "muse",
            "send",
            "--discussion",
            "dsc_fixture",
            "--packet",
            "pkt_fixture",
            "--question-file",
            "@text",
            "--share-grant",
            "pmt_fixture",
            "--request-key",
            KEY,
        ],
        "muse",
        "submit_muse_exchange",
        m.MuseSendRequest,
        {
            "discussion_id": "dsc_fixture",
            "packet_id": "pkt_fixture",
            "question": "原文😀\r\né",
            "share_grant_id": "pmt_fixture",
            "request_key": KEY,
        },
    ),
    (
        [
            "muse",
            "read",
            "--exchange",
            "exc_fixture",
            "--refresh",
            "--cursor",
            "cursor_fixture",
            "--access-grant",
            "pmt_fixture",
        ],
        "muse",
        "read_muse_exchange",
        m.MuseReadRequest,
        {
            "exchange_id": "exc_fixture",
            "refresh": True,
            "cursor": "cursor_fixture",
            "access_grant_id": "pmt_fixture",
        },
    ),
    (
        [
            "muse",
            "close-exchange",
            "exc_fixture",
            "--outcome",
            "needs_more_data",
            "--request-key",
            KEY,
        ],
        "muse",
        "close_muse_exchange",
        m.MuseCloseExchangeRequest,
        {"exchange_id": "exc_fixture", "outcome": "needs_more_data", "request_key": KEY},
    ),
    (
        [
            "draft",
            "save",
            "--project",
            PROJECT,
            "--text-file",
            "@text",
            "--platform",
            "generic",
            "--request-key",
            KEY,
        ],
        "content",
        "save_draft",
        m.DraftSaveRequest,
        {
            "project_id": PROJECT,
            "text": "原文😀\r\né",
            "platform": "generic",
            "facts_refs": [],
            "request_key": KEY,
        },
    ),
    (
        [
            "draft",
            "save",
            "--project",
            PROJECT,
            "--text-file",
            "@text",
            "--platform",
            "threads",
            "--draft",
            "drf_fixture",
            "--expected-revision",
            "2",
            "--facts-file",
            "@refs",
            "--request-key",
            KEY,
        ],
        "content",
        "save_draft",
        m.DraftSaveRequest,
        {
            "project_id": PROJECT,
            "text": "原文😀\r\né",
            "platform": "threads",
            "draft_id": "drf_fixture",
            "expected_revision": 2,
            "facts_refs": [REF],
            "request_key": KEY,
        },
    ),
    (
        ["draft", "preview", "drf_fixture", "--revision", "2"],
        "content",
        "preview_draft",
        m.DraftPreviewRequest,
        {"draft_id": "drf_fixture", "revision": 2},
    ),
    (
        [
            "export",
            "--project",
            PROJECT,
            "--refs-file",
            "@refs",
            "--format",
            "bundle",
            "--request-key",
            KEY,
        ],
        "content",
        "export_objects",
        m.ExportRequest,
        {"project_id": PROJECT, "selection": [REF], "format": "bundle", "request_key": KEY},
    ),
    (
        [
            "auth",
            "import",
            "--file",
            "explicit-auth-file",
            "--provider",
            "fixture-provider",
            "--account",
            "fixture-account",
        ],
        "safety",
        "import_auth",
        m.AuthImportRequest,
        {
            "path": "explicit-auth-file",
            "provider": "fixture-provider",
            "account": "fixture-account",
        },
    ),
    (
        ["permit", "create", "--project", PROJECT, "--kind", "share", "--request-key", KEY],
        "safety",
        "create_permit",
        m.PermitCreateRequest,
        {"project_id": PROJECT, "kind": "share", "request_key": KEY},
    ),
    (
        [
            "permit",
            "create",
            "--project",
            PROJECT,
            "--kind",
            "share",
            "--scope-file",
            "@scope",
            "--provider",
            "fixture-provider",
            "--account",
            "fixture-account",
            "--issuer",
            "fixture-operator",
            "--expires-at",
            "2030-01-01T00:00:00+00:00",
            "--max-uses",
            "3",
            "--exact-digest",
            "a" * 64,
            "--request-key",
            KEY,
        ],
        "safety",
        "create_permit",
        m.PermitCreateRequest,
        {
            "project_id": PROJECT,
            "kind": "share",
            "scope": {"packet_digests": ["a" * 64]},
            "provider": "fixture-provider",
            "account": "fixture-account",
            "issuer": "fixture-operator",
            "expires_at": "2030-01-01T00:00:00+00:00",
            "max_uses": 3,
            "exact_digest": "a" * 64,
            "request_key": KEY,
        },
    ),
]


@pytest.mark.parametrize("command,module_name,function,model,expected", COMMANDS)
def test_cli_request_mapping(
    ctx, tmp_path, monkeypatch, capsys, command, module_name, function, model, expected
):
    _bind_context(ctx, monkeypatch)
    text = tmp_path / "text.txt"
    text.write_bytes("原文😀\r\né".encode())
    refs = tmp_path / "refs.json"
    refs.write_text(json.dumps([REF]), encoding="utf-8")
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"packet_digests": ["a" * 64]}), encoding="utf-8")
    paths = {"@text": str(text), "@refs": str(refs), "@scope": str(scope)}
    args = [paths.get(arg, arg) for arg in command]
    module = app if module_name == "app" else import_module(f"metamuse.{module_name}")
    seen = []

    def delegate(context, request):
        assert context is ctx and isinstance(request, model)
        assert request == model.model_validate(expected)
        seen.append(request)
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")

    monkeypatch.setattr(module, function, delegate)
    assert cli.main(["--json", *args]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_UNSUPPORTED"
    assert len(seen) == 1
