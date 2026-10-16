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
comment/reply node or changed content. An already
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


Only DOM progress authorizes expansion progress: a new relevant comment/reply
node or changed identified content within the read deadline. Requests, response
statuses, counts and disappearing controls are neither progress nor failure evidence.
The page defines request meaning, and delegated listeners and async scheduling
cannot establish reliable request ownership; GraphQL errors may also use HTTP 200.
No owned CDP session enables the Debugger domain or async stack depth. The AST
source-constant audit test_r10_no_debugger_or_expansion_network_authority rejects
Debugger commands across the session and capture modules and checks compiled
session constants too, so a runtime wrapper cannot evade the audit.
Network.enable remains for socket guards.

A click that cannot be performed records expansion_failed: identity revalidation,
scroll, usable-quad retrieval, hit lookup, node resolution, composed hit check or
CDP mouseMoved, mousePressed or mouseReleased fails before mouseReleased completes.
After a completed click, no relevant DOM progress by the read deadline records
expansion_progress_timeout, partial coverage and comments_empty=false. Both outcomes
retain observed data; document and guard stops keep their existing precedence.
Known limitation (async_hidden_500): DOM evidence is page content. A page that
renders comments and claims exhaustion before its own request fails cannot be
distinguished from a truthful page. LIVE exhaustion mappings remain unverified.


Facebook navigation barriers, page shapes and identity adoption apply only to the main frame. All network subframe document requests are aborted with blocked_subframe without setting navigation_error. A share identity is adopted only from the main frame.


Facebook sandbox state is observed from Browser.getBrowserCommandLine and, on Linux, renderer /proc/<pid>/status Seccomp: 2. No --no-sandbox is allowed for enabled. Failed observation is unverified and refused without the operator exception. The production executable environment override is removed; tests inject an executable through a seam. Capability launch checks verify this running state, not exit status alone.


Facebook cookie path/expiry/boolean/sameSite/name/value shapes are validated before launch; expiry is -1 or finite Unix seconds through 253402300799, and path starts with /. Invalid state returns fixed E_AUTH_REQUIRED and needs_auth. Context/state failures suggest auth repair. Only an actual required sandbox launch failure offers the unsandboxed exception.


After a Facebook post identity is bound, a main-frame navigation to an unknown non-post shape returns E_CHECKPOINT_REQUIRED with the distinct message “Facebook navigation left the approved post” and browser-session review advice. Unsupported operator inputs retain E_UNSUPPORTED. Share destinations before any post is bound retain the separately documented resolution-type refusal.


Facebook rendered text includes inline image alt text (including emoji) and follows the structured capture/rendering contract below. JSON text remains unchanged.

CI uses Ubuntu 24.04 and locked development, remote and Facebook dependencies. Each
test job installs ffmpeg/ffprobe and Chromium through the locked Playwright package. The
hosted runner enables unprivileged user namespaces with the AppArmor sysctl before
testing. All workflow actions use full commit SHAs. METAMUSE_REQUIRE_BROWSER_TESTS=1
converts skipped Facebook/browser tests into failures; locally an absent Chromium
or unavailable sandbox still has an explicit skip reason. The suite includes
sandboxed launch verification and sandboxed WebSocketStream/_apiPassThrough tests;
other loopback integration fixtures use a labelled operator exception. No browser
test is excluded from hosted CI. Test scratch is a unique RUNNER_TEMP disk directory
cleaned on exit. A hosted Actions result requires the orchestrator to publish this branch.

The previous single `check` job ran Facebook, Muse, media and every other test
sequentially before the corpus and fresh-wheel checks. CI now collects the **entire
normal pytest suite in each of five jobs**, then selects core, muse, media, facebook
or privacy by the collected file path. Unknown files go to core; nested tests and
pytest's normal file patterns are retained. Selection preserves conftest ordering.
All Facebook modules share one job and one captured-input append stream, with
`test_r9_fixture_differential_corpus` last and its default seeded corpus unchanged.
Remote-media tests of the yt-dlp Facebook extractor do not call DOM fixture capture
and belong to media. New tests that capture Facebook inputs must belong to the
Facebook cohort too. See the small classifier in `scripts/ci_cohorts.py`.

Every job retains its 30-minute deadline. The matrix permits five concurrent jobs
and does not cancel peers on failure. `--maxfail=1` prints the first real traceback;
a failing cohort is incomplete and needs a cause fix on new source, never an unchanged
retry for green. A 30-second faulthandler dump is diagnostic only. Successful jobs
run every selected node, preserving existing assertions, strict xfails and browser
skip refusal. No per-test deadline or production limit changes.
The cohort Python process enables unbuffered output and faulthandler. Flushed
`COHORT_START <node ID>` and `COHORT_FINISH <node ID>` lines identify actual runtime
progress even if pytest exits abruptly. A finish line records the end of a node,
including a failed test; manifests and exit codes still determine success. These
diagnostics do not recover or explain the earlier hosted Muse exit 139.

The required `check` depends on the entire matrix, `static-wheel`, and
both `installed-journeys` and `privacy-load`. It runs
even after upstream failure, checks every job result, and verifies five manifests:
same source SHA, identical sorted full node IDs and SHA-256, nonempty disjoint
selected sets whose union equals the full collection, and completed runtime nodes
with pytest exit zero. Collection-only evidence cannot satisfy this gate. Missing,
failed or cancelled jobs cannot make `check` green. Static validation, schema drift,
corpus validation and the unchanged fresh-wheel CLI/MCP/packaged-Skill smoke run in
`static-wheel`. The generated fifteen-tool contract remains authoritative.

`installed-journeys` is an additional required 30-minute job on a clean hosted disk.
It builds a fresh wheel from the checked-out runtime SHA, syncs locked development,
remote and Facebook dependencies into a separate venv without installing the project,
then installs only that wheel offline with `--no-deps`. All jobs select uv-managed
CPython 3.12.13 and assert the actual interpreter version. The installed runner checks
all package Python/data bytes, notices and packaged Skill against the exact source,
requires package imports inside the new venv and outside the checkout, and runs from
an external disk directory. It copies only the four unmodified harness modules
`test_journey`, `test_muse_research`, `test_muse_lifecycle`, `test_safety_permits` and
the synthetic `comments_tree.jsonl`, checking their hashes before and after execution.
No conftest or external pytest plugin participates. The two selected nodes are:

```text
tests/test_journey.py::test_mcp_only_journey_followup_restart_and_export
tests/test_muse_research.py::test_one_cli_approval_two_mcp_questions_restart_citations_export
```

These are the original exact-approval and bounded-research acceptance tests, with
their existing assertions and deadlines. Both must pass without skips. This adds
installed-package acceptance alongside their existing cohort coverage; it does not
alter the five-cohort union or replace the fresh-wheel smoke. There is no retry or
pass-on-error path. A failed, missing, cancelled or skipped installed job fails `check`
even if all cohorts pass. Run this job only on the combined final repair candidate;
do not run the two journeys locally while preparing CI changes.

`scripts/check_installed_journeys.sh` uses unique RUNNER_TEMP/TMPDIR disk scratch,
rejects tmpfs/ramfs, clears Python import overrides, and cleans its scratch on exit.
Its provenance records the runtime commit, tree and raw parent IDs (including PR
merge commits), candidate SHA, interpreter, import origin, exact hashes and pytest
summary/exit. The orchestrator separately verifies the hosted runtime tree equals
the intended candidate tree. On failure, bounded synthetic MCP stderr tails and
owned process/resource/database-file-role metadata aid diagnosis; no database or
workspace content is uploaded. Cleanup targets only the pytest process group the
helper created. Diagnostic snapshots cannot recover an earlier missing failure cause.
An always-upload step retains the installed log, JUnit and provenance for seven days
under `installed-<runtime SHA>-journeys`, outside the `ci-<SHA>-*` cohort artifact
pattern. This job adds one runner and another wheel build/dependency installation.
Hosted exact-source full and installed passes plus independent review remain required;
local helper probes and collection do not establish acceptance or LIVE.

`privacy-load` adds one required 30-minute hosted job on the combined final repaired
tree. Five serial measurements each run the same unchanged six regression nodes:

```text
tests/test_muse_round5_growth.py::test_r5_5_ordinary_pairs_core_growth
tests/test_muse_round6_growth.py::test_r6_2_adversarial_grammars_linear[k=>-False-text]
tests/test_muse_round6_growth.py::test_r6_2_adversarial_grammars_linear[k=>-True-text]
tests/test_muse_round6_growth.py::test_r6_2_adversarial_grammars_linear[k=>-False-body]
tests/test_muse_round6_growth.py::test_r6_2_adversarial_grammars_linear[k=>-True-body]
tests/test_at042_review_fixes.py::test_b16a_bare_muse_cookie_must_not_leak_into_redacted_bundle
```

The fixed `1 2 3 4 5` loop stops at its first failed run and propagates its exit.
Each run has a unique disk RUNNER_TEMP basetemp, cleaned by the exit trap, and
prints the original CPU_GROWTH tables and pytest summary with `-q -s`. No size,
sample count, slope, ratio, time or RSS assertion changes. Five successful runs
must provide 25 original tables, 300 CPU samples and 30 test passes; the orchestrator
independently checks those logs. Earlier measurements on different redactor code
cannot certify a later Muse entrypoint repair. Do not execute the load runs locally
while preparing this workflow.

Each `load-N.log` includes the initial clean runtime commit/tree/parents, candidate
SHA, actual managed 3.12.13 interpreter and SHA-256 of both redactors, the CPU helper
and the three harness modules. `identity.json` and `results.tsv` retain that identity,
attempted runs, completed runs with exit zero, and the job exit. A terminated job
can leave an attempted run without a completion record. An always-upload step keeps
only these small diagnostics and logs under `privacy-load-<runtime SHA>` for seven
days, outside the cohort artifact pattern. No workspace or database is uploaded.
This adds a runner and repeated measurements alongside the five-cohort full union;
it neither replaces nor enters that union. Missing, failed, skipped or cancelled
`privacy-load` fails `check`, even if all other jobs pass. All eight execution jobs
(five cohorts, static/wheel, installed journeys and privacy load) remain bounded;
their aggregate also retains its 30-minute limit. Final hosted success, exact source
and worst resource-cap audits and independent review remain required.

Manifests contain node IDs and execution metadata, without captured fixture bodies.
An always-upload step retains manifests and diagnostic logs for seven days under
SHA/cohort-specific artifact names, including failures. Official upload/download
actions are pinned to full commits, as are checkout and uv setup. Parallel execution
uses more simultaneous runners and repeats dependency installation and collection;
it does not prove that any particular cohort will finish under its deadline. Hosted
completion and skip/xfail log inspection on the exact candidate SHA remain required.
Collection audits and old partial runs do not establish full acceptance or LIVE.

For a local **collection-only** audit (no test bodies), after an offline locked sync:

```bash
# Choose a disk directory outside the checkout for manifests and logs.
unset LD_LIBRARY_PATH
export PATH="$HOME/.local/bin:$PATH"
source_sha="$(git rev-parse HEAD)"
pytest-disktmp uv run --locked python scripts/ci_cohorts.py run \
  --cohort facebook --source-sha "$source_sha" \
  --manifest "$HOME/agent-work/tmp/facebook-collection.json" -- --collect-only -q
```

Repeat for all five cohorts and independently compare their selected sets to normal
unfiltered `pytest --collect-only` node IDs. Hashes use sorted node IDs serialized as
ASCII JSON with compact separators, then UTF-8 SHA-256. Full-suite flags that deselect
nodes (such as `-k` or `-m`) are refused by the cohort helper rather than silently
shrinking coverage. Normal pytest diagnostic flags and `--basetemp` are supported.

Structural JSON scripts require both data-fb-post-payload and application/json MIME
type. Every other script is excluded from body text, even if it carries that marker.

Synthetic review/share-resume jobs use a 300-second wall budget to allow host storage
stalls while committing batches. This changes only test requests; their two-second
browser deadlines and production limits remain unchanged.

Embedded JSON integer tokens over 128 characters stay lexical for field validation.
Oversized counts add their fixed *_count_unparsed gap and retain the observed post;
they do not turn a valid JSON document into a malformed-payload error.


Facebook captures the main HTML document through CDP in an isolated world, never
through main-world evaluation. An explicit-stack read-only walker uses pristine
DOM getters, `checkVisibility()` and computed styles. It creates no elements,
clones no document and writes no attributes, so capture cannot invoke custom-element
constructors, attribute reactions or mutation observers. CDP discovers closed shadow
roots and resolves their handles into that world. The same isolated world is scoped
to the current navigation/frame. Iframe documents are not post evidence.

Every evaluated DOM function captures its complete accessor table before touching
the tree. WebIDL getters come from prototype descriptors and methods from their
prototypes, invoked with captured `Reflect.apply`; Window global methods use their
own descriptors. This defeats form/document named-property clobbering even across
isolated worlds. The table covers Document, Node, Element, Text, Attr, template,
slot, shadow-root, Range, DOM collections and computed-style access. HTML template
content and slot assignment are used only in the XHTML namespace.

`test_r9_no_direct_dom_members` tokenises the narrow observation JavaScript dialect
(including strings/comments), checks postfix chains and rejects direct, indexed or
optional member access on platform handles or DOM-valued accessor results. DOM
handles use `n_*`; aliases cannot become plain-data locals. The permitted `v_*`
collections/primitives and `r_node` records are built by the walker itself, and
record fields have an explicit allowlist. Unknown receivers are rejected. Negative
alias/chain tests enforce that boundary. `test_r9_evaluated_function_inventory`
uses Python ASTs to enumerate every evaluation payload: only the checked walker,
hit test and blank-page popup probe may access DOM objects. The remaining payloads
only store/resolve isolated-world handles, close paused workers or evaluate zero.
The clobbering corpus derives its names from this table and the global-method
table, with six element patterns in barrier and painted-comment contexts.

The observation is a flat structured node list: kind, namespace, tag, ordinary
attributes, text, light-tree children, composed-tree children and a rendered boolean.
Expand controls also carry a CDP backend node identity. There is no reserved HTML
attribute channel or trusted-HTML flag; every page-authored `data-metamuse-*`
attribute is ordinary markup, regardless of case, namespace or creation method.
Chromium observations are never HTML-reparsed. Progress compares structured comment
identities/text and immediately classifies new barrier evidence during polling. CDP clicks the exact captured rendered control, including when
another hidden control has the same identifier. Serialized structured data supplies
the observation digest and byte accounting; it is not parsed as HTML.

| Content | Rendered evidence |
| --- | --- |
| Ordinary laid-out content | yes |
| Open, declarative and closed shadow content | yes |
| Light children assigned to a slot | yes |
| Opacity zero, off-screen, zero-size or clipped | yes; painted-but-not-seen limit |
| Off-screen `content-visibility:auto` descendants | yes, even without layout nodes |
| `aria-hidden=true` | yes; accessibility does not determine painting |
| `hidden` with a CSS display override | yes, by the cascade |
| `display:none`, or `hidden` without an override | no |
| `hidden=until-found` | no |
| `content-visibility:hidden` descendants | no where computed display supports containment; otherwise yes |
| Closed details content other than its first summary | no |
| Scripting-on `noscript`, and template content | no |
| Unslotted light children of a shadow host | no |
| `visibility:hidden` or `collapse` | no; descendants may restore `visible` |
| Text, including through `display:contents` | its text box and inherited visible state; skipped auto content remains eligible |


An element's composed parent is its assigned slot, otherwise its parent element,
otherwise its shadow root's host. A `display:none` composed ancestor blocks every
composed descendant, including slotted `display:contents` children. Visibility can
be restored by descendants, but an unrendered container never establishes a post,
comments-section or comment identity. Extractor stack frames restore exactly the
state opened at entry. `display:contents` suppresses replaced elements and controls:
img, input, textarea, select, video, audio, canvas, iframe, embed, object, br, wbr,
meter, progress, frame and frameset. Look-through never climbs out of these nodes.

The locked Chromium build computes `display:block` for `frame` and `frameset`
even with authored `display:contents`; the Appendix B differential checks record
this exception. They are refused by fixtures. The other listed replaced/form
elements are unrendered with computed `display:contents`.

One `capture.controls` traversal assigns rendered controls in composed order to
both the extractor queue and the CDP identity map. IDs use `data-expand-id` or
`expand_N`; the first rendered duplicate wins. The hit test follows assigned slots,
parents and shadow hosts through cached accessors, accepting labels inside a
control's shadow root or assigned through a slot. An overlay, removed control or
unusable quad fails expansion without clicking a substitute node.

Default `checkVisibility()` preserves auto-skipped content. Text-range boxes exclude
non-painting direct text in closed details; looking through `display:contents`
preserves text whose immediate parent has no box. Opacity, clipping, size and viewport
position do not filter evidence. Possible painted `aria-hidden` duplicates must be
measured in AT-039, not excluded by assumption. JSON text keeps its existing rules.

`FixtureTree` is a static test double accepting a balanced subset, not a general
HTML5 parser. Its HTML tag vocabulary is explicit (`HTML_TAGS`); foreign SVG and
MathML vocabularies exclude HTML breakout and integration-point rules. Fragments
use a body-fragment context. Explicit documents require all three html/head/body wrappers in order; a template
fragment cannot admit them.
It models scripting-on HTML `noscript`, the serializer's exact 18 HTML-only void
tags, inline declaration order and `!important`, hidden/display overrides, content
visibility, details and inherited visibility. Unsupported parser modes, unbalanced
or implied end tags, nested form/a/button corrections, foster parenting and
implicit table sections refuse with the fixed capture failure. Rawtext/RCDATA/
script-data/PLAINTEXT elements other than inert script/style and HTML noscript are
refused: xmp, iframe, noembed, noframes, plaintext, textarea and title. Foreign HTML
names (including noscript/template/slot), dialog, popover and declarative shadow DOM
are refused. Executable scripts, event handlers, stylesheets, external stylesheet
links, refresh metadata and unsupported inline rendering declarations also refuse;
fixtures do not simulate execution, resources or the external CSS cascade. Inert script payloads admit application/json, application/ld+json and text/plain; other non-empty script bodies refuse. Legacy executable-script and embed examples remain real Chromium regressions, with explicit fixture refusals.


A vocabulary audit also checks native defaults and valid parent contexts, including
all admitted tags and inline display/visibility values. `area` and `basefont` are
non-rendered by default; standalone `source` and `track` remain eligible under the
capture rule. Their display overrides refuse. SVG fixtures admit only svg/g
containers, text/tspan text contexts and empty geometric leaves; defs, symbol and
use refuse. A tspan outside text/tspan and any element child of a geometric leaf
refuse. Foreign inline display values other than none, and HTML table-column or
table-column-group display values, refuse because their used display or descendant
box suppression requires layout. These frozen audit inputs remain in the parity
corpus even when the admitted vocabulary is narrowed.

The differential corpus includes committed HTML literals/files, runtime fixture
inputs recorded by the suite, reviewer-provided markup and a seeded depth-five
generator. Its marker/role vocabulary is checked against AST literals from the
extractor, control traversal and DOM parse; every marker also has a deterministic
sample independent of random selection. Accepted inputs must agree with sandboxed Chromium on node kind, tag,
namespace, attributes, text, rendered flag, light-parent relation and composed
order. Normalisation removes document/fragment/doctype/comment records and empty
html/head/body scaffolding; it preserves evidence nodes and their parent/order.
Unsupported inputs are counted as refusals. The capture-only audit opens one sandboxed, policy-proxied loopback session
without a job store, permit setup or TLS key generation. It waits for static load
and flushes DOM events before each capture; job-level parity is checked separately.
The default Chromium audit has an
independent 90-second deadline; the job/browser budgets remain 300 seconds.
Potentially rendered whitespace-only text nodes are refused: their boxes depend
on layout and whitespace collapse. Mixed text keeps its source whitespace. MathML
text-integration-point element children, paragraph-implying block starts and
foreign-content foster parenting also refuse. HTML5 `</br>` creates a `br` node.
Literal NULs, foreign CDATA/conditional declarations, processing instructions and
ambiguous legacy character references in start tags are refused. Exact named
references ending in semicolons retain the HTML5 decoding; text is never decoded
a second time. These lexical refusals cover differences in the standard-library
parser rather than claiming it is a full HTML5 tokenizer.
Metadata display overrides are refused rather than approximated. Display inheritance (`inherit`) also refuses; its resolved value is not the inline token. The tested `revert` and `revert-layer` behavior remains supported. Implicit HTML,
head and body nodes are materialised before bounds checking, so the document
scaffold is charged exactly like Chromium; misplaced explicit wrappers refuse.

The fixture-only raw UTF-8 markup precheck is 8 MiB; every parity input is below it.

Capture permits at most 65,536 nodes and 8 MiB of UTF-8 text plus attribute names and
values, independently of the operator-selected `limits.max_bytes`. The existing
HTML/observation and proxy-byte budget remains binding; these additional structural
bounds also cover a tiny script that creates a large DOM. Flat records add overhead
beyond equivalent HTML, and their serialized bytes count against the same job budget. Bounds, unsupported non-HTML main MIME
types (XHTML/XML/SVG), and capture failures refuse with fixed Facebook-specific
`E_SELECTOR_DRIFT`, without page text or `E_STORAGE`. Inaccessible closed roots add
`closed_shadow_capture_unavailable` and force partial coverage. Subtree insertions or shadow-root attachments observed
across CDP discovery/capture commands add `capture_changed_during_observation`, so
a closed root created during handle discovery cannot be silently omitted. Incomplete captures
never claim `scope_satisfied`.

Stop markers (`data-fb-state`, `data-page-state`, `data-checkpoint`, login/checkpoint
form IDs, head/meta states) are inspected across the whole light DOM, unslotted
children, templates and shadow roots, regardless of rendering. Completeness evidence
such as `data-comments-exhausted` counts only when rendered. Text retains the existing
single decode, image-alt, newline and edge-whitespace contract.


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
Shared and service workers use `Target.closeTarget`. Success, refusal and lost
close replies all enter the same pending-target set: destruction is asynchronous.
Verification runs after callbacks return, before import, with a bounded five-second
wait, including dedicated workers already sent the fixed `self.close()`. The verifier
iterates a copy and discards targets only after proving disappearance. Attachments
delivered while checking are included in that same five-second deadline; new targets
never extend it. Unexpected verification exceptions become the fixed guard failure. A browser
disconnect is never disappearance evidence; a target still listed past the bound
fails closed. No worker resume or detach-to-run command is sent. Popups,
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
Captured observations remain candidates until their post-capture usage checkpoint,
barrier classification and byte check complete. Records, final counts, exhaustion,
controls and snapshot provenance advance together only on admission. A cutoff
rejects the candidate conservatively and retains the last admitted page and all
previously accumulated records; rejected metadata cannot invent coverage conflicts.
Upstream candidates are registered before connect can block; teardown closes
them before waiting for the dial barrier, so pending connects are included. A
50 ms browser-loop watcher reacts to byte cutoff and latched socket/guard
failures, closing the affected page to interrupt in-flight
navigation, click and expansion operations, instead of waiting for the full
`read_timeout_seconds`. Owned shutdown detaches the response listener; in-flight
peer lookups ignore only target-closed replies with owned closure or an observed
closed page on a connected browser. Active peer refusals retain their typed stop;
an unexplained lost target fails the guard, and other exceptions are not suppressed.
Only navigation/socket/wait interruptions caused by the
cutoff become `E_LIMIT_REACHED` with partial coverage. Existing fatal errors are
never rewritten merely because the byte latch is set. Error precedence is guard
failure or unverified target shutdown, denied destination (`E_SCOPE_DENIED`),
login/checkpoint/challenge/rate-limit barrier, off-post navigation (including share-link
`E_UNSUPPORTED` and `E_DATA_STALE`), then the byte budget. Every other CoreError also
outranks the cutoff. Only the named aborted-navigation, closed-socket and interrupted-wait
errors caused by the cutoff may be dropped. Error recording retains the strongest
stop, including at close after the final observation with no expansion. When HTML has been observed, barrier classification runs before the byte
budget decision. Fatal stops preserve their normal job state, error, records and
next action. Every operation checks guard state first, and close always verifies
pending target destruction before tearing down CDP, including after a cutoff.
Already pinned authorities are revalidated synchronously through the complete URL
policy using their pinned addresses, without resolving or dialing, even after a
cutoff. This permits completed observations to reach barrier classification before
the budget decision while preserving destination checks. For unpinned URLs, all
DNS-free syntax, scheme, credential, hostname/IP-literal and port checks run before
the latch; port validation therefore precedes DNS. Allowlisted navigation paths are
then classified, so an allowlisted login URL retains its login barrier. DNS-free
proxy refusals enter the same audit counters exactly once as later policy refusals.
Only the remaining DNS-requiring allow path is interruptible by the latch. Its wait remains bounded. The watcher uses the pinned Playwright
implementation loop, as does the existing async WebSocket close adapter. Tests
bound hangs at an independent 90-second subprocess deadline, with 300-second read
and job budgets.


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


Round 10 fixture insertion-mode contract: fixtures are either body-context fragments
with no html/head/body tags, or explicit html/head/body documents in that order.
Nothing but whitespace may follow head before body, or follow body/html; unsupported
structural repairs refuse. Script content containing <!-- refuses, including inert
JSON scripts, because HTML5 script-data escape states differ from rawtext. Style
is rawtext and has no script escape states (non-empty stylesheets still refuse).
The seeded parity corpus covers every insertion boundary and implied-end-tag or
parser-inserted-element class. The comparator retains head/body/none container
identity and has negative tests for tag, namespace, attributes, text, rendering,
order and container. Fixture-only raw UTF-8 prechecks remain 8 MiB; all parity
inputs are below that bound.

All Python consumers of captured tag names use capture.html_tag: only XHTML-
namespace nodes have HTML form/script/void/block semantics. Foreign nodes remain
generic containers. test_r10_namespace_tag_table derives its SVG and MathML rows
from the extractor's HTML_TAGS table; broad attribute stop markers still apply
regardless of namespace or rendering.

An unrendered post/comment/reply/comments-section identity isolates its subtree:
painted descendants cannot attach to an enclosing or sibling identity. A rendered
nested identity owns its own content. Painted text without such an identity is
dropped and adds unattributed_rendered_text, making coverage partial. The same
isolation applies to image alt text and derived block boundaries. Each extractor
stack frame saves and restores this isolation state as well as body/comment state.

Before WALK and HIT_TEST, one shared bounded helper indexes assignedNodes from
every discovered slot, including closed roots, without flattening. Composed parent
uses that reverse map when Element/Text assignedSlot returns null for a closed
root. Both consumers use the same captured accessors and the same 65,536-node
bound. Slotted text and labels therefore retain their shadow ancestors for both
rendering and click validation.

Content-visibility skipping is decided from Chromium's computed display, proved
by test_r10_content_visibility_display_oracle against an independent child-span
checkVisibility() and getClientRects().length oracle, plus direct text. The pinned
build skips hidden contents for block, inline-block, flow-root, flex, inline-flex,
grid, inline-grid, list-item and table-cell. It does not skip for inline, contents,
table, inline-table, row/group/caption table boxes, ruby or ruby-text. Column boxes
already suppress their children. Unsupported ruby-* declarations compute to block.
This measured table-cell result differs from a blanket exclusion of internal table
boxes. Fixture content-visibility is accepted only for independently proved display
values; unmodelled/unsupported display values refuse with the fixed capture error.
Auto-skipped content remains eligible.

The accepted fixture defaults for `html`, `body` and `legend` are block display.
Focused parity checks also retain explicit-inline mirrors: Chromium blockifies
the root and the fieldset's first legend, while inline body contents remain
eligible under `content-visibility:hidden`.

A trusted click moves the pointer to the quad first, then resolves and checks
the hit node through its composed ancestors, and immediately sends mousePressed
and mouseReleased. Hover-revealed overlays therefore fail the hit check without
receiving a click; the observed data remains with expansion_failed/partial.

Read-only clobbering, foreign-template/slot and barrier-location rows share one
sandboxed capture session per group. Each row replaces the document and flushes
pending DOM events before capture; no row relies on a previous row's DOM. Click
and expansion regressions retain separate browser sessions and stores, preventing
state, navigation, listener or budget leakage between state-changing cases.
Normalisation preserves each evidence node's head/body/none container as well as
the previously compared dimensions.

The explicit-document fixture form also refuses leading non-whitespace text,
duplicate doctypes and doctype-bearing fragments. Those trigger HTML5 document
insertion rather than the documented body-fragment context. The expanded
insertion-mode corpus and container comparison expose these placements.
