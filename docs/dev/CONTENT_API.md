# B07/B11 content API

The five content functions run synchronously through the existing `app.py` wrappers.
They use the one store and durable request receipts. They never call a model, create
permits, submit jobs, publish, upload, or access the network. Their capability evidence
is `fixture_passed`, with local workspace scope; no LIVE capability is implied.

## Search

`SearchRequest` accepts project_id, query, cursor and limit (default 50, maximum 200).
CLI exposes `search --project ID --query TEXT [--cursor CURSOR] [--limit N]`.
Search is an exact, case-sensitive SQLite `instr` substring match. It supports one- and
two-character zh-Hant queries without a tokenizer. It performs no Traditional/Simplified
conversion, case folding, Unicode normalization, stemming, or wildcard expansion.
Empty queries are invalid; literal newlines, emoji and combining characters are supported.

The first page fixes the latest revision of each item/segment at query time, matches
that revision's text, and saves ordered exact-reference membership in a private snapshot.
Order is item_revision before transcript_segment, then binary ID order and revision.
Later insertions and new revisions cannot change that membership. Pages resolve exact
revisions, rather than re-running an offset query against current latest revisions.
A deleted or newly foreign member fails with `E_REFERENCE_INVALID`; it is never silently
skipped. Source metadata is read at page time, while selected text revisions are fixed.

Results contain `ref` (an exact EvidenceRef), `snippet` (at most 240 code points),
`snippet_start_code_points` (offset into the original text), and whitelisted `source`
metadata including collection_method and upstream_collection_method. Transcript results
include method, timing, review and the limitation that frames were not analyzed.
`data.snapshot_id` identifies membership; `next_cursor` is a random opaque marker bound
to project and exact query digest. Malformed, unknown, expired, or foreign cursors are
`E_INPUT_INVALID`. Cursors survive application restart and page size can change.

Snapshots and cursor markers reside in `runtime/search/`, with 0700 directories and
0600 files. They hold only refs, project/query digests and offsets, not source text or
credentials. No migration or second database is added. There is no automatic expiry or
retention sweep yet: the operator may clear these markers to invalidate old cursors.
Privacy deletion must account for surviving reference markers. Markers are pagination
state and never permissions; same-UID hostile shell access is outside this trust model.

## Packet preview

`prepare_packet` requires an explicit, nonempty selection of source EvidenceRefs.
Each item or transcript revision must exist and be visible in the selected project;
draft revisions are rejected as original evidence. Rendered text includes exact selected
text, internal reference/revision labels, source method, upstream method, completeness
and missing fields (or transcript timing/review/limitations). It includes no raw transport
metadata, source URLs, headers, object paths, or use_basis dictionaries.

The text redaction guard runs before the rendered UTF-8 byte count and digest. The limit
is the smaller of 48 KiB and the configured packet limit, including labels and metadata.
Overflow is `E_LIMIT_REACHED`, never silent truncation. The digest is SHA-256 of the exact
rendered UTF-8 bytes (not a token count and not canonical JSON of the string).

The result includes the Packet fields plus `utf8_bytes`, `sources` and a redaction notice.
`rendered_text` is the exact pre-share text. Records contain only category and count;
never secret values, secret hashes or original offsets. Source text requesting secret
leaks, shell execution or permission changes remains inert text.

Packets are immutable through the feature API. A receipt replays the original preview;
changed payload under the same request key is `E_IDEMPOTENCY_CONFLICT`. New data requires
a new selection and key, creating a new packet. The core may mark the old packet stale
without changing its selection, text or digest. Replay still validates source existence.

Automatic redaction is a guard, not a guarantee of privacy, anonymity, legal compliance
or safe sharing. It covers URL token/signed parameters (including percent-encoded names),
Bearer headers, cookie/session strings, sk-style keys and absolute POSIX/Windows paths.
Structured bundle metadata also redacts recognized sensitive fields. Host/operator review
and the independent grant/send verifier remain necessary for any actual external share.

## Host drafts and preview

`DraftSaveRequest` adds optional `assets`, `variant`, and typed `preferences`.
`facts_refs` defaults to an empty list. CLI supports `--facts-file`, `--assets-file`,
`--variant-file`, and `--preferences-file` (strict UTF-8 JSON, with existing file limits).
Preferences default to language `und`, emoji `unspecified`, format `plain_text`, scope
`this_draft`. Explicit format may be `markdown`; persistent or project preferences are
not implemented, and no author persona, formula, CTA or timing strategy is imposed.

Save preserves only host-provided text, including CRLF and Unicode composition, and
performs no model call. New drafts start at revision 1 and omit expected_revision.
Updates require draft_id and expected_revision; a stale writer gets `E_DATA_STALE`.
Revisions append atomically with the head update and receipt. The digest uses canonical
JSON over full text, platform, variant, exact facts_refs, asset IDs and preferences.
Evidence and ready assets must be visible in the project. Same request replay never
silently updates a newer draft head. References to drafts are checked transitively;
cycles or depth above 32 fail instead of recursively leaking foreign/missing facts.

Preview returns the exact draft revision, counts, declared format issues, reference and
asset issues, and date/number lexical conflict candidates. It counts Unicode code points
with Python len, UTF-16 units as UTF-16LE byte length / 2 (no BOM), and UTF-8 bytes by encoding.
It does not count grapheme clusters. Format checks report unknown format, NUL and empty
text; they do not assert platform rendering correctness. Missing/foreign/deleted refs and
missing asset bytes are listed. Reference existence never establishes semantic support.

No sourced, dated platform length limits are configured in this build. Every platform
therefore returns a warning and null limit/source/verified_at, with no guessed hard limit.
Conflict candidates compare written date/number tokens without interpreting claims,
converting dates, or asserting contradiction; the host must review them.

## Exports and recovery

`export_objects` supports text, markdown, jsonl, csv and bundle. All selections and the
transitive facts/assets of selected drafts are checked before export and on replay.
Missing/foreign/deleted refs fail. Original private text is preserved in text/Markdown/
JSONL; CSV prefixes an apostrophe to cells starting with `=`, `+`, `-`, `@`, tab or CR.
JSONL decoding recovers the original text exactly, including emoji, CRLF and composition.

Bundle is a directory containing redacted JSONL records, Markdown members and a manifest.
It includes whitelisted source information and draft asset metadata, not raw source
transport records or opaque media bytes. Copying media cannot establish public safety
using a text redaction guard. A full media archive remains pending; the manifest explicitly
records `asset_policy=metadata_only`. Bundle redaction has the same guard limitation as
packets. Other formats are private original-text exports and must not be described as
redacted public bundles.

Names are internal (`records.jsonl`, `records.csv`, `object-0001.txt`/`.md`, manifest.json),
never derived from source titles, URLs or local paths. All final members stay below
`exports/<internal-export-id>/`; symlink directory escapes are refused. Directory/file
permissions are 0700/0600. Artifact references are workspace-relative, not local abs paths.

An export is inserted as staging, files are written and fsynced, then manifest.json is
written with every payload member's SHA-256 and byte count. The manifest excludes its own
hash to avoid recursion; its exact bytes are verified against the stored manifest.
After directory fsync and rename, parent directories are fsynced, and ready state plus
receipt commit in the original transaction. Replay verifies member set, manifest bytes,
sizes and hashes, and refuses missing, altered or additional files. Nothing is uploaded.
Handled write/rename/commit errors roll back and remove only directories owned by this
attempt. A process/host crash may leave orphan staging/final directories without a ready
row; they are not returned as success and automatic orphan cleanup is not implemented.

## Integration pending

Schema 1 transcript visibility follows project item/asset links. All content visibility
SQL lives in `_transcript_scope_sql`; exact validation also calls the core store validator.
B05/B06 migration 002's project-scoped, nullable-asset transcripts need this helper and
the core validator adapted together. No migration, store semantics or jobs semantics
were changed for this task. Asset-independent transcripts are not claimed implemented.

`tools/measure_content_search.py` produces real MEASURE results for 30 fresh first-page
calls on a fixed 10,000-record synthetic zh-Hant corpus. It uses a unique home-directory
disk workspace and removes it on exit. It reports reference-environment deviations;
its RSS is process peak including setup, not the separate import-RSS acceptance result.
