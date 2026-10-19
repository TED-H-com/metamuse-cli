# Muse transport and lifecycle (B09/B10)

B09 supplies the serial Noise gateway transport. B10 implements the four lifecycle
app functions, shared CLI/MCP tools and durable jobs in `_muse_lifecycle.py`.
`muse.open` and `muse.send` are external submissions; `muse.history` and
`muse.sessions` are separately authorized safe reads. `IMPLEMENTED_TOOLS` contains
only the four lifecycle tools. No background worker is started by a tool call.

The spike under `spikes/muse/` is the historical record. Production code is `muse.py` plus `_muse_codec.py`, `_muse_associate.py`, `_muse_link.py`, `_muse_noise.py`, and `_muse_http.py`. `curl_cffi` and `noiseprotocol` are imported inside those helpers on first use. Importing `metamuse.muse` does not import them. A missing install raises `E_DEPENDENCY_MISSING`.

Historical B09 (2026-10-13) had no authorized account. On 2026-10-14 the orchestrator
recorded one authorized LIVE standard msg1/msg2 observation: nested key equaled the
responder static key and nested nonce equaled the fresh client nonce. On 2026-10-17,
an explicitly authorized run completed the standard handshake and a synthetic
two-turn CLI journey with restart, cited draft and exports across corrected candidate
builds. See [the run's scope and defects](MUSE_SPIKE.md#11-authorized-live-diagnostics-2026-10-17).
Version `0.1.0a2` includes these fixes; `0.1.0a1` lacks them. The cross-build LIVE
journey is not clean-install release acceptance.

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

The daemon's session body is `{"method":"/api/session/start","params":{"origin":"fresh",
"lifecycle":"persistent","title":"..."}}`. Sends use `items: [{type: "text", text: "..."}]`,
the mapped `session_id`, a stable 16-hex project client node and empty `capabilities`.
History sends `session_id` and `limit` as URL query parameters with an empty GET
body. Upstream `request()` converts its callers' dict body to query parameters;
putting them in a wire JSON body instead can select the main chat. Native events'
nested payload session identity is preserved; conflicting outer/nested IDs stop.
Successful `{ok: true, result: {...}}` responses are unwrapped once. History accepts
`chat_events` and the older `events` field, refusing conflicting aliases or error wrappers.
Old frozen flat mutation bodies and their grants are never rewritten into new wire bytes.

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
    body={
        "session_id": session_id,
        "items": [{"type": "text", "text": question}],
        "node_id": "0123456789abcdef",
        "capabilities": {},
    },
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

HTTPS and WSS use `curl_cffi` 0.16.3, `impersonate="chrome"`, and the certifi CA bundle (`verify` is that path, and `CURLOPT_CAINFO` is the same path). `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, and `SSL_CERT_FILE` are not the trust store. Hostname verification stays on. `curl_options` are passed to the `Session` constructor, not to `Session.request`. Redirects are not followed: a 3xx is `E_SCOPE_REQUIRED` and the `Location` value is not copied into the error. Auth/mint bodies stop at 65536 decompressed bytes; session responses stop at 1 MiB.

CurlSocket serializes native send, receive and cleanup. At construction, the connector
thread duplicates curl's active socket descriptor and retains that duplicate solely for
cancellation, without changing its native flags or timeout settings. In particular, curl's
nonblocking mode and the adapter's finite idle-read deadline are preserved. Notifications
never query curl for a descriptor. Close marks the pipe closed
and shuts down this owned duplicate, interrupting receive/select or an in-progress send
without waiting for the transport lock or calling curl concurrently. A send that has not
entered the native backend is rejected; if cancellation races a call already entering it,
the socket shutdown prevents a later handoff, while any bytes accepted earlier retain the
usual uncertain/no-resend accounting. This follows [libcurl's handle ownership rule](https://curl.se/libcurl/c/threadsafe.html).

The active operation performs native `terminate()` cleanup after unwinding; an idle handle
can transfer sequentially to the closing thread. Cleanup is claimed once, closes the
duplicate, and sends no WebSocket close frame. The duplicate pins the original socket
identity, so reuse of curl's old descriptor number cannot interrupt an unrelated socket.
No cancellation of a connector-owned socket is promised before connect returns. Synthetic
adapters without a native descriptor can defer cleanup, but cannot claim native wakeup.

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
   expectation follows authenticated owner hashes and normalized VM IDs in one fixed-upstream
   trust domain. Provider/account labels cannot partition or clear it. Every observed owner↔VM
   association propagates confidentiality transitively, and new rows inherit it.
   A standard observation cannot clear the expectation.
   Connect/retry cannot reselect a different VM. Storage corruption, missing backing state,
   unsupported provenance/version or failed CAS blocks with `E_STORAGE`, retry class `never`.
4. Mint POST `/api/hatch/token` with exactly `{vmAddress: <row endpoint_url>, vmName: <row name>}`.
   Access tokens retain their separate bounded grammar. Hatch legacy tokens are single
   opaque segments (`A-Z`, `a-z`, digits, `_~+/=-`; no dots), or accept
   `s0:<base64url>.<base64url>.<base64url>` verbatim. A present non-delegation notary must have
   six nonempty base64url segments after trimming; pass it unchanged. Missing/blank omits it.
   Every present `token`, `notary_token`, `notaryToken`, `noiseNotaryToken` and `endorsement`
   is validated. Delegation, unknown/malformed endorsement envelopes and malformed/oversized
   s0 envelopes stop with `E_PROTOCOL_MISMATCH` (`never`); aliases are never silently dropped.
   A standard VM can return a server-issued `endorsement.v1` notary. Only its bounded
   six-part canonical base64url format is accepted in a notary field: object metadata
   with nonempty identity/public_key and string restrictions, two 64-byte signatures,
   and an exact `{"uri":"/v1/noise"}` destination object, without duplicate JSON keys.
   The original token goes to the gateway for verification. This is format recognition,
   not local signature verification, CVM support or permission to sign a delegation.
   Endorsements remain invalid as the hatch access token. Standard Noise key/nonce,
   TLS and durable confidentiality checks still run independently.
   Conflicting nonempty notary aliases also stop.
   No local signature/expiry verification is claimed. This is only a cache hint:
   decode the third notary segment as before; missing/undecodable/non-object JSON or
   non-list `restrictions` means no hint. Scan restrictions in order, skipping
   non-strings, strings without the exact `timeout:` prefix, and unusable timeouts.
   Parseable means Python `float()` of the stripped remainder (including its accepted
   sign, exponent, underscore and Unicode decimal-digit forms). Parse failures,
   non-finite values and values <= 0 are unusable and skipped. The first positive
   finite value decides: stale iff expiry <= wall-clock now + 60 seconds; later
   entries are ignored. No usable entry means no hint, so not stale. Mint at most
   twice, then stop if the remint is still stale.
   Credentials are minted anew per connection, held only in memory for connection-lifetime redaction and never cached durably.
5. POST `/api/hatch/vm/wake` `{vm_id, retry_count: 0}`, then guarded WSS dial with exactly
   `vm_id`, `auth_token`, optional `notary_token`, `app_id=hatch-web`, `request_id=<fresh UUID4>`.
   WSS sends fixed `Origin: https://muse.ai`; no notary header. Upgrade 502/503/504 causes
   wake `{vm_id, retry_count: n, connect_attempt_id: <fresh UUID4>}`, followed by
   `max(2s, min(0.2s × 1.5^(n−1), 8s))`. Stop after 20 redials or exhaustion of the
   240-second wake/dial/handshake budget, with `E_RESOURCE_UNAVAILABLE` and an operator action to retry later because
   the VM is not running yet. Each redial rechecks captured currentness and uses a fresh
   request ID. Remaining budget is checked immediately before each wake, dial and handshake,
   including after guard/peer validation. Exhaustion never passes a nonpositive curl timeout
   or sends msg1; it returns `E_RESOURCE_UNAVAILABLE` with an operator retry action.
   Other failures do not enter this redial loop. The 240-second clock starts after mint;
   it is not an end-to-end connect deadline. Claimed Muse trust transactions retain core's
   30-second SQLite busy timeout (unclaimed worker maintenance uses a separate one-second
   scope). Trust currentness checks use `BEGIN IMMEDIATE` even though they inspect state.
   A continuously held WAL writer stops the first freeze/currentness transaction after
   its busy wait; that error does not trigger the three-connect invalidation retry.
   Auth/session and up to two mints precede the clock. An in-budget trust wait can overrun
   it, and the final currentness check follows the handshake. For stable WAL and adapters
   honoring their timeouts, conservative budget accounting is 410 seconds per fresh
   connect: 80 seconds bootstrap HTTP, 60 seconds pre-budget trust waits, and 270 seconds
   for the 240-second phase plus a possible 30-second check. Three notification-driven
   connects total 1230 seconds; an initial cached-loop recheck adds up to 30 (1260 seconds).
   These are timeout contributions, not wall-clock guarantees: file/RLocks, fsync, schema
   work, DNS/guard callbacks and host scheduling have no end-to-end deadline here.
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
   holding trust-domain/per-account locks across commit and msg3 handoff. Release the shared
   maintenance lock after commit and before network handoff. Split only through the
   mature library. After commit, every confidential write notifies all live workspace
   listeners and immediately closes their sockets, including unrelated labels. This covers
   new confidential account, VM or observation rows and every 0→1 flag transition,
   including inherited edges and SQL propagation. A failing listener does not stop the
   remaining listeners; after all run, the writer reports
   `Muse security state internal failure` chained from the first exception, while the write
   remains committed. A non-direct freeze with no confidential write does not broadcast;
   a binding/generation change still notifies its own label.
   Each call first rechecks durable currentness. A refused context closes and stops before
   any HTTPS request or WSS dial. An unaffected context whose socket was closed by a
   broadcast closes its old receive loop and reconnects through auth, session, freeze,
   mint, wake and the full handshake in that same call. Immediately after freeze returns,
   clear invalidation and register the listener, before mint, wake or opening a socket.
   Registrations are de-duplicated per transport and label key. Any workspace-confidential
   or own-label notification after registration that wins before application handoff forces
   a fresh connect in this call before its first application frame, including a notification before loop publication or during
   the final durable check. Track the returned candidate from connector return through
   Noise, including a blocked msg2 read, until loop promotion or actual failure cleanup.
   Notification closes both the owned candidate and any published socket without taking
   the transport lock. If notification precedes candidate registration, close the returned
   link before handshake use; a socket still owned by `connect()` cannot be cancelled
   before that method returns. Cleanup of an old attempt never closes a later candidate.
   An expected invalidation close rechecks the captured snapshot before retrying: an
   unaffected context uses the same bounded fresh connect, while a durable storage or
   protocol refusal stops before any further HTTPS/WSS work. Real verification/storage
   failures keep their classification. After publishing the loop and checking currentness,
   check invalidation again. Allow at most three connects per call. Exhaustion returns
   the fixed `E_NETWORK_TIMEOUT` / `safe_read`, `Muse connection failed`, with action
   `Retry the connection. No gateway request frame was sent.` A mutation then reports
   never-sent through `MuseNotSubmitted` or the `chat.stream` `failed_before_send` attempt.
   No standard enrollment-health or capability-registration gate is added.

Every call, including reuse of an open loop, hands its first application request to
`muse_application_handoff` under the existing trust-domain/account boundary. It checks
the **old** snapshot, commits the check without changing msg3 provenance, and releases
SQLite and `maintenance.lock` before the invalidation check and `open_request`/send.
Trust writes and their notifications share that boundary. If a write wins first, an
unaffected context reconnects before any application frame; a changed binding or
confidential/refused context stops before fresh HTTPS/WSS or application work. Connect
and pre-send recovery share the same three-attempt budget, and authorization is consumed
once. A true pre-send refusal yields `MuseNotSubmitted` for session.start or
`failed_before_send` for chat.stream. Storage/protocol failures stop rather than retry.
If application handoff wins first, a later write is an in-flight case: bytes already
handed off cannot be cancelled. Send uncertainty is never replayed. The trust locks span
only validation and the first send, not response collection; callbacks remain lock-free
with respect to the transport lock. Callback exceptions leave the Store boundary with
their original transport meaning.

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
200 (spike §2/§5 bootstrap); wake accepts 200 (observed in an operator LIVE probe; evidence kept outside this repository), plus 202 and 204 as
explicitly authorized in the round-2 operator contract. 202/204 indicate accepted/no-content
activation, not a running VM. They were not observed LIVE by this task. Wake success needs
no JSON body. Read/mint success still requires bounded valid JSON and its own field checks.

| Status | Code | retry_class |
|---|---|---|
| 401, 403 | `E_AUTH_REQUIRED` | `user_action` |
| 429, integer `Retry-After` of 1 to 6 ASCII digits only | `E_RATE_LIMITED` | `safe_read` |
| 3xx | `E_SCOPE_REQUIRED` | `user_action` |
| HTTPS 5xx; WSS 5xx after its bounded redial (only 502/503/504 redial) | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 404, 409, 410 on session or wake | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| Other unexpected 2xx/4xx or unsupported response shape | `E_PROTOCOL_MISMATCH` | `never` |
| 200 explicit `ok: false` on auth | `E_AUTH_REQUIRED` | `user_action` |
| Twice-stale notary after one remint (MUSE_SPIKE §9.1 one-remint stop; unavailable/retry-later is an inference) | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 200 session status `unavailable`, or no identity key, without confidential hints | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| CVM/RV/delegation/unsupported endorsement/malformed s0/crypto/key/nonce mismatch | `E_PROTOCOL_MISMATCH` | `never` |

Upgrade 401/403 is `E_AUTH_REQUIRED`, 429 is `E_RATE_LIMITED`; DNS/connect/TLS failure
is `E_NETWORK_TIMEOUT`, with `E_SCOPE_REQUIRED` when the guard refuses. Trust contention is `E_STORAGE` / `safe_read`; environment failures use `user_action`.
Changed context, integrity and committed-barrier failures remain `never`, with fixed guidance
for reconnecting, verified restore or safe repair respectively. Effective confidentiality stops first. The upgrade URL, query, response body and raw
curl exception are never copied into any exception, envelope or log. Redaction covers
the tested Unicode component nonce/token/proof/secret/notary/noise-key/attempt-id/request-id/recovery
families and existing RV/endorsement/wake aliases, including prefixes/suffixes, snake/camel/hyphen/dotted
keys in query, header, repr, JSON, arrow and XML forms. Ordinary vm_id/discussion_id/job_id remain visible.
Known values match the shared original/canonical views under its existing decoding budget;
Muse also retains raw and two-percent-level coordinates (including quote_plus and lowercase escapes),
then scrubs the union of matching spans. Alias scanning includes full WSS URLs; an ordinary
scheme or identifier cannot consume a later query key. Text requiring no redaction preserves its
original percent encoding. Raw envelopes and embedded wake bodies are scrubbed.
Base32, colon-separated hex and bytes repr are outside this tested scope.
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

The verdict is classified on the original bounded body, then a separate public body is scrubbed.
Complete known-value ranges are selected before public-key placeholders or any destructive mask,
and projected through those sparse spelling edits. The shared `redact_text` additive hook combines
known values and codec Bearer credential spans from its original/canonical views with complete
header folds before rendering; it reuses the existing decoder budgets and mappings. Muse's bounded
syntax fallback follows that scan. Muse opts into the closed legacy `redacted` marker for additive
selections; independent shared masks keep their own replacements, and covering structured masks
retain valid source-layer JSON. Known values override public-key exemptions, including private
key fragments, while independent public boundaries remain visible. Undecodable bytes are not returned.

A broadcast can also interrupt an exchange already waiting on a socket. It closes the
socket without replaying the request. An incomplete read returns `E_NETWORK_TIMEOUT`;
a handed-off `chat.stream` retains its partial/uncertain attempt, marker, digest and
stream id, and a handed-off `session.start` raises `MuseStateUnknown` for reconciliation.
Protocol failures keep their existing `never` handling. A later call rechecks durable
state before reconnecting; it does not resend the interrupted mutation. Mutation
`consume` runs once before `_connect`; this call's application frame is opened only
in `_unary`, after reconnect completes, so reconnecting an idle, unaffected socket
neither reauthorizes nor sends this call twice.

After a mutation handoff, a read failure does not use safe-read retry guidance. `chat_stream` returns an uncertain attempt and keeps the marker, digest, and stream id. `session.start` raises `MuseStateUnknown` (`E_EXTERNAL_STATE_UNKNOWN`, `reconcile`). A protocol failure after handoff raises `MuseProtocolStop` (`E_PROTOCOL_MISMATCH`, `never`) and still carries that evidence. Only a read, or a failure proven to be before handoff, may use safe-read retry guidance. A raised network timeout does not mean the frame was not handed off.

`chat_stream` does not raise for a timeout, a non-2xx status, `ok: false`, or a body that is not a JSON object. Those stay `send_uncertain` after handoff, with `app_error` or `undecided` on the evidence. `failed_before_send` is only when no frame bytes were handed off. A later user echo of this exchange promotes the state to `sent` and leaves the evidence row in place. A recognized marker that is not this request disqualifies the event even when another marker matches. Pass the real `Exchange` and the history events into `chat_stream` when they are already known.

`delivery_state`, `attempt_evidence`, `associate_reply`, `observe`, `Exchange.earlier_unresolved`, and `ObservationMemory` are re-exported from `metamuse.muse`. `CALIBRATED_COMPLETE_STATUSES` is empty. `ambiguous` is an association result, not an exchange state. `closed` does not reopen. The transport never resends.

For a native user echo, exact `payload.content` takes precedence over the
presentation field `display_text`; the latter is a fallback only when content is
absent or empty. A conflicting request marker still disqualifies the event.
Assistant display text remains available for presentation. This distinction was
observed during the authorized synthetic LIVE run; it does not calibrate a
completion status or declare the research finished.

## Schema

B09 migration `005_muse_trust.sql` is amended in place for round 4, with no 006.
Local account/VM tables retain frozen connection currentness; label-independent
`muse_trust_observations` records authenticated owner hashes and normalized VM IDs.
One fixed-upstream trust domain spans all provider/account labels. Its recursive closure
and 21 SQL guards propagate confidentiality transitively and monotonically, set-wise
without trigger depth growing with the component. Three explicit indexes support lookup.
A committed propagation barrier, changed schema, corruption or unavailable state blocks.
No public model/schema change is needed. Fresh-v5, v4→v5 and backup/restore are tested.
An older development-v5 workspace is noncanonical and has no implicit reset/upgrade.
Rollback restores a verified v4 backup into a fresh workspace. Restoring any older backup
**discards confidential expectations recorded after that backup**, including during v4
rollback. No in-place reset tool exists.

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

The daemon `items[0].text` is exactly `question + "\n\n" + packet.rendered_text`; the two host
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
Association computes the sanitized outbound view before the write transaction.
Settlement verifies the stored outbound body is unchanged and reuses that view;
no redaction runs while the write lock is held.
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

<a id="bounded-research-workflow-synthetic-evidence"></a>

## Bounded research workflow

Use exact `permit approve` for a verbatim action. For a few host-authored questions
with one fixed packet, the human may instead approve one bounded research scope.
This workflow has synthetic coverage and one authorized real-account CLI journey
using a synthetic packet. That LIVE run crossed candidate builds while protocol
defects were fixed; it does not verify a fresh installation of the published alpha.
The existing CLI/MCP requests, worker and durable discussion/exchange/results remain
the same.
Credential-bearing new topics/questions are refused by the shared Muse checks. A legacy
packet that fails the current preview redaction check needs a new packet before approval.

1. Import permitted sources (`add --file PATH --kind text --project PROJECT
   --request-key import --run`, or the existing MCP collect). Search, select exact
   source evidence refs, then `packet prepare --project PROJECT --selection-file
   selection.json --request-key packet`; inspect `packet preview PACKET`. Source
   methods, revisions and missing facts stay visible; Muse answers are not sources
   for a new packet.
2. Save an existing new-open request in `open.json`:

   ```json
   {"project_id":"PROJECT","topic":"Synthetic activity review","request_key":"research-open"}
   ```

   No grant or reconciliation discussion_id belongs in this file. The human runs:

   ```sh
   metamuse --workspace /path/to/workspace --json permit research \
     --open-request-file open.json --packet PACKET --rounds 2 --max-reads 4 \
     --expires-at EXPIRY_WITH_TIMEZONE --request-key research-approval
   ```

   Choose an absolute expiry in the next 24h. Review the quoted topic/open key,
   configured account/provider/backend, project/discussion, packet and selection
   hashes, one open plus read ceiling, question ceiling and expiry. Terminal `yes`
   confirms, or a matching `--exact-digest` confirms the whole displayed research
   scope in a human non-TTY CLI. The digest includes all bounds and the actual open
   payload; it is not the old open action digest. A missing confirmation creates
   nothing and returns the review digest for inspection. Agent/MCP never self-approve.
3. Keep the returned `access_grant_id`, `share_grant_id` and `discussion_id`. Call
   `muse open --project PROJECT --topic "Synthetic activity review" --request-key
   research-open --access-grant ACCESS`. The key and topic must match open.json.
   Accepted means queued; run the existing worker or use MCP's attached worker and
   inspect job completion before sending.
4. Call `muse send --discussion DISCUSSION --packet PACKET --question-file first.txt
   --share-grant SHARE --request-key question-1`, then `muse read --exchange EXCHANGE1
   --refresh --access-grant ACCESS`. Wait for the returned job, and read locally to
   obtain the stored reply/evidence refs. Close the host outcome with
   `muse close-exchange EXCHANGE1 --outcome needs_more_data --request-key close-1`.
   Repeat with a different question file/key and the same packet and grant IDs.
   A live baseline history read uses the same ACCESS via the mapped discussion's
   existing `muse open --discussion DISCUSSION ... --request-key baseline` route.
5. After restart, read the original exchange IDs locally; refresh uses the same
   ACCESS while it has reads/time remaining. Unknown open/send means reconcile the
   original intent using these read routes, never send again or reset counters.
6. The host writes the draft, citing original packet source refs plus each relevant
   read observation's exact `evidence_ref` (`muse_observation`, revision 1). Use
   `draft save --project PROJECT --platform generic --text-file draft.txt
   --facts-file facts.json --request-key draft`, then `draft preview DRAFT --revision 1`.
   Put the returned draft ref (`draft_revision`, ID, revision) in export-refs.json;
   `export --project PROJECT --refs-file export-refs.json --format bundle
   --request-key export` creates local files/manifest. Check citations and factual
   support; an existing reference alone does not establish support. Export does not
   publish. Fixture-derived drafts/exports retain mock/backend/research_complete tags.

Research means explicit consent to varied host-authored questions on the displayed
topic with one immutable packet. The tool enforces identities, packet/revisions and
budgets, but cannot prove arbitrary question meaning or that free text contains no
other data. The host must stay on topic and add no other data. New packet/data revision,
project, discussion/topic/open key, provider/account/backend needs new human scope.
No collect/post/reply/social/external-publishing permission is granted. Maxima are
1–20 rounds (also workspace max_exchanges), 1–200 remote reads and 24h. The access row's
max_uses is reads + one open; the share row's max_uses is rounds. Local reads are free;
reserved safe-read retries recheck current validity without another use. The pair's
receipt replays the original IDs/current counters, with no refill or renewal. Either
row can be revoked with existing `permit revoke`; inspect both with `permit list`.
Changed/expired/revoked/exhausted grants refuse queue and send/read boundaries. New
questions never reset usage, and unknown delivery never refunds or authorizes replay.

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
before its batch, then cuts to 512 characters. Association sanitizes its locally bounded outbound view (48 KiB packet, 8 KiB
question, two joining bytes) before the transaction. Settlement compares the complete
stored outbound body with the frozen exchange, then reuses that view. No redaction
or sanitize_document call runs inside the jobs transaction. Oversized legacy text refuses before redaction with
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

Reconciliation preparation and receipt replay use one topic-identity predicate:
the stored title or its current display-redacted form, together with discussion ID
and project. Replaying the human-approved request, before or after settlement, and
re-previewing an existing job retain the original job identity. A saved display title
that no longer matches current rules requires requesting reconciliation again.

Layered grammar details: a bracket or separator inside a quoted value cannot close
an outer container or turn into a call-argument separator. An actual enclosing quote
still closes that value even when the quote belongs to a deeper decoded layer.
Raw replay recognizes the whole escaped closing quote, preserving whole-value
exemptions inside JSON captures. Shell quoting of option names, including curl
--user/--cookie, sshpass -p and attached mysql -p, retains the argument rule.
A whole form field is decoded before lexing a HAR document it contains, including
whole percent-encoded form bodies. The HAR lexer never decodes string data into
structure. A failed container stops at its first error; independently indexed later containers remain eligible, even after prose or a damaged outer candidate.

Known form boundaries are carried through decoded layers, including encoded newlines.
An encoded nested form prefix gets its own operator layer within its parent field;
its plus signs decode only at that layer or shallower. The scan never grants form
semantics to unrelated prose after a question mark. Whole escaped quoted placeholders
are canonicalized before applying the same closed exemption list; their nested
assignments remain independently inspected. Public-key block mirrors stay public.

B16a structural rule: ambiguity may only lengthen a redaction. Single-line single,
double and backtick quotes pair locally at the same decoding layer and never
cross a line break at that layer. An apostrophe between alphanumerics is prose,
not a delimiter. Triple quotes are one multiline token, recognized before single
quotes; heredoc bodies and YAML block scalars retain their multiline extent.
A fully parsed JSON layer proves exact string extents. Only a delimiter proved
by these rules may shorten a credential value, URL authority or private-key block.

At a credential value's start, a single, double or backtick quote opens its own
value if the same-layer, same-line matching quote follows. This local opener
takes priority over an unrelated earlier quote. Attached command flags use the
same rule; only a proven quoted flag's closing delimiter precedes its argument.

Exemptions require a complete genuine closing delimiter at the same layer.
Forced closure at an enclosing quote or end of input is not a match. Unclosed
placeholders and lookups are credentials. Fixed environment lookups have locally
quoted arguments, so parentheses or brackets within an argument are data. Lookup
and placeholder extents are indexed once; nested lookups never repeatedly copy
or decode a suffix per assignment.

Every scanner continues inside an exempt outer value. Assignment, header, XML,
query, curl and SQL/sshpass/mysql passes advance only past the matched key or
operator until masking a value. Cookie-chain discovery likewise revisits exempt
single members. URL candidates always resume at the next candidate; HAR walks
all string/container children; jar, prefix, JWT, block and path passes enumerate
independent matches. Only a selected redaction permits skipping its consumed value.

Protocol-relative // authorities are URL candidates, independent of preceding
URLs. A scheme or // exposed at a deeper layer inside a password remains value
data. A root candidate with no userinfo operator before a deeper scheme still
yields to that independently scanned URL. One monotone successor cursor per
bounded layer prevents rescanning. Markdown autolinks end at their proven > in both authority and query
passes. A raw backslash ends a special-scheme authority even when a permissive
decoded view would treat its escape as data. Accepted benign cost: an unenclosed
scheme://host:port,user@host contains WHATWG userinfo, as does a literal backslash
in a non-special scheme's userinfo; both are redacted by design.

HAR JSON containers are candidates at every offset and decoded layer, including
prose, fences, log prefixes and packet headers. A strict lexer evaluates completed
objects before an error; an independent candidate swallowed as string content is
not lost. Lexical ends and container outcomes are memoized once, so late failures
and candidate recovery remain linear. Any sensitive repeated name marks every
value in that object private, without overwriting an earlier sensitivity decision.

Private-key block labels accept space, form plus and percent-encoded space at
every supported layer. Quotes in PuTTY comments shorten the block only when
proved to close an enclosing string; otherwise it extends through Private-MAC.
Quoted form-body discovery pairs quotes per decoding layer, and decoded-only
whitespace remains form value data.


B16a context assurance: natural and seeded malformed contexts surround in-scope
rows both inside and outside raw, JSON (one/two), percent and form layers.
Representations stay within the supported decoding budgets: a row already using
three percent layers retains its context in raw/JSON form instead of receiving a
fourth percent/form wrapper. Count these representation adjustments; no natural
context is excluded. Whole-input peeling preserves percent escapes inside URL
password data.
The property checks marker fragments as well as whole values, and checks the
one-call fixpoint. Natural-context benign mirrors must remain unchanged;
malformed random-context benign changes are measured, since ambiguous structure
may lengthen a mask. The installed CPython corpus retains a 0.40% refusal budget.
HTML/XML elements make value/content private when the same element has a sensitive
name; type=password makes value private. Attribute order and quoting do not
change that rule. Benign username/description elements remain unchanged.


Grammar-proven HTML/XML attribute strings may contain line breaks; their local
matching quote is indexed separately from prose quote pairing. Attribute name/type
classification resolves HTML character references. Oversized (over 1,024-character)
name/type selectors fail closed before decoding. Exempt attribute values remain
searchable for independent nested elements. A malformed unquoted nested tag starts
an independent candidate instead of causing a repeated suffix walk.

A shell-parameter placeholder must have a complete balanced closing brace and a
parameter header (identifier/positional/special parameter, optionally followed by
its parameter operator). Random prose before the brace cannot turn an incomplete
credential into a valid placeholder. Fixed lookup and complete double-brace extents
are indexed once; exemption checks do not copy an enclosing suffix per assignment.
Exact JSON string boundaries stay mapped through form/percent/JSON decoding;
quoted defaults cannot jump over a proven enclosing JSON close. Raw special-scheme
backslash barriers exclude serialization backslashes and Unicode-escape data.
Netscape jar fields remain recognizable after a surrounding context prefix and
retain CR/LF line endings. These rules preserve the existing four-application bound.


Round-7 extents use delimiter ownership D1–D11 in 02_SPEC and SAFETY_API.
An unrelated quote, plural possessive or bracket never shortens a credential.
Only the value's own quote, an immediately adjacent opener, or a proven JSON
string supplies a closing bound, with the D2 follower test. Escapes, doubled
quotes and shell quote idioms remain value data. Unquoted trailing punctuation
is excluded before one shared, layer-true whole-value exemption decision.
Shell reference defaults remain exempt; literal defaults are credentials.
Markup comments and attributes are bounded contexts. Private blocks retain their
final MAC/hash/END line, YAML indicators accept indentation digits and comments,
and embedded forms continue through quotes and angle brackets. Short curl flags
use conservative lexical scope within a simple-command segment, including pasted
prose; additional benign refusals are intentional. This does not parse shell
command-word position. Escaped continuations and separators in owned preceding
arguments preserve curl scope; actual command separators release it, including
after consumed private values. SQL principals must precede their clauses within
the same statement. See D10 in [SAFETY_API.md](SAFETY_API.md) for source-layer
escape preservation and the forward boundary state.
Per-scanner indexes keep terminator and exemption work linear; the public fixpoint
call retains its bound.
Remote trust uses one fixed upstream (MUSE_ORIGIN/HATCH_WSS_BASE). Provider and account are local connection labels; neither partitions owner or VM expectations. The shared trust-domain lock serializes all labels.

HTTP 200 with missing, malformed or drifted credential/context fields is interface drift: E_PROTOCOL_MISMATCH / never. Explicit `ok: false` and HTTP 401/403 remain E_AUTH_REQUIRED. A twice-stale notary after one remint is E_RESOURCE_UNAVAILABLE with a retry-later action (MUSE_SPIKE §9.1 supports one remint then stop; unavailable/retry-later is an inference, with no evidence that session refresh repairs it).

Authenticated CVM hints commit label-independent owner/VM observations before binding, generation, status, uniqueness or selection refusals. Session-level evidence records every usable VM id, including non-selected rows; nested evidence records the union of its enclosing identity scopes, as specified below. Owner mismatch never drops that observation; the existing local binding is still refused.

Round 3 amends unreleased migration 005 in place (no 006). Label-independent authenticated observations and label-bound selection history form an owner↔VM graph. The recursive `muse_trust_confidential` view computes its transitive closure, with owner-only evidence as an isolated seed. Each propagation trigger updates matching account, VM and observation rows set-wise. A transaction-local `muse_trust_propagating` barrier suppresses nested propagation; a committed barrier row is integrity failure and blocks connects. Component size does not grow trigger depth; there is no identity cap on recording confidentiality. The flag never clears, and a newly observed edge inherits both ends' effective expectation.

Msg2 is decrypted up to the Noise message maximum of 65535 bytes. RV discrimination uses the full authenticated plaintext before the standard-only 8192-byte cap; oversized RV evidence remains permanent for that owner and VM, without a reset tool. Session JSON is bounded at 1 MiB (auth/mint stay 64 KiB). A session response above 1 MiB returns E_LIMIT_REACHED naming the session response, records nothing and never dials.

Per-connection scrubbing includes uppercase/lowercase nonce hex, padded/unpadded standard and URL-safe base64, and uppercase/unhyphenated WSS request and wake attempt UUIDs. Alias values are scrubbed in JSON, Python reprs, headers, colon lines and spaced assignments. Known values are replaced longest first, including their percent-encoded variants.

CVM hint keys and values use the shared Unicode component normalization described below. A component starting with cvm or containing confidential records permanent remote expectations, subject to the marker-key falsy rule and opaque-credential skip. Snake, camel, hyphen, compatibility and ignorable variants follow that component rule; joined cross-word spellings do not. This is refusal evidence, never permission to connect to CVM.

Trust is monotonic in place. Restoring an older backup, or rolling back to the documented verified v4 backup in a fresh workspace, discards confidential expectations recorded after that backup. A stale generation or changed connection binding returns E_STORAGE / never with a fresh-connection retry action; it does not advise restore. Integrity/schema failures advise repair and only a verified restore, with this loss warning. There is no in-place reset tool.

2026-10-14 round 4: an authenticated hint bind inserts its transaction-local barrier, bulk-inserts all observation and label VM rows, materializes the recursive closure once, updates the three tables set-wise, removes the barrier and commits. There is no per-id closure or recording cap. A separate small transaction checks the label after this independent evidence commit. Raw-SQL propagation and inherit guards remain active outside that batch.


Round 4 storage mapping: SQLITE_BUSY/LOCKED past busy_timeout returns E_STORAGE / safe_read, “Muse security state is busy; retry”. READONLY identifies a read-only workspace and FULL a full disk, both user_action. Recording failures retain “Muse confidentiality could not be recorded” with the same fixed environment guidance, and stop before msg3. Integrity/schema failures alone advise verified restore with the later-expectations loss warning.

A committed muse_trust_propagating marker is always invalid and has a specific E_STORAGE message. Safe human repair, after stopping Muse connections: acquire exclusive maintenance.lock, BEGIN IMMEDIATE, retain the barrier, rerun the recursive owner/VM closure and the three set-wise flag updates across the whole workspace, DELETE the marker, then COMMIT. This retains every observation and expectation; no restore is needed. There is no automatic repair/reset command.

HTTP 200 session status must be exactly `assigned` or documented `unavailable`. A missing, renamed, upper-case, non-string or other status with a VM id is interface drift: protocol_stop(), E_PROTOCOL_MISMATCH / never. No identity key remains E_RESOURCE_UNAVAILABLE; a compact-vmid alias without exact vm_id is protocol drift before the unavailable check. Non-object responses name session, hatch or auth in their stop action.


Round 4 amends unreleased migration 005 again, with no 006: muse_trust_accounts now checks lower-case hexadecimal viewer/session hashes and normalized nonempty active VM ids, or NULL only with confidentiality set. The same constraints are verified on fresh v5, v4→v5 and confidential backup→restore. Noncanonical development v5 databases still fail schema verification.

Round 4 provenance design: source and observed_at are frozen once an observation or label VM row has confidential_required=1, including later direct evidence. A direct hint/RV may stamp a prior cr=0 observation during its transition to 1; a standard freeze never restamps a cr=1 row. Inherited propagation retains the association’s prior provenance, rather than claiming direct CVM evidence. Existing immutable guards enforce this metadata rule; no first-cause column is needed because nothing overwrites confidential provenance.


Session and selected-row keys whose compact form is `vmid` must all be valid and
normalize to one VM identity, independently of key order. Name, endpoint and the
persistent trust key use that identity. The exact server `vm_id` may be retained
on the wire only after that agreement check (case and surrounding whitespace
are the only permitted differences). A confidential hint records every usable
identity alias before any disagreement is refused, before mint, wake or dial.

Muse sanitization includes the codec's cookie, bearer and credential-pair
shapes, and treats Authorization and Proxy-Authorization scheme plus credential
as one value. Known secrets are matched in both raw text and up to two decoded
percent-encoding layers; their coordinate-mapped span union is merged before
one output join. A literal percent next to a hex nonce or UUID cannot hide it.

Muse credential keys and confidential markers share one Unicode component helper:
remove Cf and Default_Ignorable_Code_Point, apply NFKC, split non-alphanumerics,
letter/digit, lower/upper and acronym boundaries, then casefold. Credential keys
match any nonce/token/proof/secret/notary/recovery/psk/passwd component (also plural),
ordered noise…key, attempt…id, request…id, rv…key, wake…body or access…key pairs
(second component may be plural), exact extra keys, or the shared core classifier.
Pairs need not be adjacent. Association keys session_id/request_marker/x-request-id/
client_request_id, vm_id/discussion_id/job_id and documented usage token counts
(including the usage prefix, such as usage.prompt_tokens) are exempt by compact spelling.
Arbitrary credential prefixes such as secret.prompt_tokens are still sensitive. Known secret values
remain scrubbed under every key. Tokenizer, secretary, proofread, awake, handshake_ms
and wake_time contain no family component and remain visible; core policy stays sensitive.

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

Round 5 storage classification (all paths fail closed before msg3):

| Primary SQLite class / cause | Fixed guidance | Retry class |
|---|---|---|
| IOERR family, CANTOPEN, PERM, NOLFS; lock EACCES/EPERM/EIO/ENOENT | Check workspace availability and permissions | user_action |
| READONLY family / lock EROFS | Make the workspace writable | user_action |
| FULL / lock ENOSPC | Free disk space | user_action |
| BUSY/LOCKED family including BUSY_SNAPSHOT | Muse security state is busy; retry after writer finishes | safe_read |
| PROTOCOL, NOMEM, INTERRUPT | Retry after a transient security-state failure | safe_read |
| CORRUPT, NOTADB, SCHEMA, CONSTRAINT; invalid trust-row type | Repair; restore only a verified backup, losing later expectations | never |
| Committed propagation barrier | Stop connections; use the documented closure-only maintenance repair, no restore needed | never |
| ERROR, MISMATCH, TOOBIG, RANGE, FORMAT, INTERNAL, MISUSE, ABORT, EMPTY, NOTFOUND, NOTICE, WARNING, AUTH, unexpected Python exception | Muse security state internal failure; report this failure | never |

Recording paths retain “Muse confidentiality could not be recorded” and the same
class-specific next action. No SQL, private paths or raw exception text is included.
Migration 005 checks stored integer generation/version/flags, text ids/hashes and
nonempty text timestamps. SQLite affinity precedes CHECK, so integer-coercible
inputs are accepted only as stored integers. Invalid stored row types are damage.

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
compositions). Known raw and twice-decoded percent-coordinate spans are united with
the shared original/canonical selections before masking; each selected union is
rendered with one join. Bodies are never truncated before redaction.
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

MEASURE (2026-10-14, shared host, warmed schema): an exactly 1 MiB authenticated
session containing 52,420 ids bound in 0.741 s CPU / 0.762 s elapsed; bulk trust
domain lock held 0.479 s, post-commit check 0.007 s. Host load near this run was
2.50/1.90/2.85 (1/5/15 minutes). This fixture measurement is not a wall-time
promise. After one/two/four unrelated maximum binds, hint/RV/inherited freeze/
standard cycle CPU ratios were 0.99–1.05 of empty baseline. A new label for an
owner's own 52,420-VM component cost 0.846 s CPU; that operation pays its component.

MEASURE: shipped marker predicate, seeded random base64url, 4,000 trials per length:
32 chars 3/4000 (0.075%); 256 4/4000 (0.100%); 1024 24/4000 (0.600%);
4096 102/4000 (2.550%). These synthetic rates are not predictions for real session
fields. Credential values are opaque to marker matching.

Before-frame session.start failures preserve the original fixed error code,
message, next action, retry class and retry delay when wrapped as MuseNotSubmitted.
SQLite BUSY/LOCKED therefore retains safe_read advice; this is affirmative
not-submitted accounting and never permits automatic replay of an uncertain send.
