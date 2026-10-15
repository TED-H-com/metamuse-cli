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
| TranscriptSegment | asset、revision、start/end毫秒、語言、原文、provided/auto/asr/manual、校對狀態；removed預設false。移除是revision≥2且text空字串的追加修正，同transaction標記派生stale；目前搜尋與解析不回已移除cue，明示舊文字版本仍可解析為stale。 |
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

B12 canonical provenance stores only reviewable display URLs on bound approved
hosts, preferring webpage_url, then extract URL, then reviewed input; canonical_basis
records the winner. Refused/signed candidates never persist. Subtitle language/method
and upstream identities are bounded patterns; text is bounded and redacted.
A 2xx subtitle/media HTML or BOM/whitespace-sniffed HTML login form stops with
E_AUTH_REQUIRED before later dials or resource rows; page classification stays with
the extractor. Every body block checks the cheap deadline; durable scan guards are
paced at 250 ms plus first/last. Changed subtitle bytes retire omitted cue indexes
atomically, including changed zero-valid-cue replacements. Failed fetches and listing
omissions do not imply removal. See COLLECT.md for bounds and measured costs.
B13 classifies login URLs as E_AUTH_REQUIRED and checkpoint/challenge URLs as
E_CHECKPOINT_REQUIRED from observed main-frame navigations and the final URL,
before parsing any underlying post container. All browser barriers stop without
retry; no synthetic DOM marker is needed for an encrypted redirect barrier.

Resume checkpoints persist cumulative expansion, byte and accepted-item consumption.
Clicks are charged before execution; observations persist consumption before parsing.
A fresh proxy receives only remaining bytes. Accepted reliable identities are stored
as bounded digests so unchanged re-observation does not refill or consume the item
budget again; edits remain revisions of the same accepted identity. Fixture byte
accounting uses cumulative UTF-8 observation bytes; real sessions use the larger
of that accounting and relayed wire bytes. Wall-clock fencing remains core-owned.

After a committed batch, resumed weak observations without native IDs are never re-imported. Neither text
nor DOM position is a deduplication key. Previously committed weak items remain; new
weak observations may be omitted, with weak_identity_resume and partial coverage.
Reliable identities still support edits and new observations.

After an expansion, the production browser waits within the read deadline for a
new relevant comment/reply node or changed identified content, or a failure response correlated to the trusted expansion click. An unchanged loaded document or disappearing button is
not progress evidence. Deadline expiry records expansion_progress_timeout with
partial coverage and comments_empty=false. Expansion response failures retain observed
data with expansion_failed/partial; only document failures retain auth/access codes.

B13 accumulates reliable observations across expansions by platform/scope/native ID
and preserves changed text as ordered revisions. Pagination/virtualization cannot
discard a previously seen reliable comment. The union consumes the item budget;
retention has at most max_items plus one lookahead identity, with expansion-bounded
revisions. Weak records are retained only from the initial snapshot; later weak
observations add weak_identity_expansion/partial instead of text or position merging.

Genuine empty comments require an identified post, a present comments section,
explicit comments-exhausted evidence, a rendered count of zero, no observed comments
across snapshots, no controls and no progress uncertainty. For fixtures that evidence
is data-comments-exhausted=true (or JSON comments_exhausted=true); LIVE mappings
remain unverified. Absence of controls alone never proves exhaustion. Contradictory
counts add rendered_count_conflict; positive counts without sufficient comments or
controls add missing_comments_controls; absent exhaustion evidence adds
comments_scope_unverified. Every gap yields partial coverage and incomplete post
evidence, never scope_satisfied. Counts whose semantics are unknown are not proof.

A reply without a parent identifier adds parent_comment independently of the
core unresolved_parent field. New reply evidence is incomplete and the job is
partial; null parent is never interpreted as a proven complete parent tree.

DOM text is decoded once by HTMLParser, preserves edge whitespace and explicit
empty bodies, and preserves br as newline. Nested block starts/ends introduce
newline boundaries without adding a second boundary beside an existing newline.
JSON text is retained as given. No normalization replaces the preserved text;
revision hashes use canonical JSON {text: preserved_text}, with no Unicode or
whitespace normalization. Reliable identity uses native tokens, never text hashes.

B13 post share inputs accept primary-host /share/p/<1..64 ASCII alphanumerics>/ with
no query/fragment and normalize to https://www.facebook.com/share/p/<code>/. B08's
access digest binds the normalized share URL/resources; page type remains
post_permalink. The browser resolves through the proxy/barrier boundary to one
supported post; other final kinds, including group posts, return E_UNSUPPORTED
with remote-media (B12) guidance for Reels/videos. Step data/observed scope and
Source.use_basis contain requested_locator/resolved_permalink; source original_url
is the requested input and canonical_url is the resolved query-free post URL.
Resume resolves the share again and requires the saved actor/token; a different
identity is E_DATA_STALE, preserves committed data and imports no new target.

B13 retains ordered text transitions by reliable native identity, including A → B → A.
Only consecutive identical observations are discarded. Revision evidence carries the
contributing HTML snapshot hash and verified requested/resolved locator, permit and
account; a new Source identifies its first contributing snapshot.


A present but unparseable Facebook comment count adds comment_count_unparsed to post and job gaps; affirmative exhaustion never converts that uncertainty into scope_satisfied.


Facebook counts and remaining hints accept only nonnegative integers or 1–18 ASCII decimal digits. Other present forms add fixed *_count_unparsed gaps; Unicode digits and oversized decimals never reach an unchecked int conversion.


Facebook parent identifiers are stripped and validated against [A-Za-z0-9._:-]{1,128} in both DOM and JSON. Invalid/empty reply parents are unknown and add parent_comment to the item and job, independently of unresolved_parent.


Facebook comment identifiers use the same stripped nonempty ASCII token validation as parents. An invalid token is absent/weak; distinct weak records are never merged by text or position.


Facebook weak-identity resume suppression begins only after an accepted batch has committed. A usage-only checkpoint from a login wall does not suppress a first import of weak comments; cumulative byte/expansion usage still survives.


Facebook blocks ws:/wss: at Chromium’s network layer with Network.emulateNetworkConditionsByRule offline rules matching both schemes and all ports. setBlockedURLs alone is insufficient. Page WebSocket routing is supplemental. Workers are verified paused before their first script and destroyed without any resume command. Each observed attempt has the fixed websocket_blocked reason; any native socket creation or observed handshake response fails E_PROTOCOL_MISMATCH before import, including events drained at close.


Facebook navigation failures use only the main-document request/redirect chain and its uncompleted CONNECT authorities. Refusals of WebSockets, workers, subresources or already-responded document authorities do not choose that error. Resolver capacity/timeouts use E_NETWORK_TIMEOUT with DNS-oriented advice, never a permit/policy denial.


Only a fetch/XHR synchronously initiated by the trusted expansion click can classify an expansion response failure. Background and proxy-aborted responses are ignored. Such a failure imports observed data with expansion_failed and partial coverage; document auth/access/barrier outcomes remain distinct. Asynchronous endpoints without click provenance are treated conservatively as progress uncertainty, not guessed failure attribution.


Facebook navigation barriers, page shapes and identity adoption apply only to the main frame. All network subframe document requests are aborted with blocked_subframe without setting navigation_error. A share identity is adopted only from the main frame.


Facebook sandbox state is observed from Browser.getBrowserCommandLine and, on Linux, renderer /proc/<pid>/status Seccomp: 2. No --no-sandbox is allowed for enabled. Failed observation is unverified and refused without the operator exception. The production executable environment override is removed; tests inject an executable through a seam. Capability launch checks verify this running state, not exit status alone.


Facebook cookie path/expiry/boolean/sameSite/name/value shapes are validated before launch; expiry is -1 or finite Unix seconds through 253402300799, and path starts with /. Invalid state returns fixed E_AUTH_REQUIRED and needs_auth. Context/state failures suggest auth repair. Only an actual required sandbox launch failure offers the unsandboxed exception.


After a Facebook post identity is bound, a main-frame navigation to an unknown non-post shape returns E_CHECKPOINT_REQUIRED with the distinct message “Facebook navigation left the approved post” and browser-session review advice. Unsupported operator inputs retain E_UNSUPPORTED. Share destinations before any post is bound retain the separately documented resolution-type refusal.


Facebook rendered text includes inline image alt text (including emoji) and follows the structured capture/rendering contract below. JSON text remains unchanged.

Structural JSON scripts require both data-fb-post-payload and application/json MIME
type. Every other script is excluded from body text, even if it carries that marker.

Embedded JSON integer tokens over 128 characters stay lexical for field validation.
Oversized counts add their fixed *_count_unparsed gap and retain the observed post;
they do not turn a valid JSON document into a malformed-payload error.


Facebook captures the main HTML document through CDP in an isolated world, never
through main-world evaluation. An explicit-stack read-only walker uses pristine
DOM getters, `checkVisibility()` and computed styles. It creates no elements,
clones no document and writes no attributes, so capture cannot invoke custom-element
constructors, attribute reactions or mutation observers. CDP discovers closed shadow
roots and resolves their handles into that world. The same isolated world is scoped
to the current navigation/frame. Iframe documents are not post evidence.

The observation is a flat structured node list: kind, namespace, tag, ordinary
attributes, text, light-tree children, composed-tree children and a rendered boolean.
Expand controls also carry a CDP backend node identity. There is no reserved HTML
attribute channel or trusted-HTML flag; every page-authored `data-metamuse-*`
attribute is ordinary markup, regardless of case, namespace or creation method.
Chromium observations are never HTML-reparsed. Progress compares structured comment
identities/text and immediately classifies new barrier evidence during polling. CDP clicks the exact captured rendered control, including when
another hidden control has the same identifier. Serialized structured data supplies
the observation digest and byte accounting; it is not parsed as HTML.

| Content | Rendered evidence |
| --- | --- |
| Ordinary laid-out content | yes |
| Open, declarative and closed shadow content | yes |
| Light children assigned to a slot | yes |
| Opacity zero, off-screen, zero-size or clipped | yes; painted-but-not-seen limit |
| Off-screen `content-visibility:auto` descendants | yes, even without layout nodes |
| `aria-hidden=true` | yes; accessibility does not determine painting |
| `hidden` with a CSS display override | yes, by the cascade |
| `display:none`, or `hidden` without an override | no |
| `hidden=until-found` | no |
| `content-visibility:hidden` descendants | no |
| Closed details content other than its first summary | no |
| Scripting-on `noscript`, and template content | no |
| Unslotted light children of a shadow host | no |
| `visibility:hidden` or `collapse` | no; descendants may restore `visible` |
| Text, including through `display:contents` | its text box and inherited visible state; skipped auto content remains eligible |

Default `checkVisibility()` preserves auto-skipped content. Text-range boxes exclude
non-painting direct text in closed details; looking through `display:contents`
preserves text whose immediate parent has no box. Opacity, clipping, size and viewport
position do not filter evidence. Possible painted `aria-hidden` duplicates must be
measured in AT-039, not excluded by assumption. JSON text keeps its existing rules.

`FixtureTree` is a static test double accepting a balanced subset, not a general
HTML5 parser. Its HTML tag vocabulary is explicit (`HTML_TAGS`); foreign SVG and
MathML vocabularies exclude HTML breakout and integration-point rules. Fragments
use a body-fragment context. Documents retain explicit html/head/body context.
It models scripting-on HTML `noscript`, the serializer's exact 18 HTML-only void
tags, inline declaration order and `!important`, hidden/display overrides, content
visibility, details and inherited visibility. Unsupported parser modes, unbalanced
or implied end tags, nested form/a/button corrections, foster parenting and
implicit table sections refuse with the fixed capture failure. Rawtext/RCDATA/
script-data/PLAINTEXT elements other than inert script/style and HTML noscript are
refused: xmp, iframe, noembed, noframes, plaintext, textarea and title. Foreign HTML
names (including noscript/template/slot), dialog, popover and declarative shadow DOM
are refused. Executable scripts, event handlers, stylesheets, external stylesheet
links, refresh metadata and unsupported inline rendering declarations also refuse;
fixtures do not simulate execution, resources or the external CSS cascade.

The differential corpus includes committed HTML literals/files, runtime fixture
inputs recorded by the suite, reviewer-provided markup and a seeded depth-five
generator. Accepted inputs must agree with sandboxed Chromium on node kind, tag,
namespace, attributes, text, rendered flag, light-parent relation and composed
order. Normalisation removes document/fragment/doctype/comment records and empty
html/head/body scaffolding; it preserves evidence nodes and their parent/order.
Unsupported inputs are counted as refusals. The default Chromium audit has an
independent 90-second deadline; the job/browser budgets remain 300 seconds.
The fixture-only raw UTF-8 markup precheck is 8 MiB; every parity input is below it.

Capture permits at most 65,536 nodes and 8 MiB of UTF-8 text plus attribute names and
values, independently of the operator-selected `limits.max_bytes`. The existing
HTML/observation and proxy-byte budget remains binding; these additional structural
bounds also cover a tiny script that creates a large DOM. Flat records add overhead
beyond equivalent HTML, and their serialized bytes count against the same job budget. Bounds, unsupported non-HTML main MIME
types (XHTML/XML/SVG), and capture failures refuse with fixed Facebook-specific
`E_SELECTOR_DRIFT`, without page text or `E_STORAGE`. Inaccessible closed roots add
`closed_shadow_capture_unavailable` and force partial coverage. Subtree insertions or shadow-root attachments observed
across CDP discovery/capture commands add `capture_changed_during_observation`, so
a closed root created during handle discovery cannot be silently omitted. Incomplete captures
never claim `scope_satisfied`.

Stop markers (`data-fb-state`, `data-page-state`, `data-checkpoint`, login/checkpoint
form IDs, head/meta states) are inspected across the whole light DOM, unslotted
children, templates and shadow roots, regardless of rendering. Completeness evidence
such as `data-comments-exhausted` counts only when rendered. Text retains the existing
single decode, image-alt, newline and edge-whitespace contract.


Facebook browser routes validate the destination through the shared URL policy
(scheme, credentials, port, exact host allowlist and all resolved IPs) before
classifying login/checkpoint or off-post navigation. Denied destinations return
`E_SCOPE_DENIED` with the fixed message “Facebook browser destination is denied
by egress policy”. Production requires HTTPS. Chromium route DNS checks share
the proxy's bounded resolver slots/deadline; DNS capacity and timeout failures
retain `E_NETWORK_TIMEOUT`. The proxy still independently checks every dial/hop.


Facebook guard failures fail closed with fixed `E_PROTOCOL_MISMATCH`: a failed
close/cancel/abort is ignored only if the target or request is provably gone;
missing liveness evidence is failure. Worker attachments must report
`waitingForDebugger=true`, and `Target.getTargets` must confirm destruction. No
worker resume command exists. The redundant per-worker network-rule step is
removed: no untrusted worker source executes, including nested worker creation.
Dedicated workers need a fixed CDP `Runtime.evaluate("self.close()")` in their
paused realm because `Target.closeTarget` alone leaves them alive in this build.
Shared and service workers use `Target.closeTarget`. Success, refusal and lost
close replies all enter the same pending-target set: destruction is asynchronous.
Verification runs after callbacks return, before import, with a bounded five-second
wait, including dedicated workers already sent the fixed `self.close()`. The verifier
iterates a copy and discards targets only after proving disappearance. Attachments
delivered while checking are included in that same five-second deadline; new targets
never extend it. Unexpected verification exceptions become the fixed guard failure. A browser
disconnect is never disappearance evidence; a target still listed past the bound
fails closed. No worker resume or detach-to-run command is sent. Popups,
downloads, route aborts and session teardown use the same fail-closed rule.


Facebook requires Chromium `--block-new-web-contents`: renderer popup creation
returns null, including URL/blank `window.open`, target=_blank links/forms,
noopener, pristine iframe open and nested popup attempts. Before init scripts or
navigation, the same sandbox verification blank page tests native and fresh
iframe open; the proxy is bound but closed (not listening) and Chromium DNS is
disabled. A failed or inconclusive check returns fixed `E_DEPENDENCY_MISSING`
“Facebook browser popup creation control is unavailable”, with no operator opt-out.
CDP `Page.windowOpen` counts refused attempts as `popup_blocked`; refusal alone
permits success. Browser-level auto-attach pauses foreign page/iframe targets
before their first script, installs network event detection and closes them.
Native WebSocket creation or handshake responses in any attached target stop
with the existing fixed `E_PROTOCOL_MISMATCH` before import. Workers never resume.
The `WebSocketStream` init shim is defense in depth alongside Playwright's
ordinary WebSocket routing; it is not the popup or socket boundary.


A Facebook proxy byte cutoff immediately shuts down both client and upstream
sockets for all active tunnels and refuses subsequent dials/registrations.
Upstream candidates are registered before connect can block; teardown closes
them before waiting for the dial barrier, so pending connects are included. A
50 ms browser-loop watcher reacts to byte cutoff and latched socket/guard
failures, closing the affected page to interrupt in-flight
navigation, click and expansion operations, instead of waiting for the full
`read_timeout_seconds`. Only navigation/socket/wait interruptions caused by the
cutoff become `E_LIMIT_REACHED` with partial coverage. Existing fatal errors are
never rewritten merely because the byte latch is set. Error precedence is guard
failure or unverified target shutdown, denied destination (`E_SCOPE_DENIED`),
login/checkpoint/challenge/rate-limit barrier, off-post navigation (including share-link
`E_UNSUPPORTED` and `E_DATA_STALE`), then the byte budget. Every other CoreError also
outranks the cutoff. Only the named aborted-navigation, closed-socket and interrupted-wait
errors caused by the cutoff may be dropped. Error recording retains the strongest
stop, including at close after the final observation with no expansion. When HTML has been observed, barrier classification runs before the byte
budget decision. Fatal stops preserve their normal job state, error, records and
next action. Every operation checks guard state first, and close always verifies
pending target destruction before tearing down CDP, including after a cutoff.
Already pinned authorities are revalidated synchronously through the complete URL
policy using their pinned addresses, without resolving or dialing, even after a
cutoff. This permits completed observations to reach barrier classification before
the budget decision while preserving destination checks. For unpinned URLs, all
DNS-free syntax, scheme, credential, hostname/IP-literal and port checks run before
the latch; port validation therefore precedes DNS. Allowlisted navigation paths are
then classified, so an allowlisted login URL retains its login barrier. DNS-free
proxy refusals enter the same audit counters exactly once as later policy refusals.
Only the remaining DNS-requiring allow path is interruptible by the latch. Its wait remains bounded. The watcher uses the pinned Playwright
implementation loop, as does the existing async WebSocket close adapter. Tests
bound hangs at an independent 90-second subprocess deadline, with 300-second read
and job budgets.


Route validation pins its validated address set by authority for this bounded
Facebook session. Every later URL still passes the shared scheme/credential/port/
host/IP policy, using that pinned set through the proxy dial and peer check. The
proxy does not resolve a different address between the browser check and its
validated dial. Redirect destinations on new authorities get independent checks.


A disconnected browser transport is not proof that a route, download or browser
process is gone. A failed route action is ignorable only with an observed request
failure or a closed page observed while the browser remains connected. Failed download cancellation and context/browser teardown therefore
fail closed even when CDP is disconnected; a page close exception may be ignored
only when the page's closed event proves that target has already disappeared.


The byte-limit latch also interrupts bounded route/proxy policy and DNS waits
within the existing 50 ms polling interval, without waiting for resolution or the
read deadline. Abandoned resolver tasks cannot dial, remain slot-bounded, and
release their slot when the underlying lookup returns. A sandboxed streaming
regression trips the byte budget while another URL is waiting on DNS.


Stop evidence is monotonic: both state attributes are examined independently,
and a later benign state cannot erase a barrier or form marker. When multiple
recognized stop states coexist, the parser uses the existing classification
order: rate limit, checkpoint/challenge, login wall, then access denial.
