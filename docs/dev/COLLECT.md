# Local collection (B05/B06) and remote media (B12)

`submit_collect` accepts `local_file` and `text` inputs, persists one `import.local`
job, and returns `accepted`. The executor runs the job. A request whose inputs are
all `url` and whose resources are a subset of `media_metadata`, `subtitles`, and
`media_file` is `collect.remote_media` (B12). Any other URL shape, including `post`
or `comments`, a mix of URL and local inputs, stays `E_UNSUPPORTED` before a job
exists. `next_action` names B12/B13 and LIVE. That check happens before the project
lookup.

`import.local` has side effect `none`. `collect.remote_media` has side effect
`safe_read`. Registration is idempotent. `metamuse_collect` stays
`fixture_passed`, `ready`, and `granted_scope`. `ffprobe` does not change that
row. `metamuse_collect.remote_media` reports the yt-dlp extra:
`missing_dependency` without the pin, and `ready` when `yt-dlp==2026.8.19`
imports. Its permission is `not_configured` while `app.network_mode` is
`offline` or `remote_media.approvals` is empty, and `granted_scope` when the
workspace is live and at least one approval is configured. That row is not a
permit. Every collection still presents `access_grant_id` for `collect.remote`
at submit. `media.py` does not register its own operation.

## Accepted bytes

At submit, each local file is read under `app.import_roots`. The payload stores the
absolute resolved path, the SHA-256 of the raw bytes, and the byte count. A client
value for either field is replaced. Text inputs pin the SHA-256 and size of the exact
UTF-8 of the payload, including a leading U+FEFF if the client sent one.

At run time the file is copied into `staging/<job>/input-N` and hashed again. A
different hash or size, a missing file, a symlink, or growth past the accepted size
records `E_DATA_STALE` for that input inside the job result. The job ends `partial`
with stop reason `data_stale`. It is not moved to `awaiting_user`. Other inputs in
the same job are still committed. A path outside the intersection of the accepted
roots and the live `metamuse.toml` roots records `E_SCOPE_DENIED` for that input.

Empty `import_roots` refuses every local file with `E_SCOPE_REQUIRED` and
`next_action` pointing at `[app] import_roots`. A request limit above the workspace
limit is `E_SCOPE_DENIED`. The format must intersect the requested resources or
submit fails with `E_INPUT_INVALID`.

| Format | Resources it can satisfy |
| --- | --- |
| `text`, and a `text` input | `post`, `comments` |
| `json`, `jsonl`, `csv` | `post`, `comments`, `media_metadata`, `media_file` |
| `vtt`, `srt` | `subtitles` |
| `media` | `media_metadata`, `media_file` |

`language` and `attach_asset_id` are only valid with a subtitle input. `attach_item_id`
is valid with a subtitle or media input. `access_grant_id` is stored on the request
and is not a collection method.

Checkpoints hold 50 outcomes. A restart continues after the last committed record, so
committed rows are not inserted again and the remainder is not dropped. One
`collect.batch` event per checkpoint records the input index and the next record
index. Event detail does not contain item text.

## Record fields

JSON, JSONL, and CSV use the same object. Unknown keys are rejected. JSON unknown
keys are row errors. CSV unknown columns and duplicate headers fail the file.

| Field | Rule |
| --- | --- |
| `platform` | Required string, at most 128 characters. |
| `kind` | Required `post`, `comment`, or `reply`. |
| `native_id` | Optional opaque string, at most 512 characters. Not trimmed. Empty becomes absent. |
| `native_scope` | Optional opaque string. Absent is stored as `""` so the source identity index applies. |
| `parent_native_id` | Optional. A present null or empty value clears a parent. An absent field leaves the current parent. |
| `text` | Optional. Null is allowed for a media-only item. At most 1,000,000 characters, and at most the workspace `text_max_bytes` measured with `utf8_text`. |
| `source_url` | Optional, at most 4096 characters. The first value is `original_url` and is not overwritten. |
| `upstream_collection_method` | Optional. Absent becomes `unknown`. |
| `created_at_raw` | Optional display string, at most 256 characters. Not required to be a timestamp. |
| `created_at` | Optional timezone-aware ISO-8601. `Z` is accepted. A naive value rejects the row. |
| `author_display` | Optional, at most 512 characters. Stored in revision evidence. |
| `media` | Optional list of `{"ref", "kind"}`, at most 100 entries. `kind` is `image`, `video`, `audio`, or `file`. |

`resources` selects rows. `post` keeps posts. `comments` keeps comments and replies.
Other kinds in the file are `skipped`, not errors. `max_items` counts committed and
unchanged rows. Skips and rejections do not consume it. When the cap is already
full, a later row is validated without mutating the book. If that row would be
committed or unchanged, the job stops with `limit_reached` and does not import it.
If every remaining row would be rejected or skipped, those rows are reported and
the stop reason is not `limit_reached`.

### JSON and JSONL

A JSON file is one object or an array of objects. The array index is the row number.
One object is row 1. Invalid UTF-8, invalid JSON, duplicate keys, `NaN`, and
`Infinity` fail the whole document (`parser_failed`). They are not an empty success.

JSONL splits on `\n`, strips one trailing `\r` before parsing, and skips blank lines.
The row number is the file line. Invalid UTF-8, invalid JSON, nonfinite numbers,
lone surrogates, and nesting deeper than 32 reject that line. Neighboring lines
are still committed. One leading UTF-8 BOM is removed. A U+FEFF later in the text
stays. An unknown field is `record` / `Record contains an unknown field`. The
field name from the file is not copied into the error. Parsing stops after
`max_items` plus a small lookahead. Further records are not read. At most 50 row
errors are kept, and the rest is a count.

### CSV

The header must include `platform` and `kind`. Columns are the record fields plus
`media_ref` and `media_kind`. A blank cell is null. The row number is the 1-based
data record, so a quoted embedded newline is still one row. `media` is a JSON array.
When that array is non-empty, `media_ref` and `media_kind` are ignored. A present
`parent_native_id` column clears the parent when the cell is blank; omit the column
to leave an existing parent. Invalid UTF-8, a missing required header, a repeated
header, or an unknown column fails the file. The unknown-column message does not
include the column name. A field may be up to one million characters. An unclosed
quote is a row error and does not merge the next row.

### Plain text

`format: text` and a `text` input become one weak post: platform `generic`, kind
`post`, upstream `unknown`, and the decoded text unchanged. One leading BOM is
removed after the hash check. An empty file is still one item whose text is `""`.
Invalid UTF-8 fails the input.

## Identity

`(platform, native_scope, native_id)` is the reliable identity. The scope is stored
on the source, including `""` when a reliable id omits `native_scope`. The same
native id in two scopes is two items. Without a native id the item is weak and
receives an internal `itm_` id. A weak observation stores a non-empty scope and
stores NULL when the scope is absent. That id is not the row number and not a
text hash. Two comments with the same text and different ids stay two items. A
weak item is always new. Not seeing an item again does not mark it deleted.

The same reliable id twice in one batch is applied in order. Identical text adds no
revision and the row is `unchanged`. Different text appends a revision and keeps the
previous one. A disagreeing kind rejects the later row with `E_REFERENCE_INVALID`
on `kind` and leaves the first row in place. A present parent field updates the
parent even when the text is unchanged, without a new revision. An absent parent
field does not clear one.

`item_revisions.text` is nullable. JSON null is SQL NULL, `missing_fields` contains
`text`, and `text_hash` is the digest of `{"text": null}`. An empty string stays
`""`, uses `{"text": ""}`, and does not list `text` as missing. The two hashes differ.

## Parents

`parent_native_id` resolves inside the same project, platform, and native scope, in
any row order. A missing parent is stored as `unresolved_parent` and a later import
of that id links it. A self-parent or a cycle rejects the incoming row with
`E_REFERENCE_INVALID` on `parent_native_id` and does not insert it or append a
revision. If linking waiting children would cycle, that incoming row is rejected
and the earlier row stays unresolved. A parent in another project or another
native scope is never linked.

## Provenance

`collection_method` is what this run did: `file_import` or `text_input`.
`upstream_collection_method` comes from the record, or `unknown` when the record
omits it. Source `collection_method` and `upstream_collection_method` are
immutable after insert. A later declaration is stored on the new item revision's
evidence and does not relabel the source. An identical-text reimport does not
write that observation. Nothing automated is stored as `manual`. A URL-only
change waits for the next text revision. `original_url` remains the first
`source_url`.

## Stop reasons

Any rejected row or input error makes coverage `partial`. Otherwise:

| Condition | Stop reason | Coverage | Job state |
| --- | --- | --- | --- |
| `max_items` reached with more acceptable rows left | `limit_reached` | `bounded`, or `partial` when a row was also rejected | `succeeded` or `partial` |
| An input is `E_DATA_STALE` | `data_stale` | `partial` | `partial` |
| The file itself failed to parse | `parser_failed` | `partial` | `partial` |
| One or more rows or inputs were rejected | `row_errors` | `partial` | `partial` |
| Nothing was accepted and nothing was skipped | `empty_input` | `bounded` | `succeeded` |
| The accepted input was fully observed | `input_exhausted` | `scope_satisfied` | `succeeded` |

`data_stale` outranks `parser_failed`, which outranks `row_errors`. `limit_reached`
outranks all three. A header-only CSV or WebVTT file and an empty JSON array are
`empty_input`, not a parser failure. A file that cannot be parsed is `parser_failed`
even when it imported zero rows.

## Media

Media bytes are streamed into `staging/<job>`, and the actual count is checked
against `media_max_bytes` and the job `max_bytes`. The MIME type comes from magic
bytes. An extension is ignored, so a PNG named `.mp4` stays `image/png`. HTML
disguised as media is `E_UNSUPPORTED`. An unrecognized type, an empty file, and a
truncated MP4 or QuickTime box stream are per-item `E_INPUT_INVALID`. None of those
become `ready`.

When `ffprobe` is installed it runs with a fixed argument list, `shell=False`, the
job read timeout, and one `file:` absolute-path argument. The child is the resolved
absolute binary, with `stdin` closed and a minimal environment (`PATH` only). It does
not inherit `FFREPORT`, `AV_LOG_*`, or `FFMPEG_*`. Stdout and stderr are each capped
at 1 MiB. A larger stream kills the process and the probe fails with a fixed error.
A file is `ready` only when that probe reports an audio or video stream with a real
codec name. A magic signature or an MP4 box walk is a pre-filter, not proof. Without
`ffprobe`, a structurally plausible file is stored `unavailable` with probe
`not_installed`. A non-zero exit, a timeout, unreadable output, output over the cap,
an exit 0 with no usable stream, or an exit 0 that leaves no file is a per-item error
and not `ready`. An `unavailable` asset may later become `ready`. A `ready` asset is
not downgraded.

The hash path is `objects/sha256/<prefix>/<hash>`. Staged bytes are hashed again
before they replace the object. A same-size change after measurement is
`E_INPUT_INVALID`. The object is placed before the database transaction. The asset
id is chosen before the checkpoint is written. The asset row and the project link
commit together. Cumulative `bytes_used` is stored on the checkpoint and restored
on resume. Media bytes are counted once, including a read that hits the job limit.
The same bytes share one asset when the sniffed MIME matches the stored MIME.
A different MIME is `E_INPUT_INVALID` (`Object MIME disagrees with its existing
classification`) and does not replace the stored classification. Two direct
imports keep two sources and two `source_assets` rows. Two comment references
keep two `item_assets` rows. The first writer's duration stays. Object paths do
not contain titles.

`media_metadata` without `media_file` records the sniffed type on the new revision
and does not publish an object. References are opened only when `media_metadata`
or `media_file` was requested. Otherwise the ref and kind stay on the revision as
evidence and the file is not read. A requested file is read at run time. A relative
`ref` is resolved next to the import file, then opened under the import roots.
`..` is the parent of the directory just opened, and only when that directory was
not a symlink. It is not part of the submit-time hash. A reference that fails still
commits the item, marks the row's `media` field, adds `media` to `missing_fields`
when the revision is new, and ends the job `partial`. An identical-text reimport
can attach bytes later and does not clear `missing_fields`, because revisions are
append-only. Attaching the same bytes to the same item again keeps the existing
`item_assets` row and still records the new source.

Collect writes `staging/<job>/.collect-owner` when it creates that directory. Sweep
deletes a directory only when that marker names the directory and `import.local`,
the job row exists with that operation, and the job is the one currently finishing
or is already terminal. A missing job, a `job_` prefix, or another operation does
not authorize deletion. Anything else is left in place and mentioned in the debug
log. Symlinks and core `object-*` files are left alone.

## Subtitles

WebVTT and SRT accept a leading BOM and CRLF. Lines split on CR, LF, and CRLF only.
U+2028 and U+0085 stay inside the cue text. Cue settings are ignored. Styling tags
and multi-line cue text stay, joined with the delimiter that was between those
lines. Exact `NOTE`, `NOTE ` / `NOTE` plus a tab, `STYLE`, and `REGION` blocks are
skipped. An identifier such as `STYLE-cue` is a cue. A cue needs `start >= 0` and
`end >= start`. A malformed timestamp, an inverted range, or a time that does not
fit in a signed 64-bit millisecond value is a row error. Touching endpoints are
not overlaps. Other overlaps are kept, stored on `overlap`, and noted on the row.
They do not by themselves make the job partial. An HTML page named `.vtt` or
`.srt` is `E_UNSUPPORTED`. A header-only WebVTT file has zero cues and is
`empty_input`.

Each kept cue is a transcript segment with method `provided`, review `unreviewed`,
`cue_index` in kept order, and language from the request, else the WebVTT
`Language:` header, else `und`. A language longer than 35 characters becomes
`und`. Cue text over 1,000,000 characters is a row error (`E_LIMIT_REACHED`), the
same character limit as record text. The workspace UTF-8 byte limit still applies.
Neighboring cues in the same file still commit, and the track stays incomplete.
Segments are project-scoped. The same file in another project gets its own
segments. A track is the file hash plus language plus item plus asset. Reimporting
a completed track reports `unchanged` and does not duplicate cues. A partial or
interrupted import stays incomplete, and the next import of those bytes inserts
only the missing cue indexes. A different language or a different item or asset is
a different track and gets its own source. There is no automatic link to an item
imported in the same job; pass `--item` or `--asset` for a record that already
exists. The asset must already be visible in the project through `item_assets` or
`source_assets`. Transcript rows are append-only. `INSERT OR REPLACE`, `REPLACE`,
and `INSERT ... ON CONFLICT DO UPDATE` cannot rewrite an existing `(id, revision)`.
A later observation is a new revision and marks packets and drafts that cite the
segment stale. Their selection and rendered text stay. `source_assets` rows are
immutable: those same statements cannot replace an existing
`(source_id, asset_id, relation)`, and no column is updated in place. A different
relation is a new row. Neither table has a secondary UNIQUE index.

Unchanged subtitle cues are listed even when they exceed `max_items`, and they do
not consume that cap. Item imports do count unchanged rows toward that cap.

## Local paths

`open_input_file` rejects an empty path, NUL, CR, LF, a component that starts with
`-`, a symlink anywhere in the walk (`O_NOFOLLOW`, then an inode check against the
real path), a non-regular file, and a path outside the configured roots. The root
directory itself is not a file. `..` is applied one component at a time and only
after the directory just opened was not a symlink. A path such as `link/../file`
is rejected instead of being rewritten to a different file. Byte limits count
reads and do not trust `st_size`. An import root that is itself a symlink is
rejected. Configure the real directory. A file reached through a symlink is
rejected even when the link is inside a root.

## Remote media (B12)

`collect.remote_media` runs only for an all-URL request whose resources are
`media_metadata`, `subtitles`, and `media_file`. It refuses before creating a job
when yt-dlp is missing (`E_DEPENDENCY_MISSING`), the workspace is offline
(`E_SCOPE_DENIED`), `remote_media.approvals` is empty (`E_SCOPE_REQUIRED`), the
extractor or exact host is not approved (`E_SCOPE_DENIED`), or `access_grant_id`
is missing or does not cover `collect.remote` (`E_SCOPE_REQUIRED` or
`E_SCOPE_DENIED`). Generic and unknown extractor keys are not approved.

### Operator approval: preview, approve, repeat

Set `[remote_media] provider` and `account` to explicit non-secret operator labels.
Their defaults are `remote-media` and `anonymous` for public unauthenticated reads.
Cookie-authenticated reads use the same provider/account labels as the human's
`auth import`; `auth_ref` remains operator configuration on each extractor approval.
An empty approvals list disables this backend. No request or grant chooses the account.

1. Submit the intended collection without `--access-grant`, through `metamuse add`
   or `metamuse_collect`. `E_SCOPE_REQUIRED` includes `approval`, with null `data`.
   Show its summary and `next_action` to the human. Save the request at
   `approval.request_file`. `approval.request` contains the canonical destination
   and can be approved directly. The original equivalent URL can also be approved
   and repeated to retain its original spelling in provenance. The preview creates no file, permit, receipt,
   job or intent and makes no network call or account discovery.
2. The human reviews the destinations, configured provider/account, cookie binding
   label (if any), extractor/host allowlist, limits and stored resources. Run the
   exact shell-quoted `approval.next_action`, which uses the shipped command:
   `metamuse --workspace WORKSPACE --json permit approve --operation collect.remote
   --request-file REQUEST_FILE --exact-digest ACTION_DIGEST --request-key APPROVAL_KEY`.
   Only the human CLI can approve. It recomputes the preview, displays the effective
   expiry and uses on stderr, and refuses an edited request with the old exact digest.
3. Repeat the unchanged request with the returned permit ID (`--access-grant ID`
   on CLI, `access_grant_id` on MCP). This returns `accepted`; use `--run`,
   `worker run`, or MCP's attached worker to execute the job.

Exactly one `collect.remote` preview callback dispatches `CollectRequest` through
the fixed route table in `collect.py`. B13 will extend the route selector and table.
Preview, submission and the step boundary call the same `prepare_remote_media`.
The B08 action digest binds the canonical URL bytes, project, configured provider/account,
resources, extractor keys, requested limits, reviewed workspace caps and the entire configured approval table.
The table is sorted canonically by extractor key, normalized sorted hosts and opaque
`auth_ref` (or null). Entries sharing an extractor use canonical order, so reordering
them cannot switch the chosen cookie binding. Configured extractor priority is retained;
the chosen first-hop keys are also bound. The preview names first-hop entries
and every entry that may serve later hops, including hosts and cookie label/account.
The job selects hops only from its bound entries. Even an unrelated approval edit
invalidates outstanding grants before consumption or egress; this fail-closed trade-off
is intentional. Grant IDs and request keys are excluded from the action digest.
Current workspace limits and extractor/host approvals are checked again at execution.
Preparation computes one ASCII-only canonical page URL: lower-case scheme,
lower-case IDNA/punycode host, non-default port only, and UTF-8 percent-encoding
of every non-ASCII path/query byte. The digest, destination, public request echo,
`permit approve` stderr and extractor's first hop all use that exact string.
The stored job request and `original_url` provenance retain the operator's original
URL. Canonically equivalent spellings therefore approve the same extraction.
Any fragment (even an empty `#`), any query key recognized by the shared `url_token`
rule (including encoded/case/dash variants), trailing-dot host, or remaining free-text
redaction of structural URL bytes returns fixed `E_INPUT_INVALID` /
`Remote URL cannot be reviewed safely`, without echoing the URL or producing a preview.
Credential-bearing page URLs are not B12 inputs. Signed media and delegation URLs
can come from the extractor; their existing log/event/error redaction still applies.
Other text fields are redacted separately before JSON encoding.
Cookie bytes are never read by the preview, and only the reference label and account
appear in its summary. Share previews bind the canonical share URL and state that
checked resolution and at most two approved extractor hops happen during execution.

An unknown permit preserves `E_SCOPE_REQUIRED` / `Permit does not exist` and includes
the preview. Expired, revoked, exhausted or mismatched grants preserve `E_SCOPE_DENIED`
and its original message, without a preview; repeat without a grant for a fresh review.
Fresh previews choose the next unused approval key without writing a receipt, so the
displayed command cannot replay an expired, revoked or exhausted permit. Offline mode,
disabled backend, missing extra and invalid/unsupported URLs keep their actual errors
without a preview. A failed preview or approval creates no grant.
Receipt lookup precedes permit verification. An exact accepted request, including its
grant ID, replays the original job even after consumption or a workspace-cap edit.
Any payload difference, including another grant, is E_IDEMPOTENCY_CONFLICT. Workspace
caps stay outside job identity and target, but all four displayed caps are in the
action digest payload. Any cap edit between review and grant-bearing submission
requires a fresh preview and approval, before permit consumption. Atomic acceptance
stores exactly those reviewed caps in `remote_media_caps` in the step checkpoint.
Use a new collection request key for a new run; an accepted key replays its original
job and cannot be reused with a different grant or payload. Pre-round-8 permits need
a fresh review because the action digest now binds canonical first hops and reviewed caps. Existing
cookie configurations must set the provider/account labels to match their imported binding.

### AT-040 operator plan (LIVE remains blocked)

In a separately authorized, permitted real-account environment, the operator first
reviews every first-hop/later-hop approval, its hosts and cookie label/account, the
canonical ASCII destination and digest-bound cap snapshot; refuses page fragments
or credential query keys; saves the reviewed request (or the original equivalent URL);
and approves the exact digest with
`metamuse permit approve --operation collect.remote --request-file REQUEST_FILE
--exact-digest ACTION_DIGEST --request-key APPROVAL_KEY`. Repeat with the returned
grant, then compare source metadata, subtitles and any requested media bytes against
the permitted source, recording limits, omissions, provenance and partial coverage.
Stop at a login wall or challenge. This plan has not been executed; the shipped
CLI/MCP tests use synthetic extractors and egress seams, with no network or accounts.

The job uses yt-dlp's Python API with plugins and config disabled. Every request,
including subtitles and media bytes, goes through one MetaMuse request handler.
That handler calls `OutboundURLPolicy.before_request`, dials only the checked
addresses with curl_cffi (`CURLOPT_RESOLVE`, no proxy, no impersonation), follows
redirects itself, and caps the body. The `media_max_bytes` cap stays on a media
download across every hop. Each hop is still checked with purpose `redirect`.
A media file is downloaded only when `media_file` was requested. Subtitles use
method `provided` or `auto`. No cues means `unavailable` and does not start ASR.
Metadata provenance uses `collection_method` `yt-dlp` and the upstream extractor
key, never `manual`. A remote video item is stored as kind `post` until core
adds a media kind.

The checkpoint stores the phase, indexes, ids, revision, text hash, and counts.
It does not store upstream media or subtitle URLs, and it does not store a
share redirect's raw `Location`. A share input may store the canonical resolved
URL, which has no tracking query. A resume of that input reuses the stored URL
instead of requesting the share link again. The resume still re-reads the
resolved page, counts that response in `bytes_used`, and compares native id,
extractor, platform, and scope with the checkpoint. On a mismatch the input is
`E_DATA_STALE`: committed metadata stays, no subtitle or media from the new
page is attached to the old item, coverage is `partial`, stop reason is
`data_stale`, and the permit use stays at the one already reserved. The job
error stays empty. Other inputs continue.

Each input is checked only against the hosts approved for its own extractor.
Approvals are not unioned across the job. A checked delegation hop uses only
the hopped extractor's approval.

A platform challenge stops the job inside the request handler, before yt-dlp
can answer it. MetaMuse does not solve, retry, or POST a challenge. The stop
does not depend on the HTTP status. Any of these is `E_CHECKPOINT_REQUIRED`,
message `Platform challenge`, next action `Stop. Do not solve a challenge or
retry it.`:

- a `cf-mitigated` header whose value contains `challenge`;
- a reason phrase that contains `challenge`;
- `__rd_verify` (case-insensitive, with or without a preceding slash)
  anywhere in the response body or in a `Refresh` or `Location`
  header.

Each request path is normalized before it is tested: percent-decode, collapse
repeated `/`, resolve `.` and `..`, lowercase. A segment that starts with
`__rd_verify`, a segment equal to `challenge.html`, or a query containing
`hash429` is the same error, and it is raised before that URL is dialed. The
same check runs on every redirect `Location`, including the share resolver,
before the target is dialed. The job is `awaiting_user`. Nothing new is stored
for that input. The received body still counts against `max_bytes`, and the
permit use stays at the one already reserved. A 403 without a challenge marker
stays `E_ACCESS_DENIED`.

Before a redirect is followed, the target is checked in this order: host
approval, challenge, checkpoint, then login. A checkpoint segment is
`E_CHECKPOINT_REQUIRED`. A login segment (`login`, `login.php`, or a segment
that starts with `login.`) is `E_AUTH_REQUIRED`, including a locale prefix such
as `/zh_TW/login/`. Media and subtitle fetches use that same check, so a login
redirect is not stored as a silent `partial` with `missing_fields`.

Pinned distribution **yt-dlp 2026.8.19**, runtime version **2026.08.19**, was
audited from the installed package without network access. The full search was
`grep -RniE --include='*.py' 'challenge|hash429|captcha|cf_clearance|__rd_verify|JSInterpreter' <site-packages>/yt_dlp`:
348 keyword hits in 34 files. The table classifies every matching line,
including fixture text and normal player deciphering. `cf_clearance` has no
hits. `JSInterpreter` has one extractor constructor call, `vk.py:61`.
The existing third-party notice records yt-dlp's Unlicense, version and source;
this audit copies no upstream implementation.

An extractor key in a solver row cannot be approved for execution. Submit,
resume and delegation hops refuse it with `E_UNSUPPORTED` and fixed message
`The extractor answers platform challenges`. Base keys are refused as well
as all concrete subclasses that inherit the solver. `TikTokVM`, `VKPlay`,
`VKPlayLive` and `VKPlayBase` do not inherit these solver methods. `Facebook`
and `FacebookReel` remain usable. The request guard additionally stops the
audited `/challenge.html`, `hash429` and `/__rd_verify` endpoints before dialing.

| Installed yt-dlp file and matching lines | Classification and decision |
| --- | --- |
| `extractor/amcnetworks.py:23` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/boosty.py:167, 168` | Fixture expected warnings from delegated YouTube player nsig deciphering; not an anti-bot answer. |
| `extractor/brightcove.py:65` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/cbsnews.py:283, 287` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/ciscowebex.py:64` | Captcha refusal message; no answer computed. |
| `extractor/common.py:2054, 2057, 2058` | Akamai player verification is unsupported; returns no formats rather than computing an answer. |
| `extractor/dplay.py:744` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/facebook.py:1110, 1115, 1117, 1118, 1119, 1123` | **Solver.** `FacebookAdsIE` answers the 403 client challenge with an empty POST to `/__rd_verify`, then retries. Refuse `FacebookAds`. `FacebookIE` and `FacebookReelIE` do not inherit this method. |
| `extractor/generic.py:829, 838` | Detects Cloudflare and raises; no answer. Generic is already refused. |
| `extractor/goplay.py:218, 220, 223, 224, 225, 226, 227, 231, 258, 259, 261, 264, 267, 268, 269, 270, 271, 293, 299` | Cognito `PASSWORD_VERIFIER` account SRP authentication; not an anti-bot, rate-limit or captcha answer. No solver refusal. |
| `extractor/kicker.py:26` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/likee.py:75` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/mtv.py:231, 235` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/niconico.py:98` | Reports failed account MFA authentication; does not solve an anti-bot challenge. |
| `extractor/nytimes.py:244` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/panopto.py:98` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/peekvids.py:19` | Bot/captcha refusal message; no answer computed. |
| `extractor/playsuisse.py:170, 179, 180` | OAuth PKCE `code_challenge`; not an anti-bot answer. No solver refusal. |
| `extractor/slideslive.py:44` | Synthetic upstream test title/description/URL contains the word; no challenge handling or answer. |
| `extractor/soundcloud.py:196, 197` | Inactive triple-quoted login payload; captcha fields are not executed. |
| `extractor/tiktok.py:223, 224, 228, 230, 233, 235, 237, 239, 241, 248, 251, 254, 256, 259, 305, 312, 313, 1290, 1309, 1503` | **Solver** at 223–313: `TikTokBaseIE` brute-forces SHA-256 proof of work, sets answer cookies and retries. Refuse `TikTok`, `TikTokUser`, `TikTokSound`, `TikTokEffect`, `TikTokTag`, `TikTokCollection`, `Douyin`, `TikTokLive`, `TikTokBase`, `TikTokBaseList`. At 1290/1309, “challenge” means hashtag content, not an anti-bot endpoint. At 1503, the Douyin signature-cookie solver is only a TODO; it still inherits the implemented base solver. |
| `extractor/udemy.py:135, 137, 138` | Captcha detection and operator-facing refusal; no answer computed. |
| `extractor/vk.py:12, 45, 46, 51, 54, 55, 59, 61, 65, 66, 68, 70, 71` | **Solver.** `VKBaseIE` runs `JSInterpreter`, computes MD5 of `hash429:salt`, submits `key`, and retries. Refuse `VK`, `VKUserVideos`, `VKWallPost`, `VKBase`. |
| `extractor/yandexmusic.py:22, 23, 26, 29, 32, 39` | Captcha detection and operator-facing refusal; no answer computed. |
| `extractor/youtube/_tab.py:2136, 2137` | Upstream test tags contain “challenge”; no challenge handling or answer. |
| `extractor/youtube/jsc/_builtin/bun.py:19, 20, 21, 65, 73, 154, 164` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/_builtin/deno.py:17, 18, 19, 53, 70, 118, 130` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/_builtin/ejs.py:13, 14, 15, 16, 17, 18, 19, 20, 34, 75, 77, 80, 148, 149, 166, 172, 179, 181, 182, 183, 185, 188, 207, 230, 239, 246, 252, 253, 273, 284, 296, 297, 307` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/_builtin/node.py:9, 10, 11, 54, 69` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/_builtin/quickjs.py:10, 11, 12, 30, 58, 66` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/_director.py:13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 37, 40, 43, 44, 47, 50, 53, 60, 61, 62, 71, 72, 74, 78, 81, 86, 91, 95, 96, 98, 109, 114, 123, 132, 138, 148, 151, 163, 187, 192, 225, 239, 240, 241, 246, 248, 249, 251, 256, 257, 258, 259, 260, 262, 265, 267, 268, 269, 270, 272, 274, 275, 276, 277, 281, 283, 284, 285, 286, 287` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `extractor/youtube/jsc/provider.py:20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 36, 42, 43, 44, 49, 51, 55, 57, 61, 66, 71, 72, 73, 78, 79, 80, 83, 84, 91, 92, 95, 98, 100, 102, 109, 110, 112, 113, 119, 120, 125, 137, 138, 141, 142, 145, 150, 151, 153, 160` | YouTube player signature / nsig deciphering infrastructure (`N` / `SIG` only); **not an anti-bot challenge answer**. No solver refusal. MetaMuse separately disables JS runtimes, remote components and subprocesses. |
| `jsinterp.py:162, 271` | Interpreter implementation/comment, not an extractor. Its only extractor constructor call is `vk.py:61` (refused above). |
| `extractor/youtube/_video.py:28, 3293, 3296, 3299, 3301, 3302, 3304, 3305, 3306, 3307, 3308, 3309, 3311, 3312, 3314, 3317, 3319, 3320, 3322, 3323, 3326, 3327, 3328, 3329, 3330, 3334, 3335, 3337, 3338, 3339, 3340, 3341, 3343, 3344, 3347, 3350, 3354, 3356, 3359, 3360, 3361, 3363, 3368, 3372, 3374, 3375, 3377, 3378, 3381, 3382, 3384, 3562, 3571, 3576, 3577, 3578, 3694, 3695, 3696, 3697, 3699, 3713, 3732, 3733, 3734, 3735, 3737, 3751` | Player signature / nsig deciphering and warnings (`N` / `SIG`); **not an anti-bot challenge answer**. No solver refusal. |
| `extractor/youtube/_video.py:4067, 4068` | Playback captcha detection/error message; no answer. |

`https://<approved host>/share/r/<token>` and
`https://<approved host>/share/v/<token>` are accepted when the token is one
to 32 letters or digits, with an optional trailing slash and no userinfo,
port, query, or fragment. MetaMuse resolves them. They are not a yt-dlp
extractor, a plugin, or the generic extractor. `/share/r/` targets
`FacebookReel`. `/share/v/` targets `Facebook`. Submit requires an approval
for that key whose hosts contain the share URL's exact host. The permit stores
the target key. The stored request keeps the original share URL; extraction uses its reviewed canonical form.
`/share/p/<token>` is refused at submit with `E_UNSUPPORTED` and next action
`Use the Facebook post collector for /share/p/ links.`

Resolution is one checked GET of the share URL through the same handler:
checker, byte budget, wall clock, cancel, the HTTPS-to-HTTP refusal, and no
request body. Redirects are not followed. The first 30x `Location` is classified
before any further dial, in this order: host approval, challenge, checkpoint,
login. An unapproved host is `E_SCOPE_DENIED` even when its path is `/login`.
A challenge `Location`, including a percent-encoded `/__rd_verify` segment, is
`E_CHECKPOINT_REQUIRED`, not `E_UNSUPPORTED`. A checkpoint path is
`E_CHECKPOINT_REQUIRED`. A login path is `E_AUTH_REQUIRED`, message
`Login required`, next action `Stop. Do not retry a login wall.` The target is
then accepted only when it is `https`, its exact host is in the target
approval, and the target extractor's `suitable()` accepts the canonical form.
`/reel/<digits>` and `/<page>/videos/<digits>` drop the query and fragment.
`/watch/?v=<digits>` keeps only `v`. HTTP 429 uses the same rate-limit result
as any other dial: `E_RATE_LIMITED`, `Retry-After`, partial coverage, and an
empty job error. HTTP 404 and 410 are `E_RESOURCE_UNAVAILABLE`. HTTP 401 stays
`E_AUTH_REQUIRED`. A 200 is a challenge when it carries a challenge marker. It
is `E_AUTH_REQUIRED` only when the body has a login form whose action targets
a login path. Any other 200 is `E_UNSUPPORTED`, message `The share link did
not redirect`. A redirect to an unapproved host is `E_SCOPE_DENIED` before that
host is dialed. Nothing is stored. The source `use_basis` and the step result
record `requested_url` (the share URL) and `resolved_url` (the canonical URL).
A resume that reuses `resolved_url` checks its host against the exact approval
matched by the original share input and checks the target extractor's
`suitable()` before any dial. Duplicate approvals for the same extractor do
not union their hosts. A mismatch is `E_SCOPE_DENIED`.

Share hosts, approval hosts, request hosts, and cookie domains use one
normalization, `safety.normalize_hostname`: strip one trailing dot, refuse a
second, encode an IDNA A-label, and lowercase. A share URL whose host ends in
`..` is refused at submit.

Extraction uses `extract_info(..., download=False, process=False)` and a
MetaMuse hop loop, so yt-dlp does not follow `url` or `url_transparent`
results itself. `process_ie_result` runs in that same `try` and `_extracting`
block, so its errors are classified and the process audit stays armed. A video
with no formats is a classified extraction error (`E_UNSUPPORTED`, message
`Extractor failed`), not `E_STORAGE`. Before `process_ie_result`, a `playlist`,
`multi_video`, or `compat_list` with more than one entry is `E_UNSUPPORTED`
(message `The upstream result is a playlist`). `additional_urls` is removed.
Nested single-entry lists are unwrapped under the same audit guard. One
`url` or `url_transparent` entry is sent back through the hop loop, so
`MAX_HOPS`, the approval, the host check, and `suitable()` apply. No URL is
extracted outside `_extract_chain`. A hop whose `ie_key` has an approval
containing the hop URL's exact host runs as a new extraction with
`allowed_extractors` set to that key only and a checker built from that
approval's hosts only. At most two hops are allowed. Each hop is a dial:
budget, wall clock, and cancel apply.

`_merge_transparent` keeps the inner result for identity and media. The outer
result may replace only descriptive fields. The stored `canonical_url`,
`platform`, extractor chain, and `upstream_collection_method` come from the
final inner extractor.

Inner, always: `id`, `extractor`, `extractor_key`, `webpage_url`,
`original_url`, `url`, `formats`, `requested_formats`, `subtitles`,
`requested_subtitles`, `automatic_captions`, `additional_urls`, `http_headers`.

Outer, only when the outer value is not null: `title`, `fulltitle`,
`alt_title`, `description`, `timestamp`, `upload_date`, `release_timestamp`,
`release_date`, `duration`, `channel`, `uploader`, `creator`, `artist`, `album`,
`track`, `genre`, `categories`, `tags`, `thumbnail`, `thumbnails`,
`view_count`, `like_count`, `comment_count`, `availability`, `age_limit`,
`live_status`, `series`, `season`, `episode`, `episode_number`,
`season_number`, `section_start`, `section_end`.

The source `use_basis` and the step result record the extractor chain, for
example `["FacebookReel", "Facebook"]`. `upstream_collection_method` is the
final inner extractor. A hop to an unapproved key or host is `E_SCOPE_DENIED`.
The next action names the extractor key and the exact host. Nothing is stored.
A third hop is `E_UNSUPPORTED`. `FacebookIE` rewrites `m.facebook.com` to
`www.facebook.com` before its page GET, so an approval that only lists the
mobile host allows the hop and then the checker refuses the desktop host.

Cookies, netrc, the yt-dlp cache, usernames, passwords, and client certificates
stay off in the fixed parameters. `cookiefile` stays unset. `CollectRequest`
has no `auth_ref`: model content cannot choose credentials. An operator may
set `auth_ref` on an approval. Preparation checks the metadata of every bound
reference without reading secret bytes: it must exist and match the configured
provider/account. A failure is the B08 `E_AUTH_REQUIRED` secret error with no
approval preview. At the step, every bound secret is resolved with
`resolve_secret(auth_ref, provider=<permit provider>, account=<permit account>)`
and parsed before the external-action boundary consumes the permit, including
bindings for later hops that this run might never use. The imported bytes must be a Netscape cookie file. A secret
that is a `Cookie:` header line is `E_INPUT_INVALID` with message `A Netscape
cookie file is required`. The message does not echo the file. Parsing strips
only the line terminator, so an empty seventh field stays an empty value.
`#` comments and the `#HttpOnly_` prefix are unchanged. Every name must be an
RFC 6265 token. Every value must be RFC 6265 cookie-octets, optionally wrapped
in one pair of double quotes: no controls, whitespace, `;`, `,`, `\`, or a
stray `"`. Every domain must pass `safety.normalize_hostname`. Any invalid
line rejects the whole file with `E_INPUT_INVALID`. The message names the
1-based line number and never the line. Cookies are parsed in memory. A
`Cookie` header is added only when every condition holds: the request host is
in that approval's hosts, the cookie domain-matches and path-matches, and a
`Secure` cookie is sent only over `https`. A `.facebook.com` cookie does not
match an `fbcdn.net` host in the same approval. A non-`Secure` cookie is the
only kind that may be sent over `http`, and it still does not leave its
domain. Subtitle and media fetches use the same rule. `Set-Cookie` is ignored.
The job stores no new credential. Cookie values are absent from the
checkpoint, step result, job request, events, logs, envelopes, exceptions,
and the database. The secret behind `auth_ref` is that Netscape file. Putting
`auth_ref` only on `FacebookReel` does not send the cookie on the `Facebook`
watch hop. Put the same reference on both approvals when the share GET and
the watch GET should both carry it. Any broken bound secret stops the run
before permit consumption and before any dial.

```toml
[[remote_media.approvals]]
extractor = "FacebookReel"
hosts = ["www.facebook.com"]
auth_ref = "auth_0123456789abcdef0123456789abcdef"

[[remote_media.approvals]]
extractor = "Facebook"
hosts = ["m.facebook.com", "www.facebook.com"]
auth_ref = "auth_0123456789abcdef0123456789abcdef"
```

`auth_ref` is optional. The value above is a synthetic reference, not a
secret. The bytes it names must be a Netscape cookie file. A `Cookie:` header
line is refused. The same reference on both approvals is what sends that file
on the share GET and on the Facebook watch hop for
`https://www.facebook.com/share/r/ReelTokenOne/` and
`https://www.facebook.com/share/r/ReelTokenTwo/`. A token left under
`XDG_CACHE_HOME` is not read. Without `auth_ref`, those two inputs are still
the same two approvals and no cookie is sent.

A process-wide audit hook, installed once at import, refuses `subprocess.Popen`,
`os.system`, `os.posix_spawn`, `os.exec*`, and `os.fork*` while extraction or
the request handler is on the stack. The refusal is `E_UNSUPPORTED` with the
message `extractor requires an external process`. There is no fallback. Local
ffprobe runs with the flag clear. `ffmpeg_location` is a path the job does not
create: yt-dlp treats a missing location as "no ffmpeg" and does not run
`ffmpeg -bsfs` during extraction. `None` would search `PATH` and run that probe.

Before every dial the handler checks the job wall clock and a pending cancel.
The dial timeout is the smaller of the read timeout and the remaining wall
clock. Every received body, including a redirect body and a non-2xx body, counts
against `max_bytes`. `bytes_used` is the total received. An HTTPS to HTTP
redirect is `E_SCOPE_DENIED`. `Authorization` is dropped when the scheme, host,
or port changes. A 307 or 308 that would forward a body to another origin is
`E_SCOPE_DENIED`.

A result with more than one playlist entry is `E_UNSUPPORTED`. The next action
is `submit the individual media URL`. Nothing from that result is stored. A
one-entry playlist is unwrapped and stored as that entry.

Subtitle bytes are one track, keyed by video, language, and method. The same
bytes and a complete track take the completed-hash shortcut. Any malformed cue
stores the valid cues with `complete` 0, adds `subtitles` to the missing
fields, and leaves the job `partial`. An incomplete track does not take the
shortcut. Changed bytes append a transcript revision for every cue in the new
file, reusing the segment id at the same cue index. `use_basis.revisions`
records each revision's own file hash. A packet or draft that cites an older
cue revision is stale. A subtitle HTTP 403 is `E_ACCESS_DENIED` and
`awaiting_user`, same as a page 403. No later subtitle or media request runs.

An unchanged title does not append an item revision. A changed upstream
timestamp is recorded on the source `use_basis` (`upstream_timestamp` and
`fetched_at`) and the original `item_revisions.raw_time` stays. A finite
fractional timestamp keeps its exact text, such as `1700000000.125`. A title
yt-dlp fabricated (`fulltitle` empty, or the synthesized `<extractor> video
#<id>` title) is stored as SQL NULL, `title` is missing, and coverage is
`partial`.

Login, a platform challenge, HTTP 403, geo restriction, and an unavailable item
stop the job. HTTP 429 stops and reports `Retry-After` without using the
safe-read retry loop. Partial coverage stays `partial`. Staging uses the same
`.collect-owner` marker with operation `collect.remote_media`, and the remote
sweep does not delete `import.local` directories.

Workspace caps (`safe_read_retries`, `read_timeout_seconds`, `media_max_bytes`,
`text_max_bytes`) are digest-bound at review. Any change before grant submission
requires new approval. Acceptance stores the reviewed snapshot; the step recomputes
the digest from that snapshot and uses the smaller of it and the current configuration. Lower caps apply to
queued jobs; raising a cap cannot exceed the reviewed snapshot. Request limits stay
in the digest and are rechecked against the current workspace before consumption.
B12 prevents retries beyond its effective cap from making egress; the core executor
can still schedule a local attempt that immediately stops at that guard. The final
error preserves the original read error's code/message, sets terminal `retry_class=never`,
and says in `next_action` that the accepted retry cap was reached.

B12 preparation enforces the shared 8192-byte UTF-8 URL bound and cheap HTTP(S)
syntax checks before redaction, display, digest, preview or job creation. One byte
over the bound returns the same outbound-policy error as the dial-time guard,
without a preview. The ASCII canonical URL must also fit this outbound bound.
URLInput still needs a core-owned schema/validation bound.

Stop-path comparison percent-decodes once, then strips each path segment at its
first semicolon before dot-segment normalization and case folding. Thus literal
`;`, `%3B` and `%3b` parameters cannot hide checkpoint, login or challenge segments.
The structural approval display shows the complete canonical path, exactly as extracted.

Transparent outer pages may change descriptive titles, but never `channel_id`,
`uploader_id`, `channel_url` or `uploader_url`. Native identity scope comes only
from the final inner result, so collecting the inner URL and an outer delegation
reuses the same source.

Login form actions resolve against the document's first parsable `<base href>`;
unparsable actions/hrefs are skipped and later ones are still checked. Missing or
empty actions use the document URL. Challenge detection matches `__rd_verify`
(case-insensitive, with or without a preceding slash), including relative paths,
literal `/`, HTML slash entities `&#47;`/`&#x2f;` and `%2F`. One percent-decoding
also covers `%5F`; JavaScript `\u005f`/`\x5f` underscore escapes and NFKC fullwidth
underscores are recognized. Split string literals are not detected; no JavaScript
is executed or reconstructed.
Invalid or credential-shaped `remote_media.provider` / `remote_media.account` labels
return a fixed E_INPUT_INVALID configuration error naming the setting, never its value.

Cookie domains must be non-IP hostnames with at least two labels: `.com`, `com` and `localhost` are refused.
Without a public-suffix list, B12 does not detect multi-label public suffixes such as
`co.uk`. IP literals and numeric shortenings such as `127.0.0.1` and `.1` are refused.
Browser exports never contain public-suffix cookies; use an unmodified
Netscape browser export and never synthesize a broad-domain cookie.

The digest-based approval-key ordinal is shared with journey. Two simultaneous
previews can show the same key, and changing approval terms with an already-used
key can conflict; this round leaves that core approval-key design unchanged.

For an accepted job, removing a first-hop host or the whole approval table also
returns E_SCOPE_DENIED with the fresh-preview next_action before consumption and
egress, even when the current selector cannot find an extractor.
