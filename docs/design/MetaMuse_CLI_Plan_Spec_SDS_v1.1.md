# MetaMuse CLI — Plan、Spec、SDS 合併閱讀版

文件版1.1。由 numbered docs 01_PLAN、02_SPEC、03_SDS、06_SOURCES 產生；各分檔是 authoritative source。待實作設計，未驗收真實帳號。



---

# MetaMuse CLI — Development Plan

文件版 1.1｜2026-10-13｜重新提供的開發交接文件，不是產品發行。  
命名：MetaMuse CLI；repo／distribution 暫定 `metamuse-cli`；指令、Python package 與 Skill：`metamuse`。

## 1. 產品與成功定義

一套本地優先的社群資料與內容工具：收集／匯入 → 貼文、留言、影音和字幕關聯 → 有來源的資料包 → 主 Agent 與 Muse 討論 → 草稿／報告 → 預覽及匯出。後續才加入明確獲准的發布、回覆與成效。

不是五個 repo 的 launcher。共用資料、任務、設定、來源、素材和錯誤處理；使用者不用管理三套 queue 或在 CSV 之間拼接資料。

主 Agent 決定研究與文案。Muse 是對話協作者，保留自己的 harness 和服務邊界。MetaMuse 保存並執行有界功能，不隱藏另一個主 Agent。

## 2. 已定案的工程方向

一個 public repo、一個 Python package、一個版本、一個 workspace SQLite。CLI 和 MCP 直接呼叫相同 application functions；Skill 是中性操作指引。少量按責任分檔是正常維護，不是拆成多個產品。

吸收授權明確的功能程式並自行維護，不必永久兼容所有舊 repo。yt-dlp、Playwright、ffmpeg、MCP SDK 保持正常依賴，不重造下載器或密碼學。

第一版不做微服務、Redis、Kubernetes、外掛市場、多租戶 SaaS、向量庫或大型 Dashboard。先完整流程，再擴平台。

## 3. 上游採納

| 來源 | 採用 | 排除／限制 |
|---|---|---|
| 自有 castle_fb_crawl | 影音連結與 yt-dlp 的使用經驗，直接寫入共用 jobs／assets。 | 舊 links.txt 佇列、失敗後全清除及私人部署資料。 |
| tlyu0419/facebook_crawler | 有用的貼文解析／分頁概念和授權允許的程式。 | 不繼承「免登入永遠可用」或舊重試行為。 |
| boo283/Facebook_comment_crawler | 留言／回覆的中性功能需求。 | 明確授權前不複製或機械翻寫原碼。 |
| nikships/muse-cli | 最小 auth／session／send／history 及必要 framing，登記來源與授權。 | 不搬全 CLI UI／更新器／258 個 raw 路由，不修改 Muse harness。 |
| Hao0321/claude-skill-social-post | 草稿版本、客觀格式、批准與對帳、成效快照。 | 作者聲線、人設、公式、hook／CTA、最佳時間、爆款策略和私人治理系統。 |

這是採納計畫，不是宣稱程式已經移植。授權與平台使用許可不同；採用前鎖定 commit、路徑、license 及修改摘要。來源與先前查核界線見 `06_SOURCES.md`。

## 4. 範圍與發行門檻

P0 是本地與共用基礎；P0-LIVE 是整合 beta 必須另測的真實服務。P1 是操作能力，P2 才做排程／額外入口／UI。

- `0.1.0aN`：完整本地匯入、關聯、packet、草稿和匯出；未完成 LIVE 的功能如實標 experimental 或 blocked。
- `0.1.0bN`：上述基礎加真實 Muse 對話及追問、獲准影音來源、至少一種獲准 Facebook 貼文／留言目標，並完成故障恢復驗收。
- `0.2.x`：逐平台驗收的發文／回覆、素材處理和成效。

沒有可用的合法測試環境可以繼續 alpha，但不能用 fixture 宣告完整 beta。命名與版本是計畫，尚未建立或發布任何套件。

## 5. 里程碑

| 階段 | 交付 | 出口條件 |
|---|---|---|
| M0 | 來源授權、SDK／host 相容性、Muse 最小 spike、依賴鎖定。 | 外部能力和未解條件有測試計画，不以路由名作證據。 |
| M1 | package、models、store、jobs、init／doctor。 | 一個權威資料庫、單 worker、可恢復狀態。 |
| M2 | 匯入、素材、字幕、搜尋、packet、草稿、匯出。 | 完整離線縱向流程，不只有 help。 |
| M3 | Muse client、session／exchange／raw messages、對帳。 | 真實有界往返與追問；逾時不重送。 |
| M4 | 影音來源、一種 Facebook 頁型與留言。 | 人工核對內容、父層和缺漏；部分結果不冒稱全部。 |
| M5 | MCP／Skill 整合、故障、打包、notice。 | P0 及 P0-LIVE 驗收與能力表相符。 |
| M6 | 素材處理、留言清單、單平台發布與成效。 | 精確批准、平台回讀及不明副作用處置。 |

M0 Muse spike 可與 M1 交錯，避免最後才發現服務限制；不把 spike 當正式 runtime。工程任務與依賴在 `04_BACKLOG.md`。本計畫不承諾日曆工期。

## 6. Public repo 與私人資料

建立乾淨的新 public repo，不直接公開既有私人歷史。真實憑證、browser profile、下載、私人留言、Muse 對話、bot 路由、機器與帳號設定留在 repo 外。

README 清楚寫非官方，不宣稱官方 API、TOS 合規或免封號。程式名字、低流量、private repo 都不能代替平台許可。資料收集、向 Muse 分享及對外發布需要分開的使用範圍。

## 7. 工程流程

一個核心 owner 維護 models/store/jobs/public contract，避免多個 Agent 同時改同一欄位。其餘可依實際功能分派，但不是建立永久的多個服務。

每個 PR 附 FR／B／AT、重現命令、實際結果、migration 和來源 notice。CI 聚焦靜態／契約、unit／fixture E2E、wheel smoke、秘密及授權檢查；真實帳號測試手動獲准後執行。用 CODEOWNERS＋維護者 review，不複製上游作者的龐大治理規則。

## 8. 風險與停止條件

Muse 介面漂移先標不相容，不改密碼學或關閉驗證。未知送達查原對話，找不到也不代表沒送達。存取拒絕／驗證／平台限制暫停，不換帳號、代理或 Muse 繞過。授權不明的程式不直接進公開包。

產品價值不能完全依賴 Facebook 當天可否收集：無遠端 backend 時，本地匯入、素材、搜尋和草稿仍有完整用途。這不是降低整合 beta 驗收，而是保留可用的離線 alpha。

## 9. 第一條驗收故事

使用獲准活動影片／字幕和指定貼文留言，在同一 project 建立來源及時間碼關聯。選取 packet，主 Agent 向 Muse 提問並追問一次，再保存附來源回顧草稿與匯出 manifest。此流程不發布。

離線測試可用假 Muse；整合 beta 必須另附真實往返與來源證據，不能混用。

---

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

## 6. Muse 對話契約

只依賴最小session、send和history。主Agent提供問題和文案，MetaMuse不自動重規劃研究。Muse自然語言原文照存，不假定strict JSON或可註冊任意tools。

```text
prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed
```

一個discussion同時一個未結算exchange。新topic本身也會外送，open需要存取範圍。send須驗packet和分享grant，保存intent後只送一次。timeout先查原session；沒有看見回覆不等於沒有送達。

保留progress、多段回答與晚到訊息。關聯必須檢查session、baseline、可辨識user echo、role和時間，不能只靠最新一則assistant訊息。歧義保存為ambiguous，不直接配對。回覆存在不等於研究完成；由host結算answered/needs_more_data/abandoned。

abandoned時若外部提交仍不明，discussion應quarantined，不能立即派下一題繞過未解提交。close_exchange不是遠端cancel。side chat不是跨客戶保密隔離；本地path不是Muse可讀資源。分析prompt不是停用Muse既有寫入工具的權限鎖。

## 7. 分享與草稿

Packet固定版本；修正建立新packet。批准可以涵蓋已展示的project snapshot，允許host在其中自行選資料和有限追問；新增來源或帳號需新範圍。不需要每個讀取都打斷使用者，但每次實際外送內容要可回查。

Question也是分享資料。去姓名不代表匿名，URL和原文仍可能識別人物。來源內容和Muse回答都不能建立grant、執行shell、修改取得方式或擴大授權。

Draft save只保存host提供文字；更新必須expected_revision，不默默覆蓋。預設language=und、emoji=unspecified、plain_text、this_draft；使用者明示才改。沒有作者人設或策略。

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
metamuse muse open --project ID --topic TEXT --access-grant ID --request-key KEY
metamuse muse send --discussion ID --packet ID --question-file PATH --share-grant ID --request-key KEY
metamuse muse read --exchange ID [--refresh] [--cursor CURSOR]
metamuse muse close-exchange ID --outcome answered|needs_more_data|abandoned --request-key KEY
metamuse draft save --project ID --text-file PATH --platform PLATFORM --request-key KEY
metamuse draft preview ID --revision N
metamuse export --project ID --refs-file PATH --format FORMAT --request-key KEY
metamuse mcp
```

更新草稿加 --draft ID --expected-revision N。明示引用／附件／變體／偏好可用 --facts-file、--assets-file、--variant-file、--preferences-file（JSON）。人類本地auth import／permit create另提供；不是能讓MCP自己approved:true的工具。憑證匯入需操作者指定檔案，不讀任意主瀏覽器profile。

CLI add預設持久化；--run前景執行。沒有長駐worker時前景命令結束不代表工作會在背景繼續。MCP可附同套件worker，但同workspace只能一個executor。

B08 local safety commands: `auth import --file PATH [--provider LABEL] [--account LABEL]`;
`permit create --project ID --kind access|share --scope-file PATH --provider LABEL
--account LABEL --issuer LABEL --expires-at TIMESTAMP --max-uses N --request-key KEY
[--exact-digest SHA256]`; `permit revoke ID --request-key KEY`; `permit list --project ID`.
Expiry is required, timezone-aware, and at most 30 days after issuance; uses are 1–1000.
Exact access targets, exact packets or an immutable displayed evidence snapshot bind
project/provider/account. [Safety API](../dev/SAFETY_API.md) defines scope formats and
backend obligations. These are human-only commands, never MCP self-approval tools.

JSON envelope：contract_version、request_id、status(ok/accepted/partial/error)、data、error、warnings、next_cursor。exit 0代表本命令成功，可能僅queued；2輸入錯、3需要人／權限、4backend不相容、5部分或等待逾時、6需對帳、7儲存／內部錯、130中止。

## 9. MCP與Skill

十五個P0工具見contracts/mcp-tools.json：inspect、project_create、collect、job_get、job_list、job_control、search、packet_prepare、muse_open、muse_send、muse_read、muse_close_exchange、draft_save、draft_preview、export。P0不曝光publish_commit或raw gateway。

所有state handle顯式傳ID，不靠connection上次選過project。stdout只給SDK協定；log、下載器和warning導向stderr。資料分頁或受保護resource引用，不回整部影片。

MCP SDK／host／協定版本在B01選定並實測，不宣稱任何host必然支援。輸出schema是我們的結果格式，不是Muse受約束生成。tool annotations不能代替授權；muse_send和refresh不可假稱read-only。

Skill短主檔＋按需references，先查實際工具；只有Skill不代表backend已安裝或登入。

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

---

# MetaMuse CLI — Software Design Specification（SDS）

文件版1.1｜2026-10-13｜本文件描述實作設計，不表示已有runtime。

## 1. 單一應用

```text
CLI／MCP → app使用案例 → jobs單executor → collect/media/muse/content
                              ↓
                     store/models/safety
```

CLI和MCP不互相拼shell。Skill没有自己的資料庫或planner。普通函式先共用，不預建BasePlugin和微服務。

```text
metamuse-cli/
  pyproject.toml
  uv.lock
  src/metamuse/
    app.py       # 使用案例與scope
    models.py    # typed contracts／產生schema
    store.py     # transaction、migration、query
    jobs.py      # durable queue、ownership、恢復
    collect.py   # 匯入與獲准來源
    media.py     # 影音與字幕
    muse.py      # 最小client與對話
    content.py   # 草稿、檢查、匯出，後續發布
    safety.py    # URL、路徑、grant、secret refs
    cli.py
    mcp.py
    data/        # 必要descriptor，無runtime資料
  skills/metamuse/
  tests/fixtures/
  docs/
  LICENSE
  THIRD_PARTY_NOTICES.md
```

檔案真的大了再拆。store不import CLI/MCP；muse不調度整個社群工作。使用AppContext傳workspace、store、clock、logger和policy，不需要DI framework。

Python 3.12作首個參考環境；型別/schema、官方MCP SDK、platformdirs、SQLite、yt-dlp、Playwright與Muse所需標準庫按B01驗證lock。ffmpeg/browser binary由人類明確安裝；不提權、不隱藏下載。延遲import非必要功能，缺browser不阻擋離線。

## 2. Runtime與信任邊界

runtime置於OS使用者私有data root，不放repo。配置、data、cache依platformdirs對應OS；一個workspace不能由MCP request任意改根目錄。

```text
<private-root>/<workspace>/
  state.sqlite3
  objects/sha256/<prefix>/<hash>
  staging/<job-id>/
  exports/<export-id>/
  logs/
  backups/
  runtime/worker.lock
```

秘密由auth_ref指向OS secret store或明示私有檔。檔案fallback不是加密保管庫；POSIX目錄0700/檔案0600，Windows用相應ACL。Cookie不經工具參數或結果直接傳遞，WSS/query/error中的token去除。

同UID且有任意shell的Agent可以改本地資料，不能假裝Skill或permit能抵禦它。此版是本地防誤用和範圍控制，不是多租戶／敵意host sandbox。需要強隔離另用OS帳號或容器。

來源、字幕、留言與Muse回答皆不可信資料；不能mint grant、執行shell、修改權限或改provenance。

## 3. Application API

inspect_capabilities、create_project、submit_collect、get_job、list_jobs、control_job、search_evidence、prepare_packet、open_muse_discussion、submit_muse_exchange、read_muse_exchange、close_muse_exchange、save_draft、preview_draft、export_objects。

handlers只做parse/render，domain結果不print。所有mutations有request_key和完整payload digest；同key同digest回原物件，異digest拒絕。canonical JSON為UTF-8、sorted keys、無多餘空白、拒NaN/Infinity，原文字串不正規化。發布digest還包含目的地、版本、附件hash，不能只hash正文。

## 4. SQLite與資料模型

每workspace一個權威SQLite，JSONL/CSV只是進出格式。foreign_keys=ON、local WAL、synchronous=FULL、有界busy_timeout；這不是硬體級絕對耐久性保證。不得多機直接共享live WAL DB。所有連線設定一致，外部等待不在transaction內。

| Table | 主鍵／內容／約束 |
|---|---|
| projects | id、name、revision、created_at。 |
| jobs | id、project、operation、request_key、payload_digest、state、coverage、limits、worker_epoch；UNIQUE(operation,request_key)。 |
| job_steps | id、job、sequence、target、checkpoint、state、attempts；UNIQUE(job,sequence)。 |
| sources | project、platform/native_scope/native_id、original/canonical URL、collection_method、upstream method／assurance、use_basis。 |
| items | project/source、kind、native ID、parent、unresolved parent、identity confidence。 |
| item_revisions | (item,revision) PK、原文/hash、raw/parsed/source time、fetched_at、complete、missing、evidence。 |
| assets | sha256 UNIQUE、mime、actual bytes、duration、object path、state。 |
| item_assets | item revision→asset、relation；保留各來源。 |
| source_assets | source、project、asset、relation。直接匯入的媒體沒有貼文時仍有專案範圍的來源關係。 |
| transcript_segments | project、可空 asset、可空 item/source、cue_index、overlap、start_ms/end_ms、text、language、method、review；end≥start≥0。至少要有 asset、item 或 source 之一。相同 bytes 的字幕不跨 project 可見。 |
| packets | project、exact selection、rendered_text、redactions、coverage、digest、created。 |
| permits | kind/scope、issuer、issued/expiry、max_uses/used、revoked、exact digest（需要時）。 |
| discussions | project/provider/auth_ref、remote_session nullable、open attempt state、last_seq、state。 |
| exchanges | discussion/job/request_key、question、packet、digest、permit、baseline、state、outcome；每discussion至多一個open。 |
| messages | remote session/message id/seq、role、raw status/text、association、observed；可靠ID按session去重。 |
| drafts | project、current_revision。 |
| draft_revisions | (draft,revision) PK、全文、platform、variant、facts、assets、偏好、digest。 |
| exports | project、selection、format、manifest/artifact、state。 |
| events | event/object/job/project、類型、去秘密detail與time；不是存所有秘密的log。 |
| worker_state | singleton owner nonce/epoch/heartbeat；不取代OS鎖。 |

P1才加actions/action_attempts、metric_snapshots，不預建campaign/voice/formula策略庫。

必須有FK及project一致性：跨project item/source/parent/packet引用拒絕。asset bytes可共用，但可見範圍依item_assets與source_assets，不因hash相同洩露別project。字幕的 project_id 是衍生文字的擁有者，可沒有 asset；共用 bytes 不共用字幕。transcript (id, revision) 只追加，不 UPDATE，也不能以 REPLACE、INSERT OR REPLACE 或 ON CONFLICT DO UPDATE 覆蓋既有主鍵。source_assets 整列不可更新，既有主鍵不可 REPLACE。兩表都沒有次要 UNIQUE；非唯一索引不是身分。單則字幕文字超過 1,000,000 字元是該列錯誤。

native IDs為opaque字串，不自行解碼。可靠ID用(platform,scope,id)識別；無ID保持弱識別，不以文字hash假裝同留言。parent不得自我／循環。revision追加，不原地抹掉舊來源；修正同transaction標記派生stale。

## 5. 工作提交与executor

先驗輸入／scope→transaction查request_key→建立job/steps/event→commit→回accepted/job_id/worker_available。收到job並不等於開始。

worker run、MCP附帶worker、CLI --run都搶相同OS lock。同workspace只有一個owner；其他入口只排隊。舊heartbeat過期但OS lock仍被持有時不能直接接管；由操作者確認終止owned process及退出後才開新worker。不得殺不明browser程序。

取得鎖後遞增epoch，短transaction claim queued step；外部動作前再驗epoch、job state、permit和target。epoch防舊callback寫回，但不能讓遠端服務獲得它不支援的fencing或exactly-once。

網路下載／模型等待不持有長transaction。每批結果commit checkpoint。純讀取可按規則重試；存取拒絕／challenge不當timeout重試。

### 重啟恢復

| 原狀態 | 處理 |
|---|---|
| queued | 正常claim。 |
| running，本地／安全讀取，無外部intent | 從可驗checkpoint續跑，無cursor則明示重新讀本次範圍。 |
| media staging | 僅backend可靠支援時resume，否則重建該staging；ready素材不刪。 |
| Muse create submitting | open_uncertain，可靠對帳或awaiting_user；不自動再建session。 |
| Muse send submitting | send_uncertain，查原session；未證明沒送達不重送。 |
| publish submitting | needs_reconcile，不重點送出。 |
| awaiting_user | 等明確條件／有效grant，不背景撞平台。 |

取消持久化後在安全邊界停止自己的資源；local cancelled與remote unknown分開。MCP呼叫等待被中止不代表原job消失。UI／CLI關閉後沒有worker就不能宣稱背景繼續。

## 6. 素材落盤一致性

寫入staging→連續量bytes→驗MIME/hash，MP4/QuickTime先做box邊界預檢→atomic rename到object store。只有ffprobe回報可用的audio或video串流，transaction才標ready；沒有ffprobe時，結構上說得通的檔留在unavailable。簽名或box walk本身不是ready。crash留下的staging只刪collect寫過、且job與operation都對得上的ownership marker目錄。不能在檔案不存在時回ready。

下載標題只作metadata，不能決定輸出目錄。asset路徑由內部ID/hash決定。失敗單項保留，不能整份queue清空。相同hash共用bytes，不消除不同来源。

字幕保存provided/auto/asr/manual、language、時間碼與校對狀態。沒有字幕不是啟動雲端ASR的批准。P1剪輯／轉檔只接核准本地asset和固定操作，不接受任意ffmpeg命令。

## 7. 收集和安全取得

共用URL、scope、job、source/item/asset及錯誤處理，只讓資料取得函式不同。依賴不可自行切帳號或繞過拒絕。

URL解析scheme/hostname，不靠字串包含facebook.com選Cookie。檢查redirect、DNS/IP與實際出站請求，包括browser subresources和媒體CDN；seed驗證不是完整SSRF防護。禁止loopback/private/link-local/metadata網段，測試localhost使用獨立fixture mode、假憑證、無真實授權。

無法攔截依賴網路時要用程序egress限制和已測host集合；做不到就不公開任意URL能力。不假稱允許網域就是平台許可。

yt-dlp固定API/argv、shell=False、停用繼承config/plugins，不提供任意--exec。ffmpeg固定argv、本地輸入、必要protocol whitelist、CPU/bytes界線。路徑檢查realpath、symlink、import root與實際大小，不接受任意資料根。P0不加ZIP匯入；export防路徑穿越及CSV公式注入，JSONL原文保真。

Browser context一個控制者，原生登入／2FA由人類處理。人接手後暫停，恢復需重看帳號/URL/target，不沿用舊element。語義locator只是實作選擇，不是全語系保證。只驗收明確頁型；其他回selector drift或unsupported，不亂點。

## 8. MuseClient

只採上游授權允許的必要auth/session/send/history與descriptors，依賴標準Noise／TLS庫。來源commit、原path與license記notice，不移植raw escape hatch。不是自己的模型API，也不替換Muse harness。

同Gateway一個receive consumer；以stream ID管理response，限制buffer／frame／body大小。P0 serial requests，不做多consumer subscriptions。完整reassembly未支援的frame回不相容，不能把半包當成功。不得關閉TLS驗證或自造握手來「相容」。token可能出現在WSS query，diagnostic不得外洩。

### Session建立

先建本地discussion＋open intent，再遠端create；回remote id才完成mapping。timeout時列open_uncertain。list對帳必須可靠識別，同標題不夠；找不到也不證明沒建。需要人選擇已有session時只顯示核准範圍，不掃其他私人對話猜。

### 送訊息

驗packet／grant／discussion→讀baseline→持久化exchange與payload→保留grant用量→標submitting→單次send→sent/send_uncertain→history讀取→候選關聯→reply_observed→host結算。

只有可靠知道尚未送出任何bytes才failed_before_send；其他不明不退還grant次數再重試。問題本身也屬分享內容。

消息關聯用session、baseline、user echo／request marker、role、時間與已校準status。非空status不是研究完成。外部手動訊息、晚到回答或跨session內容不自動歸給本輪。保留raw自然語言、多段和cursor，無JSON也不是失敗。

同discussion一個open exchange；host選answered/needs_more_data/abandoned。未知提交被abandon後discussion隔離，不藉close繼續派工。遠端cancel未驗證就not_supported/unknown。晚到訊息保留，不能配到下一題。

模型拒絕不以改來源、假身份或重寫同目的prompt迴避。兩個Agent同讀一份資料的共識不是獨立證據。分析prompt不會收回Muse原生工具權限，操作者需在服務本身設定。

## 9. Grant與packet

Grant由可信人類本地流程建立：kind、project/provider/account、可用snapshot或packet digests、expiry、max_uses、issuer。MCP無自我批准工具。對已展示snapshot可預先授權有界討論，不必每句打斷；新增來源／帳號不繼承。

packet從明確field selection生成，保留來源方法與限制；不帶headers、Cookie、token、localpath或不必要個資。自動redaction只是防呆，不能保證所有私密文字已識別。

送出前重驗source可分享、revision、grant有效性、question內容範圍和bytes。無法自動證明文本合法就由操作者／host負責明示輸入，不給假的合規標章。隱去姓名仍可能由URL或原文識別，必要時只傳聚合／摘錄或不分享。

## 10. 草稿、發布與快照

Draft是host提供的文字。revision避免覆蓋；共同正文和platform variant有關聯。沒有策略LLM在save背後。檢查字元／bytes算法要說明，平台限制記來源與驗證日期，未知就warning，不硬編不可靠數字。

Export先staging再manifest/hash/ready。引用不存在、跨project或已刪除不成功；引用存在仍需host確認語義支持。

P1 Action綁kind/platform/account/destination/parent、draft revision、assets、digest、grant和期限。變任何一項都需新批准。提交前驗真實帳號/target並寫attempt(submitting)，然後只送一次。

平台receipt或回讀才sent_verified。按過按鈕不算成功，timeout進needs_reconcile；換attempt ID不能繞防重發。沒看到貼文不是沒發出的充分證據。跨平台部分成功分別記，不預設刪成功貼文rollback。

Metrics保留原值、定義、時間、surface與null；外部參考與自有數據分开。保存數據不觸發人設或創作策略學習。

## 11. 介面與schema

models.py產生checked-in schemas，CLI/MCP/tool樣本同PR更新，不能維護三份不同truth。官方SDK處理wire/版本；B01鎖定實際host支援，工具是application契約。

MCP stdout只協定。structuredContent驗證本工具資料，不約束Muse。資源URI只解本產品既有白名單物件，不將URI拼成任意path或HTTP fetch。外部發送／refresh不可錯標read-only。

預設50筆、最多200，提供cursor；每個query固定snapshot／排序，避免翻頁時新增資料造成漏重。不整包回影片或私人資料庫。

Skill名稱metamuse、短frontmatter/主檔、references按需。Skill能否使用以實際安裝backend為準，allowed-tools不是sandbox。

## 12. 留存與備份

events只記必要去秘密狀態，完整來源和對話留私人store。診斷包白名單生成並預覽，不隱含匯出signed URLs或headers。

刪除先預覽影響→批准→刪內容／索引、撤銷未用grants、標記派生stale；別的source仍引用同asset時保留bytes。audit不保留被刪原文。SQLite WAL與備份可能仍含資料，說明保留與compaction，不保證鑑識抹除或遠端Muse清除。

備份用SQLite一致backup加ready object manifest；在新workspace做FK/hash/schema檢查。migration維護鎖＋升級前備份；不能只複製live DB主檔或刪庫假成功。舊版程式遇新schema拒絕寫入。

## 13. 驗收與決策

unit測契約、ID、parent、scope、limits；integration測DB/檔案/鎖/crash；fixture測自製頁與mock Muse；LIVE需明確授權，四類不混稱。

ADR：單package、單機SQLite、主Agent在外、最小Muse client、初版單worker、誠實coverage、原文與分析分開、外部副作用不盲重試、public code/private runtime、中性工具不帶作者策略。

先models/store/jobs與離線縱切片；Muse spike早做但不誤稱完成。任何外部能力附版本、時間、範圍和證據。不得以 --help、route名、schema通過或mock宣稱真實帳號驗收。

### B02–B04 core implementation clarifications

Core API and exact column inventory: [docs/dev/CORE_API.md](../dev/CORE_API.md).
The initial schema adds `request_receipts` for synchronous mutation idempotency;
`metamuse.toml` is the single workspace config and `runtime/maintenance.lock` protects
schema transitions. Job `started_at` persists the first-claim wall-clock boundary;
step `uncertain_state` persists the recovery classification independently of registry
availability. These are additions to the tables above, not separate stores/queues.

### B08 safety implementation clarifications

[Safety API](../dev/SAFETY_API.md) defines the URL request/redirect/subresource hook,
separately named localhost fixture checker, private-file secret fallback and typed
access/share scope formats. There is no OS-keyring implementation in this build.
Access scopes bind exact action digests; share scopes bind stored packet digests or
an immutable displayed set of original evidence refs. Operator/host question semantics
remain a stated responsibility, not an automatic legality/privacy proof.

The standard external verifier reserves use inside `before_external_action`'s
transaction alongside submission intent. Durable identifier/hash-only reservation events
use the existing `events` table; do not prune them while recoverable/uncertain work needs
them. Uncertain intent/action checks block fresh request keys and permits. No schema or
migration is added. Per-request policy alone cannot constrain an unguarded dependency's
DNS, proxy or subresources: un-interceptable backends require process egress controls
and a tested host set before exposing arbitrary URL acquisition.

Job-list cursors fix creation-order membership using a row high-water mark; statuses
are read live and physical job deletion/reordering is not exposed. MCP job_list requires
project_id; CLI jobs list may show the workspace with optional --project/--cursor/--limit.
Operation registration assigns one side-effect class to all steps of that operation;
feature owners must keep external submission separate from independently retryable
read/reconciliation work. Migration SQL remains the authoritative database definition.

Core review amendment: an entered external-submit step never requeues, even if its
submitting boundary was not persisted; crashes/cancellation retain delivery uncertainty.
Definite reconciliation uses the fenced Executor.settle_uncertain API described in
CORE_API, with separate safe-read history jobs. Offline policy applies before external
handlers/actions, retry limits come from workspace configuration, and UTF-8 byte limits
are checked at model/app/store boundaries. Unknown operation handlers leave work queued.
Schema 001 guard fixes remain version 1 before release; transcript scope and immutability
are deferred to B05/B06 migration 002, without treating shared bytes as derived-data permission.

### B07/B11 content implementation clarifications

Content requests, outputs and recovery rules: [docs/dev/CONTENT_API.md](../dev/CONTENT_API.md).
Search uses exact case-sensitive substring matching without Traditional/Simplified or
Unicode conversion. Stateless HMAC cursors carry project/query bindings, row watermarks,
membership fingerprints and last sort keys; latest-as-of queries exclude later inserts
and revisions. Deletion, replacement or loss of visibility fails rather than skipping a
reference. A single workspace signing key uses existing SQLite receipts and survives
backup/restore; no per-query files, second store or migration is added. Formal key lifecycle
support remains a core request. Long snippets preserve the entire matched query.

Packet digests are SHA-256 of exact redacted rendered UTF-8 bytes, including source labels
and limitations; the 48-KiB limit applies after rendering/redaction. Draft digests cover
canonical full text/platform/variant/refs/assets/preferences. Explicit preference scope is
this_draft only; stored preferences are typed, with language/emoji bounded to 35/32 code
points. Core TransactionWriter draft CAS and metamuse_now() timestamps apply. Receipts
detect changed payloads before ref checks; replay validates access and recomputes staleness
for the selected revision. Transitive resolution is memoized with node/edge/depth/byte
budgets, and exports flatten each dependency once. Preview reports code points, UTF-16
units and UTF-8 bytes, not graphemes. It checks transitive facts and separates date digits
from number candidates; platform limits remain unknown until a source and date are recorded.

Private text/Markdown/JSONL exports retain original text. CSV prefixes an apostrophe when
a cell starts tab/CR/LF or, after leading whitespace/quotes, begins =, +, -, @ or |.
Private manifests use redactions=null; Markdown includes a private unredacted notice.
Bundle exports contain redacted records and asset metadata only, not opaque media bytes.
ASCII-scoped redaction covers encoded URL parameters, JSON credentials, Authorization,
folded cookies and plausible paths while preserving CJK slash prose and JSON delimiters.
Automatic redaction is a guard, not a guarantee. A full media archive remains pending.
Manifest payload members have hashes/sizes; the manifest excludes its own hash and is
verified against exact stored bytes. Ready state follows fsync and rename. No export
publishes/uploads; handled failures clean owned artifacts and crashes may leave orphans.
Core backups exclude export directories: missing restored artifacts return
E_RESOURCE_UNAVAILABLE with recovery guidance, never a successful replay. Export archive
support and orphan cleanup remain pending. Legacy search-marker files are no longer read
or created; cleanup of files from older development builds remains an operator task.

Schema-1 transcript visibility requires a current item/asset link in the requested
project and no surviving link (including historical) in any other project. Sharing bytes
hides transcripts from both projects. Migration 002 must replace this fail-closed helper
with authoritative project_id and coordinate the core validator, nullable-asset anchors
and proactive transcript stale marking. B07/B11 computes transcript staleness at use;
its fixture evidence does not claim pending integration or any LIVE result.

---

# 來源與恢復說明

文件版1.1｜2026-10-13。本次重新生成文件下載包，使用本對話既定MetaMuse CLI決策、先前保存的單一工具v3及功能v2，沒有執行新的平台查核或真實帳號操作。

原先的ZIP在目前session不可取得；這是重新提供的交接版，不主張與先前ZIP逐位元相同。文件規格已保存的設計不等於產品程式已完成。

## 先前討論採用的一手來源（實作前重驗）

- Muse client與protocol：[repository](https://github.com/nikships/muse-cli)、[protocol](https://github.com/nikships/muse-cli/blob/main/docs/PROTOCOL.md)。最小session/send/history的使用基線，不是官方穩定API保證。
- Facebook貼文概念：[tlyu0419/facebook_crawler](https://github.com/tlyu0419/facebook_crawler)。先前對話已核對MIT；移植時鎖commit／license／原路徑。
- 留言需求：[boo283/Facebook_comment_crawler](https://github.com/boo283/Facebook_comment_crawler)。先前未見明確LICENSE，不推定可任意移植；未解授權不复制／機械翻寫。
- 中性內容功能：[Hao0321/claude-skill-social-post](https://github.com/Hao0321/claude-skill-social-post)。先前核對MIT；不帶作者策略或私人治理內容。
- 自有影音wrapper：使用者先前提供的程式與工作經驗；不附私人部署或帳號設定。
- 依賴：[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[Playwright auth](https://playwright.dev/python/docs/auth)、[SQLite WAL](https://sqlite.org/wal.html)、[MCP工具規格](https://modelcontextprotocol.io/specification/latest/server/tools)、[Agent Skills](https://agentskills.io/specification)。B01選定實測版本，不把歷史查核當今天所有環境都相容。
- 授權說明：[GitHub licensing](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。公開可讀、軟體授權與平台資料使用許可是不同問題。
- 私密資料：[GitHub removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。已曝光憑證先輪換，gitignore不是刪除歷史。

本包是原創文件／契約，不包含移植的第三方runtime。實際採用每段程式記repo、commit、原path/blob、license、採用範圍、local path、修改與review；保留所需完整license文字。

沒有完整核實當前帳號適用的Facebook／Muse條款，本包不保證TOS合規、帳號安全或名稱商標可用。不將改名、低調或資料來源偽裝視為許可。
