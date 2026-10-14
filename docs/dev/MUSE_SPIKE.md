# M0 Muse spike — protocol notes and offline client

Historical spike status (2026-10-13): **blocked: no authorized test account**. The 2026-10-14 update is in §9.1. Nothing in this pass opened a socket to muse.ai, `hatch.metaaivm.com`, or any other Muse host. There was no login and no cookie. The spike’s tests fail if the spike modules name those hosts or import a network client.

Pinned source: [nikships/muse-cli](https://github.com/nikships/muse-cli) commit `ebcb6310432ad2d39927bcb59e686330b0538310` (2026-10-03T11:59:33-04:00), package version 0.3.2. Protocol write-up: `docs/PROTOCOL.md` at that commit. Implementation read beside it: `src/muse_cli/gateway.py`, `cli.py`, `chat.py`, `routes.json`, `desc0.bin`, `desc1.bin`. MIT, `Copyright (c) 2026 Nik Ships`. The spike reimplements the framing. It does not copy `gateway.py` and it does not vendor the descriptor binaries.

Labels used below:

- **Documented** — stated in `docs/PROTOCOL.md` at that commit.
- **Code** — present in the Python at that commit and not stated as a protocol rule in `PROTOCOL.md`.
- **Inference** — a reading that B09 should keep, which a LIVE session can still overturn.

## 1. What the minimal client has to do

SDS §8 and PLAN M0: auth, create a session, send once, read history, correlate by stream id, one receive consumer, bounded frames, no partial success. SPEC §6 states:

```text
prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed
```

`closed` is the host’s `answered` / `needs_more_data` / `abandoned`. No gateway frame means closed. `close_exchange` is not a remote cancel. The spike adds no exchange states. After bytes have been handed off, `ok: false`, a status outside 2xx, and a body that is empty, not a JSON object, or undecodable are attempt evidence stored beside the state. The exchange stays `send_uncertain`. Nothing is resent. A later user echo for this exchange promotes that state to `sent`.

## 2. Endpoints and where tokens appear

**Documented** (`PROTOCOL.md` “Layers” and “Details”):

1. `POST https://muse.ai/api/auth/check` with the cookie jar returns JSON `access_token`.
2. `GET https://muse.ai/api/session` returns the assigned VM id. HTTP 200 with `{"status":"unavailable","vm_resolution_issue":{"kind":"retryable"}}` means the VM is restarting, which the client treats as a clean error rather than a missing key.
3. `POST https://muse.ai/api/hatch/token` with JSON `{vmAddress, vmName}` returns a hatch `token`.
4. WebSocket `wss://hatch.metaaivm.com/v1/noise?vm_id=<id>&auth_token=<hatch token>`. The host is the shared hatch host, not the per-VM hostname. Plain TLS without a Chrome fingerprint is reset at the edge.

**Code** (`gateway.py`): the hatch token is `urllib.parse.quote`’d into `auth_token`. `fetch_access_token` POSTs `/api/auth/check` with the cookie jar only (`_hatch_headers(cookies)`). It does not send a Bearer token. The JSON response contains `access_token`. `fetch_hatch_token` calls `_hatch_headers(cookies, access_token)`, and that helper is what sets `Authorization: Bearer <access_token>`, on `POST /api/hatch/token`. POSTs set `Sec-Fetch-Site`, `Sec-Fetch-Mode`, and `Sec-Fetch-Dest` because **documented** behavior is that muse.ai returns 403 without them. `WebSocket.connect(..., impersonate="chrome", verify=True)`. The comment in `gateway.py` says `curl_cffi`’s default `verify=None` would disable TLS verification, so the client passes `verify=True`. B09 must keep verification on (SDS §8).

The hatch token sits in the WSS query string. The access token sits in an Authorization header on the hatch-token request, not on `auth/check`. Cookies sit in the Cookie header. Diagnostics, exceptions, and logs have to run through a redactor before they are stored. The spike’s `redact()` replaces `auth_token=`, `access_token=`, `hatch_sess=`, and `Bearer ` values. A test feeds `auth_token=supersecret` and asserts the secret is gone.

**Code** (`cli.py` `cmd_wake`): `POST https://muse.ai/api/hatch/vm/wake` with `{vm_id, retry_count}`. That is operator recovery when `/api/session` says the VM is down. It is not part of the discussion state machine.

**Inference:** a hatch token is minted per command in this client (`Gateway.__init__` always calls `fetch_hatch_token` unless one was injected). How long a token stays valid on an open socket is not in the sources. Mid-connection expiry is an open LIVE question.

## 3. Handshake and crypto

**Documented:** Noise_XX, pattern `Noise_XX_25519_AESGCM_SHA256`, empty payloads both ways. This account’s client negotiates standard mode (empty msg3). An attested mode with SNP attestation and HMAC exists in the web client for other configurations. The CLI does not implement that mode.

**Code:** `NoiseConnection.from_name(b"Noise_XX_25519_AESGCM_SHA256")`, initiator, static key `os.urandom(32)`, then `write_message(b"")`, read message 2, `write_message(b"")`. Handshake messages are the Noise tokens; the application payloads are empty.

**Spike:** `spikes/muse/handshake.py` runs the same pattern with `noiseprotocol==0.3.1` (MIT, depends on `cryptography`, which provides X25519 / AES-GCM / SHA-256). Two in-memory parties exchange three empty payloads, then one encrypted client frame and one encrypted server frame. A second finished handshake’s responder decrypting the first session’s ciphertext raises `noise.exceptions.NoiseInvalidMessage`. No cipher is implemented in the spike. The attested/RV mode is not implemented and must not be invented to “get through” (PLAN §8, SDS §8).

Checked on 2026-10-13: `uv run --python 3.12 spikes/muse/handshake.py` and `uv run --python 3.14 spikes/muse/handshake.py` both printed `handshake_finished: true` and `other_session_rejected: true`. The follow-up suite does not launch those commands. It calls `handshake()` in-process after `pytest.importorskip("noise")`, and it skips if `noise` is not already installed.

## 4. Frame format

**Documented:** `NoiseTransportFrame{chunk_id(i64), chunk_index, total_chunks, payload}` is protobuf, then Noise-transport encrypted, then one WebSocket binary frame. Inside the payload, a request is `ServiceRequest{service, payload = ServiceFrame{stream_id, request = ApplicationRequest{verb, path, headers, body, end_body}}}`. A response is `ServiceResponse` wrapping `ServiceFrame` with `ApplicationResponse{status, headers, body, end_body}` and further `body_chunk` frames until `end_body`. Stream ids start at 1. Services: daemon 0, sentinel 1, vault 2, authd 3.

**Code / descriptors:** field numbers are not in `PROTOCOL.md`. They were read from `desc0.bin` and `desc1.bin` with `google.protobuf` on 2026-10-13. `desc1.bin` is proto3. Service enums: `SERVICE_DAEMON=0`, `SERVICE_SENTINEL=1`, `SERVICE_VAULT=2`, `SERVICE_AUTHD=3`. Reset codes: `CODE_UNSPECIFIED=0`, `CANCELLED=1`, `TIMEOUT=2`, `PROTOCOL_ERROR=3`, `REFUSED_STREAM=4`, `INTERNAL_ERROR=5`, `SERVICE_UNAVAILABLE=6`.

| Message | Field numbers |
|---|---|
| `ingress_rev_proxy.NoiseTransportFrame` | 1 `chunk_id` int64, 2 `chunk_index` uint32, 3 `total_chunks` uint32, 4 `payload` bytes. Each field is proto3 optional (synthetic oneof), so explicit zeros are on the wire. |
| `hatch.noise.ServiceRequest` | 1 `service` enum, 2 `payload` bytes. Plain proto3: `service = 0` is omitted. |
| `hatch.noise.ServiceResponse` | 1 `payload` bytes. |
| `hatch.noise.ServiceFrame` | 1 `stream_id` int64, oneof `kind`: 2 `request`, 3 `response`, 4 `body_chunk`, 5 `reset`. |
| `hatch.noise.Header` | 1 `key`, 2 `value`, strings. |
| `hatch.noise.ApplicationRequest` | 1 `verb`, 2 `path`, 3 repeated `headers`, 4 `body`, 5 `end_body`. |
| `hatch.noise.ApplicationResponse` | 1 `status` int32, 2 repeated `headers`, 3 `body`, 4 `end_body`. |
| `hatch.noise.BodyChunk` | 1 `data`, 2 `end_body`. |
| `hatch.noise.Reset` | 1 `code`, 2 `reason`. |

The spike codec in `spikes/muse/framing.py` was checked against `protobuf` 7.36.2 parsing those two descriptor binaries. Serialized bytes matched for a daemon request with a negative `chunk_id`, a sentinel request (`service = 1`), a finished and an unfinished application response, a mid body chunk, a final body chunk, and a reset. That check used the local clone; it is not a unit test in the repo, because the binaries stay outside the repo.

**Code:** `gateway.py` always sends `total_chunks = 1`, `chunk_index = 0`, and on read it parses one `NoiseTransportFrame` and does not concatenate chunks. **Spike policy, aligned with SDS §8:** `total_chunks != 1` or `chunk_index != 0` is `E_PROTOCOL_MISMATCH`. A partial chunk is not a successful body.

**Code:** every `ApplicationRequest` gets an `x-request-id` header set to a new UUID, and a `content-type: application/json` header when the body is non-empty. Nothing in `cli.py` or `chat.py` reads `x-request-id` back from history.

**Documented** size limits: none. **Spike policy** (not a server limit), split into plaintext and ciphertext:

| Budget | Value | Why |
|---|---|---|
| Ciphertext | 65535 bytes | `noiseprotocol` 0.3.1 `noise.constants.MAX_MESSAGE_LEN`. `encrypt` and `decrypt` both reject a buffer longer than this (`noise/connection.py`). |
| Authentication tag | 16 bytes | Noise AEAD tag. Measured: ciphertext length is plaintext length plus 16. |
| Plaintext frame | 65519 bytes | `MAX_FRAME_BYTES`. A 65519-byte plaintext round-trips (ciphertext is 65535). A 65520-byte plaintext encrypts, because `encrypt` allows plaintext up to 65535, and the 65536-byte ciphertext is then rejected by `decrypt`. The spike refuses the oversized plaintext before encrypt. |
| Reassembled body | 1 MiB | `MAX_BODY_BYTES`, across frames. Each frame still has to fit the plaintext cap. |
| Foreign frames | 32 | `MAX_SKIPPED_FRAMES` while waiting for the matching stream id. |
| Frames per collect | 64 | `MAX_COLLECT_FRAMES`, including foreign frames and empty chunks. |
| Deadline | 30s default | `time.monotonic` inside `collect`. Exhaustion raises `IncompleteBody`. |

SPEC’s 48 KiB packet and 8 KiB question caps sit above this and still apply to what we put in the body. Do not copy a 256 KiB frame cap into B09. Server limits remain a LIVE question.

## 5. Stream ids, send acknowledgement, history

**Documented / code:** `Gateway.stream` starts at 1 and increments per `_open`. The unary reader ignores frames whose `stream_id` is not the id just opened. Two concurrent `_read_frame` callers split Noise state and the failure is fatal (`BAD_DECRYPT`). The client serializes receives with a lock and still requires exactly one consumer. Subscriptions start a second thread; `cmd_send` does not use them for that reason.

**Documented:** `chat.stream`’s own response stream carries only the send echo, then ends. Live `chat.subscribe` events use different shapes (`delta.text_append` with `message_seq` and text chunks, `delta.message_done` with the full transcript) and only cover the main chat. Threaded replies do not arrive as live events. `send` polls `chat.history` instead. History is the source of truth.

**Code** (`cmd_send` / `Chat.send`): `_open("chat.stream")` returns as soon as the request frame is written. `sent: true` is printed at that moment, before the stream’s application response is read. The following poll looks only for assistant replies. The send echo is not inspected. Frames for that stream id are skipped by later `call_json` reads (`if sf.stream_id != sid: continue`), so the echo can be discarded.

**Spike rule for B09, stricter than muse-cli’s `sent: true`:** a send is `sent` only when one of these is in hand:

1. The `chat.stream` application response for that stream id is complete (`end_body`), the status is 2xx, and the body parses as a JSON object whose `ok` is not boolean false. **Code** (`gateway.py` `call_json`): `ok is False` raises `GatewayError`. **Code** (`request`): a finished status outside 2xx raises `GatewayError` before that check. The spike records both as attempt evidence `app_error`. `GatewayError` is not proof that the send had no side effect. An empty body, a non-object body, a JSON decode failure, or a `UnicodeDecodeError` is attempt evidence `undecided`. That is stricter than `call_json` turning an empty payload into `{}`. `PROTOCOL.md` calls the body the send echo. Its full JSON shape was not captured offline.
2. A `message.user` history event in the same session with `seq` greater than the baseline that is this exchange’s turn. A marker field (`request_marker`, `x-request-id`, or `client_request_id`, on the event or its payload) that names a different request wins over identical text: `req-2` is not this exchange’s `req-1`. With no conflicting marker, the text must equal the sent text, or a marker field must equal the request marker exactly. `req-1` does not match `req-10`, and the marker is not searched as a substring of the text. When the event carries `time_ms`, a stamp earlier than the local submit time is not this attempt. `time_ms` is the spike’s stand-in; `fmt_event` does not read a timestamp.

Attempt evidence (`acked`, `app_error`, or `undecided`, plus a redacted detail) is not an exchange state. `app_error` and `undecided` after a handoff leave the exchange at `send_uncertain`. Bytes were handed off, so this is not `failed_before_send`, the grant is not released, and nothing resends. History reconciliation still promotes the exchange to `sent` when this exchange’s user echo is found. The evidence row stays `app_error` or `undecided`.

If bytes were handed to the transport and there is no clean acknowledgement and no user echo for this exchange, the state is `send_uncertain`. A missing assistant message does not mean the send failed (SPEC §6, PLAN §8). There is no automatic retry.

`failed_before_send` only when no frame bytes were handed to the transport. A Noise encrypt that never ran, or a write that raised before the frame was queued, qualifies. A timeout after `send` returned does not.

**Documented** history:

- `chat.history` is GET query parameters only. `transcript_mode` is rejected; omit it. `session_id` selects a side chat. `limit` works.
- Events use `event_name` `message.user` or `message.assistant`. Text is in both the top level and `payload.display_text` (the client also falls back to `payload.content`).
- A genuine assistant reply has an empty `reply_to_message_id`. Proactive pushes (the doc names Telegram drafts and background task updates) are self-referential there. That is how `send` separates a reply from background chatter.

**Code** (`is_reply`, `fmt_event`, `Chat.new_replies`):

- Baseline is the max `seq` from `chat.history` with `limit` 1 in the same scope, taken before `_open`. A failure of that read leaves baseline 0.
- A reply candidate in muse-cli has `event_name == message.assistant`, `seq > baseline`, empty `reply_to_message_id` (top level or payload), non-empty text, and a non-empty `payload.status`. The comment says a reply still being written has no status yet, and to wait for `"completed"`. The condition itself is any truthy status. **Do not adopt that predicate.** Unknown and uncalibrated statuses, including `running` and the comment’s word `completed`, stay progress. Completion requires a status in an explicit calibrated set. The default set is empty until LIVE records the enum. `UNVERIFIED_UPSTREAM_COMPLETE = {"completed"}` exists so a test can open the gate. It is not a pin for B09.
- `fmt_event` exposes `seq`, `role` (payload role or else `event_name`), `message_id`, and text.
- `sessions.list` rows include `session_id`, `title`, `is_thread`, `pinned`, `archived`, `updated_at_ms`.
- `session.start` body is `{"method":"/api/session/start","params":{"origin":"fresh","lifecycle":"persistent","title"?}}`. The HTTP route is `POST /session/start` (`routes.json`). The client accepts `session_id` or `id` on the result. Rename is a flat `{session_id, title}`.
- `chat.stream` body is `{items:[{type,text}], node_id, capabilities, session_id?}`. `node_id` is a client hex stored in config (`secrets.token_hex(8)` or `MUSE_NODE_ID`), not a per-exchange id.
- GET calls move a dict body onto the query string (`request()` in `gateway.py`). `cmd_history` therefore sends `limit` and optional `session_id` as the query.

**Code** pagination: the client never sends a cursor. `routes.json` also has `GET /chat/history-window` (`chat.history_window`) and `GET /chat/message` (`chat.message_get`). Neither is called. **Inference:** do not depend on `history_window` until a LIVE call shows its parameters. Polling `limit` can miss or repeat events if the session grows during the poll. SPEC wants a stable snapshot per page. B09 should keep the raw events and a cursor of its own (`last seq` it has stored), and treat a gap as incomplete rather than as a full transcript.

**Documented** ordering: history `seq` is the order `is_reply` and `Chat.events` sort by. Live deltas use `message_seq`, which is a different channel and is not the P0 wait path. `chunk_id` is a random int64 per outbound frame (`struct.unpack("<q", os.urandom(8))`), not a chat sequence number.

**Code** progress and multi-part: on the unary path, body bytes are concatenated from `response.body` and `body_chunk.data` until `end_body`. On the subscribe path, `delta.text_append` chunks and `delta.message_done` carry the transcript. `Chat.send` keeps listening for 10 seconds after the first reply because follow-up assistant messages are normal (`QUIET = 10`). For one exchange, more than one qualifying assistant message is **ambiguous** (SPEC §6). The spike stores that as `ambiguous` and does not attach either message. Extra text stays on the discussion as unattached, including text that arrives after `closed`. It is not the next question’s reply.

**Code** errors:

- `AuthError` when `auth/check`, `hatch/token`, or cookie parsing fails, or `/api/session` is non-200. The message tells the operator to re-export cookies.
- `GatewayError` when `/api/session` is 200 without `vm_id` (VM unavailable), when the application status is outside 2xx, when a stream `reset` arrives, or when JSON says `ok: false`.
- `TimeoutError` when no application response arrives before the deadline. `status is None` after the loop is that timeout, including the case where only body chunks arrived.
- JSON is not required for success at the transport layer. `call_json` raises on non-JSON. SPEC says a natural-language reply with no JSON is still a reply. B09 parses history events leniently and keeps the raw text.

**Spike rule:** a non-empty `payload.status` is raw progress until it is a member of the calibrated set. It does not mean the research is done, and it does not mean `closed`. The string `"completed"` is what the upstream comment names. LIVE has to list the real enum before any status admits `reply_observed`.

## 6. State machine, with the evidence for each step

| Transition | What proves it | Label |
|---|---|---|
| `prepared` → `submitting` | Local row exists (discussion, packet, grant, baseline `seq`, question bytes) and exactly one send is about to be written. | SDS §8. Not a gateway frame. |
| `submitting` → `failed_before_send` | The transport was not given a frame. Grant uses may be released. | SDS §8. Spike `delivery_state(wrote_any_bytes=False)`. |
| `submitting` → `sent` | Finished 2xx JSON object on the send’s stream id (`end_body` set) whose `ok` is not false, or a same-session `message.user` with `seq > baseline` that is this exchange’s turn: exact text unless a marker field names another request, or an exact marker match, and no `time_ms` before local submit. A later echo promotes `send_uncertain` to `sent`. | Echo: **documented** as “send echo”, shape **not** captured. `ok is False` is **code** and is attempt evidence, not this transition. User echo: **inference**. A conflicting marker winning over identical text is the spike rule. muse-cli’s `sent: true` is earlier and is not the rule. |
| `submitting` → `send_uncertain` | A frame was handed off, and there is no clean acknowledgement and no user echo for this exchange. Covers a deadline with no echo, `ok: false`, a finished status outside 2xx, an empty or non-object body, and bytes that fail UTF-8 or JSON decoding. The verdict is attempt evidence beside the state. No resend and no grant release. | SPEC §6. **Code** `gateway.py` `request` and `call_json` supply the `app_error` verdict. Absence of an assistant reply is not evidence of non-delivery. |
| `sent` or `send_uncertain` → `reply_observed` | Exactly one assistant event tied to this exchange’s user turn: same session, `seq` above that user event, empty `reply_to_message_id`, non-empty text, no other user message between them, no earlier user turn still between the baseline and the echo, the caller does not still have an earlier unresolved exchange in the session, and `payload.status` in the calibrated set. `observe` applies this when the association state is `reply_observed`. A stored `ambiguous` label moves to `reply_observed` on that positive resolution. | Tie-to-turn is SDS §8. `reply_to` is **documented**. The truthy status check is **code** and is not adopted. One unsettled exchange per discussion is SPEC §6. |
| `reply_observed` → `closed` | Host sets `answered`, `needs_more_data`, or `abandoned`. | SPEC §6. |
| no reopen | `closed`, `reply_observed`, and `failed_before_send` stay put when a later association is ambiguous or resolved. | SPEC §6. `closed` is host settlement. |
| no transition on remote cancel | `chat.cancel` is not in the minimal set. Report `not_supported` / unknown. | SDS §8. |

Association refuses “the latest assistant message”:

- An assistant event whose `session_id` is some other session is counted and ignored, even if its `seq` is the largest in the batch.
- A same-session event with a non-empty `reply_to_message_id` is background, not the reply.
- An event with `seq <= baseline` is history from before this exchange.
- Two qualifying replies are `ambiguous`. Pass the previous `Association` as `prior` on every later poll. The memory is the union of events. The same session, event name, and `seq` are one event: a message id that appears on a later poll enriches that row. Two non-empty different message ids at the same `seq` stay distinct. A later page that contains only one of the candidates does not resolve ambiguity: the omitted candidate is still in memory. When more than one completed candidate is tied, their keys are the competing set. Ambiguity clears only when a later observation disqualifies one of those keys (`reply_to_message_id` appears on that same event) and exactly one of the competing candidates remains, or the host passes one of those candidates’ message ids. A `reply_to_message_id` on an event outside the competing set does not clear it. A competitor whose status changes to progress, including `running`, does not clear it. A missing row is not disqualification. The spike does not pick the first match the way `cmd_send` does.
- An assistant event with no user turn for this exchange is not paired. It stays on `unresolved`. A manual user message between the echo and the assistant leaves that assistant unresolved. Another user message after the baseline and before the echo makes a following assistant `ambiguous` instead of `reply_observed`. While `Exchange.earlier_unresolved` is set, an assistant message after this echo is not auto-paired either, including an older answer that arrives after the echo. B09 sets that flag from other open exchanges in the same session (SPEC §6: one unsettled exchange per discussion; SDS §8: a late answer is not assigned to the next question). The flag is a caller fact, not a muse-cli field. Clearing it lets a single remaining tied candidate pair. That withheld case does not by itself latch the competing-candidate set.
- A reply can move `send_uncertain` to `reply_observed` without a second send only when it is tied to this exchange’s user turn. A late answer to an earlier question is not that turn. `observe` leaves `sent` and `send_uncertain` unchanged while the association is `ambiguous` or `none`. `closed` does not reopen.

`x-request-id` is the request marker the current client generates. **Inference:** it is not a confirmed correlation id, because this client never reads it back. B09 should still store it on the exchange (SDS already says correlation uses a request marker). LIVE has to show whether history echoes it, echoes `node_id`, or echoes only the user text. Until then, the user-echo test in the scoped session is `seq > baseline` plus either exact text, when no marker field names another request, or an exact marker-field match. A prefix such as `req-1` inside `req-10` is not a match. Identical text carrying `req-2` is not an exchange whose marker is `req-1`.

Session create, same idea (SDS §8): write the local discussion and an open attempt, then `session.start`. The remote id is what completes the mapping. On timeout, `open_uncertain`. `sessions.list` must identify the row by `session_id`, not by title. Not finding a row does not prove the create never happened. The spike does not call `session.start`. It only documents the body.

## 7. Routes to keep and routes to leave out

Minimal set for B09:

| Kind | Call | Why |
|---|---|---|
| HTTPS | `POST /api/auth/check` | Access token from the operator’s cookie jar. |
| HTTPS | `GET /api/session` | VM id. Unavailable is not an auth failure. |
| HTTPS | `POST /api/hatch/token` | Token for the socket. Never log it. |
| WSS | `/v1/noise` on `hatch.metaaivm.com` | The only gateway transport. |
| Gateway | `session.start` `POST /session/start` | Create the remote session. |
| Gateway | `sessions.list` `GET /api/session/list` | Reconcile a create. Match `session_id`. |
| Gateway | `chat.stream` `POST /chat/stream` | The one send. |
| Gateway | `chat.history` `GET /chat/history` | Baseline and replies. Query: `limit`, optional `session_id`. Omit `transcript_mode`. |

Service id for all four gateway routes is daemon 0. `routes.json` leaves `service` unset on them, and `gateway.py` maps a missing service to daemon.

Deliberately excluded:

- `cmd_raw` and the rest of the 258 routes.
- `update.py`, `site/`, the muse-cli skill, feed, goals, ideas, themes.
- `chat.subscribe`, `chat.stream_events`, and other `*.subscribe` routes as the way to see a reply.
- `chat.cancel`, `chat.delete_turn`, session pin / archive / delete.
- `chat.history_window` and `chat.message_get` until a LIVE call shows they add a stable cursor. They are not required to explain send and history.
- Wake, as an operator action outside the exchange state machine.
- Auth export and any Chrome remote-debugging automation inside the library.
- Harness edits, attested Noise, and any handshake we would be inventing.

## 8. The offline spike

Layout:

| File | Role |
|---|---|
| `spikes/muse/framing.py` | Protobuf wire codec, size caps, `redact`, `E_PROTOCOL_MISMATCH` / `E_LIMIT_REACHED`. |
| `spikes/muse/loop.py` | One receive consumer, in-process byte pipe, scripted fake gateway. |
| `spikes/muse/associate.py` | Delivery state and reply association. |
| `spikes/muse/handshake.py` | In-process Noise_XX. PEP 723 names `noiseprotocol==0.3.1` for a direct run. Tests call it only when `noise` is already importable. |
| `spikes/muse/test_spike.py` | Pytest. No network. |

`handshake.py` metadata:

```text
requires-python = ">=3.12"
dependencies = ["noiseprotocol==0.3.1"]
```

Follow-up tests use Python 3.12 with `noiseprotocol==0.3.1` provisioned before pytest, and a disk-backed pytest basetemp (never `/dev/shm`):

```text
pytest-disktmp uv run --no-project --python 3.12 --with pytest --with noiseprotocol==0.3.1 pytest spikes/muse -q
```

That run reported 45 passed. `uv run --no-project --python 3.12 --with ruff ruff check spikes/muse` and `ruff format --check spikes/muse` were clean. The handshake test does not spawn a resolver: it `importorskip`s `noise` and calls `handshake()` in-process.

The tests cover:

- A decoy response on another stream id does not complete the request. The matching stream id does.
- Body chunks concatenate, and success is returned only with `end_body`.
- Status 200 with `end_body` false and no further frame raises `IncompleteBody`.
- A frame larger than the 65519-byte plaintext cap, and a reassembly past the body cap, raise `FrameTooLarge` (`E_LIMIT_REACHED`).
- ServiceFrame field 6 raises `ProtocolMismatch` (“unknown frame type”).
- `total_chunks = 2` is `ProtocolMismatch`, not a partial success.
- A second enter on the same loop, a second `ReceiveLoop` on the same link, `collect` without ownership, and a concurrent `collect` are refused.
- More frames than the collect budget, or a monotonic deadline that is already due, raise `IncompleteBody`. A finished body is not returned in either case.
- A reset frame is not a success.
- A negative `chunk_id` round-trips.
- An assistant event for another session is not the reply, including when it is the latest event in the batch. A same-session assistant with no user turn for this exchange is not paired either.
- A manual user message between the echo and the assistant, including one that a later poll omits, leaves the assistant unresolved. Another user turn before the echo keeps the following assistant ambiguous. While an earlier exchange in the session is unresolved, an answer that arrives after the current echo stays unpaired until that flag clears or the host names the candidate.
- Request markers match exactly. `req-1` does not match `req-10`. Identical text with `x-request-id` `req-2` is not this exchange’s `req-1`.
- Two same-session candidates stay `ambiguous` across a later poll that contains only one of them. A host message id, or a later `reply_to_message_id` on one of the competing candidates, is what resolves it. One candidate moving to `running`, together with an unrelated event gaining `reply_to_message_id`, leaves the remaining candidate unpaired.
- The same user event seen first without a message id and later with one stays one echo, and the association stays `reply_observed`. Two non-empty different message ids at the same `seq` stay two events.
- `running`, and `completed` under the default empty calibration set, are progress. They are not `reply_observed`.
- A user echo moves a timed-out send to `sent`. No echo leaves `send_uncertain`. No bytes written is `failed_before_send` even if an assistant event is already in the list.
- A finished HTTP 200 body `{"ok":false,"error":"rejected"}` is attempt evidence `app_error` and exchange state `send_uncertain` until this exchange’s user echo, which promotes the state to `sent` and leaves the evidence as `app_error`. A finished status 500 is the same, including when the body is not valid UTF-8. `observe` can then move that exchange to `reply_observed` when the tied assistant is the one reply. Nothing resends, and the state is not `failed_before_send`. A non-JSON 2xx body, including undecodable bytes, stays `send_uncertain` with evidence `undecided`.
- A reply tied to this exchange’s user turn can move `send_uncertain` to `reply_observed`. `observe` moves a stored `ambiguous` label to `reply_observed` when the association resolves, and it leaves `closed` closed. `observe` leaves `sent` as `sent` while the association is still ambiguous.
- An explicit foreign `session_id` is not overwritten by the history query’s session.
- `auth_token=` values do not survive `redact`.
- The Noise_XX handshake, encrypted round trip, and cross-session reject run in-process. Plaintext of 65519 bytes round-trips with a 65535-byte ciphertext. Plaintext of 65520 bytes produces a 65536-byte ciphertext and `decrypt` raises `NoiseInvalidMessage`.

The fake gateway is `FakeGateway` on `InProcessLink`. It never listens on a port.

## 9. Open questions only a LIVE session can answer

1. The JSON shape of the `chat.stream` send echo, and which fields are stable.
2. Whether `message.user` appears with a new `seq` before the assistant reply, so a timeout poll can prove delivery from the echo alone.
3. Whether `x-request-id`, `node_id`, or any other client marker is stored on the user event. If none are, text match in the scoped session is the only user echo.
4. The `payload.status` enum. The upstream condition treats any non-empty value as ready, and the comment names `completed`. The spike does not. Until LIVE lists which strings mean the assistant turn is finished, the calibrated set stays empty and values such as `running` and `completed` are progress, not `reply_observed`.
5. Parameters of `chat.history_window`, and whether `chat.history` can page older than `limit` without skipping or duplicating when new messages arrive.
6. Server limits on frame size, body size, and `limit`.
7. Hatch-token lifetime and what a mid-socket expiry looks like (close, reset code, HTTP status).
8. Whether two sessions with the same title are always distinguished by `session_id` in `sessions.list`, and whether `session.start` returns `session_id` or `id` in current responses.
9. Historical question (2026-10-13): whether nonempty msg2 always means attested mode. Superseded for the documented standard subset by §9.1; RV/CVM still stops with `E_PROTOCOL_MISMATCH`.
10. Whether the Chrome TLS fingerprint is still required, and which `curl_cffi` impersonation string the edge accepts.
11. Rate-limit responses and `Retry-After` on auth and on `chat.stream`. Safe-read retries must not resend a chat.


### 9.1 Standard transport update, 2026-10-14

The original spike above remains a historical record. One orchestrator-authorized LIVE
An operator LIVE probe (evidence kept outside this repository) confirmed msg1 nonce field 1 (32 bytes), msg2 outer bytes field 2 (68 bytes),
and exactly nested fields 1/2 (32 bytes each): key equals responder Noise static and nonce
equals this attempt's fresh challenge. Msg3 and application frames were not sent. This is
one observation, not LIVE lifecycle acceptance. The independently documented protocol facts
come from the round-2 interoperability review; no proprietary schema/parser/code is adopted.

B09 now supports only that strict standard subset using the same pinned mature Noise/TLS.
Optional explicit platform=0 is the proto3 default. Duplicate/wrong-wire/unknown/evidence,
missing nonce, wrong key/nonce, malformed RV/chip binding and oversize fail closed. Valid RV
records sticky confidential expectation then stops. CVM assignment and CVM_* state also
persist confidentiality before refusal, including contradictory selected CVM metadata. No CVM/delegation verification is invented. Empty
msg3 follows committed owner/session/selection-generation validation and both equalities;
standard has no enrollment-health or capabilities registration gate. Identity encoding and
existing serial frame/completeness budgets remain enforced.

The browser standard branch does not validate nested key/nonce; these comparisons and
persistent downgrade refusal are stricter local policy. Staging applicability is respected,
not disabled. Standard does not claim attestation, SNP/TCB or notary signature verification.
Private schema-5 Store trust is keyed by provider/account/VM, survives restart/auth_ref
rotation, has a sticky active-account expectation and refuses stale CAS/corrupt state.
Connect/retry does not reselect a VM or clear security state. An account file lock serializes
committed observation and msg3 against context changes; generation changes invalidate sockets.

The operator probe also exposed two adapter defects: curl RESOLVE now has one comma-joined host:port entry
with bracketed IPv6, preserving checked-address order; upgrade errors now distinguish
401/403, 429, 5xx and transport failures without leaking URL/query/body. Session reads use
`?vm_state=live`. Connect performs one activation wake and at most 20 bounded redials/240s
for 502/503/504, with a fresh UUID attempt ID and capped schedule documented in MUSE.md.
Wake requests account power state only inside an already-granted live-mode operation.
No lease/reset/chip-selection/enrollment/registration endpoint is called. Fixtures use
injected HTTP, clock and an in-process mature-Noise responder; no test opens a network socket.

WSS query is exactly VM ID, opaque route token (legacy or s0 envelope), optional six-segment
non-delegation notary, `app_id=hatch-web` and fresh request UUID; fixed Muse Origin is sent.
Hatch body takes validated row endpoint/name. New connection credentials are memory-only;
known stale timeout hints allow one remint then stop. Redaction includes raw/encoded tokens,
nonce/proof, connect attempt IDs and wake bodies. Original B10 application handoff,
uncertainty, no-resend and no-refund rules remain binding. New connect failures cannot turn
an earlier handed-off submission into `failed_before_send`.

OFFLINE/FIXTURE results establish implementation behavior only. AT-038 and AT-043 stay
unproven until the orchestrator's authorized full connect/read, exchange/echo/reply/follow-up
and source-to-Muse-to-draft/export runs. Questions 1–8, 10–11 above remain LIVE questions.

### 9.2 Independent-review corrections (2026-10-14)

Round 2 supersedes §9.1's narrower persistence description without rewriting that history.
Remote viewer hash and normalized VM identity join expectations across local labels.
Migration 005 is amended before merge with inherited/propagated confidentiality and guards;
owner-only hints are persistent even without a usable VM. Every authenticated session row
is scanned before assignment/uniqueness checks, including CVM replacement indicators.
Storage identity is trimmed/lowercased; the exact server ID is retained on the wire.

A canonical field-2/varint tag identifies Noise-authenticated RV-shaped msg2; persist the
permanent confidential tier before refusing even malformed/unsupported RV fields. All
accepted handshake varints are minimal uint64 and RV attestation is nonempty bytes. Both
standard equalities and mature Noise/TLS remain mandatory. No reset tool exists.

All credential aliases are validated. Case-insensitive delegation/endorsement prefixes,
malformed/oversized s0 and conflicting notary aliases refuse with protocol/never; legacy
opaque tokens no longer admit dots. Nonpositive budgets stop before wake/dial/msg1. Only
endpoint-specific success statuses are admitted: bootstrap 200 from the spike contract,
wake 200 observed in an operator LIVE probe (evidence outside this repository), plus operator-authorized 202/204 (not LIVE observed here).

Shared maintenance.lock ends at commit before msg3 I/O; provider/account trust locks remain.
Confidential expectation precedes generation equality. Actual schema signatures are compared
without replaying migrations at every check. Lifecycle Store wiring is now applied.
Raw/encoded query/JSON aliases, embedded wakes, nonce encodings, request IDs and dataclass
reprs are scrubbed. Regressions cover all five currentness boundaries, cross-process changes,
SQL replacement/rowid/deletion, backup/restore and cross-label refusal; mutation evidence is
recorded in the task report. OFFLINE/FIXTURE only; AT-038/AT-043 remain unproven LIVE.

## 10. Historical inputs for B09 (2026-10-13; current handshake/wake in §9.1)

- Pin `noiseprotocol==0.3.1` and use `cryptography` only through it. Pattern `Noise_XX_25519_AESGCM_SHA256`, empty handshake payloads, `verify=True`, Chrome impersonation via `curl_cffi==0.16.3`. Do not add the `websockets` package for this gateway.
- One receive loop per gateway connection, owned by the connection. A second loop on the same link, `collect` without that ownership, and a second `collect` while one is running are protocol errors. P0 is serial unary calls. Do not subscribe while a unary read is in progress.
- Caps: plaintext frame 65519 bytes, ciphertext 65535 bytes, 16-byte tag, reassembled body 1 MiB, 32 foreign frames, 64 frames per collect, 30s monotonic deadline. Do not raise the frame cap to 256 KiB. `encrypt` on `noiseprotocol` 0.3.1 will accept a larger plaintext, and `decrypt` will reject the ciphertext. SPEC’s 48 KiB / 8 KiB caps still apply to what the body is allowed to contain.
- Persist on the exchange, which SDS §4 already sketches (`baseline`, `state`, remote session id, message `seq` / role / raw status / association): the outbound text (or its digest), the `x-request-id`, the stream id of that attempt, whether any frame bytes were handed off, the attempt evidence (`acked`, `app_error`, or `undecided`, and a redacted detail) separate from the exchange state, and the cumulative observation memory (de-duplicated events, the competing candidate keys, and an ambiguity flag). `models.py` is owned by B02–B04. If the first schema omits the marker, the outbound digest, the attempt-evidence fields, or a place to keep unresolved candidate ids, add them before B09 merges. This pass did not edit `src/`.
- Implement `delivery_state`, `attempt_evidence`, `associate_reply`, and `observe` from `spikes/muse/associate.py`. Do not copy muse-cli’s `sent: true` after `_open`, its truthy status check, or its first matching reply. Keep `CALIBRATED_COMPLETE_STATUSES` empty until LIVE fills it. After bytes are handed off, `app_error` and `undecided` stay `send_uncertain`: do not resend, and do not refund the grant as `failed_before_send`. When history shows this exchange’s user echo, promote the state to `sent` and keep the evidence row. Pair an assistant only with this exchange’s turn. A conflicting marker wins over identical text. While an earlier exchange in the session is unresolved, do not auto-pair a later answer. Clear stored ambiguity only by disqualifying one of the competing candidates or by a host decision. Do not reopen `closed`.
- On `E_PROTOCOL_MISMATCH` (unknown field, multi-chunk frame, attested handshake), stop. Do not weaken TLS or the handshake.
- Redact query tokens before any log, exception string, or event row.
- Keep the route allowlist in §7. A method outside it is a programming error, not a `raw` escape hatch.
- LIVE remains blocked until an operator authorizes an account. Fixture plus `blocked` is the honest result until then.

### 9.3 Round-3 verification changes (2026-10-14)

The fixed-upstream standard transport now keys durable trust on authenticated remote owners and normalized VMs, independent of operator labels. It commits all hints and RV observations before refusal, including stale or changed bindings, and propagates owner↔VM components set-wise. The 1 MiB session cap and full-Noise RV discriminator precede standard-only limits. Shape drift, secret encodings and deadline tests are expanded. Evidence remains OFFLINE/FIXTURE; AT-038 and AT-043 LIVE have not run. CVM remains unsupported. Earlier spike findings and limits above are history, not new LIVE evidence.
