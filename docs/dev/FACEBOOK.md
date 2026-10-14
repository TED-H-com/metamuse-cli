# Bounded Facebook Post Collection (B13)

`metamuse.facebook` implements operation `collect.facebook_post` for bounded collection of a single approved Facebook page type (`post_permalink`), backed by `metamuse._browser_egress` for process-local egress enforcement.

## Scope, Page Type, and Narrow Dispatch

- **Single page type (`post_permalink`)**: Only single-post permalink URLs on `www.facebook.com`, `facebook.com`, `m.facebook.com`, or `web.facebook.com` are accepted (`/<actor>/posts/<post_id>`, `/permalink.php?story_fbid=<id>&id=<actor>`, `/story.php?story_fbid=<id>&id=<actor>`). Profile timelines, groups, watch feeds, search, reels, stories, photos, and other URL shapes are rejected before a job is created with `E_UNSUPPORTED`.
- **Narrow dispatch**: `collect.submit_collect` delegates to `facebook.submit_facebook_collect` only when `facebook.is_facebook_post_request(request)` is true: every input must have `kind == "url"` with a parsed hostname in Facebook's domain set, and `resources` must be a non-empty subset of `{"post", "comments"}`. Media-only resource requests (`media_metadata`, `subtitles`, `media_file`) are left to B12 (`remote_media.py`), and non-Facebook URLs retain `collect.py`'s existing `E_UNSUPPORTED` path.

## Bounds and Identity-Based Resume

Every job is bounded by workspace/job limits and `[facebook]` budgets:

- `max_urls`: Single post permalink per job (`len(request.inputs) == 1`).
- `max_items` and `max_bytes`: Enforced per job against workspace ceilings (`[limits]`) and in real time by `BrowserEgressProxy` across both directions of every tunnel/request. Exceeding `max_bytes` immediately stops the proxy tunnel and finishes the job with `coverage_status = "partial"`, `stop_reason = "limit_reached"`, and `E_LIMIT_REACHED`.
- `wall_clock_seconds`: Enforced per job across navigation and expansion steps.
- `max_expansions` (`0..100`, default `10`): Maximum comment/reply expansion clicks per job.
- `pacing_ms` (`0..60_000`, default `1500`): Conservative pacing delay between expansion actions (no rate is claimed to be "safe").
- `read_timeout_seconds` (`[limits]`, default `30`): Navigation, selector, and proxy idle timeout.
- **Identity-based checkpoints and resume (AT-013, FR-010)**: Progress is checkpointed every 50 records (`BATCH_SIZE = 50`) with a compact metadata payload (`expansions_used`, `bytes_used`, `halt`, `missing_fields`, `accepted`) without storing rendered HTML in SQLite or slicing by index. On restart/resume, the page is re-fetched and every parsed record is passed through `collect._Book.apply`, which loads already-committed items and revisions from SQLite and turns unchanged items into `unchanged` rows while committing any newly appeared or edited comments without duplicating items or consuming an extra permit use.

## Configuration Keys (`[facebook]`)

Configured in `metamuse.toml` under `[facebook]` (`extra="forbid"`):

| Key | Type / Default | Purpose |
| --- | --- | --- |
| `enabled` | `bool = False` | Master switch; must be `true` to submit or run jobs. |
| `approved_page_types` | `list[Literal["post_permalink"]] = []` | Must explicitly include `"post_permalink"`. |
| `auth_ref` | `str \| None = None` | Secret store reference (`auth_<32hex>`) holding Playwright storage state JSON. |
| `account` | `str \| None = None` | Account label matching the B08 access permit and secret store entry. |
| `max_expansions` | `int = 10` | Expansion click budget per job (`0..100`). |
| `pacing_ms` | `int = 1500` | Pacing delay in milliseconds (`0..60000`). |
| `allowed_hosts` | `list[str]` | Approved HTTPS host allowlist for document and CDN subresource egress. |

No fixture or loopback switch exists in operator configuration; supplying `fixture_url_mode` (or any unknown key) fails config loading with `E_INPUT_INVALID`.

## Operator Steps and Capability Status

1. **Install optional extra and browser binary**:
   - `uv sync --extra facebook` (installs `playwright==1.63.0` with lazy import).
   - `uv run playwright install chromium` (operator step; code and tests never download browsers).
2. **Import storage state secret**:
   - Export a Playwright `storage_state` JSON file outside MetaMuse and import it with `metamuse auth import` (stored in B08 `SecretStore` under `auth_ref`).
   - `sanitize_storage_state` selects cookies and `https://` origins strictly by parsed hostname (`PRIMARY_FACEBOOK_HOSTS`), drops CDN/lookalike/third-party domains, forces `secure=True`, and redacts secret values from error strings, logs, and `repr`/`str`. Installed browser profiles are never scanned.
3. **Configure `metamuse.toml`**:
   - Set `[app] network_mode = "live"`.
   - Set `[facebook] enabled = true`, `approved_page_types = ["post_permalink"]`, `account = "<account>"`, and `auth_ref = "<auth_ref>"`.
4. **Mint B08 access permit (`access_grant_id`)**:
   - Issue a B08 `access` permit via `metamuse permit create` scoped to `provider="facebook"`, `account="<account>"`, `operations=["collect.remote"]`, and `target_digests=[facebook.permit_digest(...)]`. Pass the permit ID as `access_grant_id` in the collect request.
5. **Capability reporting (`metamuse_collect.facebook_post`)**:
   - `collect.capability_status` keeps `metamuse_collect` unchanged (`ready` / `granted_scope` / `fixture_passed`) for local imports and adds backend row `metamuse_collect.facebook_post`:
     - `environment`: `missing_dependency` (Playwright not importable or Chromium binary absent), `needs_auth` (missing/invalid storage state for `auth_ref`), or `ready`.
     - `permission`: `not_configured` (disabled, `"post_permalink"` not approved, or `network_mode != "live"`) or `granted_scope`.
     - `verification`: `fixture_passed` (never `live_verified`).

## Parsing, Identity, Parents, and Provenance

- **Identity**: Reliable items use `(platform="facebook", native_scope="<actor>/<post_id>", native_id)`. Comments without a native ID receive `identity_confidence = "weak"`.
- **Parents (AT-004)**: Top-level comments link to the post's `native_id`; replies link to their parent comment's `native_id`. If a parent comment ID is missing from the observation and store, `parent_id` stays `None` and `unresolved_parent` records the missing native ID (`missing_fields` includes `"parent_comment"`).
- **Revisions (AT-013)**: Re-observing a reliable item with changed text appends a new `ItemRevision`. Omitting an item in a subsequent observation never deletes existing database rows.
- **Provenance (AT-017)**: Sources record `collection_method = "browser_playwright"`, `upstream_collection_method = "facebook_post_permalink"` (never `manual`), `assurance = "browser_verified"`, `original_url`, `canonical_url`, and `fetched_at`.
- **Content is data (AT-032)**: Post and comment text is stored strictly as inert UTF-8 data and never interpreted as prompts, instructions, or permissions.

## Failure Classes and Coverage Gaps (AT-041)

Unexpanded `"more comments"` or `"more replies"` controls, unresolved parent comments, or reaching `max_expansions` / `max_items` / `max_bytes` record explicit gap entries in `missing_fields`, producing `coverage_status = "partial"` and job state `partial` (never `complete` or `scope_satisfied`).

Distinct failure conditions stop immediately (`safe_read_retries = 0`) with explicit error codes and `next_action` guidance, without retry loops, stealth plugins, fingerprint spoofing, CAPTCHA solvers, or proxy/account rotation:

| Failure condition | Error code | Job state |
| --- | --- | --- |
| Login wall or redirect to `/login` | `E_AUTH_REQUIRED` | `awaiting_user` |
| Checkpoint / challenge page or redirect | `E_CHECKPOINT_REQUIRED` | `awaiting_user` |
| HTTP 403 or permission barrier | `E_ACCESS_DENIED` | `awaiting_user` |
| HTTP 429 or rate-limit barrier | `E_RATE_LIMITED` | `failed` |
| Missing post/comments container (`selector_drift`) | `E_SELECTOR_DRIFT` | `failed` |
| Valid post with zero comments and zero expand controls | — (`comments_empty=True`) | `succeeded` (`scope_satisfied`) |

## Browser Egress Proxy and Verification Boundary (AT-030, FR-019)

- **Why `context.route` is not the egress boundary**: Playwright invokes a `context.route` handler only for the first URL of a redirect chain; Chromium follows subsequent `3xx` hops internally with its own DNS lookups and socket connections without calling `context.route` again, and `Response.server_addr()` runs only after a request has already been sent.
- **Local egress proxy (`src/metamuse/_browser_egress.py`)**: Every browser session starts a stdlib `BrowserEgressProxy` listening on `127.0.0.1:0` and stops it in `session.close()`. Chromium is launched with pinned flags from `build_chromium_launch_args(proxy.port)`:
  - `--proxy-server=http://127.0.0.1:<port>`
  - `--proxy-bypass-list=<-loopback>` (even loopback traffic cannot bypass the proxy)
  - `--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1` (disables Chromium's own DNS resolution)
  - `--disable-quic` (prevents UDP QUIC bypass)
  - `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` (prevents WebRTC UDP leakage)
- **Proxy enforcement on every request and redirect hop**:
  - `CONNECT host:port`: runs `OutboundURLPolicy.before_request("https://host:port/", purpose=...)` (or the test-only `check_fixture_url` checker), opens a TCP connection to `checked.addresses[0]` only without re-resolving DNS, verifies `upstream_sock.getpeername()` against `checked.addresses` (`peer_address=`) and `checked.port`, answers `200 Connection Established`, and splices bytes both ways so end-to-end TLS and certificate verification remain intact between Chromium and the server. Any refusal answers `403 Forbidden`, closes the socket without dialing, and records an aborted `EgressDecision`.
  - Absolute-form plain-HTTP (`GET` / `HEAD http://host:port/path`): refused in production (`Plain HTTP is refused by the Facebook browser egress proxy`); permitted in local synthetic fixture tests only through `EgressGuard.for_fixture_test`, applying the same policy check, pinned dial to `checked.addresses[0]`, and peer verification on every request and `3xx` redirect hop.
  - All other proxy inputs are refused (`403`): non-`GET`/`HEAD`/`CONNECT` methods, origin-form requests (`GET /path`), `Upgrade` / WebSocket headers, request bodies, oversized headers, and requests targeting `127.0.0.1:<proxy_port>` / `localhost:<proxy_port>`.
  - Concurrent tunnels are bounded (`MAX_CONCURRENT_TUNNELS = 16`), socket idle timeouts are bounded by `read_timeout_seconds`, and bytes relayed in both directions are counted against `max_bytes`.
- **Secondary browser checks and hardening**:
  - `context.route("**/*", ...)` is retained as a secondary check for browser resource-type blocking (`websocket`, `eventsource`, `manifest`, `service_worker`) and initial navigation page-type validation.
  - `Response.server_addr()` is retained as a secondary response check confirming that the proxy verified the upstream socket peer.
  - Service workers (`service_workers="block"`), downloads (`accept_downloads=False`), popups, and ambient proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`) are blocked/refused.

## Known Limitations

1. **HTTPS URL paths inside `CONNECT` tunnels**: Because TLS remains end-to-end between Chromium and the remote server over `CONNECT host:port`, the local egress proxy observes the target hostname, port, resolved IP, connected peer IP/port, and total bytes transferred for every HTTPS hop, while path-level inspection of encrypted redirect targets inside an allowed Facebook host occurs in Chromium/Playwright (`_on_response` / DOM barrier classification). Any redirect to a disallowed host, private/loopback IP, or unapproved port is blocked at the `CONNECT` boundary before any TCP connection is opened.
2. **Unsandboxed Chromium renderer on Ubuntu (`chromium_sandbox=False`)**: On Ubuntu 24.04+ systems where `kernel.apparmor_restrict_unprivileged_userns=1` is enabled, Chromium's unprivileged user-namespace sandbox fails to launch unless an operator-installed AppArmor profile grants `userns` to the Playwright Chromium binary. `PlaywrightBrowserDriver` therefore launches Chromium with `chromium_sandbox=False` so local integration tests and default operator setups work out of the box. In this mode the renderer process is unsandboxed at the OS user-namespace layer; containment relies on the session-local `BrowserEgressProxy`, disabled Chromium DNS/QUIC/WebRTC, blocked service workers/downloads/popups, and strict session bounds. Operators who require OS-level renderer sandboxing should install an AppArmor profile for the Chromium binary under `/etc/apparmor.d/`.

## Round-4 safety and evidence rules

The operator host allowlist is an exact, nonempty set. Config loading rejects every
wildcard, IP literal, local hostname, invalid or empty entry with the fixed
`E_INPUT_INVALID: Invalid Facebook host allowlist`. Invalid entries never silently
disappear, and an empty effective set never grants unrestricted egress. The default
explicit Facebook/CDN host list is retained.

Egress diagnostics retain only fixed reason/purpose classifications and counts.
No denied hostname, URL, path or query is persisted or returned, even when a page
encodes cookie/localStorage bytes into a refused destination. In-memory transport
state is private to the session; summaries, checkpoints, events and errors contain
no such source-controlled diagnostic strings.

Authorized post identity requires exact, case-sensitive actor and post-token equality.
Primary-host aliases and accepted permalink syntaxes with those same tokens are
equivalent; numeric story_fbid and pfbid tokens are never inferred equivalent.
Main-frame routes, every observed frame navigation and the final URL are checked
before extraction; extracted actor/id mismatch stops with E_REFERENCE_INVALID.
New document requests are aborted where Playwright routing (or the plaintext
fixture proxy) exposes them. HTTPS 302 paths inside CONNECT can already have been
requested before Playwright observes the final navigation; no data, source or item
from that document is stored or reported. Script/meta navigation is route-blocked.

All WebSockets, including worker WebSockets and WSS to approved hosts, are closed
by context.route_web_socket before the first navigation. No upstream WebSocket
connection is established; CONNECT host/IP checks remain a separate boundary.

B13 classifies login URLs as E_AUTH_REQUIRED and checkpoint/challenge URLs as
E_CHECKPOINT_REQUIRED from observed main-frame navigations and the final URL,
before parsing any underlying post container. All browser barriers stop without
retry; no synthetic DOM marker is needed for an encrypted redirect barrier.

Proxy policy/resolution waits are bounded by the read timeout and interruptible on
close. Closure fences dialing and socket registration; once close returns, no
upstream connection can occur later. Python cannot cancel an in-flight NSS lookup;
abandoned daemon resolution tasks have no dialing capability and a process-wide
16-slot bound, released only when their lookup returns. Capacity exhaustion refuses
requests. An already entered dial is bounded by the socket timeout and closure waits
for it before closing every registered socket.

Resume checkpoints persist cumulative expansion, byte and accepted-item consumption.
Clicks are charged before execution; observations persist consumption before parsing.
A fresh proxy receives only remaining bytes. Accepted reliable identities are stored
as bounded digests so unchanged re-observation does not refill or consume the item
budget again; edits remain revisions of the same accepted identity. Fixture byte
accounting uses cumulative UTF-8 observation bytes; real sessions use the larger
of that accounting and relayed wire bytes. Wall-clock fencing remains core-owned.

On resume, weak observations without native IDs are never re-imported. Neither text
nor DOM position is a deduplication key. Previously committed weak items remain; new
weak observations may be omitted, with weak_identity_resume and partial coverage.
Reliable identities still support edits and new observations.

After an expansion, the production browser waits within the read deadline for a
new relevant comment/reply node or changed identified content, or a classified
HTTP failure response. An unchanged loaded document or disappearing button is
not progress evidence. Deadline expiry records expansion_progress_timeout with
partial coverage and comments_empty=false; HTTP failures retain their own codes.

B13 accumulates reliable observations across expansions by platform/scope/native ID
and preserves changed text as ordered revisions. Pagination/virtualization cannot
discard a previously seen reliable comment. The union consumes the item budget;
retention has at most max_items plus one lookahead identity, with expansion-bounded
revisions. Weak records are retained only from the initial snapshot; later weak
observations add weak_identity_expansion/partial instead of text or position merging.

The pinned Playwright 1.63 sync WebSocket callback runs on its asyncio loop; the
close handler awaits the underlying route implementation instead of nesting a
synchronous API call. The local WSS regression covers page and worker closures.

The pinned browser does not route worker WebSockets through Playwright's WebSocket
hook. Dedicated/shared/service workers are therefore paused by CDP before their
first script and terminated, in addition to blocking service-worker registration.
This intentionally disables worker-backed page features; unavailable content remains
a coverage gap. Real loopback tests prove both dedicated and shared worker WSS
cannot issue a handshake, while an approved page WSS receives a closed event.

Genuine empty comments require an identified post, a present comments section,
explicit comments-exhausted evidence, a rendered count of zero, no observed comments
across snapshots, no controls and no progress uncertainty. For fixtures that evidence
is data-comments-exhausted=true (or JSON comments_exhausted=true); LIVE mappings
remain unverified. Absence of controls alone never proves exhaustion. Contradictory
counts add rendered_count_conflict; positive counts without sufficient comments or
controls add missing_comments_controls; absent exhaustion evidence adds
comments_scope_unverified. Every gap yields partial coverage and incomplete post
evidence, never scope_satisfied. Counts whose semantics are unknown are not proof.

Shared workers lack the page opener relation, so a separate browser-level CDP
auto-attach pause covers them before script execution. Dedicated workers use the
page-related target pause. Both paths close the target before resuming any script.
