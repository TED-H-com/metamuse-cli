# MetaMuse CLI

Pre-alpha. Nothing is released yet.

A local-first tool for organising permitted social posts, comments, media and subtitles into sourced evidence packets, discussing them with Muse, and saving drafts and exports. One package (`metamuse`), one shared core for the CLI, MCP server and Skill.

This is an unofficial project. It is not affiliated with Meta, Facebook or Muse, and it does not claim official API access, terms-of-service compliance, or protection against account restrictions.

Credentials, account settings, real collected data and live-test evidence are never stored in this repository; the code reads them from operator-provided locations at runtime.

Design baseline: [docs/design/](docs/design/README.md).

The B02–B04 core supports offline initialization/diagnostics, projects, durable jobs,
SQLite migrations, content objects and backup/restore. Search, packets, drafts and
exports share the same application and MCP server. Muse lifecycle
supports explicitly selected fixture integration and separately authorized transport;
LIVE remains unverified and requires an operator-authorized account. See
[MUSE.md](docs/dev/MUSE.md) for binding, grants, jobs and recovery. Schema 3 adds Muse
lifecycle records after B05/B06's schema 2.

```sh
uv sync --python 3.12
uv run metamuse --workspace /path/to/private/workspace --json init
uv run metamuse --workspace /path/to/private/workspace --json doctor
pytest-disktmp uv run pytest -q
uv run ruff check .
```

Runtime workspaces belong outside the checkout. Operation registration, handler
boundaries and database APIs are documented in [CORE_API.md](docs/dev/CORE_API.md).
Dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No real
account integration or publishing capability is claimed by this core build.

## Skill

The agent skill definition lives in [skills/metamuse/](skills/metamuse/SKILL.md). The Skill requires an installed, reachable MetaMuse backend (CLI or MCP server) to execute operations; when the backend is unavailable, operations cannot be simulated.

License: [Apache-2.0](LICENSE).
