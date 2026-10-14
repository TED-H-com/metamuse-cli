"""CLI parse/render only; application results never print themselves."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tomllib
from collections.abc import Sequence
from contextlib import ExitStack, nullcontext, redirect_stdout
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from metamuse import app
from metamuse import models as m
from metamuse.jobs import TERMINAL, Executor, worker_available
from metamuse.store import SCHEMA_VERSION


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Invalid command arguments",
            next_action="Run metamuse --help for command syntax",
        )


def _request_key(parser: Any) -> None:
    parser.add_argument("--request-key", required=True)


def _project(parser: Any, *, required: bool = True) -> None:
    parser.add_argument("--project", required=required)


def parser() -> Parser:
    root = Parser(prog="metamuse")
    root.add_argument("--workspace")
    root.add_argument("--json", action="store_true")
    root.add_argument("--non-interactive", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--live", action="store_true")
    project = commands.add_parser("project").add_subparsers(dest="action", required=True)
    create = project.add_parser("create")
    create.add_argument("--name", required=True)
    _request_key(create)
    jobs = commands.add_parser("jobs").add_subparsers(dest="action", required=True)
    listing = jobs.add_parser("list")
    _project(listing, required=False)
    listing.add_argument("--cursor")
    listing.add_argument("--limit", type=int, default=50)
    for name in ("get", "resume", "cancel"):
        cmd = jobs.add_parser(name)
        cmd.add_argument("id")
        if name != "get":
            _request_key(cmd)
    worker = commands.add_parser("worker").add_subparsers(dest="action", required=True)
    worker.add_parser("run").add_argument("--once", action="store_true")
    add = commands.add_parser("add")
    _project(add)
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--url")
    add.add_argument("--kind")
    add.add_argument("--resources")
    add.add_argument("--access-grant")
    add.add_argument("--item", help="Attach a subtitle or media file to this item")
    add.add_argument("--asset", help="Attach a subtitle file to this asset")
    add.add_argument("--language", help="Subtitle language; defaults to the file or und")
    add.add_argument("--run", action="store_true")
    _request_key(add)
    search = commands.add_parser("search")
    _project(search)
    search.add_argument("--query", required=True)
    search.add_argument("--cursor")
    search.add_argument("--limit", type=int, default=50)
    packet = commands.add_parser("packet").add_subparsers(dest="action", required=True)
    prepare = packet.add_parser("prepare")
    _project(prepare)
    prepare.add_argument("--selection-file", required=True)
    _request_key(prepare)
    muse = commands.add_parser("muse").add_subparsers(dest="action", required=True)
    opening = muse.add_parser("open")
    _project(opening)
    opening.add_argument("--topic", required=True)
    opening.add_argument("--access-grant", required=True)
    _request_key(opening)
    send = muse.add_parser("send")
    for arg in ("discussion", "packet", "question-file", "share-grant"):
        send.add_argument(f"--{arg}", required=True)
    _request_key(send)
    read = muse.add_parser("read")
    read.add_argument("--exchange", required=True)
    read.add_argument("--refresh", action="store_true")
    read.add_argument("--cursor")
    read.add_argument("--access-grant")
    close = muse.add_parser("close-exchange")
    close.add_argument("id")
    close.add_argument(
        "--outcome", required=True, choices=["answered", "needs_more_data", "abandoned"]
    )
    _request_key(close)
    draft = commands.add_parser("draft").add_subparsers(dest="action", required=True)
    save = draft.add_parser("save")
    _project(save)
    save.add_argument("--text-file", required=True)
    save.add_argument("--platform", required=True)
    save.add_argument("--draft")
    save.add_argument("--expected-revision", type=int)
    save.add_argument("--facts-file")
    save.add_argument("--assets-file")
    save.add_argument("--variant-file")
    save.add_argument("--preferences-file")
    _request_key(save)
    preview = draft.add_parser("preview")
    preview.add_argument("id")
    preview.add_argument("--revision", type=int, required=True)
    export = commands.add_parser("export")
    _project(export)
    export.add_argument("--refs-file", required=True)
    export.add_argument("--format", required=True)
    _request_key(export)
    commands.add_parser("mcp")
    for command, action in (("auth", "import"), ("permit", "create")):
        sub = commands.add_parser(command).add_subparsers(dest="action", required=True)
        stub = sub.add_parser(action)
        if command == "auth":
            stub.add_argument("--file", required=True)
            stub.add_argument("--provider")
            stub.add_argument("--account")
        else:
            _project(stub)
            stub.add_argument("--kind", required=True)
            stub.add_argument("--scope-file")
            stub.add_argument("--provider")
            stub.add_argument("--account")
            stub.add_argument("--issuer", default="local_operator")
            stub.add_argument("--expires-at")
            stub.add_argument("--max-uses", type=int, default=1)
            stub.add_argument("--exact-digest")
            _request_key(stub)
            revoke = sub.add_parser("revoke")
            revoke.add_argument("id")
            _request_key(revoke)
            listing = sub.add_parser("list")
            _project(listing)
    return root


def _read_utf8(path: str, max_bytes: int) -> str:
    """Count actual bytes and preserve all string contents, including CRLF."""
    try:
        with Path(path).expanduser().open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError as exc:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Cannot read the specified input file"
        ) from exc
    if len(content) > max_bytes:
        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input file exceeds configured byte limit")
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input file is not valid UTF-8") from exc


def _read_json(path: str, max_bytes: int) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError("Nonfinite JSON number")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Nonfinite JSON number")
        return number

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON key")
        return result

    try:
        return json.loads(
            _read_utf8(path, max_bytes),
            parse_constant=reject_constant,
            parse_float=finite_float,
            object_pairs_hook=unique_object,
        )
    except ValueError as exc:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input file is not valid strict JSON") from exc


def _collect_request(ctx: app.AppContext, args: argparse.Namespace) -> m.CollectRequest:
    if args.file:
        if args.kind is None:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Local add requires --kind")
        source = m.LocalFileInput(kind="local_file", path=args.file, format=args.kind)
        defaults = {
            "vtt": ["subtitles"],
            "srt": ["subtitles"],
            "media": ["media_metadata", "media_file"],
        }
        resources = defaults.get(args.kind, ["post", "comments"])
    else:
        if args.kind is not None or args.resources is None:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "URL add requires --resources and no --kind"
            )
        source = m.URLInput(kind="url", url=args.url)
        resources = []
    if args.resources is not None:
        resources = [part.strip() for part in args.resources.split(",")]
    limits = m.CollectLimits(
        **{key: getattr(ctx.limits, key) for key in m.CollectLimits.model_fields}
    )
    return m.CollectRequest(
        project_id=args.project,
        inputs=[source],
        resources=resources,
        limits=limits,
        access_grant_id=args.access_grant,
        request_key=args.request_key,
        attach_item_id=args.item,
        attach_asset_id=args.asset,
        language=args.language,
    )


def _run_foreground(ctx: app.AppContext, accepted: m.Envelope) -> m.Envelope:
    if accepted.status != "accepted":
        return accepted
    if not isinstance(accepted.data, dict) or not isinstance(accepted.data.get("job_id"), str):
        raise m.CoreError(m.ErrorCode.STORAGE, "Accepted response has no job ID")
    job_id = accepted.data["job_id"]
    with ExitStack() as stack:
        try:
            executor = stack.enter_context(Executor(ctx.store, non_interactive=ctx.non_interactive))
        except m.CoreError as exc:
            if exc.detail.code != m.ErrorCode.JOB_BUSY:
                raise
            return accepted.model_copy(
                update={
                    "warnings": [
                        *accepted.warnings,
                        "Another executor owns this workspace; "
                        "queued work remains with that executor.",
                    ]
                }
            )
        job = ctx.store.get(m.Job, job_id)
        while job.state not in (*TERMINAL, "awaiting_user"):
            worked = executor.run_once()
            job = ctx.store.get(m.Job, job_id)
            if not worked and job.state not in (*TERMINAL, "awaiting_user"):
                raise m.CoreError(
                    m.ErrorCode.UNSUPPORTED,
                    "Accepted job has no implemented runnable step in this build",
                    next_action=f"Inspect job {job_id}",
                )
    warnings = list(accepted.warnings)
    if job.state in ("failed", "awaiting_user") and job.error is not None:
        return m.Envelope(
            request_id=accepted.request_id,
            status="error",
            error=m.ErrorDetail.model_validate(job.error),
            warnings=[*warnings, f"Job {job_id} is {job.state}"],
        )
    if job.state == "awaiting_user":
        warnings.append("Job is awaiting user action; foreground execution stopped")
    return m.Envelope(
        request_id=accepted.request_id,
        status="partial" if job.state in ("partial", "awaiting_user") else "ok",
        data={
            "job_id": job.id,
            "state": job.state,
            "worker_available": worker_available(ctx.store),
            "job": job.model_dump(mode="json"),
        },
        warnings=warnings,
    )


def dispatch(args: argparse.Namespace) -> m.Envelope | None:
    ctx = app.AppContext.open(args.workspace)
    ctx.non_interactive = args.non_interactive
    result = _dispatch(ctx, args)
    if result is not None:
        result = result.model_copy(
            update={"warnings": list(dict.fromkeys([*result.warnings, *ctx.store.open_warnings]))}
        )
    return result


def _dispatch(ctx: app.AppContext, args: argparse.Namespace) -> m.Envelope | None:
    if args.command in ("worker", "add", "packet", "muse", "draft", "export", "mcp"):
        app.register_builtin_operations()
    match args.command:
        case "init":
            return m.Envelope(
                status="ok", data={"initialized": True, "schema_version": ctx.store.schema_version}
            )
        case "doctor":
            if args.live:
                return m.failure(
                    m.CoreError(
                        m.ErrorCode.UNSUPPORTED, "Live diagnostics are not available in this build"
                    )
                )
            return app.inspect_capabilities(ctx, m.InspectRequest())
        case "project":
            return app.create_project(
                ctx, m.ProjectCreateRequest(name=args.name, request_key=args.request_key)
            )
        case "jobs":
            if args.action == "list":
                return app.list_jobs(
                    ctx,
                    m.JobListQuery(project_id=args.project, cursor=args.cursor, limit=args.limit),
                )
            if args.action == "get":
                return app.get_job(ctx, m.JobGetRequest(job_id=args.id))
            return app.control_job(
                ctx,
                m.JobControlRequest(
                    job_id=args.id, operation=args.action, request_key=args.request_key
                ),
            )
        case "worker":
            with Executor(ctx.store, non_interactive=ctx.non_interactive) as executor:
                count = executor.run(once=args.once)
                unavailable = executor.unavailable_operations()
            return m.Envelope(
                status="partial" if unavailable else "ok",
                data={"steps_processed": count},
                warnings=[
                    "Jobs with unavailable operations remain queued; "
                    "use a build that implements them"
                ]
                if unavailable
                else [],
            )
        case "add":
            result = app.submit_collect(ctx, _collect_request(ctx, args))
            return _run_foreground(ctx, result) if args.run else result
        case "search":
            return app.search_evidence(
                ctx,
                m.SearchRequest(
                    project_id=args.project, query=args.query, cursor=args.cursor, limit=args.limit
                ),
            )
        case "packet":
            return app.prepare_packet(
                ctx,
                m.PacketPrepareRequest(
                    project_id=args.project,
                    selection=_read_json(args.selection_file, ctx.limits.text_max_bytes),
                    request_key=args.request_key,
                ),
            )
        case "muse":
            match args.action:
                case "open":
                    return app.open_muse_discussion(
                        ctx,
                        m.MuseOpenRequest(
                            project_id=args.project,
                            topic=args.topic,
                            access_grant_id=args.access_grant,
                            request_key=args.request_key,
                        ),
                    )
                case "send":
                    return app.submit_muse_exchange(
                        ctx,
                        m.MuseSendRequest(
                            discussion_id=args.discussion,
                            packet_id=args.packet,
                            question=_read_utf8(
                                args.question_file, ctx.limits.question_max_utf8_bytes
                            ),
                            share_grant_id=args.share_grant,
                            request_key=args.request_key,
                        ),
                    )
                case "read":
                    return app.read_muse_exchange(
                        ctx,
                        m.MuseReadRequest(
                            exchange_id=args.exchange,
                            refresh=args.refresh,
                            cursor=args.cursor,
                            access_grant_id=args.access_grant,
                        ),
                    )
                case "close-exchange":
                    return app.close_muse_exchange(
                        ctx,
                        m.MuseCloseExchangeRequest(
                            exchange_id=args.id, outcome=args.outcome, request_key=args.request_key
                        ),
                    )
        case "draft":
            if args.action == "save":
                return app.save_draft(
                    ctx,
                    m.DraftSaveRequest(
                        project_id=args.project,
                        text=_read_utf8(args.text_file, ctx.limits.text_max_bytes),
                        platform=args.platform,
                        draft_id=args.draft,
                        expected_revision=args.expected_revision,
                        facts_refs=_read_json(args.facts_file, ctx.limits.text_max_bytes)
                        if args.facts_file
                        else [],
                        assets=_read_json(args.assets_file, ctx.limits.text_max_bytes)
                        if args.assets_file
                        else [],
                        variant=_read_json(args.variant_file, ctx.limits.text_max_bytes)
                        if args.variant_file
                        else {},
                        preferences=_read_json(args.preferences_file, ctx.limits.text_max_bytes)
                        if args.preferences_file
                        else m.DraftPreferences(),
                        request_key=args.request_key,
                    ),
                )
            return app.preview_draft(
                ctx, m.DraftPreviewRequest(draft_id=args.id, revision=args.revision)
            )
        case "export":
            return app.export_objects(
                ctx,
                m.ExportRequest(
                    project_id=args.project,
                    selection=_read_json(args.refs_file, ctx.limits.text_max_bytes),
                    format=args.format,
                    request_key=args.request_key,
                ),
            )
        case "auth":
            from metamuse import safety

            ctx.require_interactive()
            return safety.import_auth(
                ctx,
                m.AuthImportRequest(path=args.file, provider=args.provider, account=args.account),
            )
        case "permit":
            from metamuse import safety

            ctx.require_interactive()
            if args.action == "revoke":
                return safety.revoke_permit(ctx, args.id, request_key=args.request_key)
            if args.action == "list":
                return safety.list_permits(ctx, project_id=args.project)
            return safety.create_permit(
                ctx,
                m.PermitCreateRequest(
                    project_id=args.project,
                    kind=args.kind,
                    scope=_read_json(args.scope_file, ctx.limits.text_max_bytes)
                    if args.scope_file
                    else {},
                    provider=args.provider,
                    account=args.account,
                    issuer=args.issuer,
                    expires_at=args.expires_at,
                    max_uses=args.max_uses,
                    exact_digest=args.exact_digest,
                    request_key=args.request_key,
                ),
            )
        case "mcp":
            from metamuse import mcp

            return mcp.run(ctx)
    raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Unknown command")


def render(result: m.Envelope, *, as_json: bool) -> None:
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if as_json:
        print(m.canonical_json(result).decode(), file=sys.stdout)
    elif result.error:
        print(f"{result.error.code.value}: {result.error.message}", file=sys.stderr)
        if result.error.next_action:
            print(result.error.next_action, file=sys.stderr)
    else:
        print(json.dumps(result.data, ensure_ascii=False, indent=2), file=sys.stdout)


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        stream=sys.stderr, level=logging.WARNING, format="%(levelname)s: %(message)s"
    )
    as_json = "--json" in argv
    try:
        with redirect_stdout(sys.stderr) if as_json else nullcontext():
            args = parser().parse_args(argv)
        as_json = args.json
        result = dispatch(args)
    except m.CoreError as exc:
        result = m.failure(exc)
    except SystemExit as exc:
        if exc.code == 0:
            if not as_json:
                return 0
            result = m.Envelope(status="ok", data={"help": True})
        else:
            result = m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid command arguments"))
    except ValidationError as exc:
        result = m.failure(m.validation_error(exc))
    except tomllib.TOMLDecodeError:
        result = m.failure(
            m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid input or workspace configuration")
        )
    except OSError:
        result = m.failure(m.CoreError(m.ErrorCode.STORAGE, "Workspace I/O failed"))
    except KeyboardInterrupt:
        result = m.Envelope(
            status="partial",
            warnings=["Interrupted; persisted jobs remain available"],
            data={"interrupted": True, "remote_state": "unknown"},
        )
        render(result, as_json=as_json)
        return 130
    if result is None:  # MCP lifecycle owns stdout and has already served its protocol.
        return 0
    render(result, as_json=as_json)
    return m.exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
