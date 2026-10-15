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

Muse 介面漂移先標不相容，不改密碼學或關閉驗證。獨立記錄且明確支援的application handshake payload可沿用同一成熟Noise/TLS及所有適用檢查；unknown、RV/CVM fail closed，不放寬cryptography或tier。未知送達查原對話，找不到也不代表沒送達。存取拒絕／驗證／平台限制暫停，不換帳號、代理或 Muse 繞過。授權不明的程式不直接進公開包。

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

Wheel 另含 `metamuse/data/skills/metamuse/`，內容與 `skills/metamuse/` 相同。`metamuse skill export` 把那份打包副本寫到操作者指定的目錄。

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

每workspace一個權威SQLite，JSONL/CSV只是進出格式。foreign_keys=ON、recursive_triggers=ON、local WAL、synchronous=FULL、有界busy_timeout；這不是硬體級絕對耐久性保證。不得多機直接共享live WAL DB。所有連線設定一致，外部等待不在transaction內。

Connection timeout為30秒，busy_timeout=30000 ms，共用一個internal constant。MEASURE：
suite load下90秒548次Store.transaction，p50 12.16 ms、p99 1781.87 ms、max 6903.15 ms；
commit至context exit的max 6901.59 ms。30秒提供超過觀測stall的餘裕，不保證更長stall不發生。
一般CLI同步等待每個SQLite operation，不印progress；逾時回固定E_STORAGE／exit 7。
30秒是每個write transaction的等待上限，不是整個CLI command的deadline；同命令可有多個transaction。
既有workspace先read檢查active content.cursor，存在就不開provisioning write transaction，
doctor、jobs get/list及search不取得write lock；首次provision在write transaction內重查以避免race。
WAL reader不取得write lock；同Store的短connection仍受process內RLock序列化。
每次writable open設journal_mode=WAL；已是WAL時SQLite視為no-op，不取write lock，
持有另一writer的真實測試亦通過。實際mode切換仍可能SQLITE_BUSY，走相同分類。
Store按sqlite_errorcode的primary byte分類SQLITE_BUSY／SQLITE_LOCKED，保留extended code於
internal _StorageBusy(CoreError)。public仍E_STORAGE／retry_class=never；其他SQLite錯誤
（含corruption、I/O、缺code）不重試，不按message判斷，不輸出SQL／path／row內容。

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
| workspace_keys | private name/version/key_material/created_at/status；active key及保留的舊verification版本。 |

P1才加actions/action_attempts、metric_snapshots，不預建campaign/voice/formula策略庫。

必須有FK及project一致性：跨project item/source/parent/packet引用拒絕。asset bytes可共用，但可見範圍依item_assets與source_assets，不因hash相同洩露別project。字幕的 project_id 是衍生文字的擁有者，可沒有 asset；共用 bytes 不共用字幕。transcript (id, revision) 只追加，不 UPDATE，也不能以 REPLACE、INSERT OR REPLACE 或 ON CONFLICT DO UPDATE 覆蓋既有主鍵。source_assets 整列不可更新，既有主鍵不可 REPLACE。兩表都沒有次要 UNIQUE；非唯一索引不是身分。單則字幕文字超過 1,000,000 字元是該列錯誤。

native IDs為opaque字串，不自行解碼。可靠ID用(platform,scope,id)識別；無ID保持弱識別，不以文字hash假裝同留言。parent不得自我／循環。revision追加，不原地抹掉舊來源；修正同transaction標記派生stale。

## 5. 工作提交与executor

先驗輸入／scope→transaction查request_key→建立job/steps/event→commit→回accepted/job_id/worker_available。收到job並不等於開始。

worker run、預設MCP附帶worker、CLI --run都搶相同OS lock。MCP --no-worker明示停用附帶executor；鎖被占用仍服務，回worker_available:false及next action，本次server不重試取得鎖。附帶worker只是同Executor的一個thread，沒有第二個scheduler或權威database。inspect附attached_worker狀態；CLI doctor由owner及Linux FLOCK觀測實際持鎖。同workspace只有一個owner；其他入口只排隊。舊heartbeat過期但OS lock仍被持有時不能直接接管；由操作者確認終止owned process及退出後才開新worker。不得殺不明browser程序。

取得鎖後遞增epoch，短transaction claim queued step；外部動作前再驗epoch、job state、permit和target。epoch防舊callback寫回，但不能讓遠端服務獲得它不支援的fencing或exactly-once。

附worker在自己的thread建立同paths／clock／migrations／schema version的sibling Store，
有獨立RLock；MCP tools保留ctx.store，因此WAL read不排在worker的SQLite wait後。
Store建立也走unclaimed retry。writer如跨process一般由SQLite仲裁，同一database及maintenance／worker OS lock不變。網路下載／模型等待不持有長transaction或connection lock。每批結果commit checkpoint。純讀取可按規則重試；存取拒絕／challenge不當timeout重試。

未claim step時（startup epoch/recovery、heartbeat、atomic claim/cancel sweep、idle及
safe-read已requeue後的等待維護）只對_StorageBusy重試。transaction先rollback，exponential
backoff加jitter：25–50 ms起，倍增至1–2秒，每次wait最多2秒，stop喚醒且不再開始下一次。
每個unclaimed action以scoped thread-local override將connect及busy_timeout（含read-only
version probe）限為1秒，其他caller與claimed transaction仍為30秒。原MEASURE的548次中
531次（96.9%）在1秒內，p50 12.16 ms；tail stall留給可stop的外層重試。
exit metadata cleanup同樣限1秒，只略過busy；其他錯誤先釋放OS lock再傳出。
未claim的stop最多一個1秒attempt加1秒cleanup，即2秒SQLite lock wait；不涵蓋fsync、
filesystem／OS maintenance lock或任意handler的執行時間。run_once與run共用此維護行為。
已claim須先到handler邊界；其後finalization最多30秒加fallback 30秒，fallback若逾時就傳出。
zero-handoff另有settlement 30秒及failure-event 30秒，最多四個連續wait即120秒，再加1秒cleanup。
pre-handler guard及handler各checkpoint／boundary transaction各自仍有30秒；I/O另受自身timeout。
MCP shutdown join worker thread以避免遺棄已claim的step或OS lock。
storage_busy_retries累計並飽和於2**31−1，既有attached status／inspect／同context doctor可見。
startup contention為starting且不可用，ownership commit後才running；非busy失敗為failed，
worker_error_code只含固定code（unexpected internal exception同tool failure慣例為E_STORAGE），
不含message或path。兩種failure皆以log_safe_exception記mcp.attached_worker及固定code，
僅class與去path的basename／line／function frames；startup stop直接starting→stopped，不短暫running。
startup failure後新提交仍accepted/queued且worker_available:false。
startup worker失敗亦保留MCP server供查診斷，不因worker失敗終止stdio服務。
ready handoff最多2秒，未完成ownership則以starting開始服務；首次短busy、lock_busy或failure亦設ready。
另一CLI doctor只觀察OS lock可用性，不讀另一process的失敗記憶。

已claim的handler不套busy重試：none／safe_read保留既有失敗規則；external_submit未越過
committed submitting boundary可failed/not_submitted，越過則uncertain，絕不自動重送。
_fail／_finish_step等保存若busy則rollback；fallback寫入也busy時傳出錯誤，保留durable
running/submitting供原有restart recovery，不捏造succeeded或failed_before_send。
shutdown清owner metadata若busy可留stale metadata；釋放OS lock才是availability權威。
同PID另須完整owner nonce存在process-local live registry；ownership commit後才加入，
exit不論cleanup結果皆移除。新的同PID Executor不會借用舊nonce；其他PID仍以Linux FLOCK觀測。

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

MCP stdin EOF、SIGTERM、SIGINT先request_stop，停止claim新step；目前有界step完成或依既有cancel／epoch規則停在安全邊界，再join thread並釋放鎖。不將server關閉當job cancel，不承諾server退出後附帶worker繼續。SIGKILL／crash由下次Executor的原有恢復處理，已進submitting的send只對帳不重送。若crash發生在ack已checkpoint但job完成前，authorized history read亦可驗原step／target／outbound digest與persisted ack後settle，attempt不增加。

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



2026-10-14 primary standard transport：authenticated session以GET /api/session?vm_state=live
選唯一一致hatch_vm row，mint body用row endpoint_url及name；WSS query固定vm_id/auth_token/
optional non-delegation notary_token/app_id=hatch-web/fresh request UUID4，Origin固定Muse。
access/hatch token grammar分開；legacy hatch不含dot；s0 opaque不改寫、不冒稱驗signature。每個
token/notary_token/notaryToken/noiseNotaryToken/endorsement alias先驗；case-insensitive delegation./
endorsement.及malformed s0回protocol/never，不silent drop。
payload/全部vms先normalize VM id、scan hatch_cvm/CVM_*/truthy cvm_required/cvmReplacementEnabled，
即使unassigned/ambiguous/invalid也先record sticky，再stop；無VM id只record account。wire保留server
exact id，與storage id只可差case/外側whitespace。msg1帶fresh 32-byte challenge；握手varint minimal
uint64；RV field 1必須nonempty bytes。bounded authenticated msg2見canonical field-2/varint tag即先
record permanent confidential，再拒絕malformed/unsupported state/fields/chip；無reset tool。
只有strict NON_TEE subset（outer field 2，optional explicit platform=0，nested key/nonce各32 bytes）
且key等於成熟Noise responder static、nonce等於本attempt challenge才可empty msg3/split。
unknown/malformed/evidence/CVM、bad AEAD/low-order DH或不等式回E_PROTOCOL_MISMATCH（never）。
不關tier、不略過適用pins/context checks；standard不做SNP/TCB/notary-signature claim。

wake是account-side power-state request，只在已grant Muse operation觸發的live-mode connect內：
DISABLED/DELETING/ERROR在wake前停止，CVM_*或hatch_cvm先record sticky再停止；其餘activation
POST /api/hatch/vm/wake {vm_id,retry_count:0}後dial。upgrade 502/503/504在每次redial前wake
{vm_id,retry_count:n,connect_attempt_id:UUID4}，delay=max(2s,min(0.2s×1.5^(n−1),8s))，最多20
redials或mint後啟動的240s wake/dial/handshake budget，超界E_RESOURCE_UNAVAILABLE並請操作者VM尚未running、稍後再試。
401/403→E_AUTH_REQUIRED；429→E_RATE_LIMITED；connect/DNS/TLS→E_NETWORK_TIMEOUT，guard refusal
→E_SCOPE_REQUIRED。bootstrap只收200（spike§2/5）；wake 200（operator LIVE probe，evidence在repository之外）或operator round-2
contract的202/204（非本task LIVE observation）不要求JSON。session/wake 404/409/410 unavailable，
其他unexpected 2xx/4xx protocol，只有401/403 auth。每次wake/dial/handshake前重查remaining budget；
耗盡回resource unavailable、不傳nonpositive curl timeout、不send msg1。offline/fixture gateway
不wake、不network；不call lease/reset/chip-selection/
enrollment/recovery/fallback-notary/capability registration。upgrade URL/query/body不入exception/log。
request選identity encoding；duplicate/unsupported response encoding拒絕，single consumer和完整性caps
不變。握手／token失敗不改B10先前handoff的不明送達、不重送、不false failed_before_send。

2026-10-14 B09 authoritative trust補充（round 3）：005未merge，原地amend、無006。
固定MUSE_ORIGIN/HATCH_WSS_BASE只有一個trust domain；provider/account是local binding label，
不partition remote expectation。label-bound account/VM row保存selection/session/generation；
label-independent muse_trust_observations以viewer_hash/normalized VM id保存authenticated observation，
empty VM id只表示owner evidence，不是wire identity。owner↔VM associations形成graph；recursive SQL
closure把confidential owner傳至全部VM、confidential VM傳至全部owner，transitive且monotonic。
21 guards、3 indexes及transaction-local propagation barrier作set-wise更新；超過1000 rows不增加
trigger depth、不因size丟record。hint bind在單一transaction先barrier、bulk observation/VM insert、
materialize closure一次、三table set-wise update、remove barrier後commit；不逐id算closure。committed barrier、changed schema/corruption阻擋connection。
CVM hints在owner binding/status/selection拒絕前獨立commit全部usable VM ids；RV msg2在stale或
changed binding也以remote identity commit、不用generation CAS。write失敗停止msg3並明示無法record。
standard handoff才做currentness/generation CAS；effective confidential先於generation檢查，回protocol/never。
trust-domain/per-account locks跨msg3，maintenance及transaction在network前release。stale context回
E_STORAGE/never並請重connect；integrity/schema才建議repair/verified restore，明示older backup（含v4
rollback）會丟backup後的confidential expectations。無in-place reset。schema一次verify、每boundary比signature。
msg2 decrypt上限65535、先full RV discriminator再standard 8192 cap；session cap 1 MiB，超界不record也不dial。
HTTP 200 missing/malformed fields是protocol/never；explicit ok:false或401/403才auth；twice-stale
notary依公開MUSE_SPIKE §9.1 one-remint後stop；resource/retry-later是inference，無session refresh修復證據。

Notary freshness is only a cache hint, not local signature/expiry verification.
Missing or undecodable third-segment JSON, a non-object document or non-list restrictions
means no hint. Skip non-strings, non-timeout prefixes and entries whose stripped timeout:
remainder fails Python float(), is non-finite or is <= 0. The first positive finite
value decides stale iff expiry <= now + 60 seconds; ignore later entries. None usable
means not stale. Mint at most twice; a twice-stale remint stops before wake or dial.

### Session建立

先建本地discussion＋open intent，再遠端create；回remote id才完成mapping。timeout時列open_uncertain。list對帳必須可靠識別，同標題不夠；找不到也不證明沒建。需要人選擇已有session時只顯示核准範圍，不掃其他私人對話猜。

### 送訊息

驗packet／grant／discussion→讀baseline→持久化exchange與payload→保留grant用量→標submitting→單次send→sent/send_uncertain→history讀取→候選關聯→reply_observed→host結算。

只有可靠知道尚未送出任何bytes才failed_before_send；其他不明不退還grant次數再重試。問題本身也屬分享內容。

消息關聯用session、baseline、user echo／request marker、role、時間與已校準status。非空status不是研究完成。外部手動訊息、晚到回答或跨session內容不自動歸給本輪。保留raw自然語言、多段和cursor，無JSON也不是失敗。

同discussion一個open exchange；host選answered/needs_more_data/abandoned。未知提交被abandon後discussion隔離，不藉close繼續派工。遠端cancel未驗證就not_supported/unknown。晚到訊息保留，不能配到下一題。

模型拒絕不以改來源、假身份或重寫同目的prompt迴避。兩個Agent同讀一份資料的共識不是獨立證據。分析prompt不會收回Muse原生工具權限，操作者需在服務本身設定。

### B10持久化與boundary補充

003_muse_lifecycle.sql在002後forward ALTER，不改001。exchanges保存精確prepared payload/marker/body digest、
original submission_step_id、submit time、stream ID、nullable handoff、first attempt evidence、獨立delivery proof，
及versioned association memory。discussions固定provider/account/auth_ref/backend與open intent/step/proof及quarantine。
messages按discussion/account隔離identity；append-only muse_observations保存每次raw sanitized observation、digest、
message/discussion FK與backend。legacy handoff與binding保持NULL；closed不reopen，proof不覆寫first evidence。
新identity有BEFORE INSERT replacement/UPSERT guard與retention trigger；migration guard audit涵蓋所有版本。

before_external_action transaction完成verifier/reservation/intent後才mint one-use issuer-registered token，
consume綁owner/epoch/job/step/operation/target/action digest。read不mint。transport affirmative zero-handoff可同process
record_not_submitted，commit_not_submitted以issuer proof保持fencing並在cancel之前atomic保存記帳；原step經settle_uncertain變local failed/remote not_submitted。closed保留host outcome，不reopen；原step settlement後guarded callback才清quarantine。寫入失敗記durable event並保留uncertain，worker清process registry後繼續queue；crash則unknown，不退用量。
history/list在另一safe_read job，讀取current access grant；原step settlement不再reserve/send/requeue。
echo比較對prepared text與stored observation套用同一sanitize_document，wire仍精確使用host文字；redaction collision保留ambiguous。
send boundary持久化write-once submitted_observation_high_water；history關聯排除prepared/zero-handoff，並要求訊息首次observation在此高水位之後。多個同文exchange共享markerless echo時保持ambiguous，不按rowid先後認領。user_echo SQL guard也檢查已提交與首次observation；transport首結果保留既有history proof、另存真實ack/verdict，不reopen closed。
live send先有已完成、授權的history baseline；已mapped discussion_id的open走history read，不做第二次create。
open boundary在reservation與token之前重查未解create；拒絕時transaction rollback，不接觸remote、不標uncertain。

### AT-042 immutable evidence link（schema 4）

EvidenceRef(kind=muse_observation,id=observation.id,revision=1)重用003的append-only、retained observation，
不引用mutable Message.text，也不增加table/column。Resolver從固定raw_event取得本次文字，從stored backend
取得mock標記；later enrichment必須新observation，不改舊引用。Store經discussion驗證project。
004_muse_evidence.sql只追加draft JSON引用的存在/project/revision guard及packet禁止Muse引用guard；
001–003不改。既有rowid-aware no-replace與identity freeze保留，fresh/upgrade-v3/restore-v3 canonical一致。
004尚未發行，直接修正兩guard：json_each(ref.value)計數kind/id/revision，任一重複則拒絕，
不依賴json_extract對duplicate key取第一值。Muse revision需json_type=integer且value=1。
Store全部JSON decoding共用object_pairs_hook遞迴拒絕duplicate keys，既有固定E_STORAGE不echo值。
來源與host文字不mint grant；packets不重分享Muse。Draft save/preview和transitive exports附muse_evidence、
backend、mock、research_complete:false；每格式的payload及manifest都有attribution。Text保留正文並加notice，
CSV在source JSON附標記且維持formula-safe，JSONL text原文不變。Bundle redacted，其他格式original_private。
Store.verify_export在原transaction或read-only archive connection從draft refs與immutable observations
重建attribution，核對manifest muse_evidence、全部JSONL records/source與Markdown source headers；
stripped/altered label即使hash與manifest bytes一致也拒絕，沿用固定export unavailable錯誤。
Shared redaction漏bare hatch_sess pair由B16a修正；AT-042 strict xfail只固定已知bundle leak，不修改redactor。

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

備份用SQLite一致backup加ready object及ready export manifests；每個export保留manifest與payload，驗bytes/hash並拒unexpected members；在新workspace做FK/hash/schema檢查。migration維護鎖＋升級前備份；不能只複製live DB主檔或刪庫假成功。舊版程式遇新schema拒絕寫入。
Restore refuses newer-version backups; older backups are verified at their recorded
schema version before forward migration through normal guarded open, including its own
pre-upgrade backup. Archives use rollback-journal SQLite, reject sidecars/unknown entries,
and are inspected read-only without current Pydantic models. Copied DB and artifacts are
reverified; backup/workspace/restore roots must not overlap except internal pre-upgrade paths.
All verified files reject symlinks/hardlinks, and unlisted objects/exports fail verification.
A private runtime/restore.incomplete marker is fsynced before initialization, blocks even
broken-symlink markers, and is removed then directory-fsynced only after success. Copied
worker ownership is cleared before the pre-upgrade backup. The original backup
is retained, and migration failure rolls back the restored database without claiming success.
Table-rebuild migrations disable foreign_keys before BEGIN IMMEDIATE, run foreign_key_check
before commit, and restore enforcement in finally; runtime connections keep foreign_keys ON.

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
BEFORE INSERT guards prevent replacement/UPSERT of guarded identities; recursive delete
triggers protect retained key versions. Ready/unavailable export payloads are immutable;
only ready→unavailable changes state, and replay verifies the authoritative export row.
Schema 001 guard fixes remain amended in place before release. Schema version 2
registers 002 for project-scoped transcripts and immutable source_assets; shared bytes
never grant permission to another project's derived data.

### B07/B11 content implementation clarifications

Content requests, outputs and recovery rules: [docs/dev/CONTENT_API.md](../dev/CONTENT_API.md).
Search uses exact case-sensitive substring matching without Traditional/Simplified or
Unicode conversion. Stateless HMAC cursors carry project/query bindings, row watermarks,
membership fingerprints and last sort keys; latest-as-of queries exclude later inserts
and revisions. Deletion, replacement or loss of visibility fails rather than skipping a
reference. Store initialization provisions a private versioned workspace key before read-only search.
Active/retired workspace_keys versions survive backup/restore. Legacy receipt keys are
not imported; old development DBs lacking the table fail with fresh-workspace guidance.
Pre-release schema 001 is amended for fresh workspaces without a version bump;
no per-query files or second store is added. Keys never enter tool results/events/exports. Long snippets preserve the entire matched query.

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
Core backups include every ready export directory and manifest, verifying exact membership,
manifest bytes and member hashes/sizes at backup and restore. Restored receipts replay the
original artifact. Missing/changed live export files are recorded as unavailable in a partial
backup manifest with an explicit warning, without blocking migrations or backup. Restore
preserves unavailable state; receipt replay fails honestly. Copied artifacts remain strictly
verified. Orphan cleanup remains pending. Legacy search-marker files are no longer read
or created; cleanup of files from older development builds remains an operator task.

Schema-2 transcript visibility uses authoritative project_id, with nullable asset/item/source
anchors. Migration 002 copies a v1 transcript only when its asset links to exactly one
project; unlinked or ambiguous ownership aborts and retains the original data. New
transcript revisions mark directly derived records stale; B07/B11 also checks transitive
staleness at use. Fresh v2 databases and restored/upgraded v1 databases must have the
same canonical schema. Both 002 tables retain their guard names, use rowid-aware INSERT
guards and whole-row UPDATE freezes, and have no secondary UNIQUE indexes. These
changes are pre-release amendments to 001/002, not an additional migration.


### Core round-3 storage clarifications

SQL guards defend against application bugs, not arbitrary same-user SQL/file edits.
Application/migration SQL forbids all conflict clauses and rowid writes; static tests
check every source SQL string and migration. Guarded identities include implicit rowid
and immutable operation/exchange/project bindings; state machines remain in jobs/app.
Canonical sqlite_master objects (tables/indexes/triggers/views plus normalized SQL) must
match replayed migrations at the recorded version on open, before upgrade backup,
before migration commit, and before any restored DB writes. Guard-dropping rebuilds fail.
Archive database checksums are required. Backup/restore return structured complete/partial
results with unavailable export IDs and warnings; restore additionally returns its Store.
Partial pre-upgrade status is durably recorded under runtime, visible in doctor/inspect
and triggering envelopes. No global Python warning filter is changed. Archives fsync DB,
manifest and directories. Restore blocker checks/unlink are inside the maintenance lock.
Raw leaf symlinks are refused before resolution; symlinked parents remain supported.

## Operator approval preview seam (B08/B10/B14)

Fixed feature registration uses `safety.register_approval_preview(operation, fn,
request_model=...)`. The shared app wrapper calls it after grant failure, before any
submission. The callback prepares exactly the action the feature will later verify,
reads only the workspace/configuration, redacts secrets and creates nothing. CLI
`permit approve` uses the same registry/model/preparation; terminal yes or an exact
recomputed digest is required before the existing create_permit API. MCP cannot mint
permits. Model text cannot select provider/account or expand the action. Packet preview
reads its immutable text; MCP packet_prepare replay returns the same fixed pre-share
preview. No schema migration or separate approval store is needed.

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
