# Bounded Facebook Post Collection (B13)

`metamuse.facebook` implements operation `collect.facebook_post` for bounded collection of a single approved Facebook page type (`post_permalink`).

## Scope, Page Type, and Narrow Dispatch

- **Single page type (`post_permalink`)**: Only single-post permalink URLs on `facebook.com`, `www.facebook.com`, `m.facebook.com`, or `mbasic.facebook.com` are accepted (`/posts/<id>`, `/<actor>/posts/<id>`, `/permalink.php?story_fbid=<id>&id=<actor>`, `/story.php?story_fbid=<id>&id=<actor>`). Profile timelines, groups, watch feeds, search, reels, and other URL shapes are rejected before a job is created with `E_UNSUPPORTED`.
- **Narrow dispatch**: `collect.submit_collect` delegates to `facebook.submit_facebook_collect` only when `facebook.is_facebook_post_request(request)` is true: every input must have `kind == "url"` with a parsed hostname in `FACEBOOK_POST_HOSTS`, and `resources` must be a non-empty subset of `{"post", "comments"}`. Media-only resource requests (`media_metadata`, `subtitles`, `media_file`) are left to B12 (`remote_media.py`), and non-Facebook URLs retain `collect.py`'s existing `E_UNSUPPORTED` path.

## Bounds and Checkpoints

Every job is bounded by both workspace/job limits and `[facebook]` budgets:

- `max_items` and `max_bytes`: Enforced per job against workspace ceilings (`[limits]`).
- `wall_clock_seconds`: Enforced per job via `JobBudget`.
- `max_pages_per_job` (1–10, default `3`): Maximum page loads per job.
- `max_expansions_per_page` (0–50, default `5`): Maximum comment/reply expansion actions per page.
- `action_delay_ms` (0–60,000, default `1500`): Conservative pacing delay between expansion actions (no rate is claimed to be "safe").
- `read_timeout_seconds` (1–120, default `30`): Navigation and selector timeout.
- **Checkpoints and resume**: Progress is checkpointed every 50 items (`cursor`). On restart, already-committed input indices and records are skipped without duplicating revisions.

## Configuration Keys (`[facebook]`)

Configured in `metamuse.toml` under `[facebook]` (`extra="forbid"`):

| Key | Type / Default | Purpose |
| --- | --- | --- |
| `enabled` | `bool = False` | Master switch; must be `true` to submit or run jobs. |
| `approved_page_types` | `tuple[str, ...] = ()` | Must explicitly include `"post_permalink"`. |
| `auth_ref` | `str \| None = None` | Secret store reference holding Playwright storage state JSON. |
| `allowed_hosts` | `tuple[str, ...]` | Exact/wildcard HTTPS host allowlist for document and subresource egress. |
| `max_pages_per_job` | `int = 3` | Page budget per job (`1..10`). |
| `max_expansions_per_page` | `int = 5` | Expansion click budget per page (`0..50`). |
| `action_delay_ms` | `int = 1500` | Pacing delay in milliseconds (`0..60000`). |
| `read_timeout_seconds` | `int = 30` | Per-navigation timeout in seconds (`1..120`). |

No fixture or loopback switch exists in operator configuration; supplying `fixture_url_mode` (or any unknown key) fails config loading with `E_CONFIG_INVALID`.

## Operator Steps and Capability Status

1. **Install optional extra and browser binary**:
   - `uv sync --extra facebook` (installs `playwright==1.63.0` with lazy import).
   - `uv run playwright install chromium` (operator step; code and tests never download browsers).
2. **Import storage state secret**:
   - Store a Playwright `storage_state` JSON payload in the B08 `SecretStore` under `auth_ref`.
   - `sanitize_storage_state` keeps only HTTPS (`secure=True`) cookies whose parsed domain is `facebook.com` or `*.facebook.com`, strips `origins`/localStorage, and never logs, envelopes, exports, or includes cookie values in `repr`. Installed browser profiles are never scanned.
3. **Configure `metamuse.toml`**:
   - Set `[app] network_mode = "bounded_collect"`.
   - Set `[facebook] enabled = true`, `approved_page_types = ["post_permalink"]`, and `auth_ref = "<ref>"`.
4. **Mint B08 `AccessGrant`**:
   - Issue an active `AccessGrant` in the store with `platform="facebook"`, `account_ref=auth_ref`, `scope` containing `"collect.facebook_post"` and `"post_permalink"`, `target_refs` containing the canonical permalink URL, and `policy_digest = permit_digest(canonical_url, auth_ref)`. Pass `access_grant_id` in the collect request.
5. **Capability reporting (`metamuse_collect.facebook_post`)**:
   - `collect.capability_status` keeps `metamuse_collect` unchanged for local collection and adds backend row `metamuse_collect.facebook_post`:
     - `environment`: `missing_dependency` (Playwright not importable or Chromium binary absent), `needs_auth` (missing/invalid storage state for `auth_ref`), or `ready`.
     - `permission`: `not_configured` (disabled, `"post_permalink"` not approved, or `network_mode == "offline"`) or `granted_scope`.
     - `verification`: `fixture_passed` (never `live_verified` without authorized LIVE evidence).

## Parsing, Identity, Parents, and Provenance

- **Identity**: Reliable items use `(platform="facebook", native_scope=post_id, native_id)`. Items without a native ID receive a weak `itm_` ID and are always inserted as new items.
- **Parents (AT-004)**: Top-level comments link to the post's `native_id`; replies link to their parent comment's `native_id`. If a parent ID has not been observed in the same project/platform/scope, `link_status` remains `unresolved_parent`.
- **Revisions (AT-013)**: Re-observing a reliable item with changed text appends a new revision. Not seeing an item in a subsequent run never marks it deleted.
- **Provenance (AT-017)**: Sources record `collection_method = "browser_assisted"`, `upstream_collection_method = "browser_session:<session_id>"` (never `manual`), `original_url`, and `fetched_at`.
- **Content is data (AT-032)**: Post and comment text is stored strictly as inert UTF-8 data and never interpreted as prompts, instructions, or permissions.

## Failure Classes and Coverage Gaps (AT-041)

Unexpanded `"more comments"` or `"more replies"` controls (or reaching `max_expansions_per_page` / `max_items`) record explicit gap entries in `result.gaps`, producing `coverage = "partial"` and job status `partial` (never `complete` or `scope_satisfied`).

Distinct failure conditions produce explicit error codes and stop reasons without retry or evasion (no stealth plugins, fingerprint spoofing, CAPTCHA solvers, or proxy/account rotation):

| Failure condition | Error code | Stop reason | Job status |
| --- | --- | --- | --- |
| Login wall or redirect to `/login` | `E_AUTH_REQUIRED` | `login_wall` | `failed` |
| Checkpoint / challenge page or redirect | `E_CHALLENGE_REQUIRED` | `challenge` | `failed` |
| HTTP 403 response | `E_REMOTE_FORBIDDEN` | `http_403` | `failed` |
| HTTP 429 or rate-limit notice | `E_RATE_LIMITED` | `rate_limited` | `failed` |
| Missing post container / malformed structure | `E_PARSE_FAILED` | `parser_failed` | `failed` |
| Valid post with zero comments and no gaps | — | `input_exhausted` (`empty_input` if post excluded) | `succeeded` |

## Egress Interception and Verification Limits (AT-030)

- **Pre-job and route interception**: Before a job is created, `submit_facebook_collect` validates every URL with B08 `check_url` / `OutboundURLPolicy` and the real DNS resolver, refusing loopback, RFC1918 private, link-local, and metadata addresses. During execution, `PlaywrightBrowserDriver` attaches `context.route("**/*", ...)` so every main-frame navigation, redirect, XHR/fetch, stylesheet, script, image, and CDN subresource is parsed and checked via `EgressGuard.check_request_url` before `route.continue_()`. Disallowed requests are immediately aborted (`route.abort("blockedbyclient")`).
- **Browser hardening**: Service workers are blocked (`service_workers="block"`), downloads are disabled (`accept_downloads=False`), popups are immediately closed on creation, and proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`) are refused before launch.
- **Peer address check (`Response.server_addr()`)**: On every HTTP response, `PlaywrightBrowserDriver` inspects `response.server_addr()` and validates the connected IP address against `validate_ip_address` (blocking loopback, private, link-local, multicast, reserved, and unspecified IPs).
- **What cannot be verified**:
  1. Chromium performs its own DNS resolution and socket connection inside the browser process after `route.continue_()` is called. Because Playwright does not expose a hook to pin the resolved IP address into Chromium's socket connect call, a theoretical time-of-check-to-time-of-use (TOCTOU) DNS rebinding window exists between Python's pre-request `check_url` DNS lookup and Chromium's TCP handshake. `Response.server_addr()` detects a rebound non-global peer IP as soon as response headers arrive and aborts the run before any content is parsed, but cannot prevent the initial TCP/TLS handshake without an external OS/network-namespace firewall.
  2. When `response.server_addr()` returns `None` (for example on cached responses or custom non-Playwright test drivers), verification relies on the pre-request `OutboundURLPolicy` DNS resolution check.
