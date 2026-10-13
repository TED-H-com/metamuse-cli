# Local collection (B05/B06)

`submit_collect` accepts `local_file` and `text` inputs, persists one `import.local`
job, and returns `accepted`. The executor runs the job. Remote `url` inputs are
rejected before a job exists with `E_UNSUPPORTED`. `next_action` names B12/B13 and
LIVE. One URL rejects the whole request, including a mix with local inputs, and this
check happens before the project lookup.

The operation side effect is `none`. Registration is idempotent. `metamuse_collect`
reports verification `fixture_passed`, environment `ready`, and permission
`granted_scope`. `ffprobe` is optional and does not change that status. `media.py`
does not register its own operation.

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
| `text` | Optional. Null is allowed for a media-only item. At most 1,000,000 characters. |
| `source_url` | Optional, at most 4096 characters. The first value is `original_url` and is not overwritten. |
| `upstream_collection_method` | Optional. Absent becomes `unknown`. |
| `created_at_raw` | Optional display string, at most 256 characters. Not required to be a timestamp. |
| `created_at` | Optional timezone-aware ISO-8601. `Z` is accepted. A naive value rejects the row. |
| `author_display` | Optional, at most 512 characters. Stored in revision evidence. |
| `media` | Optional list of `{"ref", "kind"}`, at most 100 entries. `kind` is `image`, `video`, `audio`, or `file`. |

`resources` selects rows. `post` keeps posts. `comments` keeps comments and replies.
Other kinds in the file are `skipped`, not errors. `max_items` counts committed and
unchanged rows. Skips and rejections do not consume it. The next row that would be
accepted sets `limit_reached` and is not imported.

### JSON and JSONL

A JSON file is one object or an array of objects. The array index is the row number.
One object is row 1. Invalid UTF-8, invalid JSON, duplicate keys, `NaN`, and
`Infinity` fail the whole document (`parser_failed`). They are not an empty success.

JSONL splits on `\n`, strips one trailing `\r` before parsing, and skips blank lines.
The row number is the file line. Invalid UTF-8, invalid JSON, and nonfinite numbers
reject that line. Neighboring lines are still committed. One leading UTF-8 BOM is
removed. A U+FEFF later in the text stays.

### CSV

The header must include `platform` and `kind`. Columns are the record fields plus
`media_ref` and `media_kind`. A blank cell is null. The row number is the 1-based
data record, so a quoted embedded newline is still one row. `media` is a JSON array.
When that array is non-empty, `media_ref` and `media_kind` are ignored. A present
`parent_native_id` column clears the parent when the cell is blank; omit the column
to leave an existing parent. Invalid UTF-8, a missing required header, a repeated
header, or an unknown column fails the file.

### Plain text

`format: text` and a `text` input become one weak post: platform `generic`, kind
`post`, upstream `unknown`, and the decoded text unchanged. One leading BOM is
removed after the hash check. An empty file is still one item whose text is `""`.
Invalid UTF-8 fails the input.

## Identity

`(platform, native_scope, native_id)` is the reliable identity. Without a native id
the item is weak and receives an internal `itm_` id. That id is not the row number
and not a text hash. Two comments with the same text and different ids stay two
items. A weak item is always new. Not seeing an item again does not mark it deleted.

The same reliable id twice in one batch is applied in order. Identical text adds no
revision and the row is `unchanged`. Different text appends a revision and keeps the
previous one. A disagreeing kind rejects the later row with `E_REFERENCE_INVALID`
on `kind` and leaves the first row in place. A present parent field updates the
parent even when the text is unchanged, without a new revision. An absent parent
field does not clear one.

`item_revisions.text` is `NOT NULL`. A JSON null is stored as `""`, `missing_fields`
contains `text`, and `text_hash` is the digest of `{"text": null}`. An empty string
uses `{"text": ""}` and does not list `text` as missing. The two hashes differ.

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
omits it. A known upstream is not replaced, including by `unknown` or by a later
different declaration. `unknown` is filled when a later record names a method.
Revision evidence stores the declaration from that record next to this run's
method. Nothing automated is stored as `manual`. An identical-text reimport does
not rewrite evidence, so a URL-only change waits for the next text revision.
`original_url` remains the first `source_url`.

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
job read timeout, and one `file:` absolute-path argument. Its absence is probe
status `not_installed`. A non-zero exit, a timeout, unreadable output, or an exit 0
that leaves no file is a per-item error and not `ready`.

The hash path is `objects/sha256/<prefix>/<hash>`. The object is placed before the
database transaction. The ready asset row and the project link commit together.
The same bytes share one asset. Two direct imports keep two sources and two
`source_assets` rows. Two comment references keep two `item_assets` rows. The first
writer's MIME type and duration stay. Object paths do not contain titles.

`media_metadata` without `media_file` records the sniffed type on the new revision
and does not publish an object. A referenced file is read at run time. It is not
part of the submit-time hash. A reference that fails still commits the item, marks
the row's `media` field, adds `media` to `missing_fields` when the revision is new,
and ends the job `partial`. An identical-text reimport can attach bytes later and
does not clear `missing_fields`, because revisions are append-only.

Staging removal only deletes real directories named `job_*` under `staging` when
the job is missing, terminal, or the job currently running. Queued and
`awaiting_user` jobs are kept. Symlinks and core `object-*` files are left alone.

## Subtitles

WebVTT and SRT accept a leading BOM and CRLF. Cue settings are ignored. Styling
tags and multi-line cue text stay, joined with `\n`. `NOTE`, `STYLE`, and `REGION`
blocks are skipped. A cue needs `start >= 0` and `end >= start`. A malformed
timestamp or an inverted range is a row error. Touching endpoints are not overlaps.
Other overlaps are kept, stored on `overlap`, and noted on the row. They do not by
themselves make the job partial. An HTML page named `.vtt` or `.srt` is
`E_UNSUPPORTED`. A header-only WebVTT file has zero cues and is `empty_input`.

Each kept cue is a transcript segment with method `provided`, review `unreviewed`,
`cue_index` in kept order, and language from the request, else the WebVTT
`Language:` header, else `und`. Segments are project-scoped. The same file in
another project gets its own segments. Reimporting the same file hash onto the
same item and asset reports `unchanged` and does not duplicate cues. A different
attach target may add segments on the existing subtitle source. There is no
automatic link to an item imported in the same job; pass `--item` or `--asset`
for a record that already exists. The asset must already be visible in the project
through `item_assets` or `source_assets`.

Unchanged subtitle cues are listed even when they exceed `max_items`. Item imports
do count unchanged rows toward that cap.

## Local paths

`open_input_file` rejects an empty path, NUL, CR, LF, a component that starts with
`-`, a symlink anywhere in the walk (`O_NOFOLLOW`, then an inode check against the
real path), a non-regular file, and a path outside the configured roots. The root
directory itself is not a file. Byte limits count reads and do not trust `st_size`.
An import root that is itself a symlink is rejected. Configure the real directory.
A file reached through a symlink is rejected even when the link is inside a root.
