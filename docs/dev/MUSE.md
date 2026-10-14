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

The executor helper `StepContext.before_external_action` lives in `jobs.py`. This transport cannot observe that call. B10 owns the precondition:

1. Persist the exchange and reserve the permit.
2. Call `step.before_external_action(target, verify=...)`.
3. Only after that call returns, call `session_start` or `chat_stream` with `external_submit=True`.
4. Pass `network_mode` from `ctx.policy.app.network_mode`.

`external_submit=False` raises `E_SCOPE_REQUIRED` before I/O. The flag is not a second permission check. It is B10's statement that step 2 returned. Reads (`sessions_list`, `chat_history`) do not take the flag. A read is not permission to send again.

`network_mode="offline"` raises `E_SCOPE_DENIED` with the same message as `Executor.require_network`, before credentials are read and before any URL is requested. The default mode is `offline`.

## What B08 must inject

```python
class CredentialProvider(Protocol):
    def cookies(self, auth_ref: str) -> str: ...


class OutboundGuard(Protocol):
    def allow(self, url: str, *, purpose: str) -> AddressPin | None: ...
```

`cookies` receives the opaque `auth_ref` from the discussion row and returns one cookie-header value. The transport does not read a browser profile or a cookie file. `allow` runs before every HTTPS URL and before the WebSocket URL. Return `None` to use the URL as written, or an `AddressPin(host, port, addresses)` that the adapter places in `CURLOPT_RESOLVE`. Raise `CoreError` to deny. An empty address list raises `E_SCOPE_REQUIRED` and does not connect.

With no credential provider the transport raises `E_AUTH_REQUIRED`. With no guard it raises `E_SCOPE_REQUIRED`. Neither default connects.

`purpose` is `auth.check`, `session`, `hatch.token`, or `wss.noise`. The guard sees the real WebSocket URL, including `auth_token`. That value must not be logged by the guard.

The adapter is `curl_cffi` 0.16.3 with `impersonate="chrome"` and `verify=True` on every HTTPS call and on `WebSocket.connect`. Verification is never turned off. Redirects are not followed: a 3xx is `E_SCOPE_REQUIRED` and the `Location` value is not copied into the error. `trust_env=False` is set and, because that flag is stored but not read by curl_cffi 0.16.3, the adapter also sets `CURLOPT_PROXY` to an empty string unless the caller passed an explicit `proxy`. Environment proxy variables are not used. Pins that cannot be applied are a failure, not an unpinned connection.

An optional `clock() -> float` is the monotonic deadline. The default is `time.monotonic`.

## Connection

Order: `POST /api/auth/check`, `GET /api/session`, `POST /api/hatch/token`, then `wss://hatch.metaaivm.com/v1/noise?vm_id=&auth_token=`. The hatch body is canonical JSON `{"vmAddress":"wss://{vm_id}.metaaivm.com/","vmName":"<vm_id>"}`. Tokens are quoted and must match a short printable alphabet. They are not stored on the transport.

Noise pattern `Noise_XX_25519_AESGCM_SHA256`. Handshake payloads are empty. A non-empty peer payload, including an attested payload, is `E_PROTOCOL_MISMATCH`. The transport stops. It does not weaken TLS.

One `ReceiveLoop` owns the socket. A second loop, a collect without that ownership, or a second call that overlaps the first raises `E_PROTOCOL_MISMATCH` (`concurrent collection` or `second receive consumer`). Calls are serial. Stream ids start at 1 and increase by one. Each collect waits at most 30 seconds of the injected clock and reads at most 64 frames. Caps elsewhere: plaintext 65519 bytes, ciphertext 65535 bytes, body 1 MiB, 32 foreign frames. Unknown field, multi-chunk frame, and oversize are `E_PROTOCOL_MISMATCH`. The frame budget is `E_LIMIT_REACHED`. A read that hits the deadline is `E_NETWORK_TIMEOUT`.

## HTTP status

| Status | Code | retry_class |
|---|---|---|
| 401, 403, other 4xx on the auth chain | `E_AUTH_REQUIRED` | `user_action` |
| 429, integer `Retry-After` only | `E_RATE_LIMITED` | `safe_read` |
| 3xx | `E_SCOPE_REQUIRED` | `user_action` |
| 503 and other 5xx | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 200 session JSON without `vm_id` | `E_RESOURCE_UNAVAILABLE` | `user_action` |
| 200 that is not a JSON object, or a `vm_id` outside the alphabet | `E_PROTOCOL_MISMATCH` | `never` |

The response body is not copied into these errors. Unavailable VM is not an auth failure.

## Attempt evidence

`chat_stream` returns a `StreamAttempt`:

| Field | Meaning |
|---|---|
| `outbound_digest` | SHA-256 hex of the exact gateway body bytes |
| `request_marker` | `x-request-id` sent on that attempt |
| `stream_id` | id of that attempt, or `None` when no frame was handed off |
| `bytes_handed_off` | the pipe accepted the request frame |
| `evidence` | `acked`, `app_error`, or `undecided`, plus a redacted detail |
| `state` | a SPEC §6 state from `delivery_state` |
| `error_code` | set when the collect did not finish. A handed-off `E_NETWORK_TIMEOUT` is uncertainty, not a safe retry of the send |

`session_start` returns a `GatewayResult` when the body finishes. If the request frame was handed off and the body does not finish, it raises `E_EXTERNAL_STATE_UNKNOWN` (`reconcile`). B10 reconciles with `sessions.list` and does not call `session_start` again for that attempt. A failure before the frame is handed off raises `E_NETWORK_TIMEOUT` and may be retried because nothing was sent.

`chat_stream` does not raise for a timeout, a non-2xx status, `ok: false`, or a body that is not a JSON object. Those stay `send_uncertain` after handoff, with `app_error` or `undecided` on the evidence. `failed_before_send` is only when no frame bytes were handed off. A later user echo of this exchange promotes the state to `sent` and leaves the evidence row in place. Pass the real `Exchange` and the history events into `chat_stream` when they are already known. If `exchange` is omitted, the placeholder reads `items[0].text` or `text` and uses the generated marker. B10 should pass the real exchange.

`delivery_state`, `attempt_evidence`, `associate_reply`, `observe`, `Exchange.earlier_unresolved`, and `ObservationMemory` are re-exported from `metamuse.muse`. `CALIBRATED_COMPLETE_STATUSES` is empty. `ambiguous` is an association result, not an exchange state. `closed` does not reopen. The transport never resends.

A raised `E_PROTOCOL_MISMATCH` after handoff has `next_action` telling B10 to stop and not to resend. Do not weaken TLS.

## Redaction

`redact` strips `cookie:` headers, `Authorization: Bearer` values, and `auth_token=`, `access_token=`, and `hatch_sess=` pairs. Gateway bodies placed on an attempt are passed through it. `__repr__` of the transport and of `StreamAttempt` omits cookies, bearer tokens, the hatch token, and the WebSocket URL. Do not log the guard's copy of that URL.

## Schema request for the core owner

This build does not add columns. B10 needs them before it can persist an attempt. `earlier_unresolved` is a read-time fact: another exchange in the same discussion still has a null outcome and a state in `prepared`, `submitting`, `sent`, or `send_uncertain`. `discussions.open_attempt_state` can hold session-start uncertainty. `messages` already stores seq, role, raw status, and one association label. It does not store the competing-candidate set.

Proposed, not applied. Core regenerates `Exchange.json`. Do not edit `001_initial.sql`.

```sql
ALTER TABLE exchanges ADD COLUMN request_marker TEXT NOT NULL DEFAULT '';
ALTER TABLE exchanges ADD COLUMN outbound_digest TEXT;
ALTER TABLE exchanges ADD COLUMN stream_id INTEGER;
ALTER TABLE exchanges ADD COLUMN bytes_handed_off INTEGER NOT NULL DEFAULT 0
  CHECK (bytes_handed_off IN (0, 1));
ALTER TABLE exchanges ADD COLUMN attempt_verdict TEXT
  CHECK (attempt_verdict IS NULL OR attempt_verdict IN ('acked', 'app_error', 'undecided'));
ALTER TABLE exchanges ADD COLUMN attempt_detail TEXT NOT NULL DEFAULT '';
ALTER TABLE exchanges ADD COLUMN observation_memory TEXT;
```

`observation_memory` is JSON: `ambiguous` (bool), `competing` and `disqualified` (arrays of identity arrays), and `events` (array of `{key, event}`). Identity arrays are the observation key `(session_id, event_name, seq)` plus a message id when two ids share a seq. `outbound_digest` is the gateway body digest, not `exchanges.digest`.

On `Exchange` in `models.py`, the matching fields are optional and defaulted so existing constructors keep working:

```python
request_marker: str = ""
outbound_digest: Digest | None = None
stream_id: int | None = None
bytes_handed_off: bool = False
attempt_verdict: Literal["acked", "app_error", "undecided"] | None = None
attempt_detail: str = ""
observation_memory: dict[str, Any] | None = None
```
