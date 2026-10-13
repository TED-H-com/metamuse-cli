# MetaMuse core API (0.1.0.dev0, contract 1.1, schema 1)

B02–B04 owns `models.py`, `store.py`, `jobs.py`, migration SQL and generated schemas.
Other tasks must build against these APIs. Request a core change from the owner with
what/why/proposed diff; do not edit those files. Feature modules, application wrappers,
CLI wiring and their tests can be implemented by their assigned owners. No automatic
module discovery, plugin framework or second orchestration loop exists.

## Layout and initialization

`src/metamuse/` is the one import package. `app.py` is the shared application layer;
`cli.py` parses/renders, `jobs.py` executes, `store.py` persists, `models.py` defines
contracts, `paths.py` owns private paths, and `schemas.py` regenerates contracts.
`collect.py`, `media.py`, `muse.py`, `content.py`, `safety.py`, `mcp.py` are reserved
modules for later tasks and currently contain no implementation.

```python
from metamuse.app import AppContext, create_project

ctx = AppContext.open()  # platformdirs user data / metamuse / default
result = create_project(ctx, {"name": "Synthetic project", "request_key": "project-1"})
project_id = result.data["id"]
```

`AppContext.open(path)` binds an explicit workspace path. `WorkspacePaths.resolve`
rejects placement inside a MetaMuse Git checkout (a home-directory dotfiles checkout
is not a MetaMuse workspace). Tests must use the `pytest-disktmp` wrapper's `tmp_path`.
`ctx.paths` exposes `root`, `database`, `objects`, `runtime`, `worker_lock` and
`object_path(sha256)`. The layout is SDS §2, with `metamuse.toml` and
`runtime/maintenance.lock` added. Created directories are 0700 and files 0600 on POSIX.
No Windows executor support is claimed: this build uses `fcntl` on Linux/WSL2.

`ctx.store` is the only database owner. `ctx.clock` and `ctx.store.clock` are callables
returning UTC ISO timestamps; `AppContext.open(clock=...)` sets both. `ctx.logger` is
a Python logger. `ctx.limits` is `models.Limits`; `ctx.policy` is the complete typed
`Config` (`app`, `worker`, `limits`, `muse`, `publishing`). Offline mode, disabled
telemetry, disabled Muse/publishing and one executor are defaults. The TOML is one
workspace configuration, not separate CLI/MCP settings. Higher limits never grant scope.

## Application functions and errors

All fifteen SDS functions exist. Implemented: `inspect_capabilities`, `create_project`,
`get_job`, `list_jobs`, `control_job`. The other ten validate their typed requests and
return `E_UNSUPPORTED` / `not implemented in this build`. `TOOL_FUNCTIONS` maps the
fifteen contract names to their functions; MCP should use it rather than shelling out.

Every wrapper accepts its model or a Python dictionary. `@application(RequestModel)`
validates extra fields and returns clean envelopes for expected input, store and I/O
errors. Domain functions do not print. Feature owners can replace their stub wrapper
with a call into their assigned feature module and update `TOOL_FUNCTIONS` if binding
changes. Update capability reporting only with actual implementation and test evidence;
`IMPLEMENTED` currently records the five tested offline application functions.

```python
from metamuse.app import application
from metamuse.models import CollectRequest, CoreError, Envelope, ErrorCode


@application(CollectRequest)
def feature_submit_collect(ctx, request: CollectRequest) -> Envelope:
    # Feature owner must validate scope/path/limits before calling submit_job.
    raise CoreError(ErrorCode.UNSUPPORTED, "not implemented in this build")
```

`CoreError(ErrorCode, message, next_action=None, retry_after=0)` carries a typed
`ErrorDetail`. `failure(error)` returns an error envelope. `retry_after` is internal
scheduler metadata, never an extra error field. The public error includes code,
message, retry_class and optional nullable next_action. Never include credentials,
raw transport exceptions or unrestricted source content in error/log/event text.
`Envelope` enforces error/null-data consistency; JSON schemas enforce the same condition.
`canonical_json(value)` returns UTF-8 bytes with sorted keys, compact separators, no
string normalization, and rejects nonfinite numbers/non-string object keys.
`payload_digest(value)` is the SHA-256 of those bytes. Models are dumped in JSON mode.

Exit mapping is centralized in `ERROR_POLICY`/`exit_code`: 0 success/accepted, 2 input
or references/key conflict, 3 permission/auth/human action, 4 unsupported/dependency/
protocol/selector drift, 5 partial/timeout/busy/limits, 6 unknown external state, 7
storage/internal, and CLI interrupt 130. JSON output is one stdout envelope; warnings
and logs use stderr. Registered CLI feature stubs do not read input files or use the network.

`create_project` returns the Project directly in `data`. `get_job` returns
`data={"job": Job, "steps": [JobStep, ...]}`. `control_job` returns job_id/state/
cancel_requested/remote_state. Mutations use durable `request_receipts` for identical
request replay and reject a changed payload. Successful original receipts are replayed,
so current state should be read with `get_job` after a replayed control call.

`list_jobs` returns `data={"jobs": [...]}` and `next_cursor`. `JobListRequest` (MCP)
requires project_id; `JobListQuery` also supports `project_id=None` for CLI's workspace
view. Default limit 50, maximum 200. The cursor binds the project and a creation-row
high-water mark and paginates in ascending creation order; later inserts are excluded.
Statuses are read live. Cursors are opaque pagination markers, not permissions or signed
tokens. Physical job deletion/reordering is not an exposed core API; feature owners must
coordinate any change that would affect this ordering. CLI `jobs list` supports optional
`--project`, `--cursor`, `--limit`.

## Register and submit jobs

Registration is an explicit import/startup responsibility. Call a feature module's
registration function before submitting work and before entering `Executor`. Operations
are process-local registrations; jobs/steps/checkpoints are persisted. No fake operation
is registered in the shipped package. Tests use isolated registry dictionaries.

```python
from metamuse.jobs import register_operation, StepContext, StepSpec, submit_job
from metamuse.models import JobResult, CollectRequest


def handle_import(step: StepContext) -> JobResult:
    offset = step.checkpoint.get("offset", 0)
    # Read a bounded batch outside a SQLite transaction.
    batch = []  # feature parser supplies these

    def save_batch(conn):
        for record in batch:
            step.store.insert(record, conn=conn)

    step.commit_checkpoint({"offset": offset + len(batch)}, write_batch=save_batch)
    return JobResult(
        coverage_status="bounded",
        requested_scope={"max_items": 500},
        observed_scope={"items": len(batch)},
        stop_reason="input_exhausted",
    )


# Illustrative only: the core does not register or implement this operation.
register_operation("import.local", handle_import, "none", request_model=CollectRequest)
# validated_request is the feature owner's validated request model.
# submit_job(ctx.store, "import.local", validated_request.request_key,
#            validated_request.model_dump(mode="json"), project_id=project_id,
#            steps=[StepSpec(target={"source_id": source_id})],
#            requested_scope={"max_items": 500}, limits={"wall_clock_seconds": 900})
```

`register_operation(name, handler, side_effect_class, *, request_model=None,
uncertain_state="needs_reconcile", registry=None)` rejects duplicate names. Side effect
classes: `none` for local work; `safe_read` for external reads; `external_submit` for a
single remote submission. Use `open_uncertain` for Muse create, `send_uncertain` for Muse
send, and `needs_reconcile` for publication/other writes. This state is persisted in the
step, so a missing registry on restart cannot erase the uncertainty classification.
If an operation needs both submission and reads, use separate registered jobs or keep
read recovery in the feature's domain records. The current registry assigns one class
to every step of an operation; it does not support heterogeneous side effects in one job.

`submit_job` validates the optional registered request model, computes a canonical digest
of payload/project/step targets/requested scope/limits, then atomically checks the key,
creates job+steps+event and commits. Unique `(operation, request_key)` serializes competing
submissions. Same identity returns the same job_id and persisted state/result; changed
identity returns E_IDEMPOTENCY_CONFLICT. Scope and exact targets belong in that identity.
The envelope is `accepted` for nonterminal jobs, `ok` for terminal jobs except `partial`.
A failed persisted job remains a successful lookup/replay with `state=failed`; inspect
its `job.error`. `worker_available` is true only with a held OS lock and executor owner
metadata, and is a point-in-time observation. Accepted never means running.

## Handler boundaries, fencing and recovery

```python
from metamuse.jobs import Executor

# Context manager owns the shared workspace OS lock and increments worker_epoch.
with Executor(ctx.store) as executor:
    executor.run(once=True)  # one step; run_once() returns whether a step was processed
```

CLI `worker run`, future foreground `--run` and MCP-attached execution must all use this
Executor. Heartbeats never authorize takeover. The OS lock remains owned even during a
hung handler. A second executor gets E_JOB_BUSY. Do not unlink/recreate worker.lock,
kill unknown processes or invent a separate worker loop.

Handlers receive `StepContext` with `job`, `step`, `payload`, `target`, `checkpoint`,
`store`, `executor`. Call `guard()` at safe boundaries. It checks owner nonce/epoch,
job/step running state, cancellation, target identity and job wall-clock budget.
`commit_checkpoint(checkpoint, write_batch=None)` performs these checks and commits a
local batch and its checkpoint together. The callback receives the already-open
connection; do not open a nested transaction or do external waits inside it. These
checks fence stale callbacks from committing through this API. Direct raw writes do
not gain fencing automatically.

Before *every* external action, call the helper with the persisted target and a verifier:

```python
def submit_remote(step):
    def verify(conn, target):
        # Safety/permit owner validates actual current project/provider/account,
        # expiry/revocation/use count, exact payload digest and destination here.
        # Database-only checks; raise CoreError on any mismatch.
        validate_permission_and_target(conn, target)  # supplied by the feature owner

    step.before_external_action(step.target, verify=verify)
    # Intent has committed. No transaction is held here.
    receipt = remote_send_once(step.payload)  # supplied by the feature owner
    # Save receipt/domain state and checkpoint together at the next safe boundary.
```

The verifier is required; the core provides no permissive default and does not mint
permits. For `external_submit`, the helper commits `submitting` before returning.
A second call to submit on that step is refused. Exceptions, cancellation or a process
crash after this boundary leave uncertainty rather than scheduling another send.
The feature must persist its discussion/exchange intent and reserve permit use *before*
this call; later receipts/history belong in its domain tables. Epoch is local fencing,
not remote exactly-once. A tiny gap between local verification and external action
cannot be removed by a local SQLite transaction.

On restart, queued steps are claimed normally. Running local/read steps become queued
with checkpoints retained; successful/failed steps are retained. A submitted external
step becomes its persisted uncertain state and the job awaits the user. Pending local
cancellation becomes cancelled; submitted cancellation preserves the uncertain step
and remote_state=unknown. `control_job(resume)` refuses any submitting/uncertain step.
Reconciliation is a feature-specific read/check followed by a guarded transaction:
prove the result and mark that original step settled. Do not simply relabel uncertainty
as queued, create a new key or refund a used permit to bypass unknown delivery.

Safe reads retry only errors classified `safe_read` (network timeout/rate limit), at
most two times, respecting `CoreError.retry_after` outside the transaction. Access
refusal/auth/checkpoint never retry automatically. Awaiting-user jobs stay paused until
explicit control. Cancellation sets `cancel_requested` first and is honoured at the
next guard/checkpoint/result boundary. It cannot interrupt arbitrary blocking handler
code or prove remote cancellation. Handlers must supply bounded I/O timeouts. Wall
clock starts at the first claim, spans steps/restarts, and is checked at boundaries;
it does not kill an uncooperative remote call. Results always retain coverage_status,
requested/observed scope, stop_reason and missing_fields; multi-step coverage uses the
least confident status and preserves per-step results.

## Store and objects

```python
from metamuse.models import Source

with ctx.store.transaction() as conn:
    ctx.store.insert(
        Source(
            id="src_fixture",
            project_id=project_id,
            platform="generic",
            collection_method="file_import",
            fetched_at=ctx.clock(),
        ),
        conn=conn,
    )
source = ctx.store.get(Source, "src_fixture")
```

`connection()` gives a short read connection. `transaction()` uses BEGIN IMMEDIATE,
commit/rollback and bounded 5-second busy timeout. All writable connections use foreign
keys, WAL and synchronous FULL. Read-only schema probes inspect journal mode and never
change a newer DB. Both contexts close their connection and hold a shared maintenance
lock. Pass `conn=conn` when already inside a transaction. Always use parameters for
values and known table/column names for SQL. Do not retain a connection across download,
browser, model, user or subprocess waits.

`insert(record, conn=None)` supports the typed records listed below. JSON fields are
canonical encoded; boolean fields use SQLite 0/1. `get(Model, id, revision=None)` decodes
a typed record; ItemRevision/DraftRevision/TranscriptSegment support exact revisions
and otherwise return the highest revision. Ready Assets are checked for file presence
and measured size on read. `decode(Model, row)` is available for bounded custom queries.
`ItemAsset` has a compound key: query through `connection()` and decode rather than `get`.

Composite FKs enforce source/parent/discussion/packet/permit/job project consistency.
Parent triggers reject self/cycles. `insert(Packet/Export/DraftRevision)` checks evidence
and asset visibility; Packet rejects draft_revision as original evidence. Raw SQL
bypasses these JSON reference checks, so use insert/validate_evidence for selections.
`validate_evidence(project, refs, conn=..., source_only=False)` and
`require_project_asset(project, asset, conn=...)` are available to feature owners.
Shared asset bytes alone do not grant cross-project visibility. Revisions are append-only
on UPDATE; deletion remains possible for future explicitly approved privacy operations.
Appending an item revision marks referring packets/drafts stale in the same transaction.
Features must enforce expected_revision and semantic/source hash correctness themselves.
The core does not infer native identities, resolve missing parents, parse content or
validate packet semantic claims.

`put_object(binary_stream, mime=..., max_bytes=..., duration_ms=None)` measures/hash-streams
actual bytes into staging, fsyncs, atomically renames to the SHA-256 path, then commits a
ready Asset. Duplicate bytes reuse the same Asset ID; add separate ItemAsset relations
for separate observations/sources. Internal hash-derived paths never use titles. Missing
or corrupt existing objects fail. MIME/codec validation is the media owner's job before
ready insertion. A crash may leave orphan staging/object files; no unsafe automatic
orphan deletion is implemented. Ready files are not deleted by job failure.

`backup(fresh_destination)` uses SQLite online backup and copies exactly the ready object
snapshot with hashes/byte counts in manifest.json. `Store.restore(backup, fresh_paths)`
checks schema version, SQLite integrity/FKs, manifest membership, object size/hash/path
before copying into a fresh workspace. It clears stale worker owner metadata. Migration
uses an exclusive maintenance lock and the worker lock, pre-upgrade online backup, and
transactional DDL; a failure rolls back without deleting original data. `PRAGMA
user_version` is schema version 1; older builds refuse newer schemas on every connection.
Add future migrations only through the core owner. No LIVE, privacy erasure/compaction,
Windows ACL verification or performance SLA evidence is implied by these APIs.

## Tables and columns

The migration SQL is authoritative; the following inventory is generated for this handover.
All JSON column values are text; timestamp values are UTC ISO strings.

| Table | Columns |
|---|---|
| projects | `id`, `name`, `revision`, `created_at` |
| jobs | `id`, `project_id`, `operation`, `request_key`, `payload_digest`, `payload`, `state`, `coverage_status`, `requested_scope`, `observed_scope`, `stop_reason`, `missing_fields`, `limits`, `worker_epoch`, `cancel_requested`, `remote_state`, `result`, `error`, `created_at`, `updated_at`, `started_at` |
| job_steps | `id`, `job_id`, `sequence`, `target`, `checkpoint`, `state`, `attempts`, `retries`, `side_effect_class`, `uncertain_state`, `worker_epoch`, `result`, `error` |
| sources | `id`, `project_id`, `platform`, `native_scope`, `native_id`, `original_url`, `canonical_url`, `collection_method`, `upstream_collection_method`, `assurance`, `fetched_at`, `use_basis` |
| items | `id`, `project_id`, `source_id`, `kind`, `native_id`, `parent_id`, `unresolved_parent`, `identity_confidence` |
| item_revisions | `item_id`, `revision`, `text`, `text_hash`, `raw_time`, `parsed_time`, `source_time`, `fetched_at`, `complete`, `missing_fields`, `evidence` |
| assets | `id`, `sha256`, `mime`, `actual_bytes`, `duration_ms`, `object_path`, `state` |
| item_assets | `item_id`, `revision`, `asset_id`, `relation` |
| transcript_segments | `id`, `asset_id`, `revision`, `start_ms`, `end_ms`, `text`, `language`, `method`, `review` |
| packets | `id`, `project_id`, `selection`, `rendered_text`, `redactions`, `coverage_status`, `digest`, `created_at`, `stale` |
| permits | `id`, `project_id`, `kind`, `scope`, `provider`, `account`, `issuer`, `issued_at`, `expires_at`, `max_uses`, `used`, `revoked`, `exact_digest` |
| discussions | `id`, `project_id`, `provider`, `auth_ref`, `topic`, `remote_session`, `open_attempt_state`, `last_seq`, `state` |
| exchanges | `id`, `project_id`, `discussion_id`, `job_id`, `request_key`, `question`, `packet_id`, `digest`, `permit_id`, `baseline`, `state`, `outcome` |
| messages | `id`, `discussion_id`, `exchange_id`, `remote_session`, `remote_message_id`, `seq`, `role`, `raw_status`, `text`, `association`, `observed_at` |
| drafts | `id`, `project_id`, `current_revision`, `stale` |
| draft_revisions | `draft_id`, `revision`, `text`, `platform`, `variant`, `facts_refs`, `assets`, `preferences`, `digest` |
| exports | `id`, `project_id`, `selection`, `format`, `manifest`, `artifact`, `state` |
| events | `id`, `object_id`, `job_id`, `project_id`, `kind`, `detail`, `created_at` |
| worker_state | `singleton`, `owner_nonce`, `worker_epoch`, `heartbeat` |
| request_receipts | `operation`, `request_key`, `payload_digest`, `response` |


`jobs(operation,request_key)`, `job_steps(job_id,sequence)`, `assets(sha256)` are unique.
Revision primary keys are `(item_id,revision)` / `(draft_id,revision)`; transcript versions
use `(id,revision)`. Partial indexes enforce reliable source/item/message identity and
one exchange with null outcome per discussion. Permit use counters cannot exceed max_uses.
Events must contain only necessary non-secret metadata; the helper does not redact
arbitrary strings automatically. Do not put raw private content into event details.

## Schemas and verification

Run from the repository root:

```sh
uv sync --python 3.12
uv run python -m metamuse.schemas
pytest-disktmp uv run pytest -q
uv run ruff check .
```

The generator writes all fifteen input schemas, the envelope and domain schemas under
`src/metamuse/data/schemas/`, plus the design envelope and every tool's input/output
schema. Design descriptions/annotations remain in mcp-tools.json. Drift tests check
both directories, reject forged approved fields, and validate the design examples.
All output schemas currently validate the shared envelope; successful data structures
are documented above and domain records are typed. Future feature owners must request
specific typed output additions from core rather than hand-editing generated JSON.
Dependencies are uv-locked; no MCP SDK, yt-dlp, Playwright or crypto package is present.
