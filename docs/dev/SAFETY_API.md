# B08 safety API

This is the implementation detail for SPEC §7–§9 and SDS §2/§7/§9.
`auth import`, `auth list`, `auth revoke`, `permit create`, `permit revoke` and
`permit list` are local operator commands. They are absent from the MCP registry. Source text, Muse answers and
`approved: true` fields are data; no parser extracts or widens permission from them.
A host with arbitrary same-UID shell access can change local state; this is the SDS §2
local misuse boundary, not a hostile-host sandbox.

## URL and outbound-request hook

```python
from metamuse.safety import OutboundURLPolicy

policy = OutboundURLPolicy(resolver=backend_resolve_all, allowed_hosts=("example.test",))
checked = policy.before_request(url, purpose="request")
# Connect to a checked address with the original hostname for TLS/SNI verification.
# Use checked.url, not the original, potentially differently parsed URL.
# Call before_request again for every redirect, subresource and media/CDN request.
```

`check_url(url, *, purpose, resolver=None, allowed_hosts=())` is pure with respect to
network I/O: it never performs DNS. A resolver is injected and returns IP strings for
**every** address the backend could use. Names without a resolver, empty answers,
invalid addresses, more than 64 addresses or a single denied address fail closed.
Purpose is `seed`, `request`, `redirect`, `subresource` or `media`. There is no special
permission attached to a purpose. Optional allowlists compare normalized exact hosts;
subdomains, paths and queries never confer permission or select credentials.

The normalized URL uses stdlib IDNA A-labels, lowercase scheme/hostname, canonical IP
literals and removes a single trailing hostname dot. Existing punycode is roundtripped;
malformed labels, ambiguous numeric hosts, backslashes, userinfo, controls (including
percent-encoded controls), zero/invalid ports and URLs over 8192 UTF-8 bytes are denied.
Production destinations use port 80 or 443 only; nonstandard ports (including SSH,
SMTP and Redis ports) are denied. The explicit fixture checker also allows nonstandard
ports when every resolved address is loopback, never for a public destination.
Legacy IPv4 decimal/hex/octal/shortened forms use the local stdlib `inet_aton` parser,
not DNS. IPv4-mapped IPv6 is checked using its IPv4 address. Compatible IPv6, NAT64,
Teredo and 6to4 are recognized but conservatively denied. Ordinary IPv6 must be public
unicast in `2000::/3`; IETF special-purpose and documentation blocks are denied.
IPv4 denies RFC1918, CGNAT, loopback, zero/unspecified, link-local/metadata, multicast,
benchmarking, documentation, protocol-assignment, deprecated relay and reserved ranges.

Range review used the primary [IANA IPv4 special-purpose registry](https://www.iana.org/assignments/iana-ipv4-special-registry/)
and [IANA IPv6 special-purpose registry](https://www.iana.org/assignments/iana-ipv6-special-registry/)
on 2026-10-13; explicit blocks include `3fff::/20` so policy does not depend solely on
Python's bundled registry version. Some special-purpose publicly reachable addresses
are intentionally denied. Registry changes require policy/test review.

`check_fixture_url` is the separately named synthetic-server entry point. It adds only
loopback, including injected `localhost` resolution; it does not allow LAN/metadata
addresses. Production `check_url`/`OutboundURLPolicy` has no fixture toggle.

**Backend obligations:** intercept every actual request; disable unguarded automatic
redirects, proxy environment/config inheritance and alternate protocols; use the returned
canonical URL and checked addresses; preserve TLS hostname/certificate verification;
validate the actual peer (`before_request(..., peer_address=...)` can reject a mismatch).
Pin DNS results to connection establishment. Do not cache a seed approval as authority
for later requests. If a dependency's DNS/redirect/browser/media requests cannot be
intercepted and pinned, use process-level egress controls and a tested host set. Without
that control, do not expose arbitrary URL acquisition. The checker cannot stop a library
that ignores it, prevent a second DNS lookup racing connection, see requests inside an
external process, or prove platform access permission. No outbound backend is implemented
or marked effective by B08.

## Secret references

`import_auth(ctx, AuthImportRequest)` reads exactly the operator-specified file, up to
1 MiB; no browser-profile discovery or reading occurs. Dirfd traversal and `O_NOFOLLOW`
reject symlinks in the leaf and parents; `O_NONBLOCK` permits rejecting FIFOs without
hanging. Lexical `..` components are rejected before opening any directory or file;
relative paths are prefixed with the working directory, never collapsed with abspath.
`fstat` checks regular-file type, current UID, no group/other permission bits,
size, and unchanged file metadata after the bounded read. Errors do not echo paths,
content, auth references or raw transport/storage exceptions.

The storage implementation is the private-file fallback only; no keyring dependency
was added. The platformdirs user config root's `metamuse/auth` directory is 0700 and
files are 0600. Config overrides inside a MetaMuse checkout and symlink directory
components are rejected. A random opaque `auth_<uuid>` names a JSON record containing metadata
and base64 bytes. Base64 is an encoding, **not encryption**. Writes create an exclusive
0600 staging file, fsync it, atomically rename and fsync the directory. Secret bytes are
never written to the workspace database, request receipts, events or logs.

The returned metadata is only `auth_ref`, optional non-secret `provider`/`account`
labels, `storage_kind=private_file`, and `created_at`. Each import creates a new reference.
There is no browser-profile reader, secret-export command or secret-returning MCP tool.

`list_auth(ctx)` / `metamuse auth list` require `ctx.require_interactive()` and return
`data.references`: provider, account, auth_ref, created_at, storage_kind and config_bound.
The binding flag checks the current workspace's Muse config, even if Muse is disabled.
Only validated metadata is returned; neither secret values/base64 nor filesystem paths
are exposed. A missing directory lists zero refs. Corrupt leaves produce `partial`,
with valid `data.references`, opaque `data.unavailable` refs/fixed reasons and working
revoke commands or fixed manual cleanup instructions; no record content is returned.
Unsafe directories still fail with the fixed auth error.

`revoke_auth(ctx, auth_ref)` / `metamuse auth revoke --auth-ref REF` also require human
interaction. Dirfd traversal rejects symlink directories. lstat rejects FIFO/directory leaves with
a fixed manual instruction; symlink leaves themselves are unlinked without following them. One owned regular file or symlink is unlinked and the directory fsynced, without
reading its secret bytes; this also allows revoking a corrupt record. The response contains
auth_ref, revoked=true, config_bound and nullable next_action. A remaining config binding
gets an import-and-rebind next action; config and other references are preserved. This
removes the local record, not credentials at their provider or copies in external backups.
No MCP tool, automatic retention sweep or second credential store is added.

`resolve_secret(auth_ref, *, provider, account, secret_store=None)` returns bytes for
backend use only. Both nonempty provider/account labels are required keyword arguments,
including on `SecretStore.resolve`; None/empty/mismatched labels fail before secret read.
Wrong labels, unsafe/missing/corrupt files and invalid references fail with `E_AUTH_REQUIRED`.
Resolution raises its fixed error outside exception handlers: both `__cause__` and
`__context__` are None, including for JSON/UTF-8/base64 corruption.
An import without both labels cannot be resolved by a backend; re-import with both labels
to bind it. There is no unlabeled test escape or CLI/MCP path to secret bytes.
Do not put bytes into envelopes, logs, exceptions, prompts, packets or diagnostics.
`SecretStore(root=...)` is an explicit dependency-injection seam for synthetic tests,
not a remotely configurable workspace or MCP input. POSIX/Linux is the tested target;
Windows ACLs and OS keyrings are unimplemented. Permission errors are not repaired on
the operator's input file. An I/O failure after the final rename can leave an unreturned
private record; this is not reported as successful import.

## P0 permits

`create_permit(ctx, PermitCreateRequest)` requires existing project, explicit non-secret
provider/account/issuer labels, a timezone-aware expiry strictly after issuance and no
more than 30 days away, and `max_uses` in 1–1000. Scope schemas reject extra fields and
wildcards. `exact_digest`, if present, binds the complete `action_digest` below in
addition to the scope. Human creation uses the existing durable request receipt: the
same key/payload identifies the original grant; a changed payload conflicts. The returned
data always reloads its current row, including used/revoked/expires_at, and includes
`replayed: true` on a receipt hit (`false` on issuance). Replay never inserts a new grant,
extends expiry, refills uses or undoes revocation, including after expiry or exhaustion.

| Kind | Required scope | Allowed use |
|---|---|---|
| `access` | `operations`: nonempty subset of `muse.open`, `muse.read`, `collect.remote`; `target_digests`: 1–200 exact complete action digests | Exact destination and actual topic/read/collection payload, bound to project/provider/account |
| `share` | `packet_digests`: 1–200 exact stored packet digests **or** `snapshot`: immutable digest plus explicit exact evidence selection | `muse.send`, reading the referenced packet in the transaction |

Example share scope: `{"packet_digests": ["<64 lowercase SHA-256 hex characters>"]}`.
Snapshot shape: `{"snapshot": {"digest": "<snapshot_digest(selection)>", "selection":
[{"kind":"item_revision","id":"itm_synthetic","revision":1}]}}`.
`snapshot_digest` hashes canonical JSON of sorted/deduplicated exact evidence refs.
Creation validates project ownership and all original revisions/segments. Verification
loads the actual packet from the database and validates project, staleness and refs.
Every packet ref must belong to the approved snapshot; supplied claimed snapshot IDs or
`scope` dictionaries on a feature request have no authority. Snapshot A never follows
later revisions, new sources, another project or a changed provider/account.

The host/operator is responsible for the semantic scope of supplied question/text;
there is no automatic legality or privacy proof. A snapshot allows bounded question
variation within displayed evidence. Use `exact_digest` when the entire question and
payload must be approved verbatim. Preserve packet immutability and source-sharing
policy in the content/Muse feature implementation; B08 does not generate or redact text.

`verify_permit(store, permit_id, PermitUse(...), *, conn, reserve=False)` checks current
kind, project, provider/account, operation, scope, exact digest, expiry, revocation and
remaining uses. A missing or unknown permit/expiry returns `E_SCOPE_REQUIRED`; an unknown
ID has the message `Permit does not exist`. Invalid, expired,
revoked, exhausted or mismatched permissions return `E_SCOPE_DENIED`. Both carry an
operator `next_action`. With `reserve=True`, it requires the caller's transaction,
increments with a conditional SQL update and rolls back with the caller. The update
checks expiry using `metamuse_now()` on the Store connection, and core triggers recheck
expiry/revocation at consumption. Constraint/expiry-boundary rejection becomes a clean
`E_SCOPE_DENIED` without SQL or submitted values. Authorization columns are immutable,
used never decreases and revocation is sticky; a changed scope/budget/expiry needs a new
human grant. SQLite's julianday clock comparison can conservatively deny a use just
before expiry at submillisecond precision; it never extends or renews the permit. Use
`store.transaction()` (`BEGIN IMMEDIATE`) for atomic reservations. Do not reserve once
here and again in the external verifier.

`permit revoke ID --request-key KEY` is idempotent and permanent. `permit list --project
ID` returns current grant records, up to 200; over 200 returns `E_LIMIT_REACHED` instead
of presenting a partial list as complete. Paged permit listing is not implemented.
These commands remain human-only; fifteen existing MCP tools remain unchanged.

## External-action verifier

Persist discussion/exchange/collection intent and exact target first. A target contains
`project_id`, `provider`, `account`, `operation`, actual destination fields, `packet_id`
for share, and a stable local `intent_id` for submission. Do not include local request
keys, permit IDs, or claimed approval flags in the actual outbound target/payload.

```python
from metamuse.safety import PermitUse, action_digest, external_action_verifier

# target and actual_payload come from trusted feature code and persisted domain records.
use = PermitUse(
    kind="share",
    project_id=project_id,
    provider=provider,
    account=account,
    operation="muse.send",
    packet_id=packet_id,
    digest=action_digest(target, actual_payload),
)


def load_payload(conn, target):
    # Database-only: reconstruct actual bytes/fields to send; verify current discussion
    # account/session/quarantine and source-sharing policy here. Never accept a claimed hash.
    return load_exact_outbound_payload_and_verify_destination(conn, target)


step.before_external_action(
    step.target,
    verify=external_action_verifier(
        step,
        permit_id=permit_id,
        use=use,
        load_payload=load_payload,
    ),
)
# Intent and use have committed together; send the exact verified payload once.
```

`action_digest` hashes canonical JSON of actual target plus outbound payload, excluding
only local `intent_id`. Include every externally material destination, question, topic,
attachment digest and outbound field; no Unicode normalization. Supply JSON-compatible
values or exact outgoing UTF-8 text; binary bodies need an explicit base64 field in
the feature's persisted payload contract. `PermitUse.digest` is not
a permission by itself: the callback recomputes it from current domain state inside the
core guard transaction. It binds target fields to the use and persisted job/step.
The feature callback must validate current real destination/account/session and reconstruct
the payload that the transport will actually use. Mutable feature state must not change
that payload after verification. No external wait or DNS occurs inside the transaction.

The verifier increments the permit and records a `permit.use_reserved` event in that
same transaction. These events contain identifiers and hashes only. A repeated safe
read checks the same durable reservation identity and rechecks expiry/revocation/scope
without spending a second use (even if the last use is already spent). A submitted
step refuses a second boundary. Uncertain matching action digest or stable intent in
any job in this workspace/project blocks a fresh use, including a changed job request
key, local intent ID or permit. Keep original uncertainty and reservation records until
reliable feature-specific reconciliation; do not delete/prune these events while the
step or its permitted safe-read retry remains recoverable. Changing packet/payload on
the same uncertain intent also remains blocked.

The core owns epoch/cancellation/target fencing and the `submitting` transition. B08
adds no worker, queue, schema or migration. The feature owns reconciliation, receipts,
source shareability and quarantine after uncertain abandonment. No automatic refunds
or remote exactly-once guarantee exist; local transactions cannot remove the gap between
verification and remote submission. Same-UID code that rewrites/deletes intent state or
bypasses the verifier is outside the stated trust boundary.

Core's interaction guard runs before CLI delegation for all human-only auth/permit
commands, and safety entry points also call `ctx.require_interactive()`. A non-interactive
context cannot import, issue, revoke or list via these entry points. `permit approve`
also requires the explicit confirmation described below. Tests opt into live-mode configuration only for synthetic handlers while
socket/DNS calls remain blocked; this is FIXTURE/INTEGRATION evidence, never LIVE.

For `settle_uncertain`, read remote evidence outside transactions and supply a separate
database-only proof callback for definite delivery/refusal. Do not pass the send verifier,
reserve another send use, refund the original use, or reopen the original submission.
The settlement callback may use the supplied `TransactionWriter` for domain proof writes;
no nested Store transaction or network wait belongs there. Revoked/exhausted send grants
do not erase the original submission or prevent settling reliable proof of that submission.
Fetching remote history still needs its separate current access permission and URL policy.

The shared URL corpus runs in place with injected DNS, plus explicit allowlists for its
host-confusion rows and loopback answers for localhost. Its 4120-byte "overlong" and
single trailing-dot rejection expectations contradict this documented contract. The
table-driven test declares two explicit spec-based allow expectations; corpus data is
unchanged. All other corpus expected_verdict values are asserted without overrides.

The verifier also requires the registered side effect to match the action: `muse.open`
and `muse.send` use `external_submit`; `muse.read` and `collect.remote` use `safe_read`.


B10 integration: the external verifier returns the recomputed action digest after its
checks/reservation. Core mints a one-use token after commit. Live Muse credentials use
an immutable provider/account/auth_ref snapshot and the explicit cookie-header JSON
format documented in [MUSE.md](MUSE.md). A Muse share also requires exact_digest for
the prepared question/packet/target action, in addition to its snapshot/packet scope.

## Human approval without Python internals

Use the E_SCOPE_REQUIRED `approval` from a shipped Muse CLI/MCP call. The preview creates
nothing. The human saves the shown request JSON, reviews the topic/question/destination
and packet via `packet preview`, then runs `permit approve --operation OP --request-file
PATH --request-key KEY [--exact-digest SHA256] [--expires-at TIMESTAMP] [--max-uses N]`.
The command recomputes/displays review on stderr, including the effective absolute UTC
expires_at, remaining duration and max_uses before accepting terminal `yes` or an exact
recomputed digest. The displayed values are stored; defaults are 24 hours and one use. `--non-interactive` is refused and no MCP approval tool exists.
Default expiry is 24h; explicit expiry is timezone-aware and within 30 days. Default-expiry
replay validates stored receipts through the core duplicate-key-rejecting decoder and
never extends the original grant. Binding labels come from configuration only.
Changed request text or packet fails digest equality; preview data cannot mint permissions.
The fixed registration seam and B12/B13 adoption obligations are in [CORE_API.md](CORE_API.md).

B16a submit/prepare order is a read-only exact-payload receipt replay, the credential
check on new text, then preview/grant checks, persistence and work. Exact replay keeps
its original result after a rule upgrade. The worker checks session.start topics,
questions, packet fixpoints and body equality again before any token or gateway.
Frozen refusals prove not_submitted, close sends as abandoned and retain the refusal's
own next action. No refused send quarantines a discussion as unknown delivery.

Stored discussion titles are never re-checked as new outbound text. Previews show a
display-redacted "Stored discussion title", outside the send/read body. Reconciliation
identifies a discussion by discussion_id and project; its request topic must equal
the stored title or its current display-redacted form. approval.request and saved
request files carry only that display-redacted title. If the display changed since
saving, E_REFERENCE_INVALID asks to request reconciliation again. Approval failures
propagate their real fixed code, message and next action.

Muse sends exactly stored packet.rendered_text. Current rules changing an older
packet cause E_REFERENCE_INVALID "Packet is stale; prepare a new packet" in prepare,
preview, submit and wire, without a preview or rewriting stored bytes. Send guidance
is "Prepare a new packet and send request with a new exact share grant". The body is
the accepted original question, two newlines and that packet. Private original
exports retain original_private and their documented original bytes.

Redaction uses bounded possessive name grammars, forward scans and precomputed
quote/container/tag terminators. A consumed outer value ending in a sensitive
assignment name/operator extends over its following value, including across a newline.
Form scopes revealed by decoding use their own operator depth; deeper pluses remain
data. Scans advance past consumed values; a fixed number
of ordered interval streams merge and each pass builds output with one join.
Original-offset maps are sparse/coalesced; changed canonical views additionally use
one byte of decoding depth per character. The four-application bound is constant.
CPU growth of the iterated public call is checked at four doubling sizes for all
review and new-rule families. Raw canonical sizes are checked before rendering;
packet/bundle rendering and redaction precede BEGIN IMMEDIATE. Writes repeat only
reference/snapshot checks. _save_send redacts the full remote detail (capped at 1 MiB)
before its batch, then cuts to 512 characters. The settlement callback sanitizes
only locally bounded outbound text (48 KiB packet, 8 KiB question, two joining bytes)
inside the jobs transaction. Oversized legacy text refuses before redaction with
fixed E_LIMIT_REACHED; this bound is measured and does not prove delivery.

Auth list returns partial for corrupt entries: valid metadata in references, opaque
refs and fixed reasons in unavailable. It suggests working revoke commands for
regular records/symlinks, and a fixed manual instruction for FIFO/directory/unowned
entries. When --workspace was given, revoke suggestions retain it, shell quoted.
Revoke unlinks a symlink via dir_fd without opening its target; other non-regular
entries require manual cleanup. List/revoke read existing config for config_bound
without opening or initializing a workspace/Store. Noninteractive auth/permit
commands refuse before opening anything; scripted human permit approval needs no
TTY. Auth values and resolved secret-target paths never appear; explicitly supplied
workspace paths remain in the requested operator command. Revoked bindings ask to
import and rebind.

## B16a classifier and finding classes

`redact_text(text) -> (text, findings)` keeps its API. [SPEC](../design/02_SPEC.md)
contains every closed exemption, name exclusion, syntax, encoding and refused-by-design
form. `redaction_sensitive_key` classifies names; `redaction_sensitive_value` applies
those closed exemptions, including whole structured numbers/arrays/objects. Names are
bounded to 128 characters in text scanners. Name normalization is cached; credential
values are never cached as name-normalization entries. No third-party scanner pattern
is copied and no dependency or migration is added.

| Finding kind | Trigger |
| --- | --- |
| sensitive_field | Quoted JSON fields, XML values and curl user credentials |
| session_cookie | Sensitive unquoted assignment or flag |
| authorization | Authorization/Proxy-Authorization header value |
| cookie | Cookie/Set-Cookie, cookie-like chains or jar value |
| url_token | Sensitive/signed query value or userinfo password |
| api_key | Known token prefixes and JWTs |
| bearer_token | Bare Bearer value |
| private_key | PEM/PGP private-key blocks or PuTTY private-key files |
| redaction_unstable | Fourth single-pass application still changes text; whole output is masked |
| local_path | Plausible private path or explicit local_path field |

Consumers aggregate all kinds generically. The Muse check refuses every kind except
local_path. Overlaps share one opaque replacement and report the covering credential
class; local_path never overrides an overlapping credential class. Adjacent masks merge.
The sentinel is opaque at every rule. Quoted strings retain their quotes and numeric/
container JSON fields use a quoted sentinel to retain JSON syntax. Canonical views map
back through coalesced sparse escape runs in forward order. Original offsets are sparse;
decoding depth uses one byte per canonical character. There is no per-value suffix
rescanning or per-span output rebuilding. Literal query '?' stays in its value;
encoded '?' ends it. Encoded whitespace/quotes are value data, not URL-token boundaries.

Prefix rules are independently written: GitHub classic body 36 or PAT 22+59, GitLab 20,
Slack 10–13 digit segments and 24–64 character body (including xoxa-2), AWS 16 after the
prefix, Google 35, npm 36, Hugging Face 34, Stripe live body 24–200. The sk-style
body compatibility rule has a minimum of 8 characters and no upper bound, including Anthropic prefixes. Three JWT segments
including empty signatures, and matching private-key labels are separately recognized. Formats outside these documented/
compatibility bounds need a sensitive name to be recognized; this is a guard, not a
universal secret scanner or a proof that text is public.

Sensitive YAML keys also cover the complete indented container or block scalar,
through its dedent. Whitespace around assignment separators includes non-ASCII
spaces and split lines. A Kubernetes `valueFrom`/`secretKeyRef` object under a
sensitive key is refused by design; it is outside the closed environment-reference
exemptions. Quoted JSON-style keys can introduce raw-newline scalar values even in
an incomplete document.

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

The classifier, all closed exemptions, layer-aware terminators, scoped form decoding,
one-call fixpoint bound and out-of-scope grammars are normative in
[SPEC FR-019](../design/02_SPEC.md). `redact_display` returns the display-safe string;
redact_text already masks the entire output on instability, including for legacy
callers that discard findings. Packets/bundles additionally refuse the finding.

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

Reconciliation preparation and receipt replay use one topic-identity predicate:
the stored title or its current display-redacted form, together with discussion ID
and project. Replaying the human-approved request, before or after settlement, and
re-previewing an existing job retain the original job identity. A saved display title
that no longer matches current rules requires requesting reconciliation again.

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
Forced closure at an enclosing quote or end of input is not a match. A header sentinel followed by an unproven quote and a value suffix is not a whole-value exemption. Unclosed
placeholders and lookups are credentials. Fixed environment lookups have locally
quoted arguments, so parentheses or brackets within an argument are data. Lookup
and placeholder extents are indexed once; nested lookups never repeatedly copy
or decode a suffix per assignment.

HAR percent and JSON escape budgets are independent. An unrelated escaped candidate cannot consume the percent budget of a different record; each intermediate JSON layer is checked before further decoding.

Every scanner continues inside an exempt outer value. Assignment, header, XML,
query, curl and SQL/sshpass/mysql passes advance only past the matched key or
operator until masking a value. Cookie-chain discovery likewise revisits exempt
single members. URL candidates always resume at the next candidate; HAR walks
all string/container children; jar, prefix, JWT, block and path passes enumerate
independent matches. Only a selected redaction permits skipping its consumed value. A bounded quoted argument that is itself a sensitive key followed by an assignment operator is revisited: its overlapping assignment value begins beyond the argument.

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
its parameter operator), or a complete `${{ … }}` template expression. Random prose before the brace cannot turn an incomplete
credential into a valid placeholder. Fixed lookup and complete double-brace extents
are indexed once; exemption checks do not copy an enclosing suffix per assignment.
Exact JSON string boundaries stay mapped through form/percent/JSON decoding;
quoted defaults cannot jump over a proven enclosing JSON close. Raw special-scheme
backslash barriers exclude serialization backslashes and Unicode-escape data.
Netscape jar fields remain recognizable after a surrounding context prefix and
retain CR/LF line endings. These rules preserve the existing four-application bound.
