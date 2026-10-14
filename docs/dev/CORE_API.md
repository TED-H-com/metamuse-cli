# MetaMuse core API (0.1.0.dev0, contract 1.1, schema 1)

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
[CONTENT_API.md](CONTENT_API.md). Other feature functions currently raise
`E_UNSUPPORTED` and have empty operation/tool declarations.

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

All fifteen SDS functions exist. Core implements `inspect_capabilities`, `create_project`,
`get_job`, `list_jobs`, `control_job`; B07/B11 implements `search_evidence`, `prepare_packet`,
`save_draft`, `preview_draft`, `export_objects`. The other five validate their typed
requests and return `E_UNSUPPORTED` / `not implemented in this build`. `TOOL_FUNCTIONS` maps the
fifteen contract names to their functions; MCP should use it rather than shelling out.

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
error/log/event text.
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
keys, recursive_triggers ON, WAL and synchronous FULL. Read-only schema probes inspect journal mode and never
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
Item/draft revisions are append-only on UPDATE; deletion remains possible for future
explicitly approved privacy operations. Transcript scope/immutability is pending B05/B06's
migration 002; shared bytes currently require that fix before project-isolated transcripts
are exposed. The transcript model/query branches are unchanged in this core review fix.
Appending an item revision marks referring packets/drafts stale in the same transaction.
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
`content.cursor` before any search. `get_workspace_key(name, version=None)` reads the
active or exact retained version; `workspace_verification_keys(name)` reads all versions.
`get_or_create_workspace_key(name, conn=None)` is for initialization/mutation boundaries;
read-only tools must use the read methods. `rotate_workspace_key(name)` atomically retires
the active version and creates the next; retired material remains verifiable and immutable.
WorkspaceKey hides material from repr, but callers must never serialize it to envelopes,
logs, events or exports. SQLite backups contain these keys and are private sensitive data.
Pre-release 001 amendments apply to fresh development workspaces. There is no schema-1
key shim or legacy content.cursor_key receipt import: a development DB missing the key
table fails open with guidance to create a fresh workspace. No released compatibility
is implied. Rotation still retains keys already provisioned in workspace_keys.

`put_object(binary_stream, mime=..., max_bytes=..., duration_ms=None)` measures/hash-streams
actual bytes into staging, fsyncs, atomically renames to the SHA-256 path, then commits a
ready Asset. Duplicate bytes reuse the same Asset ID; add separate ItemAsset relations
for separate observations/sources. Internal hash-derived paths never use titles. Missing
or corrupt existing objects fail. MIME/codec validation is the media owner's job before
ready insertion. A crash may leave orphan staging/object files; no unsafe automatic
orphan deletion is implemented. Ready files are not deleted by job failure.

`verify_export(Export)` checks the ready directory, exact manifest bytes, every payload
member's size/hash, and rejects symlinks/unexpected files. Export replay uses this same
verifier. `backup(fresh_destination)` uses SQLite online backup and copies exactly the
ready object and ready export snapshots. Backup manifest.json lists object hashes/sizes
and export IDs/artifact paths/manifest hashes. Each export carries its payload manifest;
uncommitted/orphan directories are excluded. Copying is followed by export verification.
`Store.restore(backup, fresh_paths)` checks schema version, SQLite integrity/FKs, manifest
membership, object size/hash/path and complete export directories before copying into a
fresh workspace with private modes. Export receipts can replay their original artifacts;
missing/corrupt artifacts fail honestly, and failed backups remove only their new target.
It clears stale worker owner metadata. Newer-version backups are refused; older backups
are first verified at their recorded version, then the restored copy follows normal
forward migration with its own pre-upgrade backup. Store's default schema version is
resolved at open time. Runtime `restore.incomplete` is created before copying and removed
only after successful migration/integrity checks; an error/crash leaves the target blocked
from normal Store/AppContext open. Restore the original verified backup into another fresh
workspace after resolving a failure; do not remove the marker to pretend a partial restore
succeeded. The original backup and rolled-back target/pre-upgrade backup are retained.
Migration uses an exclusive maintenance lock and the worker lock, pre-upgrade online
backup, and transactional DDL; a failure rolls back without deleting original data.
Migration disables FK enforcement before BEGIN IMMEDIATE for documented table rebuilds,
checks foreign_key_check before commit, and restores FK enforcement in finally. `PRAGMA
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
| workspace_keys | `name`, `version`, `key_material` (private BLOB), `created_at`, `status` (active/retired) |


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
Dependencies are uv-locked; no MCP SDK, yt-dlp, Playwright or crypto package is present.

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

Every Store connection enables recursive_triggers so conflict-resolution deletes fire
retention triggers. BEFORE INSERT guards reject REPLACE/INSERT OR REPLACE/UPSERT conflicts
on these identities; use explicit guarded UPDATE for mutable state, not conflict replacement.

| Guarded table | Protected identity / behavior |
|---|---|
| sources | id and reliable source identity; acquisition methods immutable |
| item_revisions | (item_id, revision); append-only UPDATE |
| packets | id; exact payload immutable, stale mutable |
| permits | id; immutable scope, monotonic use, sticky revocation, expiry checks |
| exchanges | id, discussion/request key, open-exchange slot; settled outcome sticky |
| drafts | id; current_revision requires appended head and never decreases |
| draft_revisions | (draft_id, revision); sequential append and immutable UPDATE |
| job_steps | id and job/sequence; entered external steps cannot reset/requeue |
| workspace_keys | name/version and active slot; immutable material, retained retired keys |
| exports | id; ready/unavailable payload immutable, ready→unavailable allowed once |

Exports cannot regain ready state from unavailable; receipt replay checks the authoritative
row against the stored receipt, then verifies that row's artifact. Corrections use new IDs.
Transcript guards/scoping remain B05/B06-owned and are not amended here.

Storage triggers freeze packet payloads (stale remains mutable), acquisition methods,
permit scope and settled exchange outcomes. Permit used is monotonic, revocation sticky,
and an increment rejects revoked, expired or unparseable-expiry rows; max_uses is bounded.
Expiry uses the Store clock via metamuse_now(), so consumption must use a Store connection.
B08 owns verification and atomic consumption on the verifier's supplied connection; core
adds no competing permit API. New scope/expiry/use budgets require a new human grant.
Ready object reads/inserts verify SHA-256; put_object can repair a corrupt occupant only
with newly measured/hash-verified bytes and rejects a conflicting MIME classification.
Failed backups remove only their newly created partial destination. Schema remains 1;
001 is amended for fresh development workspaces, not an automatic upgrade of existing
schema-1 development databases. Core still owns all migrations and generated schemas.
