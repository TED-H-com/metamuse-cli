# Muse transport and lifecycle (B09/B10)

B09 supplies the serial Noise gateway transport. B10 implements the four lifecycle
app functions, shared CLI/MCP tools and durable jobs in `_muse_lifecycle.py`.
`muse.open` and `muse.send` are external submissions; `muse.history` and
`muse.sessions` are separately authorized safe reads. `IMPLEMENTED_TOOLS` contains
only the four lifecycle tools. No background worker is started by a tool call.

The spike under `spikes/muse/` is the historical record. Production code is `muse.py` plus `_muse_codec.py`, `_muse_associate.py`, `_muse_link.py`, `_muse_noise.py`, and `_muse_http.py`. `curl_cffi` and `noiseprotocol` are imported inside those helpers on first use. Importing `metamuse.muse` does not import them. A missing install raises `E_DEPENDENCY_MISSING`.

LIVE is not run. There is no authorized account. Tests are offline and use an in-memory gateway.

## Allowlist

| Call | Wire | Mutation |
|---|---|---|
| `POST /api/auth/check` | HTTPS, cookie jar, empty body | no |
| `GET /api/session` | HTTPS, cookie jar | no |
| `POST /api/hatch/token` | HTTPS, cookie jar plus `Authorization: Bearer` | no |
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

B16a checks new topics/questions with the shared name-and-syntax classifier and closed
exemptions in [SPEC §FR-019](../design/02_SPEC.md). Every finding except `local_path`
refuses with fixed `E_INPUT_INVALID` before a preview. Prose that merely mentions names
without assignment syntax stays benign; `Password: required`, `Secret: the meeting moved`,
`Session: Opening keynote` and `Authorization: Basic or Bearer?` are refused by design.
Accepted topic/question bytes remain exact. Stored titles are display-only and are
never credential-checked during send/read/reconciliation.

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

`check` never returns `None`. It returns the canonical URL, hostname, port, and at least one address. Empty addresses are refused. The transport dials `checked.url`, keeps Host and SNI on the hostname, and puts `{hostname}:{port}:{address}` in `CURLOPT_RESOLVE`. It refuses a pin whose host, port, or scheme does not match the URL it asked to check, and it refuses an IP-literal hostname.

`verify_peer` runs after the adapter reads the actual peer (`primary_ip` on HTTPS, `CURLINFO_PRIMARY_IP` on WSS) and before the transport accepts a response or starts the Noise handshake. A mismatch raises `E_SCOPE_REQUIRED` and the body is not parsed.

Transport purpose names stay `auth.check`, `session`, `hatch.token`, and `wss.noise`. They are not B08 `URLPurpose` values. B10 maps every one of them to B08 `purpose="request"`. B08 accepts only HTTP(S), so the WSS check is B10's adapter: validate the fixed hatch authority through an equivalent HTTPS URL, then restore only the scheme on the `CheckedURL` handed back here.

`cookies` receives the opaque `auth_ref` and returns one cookie-header value. The transport does not read a browser profile or a cookie file. The guard sees the real WebSocket URL, including `auth_token`. That value must not be logged.

There is no guard-validated proxy. `MuseTransport(proxy=...)` raises `E_SCOPE_REQUIRED`. The adapter always sets `CURLOPT_PROXY` to an empty string.

HTTPS and WSS use `curl_cffi` 0.16.3, `impersonate="chrome"`, and the certifi CA bundle (`verify` is that path, and `CURLOPT_CAINFO` is the same path). `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `SSL_CERT_FILE` are not the trust store. Hostname verification stays on. `curl_options` are passed to the `Session` constructor, not to `Session.request`. Redirects are not followed: a 3xx is `E_SCOPE_REQUIRED` and the `Location` value is not copied into the error. Auth bodies stop at 65536 decompressed bytes.

An optional `clock() -> float` is the monotonic deadline. The default is `time.monotonic`.

## Connection

Order: `POST /api/auth/check`, `GET /api/session`, `POST /api/hatch/token`, then `wss://hatch.metaaivm.com/v1/noise?vm_id=&auth_token=`. The hatch body is canonical JSON `{"vmAddress":"wss://{vm_id}.metaaivm.com/","vmName":"<vm_id>"}`. Tokens are quoted and must match a short printable alphabet. They are held only for the call, then cleared.

Noise pattern `Noise_XX_25519_AESGCM_SHA256`. Handshake payloads are empty. A non-empty peer payload, including an attested payload, is `E_PROTOCOL_MISMATCH`. Noise handshake, authentication, and nonce failures are the same code. They are not `E_NETWORK_TIMEOUT`.

One `ReceiveLoop` owns the socket. A second loop, a collect without that ownership, or a second call that overlaps the first raises `E_PROTOCOL_MISMATCH`. `close` takes the same call lock. Calls are serial. Stream ids are positive int64 values starting at 1. The transport stops that connection before the id would exceed `2**63-1`. It does not wrap ids on the same connection. Each collect waits at most 30 seconds of the injected clock and reads at most 64 frames. A caller cannot raise those caps. Caps elsewhere: plaintext 65519 bytes, ciphertext 65535 bytes, body 1 MiB, 32 foreign frames. WebSocket reads also stop at 65535 bytes and 64 fragments, and they check the deadline on every fragment. Unknown field, multi-chunk frame, duplicate response, a body chunk before the response, and oversize are `E_PROTOCOL_MISMATCH`. The frame budget is `E_LIMIT_REACHED`. A read that hits the deadline is incomplete.

A protocol error, an authentication failure, or a failed encrypted write poisons the link and closes it. The Noise nonce is not rewound. The next call may handshake a new connection. That new connection does not authorize the submission whose token was already consumed.

## HTTP status

| Status | Code | retry_class |
|---|---|---|
| 401, 403, other 4xx on the auth chain | `E_AUTH_REQUIRED` | `user_action` |
| 429, integer `Retry-After` of 1 to 6 digits only | `E_RATE_LIMITED` | `safe_read` |
| 3xx | `E_SCOPE_REQUIRED` | `user_action` |
| 503 and other 5xx | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 200 session JSON without `vm_id` | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 200 that is not a JSON object, or a `vm_id` outside the alphabet | `E_PROTOCOL_MISMATCH` | `never` |

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
Credential topics/questions are refused before review. Accepted text is displayed exactly;
save the preview request without edits to approve the same bytes.

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
refusal, hydration limits, and repeated uncertain settlement. LIVE/AT-038 remains blocked:
no operator-authorized account; fixture success is not service verification.


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

Packets and bundles use the shared redactor's bounded fixpoint before hashing. Still-changing text is fixed `E_INPUT_INVALID` "Packet redaction did not
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

Human `packet preview` refuses text rendered by older rules with the fixed
`E_REFERENCE_INVALID` stale-packet error before exposing it. URL userinfo stops at
the literal authority boundary (`/`, `?` or `#`); encoded characters remain inside
the mapped password span. Scheme scanning has a forward anchor and accepts custom
schemes of any length.

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

One call to `redact_text` applies the single pass at most four times (three changing
passes and one confirming pass). For every input without `redaction_unstable`,
`redact_text(redact_text(x)[0])[0] == redact_text(x)[0]`. The fourth changing
application reports `redaction_unstable`; packet/bundle rendering refuses with the
fixed "Packet redaction did not stabilize" E_INPUT_INVALID before hashing or writes.
Muse text refuses the finding; nonrefusable displays and error details mask the whole
value. There is no separate packet iteration bound.
