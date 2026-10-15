# 機器可讀設計基線

十五個工具都是待實作application契約，不是已運行MCP。恢復版提供自包含inputSchema及共同output envelope；細項成功data依02_SPEC/03_SDS在實作模型補齊後產生專屬outputSchema。不要宣稱外層schema通過就驗證了資料內容、權限或平台能力。

EvidenceRef新增muse_observation：id指向immutable observation、revision固定1（schema條件及runtime驗證）。
草稿／匯出只接受同project的真實stored observation；backend/mock不由input供應。read回evidence_ref。
Derived draft preview、payload members及manifest附backend/mock/research_complete:false與muse_evidence；
fixture的mock=true，transitive refs不得丟標記。Packet拒絕Muse refs；引用不授權亦不重分享。
SQL拒絕ref重複kind/id/revision且Muse revision需JSON integer 1；Store decoding遞迴拒絕duplicate object keys。
Product bundle verifier核對stored attribution與manifest/JSONL/Markdown；hash一致仍不能丟或偽造標記。

EvidenceRef為kind/id/revision的統一識別。Packet只允許item_revision和transcript_segment；draft_revision可匯出但不能冒充原始來源。此語義需在app層明確驗證。

UUID等ID由實作建立，示例使用固定假ID。JSON Schema不能證明同project、grant有效性、來源真實性、parent無循環、UTF-8 bytes或副作用結果。這些是產品測試，不是文件驗證。

文件版與契約版1.1表示重新提供的交接基線；不是已發行CLI版本。實作前把詳細模型與工具schema一起固定，勿維護平行真相。

B02–B04 implementation clarification: `models.py` is now the schema source. Run
`uv run python -m metamuse.schemas` from the repo root to regenerate both checked-in
schema locations. Envelope error objects have optional nullable `next_action` in
addition to the twenty enumerated error codes and retry_class, resolving SPEC §11's
previous conflict with additionalProperties=false. All seven envelope fields remain
required on the wire; error status requires an error object and null data, other
statuses require null error. Detailed feature-specific success data schemas remain
future feature work; the core result shapes and domain schemas are documented in
[CORE_API.md](../../dev/CORE_API.md).

UTF-8 limits use model validation plus `x-max-utf8-bytes` schema metadata; standard
JSON Schema maxLength counts characters and cannot enforce those byte limits. The schema
regeneration command also regenerates the narrative rollup from 01/02/03/06 (including
the sources appendix) and refreshes MANIFEST. Edit the numbered narrative sources,
not a second hand-synchronized copy of their clarifications.

`metamuse skill export --dest DIR [--force]` is a human CLI command. It copies the
packaged Skill into that directory and is not a sixteenth MCP tool. The fifteen tool
contracts are unchanged. Installation is documented in
[SKILL.md](../../dev/SKILL.md).

B16a human CLI auth contracts (shared Envelope, no new MCP tool):

- `auth list`: no arguments; `data.references` contains valid objects with
  `provider`/`account` (nullable non-secret labels), `auth_ref` (opaque auth ID),
  `created_at` (timezone-aware timestamp), `storage_kind="private_file"` and
  `config_bound` (boolean for the current Muse config). No secret values or paths.
- `auth revoke --auth-ref REF`: `data` contains `auth_ref`, `revoked=true`,
  `config_bound` and nullable `next_action`. A remaining binding requires import and
  rebind. Symlinks are unlinked without following their target; FIFO/directory
  entries receive fixed manual cleanup guidance. Resolving after revocation returns
  fixed `E_AUTH_REQUIRED` with an empty cause/context chain.
- Both commands require human interaction; `--non-interactive` returns
  `E_SCOPE_REQUIRED` before storage access. Unsafe directories return fixed
  `E_AUTH_REQUIRED`; corrupt records produce the partial listing below. Missing storage lists zero refs; revoke requires an existing ref.

Muse's unchanged input schemas enforce topic/question credential checks after read-only
receipt replay and before previews/persistence. Only credential-class values return
fixed `E_INPUT_INVALID`; accepted prose/paths stay exact in preview and wire.

B16a auth-list partial contract: `status=partial`, `data.references` contains valid
metadata; `data.unavailable` contains only opaque `auth_ref`, a fixed `reason`, and
a working revoke `next_action` (including `--workspace` when supplied), or fixed
manual cleanup guidance for non-revocable entries. `data.next_action` names the
applicable cleanup actions. Corrupt values/content and directory paths are never returned.
Strict backend SecretStore.list/resolve failures retain fixed detached E_AUTH_REQUIRED.
B13 backend data uses existing open JSON result/provenance fields, documented in
[Facebook collection](../../dev/FACEBOOK.md): step data includes browser_sandbox,
requested_locator and resolved_permalink; Source.use_basis includes both locators.
Capability retains its existing three status fields. The unsandboxed-operator
warning belongs to Envelope.warnings, never a new Capability schema field.

B13 revision evidence additionally carries requested_locator, resolved_permalink,
permit_id, account and input_sha256 for the contributing snapshot. Source.use_basis
input_sha256 refers to its first contributing snapshot. These are additive entries
in existing free-form dictionaries; core schemas are unchanged.

B13 browser_sandbox is an observed value: enabled, disabled_by_operator or unverified.
Requested scope initially says unverified and carries browser_sandbox_requested
(required or operator_exception) separately. No core schema field is added.
