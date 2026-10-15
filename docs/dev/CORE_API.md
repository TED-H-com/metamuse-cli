# MetaMuse core API (0.1.0.dev0, contract 1.1, schema 6)

B02–B04 owns `app.py`, `cli.py`, `models.py`, `store.py`, `jobs.py`, migration SQL and
generated schemas. Other tasks must build against these APIs without editing them.
Request a core change from the owner with what/why/proposed diff. No automatic module
discovery, plugin framework or second orchestration loop exists.

## Layout and initialization

`src/metamuse/` is the one import package. `app.py` is the shared application layer;
`cli.py` parses/renders, `jobs.py` executes, `store.py` persists, `models.py` defines
contracts, `paths.py` owns private paths, and `schemas.py` regenerates contracts.
`collect.py`, `media.py`, `muse.py`, `content.py`, `safety.py`, `mcp.py` contain the
typed feature seams. B07/B11 implements the five content functions; see
[CONTENT_API.md](CONTENT_API.md). `collect.py` implements local `submit_collect` for
files and pasted text and declares `metamuse_collect`. URL inputs that request only
media metadata, subtitles, or a media file go to `remote_media.py` (`collect.remote_media`).
Posts, comments, and mixed URL inputs stay `E_UNSUPPORTED` until B13. `media.py` provides
local media and subtitle helpers and registers no operation. `safety.py` owns the local
input reader and the outbound URL policy that remote media calls.
Record formats are in `docs/dev/COLLECT.md`. `muse.py` implements the B10 lifecycle;
see [MUSE.md](MUSE.md).

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

Before opening SQLite, Store refuses linked or nonregular database/sidecar files.
SQLite's last connection may unlink a WAL/SHM sidecar during `lstat`, which can
return a regular inode with zero links instead of raising `FileNotFoundError`.
Only for those live sidecars, Store rechecks the current pathname once with the
strict file checks: absence is allowed, while a symlink, hardlink or nonregular
replacement is refused. The database and archive checks remain strict; a second
race during revalidation can still refuse the open. This is a file check, with
no transaction, job or external-action replay.

`ctx.store` is the only database owner. `ctx.clock` and `ctx.store.clock` are callables
returning UTC ISO timestamps; `AppContext.open(clock=...)` sets both. `ctx.logger` is
a Python logger. `ctx.limits` is `models.Limits`; `ctx.policy` is the complete typed
`Config` (`app`, `worker`, `limits`, `muse`, `publishing`, `remote_media`). `app.import_roots` lists
directories a local import may read; an empty list refuses every local file. Offline
mode, disabled telemetry, disabled Muse/publishing and one executor are defaults. The
TOML is one workspace configuration, not separate CLI/MCP settings. Higher limits
never grant scope.

## Application functions and errors

All fifteen SDS functions exist. Core implements `inspect_capabilities`, `create_project`,
`get_job`, `list_jobs`, `control_job`; B07/B11 implements `search_evidence`, `prepare_packet`,
`save_draft`, `preview_draft`, `export_objects`. Local `submit_collect` accepts files and
pasted text. URL inputs for media metadata, subtitles, or a media file are B12
`collect.remote_media` and require an allowlist plus a `collect.remote` permit.
Posts, comments, and mixed URL inputs stay `E_UNSUPPORTED`; `next_action` names
B12/B13 and LIVE. The four Muse functions use the B10 durable lifecycle and explicit
fixture/live binding. `TOOL_FUNCTIONS` maps the fifteen
contract names to their functions; MCP should use it rather than shelling out.

Every wrapper accepts its model or a Python dictionary. `@application(RequestModel)`
validates extra fields and returns clean envelopes for expected input, store and I/O
errors. Validation, wrappers and `TOOL_FUNCTIONS` stay in `app.py`; the ten feature
wrappers delegate to functions of the same names in their assigned modules. Implement
the feature function without adding a second decorator. Domain functions do not print.
Request new application functions and typed result models from core. Core's five
implemented tools are separate from each feature's implemented-tool declaration.

```python
from metamuse.app import AppContext
from metamuse.models import CollectRequest, CoreError, Envelope, ErrorCode


def submit_collect(ctx: AppContext, request: CollectRequest) -> Envelope:
    # Feature owner must validate scope/path/limits before calling submit_job.
    raise CoreError(ErrorCode.UNSUPPORTED, "not implemented in this build")
```

`CoreError(ErrorCode, message, next_action=None, retry_after=0)` carries a typed
`ErrorDetail`. `failure(error)` returns an error envelope. `retry_after` is internal
scheduler metadata, never an extra error field. The public error includes code,
message, retry_class and optional nullable next_action (populated from
`DEFAULT_NEXT_ACTIONS[code]` whenever `ErrorDetail`/`CoreError`/`failure`/`validation_error`
is constructed without an explicit `next_action`, keeping CLI and MCP envelopes identical).
Never include credentials, raw transport exceptions or unrestricted source content in
error/log/event text. Unexpected CLI failures and the `mcp.call_tool` OSError and
unexpected-exception branches log through `safe_log.log_safe_exception`: the operation,
error code, request id or job id when it is already a public id, exception class names,
and `file:line:function` frames. That log does not include exception text, arguments,
locals, or source lines, and it does not use `exc_info`.
`Envelope` enforces error/null-data consistency; JSON schemas enforce the same condition.
`canonical_json(value)` returns UTF-8 bytes with sorted keys, compact separators, no
string normalization, and rejects nonfinite numbers/non-string object keys.
`payload_digest(value)` is the SHA-256 of those bytes. Models are dumped in JSON mode.

Exit mapping is centralized in `ERROR_POLICY`/`exit_code`: 0 success/accepted, 2 input
or references/key conflict, 3 permission/auth/human action, 4 unsupported/dependency/
protocol/selector drift, 5 partial/timeout/busy/limits, 6 unknown external state, 7
storage/internal, and CLI interrupt 130. JSON output is one stdout envelope; warnings
and logs use stderr. CLI reads text/question/selection/refs files as strict UTF-8, counting
actual bytes before delegation. It preserves CRLF and Unicode composition. Question files
use `question_max_utf8_bytes`; text, selections, refs, facts and permit scope files use
`text_max_bytes`. JSON files reject duplicate keys and nonfinite numbers and are then
validated by the request model. Local import/media and auth files are passed as paths;
their feature owners enforce path/content policy. No stub or content function performs
a network request.

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

Registration is an explicit import/startup responsibility. Call
`app.register_builtin_operations(registry=None)` before submitting work and before
entering `Executor`. It imports the fixed tuple `collect`, `media`, `content`, `safety`,
`muse` and calls each optional `register_operations(registry)` hook. Hooks must be
idempotent per registry because submission and execution may register in the same process.
Operations are process-local registrations; jobs/steps/checkpoints are persisted. No fake
operation is registered in the shipped package. Tests use isolated registry dictionaries.

```python
from metamuse.jobs import OPERATIONS, register_operation, StepContext, StepSpec, submit_job
from metamuse.models import JobResult, CollectRequest


def handle_import(step: StepContext) -> JobResult:
    offset = step.checkpoint.get("offset", 0)
    # Read a bounded batch outside a SQLite transaction.
    batch = []  # feature parser supplies these

    def save_batch(conn):
        for record in batch:
            conn.insert(record)

    step.commit_checkpoint({"offset": offset + len(batch)}, write_batch=save_batch)
    return JobResult(
        coverage_status="bounded",
        requested_scope={"max_items": 500},
        observed_scope={"items": len(batch)},
        stop_reason="input_exhausted",
    )


def register_operations(registry=None):
    target = OPERATIONS if registry is None else registry
    if "import.local" not in target:
        register_operation(
            "import.local", handle_import, "none", request_model=CollectRequest, registry=target
        )


# Illustrative only: the core does not register or implement this operation.
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
The envelope is `accepted` for queued/running jobs, `ok` for terminal jobs except `partial`.
Awaiting-user replay returns the stored error with the job ID in next_action; reconcile
errors exit 6 and callers retrieve the body with job_get.
A failed persisted job remains a successful lookup/replay with `state=failed`; inspect
its `job.error`. `worker_available` is true only with a held OS lock and executor owner
metadata, and is a point-in-time observation. Accepted never means running.

## Handler boundaries, fencing and recovery

```python
from metamuse.app import register_builtin_operations
from metamuse.jobs import Executor

register_builtin_operations()
# Context manager owns the shared workspace OS lock and increments worker_epoch.
with Executor(ctx.store) as executor:
    executor.run(once=True)  # one step; run_once() returns whether a step was processed
```

CLI `worker run` and `add --run` call registration before using this Executor. The future
MCP-attached worker must call `register_builtin_operations()` before entering it too;
`cli mcp` registers before delegating to `mcp.run(ctx)`. That function owns protocol stdout
and returns `None` when serving ends, so CLI does not append an envelope. `add --run`
submits first and runs the shared FIFO executor until the requested job is terminal or
awaiting user action; earlier queued work can run first. It stops without draining later
jobs. If the lock is held, it returns the accepted envelope with a stderr warning; it
does not start another executor. Failed/user-action jobs return their persisted error;
partial jobs retain coverage and exit 5. Heartbeats never authorize takeover. The OS lock
remains owned even during a
hung handler. A second executor gets E_JOB_BUSY. Do not unlink/recreate worker.lock,
kill unknown processes or invent a separate worker loop.

Handlers receive `StepContext` with `job`, `step`, `payload`, `target`, `checkpoint`,
`store`, `executor`. Call `guard()` at safe boundaries. It checks owner nonce/epoch,
job/step running state, cancellation, target identity and job wall-clock budget.
`commit_checkpoint(checkpoint, write_batch=None)` performs these checks and commits a
local batch and its checkpoint together. The callback receives a TransactionWriter bound to the
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
The feature must persist its discussion/exchange intent before this call. B08's standard
verifier reserves the permit use inside this call's transaction, together with the
submission intent; do not reserve it a second time beforehand. Later receipts/history
belong in domain tables. Epoch is local fencing,
not remote exactly-once. A tiny gap between local verification and external action
cannot be removed by a local SQLite transaction.

On restart, queued steps are claimed normally. Running local/read steps become queued
with checkpoints retained; successful/failed steps are retained. Any entered external
step becomes its persisted uncertain state and the job awaits the user. Pending local
cancellation becomes cancelled; submitted cancellation preserves the uncertain step
and remote_state=unknown. `control_job(resume)` refuses submitting/uncertain steps and
entered external steps that did not succeed. An entered external step is never requeued.
Reconciliation is a feature-specific read/check followed by a guarded transaction:
prove the result and mark that original step settled. Do not simply relabel uncertainty
as queued, create a new key or refund a used permit to bypass unknown delivery.

Safe reads retry only errors classified `safe_read` (network timeout/rate limit), at
most `limits.safe_read_retries` times (0–2), respecting a capped `CoreError.retry_after`
outside the transaction and checking cancellation every 250 ms. Access
refusal/auth/checkpoint never retry automatically. Awaiting-user jobs stay paused until
explicit control. Cancellation sets `cancel_requested` first and is honoured at the
next guard/checkpoint/result boundary. It cannot interrupt arbitrary blocking handler
code or prove remote cancellation. Handlers must supply bounded I/O timeouts. Wall
clock starts at the first claim, spans steps/restarts, and is checked at boundaries;
it does not kill an uncooperative remote call. Results always retain coverage_status,
requested/observed scope, stop_reason and missing_fields; multi-step coverage uses the
least confident status and preserves per-step results.

SQLite contention is retried only while no step is claimed: epoch/recovery startup,
heartbeat, atomic claim/cancel sweep, idle checks, and heartbeat/cancellation during
an already requeued safe-read delay. `Executor.run_once()` uses the same maintenance
retry path as `run()`. Only internal `_StorageBusy` errors qualify. Each failed
transaction rolls back before a jittered exponential wait (initial range 25–50 ms,
doubling to a 1–2 second range). Each backoff wait is at most 2 seconds and wakes on
`request_stop()`; no further attempt begins after stop. Every unclaimed action uses a scoped thread-local 1-second connection/busy timeout,
including the read-only version probe. `unavailable_operations()` owns this retry
path even when called after `run()`; after stop it returns `False` without a query. The default remains 30 seconds elsewhere.
The loaded 548-transaction sample put 531 (96.9%) within one second; the 12.16 ms
median fits easily, while tail stalls are retried instead of lengthening each attempt.
Exit metadata cleanup also uses one second, skips only busy errors, and releases the
OS lock before propagating any other cleanup failure.

With no step claimed, SQLite contention delays stop by at most one 1-second attempt
plus one 1-second cleanup (2 seconds). Backoff wakes immediately. These are lock-wait
bounds, not deadlines on fsync, filesystem/OS maintenance locks or arbitrary handlers.
A claimed handler reaches its boundary first. After it returns, finalization can wait
30 seconds and its fallback another 30; if the fallback times out, durable state is
left for recovery. Zero-handoff processing can additionally wait 30 in settlement
and 30 in its failure-event write: at most four successive waits (120 seconds), plus
the 1-second exit cleanup. The pre-handler guard and each handler checkpoint/boundary
transaction independently retain 30 seconds. Handler I/O has its own bounds. MCP
shutdown joins the worker thread so it cannot abandon a claimed step or its OS lock.

`storage_busy_retries` is a cumulative, saturating counter (maximum 2**31−1).
The existing attached-worker status/inspect/doctor data exposes it together with
`attached_worker` and nullable `worker_error_code`. Startup contention remains
`starting` and unavailable until ownership metadata commits; an established worker
retains its OS lock while retrying. Non-busy failure reports `failed` and only a fixed
error code, never exception text or paths. E_STORAGE also covers unexpected internal
worker failures, following the existing unexpected-tool-failure convention. Both
CoreError and unexpected failures call log_safe_exception with operation
mcp.attached_worker and the fixed code; logs contain only class names and sanitized
basename/line/function frames. Startup failure leaves the server serving, and later
job acceptance stays queued with worker_available false. A stopped startup goes
directly from starting to stopped, without reporting running before ownership.
The MCP server keeps serving diagnostics even if its attached worker fails at startup.
The start handoff waits at most 2 seconds for ready, then serves with `starting` if
ownership has not committed. Ready is also signaled on the first short busy attempt,
lock-busy or failure. Availability stays false until committed ownership.
The integration journey explicitly polls inspect before its initial flow and restart
flow, using the remaining time in its existing 600-second shared test deadline.
Only `starting` with `worker_available:false` is transitional; it still requires
`running` with `worker_available:true` before proceeding. Failed, disabled,
lock-busy, stopped or inconsistent states fail immediately with the full inspect
state; deadline diagnostics retain the last full state. This wait is opt-in at
those journey prerequisites, not part of the generic MCP server context: tests of
disabled workers, lock ownership and queued acceptance continue to observe those
states directly. It does not change production readiness or promise background work.
Doctor uses these fields when an attached-worker status provider is present; a separate
CLI process observes current OS-lock availability, not another process's failure memory.

Once a step is claimed, busy never wraps or replays the handler. `none`/`safe_read`
retain their failure policies (E_STORAGE has public retry_class `never`); an external
step before the committed submitting boundary can fail as not_submitted, while one
past that boundary becomes uncertain. A busy failure while saving a result/error
rolls back the transaction. If the fallback failure/uncertainty write is also busy,
the error propagates and the durable running/submitting state remains for existing
restart recovery; it cannot become a fabricated success or failed_before_send.
Entered external work is never automatically resent. On shutdown a busy owner-metadata
cleanup may leave stale metadata; OS-lock release remains authoritative for availability.
For this process, availability also requires the complete owner nonce in the live
registry, added only after ownership commits and removed even if exit cleanup fails.
A later Executor in the same PID cannot make an old row look available. Other PIDs
continue to require matching Linux FLOCK evidence.

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
commit/rollback and a bounded 30-second busy timeout. `sqlite3.connect(timeout=30)`
and `PRAGMA busy_timeout=30000` share one internal constant. A 90-second sample under
suite load measured 548 transactions: p50 12.16 ms, p99 1781.87 ms, max 6903.15 ms;
commit-to-context-exit max was 6901.59 ms. Thirty seconds gives margin above that
observed stall; it is not a host-wide deadline or a guarantee against longer stalls.
A CLI caller waits synchronously, without progress output, for each contended SQLite
operation; exhaustion returns a sanitized E_STORAGE (exit 7). The 30-second limit is
per contended write transaction, not per CLI command: commands can perform several
transactions in sequence. Existing-workspace doctor, jobs get/list and search open
without taking the write lock. Foreground workers
instead keep retrying unclaimed maintenance until stopped. WAL readers do not take
the write lock, although one Store instance still serializes its short connection
lifetimes with its process-local RLock.

Store classifies SQLITE_BUSY and SQLITE_LOCKED by `sqlite_errorcode`'s primary byte,
retaining the full extended code on internal `_StorageBusy(CoreError)`. The same
classification applies to schema verification and migration BEGIN/commit failures. Both it and
other SQLite errors expose only E_STORAGE/never publicly; corruption, I/O errors,
and missing codes do not qualify for busy retry. No SQL, paths or values enter error
messages. Existing transaction constraint errors retain E_REFERENCE_INVALID.
All writable connections use foreign keys, recursive_triggers ON, WAL and synchronous
FULL. Writable opens issue journal_mode=WAL: SQLite treats an already-WAL database as a
no-op without taking the write lock, including with a held writer. A real WAL
transition can return SQLITE_BUSY and uses the same classification. Read-only
schema probes inspect journal mode and never
change a newer DB. Both contexts close their connection and hold a shared maintenance
lock. Pass `conn=conn` when already inside a transaction. Always use parameters for
values and known table/column names for SQL. Do not retain a connection across download,
browser, model, user or subprocess waits.

`insert(record, conn=None)` supports the typed records listed below. JSON fields are
canonical encoded; boolean fields use SQLite 0/1. `get(Model, id, revision=None)` decodes
a typed record; ItemRevision/DraftRevision/TranscriptSegment support exact revisions
and otherwise return the highest revision. Ready Assets are checked for file presence
and measured size/hash on read. `decode(Model, row)` is available for bounded custom queries.
`ItemAsset` has a compound key: query through `connection()` and decode rather than `get`.

Composite FKs enforce source/parent/discussion/packet/permit/job project consistency.
Parent triggers reject self/cycles. `insert(Packet/Export/DraftRevision)` checks evidence
and asset visibility; Packet rejects draft_revision as original evidence. Raw SQL
bypasses these JSON reference checks, so use insert/validate_evidence for selections.
`validate_evidence(project, refs, conn=..., source_only=False)` and
`require_project_asset(project, asset, conn=...)` are available to feature owners.
Item, draft and transcript revisions are append-only on UPDATE; deletion remains possible
for future explicitly approved privacy operations. A transcript's `project_id` owns the
derived text, including when `asset_id` is null. Evidence reads load that stored project
and the requested revision. They do not treat the caller as the owner. An optional asset
must already be visible in the owning project through `item_assets` or `source_assets`.
Shared asset bytes do not share transcript text. Appending an item or transcript revision
marks referring packets and drafts stale in the same transaction and does not rewrite
packet selection or rendered text.
Use `append_draft_revision(record, expected_revision=..., conn=...)` for atomic draft CAS;
revision insertion advances current_revision. Features enforce semantic/source hash correctness.
The core does not infer native identities, resolve missing parents, parse content or
validate packet semantic claims.

Content adds a nonempty SearchRequest.query and typed DraftPreferences on both input
and stored DraftRevision: language/emoji are nonempty bounded strings (35/32 code
points), format plain_text/markdown, scope this_draft. The content-owner/core-owner task
explicitly authorizes these additive contracts; generated schemas come from models.py.
See [CONTENT_API.md](CONTENT_API.md) for content resolution budgets and replay behavior.
`workspace_keys` holds internal 32-byte signing material. Store initialization provisions
`content.cursor` before any search. Existing workspaces check for the active key with
a read connection and open no provisioning transaction when it exists. First opens
recheck under the write transaction, so racing opens create exactly one active key. `get_workspace_key(name, version=None)` reads the
active or exact retained version; `workspace_verification_keys(name)` reads all versions.
`get_or_create_workspace_key(name, conn=None)` is for initialization/mutation boundaries;
read-only tools must use the read methods. `rotate_workspace_key(name)` atomically retires
the active version and creates the next; retired material remains verifiable and immutable.
WorkspaceKey hides material from repr, but callers must never serialize it to envelopes,
logs, events or exports. SQLite backups contain these keys and are private sensitive data.
Pre-release 001 amendments apply to fresh development workspaces. There is no schema-1
key shim or legacy content.cursor_key receipt import: a development DB missing the key
table fails open with guidance to create a fresh workspace. A same-version DB missing
001's required storage triggers also fails open rather than claiming the current guards.
No released compatibility
is implied. Rotation still retains keys already provisioned in workspace_keys.

`put_object(binary_stream, mime=..., max_bytes=..., duration_ms=None)` measures/hash-streams
actual bytes into staging, fsyncs, atomically renames to the SHA-256 path, then commits a
ready Asset. Duplicate bytes reuse the same Asset ID; add separate ItemAsset relations
for separate observations/sources. Internal hash-derived paths never use titles. Missing
or corrupt existing objects fail. MIME/codec validation is the media owner's job before
ready insertion. A crash may leave orphan staging/object files; no unsafe automatic
orphan deletion is implemented. Ready files are not deleted by job failure.

`verify_export(Export, conn=None)` checks the ready directory, exact manifest bytes, every
payload member's size/hash, and rejects symlinks, hardlinks and unexpected files. Bundle
verification also resolves immutable Muse citations from SQLite and checks manifest,
JSONL and Markdown attribution, including exact `muse_evidence`, backend and boolean
mock/research_complete labels. Consistent hashes cannot substitute for those labels.
Pass the active transaction connection before commit. Receipt replay compares the
authoritative row to its receipt and verifies that row, not a receipt copy.

`backup(fresh_destination)` returns BackupResult(path, status, unavailable_export_ids,
warnings); status is complete/partial. `Store.restore(backup, fresh_paths)` returns
RestoreResult with those same fields plus `.store`. Use `.path` to obtain the archive
path and `.store` to use the restored database. Structured results retain partial status
even when Python suppresses repeated warnings. BackupWarning is a secondary signal at
the public caller; no global warning filters are installed.

```python
archived = ctx.store.backup(destination)
if archived.status == "partial":
    report_unavailable(archived.unavailable_export_ids, archived.warnings)
restored = Store.restore(archived.path, fresh_paths)
store = restored.store
```

Destinations must be outside and disjoint from the live workspace; only automatic
pre-upgrade backups use workspace/backups. SQLite archives use rollback-journal mode.
The manifest requires DB hash/size, ready objects, export IDs/artifact paths/manifest hashes
and export state. Every copied artifact is verified; unavailable exports are recorded
explicitly without blocking upgrades. Authoritative object loss still fails. DB, manifest,
member files and directory entries are fsynced before returning. Uncommitted exports
are excluded. Pre-upgrade results are durably recorded in runtime/pre_upgrade_backups.json
(last 100 records), exposed by doctor/inspect as pre_upgrade_backups, and partial upgrade
warnings are included in application and CLI envelopes created by that opening context.

Archive inspection is immutable/read-only and rejects sidecars/unknown entries. Every
verified file must be regular, unsymlinked and have st_nlink=1; unlisted members are refused.
Raw SQL inventories at the backup version avoid current Pydantic models. The entire
sqlite_master set of tables/indexes/triggers/views, with whitespace-normalized SQL, must
match MIGRATIONS[1..version] replayed in memory. SQLite-internal objects alone are ignored.
Check the old schema before the pre-upgrade snapshot, the target schema before migration
commit, the archive before any writable copy connection, the copied DB before its first
write, and every normal Store open. Expected schemas are process-cached by exact migration
contents (16 entries); actual DB schemas are always read and compared. Rebuild migrations
must recreate prior guards; dropping a guard while retaining its table is refused.
Newer backups and legacy manifests missing the database checksum are refused. Older
copies use guarded forward migrations with their own pre-upgrade backup. Verified old
artifacts replay byte-for-byte; restored unavailable exports stay unavailable.

A symlink at the raw leaf workspace/backup/destination path, including a dangling link,
is refused before resolution. Symlinked parent directories remain supported. Live SQLite
DB/WAL/SHM files are checked with lstat for private regular files; private_file uses
O_NOFOLLOW. These checks protect application mistakes, not a hostile process controlling
the same user and changing paths during a system call.

Runtime restore.incomplete is written and fsynced before initialize, and removed with a
subsequent directory fsync inside the maintenance lock, only after successful
migration/integrity checks. Connection/migrate recheck the marker after acquiring that
lock. Normal open,
connection, migrate and put_object reject even broken-symlink markers. Copied worker_state
is cleared before the pre-upgrade backup. A failure leaves the target blocked; restore
again into another fresh destination after resolving the failure. Do not remove a marker
to pretend a partial target succeeded. Original backups and rolled-back targets remain.
Migration holds the maintenance/worker locks, disables foreign_keys before BEGIN IMMEDIATE
for table rebuilds, checks foreign_key_check before commit, and restores enforcement in
finally. DDL failure rolls back without deleting original data. `PRAGMA
user_version` is schema version 3; older builds refuse newer schemas on every connection.
Add future migrations only through the core owner.
Schema 2 adds project-scoped transcript anchors and source_assets. Migration 002 copies
a v1 transcript only when its asset links to exactly one project; ambiguity aborts safely.
No LIVE, privacy erasure/compaction, Windows ACL verification or performance SLA
evidence is implied by these APIs.

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
| source_assets | `source_id`, `project_id`, `asset_id`, `relation` |
| transcript_segments | `id`, `project_id`, `asset_id`, `item_id`, `source_id`, `revision`, `cue_index`, `start_ms`, `end_ms`, `text`, `language`, `method`, `review`, `overlap` |
| packets | `id`, `project_id`, `selection`, `rendered_text`, `redactions`, `coverage_status`, `digest`, `created_at`, `stale` |
| permits | `id`, `project_id`, `kind`, `scope`, `provider`, `account`, `issuer`, `issued_at`, `expires_at`, `max_uses`, `used`, `revoked`, `exact_digest` |
| discussions | `id`, `project_id`, `provider`, `auth_ref`, `topic`, `remote_session`, `open_attempt_state`, `last_seq`, `state`, plus immutable binding/backend and original open attempt/proof/quarantine fields listed in [MUSE.md](MUSE.md) |
| exchanges | `id`, `project_id`, `discussion_id`, `job_id`, `request_key`, `question`, `packet_id`, `digest`, `permit_id`, `baseline`, `state`, `outcome`, plus prepared request, original step, attempt accounting, delivery proof and typed observation memory listed in [MUSE.md](MUSE.md) |
| messages | `id`, `discussion_id`, `exchange_id`, `remote_session`, `remote_message_id`, `seq`, `role`, `raw_status`, `text`, `association`, `observed_at`, `backend`, `identity_key` |
| muse_observations | `id`, `discussion_id`, `message_id`, `identity_key`, `raw_event`, `observation_digest`, `observed_at`, `backend` (append-only) |
| drafts | `id`, `project_id`, `current_revision`, `stale` |
| draft_revisions | `draft_id`, `revision`, `text`, `platform`, `variant`, `facts_refs`, `assets`, `preferences`, `digest` |
| exports | `id`, `project_id`, `selection`, `format`, `manifest`, `artifact`, `state` |
| events | `id`, `object_id`, `job_id`, `project_id`, `kind`, `detail`, `created_at` |
| worker_state | `singleton`, `owner_nonce`, `worker_epoch`, `heartbeat` |
| request_receipts | `operation`, `request_key`, `payload_digest`, `response` |
| workspace_keys | `name`, `version`, `key_material` (private BLOB), `created_at`, `status` (active/retired) |


`item_revisions.text` is nullable. SQL NULL is the stored form of a JSON null and is the
source of truth for absent text. `missing_fields` containing `"text"` mirrors that NULL.
Readers render a NULL revision as missing text, never as an empty string. An empty string
without that marker is present empty text. The two cases use different `text_hash` values:
the digest of `{"text": null}` and the digest of `{"text": ""}`.

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

The generator writes all fifteen input schemas, the envelope and domain schemas, and a
packaged copy of `mcp-tools.json` under `src/metamuse/data/schemas/`, plus the design
envelope and every tool's input/output schema in `docs/design/contracts/`. Design
descriptions/annotations are authored in `docs/design/contracts/mcp-tools.json` and
mirrored to package data. Drift tests check both directories, reject forged approved
fields, and validate the design examples.
All output schemas currently validate the shared envelope; successful data structures
are documented above and domain records are typed. Future feature owners must request
specific typed output additions from core rather than hand-editing generated JSON.
Dependencies are uv-locked. MCP uses its SDK; Muse imports its curl/Noise dependencies
lazily. See `THIRD_PARTY_NOTICES.md` for their licenses and native components. B10 adds
no dependency. `yt-dlp==2026.8.19` is the optional `remote` extra and a dev-group pin,
imported lazily by `remote_media.py`. Playwright is not a dependency.

## Feature seam and core change requests

| Feature owner | Files / application functions |
|---|---|
| B05/B06 | `collect.py`: `submit_collect`; `media.py`: media helpers and operations |
| B07/B11 | `content.py`: `search_evidence`, `prepare_packet`, `save_draft`, `preview_draft`, `export_objects` |
| B08 | `safety.py`: safety helpers, `import_auth`, `create_permit`, external-action verifier |
| B09/B10 | `muse.py`: `open_muse_discussion`, `submit_muse_exchange`, `read_muse_exchange`, `close_muse_exchange` |
| B14 | `mcp.py`: `run(ctx)`, tools through `app.TOOL_FUNCTIONS`, attached worker through the shared Executor |

Owners edit their modules, supporting feature files and tests. They do not edit `app.py`,
`cli.py`, `models.py`, `store.py`, `jobs.py`, migrations or generated schemas. All SPEC §8
commands already map flags to typed requests and delegate. `auth import` and `permit create`
are human-only local commands with `AuthImportRequest`/`PermitCreateRequest`; neither is in
the fifteen MCP tool registry. `--provider`/`--account` label an auth import; permit creation
supports `--scope-file` (JSON object), those labels, `--issuer`, `--expires-at`, `--max-uses`
and `--exact-digest`. These fields express local intent; only safety validates and issues
permission. Model output and arbitrary `approved` fields cannot create grants.

B08's implemented safety APIs and exact scope formats are documented in
[SAFETY_API.md](SAFETY_API.md). This adds human-only `permit revoke`/`permit list`,
private-file auth references, a per-request URL hook and the external-action verifier.
No safety auth/permit function is added to the MCP tool registry. Feature owners must
integrate the per-request network hook and the database-only verifier; a seed check or
caller-supplied approval/digest alone does not authorize an external action.

Each feature declares its own capabilities and registers its own operation names:

```python
IMPLEMENTED_TOOLS: frozenset[str] = frozenset({"metamuse_search"})


def capability_status(ctx):
    # Optional offline metadata. Report only actual evidence/configuration/permission.
    return {
        "metamuse_search": {
            "verification": "fixture_passed",
            "environment": "ready",
            "permission": "granted_scope",  # local workspace read; no remote grant implied
        }
    }
```

`inspect_capabilities` aggregates declarations without a shared editable set. Undeclared
tools remain absent; declarations alone default to unverified/unavailable/not_configured.
The optional status hook can set only verification/environment/permission for tools
declared by that module. `Capability` computes effective status from all four axes.
An optional backend or page type reports its own row named `<declared tool>.<backend>`
(`[a-z][a-z0-9_]{0,63}`, all three fields required), for example
`metamuse_collect.browser_post`. Backend rows follow the fifteen tool rows in name order.
A missing browser, extra or login therefore never marks the tool's local function
unavailable (SPEC §2: missing Muse or browser does not block local features).
Collect reports `metamuse_collect.remote_media` for the optional yt-dlp extra. Its
environment is `missing_dependency` without the pinned import and `ready` when
`yt-dlp==2026.8.19` imports. Permission is `not_configured` while the workspace
is offline or `remote_media.approvals` is empty, and `granted_scope` when the
workspace is live and an approval is configured. Each collection still needs
its own `collect.remote` permit at submit. The capability row is not that permit.
An approval may set optional `auth_ref` (`auth_` and 32 hex digits). That field
is configuration only. `CollectRequest` has no `auth_ref`.
Hooks never make network calls. Use the idempotent `register_operations` example above;
operation names must be unique and preserve side-effect/recovery semantics on restart.

For a needed result model, input field or database column, add a **Core change requests**
section to the feature report: required behavior, model/table/field, proposed diff, default
and compatibility rules, and a small failing use case. Core owns schema regeneration,
new versioned migrations, backups and upgrade tests. Do not change migration 001 or place
duplicate columns/contracts in a separate feature database while waiting for that change.

## Review guardrails and reconciliation

`config.py` owns the same typed workspace policy (re-exported Config/defaults in app).
Executor reads configured limits at startup; offline mode is checked before entering
safe_read/external_submit handlers and again at every external-action helper. Local
`none` handlers must not perform network actions. Live mode does not grant permission.
App request validation applies ctx.limits to UTF-8 text/question fields; questions have
an absolute 8192-byte ceiling. Packet insertion applies the configured packet byte cap.
`utf8_text(text, max_bytes)` measures without normalization; JSON schemas expose byte
limits as metadata because standard JSON Schema length counts characters. Validation
errors contain bounded field paths, never inputs, exception context or submitted values.
Registered job payloads are revalidated at claim. Stored records use structural validation
without current input byte caps, so lowering limits does not hide immutable history.

CLI passes `--non-interactive` to AppContext and Executor; human-only auth/permit commands
are refused. Features that need a prompt must call `ctx.require_interactive()` or
`step.require_interactive()` before prompting. MCP must create its attached Executor with
`non_interactive=True` and must not offer human-only commands. JSON help writes usage to
stderr and one help envelope to stdout. Missing operation handlers leave jobs queued;
worker exits partial when only unavailable work remains. Availability uses Linux's
read-only `/proc/locks` FLOCK records plus owner metadata; inability to observe returns
false. F_GETLK/F_OFD_GETLK do not observe Linux flock and cannot replace this check.

`commit_checkpoint` passes TransactionWriter to write_batch. Use `writer.insert`,
`writer.get`, `writer.append_draft_revision` or parameterized `writer.execute` for static
SELECT/INSERT/UPDATE/DELETE/WITH statements. There is no commit/rollback/transaction API;
nested same-workspace Store transactions fail immediately. The guard is rechecked after
the callback, so attempted epoch/state changes roll back the entire batch.

B10 can settle uncertainty through the executor already held by its history-read job:

```python
# Fetch remote history outside a transaction first. The feature supplies evidence checks.
def verify_proof(conn, target):
    verify_original_session_and_definite_receipt(conn, target)  # feature-owned, database-only


settled = step.executor.settle_uncertain(
    original_step_id,
    expected_target=original_target,
    verify=verify_proof,
    result=JobResult(coverage_status="bounded", data={"receipt_id": receipt_id}),
    write_batch=lambda writer: writer.insert(observed_message),
)
```

Exactly one of result/error is required; error must prove a definite refusal, not a
retryable timeout or remaining uncertainty. Epoch/owner, uncertain state and original
target are checked in the same transaction as proof/domain writes and completion.
Settlement retains local cancellation and records remote confirmation/refusal separately.
It never sends or requeues the original entered step, including after a proven refusal.
Keep one side-effect class per operation: Muse create/send are external_submit jobs;
history reads/reconciliation are separate safe_read jobs. Any explicitly approved new
submission after a proven refusal requires feature-level target/intent/permission checks;
a new key cannot bypass unresolved delivery.

SQL guards are defense in depth against application bugs. They are not a sandbox
against a same-user actor issuing arbitrary SQL, changing guard definitions or editing
the DB file. Job/exchange state machines remain enforced by jobs.py and the application;
SQL freezes identities/bindings and selected monotonic facts, not every state transition.

Application and migration SQL must never use INSERT OR ..., UPDATE OR ..., OR REPLACE,
REPLACE INTO, OR IGNORE or ON CONFLICT, and must never write rowid/_rowid_/oid. The static
scan in tests/test_sql_policy.py enforces this across every source SQL string and migration.
Exceptions require an explicit, commented allowlist of exact source locations; none exist.
Read-only SELECT rowid for ordering is allowed. Use SELECT/checks and request_receipts in
a short transaction for idempotency/deduplication, then ordinary INSERT or guarded UPDATE.
INSERT OR IGNORE and ON CONFLICT DO NOTHING also abort on guarded identity conflicts;
they are not supported no-op ingestion mechanisms.

Every Store connection enables recursive_triggers. BEFORE INSERT guards reject identity
conflicts including explicit NEW.rowid; experimentally automatic NEW.rowid is -1 before
assignment. BEFORE UPDATE guards freeze rowid and primary/unique identities. Partial
unique slots with legitimate lifecycle changes (active key retirement, exchange closure)
use their existing guarded APIs rather than immutable status values.

| Guarded table | Identity and immutable binding columns (rowid frozen on all) |
|---|---|
| sources | id, project_id, platform, native_scope/native_id; acquisition methods immutable |
| item_revisions | item_id/revision; all payload updates forbidden |
| packets | id, project_id; exact payload immutable, stale mutable |
| permits | id, project_id; grant scope immutable, monotonic use and sticky revocation |
| exchanges | id, project_id, discussion_id, request_key, question, packet_id, digest, permit_id; closed outcome sticky |
| drafts | id, project_id; head requires appended revision and cannot decrease |
| draft_revisions | draft_id/revision; sequential append, payload updates forbidden |
| job_steps | id, job_id, sequence, target, side_effect_class, uncertain_state; entered attempt cannot reset/requeue |
| workspace_keys | name/version, key_material, created_at; retired material retained, active slot rotated atomically |
| exports | id, project_id always; ready/unavailable payload frozen, ready→unavailable allowed once |
| transcript_segments | id/revision; whole row including project, anchors and rowid is append-only |
| source_assets | source_id/asset_id/relation; whole row including project and rowid is immutable |

Every table's no-replace guard includes primary, declared secondary unique and rowid
conflicts. The F2 regression fixture uses unreferenced victims and asserts exact trigger
messages; METAMUSE_TEST_DROP_GUARDS=1 is a test-only mutation switch proving all 38
replacement cases fail when their guards are removed. Exports cannot regain ready state
from unavailable. Receipt replay compares the authoritative row and verifies its artifact.
Schema 2 retains the four B05/B06 guard names: transcript_revision_immutable,
transcript_revision_no_replace, source_assets_immutable and source_assets_no_replace.
Their whole-row UPDATE guards cover rowid and all binding columns; the INSERT guards
cover the composite primary key and rowid. Neither table has a secondary UNIQUE index.
Canonical checks replay both migrations for fresh v2, upgrades and restores. A retained
table rebuild must preserve or recreate every prior guard name.

Storage triggers freeze packet payloads (stale remains mutable), acquisition methods,
permit scope and settled exchange outcomes. Permit used is monotonic, revocation sticky,
and an increment rejects revoked, expired or unparseable-expiry rows; max_uses is bounded.
Expiry uses the Store clock via metamuse_now(), so consumption must use a Store connection.
B08 owns verification and atomic consumption on the verifier's supplied connection; core
adds no competing permit API. New scope/expiry/use budgets require a new human grant.
Ready object reads/inserts verify SHA-256; put_object can repair a corrupt occupant only
with newly measured/hash-verified bytes and rejects a conflicting MIME classification.
Failed backups remove only their newly created partial destination. Schema is 5;
001 and 002 are amended pre-release. Canonical v1 workspaces upgrade through 002;
older development workspaces missing amendments in 001 must be recreated.
Core still owns all migrations and generated schemas.


## B10 core extension

Schema 3 adds the typed Muse lifecycle records and append-only `muse_observations` after
migration 002. [MUSE.md](MUSE.md) defines the complete column/guard and lifecycle contract.
`submit_job(..., prepare_domain=...)` receives a TransactionWriter, new Job and JobStep
list. It runs database-only domain intent creation inside the same submission transaction;
replay never calls it. Discussion/exchange, job, original step and receipt commit together.

B08 `external_action_verifier` returns its recomputed verified digest. For an external
submit, `before_external_action` returns an opaque issuer-registered ExternalActionToken
after commit. Consumers must pass it to the prepared transport request; it is bound to
owner/epoch/job/step/operation/target/digest and one-use. Safe reads return None. Entered
steps cannot mint or reserve again. Legacy trusted handlers that return no digest receive
no token and cannot call the Muse mutation transport.

`record_not_submitted(step, concrete_transport_result)` registers affirmative in-process
zero-handoff proof before writing accounting. `step.commit_not_submitted(proof,
write_batch=...)` atomically saves that accounting and checkpoint before honoring local
cancellation; owner/epoch/target/step guards still apply. `settle_uncertain(...,
not_submitted=proof)` settles local failure with remote_state=not_submitted, retaining
local cancellation, and never refunds or requeues. An optional database-only
`after_settlement` callback registered with the proof can clear the original quarantine
after the original step is failed; proof registration is consumed only after commit.
Accounting/settlement failures leave the step uncertain with a durable
`step.not_submitted_save_failed` event, and the worker continues other queued jobs.
Without that process-local proof a crash remains uncertain even if a later caller claims
False. Original delivery proof and host outcome stay separate. No direct boolean/token
constructor supplies authority. Executor accepts explicit fixture gateway, secret-store
and resolver DI; fixture network exemption applies only to registered Muse operations and
an explicitly selected fixture target/backend.


## AT-042 core extension

Migration 004 adds draft Muse evidence and packet no-reshare guards without new columns
or tables. `EvidenceRef.kind="muse_observation"` refers to the immutable observation ID
with revision=1. `validate_evidence` checks its discussion's project and rejects it with
source_only=True. Existing append-only, retention, rowid/no-replace and identity guards
are unchanged. App content functions resolve stored raw_event and backend, propagate
verified attribution transitively, and never copy model-provided authority.
Both new guards count `kind`, `id` and `revision` keys with `json_each(ref.value)` and
reject duplicates regardless of the first kind. The draft guard requires JSON type
integer and value 1. Every JSON decode in Store uses a recursive duplicate-key-rejecting
`object_pairs_hook`; stored records and receipts retain their fixed `E_STORAGE` errors,
without echoing values. Migration 004 is amended before release; no 005 is added.
See [CONTENT_API.md](CONTENT_API.md) and [OFFLINE_SLICE.md](OFFLINE_SLICE.md).

## Approval preview registration (B08/B10/B14)

```python
from metamuse import models as m, safety

# Call from the feature's idempotent register_operations, before submissions/workers.
safety.register_approval_preview("collect.remote", preview_remote, request_model=m.CollectRequest)
```

The callback `(ctx, validated_request) -> models.ApprovalPreview` reads configuration and
stored domain data and calls the SAME prepare code used by the submission/boundary.
It must create nothing: no grant, receipt, job, intent, temporary file, network call or
account discovery. `operation` identifies the request entrypoint; `permit_operation`
is the actual scope operation (Muse open reconciliation needs muse.read). The preview
includes kind/project/configured provider/account, action_digest, destination, a precise
summary and non-secret request/request_file/next_action. Shares additionally include
packet_id, packet_digest, packet_utf8_bytes and operator packet_preview_command.
Use `safety.operator_command(ctx, ...)` for shell-quoted commands; never interpolate
untrusted text as shell code. Redact credentials in summary/topic/question/request.
The exact digest binds original prepared bytes even when display text is redacted.

The shared application decorator adds a preview only on SCOPE_REQUIRED when preparation
succeeds, preserves its actual message (including "Permit does not exist") and returns
null data. SCOPE_DENIED retains its code/message without a preview. Grant denials tell
callers to repeat without a grant for a fresh preview; never reuse or widen the old one.
Callbacks must redact raw text fields separately before encoding request JSON and use
those same strings in the summary. Exact digests continue to bind the original bytes.
The displayed operator command must use an unused approval request key; repeating
an already submitted command replays its original permit, including consumed or
revoked state. Muse selects the next unused digest-based key by reading receipts;
displaying a fresh preview creates nothing.
Disabled backend, missing credentials, stale/invalid references and unsupported routes retain their
actual errors when preparation cannot produce a valid review. Callback failures create
no grant. `permit approve` validates through that registry and recomputes/displays the
preview. Only the human CLI confirms and calls existing create_permit; no MCP route is
added. Expiry defaults to 24h, explicit expiry remains capped at 30 days, every permit
is bound to exact_digest, and default-expiry receipt replay preserves the original expiry.

B12/B13 must implement one collect.remote preview dispatcher for the same CollectRequest,
route to their fixed backend preparation, derive provider/account from operator config,
and use the identical target/payload in PermitUse and external_action_verifier. Two
callbacks cannot register the same operation with different implementations: coordinate
that dispatcher with core. Do not accept account labels/digests/approved flags from model
text. Register previews before returning grant errors, enforce current permit and target
again at the step boundary, and retain B08 egress/retry rules. `collect.register_operations`
registers the sole dispatcher with `CollectRequest`; `_remote_route` selects the fixed
`_REMOTE_APPROVAL_ROUTES` table. Today `collect.remote_media` calls
`remote_media.preview_remote_media`, using `prepare_remote_media` at all three boundaries.
B13 adds its route to that selector/table rather than registering another callback.

B12's target binds the whole canonical configured approval table (extractor key,
normalized sorted hosts and opaque auth_ref or null), including all possible hop
entries. Any edit requires a fresh preview before consumption/egress. Preparation
checks binding metadata only; the step resolves/parses all bound secrets before use.
Exact request receipts, including the grant ID, replay before permit verification.
Workspace caps stay outside target and job identity, enter the approval digest payload,
and are checkpointed exactly as reviewed at acceptance. Execution applies min(snapshot, current). Request limits remain bound
and are checked against current workspace limits. Preparation bounds URL UTF-8 bytes
at 8192 and checks cheap syntax before redaction, display or digest. The resulting
ASCII canonical URL must also fit the same outbound bound.

B12 computes one ASCII-only canonical URL: lower-case scheme and
IDNA2003/punycode host, non-default port only, UTF-8 percent-encoded non-ASCII path/query.
Decode unreserved escapes, upper-case remaining escapes, and pre-encode ASCII outside
the pinned yt-dlp/curl_cffi safe-set intersection. Only m.facebook.com maps to www;
host approval checks www. Refuse IDNA deviation characters ß/ς and mapped trailing dots.
The digest, preview, request echo and operator stderr bind that string. The extractor
receives exactly the displayed URL. The transport sends exactly each URL the checker
approved, without requoting or dot-segment removal (quote=False, PATH_AS_IS and an explicit
REQUEST_TARGET, preserving escape hex case too). Extractors
may derive further requests, e.g. FacebookReel watch URLs, added query parameters or
API endpoints; each is checked against bound approval entries before any dial. Wire
tests prove that the first page request bytes equal the display for the fixture
extractor and FacebookIE watch, video and story URLs. The job request and original_url
provenance retain the original input. Refuse fragments (even empty #), shared url_token
query keys, invalid escapes, dot segments (including escapes and parameters), duplicate
canonical query keys and structural redaction, with fixed E_INPUT_INVALID, no preview
or URL echo. At preview and before extraction, assert the pinned-library fixed point:
display == requote_uri(normalize_url(display)); drift fails closed. Signed extractor-hop
URLs retain existing redaction; credential-bearing page URLs are outside B12 inputs. All four displayed workspace caps enter the action digest
payload; a pre-submission edit requires new approval. At execution, rebuild the digest
from the reviewed acceptance snapshot and enforce min(snapshot, current caps).

AppContext.worker_status is an optional process-owned callback installed by MCP startup.
Shared wrappers replace worker availability fields with attached-worker truth there;
CLI contexts use the existing Linux FLOCK observation. No feature sets this callback.

Each Store serializes connection contexts with its own threading RLock. The attached
worker constructs a sibling Store inside its thread, retaining the workspace paths,
clock, migration map and schema version, with independent connection serialization.
Its construction is an unclaimed retry too. MCP tools keep ctx.store: their WAL reads
do not queue behind worker SQLite writer waits. Writers contend in SQLite, as across
processes; the database, maintenance/worker locks and scheduler remain shared.
Builtin operation handlers use the step Store. The lazy fixture Muse gateway uses
that same worker Store. An explicitly injected fixture gateway retains its supplied
Store and mutable fixture state; its database access is read-only. Credentials use
the secret store, and the resolver does DNS; neither uses the workspace database.
Network waits never hold a connection lock or transaction.

### Human bounded research on the existing safety seam

`permit research` is a human-only alternative to exact `permit approve`, described in
[SAFETY_API.md](SAFETY_API.md) and [MUSE.md](MUSE.md). It issues existing access/share
Permits atomically via one Store.transaction/mutate_once; MCP tools and core schemas
are unchanged. Optional strict scope JSON carries discussion/open identity, fixed packet,
budgets and current binding. Queue-domain checks read the newly inserted discussion or
exchange through TransactionWriter.execute in the same transaction; no nested transaction
or core mutation API is added. At reservation the existing safety verifier rechecks the
actual persisted state on the supplied connection. Legacy PermitUse reservation fingerprints
retain their previous shape; research additionally binds trusted discussion context.
Exact-mode question equality remains required. Candidate evidence is synthetic only.
Research pair/manual creation performs privacy preflight before writer ownership and
passes only a process-owned immutable snapshot to its atomic raw-state recheck. The
manual safety seam is prepare_creation outside the writer, then validate_creation with
that internal handoff inside it. No serialized prechecked approval or redactor callback
is accepted by the transaction; no public model or Store API changes are introduced.

## B09 standard transport trust (schema 5)

Unreleased migration `005_muse_trust.sql` is amended in place again for round 3;
there is no 006. Public models/contracts do not change. The upstream is fixed by
`MUSE_ORIGIN`/`HATCH_WSS_BASE`, so one trust domain and one `muse-trust-domain.lock`
cover every provider/account label in the workspace. Those labels identify frozen
connection bindings, never separate security expectations.

`muse_trust_accounts` retains `(provider, account)` currentness: authenticated
viewer/session hashes, active selection, generation and an inherited sticky flag.
`muse_trust_vms` retains label-bound selection provenance. `muse_trust_observations`
is the label-independent record keyed by `(viewer_hash, normalized vm_id)`; an empty
VM id represents owner-only evidence. No cookies, auth_ref, tokens or raw keys are stored.
VM storage IDs are ASCII, trimmed and lowercase; the exact server ID on the wire may
differ only in case and surrounding whitespace.

Every observed owner↔VM association forms an edge. The recursive
`muse_trust_confidential` view computes the closure of confidential seeds across all
labels and observations. A confidential owner raises all observed VMs; a confidential
VM raises every observed owner, transitively. New rows inherit that effective flag;
standard never clears it. The 21 named SQL guards retain identities, refuse replacement,
upsert/rowid reuse, downgrade and zero rows beside a required identity, and propagate
account, VM and observation flags set-wise. Three explicit indexes support owner/VM
lookups. `muse_trust_propagating` is a transaction-local recursion barrier, always empty
at commit; Store boundaries refuse a committed barrier row. A component above 1000 rows
records without per-row trigger recursion or a confidentiality-recording identity cap.

`Store.muse_freeze(...)` commits every authenticated confidential hint for its observed
owner and all usable VM IDs **before** any owner binding/status/selection refusal.
The existing label cannot be rebound silently. A hint with no usable ID records its owner.
Standard freeze captures one session/selection generation and retains the association.
`muse_record_confidential(snapshot)` commits Noise-authenticated RV evidence for the
snapshot's remote owner/VM without checking its label or generation CAS. Even stale or
changed bindings cannot discard an observation. Unsupported/malformed RV state, fields
or chip binding still become permanent confidential expectations before refusal.
Write failure stops before msg3 and says confidentiality could not be recorded.

`muse_assert_current` computes the effective requirement in the same transaction as
currentness. Confidentiality is E_PROTOCOL_MISMATCH / never before generation checks.
A stale snapshot/binding is E_STORAGE / never with a fresh-connection retry action.
`muse_standard_handoff` checks the same state and commits provenance/CAS before empty
msg3. Trust-domain and per-account locks span handoff; maintenance.lock and the SQLite
transaction are released before network I/O. Every call rechecks durable state, including
cross-process writers. After commit, a new confidential account/VM/observation row or
any 0→1 flag transition (including inherited edges and trigger propagation) notifies
all live workspace listeners and immediately closes their sockets. A failing listener
does not stop the others; after all callbacks run, the writer reports
`Muse security state internal failure` chained from the first exception, with the
write already committed. A non-direct freeze counts actual confidential SQL writes, including trigger writes, independently of its
label's binding changes. A no-op freeze does not broadcast; selection-only changes
notify their own label. On its next call an unaffected, invalidated transport first
passes the durable recheck, then reconnects through the full bootstrap and handshake
before any application frame. Immediately after freeze returns, the transport clears
invalidation and registers `muse_watch`, before mint, wake or opening a socket. The
registry de-duplicates each bound callback per workspace/label key. Any subsequent
workspace-confidential or own-label notification that wins before application handoff
forces a fresh connect in the same call before its first application frame; the transport rechecks invalidation after
publishing the loop and the final durable assertion. At most three connects per call
are allowed. The transport tracks an owned candidate from connector return through
handshake to promotion or actual closure; notifications close it even during a blocked
msg2 read. A notification before candidate registration closes the returned link before
handshake use; connector-owned sockets cannot be cancelled before `connect()` returns. Callbacks
take no transport lock, and cleanup only releases its own attempt. Expected candidate
closure rechecks the old snapshot before fresh bootstrap; durable refusals and real
verification/storage failures stop with their original classification and no new requests.
Exhaustion returns E_NETWORK_TIMEOUT / safe_read, `Muse connection failed`,
with action `Retry the connection. No gateway request frame was sent.` A mutation
reports never-sent through `MuseNotSubmitted` or a chat.stream `failed_before_send`
attempt. An affected context stops before HTTPS or WSS. An
in-flight interruption retains normal read failure or mutation uncertainty handling;
no notification replays a mutation. The documented human closure repair runs after
stopping Muse connections, so it has no live listeners to notify.

`muse_application_handoff(snapshot, handoff)` uses the same trust-domain/account locks
to check the old snapshot and serialize the first application send against trust writes
and their notifications. Unlike `muse_standard_handoff`, it does not write msg3
provenance or repeat its CAS bookkeeping. Its check transaction commits and
`maintenance.lock` is released before the callback; both trust locks remain held until
the callback completes. The callback checks invalidation and calls `open_request`/send;
it must not recursively perform trust writes. Response collection runs after release.
Callback exceptions, including SendUncertain and protocol failures, are re-raised outside
the storage error wrapper with their original identity. StoreMuseTrust exposes this as
`application_handoff`.

If a notification wins before handoff, the old durable snapshot decides refusal before
any new bootstrap. A current, invalidated socket reconnects in the same call, sharing
the existing three-attempt budget with handshake recovery and consuming authorization
once. True pre-send refusal returns MuseNotSubmitted/session.start or
FAILED_BEFORE_SEND/chat.stream. If handoff wins the boundary first, a later notification
is an in-flight close and never replays the action; it cannot cancel already handed-off
bytes. Invalidation callbacks take no transport lock.
The curl pipe separates cancellation from handle cleanup: its connector thread caches an
owned duplicate of the active socket without changing native flags or timeout settings
(including nonblocking mode). Notifications shut that socket down without any
concurrent curl API call or wait for the active operation. Native send/receive/cleanup are
serialized; an active operation cleans up after unwinding, while idle cleanup may safely
transfer threads. Cleanup is claimed once and releases the duplicate without sending a
close frame. Descriptor reuse cannot redirect cancellation. Earlier accepted bytes keep
in-flight uncertainty and are never replayed; connector-owned sockets remain outside the
cancellation guarantee until connect returns.

Canonical schema verification is cached per Store trust boundary; every boundary checks
the actual schema signature for changed guards. Integrity/schema failures block with
E_STORAGE and advise repair or a verified restore. Fresh-v5, v4→v5 and backup/restore
checks cover the amended observations and guards. Older development-v5 schemas are
noncanonical and have no implicit upgrade. Rollback restores a verified v4 backup into
a fresh workspace. **Restoring an older backup, including v4 rollback, discards
confidential expectations recorded after that backup.** No in-place reset tool exists.

2026-10-14 round 4: an authenticated hint bind inserts its transaction-local barrier, bulk-inserts all observation and label VM rows, materializes the recursive closure once, updates the three tables set-wise, removes the barrier and commits. There is no per-id closure or recording cap. A separate small transaction checks the label after this independent evidence commit. Raw-SQL propagation and inherit guards remain active outside that batch.


Round 4 storage mapping: SQLITE_BUSY/LOCKED past busy_timeout returns E_STORAGE / safe_read, “Muse security state is busy; retry”. READONLY identifies a read-only workspace and FULL a full disk, both user_action. Recording failures retain “Muse confidentiality could not be recorded” with the same fixed environment guidance, and stop before msg3. Integrity/schema failures alone advise verified restore with the later-expectations loss warning.

A committed muse_trust_propagating marker is always invalid and has a specific E_STORAGE message. Safe human repair, after stopping Muse connections: acquire exclusive maintenance.lock, BEGIN IMMEDIATE, retain the barrier, rerun the recursive owner/VM closure and the three set-wise flag updates across the whole workspace, DELETE the marker, then COMMIT. This retains every observation and expectation; no restore is needed. There is no automatic repair/reset command.

HTTP 200 session: status=unavailable或完全沒有identity key回resource；alias-only vmid回protocol；其他非assigned status（含missing/type/case drift）回protocol/never。non-object response action標session/hatch/auth path。



Round 4 amends unreleased migration 005 again, with no 006: muse_trust_accounts now checks lower-case hexadecimal viewer/session hashes and normalized nonempty active VM ids, or NULL only with confidentiality set. The same constraints are verified on fresh v5, v4→v5 and confidential backup→restore. Noncanonical development v5 databases still fail schema verification.

Round 4 provenance design: source and observed_at are frozen once an observation or label VM row has confidential_required=1, including later direct evidence. A direct hint/RV may stamp a prior cr=0 observation during its transition to 1; a standard freeze never restamps a cr=1 row. Inherited propagation retains the association’s prior provenance, rather than claiming direct CVM evidence. Existing immutable guards enforce this metadata rule; no first-cause column is needed because nothing overwrites confidential provenance.


Session and selected-row keys whose compact form is `vmid` must all be valid and
normalize to one VM identity, independently of key order. Name, endpoint and the
persistent trust key use that identity. The exact server `vm_id` may be retained
on the wire only after that agreement check (case and surrounding whitespace
are the only permitted differences). A confidential hint records every usable
identity alias before any disagreement is refused, before mint, wake or dial.

Effective Muse trust and SQL inherit guards seed a recursive component at the
requested owner and VM. Every traversal uses indexed owner/VM edges, including
active selections, historical label VMs and label-independent observations.
All six AFTER propagation triggers and the bulk bind seed the same indexed
component recursion from the new remote owner/VM. Bulk bind materializes that
component once after inserting all observations, under the transaction barrier.
Small confidential writes and unrelated standard handoffs do not enumerate other
components. Only explicit human barrier repair computes the whole-workspace closure.
Raw-SQL inherit/retention/monotonic guards remain. Unreleased migration 005 is
amended in place again; no 006. Fresh v5, v4→v5 and backup/restore use this schema.

Muse trust failures classify SQLite primary codes (including extended codes):
IOERR/CANTOPEN/PERM/NOLFS/READONLY/FULL are environment/user_action;
BUSY/LOCKED/PROTOCOL/NOMEM/INTERRUPT are transient/safe_read;
CORRUPT/NOTADB/SCHEMA/CONSTRAINT and invalid stored types are damage/never.
Unclassified codes and Python exceptions return a fixed internal failure and
report action, without restore or permissions advice. Recording retains its
honest failure prefix and the underlying guidance. A committed barrier uses
closure-only human repair. Unreleased 005 enforces integer generation/flags,
text identities/hashes and nonempty text timestamps, also on upgrade and restore.

Hint matching walks every object/list to depth 32. A normalized component that
starts with cvm or contains confidential is a marker. Marker keys count only
with truthy values (excluding false/0/null/empty values and compact false/no/0/none).
String marker values count regardless of key, except opaque credential strings
and strings in lists directly under sensitive keys. Objects under sensitive keys
are still walked, including their keys and values under non-sensitive keys.
This retains hatch_cvm, hatchCvm, CVM_RUNNING, cvm1, confidential, negated
non-confidential/nonconfidential/not cvm/no-cvm, full-width, compatibility and
ignorable-separated forms. C V M, c_v_m, c-vm and hatchcvm/HATCHCVM no longer match.
It avoids cross-word Public VM/basic vm and opaque xQ7cVm2R false positives.
Evidence records the union of every enclosing identity-bearing object's usable
VM ids, including the session and selected row. If the innermost scope is the
session or no scope exists, persist owner evidence and all usable body ids.
Duplicate JSON pairs are retained for this scan, including every identity value;
record evidence first, then refuse any duplicate key at any depth. All Muse HTTPS
JSON bodies require strict UTF-8, reject leading U+FEFF, and pass the depth check
before parsing; UTF-16/32 and malformed UTF-8 record nothing. Alias-only vmid in
the session or selected row is protocol drift, before unavailable checks. Only
no identity key at all, or status=unavailable, remains resource unavailable.
Every observation commits before refusal even on owner mismatch or invalid selection.
False positives permanently refuse the connected standard tier, with no reset tool;
this intentionally errs toward confidentiality.

Owned Muse redaction scans key+separator occurrences once, inspecting a value
only for sensitive keys and resuming after it. Unquoted values stop at quotes;
JSON-text pairs embedded in plain text, including Unicode-escaped keys, are covered.
Quoted keys scan plain runs between escapes. Classification caches retain only keys
of at most 256 characters, so large authenticated keys cannot accumulate across connections. Authorization consumes a whole
run on failure and redacts scheme-less and scheme-plus-credential values. Every
codec-hidden value must also be hidden by text/body sanitization (seeded 5,000-case
compositions). Known raw and twice-decoded percent-coordinate spans are unioned,
merged, and rendered with one join; bodies are never truncated before redaction.
CPU doubling tests cover k:, k=>, a=1;, k:v|, ;p=1 and authorization:, raw and in
JSON string wrappers, 128 KiB–1 MiB, for owned scanning and public text/body paths.
The B16a integration carries the linear core scanner and converts the five
inherited core-growth xfails into ordinary tests, at the standard 128 KiB, 256 KiB,
512 KiB and 1 MiB sizes. Each size retains three CPU samples and two calls per
sample (the existing authorization family retains four calls). The slope limit
remains 1.25, the endpoint ratio limit remains 12, and the 1 MiB CPU ceiling
remains 12 s for the adversarial Muse grammars. The bare-cookie AT-042 regression
is also an ordinary passing test. Focused measurements do not certify the full
integrated application or replace exact-source hosted integration gates.

`approval_failure` treats a callback's `E_UNSUPPORTED` as an inapplicable preview
and preserves the original error. Other callback `CoreError` refusals propagate
their fixed code, message and next action, including credential, stale-packet and
scope refusals from an applicable preview. Unexpected exceptions preserve the
original error and log only `Approval preview callback failed (TYPE)`, without
exception text or a traceback. These refusal paths produce no preview or operation
side effect.


### Retiring a transcript cue (B12 / FR-006 / AT-045)

Migration `006_transcript_removal.sql` upgrades schema 5 to 6 after the Muse
trust migration `005_muse_trust.sql`, preserving its tables and guards.

TranscriptSegment.removed defaults to false. A producer retires an observed cue
by appending the same id/project at a later revision with removed=true, text="",
and its prior timing, language and method. Use the same transaction as replacement
cues and the new source observation. Store.insert refuses missing prior rows,
out-of-order/removal-at-1, text-bearing removals or double removals with
E_REFERENCE_INVALID; its existing revision>1 rule marks citing packets/drafts
stale in that transaction. Never UPDATE or REPLACE historical segments.
A reappearance appends a normal text-bearing row with the same ID.

Schema migration adds a checked default-0 removed column without rebuilding the
table or changing append-only triggers/indexes. Search/current listings exclude
latest removals. Evidence resolution, packet prepare and exports refuse a removal
revision with E_REFERENCE_INVALID and the last text-bearing revision number, with
no text; explicit older text-bearing refs still resolve stale=true. Raw Store.get
remains an internal revision reader so producers/audits can inspect removals.
A listing omission alone is not a removal (B18).
