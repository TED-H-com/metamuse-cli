#!/usr/bin/env python3
"""Official stdio client: attached worker, local collection, polling and search.

Every printed field comes from the actual SDK/server response. The server owns
its default worker; this client never runs a CLI worker or touches the store.
"""

from __future__ import annotations

import asyncio
import json
import time
from importlib.metadata import version
from pathlib import Path

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DEMO = Path("/tmp/metamuse-demo")


def show(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)


async def call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    if len(result.content) != 1 or not isinstance(result.content[0], types.TextContent):
        raise SystemExit(f"{name} result was not one text block")
    payload = json.loads(result.content[0].text)
    if result.is_error or payload["status"] == "error":
        raise SystemExit(f"{name} failed: {payload['error']}")
    return payload


async def main() -> None:
    if Path.cwd() != DEMO:
        raise SystemExit("refusing a workspace outside the demo directory")
    project = json.loads((DEMO / "out/project.json").read_text())["id"]
    params = StdioServerParameters(
        command="metamuse",
        args=["--workspace", "ws", "--non-interactive", "mcp"],
        cwd=str(DEMO),
        env={
            "PYTHONUNBUFFERED": "1",
            "HOME": str(DEMO / "home"),
            "PATH": "/tmp/metamuse-demo/bin:/usr/bin:/bin",
            "LANG": "zh_TW.UTF-8",
            "LC_ALL": "zh_TW.UTF-8",
            "TMPDIR": str(DEMO / "tmp"),
        },
    )
    print(f"client=mcp {version('mcp')} stdio_client", flush=True)
    with (DEMO / "out/mcp-stderr.txt").open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print(f"server={init.server_info.name} version={init.server_info.version}")
                print(f"protocol={init.protocol_version}")
                tools = (await session.list_tools()).tools
                print(f"tools={len(tools)}")
                for tool in tools:
                    hint = bool(tool.annotations and tool.annotations.read_only_hint)
                    print(f"tool={tool.name} readOnly={str(hint).lower()}")
                inspected = (await call(session, "metamuse_inspect", {}))["data"]
                show(
                    {
                        "call": "metamuse_inspect",
                        **{key: inspected[key] for key in ("worker_available", "attached_worker")},
                    }
                )
                accepted = await call(
                    session,
                    "metamuse_collect",
                    {
                        "project_id": project,
                        "inputs": [
                            {
                                "kind": "local_file",
                                "path": str(DEMO / "inputs/mcp.jsonl"),
                                "format": "jsonl",
                            }
                        ],
                        "resources": ["post", "comments"],
                        "limits": {
                            "max_urls": 10,
                            "max_items": 500,
                            "max_bytes": 1073741824,
                            "wall_clock_seconds": 900,
                        },
                        "request_key": "mcp-file",
                        "access_grant_id": None,
                    },
                )
                data = accepted["data"]
                show(
                    {
                        "call": "metamuse_collect",
                        "status": accepted["status"],
                        **{key: data[key] for key in ("job_id", "state", "worker_available")},
                    }
                )
                deadline = time.monotonic() + 30
                previous = None
                while True:
                    job = (await call(session, "metamuse_job_get", {"job_id": data["job_id"]}))[
                        "data"
                    ]["job"]
                    if job["state"] != previous:
                        show(
                            {
                                "call": "metamuse_job_get",
                                "state": job["state"],
                                "coverage_status": job["coverage_status"],
                            }
                        )
                        previous = job["state"]
                    if job["state"] in {"succeeded", "partial", "failed", "cancelled"}:
                        break
                    if time.monotonic() >= deadline:
                        raise SystemExit("attached worker did not finish the synthetic import")
                    await asyncio.sleep(0.1)
                (DEMO / "out/mcp-job.json").write_text(json.dumps(job))
                hit = (
                    await call(
                        session,
                        "metamuse_search",
                        {
                            "project_id": project,
                            "query": "正常留言第一行",
                            "limit": 1,
                        },
                    )
                )["data"]["results"][0]
                show(
                    {
                        "call": "metamuse_search",
                        "ref": hit["ref"],
                        "collection_method": hit["source"]["collection_method"],
                        "upstream_collection_method": hit["source"]["upstream_collection_method"],
                    }
                )


if __name__ == "__main__":
    asyncio.run(main())
