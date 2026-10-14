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

Production launches pass `chromium_sandbox=True`. Failure stops with
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

Every Facebook result's `data.browser_sandbox` is `"enabled"` or
`"disabled_by_operator"`; checkpoints and requested scope also expose the selected
mode, including jobs that stop before producing a result. Inspector/doctor envelopes
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
The first observed supported post becomes the authorized identity; subsequent
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
`context.route_web_socket` closes page WebSockets before navigation. The pinned
Playwright 1.63 callback runs on its asyncio loop, so the handler awaits its underlying
close implementation instead of making a nested synchronous API call. The pinned
browser omits worker WebSockets from that hook: dedicated and shared workers are
paused before their first script using CDP target attachment and terminated. Shared
workers require a browser-level attachment because they lack a page opener relation.
Worker-dependent page content is consequently unverified and may be unavailable.

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
`weak_identity_expansion`. On resume weak records are skipped entirely with
`weak_identity_resume` and partial coverage. Text or position is never a deduplication
identity. Previously committed weak items remain; newly visible weak data may be omitted.
No rendered HTML or record bodies enter checkpoints.

## Completeness, parent links and text

After expansion the production driver waits within the read deadline for a new relevant
comment/reply node or changed content, or a classified HTTP failure response. An already
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
no --no-sandbox flag. The test skips only when that launch is unavailable.


A present but unparseable Facebook comment count adds comment_count_unparsed to post and job gaps; affirmative exhaustion never converts that uncertainty into scope_satisfied.


Facebook counts and remaining hints accept only nonnegative integers or 1–18 ASCII decimal digits. Other present forms add fixed *_count_unparsed gaps; Unicode digits and oversized decimals never reach an unchecked int conversion.


Facebook parent identifiers are stripped and validated against [A-Za-z0-9._:-]{1,128} in both DOM and JSON. Invalid/empty reply parents are unknown and add parent_comment to the item and job, independently of unresolved_parent.
