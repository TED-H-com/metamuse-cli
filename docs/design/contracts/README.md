# 機器可讀設計基線

十五個工具都是待實作application契約，不是已運行MCP。恢復版提供自包含inputSchema及共同output envelope；細項成功data依02_SPEC/03_SDS在實作模型補齊後產生專屬outputSchema。不要宣稱外層schema通過就驗證了資料內容、權限或平台能力。

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
