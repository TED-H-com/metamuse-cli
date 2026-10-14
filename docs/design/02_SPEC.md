# MetaMuse CLI — Product & Interface Specification

文件版 1.1｜2026-10-13｜下列命令與工具是待實作契約。  
MUST 必須；SHOULD 應；MAY 可選。數字是工程預設或驗收目標，不是平台安全頻率或既有效能。

## 1. 功能要求

| ID | 級別 | 功能 | 必須交付 |
|---|---|---|---|
| FR-001 | P0 | 單一產品與共用核心 | 一個套件／版本／workspace store，CLI／MCP 呼叫相同 application functions。 |
| FR-002 | P0 | 初始化與診斷 | 無憑證可離線 init／doctor，區分實作、驗證、環境與權限。 |
| FR-003 | P0 | 本地匯入 | 文字、CSV／JSON／JSONL、VTT／SRT 及指定媒體；逐項錯誤與真實來源。 |
| FR-004 | P0-LIVE | 有界來源收集 | 已獲准且已驗收的來源，指定 URL 與上限；不是全帳號歷史巡查。 |
| FR-005 | P0-LIVE | 留言及回覆 | 保留來源 ID、父子關係、原文和缺漏；真實目標另驗收。 |
| FR-006 | P0 | 影音與字幕 | metadata、可用字幕、必要下載；沒有字幕保持 unavailable，遠端路線另驗收。 |
| FR-007 | P0 | 持久化任務 | 先保存再排程；單 workspace 執行所有權；重啟不丟 queue。 |
| FR-008 | P0 | 續跑與防重複 | 讀取與外部發送重試分離；request_key 去重，不明副作用先對帳。 |
| FR-009 | P0 | 來源與版本 | 原文、原始時間、取得時間、來源方法、可靠 ID 及版本證據。 |
| FR-010 | P0 | 素材關聯與去重 | 相同 bytes 可共用，不能把不同來源、發布或留言身分合併。 |
| FR-011 | P0 | 搜尋與讀取 | project 隔離、分頁、片段與版本引用；中文測試。 |
| FR-012 | P0 | 對外分享資料包 | 固定資料版本、必要摘錄、預覽、來源方法與分享範圍。 |
| FR-013 | P0-LIVE | Muse 對話 | 主題 session 映射、文字問答和追問；不接管 harness。 |
| FR-014 | P0 | Muse 失敗恢復 | 送達不明先讀原對話，保存多段回答；回答不等於研究完成。 |
| FR-015 | P0 | 主 Agent 主導 | 主 Agent 提問題和文案，工具沒有第二個隱藏策略 Agent。 |
| FR-016 | P0 | 草稿與偏好 | 草稿版本、平台變體及明示偏好；無作者公式或自動永久學習。 |
| FR-017 | P0 | 檢查與匯出 | 客觀格式、引用缺口、文字／Markdown／素材包；不冒稱發布。 |
| FR-018 | P0 | CLI／MCP／Skill | 同一份工具契約、乾淨 stdio、明確錯誤；Skill 不授予權限。 |
| FR-019 | P0 | 憑證與權限 | 平台帳號隔離，秘密不進 prompt／log／public repo。 |
| FR-020 | P0 | 輸入與程序安全 | URL、出口、路徑、大小、固定 argv；來源文字不能變程式。 |
| FR-021 | P0 | 公開發行 | 授權清冊、乾淨歷史、虛構樣本、非官方聲明、能力實測表。 |
| FR-022 | P0 | 刪除與備份 | 索引與派生物、剩餘引用、backup restore；不保證遠端抹除。 |
| FR-023 | P0 | 觀測与容量 | 真實 count／bytes／duration；未知用量／費用不捏造。 |
| FR-024 | P1 | 基礎素材處理 | 可選 ASR、音訊、指定裁切、轉檔、圖片壓縮。 |
| FR-025 | P1 | 來源變更與影響 | 追加新觀測、標記引用草稿過期；不可見不推定刪除。 |
| FR-026 | P1 | 留言工作清單 | 待回覆／草稿／已處理，正確 parent；預設 draft_only。 |
| FR-027 | P1-LIVE | 發布與回覆 | 預覽及批准綁目標／內容／附件／期限；單目的地驗收。 |
| FR-028 | P1 | 發布對帳 | 不明禁止重送；各平台結果分開，不保證原子發布。 |
| FR-029 | P1 | 中性成效快照 | 原值、定義、時間、分母、缺值；不自動制定策略。 |
| FR-030 | P2 | 指定排程與監測 | 只有使用者指定時區、日期及範圍；無最佳時段策略。 |
| FR-031 | P2 | 其他入口與協作者 | 訊息入口／provider 共用核心，不先做外掛框架。 |
| FR-032 | P2 | 輕量工作台 | 現有工具的視圖，不新增資料真相、SaaS 或隱藏 AI。 |

## 2. 範圍與能力表

一個 workspace 對應本機使用者，一個 MCP process 啟動時綁定一個 workspace；工具不能任意切 data root。初版不是多租戶隔離服務。project 是資料選擇邊界。

資料模型可以保存多種 platform 名稱，但不代表每一種都可讀取／發布。每項能力分開保存 implementation（absent/implemented）、verification（unverified/fixture_passed/live_verified/regressed）、environment（ready/missing_dependency/needs_auth/unavailable）、permission（not_configured/granted_scope/denied），再計算 effective。驗收附 backend version、帳號類型、頁型、時間與證據。

離線 doctor 不觸網；明示 live 才做獲准健康檢查。缺 Muse 或 browser 不阻擋本地功能。

## 3. 共用資料契約

| 物件 | 必要資訊與規則 |
|---|---|
| Source | platform、native scope/id、原始／正規化 URL、collection_method、取得時間、使用依據引用。 |
| ItemObservation | project/source、kind(post/comment/reply)、native ID、parent、原文、原始與解析時間、revision、完整性與缺值。 |
| Asset | SHA-256、MIME、實際 bytes、時長、私有路徑引用、所有來源關係；相同bytes不抹掉不同來源。 |
| TranscriptSegment | asset、revision、start/end毫秒、語言、原文、provided/auto/asr/manual、校對狀態。 |
| Packet | 固定來源revision／字幕區間、實際送出文本、redactions、來源方式、範圍、digest。 |
| Discussion/Exchange | project、remote session、question、packet版本、提交意圖／狀態、cursor、raw messages與關聯狀態。 |
| Draft | 完整文字、revision、platform variants、facts／assets、明示偏好、digest。 |
| Publication（P1） | 目標帳號／目的地／parent、精確payload／附件、批准、attempt、remote receipt及對帳。 |
| MetricSnapshot（P1） | 平台／指標定義、原值、單位、取得時間、觀測期間、qualifier；missing不等於0。 |

來源方法保留真實值。file_import只表示這次讀檔，上游原取得方式另記 upstream_collection_method；不知道就 unknown，不能將自動來源改稱人工找到。操作者聲明與系統實際觀测分開。

native ID 缺少就建立內部 ID＋weak identity；不得用本次第幾筆當穩定留言 ID。parent沒取得時保留 unresolved，不能亂接到其他留言。相似文字不是同一筆資料。

## 4. 收集與素材

輸入為指定 URL、本地檔或文字。resources 明列 post/comments/media_metadata/subtitles/media_file。預設不下載完整影片；僅 metadata和所需可用字幕。無字幕回 unavailable；ASR另須啟用與分享許可。

JSON/JSONL/CSV逐項驗證，保留繁中、emoji、換行及無文字媒體項目。VTT/SRT驗證start≥0、end≥start；合理重疊可保留並標記。格式不支援就具體報錯，不當成HTML或可執行檔。

取得上限和資料完整性分開。多次滾動無新增只是一種停止理由，不能證明全部留言。登入頁、checkpoint、權限拒絕、限流、selector變動、timeout與真實空資料必須有不同結果。

同媒體bytes可共用asset，但保留貼文、來源與字幕關聯。只讀字幕只能說依字幕判讀，不能說已分析影片畫面。

## 5. Jobs 與結果

```text
queued → running → succeeded | partial | failed | awaiting_user | cancelled
```

accepted僅表示已commit job；沒有worker時必須queued+worker_available:false。完成只指本次有界工作，另存 coverage_status（unknown/scope_satisfied/bounded/partial）、requested/observed_scope、stop_reason、missing_fields。

同operation＋request_key相同payload回原結果；同key異payload回E_IDEMPOTENCY_CONFLICT。要新觀測用新key。本地去重不是遠端exactly-once保證。

job取消先持久化，在安全邊界停止。已發送Muse／發文只能回本地停止與remote cancel狀態；沒證據不可回remote confirmed。中止MCP等待不自動取消已接受job，需明確job_control。

未claim step的worker遇暫時SQLite BUSY／LOCKED須保持存活，以有界jitter backoff重試並可stop；
每個unclaimed lock attempt最多1秒，stop加exit cleanup最多2秒lock wait；claimed transaction仍30秒，
finalization/fallback及zero-handoff保存可合計120秒，細節及限制見SDS。
既有worker status顯示有界storage_busy_retries。已claim步驟仍遵循原failure／uncertainty規則，
不自動重送external_submit。非busy的attached worker失敗顯示failed與固定worker_error_code，
不暴露exception內容。一般CLI等待storage lock逾時仍回E_STORAGE；詳細timeout與停止界線見SDS。

## 6. Muse 對話契約

只依賴最小session、send和history。主Agent提供問題和文案，MetaMuse不自動重規劃研究。Muse自然語言原文照存，不假定strict JSON或可註冊任意tools。

```text
prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed
```

一個discussion同時一個未結算exchange。新topic本身也會外送，open需要存取範圍。send須驗packet和分享grant，保存intent後只送一次。timeout先查原session；沒有看見回覆不等於沒有送達。

保留progress、多段回答與晚到訊息。關聯必須檢查session、baseline、可辨識user echo、role和時間，不能只靠最新一則assistant訊息。歧義保存為ambiguous，不直接配對。回覆存在不等於研究完成；由host結算answered/needs_more_data/abandoned。
echo關聯以同一deterministic redaction比較outbound與observation；wire保留host原文。prepared與已證明not_submitted的exchange不參與關聯。訊息首次observation必須晚於該send boundary的高水位；enrichment不改首次時間。無marker且同文符合多個已提交exchange時保存sticky ambiguous，不認領訊息或建立echo proof。history proof與第一次transport outcome分開保存，後到ack不可丟失。

abandoned時若外部提交仍不明，discussion應quarantined，不能立即派下一題繞過未解提交。close_exchange不是遠端cancel。side chat不是跨客戶保密隔離；本地path不是Muse可讀資源。分析prompt不是停用Muse既有寫入工具的權限鎖。

B10實作補充：零bytes證據只接受同process內transport記帳；持久化結果前crash仍unknown。已證明零handoff的記帳先於cancel保存，原step為local failed/remote not_submitted；同時host close不重開exchange，settlement後才解除原step隔離。記帳或settlement寫入失敗時持久化失敗事件、保留uncertain並繼續queue。
`not_submitted`不退grant、不requeue；新提交要新key與grant。binding只含provider/account/auth_ref，
discussion固定snapshot。fixture backend須明示選擇，discussion/message/observation及content envelope標mock，
inspect只fixture_passed。read預設local；refresh排safe_read job＋current access grant。open對帳可明示原discussion_id，
unknown只排list job，不再create；已mapped discussion則排history baseline read。cursor由workspace key簽名，綁discussion/exchange與observation高水位。
open在提交與boundary兩次檢查同project/account的未解create；已queued的第二次create也不得越過，grant未使用、step本地failed。

B09安全狀態：authenticated CVM hint bind以單一bulk transaction保存全部usable identities，
closure只計算一次；confidential evidence先獨立commit，才檢查local label。無recording cap。

Notary freshness is only a cache hint, not local signature/expiry verification.
Missing or undecodable third-segment JSON, a non-object document or non-list restrictions
means no hint. Skip non-strings, non-timeout prefixes and entries whose stripped timeout:
remainder fails Python float(), is non-finite or is <= 0. The first positive finite
value decides stale iff expiry <= now + 60 seconds; ignore later entries. None usable
means not stale. Mint at most twice; a twice-stale remint stops before wake or dial.

## 7. 分享與草稿

Packet固定版本；修正建立新packet。批准可以涵蓋已展示的project snapshot，允許host在其中自行選資料和有限追問；新增來源或帳號需新範圍。不需要每個讀取都打斷使用者，但每次實際外送內容要可回查。

Question也是分享資料。去姓名不代表匿名，URL和原文仍可能識別人物。來源內容和Muse回答都不能建立grant、執行shell、修改取得方式或擴大授權。

Draft save只保存host提供文字；更新必須expected_revision，不默默覆蓋。預設language=und、emoji=unspecified、plain_text、this_draft；使用者明示才改。沒有作者人設或策略。

AT-042引用補充：EvidenceRef新增`muse_observation`，id為host讀回的immutable observation，revision固定1。
Store驗證同project的discussion與observation，不接受host自填backend/mock。Draft save、preview及所有export
（bundle/jsonl/markdown/text/csv）的Muse-derived records保留`backend`、`mock`、`research_complete:false`
及`muse_evidence`（exact ref、stored digest、discussion、observed_at）；transitive draft引用也保留。
fixture的mock固定true；live的backend照實保存，不憑內容升級verification。Export manifest亦保留同樣attribution。
Text在原文前加Muse evidence notice；Markdown/CSV的source攜帶attribution；JSONL保真text欄位。
Packet只接受原始item/transcript，不接受Muse observation或draft；引用Muse不授權、不重分享、不證明研究完成。
Stored JSON拒絕所有層級的duplicate object keys，使用既有固定E_STORAGE且不echo值。
Draft/packet SQL guards拒絕重複kind/id/revision；Muse revision必須JSON integer 1，不接受true。
Product bundle verifier從stored citations核對manifest muse_evidence、JSONL records及Markdown source的
backend/mock/research_complete；即使重算hash一致，缺少或偽造標記仍是固定export unavailable錯誤。
已知bare hatch_sess cookie pair未被shared redaction遮蔽，AT-042 strict xfail固定此限制，B16a修正。

Preview檢查客觀格式、素材可用、引用存在、日期/數字衝突候選。引用存在不代表語義被支持。Export生成本地文件與manifest，不發布或上傳。

## 8. CLI

所有命令是開發目標。全域 --workspace、--json、--non-interactive 放子命令前。

```text
metamuse init
metamuse doctor [--live]
metamuse project create --name NAME --request-key KEY
metamuse add --project ID --file PATH --kind FORMAT --request-key KEY [--run]
metamuse add --project ID --url URL --resources LIST --access-grant ID --request-key KEY [--run]
metamuse jobs list [--project ID] [--cursor CURSOR] [--limit N]
metamuse jobs get ID
metamuse jobs resume ID --request-key KEY
metamuse jobs cancel ID --request-key KEY
metamuse worker run [--once]
metamuse search --project ID --query TEXT [--cursor CURSOR] [--limit N]
metamuse packet prepare --project ID --selection-file PATH --request-key KEY
metamuse packet preview ID
metamuse muse open --project ID --topic TEXT [--access-grant ID] --request-key KEY [--discussion ID]
metamuse muse send --discussion ID --packet ID --question-file PATH [--share-grant ID] --request-key KEY
metamuse muse read --exchange ID [--refresh] [--access-grant ID] [--cursor CURSOR]
metamuse muse close-exchange ID --outcome answered|needs_more_data|abandoned --request-key KEY
metamuse draft save --project ID --text-file PATH --platform PLATFORM --request-key KEY
metamuse draft preview ID --revision N
metamuse export --project ID --refs-file PATH --format FORMAT --request-key KEY
metamuse skill export --dest DIR [--force]
metamuse mcp [--no-worker]
metamuse permit approve --operation OP --request-file PATH --request-key KEY [--exact-digest SHA256] [--expires-at TIMESTAMP] [--max-uses N]
```

更新草稿加 --draft ID --expected-revision N。明示引用／附件／變體／偏好可用 --facts-file、--assets-file、--variant-file、--preferences-file（JSON）。人類本地auth import／permit create另提供；不是能讓MCP自己approved:true的工具。憑證匯入需操作者指定檔案，不讀任意主瀏覽器profile。

CLI add預設持久化；--run前景執行。沒有長駐worker時前景命令結束不代表工作會在背景繼續。MCP預設附同套件worker；--no-worker退出。使用與worker run相同Executor／OS lock，同workspace只能一個executor。若鎖被持有，server仍服務但worker_available:false、附next action；本process不重試取得鎖，原owner退出後重啟MCP。附worker使用獨立Store instance連同一workspace，MCP reader不排在worker write wait後；startup ready handoff最多2秒，未commit仍starting不可用。只有附帶worker持鎖且運作時MCP envelope才回true，停止server後不承諾背景續跑。

B08 local safety commands: `auth import --file PATH [--provider LABEL] [--account LABEL]`;
`permit create --project ID --kind access|share --scope-file PATH --provider LABEL
--account LABEL --issuer LABEL --expires-at TIMESTAMP --max-uses N --request-key KEY
[--exact-digest SHA256]`; `permit revoke ID --request-key KEY`; `permit list --project ID`.
Expiry is required, timezone-aware, and at most 30 days after issuance; uses are 1–1000.
Exact access targets, exact packets or an immutable displayed evidence snapshot bind
project/provider/account. [Safety API](../dev/SAFETY_API.md) defines scope formats and
backend obligations. These are human-only commands, never MCP self-approval tools.

`metamuse skill export` copies the packaged Skill into the directory named by `--dest`.
It refuses to overwrite an existing file unless `--force` is set. It does not choose an
agent or host config directory. It is not an MCP tool.

Grant-requiring Muse open/send/read (refresh and open reconciliation) accepts an omitted or
unknown grant and returns E_SCOPE_REQUIRED with null data and a nullable `approval` preview.
It creates no grant, discussion, exchange or job. Preview includes operation/permit_operation,
kind, project, configured provider/account, exact action digest, destination, topic/question,
and for shares packet ID/digest/UTF-8 bytes plus the exact `packet preview` command.
`request` is a non-secret copy to save at `request_file`; `next_action` is the exact human
CLI command. Each text field is redacted separately before JSON encoding, using the same
strings in the summary and request. The digest still binds original bytes, so a redacted
or otherwise changed request cannot reuse that digest. Expired, revoked, exhausted,
mismatched or non-exact share grants retain E_SCOPE_DENIED and their actual message,
without a preview; repeat the call without a grant for a fresh review. Stale packets
return E_REFERENCE_INVALID "Packet is stale; prepare a new packet" in submit and preview.
Policy and authentication failures retain their actual errors without a preview.
For B12 remote media, the exact digest also binds the whole configured approval table:
canonical sorted extractor keys, normalized sorted hosts and opaque auth_ref or null.
The review identifies first-hop and later-hop entries and shows each cookie label/account.
Selection uses those bound entries; even an unrelated approval edit invalidates an
outstanding grant before consumption or egress and requires a fresh review.
B12 preparation computes one ASCII-only canonical URL: lower-case scheme and
IDNA2003/punycode host, non-default port only, UTF-8 percent-encoded non-ASCII path/query.
Decode unreserved escapes, upper-case remaining escapes, and pre-encode ASCII outside
the pinned yt-dlp/curl_cffi safe-set intersection. Only m.facebook.com maps to www;
host approval checks www. Refuse IDNA deviation characters ß/ς and mapped trailing dots.
The digest, preview, request echo and operator stderr bind that string. The extractor
receives exactly the displayed URL. The transport sends exactly each URL the checker
approved, without requoting or dot-segment removal (quote=False, PATH_AS_IS and an explicit
REQUEST_TARGET, preserving escape hex case too). Extractors
may derive further requests, e.g. FacebookReel watch URLs, added query parameters or
API endpoints; each is checked against bound approval entries before any dial. Wire
tests prove that the first page request bytes equal the display for the fixture
extractor and FacebookIE watch, video and story URLs. The job request and original_url
provenance retain the original input. Refuse fragments (even empty #), shared url_token
query keys, invalid escapes, dot segments (including escapes and parameters), duplicate
canonical query keys and structural redaction, with fixed E_INPUT_INVALID, no preview
or URL echo. At preview and before extraction, assert the pinned-library fixed point:
display == requote_uri(normalize_url(display)); drift fails closed. Signed extractor-hop
URLs retain existing redaction; credential-bearing page URLs are outside B12 inputs.
The preview's operator command uses the next unused digest-based approval key, so a
fresh review cannot replay an exhausted/revoked/expired grant. Repeating a previously
submitted approval command still replays its original permit and mutable use state.

`permit approve` recomputes the preview through the same preparation code and displays it
on stderr, including the effective absolute UTC expires_at, remaining duration and
max_uses before confirmation. These are the stored values (defaults: 24 hours and one
use); replay displays the original expiry. It requires typed `yes` at a terminal or equality with `--exact-digest` in a
non-TTY operator CLI. `--non-interactive` refuses this command even with an exact digest.
Provider/account cannot be supplied in model text or flags. Every approval has an expiry:
default 24 hours, or explicit timezone-aware `--expires-at` within 30 days. Exact action
and packet scope are stored in the existing permit; repeated approval with the same key
and default expiry replays the original permit without extending it. No MCP approval tool.

JSON envelope：contract_version、request_id、status(ok/accepted/partial/error)、data、error、warnings、next_cursor、approval（nullable）。exit 0代表本命令成功，可能僅queued；2輸入錯、3需要人／權限、4backend不相容、5部分或等待逾時、6需對帳、7儲存／內部錯、130中止。

## 9. MCP與Skill

十五個P0工具見contracts/mcp-tools.json：inspect、project_create、collect、job_get、job_list、job_control、search、packet_prepare、muse_open、muse_send、muse_read、muse_close_exchange、draft_save、draft_preview、export。P0不曝光publish_commit或raw gateway。

所有state handle顯式傳ID，不靠connection上次選過project。stdout只給SDK協定；log、下載器和warning導向stderr。資料分頁或受保護resource引用，不回整部影片。

MCP SDK／host／協定版本在B01選定並實測，不宣稱任何host必然支援。輸出schema是我們的結果格式，不是Muse受約束生成。tool annotations不能代替授權；muse_send和refresh不可假稱read-only。

Skill短主檔＋按需references，先查實際工具；只有Skill不代表backend已安裝或登入。
安裝已打包的 Skill 用人類 CLI `metamuse skill export --dest DIR [--force]`，不是第十六個 MCP 工具，也不會自己寫入 agent 或 host 設定目錄。

## 10. 起始限制

| 項目 | 工程預設 |
|---|---|
| 同workspace executor | 1 |
| 單job URL／item | 10／500 |
| 單media／單job bytes | 512 MiB／1 GiB |
| 文字匯入 | 20 MiB |
| Packet／question外送 | 48 KiB／8 KiB UTF-8；不是provider token限制 |
| Job wall clock／單read | 15分鐘／30秒 |
| 安全讀取重試 | 最多2次，尊重Retry-After；拒絕／驗證不套用 |
| Muse polling | 5秒退避到30秒，120秒未讀回就回等待警告，不重送 |
| 授權內自主追問 | 起始最多3個exchange，host決定，不是自動互聊 |
| 結果分頁 | 預設50，最多200 |

提高上限不擴大平台許可。實際bytes持續計量，不信任Content-Length或來源宣稱。

## 11. 錯誤與非功能需求

錯誤碼：E_INPUT_INVALID、E_REFERENCE_INVALID、E_SCOPE_REQUIRED、E_SCOPE_DENIED、E_AUTH_REQUIRED、E_DEPENDENCY_MISSING、E_UNSUPPORTED、E_PROTOCOL_MISMATCH、E_ACCESS_DENIED、E_CHECKPOINT_REQUIRED、E_RATE_LIMITED、E_SELECTOR_DRIFT、E_NETWORK_TIMEOUT、E_JOB_BUSY、E_IDEMPOTENCY_CONFLICT、E_EXTERNAL_STATE_UNKNOWN、E_STORAGE、E_LIMIT_REACHED、E_DATA_STALE、E_RESOURCE_UNAVAILABLE。

附retry_class（never/safe_read/reconcile/user_action）與next_action，不吞錯回空列表。

參考性能目標（尚未量測）：4vCPU／8GiB／SSD Linux，固定10,000筆樣本，至少30次，submit p95≤1秒、搜尋首50筆p95≤2秒；20MiB匯入core peak RSS≤512MiB，browser/codec另量測。未達列deviation，不捏造數據。

Linux／WSL2為首個執行目標；其他平台按實測列。無憑證離線路線零背景網路。升級須migration/backup restore，不刪庫過關。product tests初始not_run。

Round 4 storage mapping: SQLITE_BUSY/LOCKED past busy_timeout returns E_STORAGE / safe_read, “Muse security state is busy; retry”. READONLY identifies a read-only workspace and FULL a full disk, both user_action. Recording failures retain “Muse confidentiality could not be recorded” with the same fixed environment guidance, and stop before msg3. Integrity/schema failures alone advise verified restore with the later-expectations loss warning.

A committed muse_trust_propagating marker is always invalid and has a specific E_STORAGE message. Safe human repair, after stopping Muse connections: acquire exclusive maintenance.lock, BEGIN IMMEDIATE, retain the barrier, rerun the recursive owner/VM closure and the three set-wise flag updates across the whole workspace, DELETE the marker, then COMMIT. This retains every observation and expectation; no restore is needed. There is no automatic repair/reset command.

HTTP 200 session: status=unavailable或完全沒有identity key回resource；alias-only vmid回protocol；其他非assigned status（含missing/type/case drift）回protocol/never。non-object response action標session/hatch/auth path。

Confidential write（new confidential account/VM/observation row，或任何0→1 flag含SQL
propagation及inherited edge）commit後notify全部workspace live listeners，跨local labels
立即close sockets。non-direct freeze沒有confidential write不broadcast；binding/generation
change仍只notify自己的label。下一call先重查durable state；refusal在任何HTTPS/WSS前stop。
若仍current但socket被broadcast關閉，同一call完成fresh auth/session/freeze/mint/wake/handshake
後才開application frame，不回假的E_STORAGE。freeze回來後立即清invalidation並註冊listener，
在mint/wake/socket之前；同transport/key註冊deduplicate。註冊後application handoff
尚未贏trust boundary時，任何workspace confidential或own-label notification（含publish前及final durable check期間）都在同一call先fresh connect；
publish並assert_current後重查invalidation，每call最多3次connect。耗盡回固定retryable
E_NETWORK_TIMEOUT/safe_read「Muse connection failed」，action「Retry the connection.
No gateway request frame was sent.」；mutation經MuseNotSubmitted或chat.stream
failed_before_send回never-sent。mutation只consume一次。
每call（含existing loop）以muse_application_handoff在既有trust-domain/account boundary
重查OLD snapshot，再清除SQLite transaction及maintenance.lock，持trust locks跨
invalidation check及open_request/send；不更新msg3 provenance。trust write/notify先贏則
current context先fresh connect，changed/confidential/refused context在新HTTP/WSS及app frame前stop。
connect與application前recovery共用上述3次上限，authorization只consume一次；真正pre-send
refusal回MuseNotSubmitted/session.start或FAILED_BEFORE_SEND/chat.stream。handoff先贏則
較晚的notification屬in-flight，不能撤回已handoff bytes，不replay；response collect不持trust lock。
callback不取transport lock；callback的SendUncertain/protocol exception不改成storage failure。
in-flight socket關閉不replay：incomplete read回E_NETWORK_TIMEOUT；handoff後chat.stream保留
uncertain/partial evidence，session.start回MuseStateUnknown/reconcile；protocol failure仍never。
下一call只重連，不重送之前的mutation。
240s不是whole-connect deadline：auth/session及最多兩次mint在其前，claimed Muse trust
transaction仍各用30s SQLite busy timeout；currentness亦以BEGIN IMMEDIATE取writer。
持續held WAL writer會在第一個freeze/currentness wait逾時即stop，不進三次invalidation retry。
clock內trust wait可overrun，final check亦在handshake後。stable WAL、adapter遵守timeout時，
保守budget accounting每fresh connect為80s bootstrap HTTP+60s pre-budget trust+270s
（240s phase及最多30s check）=410s；三次1230s，加initial cached check最多30s為1260s。
這不是wall-clock上限：file/RLocks、fsync、schema work、DNS/guard與host scheduling另有等待。



Round 4 amends unreleased migration 005 again, with no 006: muse_trust_accounts now checks lower-case hexadecimal viewer/session hashes and normalized nonempty active VM ids, or NULL only with confidentiality set. The same constraints are verified on fresh v5, v4→v5 and confidential backup→restore. Noncanonical development v5 databases still fail schema verification.

Round 4 provenance design: source and observed_at are frozen once an observation or label VM row has confidential_required=1, including later direct evidence. A direct hint/RV may stamp a prior cr=0 observation during its transition to 1; a standard freeze never restamps a cr=1 row. Inherited propagation retains the association’s prior provenance, rather than claiming direct CVM evidence. Existing immutable guards enforce this metadata rule; no first-cause column is needed because nothing overwrites confidential provenance.


Session and selected-row keys whose compact form is `vmid` must all be valid and
normalize to one VM identity, independently of key order. Name, endpoint and the
persistent trust key use that identity. The exact server `vm_id` may be retained
on the wire only after that agreement check (case and surrounding whitespace
are the only permitted differences). A confidential hint records every usable
identity alias before any disagreement is refused, before mint, wake or dial.

Muse sanitization includes the codec's cookie, bearer and credential-pair
shapes, and treats Authorization and Proxy-Authorization scheme plus credential
as one value. Known secrets are matched in both raw text and up to two decoded
percent-encoding layers; their coordinate-mapped span union is merged before
one output join. A literal percent next to a hex nonce or UUID cannot hide it.

Muse credential keys and confidential markers share one Unicode component helper:
remove Cf and Default_Ignorable_Code_Point, apply NFKC, split non-alphanumerics,
letter/digit, lower/upper and acronym boundaries, then casefold. Credential keys
match any nonce/token/proof/secret/notary/recovery/psk/passwd component (also plural),
ordered noise…key, attempt…id, request…id, rv…key, wake…body or access…key pairs
(second component may be plural), exact extra keys, or the shared core classifier.
Pairs need not be adjacent. Association keys session_id/request_marker/x-request-id/
client_request_id, vm_id/discussion_id/job_id and documented usage token counts
(including the usage prefix, such as usage.prompt_tokens) are exempt by compact spelling.
Arbitrary credential prefixes such as secret.prompt_tokens are still sensitive. Known secret values
remain scrubbed under every key. Tokenizer, secretary, proofread, awake, handshake_ms
and wake_time contain no family component and remain visible; core policy stays sensitive.

Effective Muse trust and SQL inherit guards seed a recursive component at the
requested owner and VM. Every traversal uses indexed owner/VM edges, including
active selections, historical label VMs and label-independent observations.
All six AFTER propagation triggers and the bulk bind seed the same indexed
component recursion from the new remote owner/VM. Bulk bind materializes that
component once after inserting all observations, under the transaction barrier.
Small confidential writes and unrelated standard handoffs do not enumerate other
components. Only explicit human barrier repair computes the whole-workspace closure.
Raw-SQL inherit/retention/monotonic guards remain. Unreleased migration 005 is
amended in place again; no 006. Fresh v5, v4→v5 and backup/restore use this schema.

Muse trust failures classify SQLite primary codes (including extended codes):
IOERR/CANTOPEN/PERM/NOLFS/READONLY/FULL are environment/user_action;
BUSY/LOCKED/PROTOCOL/NOMEM/INTERRUPT are transient/safe_read;
CORRUPT/NOTADB/SCHEMA/CONSTRAINT and invalid stored types are damage/never.
Unclassified codes and Python exceptions return a fixed internal failure and
report action, without restore or permissions advice. Recording retains its
honest failure prefix and the underlying guidance. A committed barrier uses
closure-only human repair. Unreleased 005 enforces integer generation/flags,
text identities/hashes and nonempty text timestamps, also on upgrade and restore.

Hint matching walks every object/list to depth 32. A normalized component that
starts with cvm or contains confidential is a marker. Marker keys count only
with truthy values (excluding false/0/null/empty values and compact false/no/0/none).
String marker values count regardless of key, except opaque credential strings
and strings in lists directly under sensitive keys. Objects under sensitive keys
are still walked, including their keys and values under non-sensitive keys.
This retains hatch_cvm, hatchCvm, CVM_RUNNING, cvm1, confidential, negated
non-confidential/nonconfidential/not cvm/no-cvm, full-width, compatibility and
ignorable-separated forms. C V M, c_v_m, c-vm and hatchcvm/HATCHCVM no longer match.
It avoids cross-word Public VM/basic vm and opaque xQ7cVm2R false positives.
Evidence records the union of every enclosing identity-bearing object's usable
VM ids, including the session and selected row. If the innermost scope is the
session or no scope exists, persist owner evidence and all usable body ids.
Duplicate JSON pairs are retained for this scan, including every identity value;
record evidence first, then refuse any duplicate key at any depth. All Muse HTTPS
JSON bodies require strict UTF-8, reject leading U+FEFF, and pass the depth check
before parsing; UTF-16/32 and malformed UTF-8 record nothing. Alias-only vmid in
the session or selected row is protocol drift, before unavailable checks. Only
no identity key at all, or status=unavailable, remains resource unavailable.
Every observation commits before refusal even on owner mismatch or invalid selection.
False positives permanently refuse the connected standard tier, with no reset tool;
this intentionally errs toward confidentiality.

Owned Muse redaction scans key+separator occurrences once, inspecting a value
only for sensitive keys and resuming after it. Unquoted values stop at quotes;
JSON-text pairs embedded in plain text, including Unicode-escaped keys, are covered.
Quoted keys scan plain runs between escapes. Classification caches retain only keys
of at most 256 characters, so large authenticated keys cannot accumulate across connections. Authorization consumes a whole
run on failure and redacts scheme-less and scheme-plus-credential values. Every
codec-hidden value must also be hidden by text/body sanitization (seeded 5,000-case
compositions). Known raw and twice-decoded percent-coordinate spans are unioned,
merged, and rendered with one join; bodies are never truncated before redaction.
CPU doubling tests cover k:, k=>, a=1;, k:v|, ;p=1 and authorization:, raw and in
JSON string wrappers, 128 KiB–1 MiB, for owned scanning and public text/body paths.
Owned scanning passes. On this main, core safety.redact_text is still quadratic
for k=> and ordinary ampersand pairs. Strict xfails name B16a and fail on XPASS;
no safety.py change is included. The 1 MiB CPU ceiling is 12 s, allowing ample
margin above the passing sub-second measurements, while rejecting minute-long work.


B12 preparation checks metadata for all bound cookie references without reading secret
bytes: existence and provider/account must match, otherwise E_AUTH_REQUIRED without a
preview. Execution resolves and parses every bound secret before permit consumption,
including unused later-hop bindings. Parsed cookies are scoped to bound entries.

B12 receipt lookup precedes permit verification: an identical request including its grant
returns its original job after consumption or workspace-cap edits. Any changed payload
is E_IDEMPOTENCY_CONFLICT. Workspace caps stay outside target and job identity, but the four displayed caps
(safe_read_retries, read_timeout_seconds, media_max_bytes, text_max_bytes) enter the
action digest payload. Any edit before grant submission requires new approval.
Atomic acceptance stores exactly the reviewed values in the B12-owned step checkpoint
`remote_media_caps`. Execution recomputes the digest from that snapshot.

At the B12 boundary each workspace cap is min(reviewed snapshot, current value).
Lower caps apply to queued jobs; increases cannot exceed the reviewed snapshot.
Request limits remain digest-bound and are checked against current workspace limits.
Retries above the effective B12 cap stop before consumption or egress, even if the
core executor schedules another local attempt. That terminal local guard preserves
the original read error code/message, sets retry_class=never, and explains the accepted
retry cap in next_action.

B12 preparation enforces the shared 8192-byte UTF-8 URL bound and cheap HTTP(S)
syntax checks before redaction, display, digest, preview or job creation. One byte
over the bound returns the same outbound-policy error as the dial-time guard,
without a preview. The ASCII canonical URL must also fit this outbound bound.
URLInput still needs a core-owned schema/validation bound.

Remote configuration labels that are invalid or credential-shaped return fixed
E_INPUT_INVALID naming only the setting. Approval callback Exceptions preserve the
original refusal without a preview; logging contains only a fixed line and type.
Challenge normalization scans every response body in overlapping 32 KiB blocks,
checking the step cancellation/wall deadline once per block and retaining the existing
timeout error on expiry. Measured CPU costs per 512 MiB are documented in COLLECT.md.
Login forms keep the first duplicate attribute value. Only the first base with an
href counts; an unparsable first href uses the document URL, ignoring later bases.
Unparsable actions are skipped while later forms are checked.
Challenge token __rd_verify matches case-insensitively with or without a preceding
slash, after HTML entities, one percent-decoding, JavaScript \u005f/\x5f underscore
escapes and NFKC folding. Split JavaScript string literals are not detected.
Cookie domains require at least two labels and cannot normalize to IP literals;
multi-label public suffix detection needs a public-suffix list.
