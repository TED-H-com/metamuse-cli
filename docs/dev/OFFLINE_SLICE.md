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

B16a submit/prepare order is a read-only exact-payload receipt replay, the credential
check on new text, then preview/grant checks, persistence and work. Exact replay keeps
its original result after a rule upgrade. The worker checks session.start topics,
questions, packet fixpoints and body equality again before any token or gateway.
Frozen refusals prove `not_submitted`, close sends as `abandoned`, and keep the refusal's
own next action; a stale packet asks to prepare a new packet. No refused send quarantines
a discussion as an unknown delivery.

Stored discussion titles are not new outbound text and are never re-checked. Previews
show a display-redacted "Stored discussion title"; it is not in the send body or read
payload. Reconciliation compares its request topic with the exact stored title only.
The original reconciliation request, not the display-redacted topic, is used for the
human approval request file and retry. Approval failures propagate their actual fixed
code, message and next action rather than replacing them with a permit error.

Packets reach a fixpoint before hashing, with at most three passes as a bounded
backstop. Still-changing text is fixed `E_INPUT_INVALID` "Packet redaction did not
stabilize": invalid share text cannot be hashed or stored. Muse sends exactly stored
`packet.rendered_text`. Current rules changing an older packet cause `E_REFERENCE_INVALID`,
"Packet is stale; prepare a new packet", in prepare/preview/submit/wire, with no preview.
The body contains only the accepted original question, two newlines and that packet.
Private original exports retain `original_private` and their documented original bytes.

Redaction uses bounded possessive name grammars, forward scans and precomputed quote,
container and tag terminators. Every scan advances past consumed values; interval
streams merge with a fixed fan-in and output uses one join. Sparse, coalesced decoding
offsets allocate for escapes only. CPU growth is checked at four doubling sizes for
all review families and new rules. Packet/bundle resolution checks raw canonical sizes,
then renders/redacts before `BEGIN IMMEDIATE`; the write repeats reference/snapshot
checks. `_save_send` redacts the full remote detail (capped at 1 MiB) before its write
batch opens, then cuts to 512 characters. The remaining settlement callback sanitizes
only locally bounded outbound text (48 KiB packet, 8 KiB question, two joining bytes).
It rejects oversized legacy text before redaction with fixed `E_LIMIT_REACHED`; it
cannot turn uncertain delivery into proof. Configured limits may lower these hard caps.

`auth list` returns `partial` for corrupt records: valid metadata in `references`,
opaque refs and fixed reasons in `unavailable`. Its actions revoke regular records or
symlinks, or give a fixed manual instruction for FIFOs/directories/unowned entries.
`auth revoke` unlinks a symlink itself via dir_fd without opening its target; it refuses
other non-regular entries with that manual instruction. List/revoke read existing
configuration for config_bound but never initialize/open a workspace or Store.
Noninteractive auth/permit calls refuse before opening anything; scripted human
`permit approve --exact-digest` needs no TTY. Auth values and outside paths never appear.

Flags whose names are `authorization`, `proxy-authorization`, `cookie` or
`set-cookie` use the complete quoted value, or the complete remaining line when
unquoted, so schemes and cookie-chain members cannot fall outside the mask.

Plain YAML string values extend through the line (excluding a YAML comment);
TOML triple-quoted strings extend through their closing triple quote. Annotated
assignment defaults retain the type annotation and redact the actual value.
Sensitive XML elements may have quoted attributes; their content is redacted
whole, while a self-closing element has an empty value. Existing secure-cookie
and cloud-header name prefixes use the same three-layer decoding in structured
records as in text.
