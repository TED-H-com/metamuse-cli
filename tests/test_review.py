"""Synthetic regressions for the cross-vendor core review; no remote actions."""

import hashlib
import io
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from metamuse import app, cli, collect, jobs
from metamuse import models as m
from metamuse.schemas import design_rollup, generated_schemas


def configure(ctx, *, live=False, **limits):
    config = ctx.paths.root / "metamuse.toml"
    config.write_text(
        '[app]\nnetwork_mode = "'
        + ("live" if live else "offline")
        + '"\n'
        + "[limits]\n"
        + "".join(f"{key} = {value}\n" for key, value in limits.items())
    )


def operation(ctx, project, handler, side="none", key="review", **kwargs):
    registry = {}
    jobs.register_operation(
        "review.fake", handler, side, registry=registry, uncertain_state="send_uncertain"
    )
    response = jobs.submit_job(
        ctx.store,
        "review.fake",
        key,
        {"fixture": True},
        project_id=project,
        registry=registry,
        **kwargs,
    )
    assert response.status == "accepted"
    return registry, response.data["job_id"]


def uncertain(ctx, project):
    configure(ctx, live=True)

    def handler(step):
        step.before_external_action(step.target, verify=lambda conn, target: None)
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Synthetic timeout")

    registry, job_id = operation(ctx, project, handler, "external_submit")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    step = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]
    assert step["state"] == "send_uncertain"
    return registry, job_id, step


def packet(ctx, project):
    return ctx.store.insert(
        m.Packet(
            id="pkt_review",
            project_id=project,
            selection=[],
            rendered_text="fixture",
            digest=m.payload_digest("fixture"),
            created_at=ctx.clock(),
        )
    )


def permit(ctx, project, **kwargs):
    return ctx.store.insert(
        m.Permit(
            id="pmt_review",
            project_id=project,
            kind="share",
            scope={},
            issuer="fixture",
            issued_at=ctx.clock(),
            expires_at=kwargs.pop("expires_at", "2099-01-01T00:00:00+00:00"),
            max_uses=2,
            **kwargs,
        )
    )


def test_f01_cli_unknown_operation_preserves_queue(ctx, project, monkeypatch, capsys):
    _, job_id = operation(ctx, project, lambda step: m.JobResult())
    monkeypatch.setattr(jobs, "OPERATIONS", {})
    code = cli.main(["--workspace", str(ctx.paths.root), "--json", "worker", "run", "--once"])
    body = json.loads(capsys.readouterr().out)
    assert ctx.store.get(m.Job, job_id).state == "queued"
    assert body["data"]["steps_processed"] == 0 and code != 0


def test_f02_cancel_is_not_swallowed_or_resumed(ctx, project):
    configure(ctx, live=True)
    calls = []

    def handler(step):
        app.control_job(ctx, {"job_id": step.job.id, "operation": "cancel", "request_key": "stop"})
        try:
            step.before_external_action(step.target, verify=lambda conn, target: None)
        except Exception:
            calls.append("swallowed")
        calls.append("sent")
        return m.JobResult()

    registry, job_id = operation(ctx, project, handler, "external_submit")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    assert calls == []
    assert app.get_job(ctx, {"job_id": job_id}).data["steps"][0]["state"] == "send_uncertain"
    result = app.control_job(
        ctx, {"job_id": job_id, "operation": "resume", "request_key": "resume"}
    )
    assert result.error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN


def test_f03_reconcile_replay_is_error_with_job_handle(ctx, project):
    registry, job_id, _ = uncertain(ctx, project)
    replay = jobs.submit_job(
        ctx.store, "review.fake", "review", {"fixture": True}, project_id=project, registry=registry
    )
    assert replay.status == "error" and m.exit_code(replay) == 6
    assert replay.data is None and job_id in replay.error.next_action


def test_f04_same_size_corruption_and_repair(ctx):
    data = b"abcdefghij"
    asset = ctx.store.put_object(io.BytesIO(data), mime="text/plain")
    ctx.paths.object_path(asset.sha256).write_bytes(b"ABCDEFGHIJ")
    with pytest.raises(m.CoreError):
        ctx.store.get(m.Asset, asset.id)
    repaired = ctx.store.put_object(io.BytesIO(data), mime="text/plain")
    assert repaired.id == asset.id
    assert ctx.paths.object_path(asset.sha256).read_bytes() == data


def test_f04_forged_ready_asset_rejected(ctx):
    sha = "cd" * 32
    path = ctx.paths.object_path(sha)
    path.parent.mkdir()
    path.write_bytes(b"0123456789")
    with pytest.raises(m.CoreError):
        ctx.store.insert(
            m.Asset(
                id="ast_forged",
                sha256=sha,
                mime="text/plain",
                actual_bytes=10,
                object_path=str(path.relative_to(ctx.paths.root)),
                state="ready",
            )
        )


def test_f04_failed_backup_removes_only_its_partial_destination(ctx, project, tmp_path):
    asset = ctx.store.put_object(io.BytesIO(b"abcdefghij"), mime="text/plain")
    ctx.paths.object_path(asset.sha256).write_bytes(b"ABCDEFGHIJ")
    target = tmp_path / "failed-backup"
    with pytest.raises(m.CoreError):
        ctx.store.backup(target)
    assert not target.exists()
    assert ctx.store.get(m.Project, project).id == project and ctx.paths.database.exists()


def test_f06_external_success_requires_intent(ctx, project):
    configure(ctx, live=True)
    registry, job_id = operation(ctx, project, lambda step: m.JobResult(), "external_submit")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "failed" and job.error["code"] == "E_PROTOCOL_MISMATCH"


def test_f06_pre_boundary_crash_is_never_replayed(ctx, project):
    configure(ctx, live=True)
    calls = []

    def handler(step):
        calls.append("entered")
        if len(calls) == 1:
            raise KeyboardInterrupt()
        return m.JobResult()

    registry, job_id = operation(ctx, project, handler, "external_submit")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        with pytest.raises(KeyboardInterrupt):
            executor.run_once()
    with jobs.Executor(ctx.store, registry=registry) as executor:
        assert not executor.run_once()
    assert calls == ["entered"]
    assert app.get_job(ctx, {"job_id": job_id}).data["steps"][0]["state"] == "send_uncertain"


def test_f07_config_zero_safe_read_retries(ctx, project):
    configure(ctx, live=True, safe_read_retries=0)
    calls = []

    def handler(step):
        calls.append(1)
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Synthetic timeout")

    registry, _ = operation(ctx, project, handler, "safe_read")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        while executor.run_once():
            pass
    assert calls == [1]


def test_f07_question_limit_is_utf8_bytes():
    payload = dict(
        discussion_id="dsc_fixture",
        packet_id="pkt_fixture",
        question="😀" * 2048,
        share_grant_id="pmt_fixture",
        request_key="fixture",
    )
    assert len(m.MuseSendRequest(**payload).question.encode()) == 8192
    with pytest.raises(ValidationError):
        m.MuseSendRequest(**{**payload, "question": "😀" * 2049})


@pytest.mark.parametrize(
    "function,payload,limit",
    [
        (
            "submit_muse_exchange",
            dict(
                discussion_id="dsc_fixture",
                packet_id="pkt_fixture",
                question="😀a",
                share_grant_id="pmt_fixture",
                request_key="fixture",
            ),
            "question_max_utf8_bytes",
        ),
        (
            "save_draft",
            dict(
                project_id="prj_fixture",
                text="😀a",
                platform="generic",
                facts_refs=[],
                request_key="fixture",
            ),
            "text_max_bytes",
        ),
    ],
)
def test_f07_app_configured_bytes_apply_to_mcp_input(ctx, monkeypatch, function, payload, limit):
    ctx.limits = m.Limits(**{limit: 4})
    module = __import__(
        "metamuse.muse" if "muse" in function else "metamuse.content", fromlist=["x"]
    )
    monkeypatch.setattr(module, function, lambda *args: pytest.fail("Oversize reached feature"))
    result = getattr(app, function)(ctx, payload)
    assert result.error.code == m.ErrorCode.INPUT_INVALID


def test_f07_packet_store_limit(ctx, project):
    configure(ctx, packet_max_utf8_bytes=4)
    with pytest.raises(m.CoreError):
        packet(ctx, project)


def test_f07_retry_wait_is_bounded_and_cancel_checked(ctx, project):
    configure(ctx, live=True)
    delays = []

    def handler(step):
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Synthetic timeout", retry_after=3600)

    registry, job_id = operation(ctx, project, handler, "safe_read")

    def wait(delay):
        delays.append(delay)
        app.control_job(ctx, {"job_id": job_id, "operation": "cancel", "request_key": "stop"})

    with jobs.Executor(ctx.store, registry=registry, sleep=wait) as executor:
        executor.run_once()
        assert not executor.run_once()
    assert sum(delays) <= 0.25
    assert ctx.store.get(m.Job, job_id).state == "cancelled"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE packets SET rendered_text='rewritten'",
        "UPDATE packets SET digest='changed'",
        "UPDATE packets SET selection='[]'",
        "UPDATE packets SET redactions='[{}]'",
    ],
)
def test_f08_packet_payload_immutable(ctx, project, statement):
    packet(ctx, project)
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(statement)
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE packets SET stale=1")
    assert ctx.store.get(m.Packet, "pkt_review").stale


def test_f08_source_provenance_immutable(ctx, project):
    ctx.store.insert(
        m.Source(
            id="src_review",
            project_id=project,
            platform="generic",
            collection_method="automated",
            fetched_at=ctx.clock(),
        )
    )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE sources SET collection_method='manual'")


@pytest.mark.parametrize(
    "initial,statement",
    [
        ({"used": 1}, "UPDATE permits SET used=0"),
        ({"revoked": True}, "UPDATE permits SET revoked=0"),
        ({"revoked": True}, "UPDATE permits SET used=1"),
        ({"expires_at": "2000-01-01T00:00:00+00:00"}, "UPDATE permits SET used=1"),
        ({"expires_at": "invalid"}, "UPDATE permits SET used=1"),
    ],
)
def test_f09_permit_database_guards(ctx, project, initial, statement):
    permit(ctx, project, **initial)
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(statement)


@pytest.mark.parametrize("success", [True, False])
def test_f10_fenced_settle_does_not_resend(ctx, project, success):
    registry, job_id, step = uncertain(ctx, project)
    with jobs.Executor(ctx.store, registry=registry) as executor:
        with pytest.raises(m.CoreError):
            executor.settle_uncertain(
                step["id"],
                expected_target={"other": True},
                result=m.JobResult(),
                verify=lambda conn, target: None,
            )
        result = executor.settle_uncertain(
            step["id"],
            expected_target=step["target"],
            result=m.JobResult(coverage_status="bounded") if success else None,
            error=None if success else m.CoreError(m.ErrorCode.ACCESS_DENIED, "Proven refusal"),
            verify=lambda conn, target: None,
        )
        assert result.state == ("succeeded" if success else "failed")
        assert not executor.run_once()
    with pytest.raises(m.CoreError):
        executor.settle_uncertain(
            step["id"],
            expected_target=step["target"],
            result=m.JobResult(),
            verify=lambda conn, target: None,
        )
    assert ctx.store.get(m.Job, job_id).state == ("succeeded" if success else "failed")


def test_f10_uncertain_raw_requeue_rejected(ctx, project):
    _, _, step = uncertain(ctx, project)
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE job_steps SET state='queued' WHERE id=?", (step["id"],))


def test_f10_draft_revision_advances_head_and_cas(ctx, project):
    draft = ctx.store.insert(m.Draft(id="drf_review", project_id=project))

    def rev(number):
        return m.DraftRevision(
            draft_id=draft.id,
            revision=number,
            text="fixture",
            platform="generic",
            digest=m.payload_digest("fixture"),
        )

    ctx.store.insert(rev(1))
    ctx.store.insert(rev(2))
    assert ctx.store.get(m.Draft, draft.id).current_revision == 2
    with pytest.raises(m.CoreError) as failure:
        ctx.store.append_draft_revision(rev(3), expected_revision=1)
    assert failure.value.detail.code == m.ErrorCode.DATA_STALE
    ctx.store.append_draft_revision(rev(3), expected_revision=2)
    assert ctx.store.get(m.Draft, draft.id).current_revision == 3


@pytest.mark.parametrize("side", ["safe_read", "external_submit"])
def test_f10_offline_never_enters_external_handler(ctx, project, side):
    registry, job_id = operation(
        ctx, project, lambda step: pytest.fail("Offline entered handler"), side
    )
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    assert ctx.store.get(m.Job, job_id).error["code"] == "E_SCOPE_DENIED"


@pytest.mark.parametrize(
    "command",
    [
        ["auth", "import", "--file", "synthetic.json"],
        [
            "permit",
            "create",
            "--project",
            "prj_fixture",
            "--kind",
            "share",
            "--request-key",
            "fixture",
        ],
    ],
)
def test_f10_noninteractive_human_commands_are_refused(ctx, capsys, command):
    assert (
        cli.main(["--workspace", str(ctx.paths.root), "--json", "--non-interactive", *command]) == 3
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_SCOPE_REQUIRED"


def test_f10_checkpoint_writer_and_no_nested_transaction(ctx, project):
    def handler(step):
        def save(writer):
            writer.insert(
                m.Event(
                    id="evt_checkpoint", project_id=project, kind="fixture", created_at=ctx.clock()
                )
            )
            with pytest.raises(m.CoreError):
                with ctx.store.transaction():
                    pytest.fail("Nested transaction opened")

        step.commit_checkpoint({"saved": True}, write_batch=save)
        return m.JobResult()

    registry, job_id = operation(ctx, project, handler)
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    assert ctx.store.get(m.Job, job_id).state == "succeeded"
    assert ctx.store.get(m.Event, "evt_checkpoint").project_id == project


def test_f10_validation_field_paths_without_values(ctx):
    secret = "synthetic-value-must-not-appear"
    result = app.submit_collect(
        ctx,
        {
            "project_id": "prj_fixture",
            "inputs": [{"kind": "local_file", "path": secret, "format": secret}],
            "resources": ["post"],
            "limits": {},
            "access_grant_id": None,
            "request_key": "fixture",
        },
    )
    assert "inputs[0].local_file.format" in result.error.message
    assert secret not in m.canonical_json(result).decode()


def test_f10_exchange_outcome_sticky(ctx, project):
    packet(ctx, project)
    permit(ctx, project)
    ctx.store.insert(
        m.Discussion(
            id="dsc_review",
            project_id=project,
            provider="fixture",
            auth_ref="fixture",
            topic="fixture",
        )
    )
    ctx.store.insert(
        m.Exchange(
            id="exc_review",
            project_id=project,
            discussion_id="dsc_review",
            request_key="fixture",
            question="fixture",
            packet_id="pkt_review",
            digest=m.payload_digest("q"),
            permit_id="pmt_review",
            outcome="answered",
            state="closed",
        )
    )
    for outcome in (None, "abandoned"):
        with pytest.raises(m.CoreError):
            with ctx.store.transaction() as conn:
                conn.execute("UPDATE exchanges SET state='closed',outcome=?", (outcome,))


def test_f11_availability_never_acquires_worker_flock(ctx, monkeypatch):
    original = jobs.fcntl.flock

    def checked(handle, operation):
        if (
            Path(
                f"/proc/self/fd/{handle if isinstance(handle, int) else handle.fileno()}"
            ).resolve()
            == ctx.paths.worker_lock
        ):
            pytest.fail("Availability acquired the worker lock")
        return original(handle, operation)

    with jobs.Executor(ctx.store):
        monkeypatch.setattr(jobs.fcntl, "flock", checked)
        assert jobs.worker_available(ctx.store)
        monkeypatch.setattr(jobs.fcntl, "flock", original)
    monkeypatch.setattr(jobs.fcntl, "flock", checked)
    assert not jobs.worker_available(ctx.store)


@pytest.mark.parametrize("args", [["--json", "--help"], ["--json", "jobs", "--help"]])
def test_f12_json_help_has_one_envelope_and_stderr_help(capsys, args):
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "ok"
    assert "usage:" in output.err and "usage:" not in output.out


def test_f13_mime_conflict_is_not_silent(ctx):
    ctx.store.put_object(io.BytesIO(b"synthetic"), mime="text/plain")
    with pytest.raises(m.CoreError):
        ctx.store.put_object(io.BytesIO(b"synthetic"), mime="image/png")


def test_f14_sources_in_rollup_and_manifest():
    root = Path(__file__).resolve().parents[1] / "docs/design"
    combined = (root / "MetaMuse_CLI_Plan_Spec_SDS_v1.1.md").read_text()
    assert (root / "06_SOURCES.md").read_text().strip() in combined
    assert combined == design_rollup(root.parents[1])
    for record in json.loads((root / "MANIFEST.json").read_text())["files"]:
        content = (root / record["path"]).read_bytes()
        assert (
            len(content) == record["bytes"]
            and hashlib.sha256(content).hexdigest() == record["sha256"]
        )


def test_f15_project_created_event_has_project(ctx, project):
    with ctx.store.connection() as conn:
        event = conn.execute("SELECT * FROM events WHERE kind='project.created'").fetchone()
    assert event["object_id"] == event["project_id"] == project


def test_f16_all_core_tools_effective_and_stubs_absent(ctx):
    from metamuse import content

    rows = app.inspect_capabilities(ctx).data["capabilities"]
    effective = {row["name"] for row in rows if row["effective"]}
    # B05 declares metamuse_collect. B07/B11 declares the content tools.
    # Muse stays ineffective until explicitly configured.
    assert effective == (
        app.CORE_IMPLEMENTED_TOOLS | collect.IMPLEMENTED_TOOLS | content.IMPLEMENTED_TOOLS
    )
    assert all(not row["effective"] for row in rows if row["implementation"] == "absent")
    assert len(generated_schemas()) == 41


def test_f01_known_job_runs_past_unknown_job(ctx, project):
    _, unknown = operation(ctx, project, lambda step: m.JobResult(), key="unknown")
    registry = {}
    jobs.register_operation("known.fake", lambda step: m.JobResult(), "none", registry=registry)
    known = jobs.submit_job(
        ctx.store, "known.fake", "known", {}, project_id=project, registry=registry
    ).data["job_id"]
    with jobs.Executor(ctx.store, registry=registry) as executor:
        assert executor.run_once()
        assert not executor.run_once()
    assert ctx.store.get(m.Job, known).state == "succeeded"
    assert ctx.store.get(m.Job, unknown).state == "queued"


def test_f01_add_run_missing_handler_preserves_job(ctx, project, monkeypatch, capsys):
    from metamuse import collect

    _, job_id = operation(ctx, project, lambda step: m.JobResult())
    monkeypatch.setattr(jobs, "OPERATIONS", {})
    monkeypatch.setattr(
        collect,
        "submit_collect",
        lambda context, request: m.Envelope(
            status="accepted", data={"job_id": job_id, "state": "queued", "worker_available": False}
        ),
    )
    code = cli.main(
        [
            "--workspace",
            str(ctx.paths.root),
            "--json",
            "add",
            "--project",
            project,
            "--file",
            "synthetic.jsonl",
            "--kind",
            "jsonl",
            "--request-key",
            "fixture",
            "--run",
        ]
    )
    assert code == 4 and json.loads(capsys.readouterr().out)["error"]["code"] == "E_UNSUPPORTED"
    assert ctx.store.get(m.Job, job_id).state == "queued"


def test_f07_retry_after_cap_without_real_wait(ctx, project):
    configure(ctx, live=True)
    registry, _ = operation(
        ctx,
        project,
        lambda step: (_ for _ in ()).throw(
            m.CoreError(m.ErrorCode.RATE_LIMITED, "Synthetic", retry_after=float("inf"))
        ),
        "safe_read",
    )
    waits = []
    with jobs.Executor(ctx.store, registry=registry, sleep=waits.append) as executor:
        executor.run_once()
    assert sum(waits) == pytest.approx(30) and max(waits) <= 0.25


def test_f10_policy_rechecked_before_external_action(ctx, project):
    configure(ctx, live=True)
    called = []

    def handler(step):
        configure(ctx, live=False)
        step.before_external_action(
            step.target, verify=lambda conn, target: called.append("verified")
        )
        called.append("sent")
        return m.JobResult()

    registry, job_id = operation(ctx, project, handler, "external_submit")
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    assert called == [] and ctx.store.get(m.Job, job_id).error["code"] == "E_SCOPE_DENIED"


def test_f10_noninteractive_is_forwarded_to_handlers(ctx, project):
    def handler(step):
        step.require_interactive()
        pytest.fail("Non-interactive handler prompted")

    registry, job_id = operation(ctx, project, handler)
    with jobs.Executor(ctx.store, registry=registry, non_interactive=True) as executor:
        executor.run_once()
    assert ctx.store.get(m.Job, job_id).error["code"] == "E_SCOPE_REQUIRED"


def test_f10_settlement_verifier_rejection_is_atomic(ctx, project):
    registry, job_id, step = uncertain(ctx, project)

    def denied(conn, target):
        conn.execute("UPDATE projects SET name='must rollback' WHERE id=?", (project,))
        raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Unproven target")

    with jobs.Executor(ctx.store, registry=registry) as executor:
        with pytest.raises(m.CoreError):
            executor.settle_uncertain(
                step["id"], expected_target=step["target"], result=m.JobResult(), verify=denied
            )
    assert ctx.store.get(m.Project, project).name == "Synthetic project"
    assert ctx.store.get(m.Job, job_id).state == "awaiting_user"


def test_f10_cancelled_multistep_settlement_keeps_local_cancel(ctx, project):
    configure(ctx, live=True)

    def handler(step):
        step.before_external_action(step.target, verify=lambda conn, target: None)
        app.control_job(ctx, {"job_id": step.job.id, "operation": "cancel", "request_key": "stop"})
        step.guard()

    registry, job_id = operation(
        ctx, project, handler, "external_submit", steps=[jobs.StepSpec(), jobs.StepSpec()]
    )
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
        row = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]
        result = executor.settle_uncertain(
            row["id"],
            expected_target=row["target"],
            verify=lambda conn, target: None,
            result=m.JobResult(coverage_status="bounded"),
        )
        assert result.state == "cancelled" and result.remote_state == "confirmed"
        assert result.coverage_status == "partial"
        assert not executor.run_once()


def test_f10_checkpoint_cannot_commit_or_modify_epoch(ctx, project):
    def handler(step):
        def batch(writer):
            with pytest.raises(m.CoreError):
                writer.execute("COMMIT")
            writer.execute("UPDATE projects SET name='must rollback' WHERE id=?", (project,))
            writer.execute("UPDATE worker_state SET worker_epoch=worker_epoch+1")

        with pytest.raises(m.CoreError):
            step.commit_checkpoint({"wrong": True}, write_batch=batch)
        assert ctx.store.get(m.Project, project).name == "Synthetic project"
        return m.JobResult()

    registry, job_id = operation(ctx, project, handler)
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    assert ctx.store.get(m.Job, job_id).state == "succeeded"


def test_f11_fcntl_queries_do_not_observe_flock(ctx):
    import ctypes
    import fcntl
    import os

    class Flock(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_short),
            ("whence", ctypes.c_short),
            ("start", ctypes.c_longlong),
            ("length", ctypes.c_longlong),
            ("pid", ctypes.c_int),
        ]

    with jobs.Executor(ctx.store):
        with ctx.paths.worker_lock.open("rb") as descriptor:
            for name in ("F_GETLK", "F_OFD_GETLK"):
                request = Flock(fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
                # Linux UAPI F_OFD_GETLK=36; some CPython builds omit the exported constant.
                command = getattr(fcntl, name, 36)
                response = fcntl.fcntl(descriptor.fileno(), command, bytes(request))
                assert Flock.from_buffer_copy(response).type == fcntl.F_UNLCK
        assert jobs.worker_available(ctx.store)


def test_f09_valid_consumption_uses_store_clock_and_cannot_exceed_budget(ctx, project):
    permit(ctx, project)
    ctx.store.clock = lambda: "2098-01-01T00:00:00+00:00"
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE permits SET used=used+1")
    assert ctx.store.get(m.Permit, "pmt_review").used == 1
    ctx.store.clock = lambda: "2099-01-01T00:00:00+00:00"
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE permits SET used=used+1")
    ctx.store.clock = lambda: "2098-01-01T00:00:00+00:00"
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE permits SET used=1.5")
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE permits SET used=used+1")
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE permits SET used=used+1")


def test_f10_unknown_property_names_are_not_echoed(ctx):
    value = "SyntheticSecretNeverShow"
    result = app.create_project(ctx, {"name": "fixture", "request_key": "fixture", value: value})
    assert "extra" in result.error.message
    assert value not in m.canonical_json(result).decode()


def test_f07_stored_packet_remains_readable_after_limit_changes(ctx, project):
    configure(ctx, packet_max_utf8_bytes=100000)
    record = m.Packet.model_validate(
        dict(
            id="pkt_large",
            project_id=project,
            selection=[],
            rendered_text="😀" * 16384,
            digest=m.payload_digest("fixture"),
            created_at=ctx.clock(),
        ),
        context={"limits": m.Limits(packet_max_utf8_bytes=100000)},
    )
    ctx.store.insert(record)
    configure(ctx, packet_max_utf8_bytes=4)
    assert ctx.store.get(m.Packet, record.id).rendered_text == record.rendered_text


def test_f07_claim_revalidates_payload_against_current_limits(ctx, project):
    registry = {}
    jobs.register_operation(
        "review.typed",
        lambda step: pytest.fail("Invalid payload reached handler"),
        "none",
        request_model=m.DraftSaveRequest,
        registry=registry,
    )
    response = jobs.submit_job(
        ctx.store,
        "review.typed",
        "typed",
        dict(
            project_id=project, text="😀a", platform="generic", facts_refs=[], request_key="typed"
        ),
        project_id=project,
        registry=registry,
    )
    configure(ctx, text_max_bytes=4)
    with jobs.Executor(ctx.store, registry=registry) as executor:
        executor.run_once()
    job = ctx.store.get(m.Job, response.data["job_id"])
    assert job.state == "failed" and job.error["code"] == "E_INPUT_INVALID"
    assert "text" in job.error["message"]
