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
fixed. Transcript visibility uses the authoritative schema-2 project_id; sharing object
bytes with another project grants no access to derived transcript text.

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

Automatic redaction is a guard, not a privacy, anonymity or legality guarantee.
The [SPEC classifier](../design/02_SPEC.md) binds the name-and-syntax rules and every
closed exemption. Sensitive structured fields are redacted whole for strings, numbers,
arrays and objects. JSON strings, including embedded/incomplete/escaped documents,
headers, cookie chains/jars, flags, XML, URL credentials, known token prefixes, JWTs,
private-key blocks and paths use the same shared detector. CJK prose and slash
expressions remain unchanged. Quoted JSON keeps its delimiters; container/numeric
JSON uses a quoted sentinel. Findings contain only category and count.

The shared redactor performs at most four applications: three changing passes and
one confirming pass. A fourth changing pass adds `redaction_unstable` and masks the
entire output. Packets and bundles refuse it with fixed E_INPUT_INVALID before hashing or writing any state.
Raw canonical size checks precede rendering; packet/bundle redaction runs outside the
write transaction, whose snapshot validation remains database-only. Sparse decoding
maps up to three percent and JSON-escape layers to original value spans and literal
plus is whitespace only within an actual URL query or complete form-body region.
Forward scans and interval closure prevent suffix rescanning and newly exposed values. Output uses one join. Current-rule changes to an
older packet refuse Muse prepare/preview/submit/wire with E_REFERENCE_INVALID, without
rewriting stored text or issuing an approval preview. Original exports remain
original_private. Original local search continues exposing selected source snippets;
it does not search packets.

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

## Immutable Muse evidence (AT-042)

`facts_refs` may include `{kind: "muse_observation", id: <read observation ID>, revision: 1}`.
Muse read supplies `evidence_ref` for each observation. The store checks existence and the
owning discussion's project. Observations are retained and append-only; enrichment creates
a new observation. Export reads that observation's fixed raw event, never mutable Message
text. Backend and observation digest come from storage, never host input. The reference
cannot carry extra `backend`, `mock`, permission or completion claims.

Save, preview and exports propagate `muse_evidence` through draft dependencies. Each entry
contains the exact ref, backend, mock, research_complete=false, stored observation digest,
discussion_id and observed_at. Derived records also expose backend/mock/research_complete;
backend is `mixed` only when multiple stored backend types occur, with each entry retaining
its own backend. Fixture entries always have mock=true; live entries remain labelled live
without claiming LIVE verification or completed research. No external operation is involved.

JSONL records and manifests carry these fields. Markdown and CSV source metadata include
them; text adds a `Muse evidence: <JSON>` notice before its unchanged body. Private formats
retain original text and have an explicit unredacted notice in their manifest. Bundle
redaction does not remove attribution. Packets reject both draft and Muse observation
refs, so citing a reply cannot re-share it. Muse text cannot create a grant or instruction.
Schema 4 only adds reference guards; the schema-3 observation identity/rowid guards remain.
The process-level walkthrough is [OFFLINE_SLICE.md](OFFLINE_SLICE.md).

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
sizes and hashes, and refuses missing, altered or additional files. Bundle verification
also compares every derived record/source and manifest Muse entry against the stored
observation's backend, mock and research_complete labels, requiring `muse_evidence`.
It checks JSONL and Markdown even when an altered bundle has internally consistent
hashes; a missing/altered label returns the fixed export-unavailable error. Nothing is uploaded.
Handled write/rename/commit errors roll back and remove only directories owned by this
attempt. A process/host crash may leave orphan staging/final directories without a ready
row; they are not returned as success and automatic orphan cleanup is not implemented.

Core backups include SQLite, ready objects and every ready export directory with its
manifest. Backup and restore use the same Store.verify_export checks as receipt replay:
exact manifest bytes, payload hashes/sizes, bundle Muse attribution from stored citations,
no symlinks, hardlinks or unexpected members.
Missing/changed live export artifacts produce a partial backup warning and an unavailable
inventory entry, without blocking workspace open or migration. Restore preserves that
unavailable state, so its old receipt keeps failing honestly. Verified original artifacts
still replay after reference checks; they are never regenerated under an old receipt.
Ready/unavailable export payloads are frozen, and replay compares the authoritative row
with its receipt before verifying files. Orphan/uncommitted directories are excluded.

## Schema-2 integration

`_transcript_scope_sql` uses authoritative `transcript_segments.project_id`. Transcripts
can use nullable asset/item/source anchors, and the core validator enforces project
ownership separately from object-byte sharing. A v1 transcript upgrades only when its
asset links to exactly one project; unlinked or ambiguous ownership aborts with the old
data retained. New transcript revisions mark directly derived packets/drafts stale in
the same transaction. Content operations also compute transitive staleness on creation,
preview and replay. Absent item text is SQL NULL and flattens to `[missing text]`.

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

Sensitive YAML keys also cover the complete indented container or block scalar,
through its dedent. Whitespace around assignment separators includes non-ASCII
spaces and split lines. A Kubernetes `valueFrom`/`secretKeyRef` object under a
sensitive key is refused by design; it is outside the closed environment-reference
exemptions. Quoted JSON-style keys can introduce raw-newline scalar values even in
an incomplete document.

Human `packet preview` refuses text rendered by older rules with the fixed
`E_REFERENCE_INVALID` stale-packet error before exposing it. URL userinfo stops at
the WHATWG authority boundary (`/`, `?`, `#`, whitespace, and backslash for special
schemes); deeper encoded delimiters remain inside the mapped password span. Scheme scanning has a forward anchor and accepts custom
schemes of any length.

Flags whose names are `authorization`, `proxy-authorization`, `cookie` or
`set-cookie` use the complete quoted value, or the complete remaining line when
unquoted, so schemes and cookie-chain members cannot fall outside the mask.

Plain YAML string values extend through the line (excluding a YAML comment);
TOML triple-quoted strings extend through their closing triple quote. Annotated
assignment defaults retain the type annotation and redact the actual value.
Sensitive XML elements may have quoted attributes; their content is redacted
whole, while a self-closing element has an empty value. Existing secure-cookie
and cloud-header name prefixes use the same three-layer decoding in structured
records as in text.

For every input without `redaction_unstable`,
`redact_text(redact_text(x)[0])[0] == redact_text(x)[0]`. Packet source text is
redacted with its original form-body scope before labels are joined. Structured
sensitive values are replaced whole regardless of type; HAR name/value objects and
cookies, CDATA/namespaced XML, flags, subscript keys and private-key formats follow
[SPEC FR-019](../design/02_SPEC.md). The layer rule treats deeper decoded delimiters
as value data, and original spans are never discarded for containing percent escapes.

URL authority candidates end at their enclosing quote/bracket at the same or a shallower
decoding layer, or the next same-layer or shallower scheme://. Each scheme is scanned independently; a root
URL never consumes a later URL. Unenclosed punctuation remains userinfo data.

Quote and bracket pairs are tracked at every decoding layer, including inside an
outer string. Quoted command arguments may open at their operator layer or deeper,
and end at the matching quote at that layer. Spaces inside remain value data.
Exempt environment lookups end at their own closing bracket or parenthesis.

HAR structure is read by an escape-aware JSON lexer at its JSON layer, before
percent or JSON escapes inside its string values become data in canonical views.
Complete documents and whole JSON-string layers share the lexer. Partial documents
stop at the first syntax error within each candidate; independent later candidates remain eligible. Name/value order
is immaterial; sensitive HAR scalars, including numbers and boolean/null literals,
are redacted whole, as are cookies arrays.

PuTTY private-key headers match after a non-identifier boundary at every layer,
including inline labels and JSON/form captures. The mask extends through Private-MAC
or, without that line, to its enclosing delimiter or end of input. Raw, JSON-escaped
and percent-encoded line breaks share this behaviour. PEM and PGP begin markers
have no line-start anchor; their existing matching-end/unterminated rule remains.

A whole-value exemption applies to that outer value only. Assignment scanning
continues after its key/operator to inspect nested assignments. Balanced shell
placeholder extents use the precomputed delimiter index, avoiding repeated suffix
parsing when defaults nest.

Redaction memory budget: a fresh subprocess making one public redact_text call
on 20 MiB of raw text must peak at no more than 1 GiB RSS. Syntax positions share
one lazy signed numeric index; quote intervals, newline offsets, edit coordinates
JSON lexer nesting, and retained mask spans use compact numeric arrays; decode depths use bytes.
Decoded syntax is released before the original-view syntax is constructed. Each
measurement uses a fresh parent and child; RUSAGE_CHILDREN reports that child only.

Layered grammar details: a bracket or separator inside a quoted value cannot close
an outer container or turn into a call-argument separator. An actual enclosing quote
still closes that value even when the quote belongs to a deeper decoded layer.
Raw replay recognizes the whole escaped closing quote, preserving whole-value
exemptions inside JSON captures. Shell quoting of option names, including curl
--user/--cookie, sshpass -p and attached mysql -p, retains the argument rule.
A whole form field is decoded before lexing a HAR document it contains, including
whole percent-encoded form bodies. The HAR lexer never decodes string data into
structure. A failed container stops at its first error; independently indexed later containers remain eligible, even after prose or a damaged outer candidate.

Known form boundaries are carried through decoded layers, including encoded newlines.
An encoded nested form prefix gets its own operator layer within its parent field;
its plus signs decode only at that layer or shallower. The scan never grants form
semantics to unrelated prose after a question mark. Whole escaped quoted placeholders
are canonicalized before applying the same closed exemption list; their nested
assignments remain independently inspected. Public-key block mirrors stay public.

Triple-quoted strings, heredoc/YAML multiline bodies and backticks are ordinary
credential-bearing contexts. Their benign content remains byte-for-byte intact.

B16a structural rule: ambiguity may only lengthen a redaction. Single-line single,
double and backtick quotes pair locally at the same decoding layer and never
cross a line break at that layer. An apostrophe between alphanumerics is prose,
not a delimiter. Triple quotes are one multiline token, recognized before single
quotes; heredoc bodies and YAML block scalars retain their multiline extent.
A fully parsed JSON layer proves exact string extents. Only a delimiter proved
by these rules may shorten a credential value, URL authority or private-key block.

At a credential value's start, a single, double or backtick quote opens its own
value if the same-layer, same-line matching quote follows. This local opener
takes priority over an unrelated earlier quote. Attached command flags use the
same rule; only a proven quoted flag's closing delimiter precedes its argument.

Exemptions require a complete genuine closing delimiter at the same layer.
Forced closure at an enclosing quote or end of input is not a match. Unclosed
placeholders and lookups are credentials. Fixed environment lookups have locally
quoted arguments, so parentheses or brackets within an argument are data. Lookup
and placeholder extents are indexed once; nested lookups never repeatedly copy
or decode a suffix per assignment.

Every scanner continues inside an exempt outer value. Assignment, header, XML,
query, curl and SQL/sshpass/mysql passes advance only past the matched key or
operator until masking a value. Cookie-chain discovery likewise revisits exempt
single members. URL candidates always resume at the next candidate; HAR walks
all string/container children; jar, prefix, JWT, block and path passes enumerate
independent matches. Only a selected redaction permits skipping its consumed value.

Protocol-relative // authorities are URL candidates, independent of preceding
URLs. A scheme or // exposed at a deeper layer inside a password remains value
data. A root candidate with no userinfo operator before a deeper scheme still
yields to that independently scanned URL. One monotone successor cursor per
bounded layer prevents rescanning. Markdown autolinks end at their proven > in both authority and query
passes. A raw backslash ends a special-scheme authority even when a permissive
decoded view would treat its escape as data. Accepted benign cost: an unenclosed
scheme://host:port,user@host contains WHATWG userinfo, as does a literal backslash
in a non-special scheme's userinfo; both are redacted by design.

HAR JSON containers are candidates at every offset and decoded layer, including
prose, fences, log prefixes and packet headers. A strict lexer evaluates completed
objects before an error; an independent candidate swallowed as string content is
not lost. Lexical ends and container outcomes are memoized once, so late failures
and candidate recovery remain linear. Any sensitive repeated name marks every
value in that object private, without overwriting an earlier sensitivity decision.

Private-key block labels accept space, form plus and percent-encoded space at
every supported layer. Quotes in PuTTY comments shorten the block only when
proved to close an enclosing string; otherwise it extends through Private-MAC.
Quoted form-body discovery pairs quotes per decoding layer, and decoded-only
whitespace remains form value data.


B16a context assurance: natural and seeded malformed contexts surround in-scope
rows both inside and outside raw, JSON (one/two), percent and form layers.
Representations stay within the supported decoding budgets: a row already using
three percent layers retains its context in raw/JSON form instead of receiving a
fourth percent/form wrapper. Count these representation adjustments; no natural
context is excluded. Whole-input peeling preserves percent escapes inside URL
password data.
The property checks marker fragments as well as whole values, and checks the
one-call fixpoint. Natural-context benign mirrors must remain unchanged;
malformed random-context benign changes are measured, since ambiguous structure
may lengthen a mask. The installed CPython corpus retains a 0.40% refusal budget.
HTML/XML elements make value/content private when the same element has a sensitive
name; type=password makes value private. Attribute order and quoting do not
change that rule. Benign username/description elements remain unchanged.


Grammar-proven HTML/XML attribute strings may contain line breaks; their local
matching quote is indexed separately from prose quote pairing. Attribute name/type
classification resolves HTML character references. Oversized (over 1,024-character)
name/type selectors fail closed before decoding. Exempt attribute values remain
searchable for independent nested elements. A malformed unquoted nested tag starts
an independent candidate instead of causing a repeated suffix walk.

A shell-parameter placeholder must have a complete balanced closing brace and a
parameter header (identifier/positional/special parameter, optionally followed by
its parameter operator). Random prose before the brace cannot turn an incomplete
credential into a valid placeholder. Fixed lookup and complete double-brace extents
are indexed once; exemption checks do not copy an enclosing suffix per assignment.
Exact JSON string boundaries stay mapped through form/percent/JSON decoding;
quoted defaults cannot jump over a proven enclosing JSON close. Raw special-scheme
backslash barriers exclude serialization backslashes and Unicode-escape data.
Netscape jar fields remain recognizable after a surrounding context prefix and
retain CR/LF line endings. These rules preserve the existing four-application bound.
