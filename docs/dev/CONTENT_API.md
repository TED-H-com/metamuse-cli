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

The first page captures SQLite item/segment row watermarks in one read transaction.
Every page computes latest revisions as of those watermarks, with exact-reference
membership and a content fingerprint. Order is item_revision before transcript_segment,
then binary ID order and revision. Keyset pagination keeps later insertions/revisions
out of the snapshot. Deletion, replacement or loss of visibility changes the fingerprint
and fails with `E_REFERENCE_INVALID`; it never silently skips members or substitutes an
older revision. Source metadata is read at page time, while selected text revisions are
fixed. Schema-1 transcript visibility can shrink when current item attachments change.

Results contain `ref` (an exact EvidenceRef), `snippet` (normally at most 240 code points),
`snippet_start_code_points` (offset into the original text), and whitelisted `source`
metadata including collection_method and upstream_collection_method. Transcript results
include method, timing, review and the limitation that frames were not analyzed.
Long queries reduce leading context; queries above 240 code points expand the snippet
to contain the full match. `data.snapshot_id` fingerprints membership. `next_cursor` is a
stateless, opaque HMAC-SHA256 authenticated token carrying project, exact query digest,
watermarks, fingerprint/count and last sort key. Malformed, tampered, wrong-workspace or
foreign cursors are `E_INPUT_INVALID`. Cursors survive restart and backup/restore; page
size can change. They grant no permission and are not an encryption mechanism.

Search creates no per-query state or files and performs no writes. Store initialization
provisions the active 32-byte `content.cursor` key in the private `workspace_keys` table.
Search signs with the active key and verifies against retained versions; rotation and
backup/restore preserve cursors signed by retained workspace_keys versions. Legacy
`content.cursor_key`/`v1` receipts are not imported; old development DBs missing the key
table must be recreated. Schema 001 is amended for fresh workspaces before release. Old file-marker cursors
are invalid; this implementation neither reads nor creates their files. Existing legacy
markers require operator retention cleanup. Same-UID hostile shell access is outside
this trust model.

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

Packets are immutable through the feature API and core payload triggers. A receipt
replays the original preview; changed payload under the same key is
`E_IDEMPOTENCY_CONFLICT`, before reference validation. New data requires a new selection
and key, creating a new packet. Replay validates current project/reference/asset access
and recomputes staleness without changing selection, text or digest. Already superseded
selections start stale. Timestamps use the Store connection's `metamuse_now()` clock.

Automatic redaction is a guard, not a guarantee of privacy, anonymity, legal compliance
or safe sharing. It covers URL query/fragment and encoded token/signed parameters,
JSON secret strings, Authorization schemes, inline/folded cookie/session strings,
sk-style keys and plausible absolute POSIX/Windows paths. ASCII-scoped credential names
avoid treating CJK key/value prose as cookies; bare CJK slash expressions remain intact.
Quoted values retain their delimiters, preserving embedded JSON syntax. Structured bundle
metadata also redacts recognized sensitive keys and hyphen/underscore variants.
Host/operator review and the independent grant/send verifier remain necessary for any
actual external share.

## Host drafts and preview

`DraftSaveRequest` adds optional `assets`, `variant`, and typed `preferences`.
`facts_refs` defaults to an empty list. CLI supports `--facts-file`, `--assets-file`,
`--variant-file`, and `--preferences-file` (strict UTF-8 JSON, with existing file limits).
Preferences default to language `und`, emoji `unspecified`, format `plain_text`, scope
`this_draft`. Language is a nonempty string of at most 35 code points; emoji is a
nonempty preference string of at most 32 code points. Both request and stored revision
use `DraftPreferences`, with extra fields rejected. Explicit format may be `markdown`;
persistent or project preferences are not implemented, and no author persona, formula,
CTA or timing strategy is imposed.

Save preserves only host-provided text, including CRLF and Unicode composition, and
performs no model call. New drafts start at revision 1 and omit expected_revision.
Updates require draft_id and expected_revision; a stale writer gets `E_DATA_STALE`.
Revisions use `TransactionWriter.append_draft_revision` for atomic CAS, head advancement
and receipt. Replays validate references/assets and report the selected revision's
staleness without overwriting a newer head. The digest uses canonical
JSON over full text, platform, variant, exact facts_refs, asset IDs and preferences.
Evidence and ready assets must be visible in the project. Same request replay never
silently updates a newer draft head. References to drafts are checked transitively with
one memo per operation. Cycles or depth above 32 fail with `E_REFERENCE_INVALID`;
2,000 unique reference nodes, 10,000 reference/asset edges and the configured text-byte
budget bound total work with `E_LIMIT_REACHED`. Cached subtrees retain their depth bound.
Export serializes each dependency once as a flat record with direct `facts_refs`, rather
than duplicating nested diamond branches. Final export payload bytes are also bounded
by the configured text-byte budget.

Preview returns the exact draft revision, counts, declared format issues, reference and
asset issues, and date/number lexical conflict candidates. It counts Unicode code points
with Python len, UTF-16 units as UTF-16LE byte length / 2 (no BOM), and UTF-8 bytes by encoding.
It does not count grapheme clusters. Typed formats are plain_text/markdown; objective
checks report NUL and empty text, without asserting platform rendering correctness.
Missing/foreign/deleted refs and missing asset bytes are listed. Asset issues identify
the asset and owning exact ref; transitive asset failures do not abort remaining facts.
Reference existence never establishes semantic support. Staleness follows the specific
revision's facts, including transitive refs and newer transcript revisions, rather than
using the draft head's flag.

No sourced, dated platform length limits are configured in this build. Every platform
therefore returns a warning and null limit/source/verified_at, with no guessed hard limit.
Conflict candidates include transitive facts and compare written date/number tokens
without interpreting claims, converting dates, or asserting contradiction. Matched dates
are removed before number-token extraction. The host must review candidates.

## Exports and recovery

`export_objects` supports text, markdown, jsonl, csv and bundle. All selections and the
transitive facts/assets of selected drafts are checked before export and on replay.
Missing/foreign/deleted refs fail. Original private text is preserved in text/Markdown/
JSONL. CSV prefixes an apostrophe to any cell starting with tab, CR or LF, or whose first
non-whitespace character after removing leading single/double quotes is `=`, `+`, `-`,
`@` or `|`. Whitespace includes Unicode whitespace; quote removal repeats across spaces.
JSONL decoding recovers the original text exactly, including emoji, CRLF and composition.

Bundle is a directory containing redacted JSONL records, Markdown members and a manifest.
It includes whitelisted source information and draft asset metadata, not raw source
transport records or opaque media bytes. Copying media cannot establish public safety
using a text redaction guard. A full media archive remains pending; the manifest explicitly
records `asset_policy=metadata_only`. Bundle redaction has the same guard limitation as
packets. Other formats are private original-text exports and must not be described as
redacted public bundles. Private manifests set `redactions=null` (not a zero-redaction
audit) and private Markdown members carry an inline private unredacted export notice.

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

Core backups include SQLite, ready objects and every ready export directory with its
manifest. Backup and restore use the same Store.verify_export checks as receipt replay:
exact manifest bytes, payload hashes/sizes, no symlinks, hardlinks or unexpected members.
Missing/changed live export artifacts produce a partial backup warning and an unavailable
inventory entry, without blocking workspace open or migration. Restore preserves that
unavailable state, so its old receipt keeps failing honestly. Verified original artifacts
still replay after reference checks; they are never regenerated under an old receipt.
Ready/unavailable export payloads are frozen, and replay compares the authoritative row
with its receipt before verifying files. Orphan/uncommitted directories are excluded.

## Integration pending

Schema 1 lacks transcript project ownership. All content visibility SQL lives in
`_transcript_scope_sql`: a transcript requires an asset attachment through a current item
revision in the requested project and no surviving attachment in any other project.
The latter includes historical attachments, conservatively avoiding exposure after a
different project's current item drops that asset. Shared assets hide transcripts from
both projects; historical attachments in the requested project alone grant no access.
This is a fail-closed stopgap, not an ownership inference. When B05/B06 migration 002
lands, switch this helper to authoritative `transcript_segments.project_id` with nullable
assets/item/source anchors, and adapt the core validator together. No migration, store
semantics or jobs semantics were changed. Asset-independent transcripts and proactive
core stale marking on transcript insertion remain pending; these content operations
compute transcript/transitive staleness on creation, preview and replay.

`tools/measure_content_search.py` produces real MEASURE results for 30 fresh first-page
calls on a fixed 10,000-record synthetic zh-Hant corpus. It uses a unique home-directory
disk workspace and removes it on exit. It reports reference-environment deviations;
its RSS is process peak including setup, not the separate import-RSS acceptance result.


Core backup/restore now return structured results. Use `backup_result.path` and
`restore_result.store`; complete/partial status, unavailable export IDs and warnings
remain available even when Python warnings are suppressed. Legacy manifests without
database checksums are refused. SQL conflict clauses (including OR IGNORE/DO NOTHING)
and rowid writes are forbidden; deduplicate with SELECT/request receipt checks and plain
INSERT in a short transaction. Schema and immutable bindings are core-owned.
