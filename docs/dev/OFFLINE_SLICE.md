# AT-042 journey through shipped entrypoints

The rerunnable acceptance starts a fresh synthetic workspace and drives
**data → packet → Muse question → follow-up in the same discussion → restart/read-back
→ host-written draft → export**. It uses the installed `metamuse` executable and the
official MCP Python `stdio_client`/`ClientSession`. No journey function calls an internal
application/prepare helper or accesses SQLite. No network, accounts, credentials,
publication or LIVE evidence is involved.

Run from the repository root:

```bash
UV_OFFLINE=1 uv sync --python 3.12
pytest-disktmp uv run pytest -q tests/test_journey.py
```

`tests/test_journey.py` is the full MCP-only acceptance. The human uses CLI `init`,
edits `metamuse.toml` to allow the synthetic input directory and explicitly selects
`[muse] enabled=true; backend="fixture"`, then uses only `permit approve`. The host
uses the fifteen shipped MCP tools over stdio, with the default attached Executor.
It never starts `worker run`, approves through MCP, calls Python internals or writes DB.

The host inspects, creates a project, queues local JSONL collection, polls job_get,
searches, prepares the fixed packet and previews it by replaying the same
packet_prepare request/key. The full pre-share preview is in the result, including
rendered_text, digest, source methods and redaction notice. Replay returns the same
packet. Operators can also use the CLI `packet preview ID` to inspect exact text.

## Operator approval round-trip

Call Muse open/send or a refreshing read without a grant. The envelope stays
E_SCOPE_REQUIRED with null data and an `approval` review, and creates nothing.
The preview shows configured provider/account, exact action digest, destination,
topic/question and, for shares, packet ID/digest/bytes and packet_preview_command.
The host shows the human this review, request JSON and next_action; the host never
executes the approval command. The human saves the request at the indicated path
and reviews it before running the command, for example:

```bash
metamuse --workspace /path/to/workspace --json permit approve \
  --operation muse.send --request-file /path/to/request.json \
  --exact-digest DIGEST_FROM_APPROVAL --request-key human-send-1
```

The command recomputes and prints the review on stderr, then issues only that exact
permit. Without --exact-digest it requires a terminal and typed `yes`. It refuses
--non-interactive even with a matching digest. Expiry defaults to 24h; --expires-at
can change it within the existing 30-day limit. The result contains the permit ID;
retry the unchanged MCP request with share_grant_id or access_grant_id. Model content
cannot choose provider/account or mint a grant. Changed request plus the old digest
is refused; a changed question with the old permit receives E_SCOPE_DENIED without a
preview. Repeat without the grant to obtain a fresh review.
Credential topics/questions are refused before preview creation. Accepted text is shown
exactly as sent, including benign paths; its digest binds those original bytes.

## Follow-up, restart and exported evidence

Each fixture answer is progress (`status=running`), so the host records
needs_more_data via muse_close_exchange before asking the follow-up in the same
discussion. It reads observations from both exchanges, closes the server, starts a
new one and reads the stored history back. Both questions and observation IDs survive;
original send attempts stay at one. A fresh authorized history poll also preserves
immutable observations and never resends. Closed message projections may gain a late
association on polling; immutable evidence IDs/digests remain the citation authority.

The host supplies its own draft text and facts_refs citing observations from both
exchanges. Draft save/preview and bundle plus Markdown exports keep
backend=fixture, mock=true, research_complete=false. Bundle verification checks
members, exact SHA-256/bytes, manifest attribution, redaction policy and labels.
Private formats use original_private text; select inputs accordingly. Invalid input
rows remain partial, never complete. live_verified never appears. Synthetic secret
markers are excluded from envelopes, logs and the selected exports.

For an abbreviated machine transcript outside the repository:

```bash
JOURNEY_TRANSCRIPT_PATH="$HOME/agent-work/tmp/journey-transcript.json" \
  pytest-disktmp uv run pytest -q tests/test_journey.py
```

`tests/test_at042_slice.py` also retains the installed CLI plus MCP --no-worker parity
path and all five export formats. Its approvals now come exclusively from CLI preview
and permit approve; it does not compute digests through preparation helpers or inspect
the database. The separate negative attribution test deliberately tampers with an
exported bundle and repairs its hashes; lost fixture labels must still be detected.

```bash
pytest-disktmp uv run pytest -q tests/test_at042_slice.py tests/test_at042_evidence.py \
  tests/test_at042_review_fixes.py tests/test_approval_cli.py tests/test_mcp_worker.py
```

The product bundle verifier resolves immutable citations from SQLite and checks
manifest attribution, JSONL records and Markdown source headers. Review regressions
strip or alter labels and rebuild consistent hashes and manifest bytes; verification
still returns the fixed E_RESOURCE_UNAVAILABLE export error. CLI and MCP receipt
replay use the same verifier.

B16a covers bare session-cookie pairs and mixed cookie chains. The AT-042 bare-cookie
bundle regression is a normal passing test; private exports retain `original_private`.

Worker process tests cover default execution, held-lock refusal, --no-worker,
SIGTERM/SIGINT safe drainage and SIGKILL recovery at a durable fixture send checkpoint.
Recovery requires authorized reads and never repeats the external send. This is
INTEGRATION/OFFLINE/FIXTURE evidence only. AT-043/LIVE remains blocked pending a
separate authorized environment. B16 still owns broader fault matrices, performance,
30-run p95/RSS, scaling and capacity measurement; this journey claims no MEASURE.

B16a submit and prepare order is read-only exact-payload receipt replay, credential
check, then preview/grant checks/persistence/work. Replay preserves accepted requests
after a rule upgrade. The worker checks frozen text again before issuing a token or
opening a gateway. Refused frozen bodies have proven `not_submitted` delivery; sends
close as `abandoned`, cannot block the discussion, and require preparing a new send.

B16a `auth list` returns `partial` when records are corrupt: valid metadata stays in
`references`, corrupt opaque refs/fixed reasons/exact revoke commands in `unavailable`,
and `next_action` lists those commands. Neither file contents nor paths are exposed.
Human-only auth/permit commands refuse noninteractive calls before opening a workspace;
scripted `permit approve --exact-digest` needs no TTY.

B16a redaction uses disjoint bounded possessive JSON key atoms (128 atoms), bounded
assignment/query names, original encoded spans and a single final join after overlap
merging. It has no dense per-character percent-offset map or per-span text rebuild.
Packet and export resolution check the raw canonical byte budget before rendering;
rendering/redaction happens outside `BEGIN IMMEDIATE`. The write transaction repeats
scope/existence checks and compares the evidence snapshot before committing.
Concurrent evidence changes require a fresh prepare/export; receipt conflicts and
artifact verification retain the shared core API.
