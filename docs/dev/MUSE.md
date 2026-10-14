# Muse transport (B09)

B09 is the gateway transport in `src/metamuse/muse.py`. It authenticates, opens one Noise_XX WebSocket, and performs serial unary calls on four routes. It returns attempt evidence. It does not open a discussion, write the database, or call `open_muse_discussion` / `submit_muse_exchange`. Those four feature functions still raise `E_UNSUPPORTED`. `IMPLEMENTED_TOOLS` is empty.

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

## What B10 must do before a mutation

The transport does not mint a core token and does not reserve a permit. B10 owns that boundary. The remaining step is to bind `SubmitAuthorization` to the core `ExternalActionToken` minted in `jobs.py` after `before_external_action` commits.

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

This build does not add columns and does not apply a migration. The core owner adds that migration, registers it, and regenerates the schemas. Do not edit `001_initial.sql`. Do not treat a default of `bytes_handed_off=0` as proof that nothing was sent: a crash after `submitting` and before the transport result leaves delivery unknown. B10 persists the attempt evidence this module returns. It does not rewrite an `app_error` into `acked` from a later history page.
