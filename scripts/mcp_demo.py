#!/usr/bin/env python3
"""Official MCP stdio client for one demo frame.

Runs `metamuse mcp` through mcp.client.stdio and calls one read-only tool.
The workspace path is the synthetic demo directory. Nothing is printed from
the process environment except the server's own names and the tool result.
"""

from __future__ import annotations

import asyncio
import json
import os
from importlib.metadata import version
from pathlib import Path

import mcp.types as types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DEMO = Path("/tmp/metamuse-demo")


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"missing {name}")
    return value


async def main() -> None:
    workspace = _required("MM_WORKSPACE")
    binary = _required("MM_BIN")
    project = Path(_required("MM_PROJECT_FILE")).read_text(encoding="utf-8").strip()
    if not workspace.startswith("/tmp/metamuse-demo"):
        raise SystemExit("refusing a workspace outside the demo directory")
    errlog_path = DEMO / "out" / "mcp-stderr.txt"
    errlog_path.parent.mkdir(parents=True, exist_ok=True)
    params = StdioServerParameters(
        command=binary,
        args=["--workspace", workspace, "--non-interactive", "mcp"],
        env={
            "PYTHONUNBUFFERED": "1",
            "HOME": str(DEMO / "home"),
            "PATH": "/tmp/metamuse-demo/bin:/usr/bin:/bin",
            "LANG": "zh_TW.UTF-8",
            "LC_ALL": "zh_TW.UTF-8",
            "TMPDIR": str(DEMO / "tmp"),
        },
    )
    print(f"client=mcp {version('mcp')} stdio_client")
    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print(f"server={init.server_info.name} version={init.server_info.version}")
                print(f"protocol={init.protocol_version}")
                listed = await session.list_tools()
                tools = listed.tools
                print(f"tools={len(tools)}")
                for tool in tools:
                    annotations = tool.annotations
                    read_only = bool(annotations and annotations.read_only_hint)
                    print(f"tool={tool.name} readOnly={str(read_only).lower()}")
                result = await session.call_tool(
                    "metamuse_search",
                    {"project_id": project, "query": "正常留言第一行", "limit": 1},
                )
                if len(result.content) != 1 or not isinstance(result.content[0], types.TextContent):
                    raise SystemExit("search result was not one text block")
                payload = json.loads(result.content[0].text)
                hit = payload["data"]["results"][0]
                ref = hit["ref"]
                print(
                    f"call=metamuse_search status={payload['status']} "
                    f"is_error={str(result.is_error).lower()}"
                )
                print(f"search_mode={payload['data']['search_mode']}")
                print(f"ref={ref['kind']} {ref['id']} revision={ref['revision']}")
                print(f"upstream={hit['source']['upstream_collection_method']}")
                print(f"collection={hit['source']['collection_method']}")
                print(f"complete={str(hit['source']['complete']).lower()}")
                print(f"snippet={hit['snippet']}")


if __name__ == "__main__":
    asyncio.run(main())
