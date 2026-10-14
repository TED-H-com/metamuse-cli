# B08 safety API

This is the implementation detail for SPEC §7–§9 and SDS §2/§7/§9.
`auth import`, `auth list`, `auth revoke`, `permit create`, `permit revoke` and
`permit list` are local operator commands. They are absent from the MCP registry. Source text, Muse answers and
`approved: true` fields are data; no parser extracts or widens permission from them.
A host with arbitrary same-UID shell access can change local state; this is the SDS §2
local misuse boundary, not a hostile-host sandbox.

## URL and outbound-request hook

```python
from metamuse.safety import OutboundURLPolicy

policy = OutboundURLPolicy(resolver=backend_resolve_all, allowed_hosts=("example.test",))
checked = policy.before_request(url, purpose="request")
# Connect to a checked address with the original hostname for TLS/SNI verification.
# Use checked.url, not the original, potentially differently parsed URL.
# Call before_request again for every redirect, subresource and media/CDN request.
```

`check_url(url, *, purpose, resolver=None, allowed_hosts=())` is pure with respect to
network I/O: it never performs DNS. A resolver is injected and returns IP strings for
**every** address the backend could use. Names without a resolver, empty answers,
invalid addresses, more than 64 addresses or a single denied address fail closed.
Purpose is `seed`, `request`, `redirect`, `subresource` or `media`. There is no special
permission attached to a purpose. Optional allowlists compare normalized exact hosts;
subdomains, paths and queries never confer permission or select credentials.

The normalized URL uses stdlib IDNA A-labels, lowercase scheme/hostname, canonical IP
literals and removes a single trailing hostname dot. Existing punycode is roundtripped;
malformed labels, ambiguous numeric hosts, backslashes, userinfo, controls (including
percent-encoded controls), zero/invalid ports and URLs over 8192 UTF-8 bytes are denied.
Production destinations use port 80 or 443 only; nonstandard ports (including SSH,
SMTP and Redis ports) are denied. The explicit fixture checker also allows nonstandard
ports when every resolved address is loopback, never for a public destination.
Legacy IPv4 decimal/hex/octal/shortened forms use the local stdlib `inet_aton` parser,
not DNS. IPv4-mapped IPv6 is checked using its IPv4 address. Compatible IPv6, NAT64,
Teredo and 6to4 are recognized but conservatively denied. Ordinary IPv6 must be public
unicast in `2000::/3`; IETF special-purpose and documentation blocks are denied.
IPv4 denies RFC1918, CGNAT, loopback, zero/unspecified, link-local/metadata, multicast,
benchmarking, documentation, protocol-assignment, deprecated relay and reserved ranges.

Range review used the primary [IANA IPv4 special-purpose registry](https://www.iana.org/assignments/iana-ipv4-special-registry/)
and [IANA IPv6 special-purpose registry](https://www.iana.org/assignments/iana-ipv6-special-registry/)
on 2026-10-13; explicit blocks include `3fff::/20` so policy does not depend solely on
Python's bundled registry version. Some special-purpose publicly reachable addresses
are intentionally denied. Registry changes require policy/test review.

`check_fixture_url` is the separately named synthetic-server entry point. It adds only
loopback, including injected `localhost` resolution; it does not allow LAN/metadata
addresses. Production `check_url`/`OutboundURLPolicy` has no fixture toggle.

**Backend obligations:** intercept every actual request; disable unguarded automatic
redirects, proxy environment/config inheritance and alternate protocols; use the returned
canonical URL and checked addresses; preserve TLS hostname/certificate verification;
validate the actual peer (`before_request(..., peer_address=...)` can reject a mismatch).
Pin DNS results to connection establishment. Do not cache a seed approval as authority
for later requests. If a dependency's DNS/redirect/browser/media requests cannot be
intercepted and pinned, use process-level egress controls and a tested host set. Without
that control, do not expose arbitrary URL acquisition. The checker cannot stop a library
that ignores it, prevent a second DNS lookup racing connection, see requests inside an
external process, or prove platform access permission. No outbound backend is implemented
or marked effective by B08.

## Secret references

`import_auth(ctx, AuthImportRequest)` reads exactly the operator-specified file, up to
1 MiB; no browser-profile discovery or reading occurs. Dirfd traversal and `O_NOFOLLOW`
reject symlinks in the leaf and parents; `O_NONBLOCK` permits rejecting FIFOs without
hanging. Lexical `..` components are rejected before opening any directory or file;
relative paths are prefixed with the working directory, never collapsed with abspath.
`fstat` checks regular-file type, current UID, no group/other permission bits,
size, and unchanged file metadata after the bounded read. Errors do not echo paths,
content, auth references or raw transport/storage exceptions.

The storage implementation is the private-file fallback only; no keyring dependency
was added. The platformdirs user config root's `metamuse/auth` directory is 0700 and
files are 0600. Config overrides inside a MetaMuse checkout and symlink directory
components are rejected. A random opaque `auth_<uuid>` names a JSON record containing metadata
and base64 bytes. Base64 is an encoding, **not encryption**. Writes create an exclusive
0600 staging file, fsync it, atomically rename and fsync the directory. Secret bytes are
never written to the workspace database, request receipts, events or logs.

The returned metadata is only `auth_ref`, optional non-secret `provider`/`account`
labels, `storage_kind=private_file`, and `created_at`. Each import creates a new reference.
There is no browser-profile reader, secret-export command or secret-returning MCP tool.

`list_auth(ctx)` / `metamuse auth list` require `ctx.require_interactive()` and return
`data.references`: provider, account, auth_ref, created_at, storage_kind and config_bound.
The binding flag checks the current workspace's Muse config, even if Muse is disabled.
Only validated metadata is returned; neither secret values/base64 nor filesystem paths
are exposed. A missing directory lists zero refs. Corrupt leaves produce `partial`,
with valid `data.references`, opaque `data.unavailable` refs/fixed reasons and exact
`metamuse auth revoke --auth-ref REF` next actions; no record content is returned.
Unsafe directories still fail with the fixed auth error.

`revoke_auth(ctx, auth_ref)` / `metamuse auth revoke --auth-ref REF` also require human
interaction. Dirfd traversal rejects symlink directories, and lstat rejects symlink or
non-regular leaves. One owned regular file is unlinked and the directory fsynced, without
reading its secret bytes; this also allows revoking a corrupt record. The response contains
auth_ref, revoked=true, config_bound and nullable next_action. A remaining config binding
gets an import-and-rebind next action; config and other references are preserved. This
removes the local record, not credentials at their provider or copies in external backups.
No MCP tool, automatic retention sweep or second credential store is added.

`resolve_secret(auth_ref, *, provider, account, secret_store=None)` returns bytes for
backend use only. Both nonempty provider/account labels are required keyword arguments,
including on `SecretStore.resolve`; None/empty/mismatched labels fail before secret read.
Wrong labels, unsafe/missing/corrupt files and invalid references fail with `E_AUTH_REQUIRED`.
Resolution raises its fixed error outside exception handlers: both `__cause__` and
`__context__` are None, including for JSON/UTF-8/base64 corruption.
An import without both labels cannot be resolved by a backend; re-import with both labels
to bind it. There is no unlabeled test escape or CLI/MCP path to secret bytes.
Do not put bytes into envelopes, logs, exceptions, prompts, packets or diagnostics.
`SecretStore(root=...)` is an explicit dependency-injection seam for synthetic tests,
not a remotely configurable workspace or MCP input. POSIX/Linux is the tested target;
Windows ACLs and OS keyrings are unimplemented. Permission errors are not repaired on
the operator's input file. An I/O failure after the final rename can leave an unreturned
private record; this is not reported as successful import.

## P0 permits

`create_permit(ctx, PermitCreateRequest)` requires existing project, explicit non-secret
provider/account/issuer labels, a timezone-aware expiry strictly after issuance and no
more than 30 days away, and `max_uses` in 1–1000. Scope schemas reject extra fields and
wildcards. `exact_digest`, if present, binds the complete `action_digest` below in
addition to the scope. Human creation uses the existing durable request receipt: the
same key/payload identifies the original grant; a changed payload conflicts. The returned
data always reloads its current row, including used/revoked/expires_at, and includes
`replayed: true` on a receipt hit (`false` on issuance). Replay never inserts a new grant,
extends expiry, refills uses or undoes revocation, including after expiry or exhaustion.

| Kind | Required scope | Allowed use |
|---|---|---|
| `access` | `operations`: nonempty subset of `muse.open`, `muse.read`, `collect.remote`; `target_digests`: 1–200 exact complete action digests | Exact destination and actual topic/read/collection payload, bound to project/provider/account |
| `share` | `packet_digests`: 1–200 exact stored packet digests **or** `snapshot`: immutable digest plus explicit exact evidence selection | `muse.send`, reading the referenced packet in the transaction |

Example share scope: `{"packet_digests": ["<64 lowercase SHA-256 hex characters>"]}`.
Snapshot shape: `{"snapshot": {"digest": "<snapshot_digest(selection)>", "selection":
[{"kind":"item_revision","id":"itm_synthetic","revision":1}]}}`.
`snapshot_digest` hashes canonical JSON of sorted/deduplicated exact evidence refs.
Creation validates project ownership and all original revisions/segments. Verification
loads the actual packet from the database and validates project, staleness and refs.
Every packet ref must belong to the approved snapshot; supplied claimed snapshot IDs or
`scope` dictionaries on a feature request have no authority. Snapshot A never follows
later revisions, new sources, another project or a changed provider/account.

The host/operator is responsible for the semantic scope of supplied question/text;
there is no automatic legality or privacy proof. A snapshot allows bounded question
variation within displayed evidence. Use `exact_digest` when the entire question and
payload must be approved verbatim. Preserve packet immutability and source-sharing
policy in the content/Muse feature implementation; B08 does not generate or redact text.

`verify_permit(store, permit_id, PermitUse(...), *, conn, reserve=False)` checks current
kind, project, provider/account, operation, scope, exact digest, expiry, revocation and
remaining uses. A missing or unknown permit/expiry returns `E_SCOPE_REQUIRED`; an unknown
ID has the message `Permit does not exist`. Invalid, expired,
revoked, exhausted or mismatched permissions return `E_SCOPE_DENIED`. Both carry an
operator `next_action`. With `reserve=True`, it requires the caller's transaction,
increments with a conditional SQL update and rolls back with the caller. The update
checks expiry using `metamuse_now()` on the Store connection, and core triggers recheck
expiry/revocation at consumption. Constraint/expiry-boundary rejection becomes a clean
`E_SCOPE_DENIED` without SQL or submitted values. Authorization columns are immutable,
used never decreases and revocation is sticky; a changed scope/budget/expiry needs a new
human grant. SQLite's julianday clock comparison can conservatively deny a use just
before expiry at submillisecond precision; it never extends or renews the permit. Use
`store.transaction()` (`BEGIN IMMEDIATE`) for atomic reservations. Do not reserve once
here and again in the external verifier.

`permit revoke ID --request-key KEY` is idempotent and permanent. `permit list --project
ID` returns current grant records, up to 200; over 200 returns `E_LIMIT_REACHED` instead
of presenting a partial list as complete. Paged permit listing is not implemented.
These commands remain human-only; fifteen existing MCP tools remain unchanged.

## External-action verifier

Persist discussion/exchange/collection intent and exact target first. A target contains
`project_id`, `provider`, `account`, `operation`, actual destination fields, `packet_id`
for share, and a stable local `intent_id` for submission. Do not include local request
keys, permit IDs, or claimed approval flags in the actual outbound target/payload.

```python
from metamuse.safety import PermitUse, action_digest, external_action_verifier

# target and actual_payload come from trusted feature code and persisted domain records.
use = PermitUse(
    kind="share",
    project_id=project_id,
    provider=provider,
    account=account,
    operation="muse.send",
    packet_id=packet_id,
    digest=action_digest(target, actual_payload),
)


def load_payload(conn, target):
    # Database-only: reconstruct actual bytes/fields to send; verify current discussion
    # account/session/quarantine and source-sharing policy here. Never accept a claimed hash.
    return load_exact_outbound_payload_and_verify_destination(conn, target)


step.before_external_action(
    step.target,
    verify=external_action_verifier(
        step,
        permit_id=permit_id,
        use=use,
        load_payload=load_payload,
    ),
)
# Intent and use have committed together; send the exact verified payload once.
```

`action_digest` hashes canonical JSON of actual target plus outbound payload, excluding
only local `intent_id`. Include every externally material destination, question, topic,
attachment digest and outbound field; no Unicode normalization. Supply JSON-compatible
values or exact outgoing UTF-8 text; binary bodies need an explicit base64 field in
the feature's persisted payload contract. `PermitUse.digest` is not
a permission by itself: the callback recomputes it from current domain state inside the
core guard transaction. It binds target fields to the use and persisted job/step.
The feature callback must validate current real destination/account/session and reconstruct
the payload that the transport will actually use. Mutable feature state must not change
that payload after verification. No external wait or DNS occurs inside the transaction.

The verifier increments the permit and records a `permit.use_reserved` event in that
same transaction. These events contain identifiers and hashes only. A repeated safe
read checks the same durable reservation identity and rechecks expiry/revocation/scope
without spending a second use (even if the last use is already spent). A submitted
step refuses a second boundary. Uncertain matching action digest or stable intent in
any job in this workspace/project blocks a fresh use, including a changed job request
key, local intent ID or permit. Keep original uncertainty and reservation records until
reliable feature-specific reconciliation; do not delete/prune these events while the
step or its permitted safe-read retry remains recoverable. Changing packet/payload on
the same uncertain intent also remains blocked.

The core owns epoch/cancellation/target fencing and the `submitting` transition. B08
adds no worker, queue, schema or migration. The feature owns reconciliation, receipts,
source shareability and quarantine after uncertain abandonment. No automatic refunds
or remote exactly-once guarantee exist; local transactions cannot remove the gap between
verification and remote submission. Same-UID code that rewrites/deletes intent state or
bypasses the verifier is outside the stated trust boundary.

Core's interaction guard runs before CLI delegation for all human-only auth/permit
commands, and safety entry points also call `ctx.require_interactive()`. A non-interactive
context cannot import, issue, revoke or list via these entry points. `permit approve`
also requires the explicit confirmation described below. Tests opt into live-mode configuration only for synthetic handlers while
socket/DNS calls remain blocked; this is FIXTURE/INTEGRATION evidence, never LIVE.

For `settle_uncertain`, read remote evidence outside transactions and supply a separate
database-only proof callback for definite delivery/refusal. Do not pass the send verifier,
reserve another send use, refund the original use, or reopen the original submission.
The settlement callback may use the supplied `TransactionWriter` for domain proof writes;
no nested Store transaction or network wait belongs there. Revoked/exhausted send grants
do not erase the original submission or prevent settling reliable proof of that submission.
Fetching remote history still needs its separate current access permission and URL policy.

The shared URL corpus runs in place with injected DNS, plus explicit allowlists for its
host-confusion rows and loopback answers for localhost. Its 4120-byte "overlong" and
single trailing-dot rejection expectations contradict this documented contract. The
table-driven test declares two explicit spec-based allow expectations; corpus data is
unchanged. All other corpus expected_verdict values are asserted without overrides.

The verifier also requires the registered side effect to match the action: `muse.open`
and `muse.send` use `external_submit`; `muse.read` and `collect.remote` use `safe_read`.


B10 integration: the external verifier returns the recomputed action digest after its
checks/reservation. Core mints a one-use token after commit. Live Muse credentials use
an immutable provider/account/auth_ref snapshot and the explicit cookie-header JSON
format documented in [MUSE.md](MUSE.md). A Muse share also requires exact_digest for
the prepared question/packet/target action, in addition to its snapshot/packet scope.

## Human approval without Python internals

Use the E_SCOPE_REQUIRED `approval` from a shipped Muse CLI/MCP call. The preview creates
nothing. The human saves the shown request JSON, reviews the topic/question/destination
and packet via `packet preview`, then runs `permit approve --operation OP --request-file
PATH --request-key KEY [--exact-digest SHA256] [--expires-at TIMESTAMP] [--max-uses N]`.
The command recomputes/displays review on stderr, including the effective absolute UTC
expires_at, remaining duration and max_uses before accepting terminal `yes` or an exact
recomputed digest. The displayed values are stored; defaults are 24 hours and one use. `--non-interactive` is refused and no MCP approval tool exists.
Default expiry is 24h; explicit expiry is timezone-aware and within 30 days. Default-expiry
replay validates stored receipts through the core duplicate-key-rejecting decoder and
never extends the original grant. Binding labels come from configuration only.
Changed request text or packet fails digest equality; preview data cannot mint permissions.
The fixed registration seam and B12/B13 adoption obligations are in [CORE_API.md](CORE_API.md).

B16a credential refusal uses redaction classes, excluding `local_path`. Code expressions,
numeric session counters, pagination/model vocabulary and header prose without values
are benign. Accepted Muse topics/questions remain exact in the wire and approval preview.

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
