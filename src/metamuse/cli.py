"""CLI parse/render only; application results never print themselves."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tomllib
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from metamuse import app
from metamuse import models as m
from metamuse.jobs import Executor


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
    add.add_argument("--run", action="store_true")
    _request_key(add)
    search = commands.add_parser("search")
    _project(search)
    search.add_argument("--query", required=True)
    search.add_argument("--cursor")
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
        else:
            _project(stub)
            stub.add_argument("--kind", required=True)
            _request_key(stub)
    return root


def dispatch(args: argparse.Namespace) -> m.Envelope:
    ctx = app.AppContext.open(args.workspace)
    match args.command:
        case "init":
            return m.Envelope(status="ok", data={"initialized": True, "schema_version": 1})
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
            with Executor(ctx.store) as executor:
                count = executor.run(once=args.once)
            return m.Envelope(status="ok", data={"steps_processed": count})
        case _:
            return app.unsupported()


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
        args = parser().parse_args(argv)
        as_json = args.json
        result = dispatch(args)
    except m.CoreError as exc:
        result = m.failure(exc)
    except (ValidationError, tomllib.TOMLDecodeError):
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
    render(result, as_json=as_json)
    return m.exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
