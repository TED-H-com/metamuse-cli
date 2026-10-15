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
Frozen refusals prove not_submitted, close sends as abandoned and retain the refusal's
own next action. No refused send quarantines a discussion as unknown delivery.

Stored discussion titles are never re-checked as new outbound text. Previews show a
display-redacted "Stored discussion title", outside the send/read body. Reconciliation
identifies a discussion by discussion_id and project; its request topic must equal
the stored title or its current display-redacted form. approval.request and saved
request files carry only that display-redacted title. If the display changed since
saving, E_REFERENCE_INVALID asks to request reconciliation again. Approval failures
propagate their real fixed code, message and next action.

Muse sends exactly stored packet.rendered_text. Current rules changing an older
packet cause E_REFERENCE_INVALID "Packet is stale; prepare a new packet" in prepare,
preview, submit and wire, without a preview or rewriting stored bytes. Send guidance
is "Prepare a new packet and send request with a new exact share grant". The body is
the accepted original question, two newlines and that packet. Private original
exports retain original_private and their documented original bytes.

Redaction uses bounded possessive name grammars, forward scans and precomputed
quote/container/tag terminators. Scans advance past consumed values; a fixed number
of ordered interval streams merge and each pass builds output with one join.
Original-offset maps are sparse/coalesced; changed canonical views additionally use
one byte of decoding depth per character. The four-application bound is constant.
CPU growth of the iterated public call is checked at four doubling sizes for all
review and new-rule families. Raw canonical sizes are checked before rendering;
packet/bundle rendering and redaction precede BEGIN IMMEDIATE. Writes repeat only
reference/snapshot checks. _save_send redacts the full remote detail (capped at 1 MiB)
before its batch, then cuts to 512 characters. The settlement callback sanitizes
only locally bounded outbound text (48 KiB packet, 8 KiB question, two joining bytes)
inside the jobs transaction. Oversized legacy text refuses before redaction with
fixed E_LIMIT_REACHED; this bound is measured and does not prove delivery.

Auth list returns partial for corrupt entries: valid metadata in references, opaque
refs and fixed reasons in unavailable. It suggests working revoke commands for
regular records/symlinks, and a fixed manual instruction for FIFO/directory/unowned
entries. When --workspace was given, revoke suggestions retain it, shell quoted.
Revoke unlinks a symlink via dir_fd without opening its target; other non-regular
entries require manual cleanup. List/revoke read existing config for config_bound
without opening or initializing a workspace/Store. Noninteractive auth/permit
commands refuse before opening anything; scripted human permit approval needs no
TTY. Auth values and resolved secret-target paths never appear; explicitly supplied
workspace paths remain in the requested operator command. Revoked bindings ask to
import and rebind.

The complete classifier and closed exemptions, decoded-layer rule, scoped form-plus
decoding, one-call fixpoint/instability contract, cookie contexts, placeholders,
new key suffixes/syntaxes and explicit out-of-scope formats are specified in
[SPEC FR-019](../design/02_SPEC.md) and [SAFETY_API](SAFETY_API.md). Every finding
except local_path refuses new Muse text. Accepted topic/question bytes remain exact;
credentials never enter a new approval preview. Unstable nonrefusable details use
one complete mask. This is OFFLINE/FIXTURE/INTEGRATION/MEASURE evidence, not LIVE.
