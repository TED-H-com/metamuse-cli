# Bounded Facebook post collection (B13)

`collect.facebook_post` reads one approved `post_permalink` through Playwright and
`BrowserEgressProxy`. CLI/MCP share the same app, store and jobs. Evidence is
OFFLINE/FIXTURE/local INTEGRATION; AT-039 LIVE remains blocked and unverified.

## Configuration and operator steps

Install `uv sync --extra facebook` and, separately as an operator step,
`uv run playwright install chromium`. Code never downloads a browser. Import a
Playwright storage-state JSON with B08 `auth import`; set the matching account and
secret reference in `metamuse.toml`. Installed browser profiles are never scanned.
Set `[app] network_mode = "live"` and explicitly approve this page type before use.

| `[facebook]` key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Operator master switch. |
| `approved_page_types` | `[]` | Must include `"post_permalink"`. |
| `account`, `auth_ref` | absent | Matching B08 account and storage-state reference. |
| `allowed_hosts` | explicit primary Facebook/CDN list | Nonempty exact host set. |
| `max_expansions` | `10` | Cumulative click budget, 0–100. |
| `pacing_ms` | `1500` | Delay between clicks, 0–60000; no safe rate is claimed. |
| `allow_unsandboxed_browser` | `false` | Operator-only security exception. |

Wildcard, IP, local, invalid and empty allowlist entries fail config loading with
fixed `E_INPUT_INVALID: Invalid Facebook host allowlist`; entries never disappear
silently and an empty set never means unrestricted. Default hosts are explicitly
listed in `config.DEFAULT_FACEBOOK_ALLOWED_HOSTS`. Unknown configuration keys fail.
Entries are bare hostnames; query/fragment delimiters are refused, never stripped.
There is no operator fixture/loopback mode. Browser exceptions cannot come from job
payloads, MCP arguments, permits or source content.

Create an access permit for `provider="facebook"`, the configured account,
`operations=["collect.remote"]`, and the exact `facebook.permit_digest` of the URL
and requested resources. Supply its ID as `access_grant_id`. A permit use is reserved
once at the external read boundary; resume re-verifies it without another use.
`accepted` means queued; a worker must run it.

## Browser sandbox is required

Default production launches pass `chromium_sandbox=True`. A verified launch failure stops with
`E_DEPENDENCY_MISSING`; there is no automatic unsandboxed retry. `next_action` gives
both remedies, in this order:

1. Install an AppArmor profile granting `userns` to the installed Playwright
   Chromium executable. Generic template; replace both placeholder paths for the
   operator's installation and load it through the OS's AppArmor administration:

   ```text
   abi <abi/4.0>,
   include <tunables/global>
   profile metamuse-playwright-chromium /ABSOLUTE/PATH/TO/PLAYWRIGHT/CHROMIUM flags=(unconfined) {
       userns,
   }
   ```

   Put the profile under `/etc/apparmor.d/PROFILE_NAME`. This narrow exception
   permits Chromium's own user-namespace sandbox; it is not an independent renderer
   filesystem policy. Verify launch again after installing it.
2. Explicitly set `[facebook] allow_unsandboxed_browser = true` after reviewing the
   risk and providing OS isolation.

With the exception enabled, a renderer compromise runs with the operator's UID and
can read the workspace, secret store and any readable age identity. Use a dedicated
OS user or container, minimal filesystem mounts, no operator home/age identities or
workspace-wide secrets, only ephemeral required Facebook state, and network controls
that also apply to native sockets/UDP/DNS. The HTTP proxy alone does not contain native
renderer compromise.

Every Facebook result’s data.browser_sandbox records observed enabled, disabled_by_operator
or unverified. Enabled requires no --no-sandbox in CDP command-line inspection and, on
Linux, renderer Seccomp: 2. Unverified is refused without the explicit exception.
Submission/requested scope and pre-launch checkpoints say unverified; requested mode
is a separate browser_sandbox_requested field. Launch failures never claim enabled. Inspector/doctor envelopes
carry the fixed warning `Facebook browser sandbox is disabled by operator configuration`
under the exception. This uses the existing envelope warning path and Capability fields.

`metamuse_collect.facebook_post.environment` is `missing_dependency` when Playwright
or its binary is missing, `needs_auth` for missing/invalid state, `unavailable` when
required sandbox launch fails, and `ready` only after a verified sandbox launch or
explicit exception. The local launch probe is headless, navigates nowhere, carries no
state, points its proxy at a closed local port, disables browser DNS, has a 10-second
process-group deadline and a bounded 60-second cache. Verification remains
`fixture_passed`, never `live_verified`. Local collection's capability is independent.

## Identity and navigation

Primary hosts are `www.facebook.com`, `facebook.com`, `m.facebook.com` and
`web.facebook.com`. Direct inputs accept `/<actor>/posts/<post_id>`, or
`/permalink.php` and `/story.php` with exactly `story_fbid` and `id` parameters.
They normalize to `https://www.facebook.com/<actor>/posts/<post_id>`.
Profiles, group posts/feeds, reels, videos, watch, photos and other page types are
unsupported. Dispatch accepts only Facebook URL inputs requesting post/comments;
media-only requests remain on the B12 path.

Authorized identity is exact, case-sensitive actor and post-token equality. Host
aliases and accepted permalink syntaxes containing the same tokens are equivalent.
Numeric `story_fbid` and `pfbid` tokens are never inferred equivalent. Routes, every
observed main-frame navigation and the final URL are checked before extraction;
extracted actor/id must also match. Mismatch stops with `E_REFERENCE_INVALID` before
source/item import. Missing identifiers are not filled in from authorization.

Normal script/meta navigations are refused before their document request where
Playwright exposes the route. Encrypted 302 paths inside an approved CONNECT tunnel
can have been requested before Playwright observes the navigation: the proxy does not
MITM TLS, and Playwright routing omits redirect hops. Nothing from a mismatched
page is stored or reported. Login URLs become `E_AUTH_REQUIRED`; checkpoint/challenge
URLs become `E_CHECKPOINT_REQUIRED`, even with an underlying valid post container.

## Post share links

Inputs also accept `/share/p/<code>/` on primary Facebook hosts, with code matching
`[A-Za-z0-9]{1,64}`, exactly one trailing slash, and no query/fragment. They normalize
to `https://www.facebook.com/share/p/<code>/`. B08's access digest binds that normalized
share URL and resources; the page type remains post_permalink. Other share kinds,
reels, watch/videos and group posts return E_UNSUPPORTED; next_action names the
remote-media (B12) path for Reels/videos and explicitly says group posts are unsupported.
The B12 implementation is unchanged.

Share navigation uses the same proxy, URL barriers and bounded deadline. A 302 or
immediate JS/meta-refresh hop can resolve to an accepted single-post permalink.
Path-permalink tracking queries/fragments are removed before authorization; the
semantic id/story_fbid query is parsed for the accepted PHP permalink forms and the
canonical result is the query-free actor/posts/token URL. Any other final page type,
including an unresolved share page, is unsupported and produces no extraction.
The first observed main-frame supported post becomes the authorized identity; subsequent
main-frame changes must match it. Source actor/id must also match before import.

Step data/observed scope record requested_locator and resolved_permalink. Sources
record the same pair in use_basis, requested input in original_url and resolved
canonical post in canonical_url. Checkpoints retain the resolved canonical identity.
Resume navigates through the original share URL again and requires that same actor
and post token. Mismatch stops with distinct E_DATA_STALE before importing the new
page; previously committed items remain and no second permit use is consumed.
Encrypted redirects retain the previously stated pre-request visibility limitation.

## Egress and secret handling

The session-local proxy listens on `127.0.0.1:0`, validates CONNECT authority and
fixture plaintext requests before dialing, pins the first checked IP, verifies the
actual peer, and counts both tunnel directions against remaining bytes. Production
refuses plaintext HTTP, bodies, upgrades, unsupported methods, self-proxy targets and
malformed input. CONNECT ports are bounded before conversion; every parse failure
returns a fixed 403. Concurrency is limited to 16 transport slots.

Chromium uses proxy-bypass-list `<-loopback>`, DNS `MAP * ~NOTFOUND` (excluding the
proxy's loopback IP), QUIC disabled and nonproxied WebRTC UDP disabled. Ambient proxy
variables are refused. Downloads, popups and service workers are blocked.
Chromium CDP installs per-URL offline rules for `ws://*:*/*` and `wss://*:*/*`
before navigation. This network-layer refusal covers WebSocketStream and the
Playwright shim’s _apiPassThrough; setBlockedURLs alone does not stop WSS.
The context WebSocket route additionally closes ordinary page sockets. Its callback
awaits the pinned underlying implementation. Dedicated/shared workers are paused,
are verified paused and destroyed without resuming; shared
workers use browser-level attachment. Worker-dependent content may be unavailable.
Native Network WebSocket creation counts websocket_blocked and stops the job; any handshake response stops
with fixed E_PROTOCOL_MISMATCH before import, including events dispatched at close.

Resolution/policy waits are bounded by the read deadline and interrupted by close.
Shutdown fences dialing/registration; after close returns no later upstream target
connection can occur. Python cannot cancel a running NSS lookup: abandoned daemon
resolution tasks have no dialing capability and a process-wide 16-slot bound, released
when lookup returns. Exhausted resolution capacity refuses requests. An entered dial
has a socket deadline and shutdown waits for it before closing registered sockets.

Egress diagnostics contain only fixed reason/purpose classifications and counts, never
a denied hostname, URL, path or query. This applies to results, checkpoints, events,
errors and `get_job`, including encoded or case-folded secret hostnames. Transport
correlation state stays in memory. Cookie/localStorage values never enter diagnostics.
Cookies and storage origins are filtered by parsed primary hostname. Malformed entries
or origins, including invalid/out-of-range/non-443 ports or credentials/path/query/fragment,
produce fixed `E_AUTH_REQUIRED: Invalid Facebook storage-state origin`; inspection
still returns its normal `needs_auth` row. Valid unrelated origins are dropped.

## Bounds, accumulation and resume

Jobs enforce max items/bytes, persisted core wall clock, workspace ceilings, expansion
budget and read deadline. Clicks are charged before execution; observations checkpoint
consumption before parsing and final wire usage after shutdown, including navigation
that stops at a barrier. Resume persists cumulative expansions, bytes, accepted count
and a bounded set of accepted reliable identity digests, and allocates only remaining
bytes. Reliable re-observation does not consume the item allowance again; edits append
revisions. Wire bytes may include the received chunk that trips the cap, which is not
forwarded. Fixture accounting uses UTF-8 observations; real sessions use the greater
of that accounting and relayed wire bytes for the attempt.

Observations accumulate across pagination/virtualization by platform/scope/native ID,
retaining ordered text transitions, including a return to an earlier text value.
Each revision records its contributing snapshot's input_sha256 plus the requested
locator, resolved permalink and verified permit/account. A new Source's input_sha256
identifies its first contributing snapshot. At most max_items plus one lookahead identity is
retained, with expansion-bounded revisions; the union consumes the budget. Weak records
are retained only from the initial snapshot. Later weak records add
`weak_identity_expansion`. After a committed batch, resumed weak records are skipped with
`weak_identity_resume` and partial coverage. Text or position is never a deduplication
identity. Previously committed weak items remain; newly visible weak data may be omitted.
No rendered HTML or record bodies enter checkpoints.

## Completeness, parent links and text

After expansion the production driver waits within the read deadline for a new relevant
comment/reply node or changed content, or a failure response correlated to the trusted expansion click. An already
loaded document or disappearing button is not progress. Timeout adds
`expansion_progress_timeout`, partial coverage and `comments_empty=false`.

Genuine empty comments require an identified post, a comments section, affirmative
exhaustion evidence, rendered count zero, no comments across observations, no controls
and no uncertainty. Synthetic fixtures use `data-comments-exhausted=true` or JSON
`comments_exhausted=true`; LIVE mappings remain unverified. Absence of controls alone
never proves exhaustion. Contradictions add `rendered_count_conflict`; positive counts
without sufficient comments/controls add `missing_comments_controls`; no exhaustion
evidence adds `comments_scope_unverified`. Unexpanded comments/replies and unknown
parents also produce explicit gaps. Every gap is partial, never scope_satisfied.

Top-level comments link to the post and replies to identified parents in the same scope.
Known missing parent IDs use core `unresolved_parent`; unknown parent IDs independently
add `parent_comment` to new reply evidence and job gaps. Missing observations never delete
items. New incomplete post/reply evidence is marked incomplete.

HTMLParser decodes character references exactly once. Source text retains edge
whitespace, explicit empty bodies, br newlines and nested block boundaries. Void tags
still retain barrier and attachment attributes without changing body depth, including
self-closing spellings of void elements. JSON text
is preserved as given. No normalized text replaces the source; revision hashes use
canonical JSON `{text: preserved_text}` without whitespace/Unicode normalization.
All content is inert data and cannot mint permissions or trigger tools.

Sources use `browser_playwright` / `facebook_post_permalink`, canonical/original URL,
fetch time and permit provenance; automated collection is never labelled manual.
Barriers stop without retries: login/auth, checkpoint/challenge, 403/access denial,
429/rate limit and selector drift have distinct outcomes. Byte-budget cutoff is partial
with `E_LIMIT_REACHED`. Parser failure and uncertainty are never empty success.

## Verification limits

Tests use synthetic redistributable data and loopback servers, with an explicitly
labelled unsandboxed test configuration on hosts that cannot start Chromium's sandbox.
A separate real sandbox launch test skips with a reason if unavailable. TLS fixtures
use a test-only certificate exemption; production TLS validation is unchanged.
AT-039 must verify actual Facebook DOM, async responses, localized barriers, raw times,
parent links, counts and completeness under an explicitly approved account/URL scope,
and record sandbox/OS isolation, versions, date, bounds and human comparison. No LIVE
requests or success claims are made here. Existing third-party provenance remains in
THIRD_PARTY_NOTICES.md; no new dependency or core schema/migration is added.

Chromium explicitly advertises automation. This also permits CDP command-line
inspection in the real sandbox test, which verifies successful default launch with
no --no-sandbox flag and Linux renderer Seccomp: 2. Local tests skip explicitly when
unavailable; METAMUSE_REQUIRE_BROWSER_TESTS=1 makes browser skips fail in CI.


A present but unparseable Facebook comment count adds comment_count_unparsed to post and job gaps; affirmative exhaustion never converts that uncertainty into scope_satisfied.


Facebook counts and remaining hints accept only nonnegative integers or 1–18 ASCII decimal digits. Other present forms add fixed *_count_unparsed gaps; Unicode digits and oversized decimals never reach an unchecked int conversion.


Facebook parent identifiers are stripped and validated against [A-Za-z0-9._:-]{1,128} in both DOM and JSON. Invalid/empty reply parents are unknown and add parent_comment to the item and job, independently of unresolved_parent.


Facebook comment identifiers use the same stripped nonempty ASCII token validation as parents. An invalid token is absent/weak; distinct weak records are never merged by text or position.


Facebook weak-identity resume suppression begins only after an accepted batch has committed. A usage-only checkpoint from a login wall does not suppress a first import of weak comments; cumulative byte/expansion usage still survives.


Facebook blocks ws:/wss: at Chromium’s network layer with Network.emulateNetworkConditionsByRule offline rules matching both schemes and all ports. setBlockedURLs alone is insufficient. Page WebSocket routing is supplemental. Workers are verified paused before their first script and destroyed without any resume command. Each observed attempt has the fixed websocket_blocked reason; any native socket creation or observed handshake response fails E_PROTOCOL_MISMATCH before import, including events drained at close.


Facebook navigation failures use only the main-document request/redirect chain and its uncompleted CONNECT authorities. Refusals of WebSockets, workers, subresources or already-responded document authorities do not choose that error. Resolver capacity/timeouts use E_NETWORK_TIMEOUT with DNS-oriented advice, never a permit/policy denial.


Only a fetch/XHR synchronously initiated by the trusted expansion click can classify an expansion response failure. Background and proxy-aborted responses are ignored. Such a failure imports observed data with expansion_failed and partial coverage; document auth/access/barrier outcomes remain distinct. Asynchronous endpoints without click provenance are treated conservatively as progress uncertainty, not guessed failure attribution.


Facebook navigation barriers, page shapes and identity adoption apply only to the main frame. All network subframe document requests are aborted with blocked_subframe without setting navigation_error. A share identity is adopted only from the main frame.


Facebook sandbox state is observed from Browser.getBrowserCommandLine and, on Linux, renderer /proc/<pid>/status Seccomp: 2. No --no-sandbox is allowed for enabled. Failed observation is unverified and refused without the operator exception. The production executable environment override is removed; tests inject an executable through a seam. Capability launch checks verify this running state, not exit status alone.


Facebook cookie path/expiry/boolean/sameSite/name/value shapes are validated before launch; expiry is -1 or finite Unix seconds through 253402300799, and path starts with /. Invalid state returns fixed E_AUTH_REQUIRED and needs_auth. Context/state failures suggest auth repair. Only an actual required sandbox launch failure offers the unsandboxed exception.


After a Facebook post identity is bound, a main-frame navigation to an unknown non-post shape returns E_CHECKPOINT_REQUIRED with the distinct message “Facebook navigation left the approved post” and browser-session review advice. Unsupported operator inputs retain E_UNSUPPORTED. Share destinations before any post is bound retain the separately documented resolution-type refusal.


Facebook HTML text includes inline image alt text (including emoji), excludes script/style/template and hidden subtrees, and retains the existing one-decode/newline/edge-whitespace contract. Real Chromium snapshots mark computed CSS hiding for the common parser; fixtures express hiding through attributes or inline style. JSON text remains unchanged.

CI uses Ubuntu 24.04, syncs the Facebook extra alongside the existing development
requirements, and installs Chromium through the locked Playwright package. The
hosted runner enables unprivileged user namespaces with the AppArmor sysctl before
testing. All workflow actions use full commit SHAs. METAMUSE_REQUIRE_BROWSER_TESTS=1
converts skipped Facebook/browser tests into failures; locally an absent Chromium
or unavailable sandbox still has an explicit skip reason. The suite includes
sandboxed launch verification and sandboxed WebSocketStream/_apiPassThrough tests;
other loopback integration fixtures use a labelled operator exception. No browser
test is excluded from hosted CI. Test scratch is a unique disk directory cleaned
on exit. A hosted Actions result requires the orchestrator to publish this branch.

Structural JSON scripts require both data-fb-post-payload and application/json MIME
type. Every other script is excluded from body text, even if it carries that marker.

Synthetic review/share-resume jobs use a 300-second wall budget to allow host storage
stalls while committing batches. This changes only test requests; their two-second
browser deadlines and production limits remain unchanged.

Embedded JSON integer tokens over 128 characters stay lexical for field validation.
Oversized counts add their fixed *_count_unparsed gap and retain the observed post;
they do not turn a valid JSON document into a malformed-payload error.


Facebook stop markers (`data-fb-state`, `data-page-state`, `data-checkpoint`, login
and checkpoint form IDs) are inspected on every element, including hidden, head
and void elements, in both drivers. Completeness evidence such as
`data-comments-exhausted` counts only in visible subtrees under the shared text
visibility rules. Chromium snapshots strip every page-authored `data-metamuse-*` attribute
from the clone, then stamp separate computed display and visibility markers.
Only trusted Chromium observations honour those markers; raw HTML and the fixture
driver ignore them. Snapshots preserve the page's own `hidden` attribute and never
mutate the live DOM. `display:none` and `hidden` exclude the whole subtree;
`visibility:hidden`/`collapse` inherit but descendants can restore `visibility:visible`.
Fixtures apply the same semantics to inline CSS. `aria-hidden=true` deliberately
excludes the whole subtree in both drivers: accessibility-hidden duplicates are not
collection evidence even when painted. It cannot be overridden by CSS visibility.
Barrier markers are always inspected, while hidden completeness evidence never counts. Head/meta and hidden inputs are invisible in both
drivers. Text and structural JSON extraction retain their existing rules.


Facebook browser routes validate the destination through the shared URL policy
(scheme, credentials, port, exact host allowlist and all resolved IPs) before
classifying login/checkpoint or off-post navigation. Denied destinations return
`E_SCOPE_DENIED` with the fixed message “Facebook browser destination is denied
by egress policy”. Production requires HTTPS. Chromium route DNS checks share
the proxy's bounded resolver slots/deadline; DNS capacity and timeout failures
retain `E_NETWORK_TIMEOUT`. The proxy still independently checks every dial/hop.


Facebook guard failures fail closed with fixed `E_PROTOCOL_MISMATCH`: a failed
close/cancel/abort is ignored only if the target or request is provably gone;
missing liveness evidence is failure. Worker attachments must report
`waitingForDebugger=true`, and `Target.getTargets` must confirm destruction. No
worker resume command exists. The redundant per-worker network-rule step is
removed: no untrusted worker source executes, including nested worker creation.
Dedicated workers need a fixed CDP `Runtime.evaluate("self.close()")` in their
paused realm because `Target.closeTarget` alone leaves them alive in this build.
Shared workers use `Target.closeTarget`. Destruction is checked after callbacks
return, before import, with a bounded five-second wait; no resume command is sent. Popups,
downloads, route aborts and session teardown use the same fail-closed rule.


Facebook requires Chromium `--block-new-web-contents`: renderer popup creation
returns null, including URL/blank `window.open`, target=_blank links/forms,
noopener, pristine iframe open and nested popup attempts. Before init scripts or
navigation, the same sandbox verification blank page tests native and fresh
iframe open; the proxy is bound but closed (not listening) and Chromium DNS is
disabled. A failed or inconclusive check returns fixed `E_DEPENDENCY_MISSING`
“Facebook browser popup creation control is unavailable”, with no operator opt-out.
CDP `Page.windowOpen` counts refused attempts as `popup_blocked`; refusal alone
permits success. Browser-level auto-attach pauses foreign page/iframe targets
before their first script, installs network event detection and closes them.
Native WebSocket creation or handshake responses in any attached target stop
with the existing fixed `E_PROTOCOL_MISMATCH` before import. Workers never resume.
The `WebSocketStream` init shim is defense in depth alongside Playwright's
ordinary WebSocket routing; it is not the popup or socket boundary.


A Facebook proxy byte cutoff immediately shuts down both client and upstream
sockets for all active tunnels and refuses subsequent dials/registrations.
Upstream candidates are registered before connect can block; teardown closes
them before waiting for the dial barrier, so pending connects are included. A
50 ms browser-loop watcher reacts to byte cutoff and latched socket/guard
failures, closing the affected page to interrupt in-flight
navigation, click and expansion operations, instead of waiting for the full
`read_timeout_seconds`. All browser operation exceptions recheck the byte latch
and return `E_LIMIT_REACHED` with partial coverage; already observed data remains
available. The watcher uses the pinned Playwright implementation loop, as does
the existing async WebSocket close adapter. Tests bound hangs at an independent
90-second subprocess deadline, with 300-second read and job budgets.


Route validation pins its validated address set by authority for this bounded
Facebook session. Every later URL still passes the shared scheme/credential/port/
host/IP policy, using that pinned set through the proxy dial and peer check. The
proxy does not resolve a different address between the browser check and its
validated dial. Redirect destinations on new authorities get independent checks.


A disconnected browser transport is not proof that a route, download or browser
process is gone. A failed route action is ignorable only with an observed request
failure or a closed page observed while the browser remains connected. Failed download cancellation and context/browser teardown therefore
fail closed even when CDP is disconnected; a page close exception may be ignored
only when the page's closed event proves that target has already disappeared.


The byte-limit latch also interrupts bounded route/proxy policy and DNS waits
within the existing 50 ms polling interval, without waiting for resolution or the
read deadline. Abandoned resolver tasks cannot dial, remain slot-bounded, and
release their slot when the underlying lookup returns. A sandboxed streaming
regression trips the byte budget while another URL is waiting on DNS.


Stop evidence is monotonic: both state attributes are examined independently,
and a later benign state cannot erase a barrier or form marker. When multiple
recognized stop states coexist, the parser uses the existing classification
order: rate limit, checkpoint/challenge, login wall, then access denial.
