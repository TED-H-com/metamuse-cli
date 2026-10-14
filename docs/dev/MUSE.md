# Muse transport and lifecycle (B09/B10)

B09 supplies the serial Noise gateway transport. B10 implements the four lifecycle
app functions, shared CLI/MCP tools and durable jobs in `_muse_lifecycle.py`.
`muse.open` and `muse.send` are external submissions; `muse.history` and
`muse.sessions` are separately authorized safe reads. `IMPLEMENTED_TOOLS` contains
only the four lifecycle tools. No background worker is started by a tool call.

The spike under `spikes/muse/` is the historical record. Production code is `muse.py` plus `_muse_codec.py`, `_muse_associate.py`, `_muse_link.py`, `_muse_noise.py`, and `_muse_http.py`. `curl_cffi` and `noiseprotocol` are imported inside those helpers on first use. Importing `metamuse.muse` does not import them. A missing install raises `E_DEPENDENCY_MISSING`.

Historical B09 (2026-10-13) had no authorized account. On 2026-10-14 the orchestrator
recorded one authorized LIVE standard msg1/msg2 observation: nested key equaled the
responder static key and nested nonce equaled the fresh client nonce. This implementation
and its tests are OFFLINE/FIXTURE only. Msg3/split/application readiness and AT-038/AT-043
remain unproven pending the orchestrator's separately authorized LIVE run.

## Allowlist

| Call | Wire | Mutation |
|---|---|---|
| `POST /api/auth/check` | HTTPS, cookie jar, empty body | no |
| `GET /api/session?vm_state=live` | HTTPS, cookie jar | no |
| `POST /api/hatch/token` | HTTPS, cookie jar plus `Authorization: Bearer` | no |
| `POST /api/hatch/vm/wake` | HTTPS, cookie jar plus bearer | account-side power-state request |
| `session.start` | daemon service 0, `POST /session/start` | yes |
| `sessions.list` | daemon service 0, `GET /api/session/list` | no |
| `chat.stream` | daemon service 0, `POST /chat/stream` | yes |
| `chat.history` | daemon service 0, `GET /chat/history` | no |

Any other route name, including `cmd_raw`, raises `MuseProgrammingError` before I/O. There is no escape hatch. `chat.history` drops `transcript_mode` when a caller passes it. Service id 0 is omitted on the wire.

Default hosts, and the only real hosts in this module:

- `https://muse.ai`
- `wss://hatch.metaaivm.com/v1/noise`

`wss://{vm_id}.metaaivm.com/` is the `vmAddress` field of the hatch-token body. The transport does not dial it. `vm_id` must match `[A-Za-z0-9._-]{1,128}`.

## Committed mutation boundary

The transport does not mint a core token and does not reserve a permit. B10 owns that boundary. `SubmitAuthorization` is implemented by the issuer-registered core `ExternalActionToken`, minted in `jobs.py` only after `before_external_action` commits.

1. Persist the exchange intent first, including the caller-supplied marker for both create and send.
2. Call `step.before_external_action(target, verify=external_action_verifier(...))`. B08's verifier reserves the one-use permit inside that transaction. Do not call `verify_permit(..., reserve=True)` before it.
3. Only after that call returns, pass the committed token as `authorization` together with the frozen `PreparedMuseRequest`.
4. Pass `network_mode` from `ctx.policy.app.network_mode`.

`session_start` and `chat_stream` take a required keyword `authorization`. `None`, a bool, and any object without `consume` raise `E_SCOPE_REQUIRED` before I/O. `consume` runs once, before any byte is written, with `safety.action_digest(target, payload)`. A consume failure sends nothing. Reads do not take an authorization and do not mint a mutation token.

`network_mode="offline"` raises `E_SCOPE_DENIED` with the same message as `Executor.require_network`, before credentials are read and before any URL is requested. The default mode is `offline`.

Question text is at most 8192 UTF-8 bytes. Packet text is at most 49152 UTF-8 bytes. Both caps are checked on the prepared payload and on the JSON body before `consume`. The marker matches `^[A-Za-z0-9._~-]{1,128}$` and is the caller's for both create and send. The transport does not generate one after authorization.

## Prepared request

```python
prepared = prepare_muse_request(
    "chat.stream",
    marker=stored_marker,
    body={"text": question},
    target={"origin": "https://muse.ai", "route": "chat.stream"},
    payload={"text": question},
)
transport.chat_stream(prepared, authorization=token)
```

`PreparedMuseRequest` is frozen: route, verb, path, marker, body bytes, target, and payload. Build it before any I/O. `body` is the exact gateway JSON. `target` and `payload` are what `action_digest` covers.

## What B08 must inject

```python
class CredentialProvider(Protocol):
    def cookies(self, auth_ref: str) -> str: ...


class OutboundGuard(Protocol):
    def check(self, url: str, *, purpose: str) -> CheckedURL: ...

    def verify_peer(self, checked: CheckedURL, peer_address: str) -> None: ...
```

`check` never returns `None`. It returns the canonical URL, hostname, port, and at least one address. Empty addresses are refused. The transport dials `checked.url`, keeps Host and SNI on the hostname, and puts one `{hostname}:{port}:{addr1},{addr2},...` entry in `CURLOPT_RESOLVE`, with IPv6 bracketed and addresses in guard order. It refuses a pin whose host, port, or scheme does not match the URL it asked to check, and it refuses an IP-literal hostname.

`verify_peer` runs after the adapter reads the actual peer (`primary_ip` on HTTPS, `CURLINFO_PRIMARY_IP` on WSS) and before the transport accepts a response or starts the Noise handshake. A mismatch raises `E_SCOPE_REQUIRED` and the body is not parsed.

Transport purpose names stay `auth.check`, `session`, `hatch.token`, `hatch.wake`, and `wss.noise`. They are not B08 `URLPurpose` values. B10 maps every one of them to B08 `purpose="request"`. B08 accepts only HTTP(S), so the WSS check is B10's adapter: validate the fixed hatch authority through an equivalent HTTPS URL, then restore only the scheme on the `CheckedURL` handed back here.

`cookies` receives the opaque `auth_ref` and returns one cookie-header value. The transport does not read a browser profile or a cookie file. The guard sees the real WebSocket URL, including `auth_token`. That value must not be logged.

There is no guard-validated proxy. `MuseTransport(proxy=...)` raises `E_SCOPE_REQUIRED`. The adapter always sets `CURLOPT_PROXY` to an empty string.

HTTPS and WSS use `curl_cffi` 0.16.3, `impersonate="chrome"`, and the certifi CA bundle (`verify` is that path, and `CURLOPT_CAINFO` is the same path). `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `SSL_CERT_FILE` are not the trust store. Hostname verification stays on. `curl_options` are passed to the `Session` constructor, not to `Session.request`. Redirects are not followed: a 3xx is `E_SCOPE_REQUIRED` and the `Location` value is not copied into the error. Auth bodies stop at 65536 decompressed bytes.

An optional `clock() -> float` is the monotonic deadline. The default is `time.monotonic`.

## Connection

The lifecycle passes `store=step.store` to `MuseTransport`, along with its existing
bound credentials, guard and network policy. Direct adapters may instead inject the typed
`StoreMuseTrust`; absence of authoritative persistence blocks with `E_STORAGE`.

1. POST `/api/auth/check`: require usable access token, authenticated `ok=true`, current
   `viewer_id` and `session_binding_id`. Bind the viewer hash to configured provider/account.
2. GET `/api/session?vm_state=live`: normalize payload and every row VM ID before matching.
   Scan the payload and all rows first: any `hatch_cvm`, `CVM_*`, truthy `cvm_required` or
   `cvmReplacementEnabled` persists sticky confidentiality then stops, even if unassigned,
   ambiguous or otherwise invalid. No usable VM ID records the owner/account expectation.
   Only after that scan require assigned status, one matching `hatch_vm` row, consistent
   VM ID/name/endpoint and known state, and reject replacement context/provisioning pins.
   The storage ID and exact server ID on wake/WSS may differ only in case and surrounding
   whitespace. Mint retains the row endpoint/name verbatim.
   `DISABLED`, `DELETING`, `ERROR` stop before wake. Missing/contradictory metadata fails closed.
3. Freeze owner/session/active-selection generation in the private Store. The effective
   expectation is the OR of the local account/VM, every fixed-upstream viewer hash and every
   fixed-upstream normalized VM ID. New rows inherit confidentiality; existing matching rows
   propagate it monotonically. A different local account label cannot clear either identity.
   A standard observation cannot clear the expectation.
   Connect/retry cannot reselect a different VM. Storage corruption, missing backing state,
   unsupported provenance/version or failed CAS blocks with `E_STORAGE`, retry class `never`.
4. Mint POST `/api/hatch/token` with exactly `{vmAddress: <row endpoint_url>, vmName: <row name>}`.
   Access tokens retain their separate bounded grammar. Hatch legacy tokens are single
   opaque segments (`A-Z`, `a-z`, digits, `_~+/=-`; no dots), or accept
   `s0:<base64url>.<base64url>.<base64url>` verbatim. A present non-delegation notary must have
   six nonempty base64url segments after trimming; pass it unchanged. Missing/blank omits it.
   Every present `token`, `notary_token`, `notaryToken`, `noiseNotaryToken` and `endorsement`
   is validated. Case-insensitive delegation./endorsement. prefixes and malformed/oversized
   s0 envelopes stop with `E_PROTOCOL_MISMATCH` (`never`); aliases are never silently dropped.
   Conflicting nonempty notary aliases also stop.
   No local signature/expiry verification is claimed. A first parseable positive finite
   `timeout:` restriction is only a cache hint: mint at most twice if within 60 seconds.
   Credentials are minted anew per connection, held only in memory for connection-lifetime redaction and never cached durably.
5. POST `/api/hatch/vm/wake` `{vm_id, retry_count: 0}`, then guarded WSS dial with exactly
   `vm_id`, `auth_token`, optional `notary_token`, `app_id=hatch-web`, `request_id=<fresh UUID4>`.
   WSS sends fixed `Origin: https://muse.ai`; no notary header. Upgrade 502/503/504 causes
   wake `{vm_id, retry_count: n, connect_attempt_id: <fresh UUID4>}`, followed by
   `max(2s, min(0.2s × 1.5^(n−1), 8s))`. Stop after 20 redials or 240 seconds, whichever
   comes first, with `E_RESOURCE_UNAVAILABLE` and an operator action to retry later because
   the VM is not running yet. Each redial rechecks captured currentness and uses a fresh
   request ID. Remaining budget is checked immediately before each wake, dial and handshake,
   including after guard/peer validation. Exhaustion never passes a nonpositive curl timeout
   or sends msg1; it returns `E_RESOURCE_UNAVAILABLE` with an operator retry action.
   Other failures do not enter this redial loop.
6. Mature `Noise_XX_25519_AESGCM_SHA256`, empty prologue: fresh CSPRNG 32-byte challenge
   in msg1 bytes field 1. Msg2 must classify strictly as outer bytes field 2 (optional
   platform field 1/varint=0), containing exactly two nested bytes fields, key and nonce,
   each 32 bytes. Require key == mature-library responder static and nonce == this attempt's
   challenge. Duplicates, unknown fields/evidence, malformed/oversized input, bad AEAD,
   low-order DH and mismatches stop with `E_PROTOCOL_MISMATCH`, `never`. All handshake
   varints must be minimal uint64. Valid RV field 1 is nonempty bytes. A canonical outer
   field-2/varint tag identifies RV even if its state, following fields or chip binding are
   malformed/unsupported. Every such Noise-authenticated shape records permanent owner/VM
   confidentiality before stopping; there is no reset tool. CVM/unknown/delegation have no fallback.
7. Commit standard-observation provenance with generation CAS immediately before empty msg3,
   holding provider/per-account trust locks across commit and msg3 handoff. Release the shared
   maintenance lock after commit and before network handoff. Split only through the
   mature library. Generation changes invalidate local sockets; each call rechecks durable
   currentness. No standard enrollment-health or capability-registration gate is added.

Wake is an account-side power-state request, performed only inside a connection triggered
by an already-granted Muse operation in live network mode. Offline mode and the separately
selected fixture gateway perform no wake or network calls. The in-process connector tests
exercise live-mode code through injected adapters only. There are no lease-vm, reset-vm,
cvm-chip-selection, enrollment, fallback notary, recovery-key or registration calls.
Standard never claims SNP, TCB, confidential execution or notary-signature verification.
These key/nonce and durable downgrade checks are stricter than the described web standard
branch; they preserve its applicable mature Noise/TLS and context checks without changing tier.

One `ReceiveLoop` owns the socket. A second loop, a collect without that ownership, or a second call that overlaps the first raises `E_PROTOCOL_MISMATCH`. `close` takes the same call lock. Calls are serial. Stream ids are positive int64 values starting at 1. The transport stops that connection before the id would exceed `2**63-1`. It does not wrap ids on the same connection. Each collect waits at most 30 seconds of the injected clock and reads at most 64 frames. A caller cannot raise those caps. Caps elsewhere: plaintext 65519 bytes, ciphertext 65535 bytes, body 1 MiB, 32 foreign frames. WebSocket reads also stop at 65535 bytes and 64 fragments, and they check the deadline on every fragment. Every request selects `Accept-Encoding: identity`; duplicate or non-identity response content encoding is refused before body interpretation. Unknown field, multi-chunk frame, duplicate response, a body chunk before the response, and oversize are `E_PROTOCOL_MISMATCH`. The frame budget is `E_LIMIT_REACHED`. A read that hits the deadline is incomplete.

A protocol error, an authentication failure, or a failed encrypted write poisons the link and closes it. The Noise nonce is not rewound. Each direction stops at a conservative 2**32 transport frames. The next call may handshake a new connection. That new connection does not authorize the submission whose token was already consumed.

## HTTP status

Success status is endpoint-specific: auth/check, session and hatch/token accept exactly
200 (spike §2/§5 bootstrap); wake accepts 200 (supplied S1 Finding 3), plus 202 and 204 as
explicitly authorized in the round-2 operator contract. 202/204 indicate accepted/no-content
activation, not a running VM. They were not observed LIVE by this task. Wake success needs
no JSON body. Read/mint success still requires bounded valid JSON and its own field checks.

| Status | Code | retry_class |
|---|---|---|
| 401, 403 | `E_AUTH_REQUIRED` | `user_action` |
| 429, integer `Retry-After` of 1 to 6 digits only | `E_RATE_LIMITED` | `safe_read` |
| 3xx | `E_SCOPE_REQUIRED` | `user_action` |
| HTTPS 5xx; WSS 5xx after its bounded redial (only 502/503/504 redial) | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 404, 409, 410 on session or wake | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| Other unexpected 2xx/4xx or unsupported response shape | `E_PROTOCOL_MISMATCH` | `never` |
| 200 unassigned session without confidential hints | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| CVM/RV/delegation/endorsement/malformed s0/crypto/key/nonce mismatch | `E_PROTOCOL_MISMATCH` | `never` |

Upgrade 401/403 is `E_AUTH_REQUIRED`, 429 is `E_RATE_LIMITED`; DNS/connect/TLS failure
is `E_NETWORK_TIMEOUT`, with `E_SCOPE_REQUIRED` when the guard refuses. Corrupt/stale trust
or failed commit/CAS is `E_STORAGE` (`never`). The upgrade URL, query, response body and raw
curl exception are never copied into any exception, envelope or log. Redaction covers
all nonce/proof/recovery/RV/notary/endorsement/wake/attempt aliases in snake, camel and hyphen
forms, raw/encoded envelopes, embedded wake bodies and known values under unknown keys.
Handshake nonce hex/base64/base64url and WSS request IDs are held per connection for scrubbing.
Credential/binding/header/body dataclass reprs suppress those fields.
The response body is not copied into these errors. Unavailable VM is not an auth failure. Provider and guard exceptions are replaced with a fixed message. The public error is raised outside the handler, so its chain does not carry the original text.

## Attempt evidence

`chat_stream` returns a `StreamAttempt`:

| Field | Meaning |
|---|---|
| `outbound_digest` | SHA-256 hex of the exact gateway body bytes |
| `request_marker` | the caller's marker, sent as `x-request-id` |
| `stream_id` | id of that attempt, or `None` when no frame was handed off |
| `bytes_handed_off` | the pipe accepted the request frame |
| `evidence` | `acked`, `app_error`, or `undecided`, plus a scrubbed detail |
| `state` | a SPEC §6 state from `delivery_state` |
| `error_code` | set when the collect did not finish |
| `body` | scrubbed UTF-8 body for persistence, or empty when the bytes are not UTF-8 |

The verdict is classified on the original bounded body, then a separate public body is scrubbed. Known credential values held on that call are removed, then `safety.redact_text` runs. Undecodable bytes are not returned.

After a mutation handoff, a read failure does not use safe-read retry guidance. `chat_stream` returns an uncertain attempt and keeps the marker, digest, and stream id. `session.start` raises `MuseStateUnknown` (`E_EXTERNAL_STATE_UNKNOWN`, `reconcile`). A protocol failure after handoff raises `MuseProtocolStop` (`E_PROTOCOL_MISMATCH`, `never`) and still carries that evidence. Only a read, or a failure proven to be before handoff, may use safe-read retry guidance. A raised network timeout does not mean the frame was not handed off.

`chat_stream` does not raise for a timeout, a non-2xx status, `ok: false`, or a body that is not a JSON object. Those stay `send_uncertain` after handoff, with `app_error` or `undecided` on the evidence. `failed_before_send` is only when no frame bytes were handed off. A later user echo of this exchange promotes the state to `sent` and leaves the evidence row in place. A recognized marker that is not this request disqualifies the event even when another marker matches. Pass the real `Exchange` and the history events into `chat_stream` when they are already known.

`delivery_state`, `attempt_evidence`, `associate_reply`, `observe`, `Exchange.earlier_unresolved`, and `ObservationMemory` are re-exported from `metamuse.muse`. `CALIBRATED_COMPLETE_STATUSES` is empty. `ambiguous` is an association result, not an exchange state. `closed` does not reopen. The transport never resends.

## Schema

B09 migration `005_muse_trust.sql` adds private account/VM trust tables and fourteen
monotonic, identity, retention, inheritance and propagation guards. Round 2 amends this
unmerged migration in place; no 006 is added. An older development-v5 workspace is refused
as noncanonical rather than silently resetting its trust. Owner/session hashes, version, source/time, active VM and
generation are private internal rows, with no public model/schema change. It uses the core
pre-upgrade backup and atomic migration. Restore a verified schema-4 backup into a fresh
workspace for rollback; never erase security state in place to make standard connect.

B10 registers forward migration `003_muse_lifecycle.sql` and regenerated schemas,
as detailed below. Do not edit `001_initial.sql`. NULL `bytes_handed_off` means unknown:
a crash after `submitting` and before the transport result provides no zero-handoff
proof. First attempt evidence is immutable; later history proof never rewrites an
`app_error` into `acked`.

## Lifecycle API and authorization

`muse_open` persists discussion, exact open payload, binding, job and original step in
one transaction before returning `accepted`, `queued`, `worker_available:false` when
no worker owns the workspace. `muse_send` does the same for the exchange. Replays
use the original request and job; a changed payload is `E_IDEMPOTENCY_CONFLICT`.
Unknown delivery returns reconciliation instructions and never enqueues a second send.
An unresolved open also blocks another create under that project/account, including
under a new request key. Submission and the committed boundary both check this rule;
an already-queued create is refused before reserving its grant or minting a token.
Its step fails locally without uncertain delivery. This is a refusal, never evidence
based on title equality.

Operators approve through the shipped entrypoints. Omit the grant on `muse open`,
`muse send`, or `muse read --refresh` (or send the corresponding MCP request). The
E_SCOPE_REQUIRED envelope includes `approval`, creates nothing, and shows the exact
configured provider/account, destination, topic/question, action digest and packet
ID/digest/byte count. Run its `packet_preview_command` to inspect the fixed packet.
Save the shown non-secret `approval.request` JSON at `approval.request_file`, then
show the human `approval.next_action`, for example:

```bash
metamuse --workspace /path/to/workspace --json permit approve \
  --operation muse.send --request-file /path/to/request.json \
  --exact-digest DIGEST_FROM_PREVIEW --request-key operator-send-1
```

The human command recomputes/displays the preview plus the effective UTC expiry,
remaining duration and max_uses before accepting the exact digest (or terminal `yes`
if omitted), and returns a permit ID. Defaults are 24 hours and one use. Retry the original request with
that ID as `share_grant_id` or `access_grant_id`. `--non-interactive` refuses approval;
there is no MCP approval tool. Expiry defaults to 24h and may be set explicitly with
`--expires-at`. `--max-uses` can allow a bounded number of identical history reads;
changed question/packet/target returns E_SCOPE_DENIED without a preview when given
the old grant. Repeat without a grant to get a fresh review. Stale packets return
E_REFERENCE_INVALID and require a new packet. Provider/account come only
from the binding, and the verified body/headers/verb/path/marker/node cannot be
substituted. Reservation and expiry/revocation checks occur again at the step boundary.
Credential-like request text is redacted for review; use the original human-owned
request file to approve its original digest, never substitute the redacted copy.

A live send requires a successful authorized history job for its baseline; use the
mapped discussion's open reconciliation request with an access approval first. A new
fixture session has a known empty baseline. Sequence observations do not claim full
history coverage. See [OFFLINE_SLICE.md](OFFLINE_SLICE.md) for a rerunnable journey.

The daemon `text` is exactly `question + "\n\n" + packet.rendered_text`; the two host
texts retain CRLF and Unicode composition. The separator is the only joining text.
Question and packet are independently capped at 8192/49152 UTF-8 bytes, and their
exact joined text is checked against the verified payload before transport use.
Topic is capped at 8192 UTF-8 bytes. No generated questions, follow-ups, strategy,
persona or action are derived from replies. Replies remain untrusted host data.

`muse_read` without `refresh` performs only local reads, including while Muse is
unconfigured or offline. With `refresh=true`, a current access grant is required;
it returns stored data and an accepted `refresh_job_id`, `state`, `worker_available`.
The approval envelope previews the exact read scope. A refresh
fetches one bounded history page from the original session; absence is not proof of
non-delivery or full history coverage. Raw observations are retained before association.
Echo comparison applies the same deterministic `sanitize_document` redaction to the
prepared outbound text and stored observations. The wire still carries the exact host
question and packet; redaction collisions remain subject to the ambiguity rule.
Association excludes prepared attempts and proven `not_submitted` / zero-handoff
attempts, including closed ones. The boundary records a write-once
`submitted_observation_high_water`; only messages first observed after that mark
and passing the sequence/time checks can belong to this send. Later enrichment
cannot make an old message eligible. If markerless echoes fit two delivered
exchanges, their association stays sticky `ambiguous` and neither claims messages
or gains a history delivery proof. A first transport result arriving after a
history proof still records its actual verdict/handoff, preserving that proof and
the host's closed state.
The app's opaque HMAC cursor uses the private versioned `content.cursor` workspace key,
binds discussion/exchange and observation high-water/offset, and survives key rotation
and backup/restore. Pages contain at most 50 observations; message text/status comes
from observations within the same high-water mark. Lifecycle state is read live.

For open recovery, pass the original `discussion_id` to `muse_open` (CLI `muse open
--discussion ID --project ID --topic ORIGINAL --access-grant ID --request-key NEW`).
When the session is still unknown, this explicitly queues `muse.sessions`, never `session.start`. A mapped discussion queues an authorized `muse.history` read instead. Only an exact original
request marker with one session ID establishes the mapping. Matching titles, missing
sessions, or a malformed page remain uncertain. A gateway that does not return marker
proof remains blocked; LIVE response formats and marker support are unverified.

`muse_close_exchange` records the host outcome and a durable request-key receipt.
Unknown delivery permits only `abandoned`, and quarantines the discussion against a
new exchange. Host outcome cannot settle the original step. History proof settles
that original step through `Executor.settle_uncertain`, without another send, permit
reservation or requeue. Quarantine clears only after settlement. Late observations
remain visible and are labeled `late`; `closed` never reopens. Uncalibrated statuses,
including `completed`, remain progress; an answer never asserts research completion.

## Token and zero-handoff settlement

`ExternalActionToken` cannot be directly constructed, copied or serialized. Its private
issuer registry binds owner/epoch/job/step/operation/target/action digest; consuming it
checks fencing, cancellation and exact action identity, then marks it one-use. Reads
return no token. An entered external step cannot mint again, even under a new verifier.
This proves the committed trusted boundary, not isolation against hostile same-UID Python.

Only the transport's concrete `failed_before_send` / `bytes_handed_off is False`, or
`MuseNotSubmitted` from a pre-frame connect failure, can register an opaque in-process
`NotSubmittedProof` with the issuer. Registration precedes the evidence write; that
write and checkpoint use `commit_not_submitted`: fenced, atomic accounting which
persists the registered proof before honoring cancellation. Core then settles the original
step as local `failed`, `remote_state=not_submitted`, retaining local cancellation. The
domain exchange remains `failed_before_send` until the host closes it, or retains an
already recorded `closed` outcome. A guarded settlement callback clears only the original
step's quarantine after that step settles. If accounting or settlement fails, the executor
records `step.not_submitted_save_failed`, keeps delivery uncertain, releases its process-local
authorizations, and continues the queue. This does not weaken closed-state or fencing guards. A new attempt requires a new request key
and grant; permit use is never refunded. A crash after `submitting` but before saving
transport accounting has NULL handoff and remains uncertain. A timeout is never relabeled
as a remote refusal. Cancellation after handoff remains local cancelled/remote unknown
until definite original-session evidence exists.

## Binding, secrets and URL adapter

Config owns `[muse] enabled`, `backend = "live" | "fixture"`, and optional
`[muse.binding] provider`, `account`, `auth_ref`. No secret value belongs in TOML.
A live discussion snapshots these labels immutably. Missing binding or secret reports
`needs_auth` for open/send in inspect and `E_AUTH_REQUIRED` with `next_action`; live
mode, credentials and permissions are all explicit. A later config change never chooses
a different account for an existing discussion.

Explicitly import UTF-8 JSON with exactly
`{"format":"cookie-header","cookie_header":"name=value; name2=value2"}` into B08's
secret store, labeled with provider/account. `BoundMuseCredentials` calls
`resolve_secret(auth_ref, provider=..., account=..., secret_store=...)`, rejects duplicate
keys, unknown format/fields, input over 8192 bytes, controls/CR/LF/NUL/DEL/non-ASCII and
malformed or empty cookie pairs. It performs no browser/cookie discovery. Failures have
fixed messages and no credential-bearing exception chains.

`MuseURLGuard` maps all transport purposes to B08 `request`, allows the two exact upstream
hosts, validates WSS through equivalent HTTPS, and restores only its scheme. Peer checks
use the original checked address snapshot, with no fresh DNS as proof. Curl pins the
canonical authority, disables proxies, fixes verified CA material and validates actual
HTTP/WSS peers before response parsing. Routing `session_id` is preserved in sanitized
JSON, while known cookie/bearer/hatch/WSS values are removed everywhere, including those
fields if they contain a known secret. Unknown secret fields and credential-like text
are redacted. Invalid UTF-8 returns no public body.

## Schema 3 and storage guards

`003_muse_lifecycle.sql` follows B05/B06's 002; it does not amend 001. It uses ALTER/ADD
and replaces the message identity index to scope it to discussion/account. Legacy rows
get NULL binding/backend and handoff/evidence, never fabricated zero delivery. Existing
host outcomes become `closed` without inventing delivery proof.

Exchange columns: marker, typed exact outbound payload/body digest, original submission
step FK, write-once submit time/observation high-water and positive int64 stream ID, nullable handoff, first
verdict/detail/SPEC error, separately guarded delivery proof and versioned observation
memory. Discussion columns snapshot binding/backend, original open intent/permit/step,
first open attempt accounting and session proof, plus unresolved-step quarantine.
`muse_observations` stores append-only raw sanitized event JSON, identity, digest,
discussion/message FKs, observed time and backend. `messages` is the stable projection.
Distinct IDs sharing a seq remain distinct; enrichment is another observation.

Every Store open, archive verification before writes and migration commit compares
`sqlite_master` with the canonical replay of all registered migrations. Retained tables
keep every earlier guard name. Package SQL and migrations use explicit existence checks
and ordinary inserts or updates; the conflict-clause allowlist is empty.
Discussion/message/exchange/observation insert guards cover rowid, primary and declared
unique identities. Update guards freeze rowid, identities and immutable bindings;
retention triggers reject delete. Prepared identity is immutable; attempt fields
are write-once; outcomes and closed state are sticky; transitions, same-discussion message
attachment, backend labels, original step and proof references are checked. Observation
memory retains ambiguity/competing/disqualified keys and a high-water mark, not a second
raw transcript. Hydration checks count and byte sum before loading (2000 observations,
2 MiB); overflow is `E_LIMIT_REACHED`, retaining all evidence and never dropping competitors.

## Fixture gateway and handoff

`OfflineMuseGateway(store=None, mode="normal", pages=...)` implements only the four
allowlisted gateway methods. It is selected by explicit operator config
`[muse] enabled=true; backend="fixture"` or explicit test DI via `AppContext.muse_gateway`
and `Executor(..., muse_gateway=...)`. The backend requires the fixture label and persists
it on every discussion/message/observation. Content envelopes carry `backend="fixture"`,
`mock=true`, `research_complete=false`; inspect reports `fixture_passed`, never LIVE.
Fixture mode can run with network mode offline; it still uses real grant/token/job checks.

Modes `open_timeout`, `send_timeout`, `app_error`, `not_submitted`, the `pages` queue,
`calls`, `sessions` and `events` support deterministic AT-018/019/020/021/037 and AT-042.
Normal fixture history can reconstruct its deterministic response after CLI restart;
custom/scripted remote world is in-process and is not a production remote database.
AT-042's host must carry the mock attribution into its resulting draft/bundle. B16 should
exercise real process kill at every boundary, restore and key rotation, epoch takeover
refusal, hydration limits, and repeated uncertain settlement. AT-038 and AT-043 remain unproven: the orchestrator's msg1/msg2 evidence is not a full lifecycle/journey; fixture success is not service verification.


## AT-042 read evidence

Every local read observation includes `evidence_ref` with kind `muse_observation`, its
immutable ID and revision 1, plus backend/mock/research_complete=false. Cite the specific
observation the host read, including progress; later enrichment is a different ID.
Content resolves that stored raw event and digest, not the current Message view. Drafts
and all exports preserve fixture attribution transitively; packets reject Muse refs.
[OFFLINE_SLICE.md](OFFLINE_SLICE.md) proves the fixture-only CLI/MCP process journey.

A crash after a committed send acknowledgement but before job completion leaves the step
uncertain on recovery. An authorized history read verifies the stored original ack's
step ID, exact target/body digest, handed-off bytes and ack verdict, then settles that
original step. It does not require a newly created echo proof, increase attempts or send
again. The same verifier still validates immutable observations for stored user-echo
proofs. This checkpoint boundary is exercised by the real subprocess worker tests.

Remote trust uses one fixed upstream (MUSE_ORIGIN/HATCH_WSS_BASE). Provider and account are local connection labels; neither partitions owner or VM expectations. The shared trust-domain lock serializes all labels.

HTTP 200 with missing, malformed or drifted credential/context fields is interface drift: E_PROTOCOL_MISMATCH / never. Explicit `ok: false` and HTTP 401/403 remain E_AUTH_REQUIRED. A twice-stale notary after one remint is E_RESOURCE_UNAVAILABLE with a retry-later action (REPORT-2 §6.2 specifies unavailable; neither the spike nor S1 proves session refresh repairs it).

Authenticated CVM hints commit label-independent owner/VM observations before binding, generation, status, uniqueness or selection refusals. Every usable VM id in the response is recorded, including non-selected rows. Owner mismatch never drops that observation; the existing local binding is still refused.

Round 3 amends unreleased migration 005 in place (no 006). Label-independent authenticated observations and label-bound selection history form an owner↔VM graph. The recursive `muse_trust_confidential` view computes its transitive closure, with owner-only evidence as an isolated seed. Each propagation trigger updates matching account, VM and observation rows set-wise. A transaction-local `muse_trust_propagating` barrier suppresses nested propagation; a committed barrier row is integrity failure and blocks connects. Component size does not grow trigger depth; there is no identity cap on recording confidentiality. The flag never clears, and a newly observed edge inherits both ends' effective expectation.

Msg2 is decrypted up to the Noise message maximum of 65535 bytes. RV discrimination uses the full authenticated plaintext before the standard-only 8192-byte cap; oversized RV evidence remains permanent for that owner and VM, without a reset tool. Session JSON is bounded at 1 MiB (auth/mint stay 64 KiB). A session response above 1 MiB returns E_LIMIT_REACHED naming the session response, records nothing and never dials.

Per-connection scrubbing includes uppercase/lowercase nonce hex, padded/unpadded standard and URL-safe base64, and uppercase/unhyphenated WSS request and wake attempt UUIDs. Alias values are scrubbed in JSON, Python reprs, headers, colon lines and spaced assignments. Known values are replaced longest first, including their percent-encoded variants.

CVM hint values compare after `strip().casefold()`; marker names compact to lowercase alphanumerics. Thus snake, camel, hyphen and case variants of vm_type/vm_state/cvm_required/cvmReplacementEnabled all record the same permanent remote expectation. This is conservative refusal evidence, never permission to connect to CVM.

Trust is monotonic in place. Restoring an older backup, or rolling back to the documented verified v4 backup in a fresh workspace, discards confidential expectations recorded after that backup. A stale generation or changed connection binding returns E_STORAGE / never with a fresh-connection retry action; it does not advise restore. Integrity/schema failures advise repair and only a verified restore, with this loss warning. There is no in-place reset tool.
