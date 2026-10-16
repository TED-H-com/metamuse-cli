# MetaMuse 工具與命令對照

本文件提供 MetaMuse 支援之 19 個 MCP 工具（15 個 P0 與 4 個發布工具）、對應 CLI 命令、必要識別碼（IDs）與結果語意。相關規則請參閱 [Skill 主文件](../SKILL.md)、[降級處理](degradation.md) 與 [安全邊界與限制](limits.md)。

> 動態限制數值（如字元組上限、檔案大小、逾時秒數）、錯誤重試策略與工具標註屬性（如 readOnlyHint、idempotentHint 等），皆在程式碼中集中維護。Host 必須動態從 `metamuse_inspect` / `metamuse doctor`、回應 envelope 中的 `retry_class` / `next_action` 以及 MCP `tools/list` 讀取，不得依賴文件靜態硬編碼。

---

## 1. 工具與命令清單

### 1. `metamuse_inspect`
- **使用者意圖**：檢查本地執行環境、功能實作狀態與 worker 可用性（不觸網）。
- **對應 CLI 命令**：`metamuse doctor`（或 `metamuse --json doctor`）
- **必要識別碼**：無。
- **成功語意**：`ok`。同步回傳 capabilities 清單、worker_available 與 network_mode。

### 2. `metamuse_project_create`
- **使用者意圖**：建立新專案以組織資料、討論與產出。
- **對應 CLI 命令**：`metamuse project create`
- **必要識別碼**：`request_key`（以及 `name`）。
- **成功語意**：`ok`。回傳新建立之 project 資訊（含 `id`）。

### 3. `metamuse_collect`
- **使用者意圖**：將外部社群貼文、留言、影音或本地檔案收集排程至專案中。
- **對應 CLI 命令**：`metamuse add`
- **必要識別碼**：`project_id`、`request_key`（遠端來源需存取授權 `access_grant_id`）。
- **成功語意**：`accepted`。工作已排入隊列，不等於執行完成；實際執行依賴 worker。

### 4. `metamuse_job_get`
- **使用者意圖**：查詢特定持久化工作之當前狀態與步驟進度。
- **對應 CLI 命令**：`metamuse jobs get`
- **必要識別碼**：`job_id`。
- **成功語意**：`ok`。回傳工作狀態與已記錄之步驟。

### 5. `metamuse_job_list`
- **使用者意圖**：分頁查詢專案內所有工作之狀態清單。
- **對應 CLI 命令**：`metamuse jobs list`
- **必要識別碼**：`project_id`。
- **成功語意**：`ok`。回傳工作清單與分頁指標（`next_cursor`）。

### 6. `metamuse_job_control`
- **使用者意圖**：恢復中斷之工作或請求取消本機工作。
- **對應 CLI 命令**：`metamuse jobs resume` 或 `metamuse jobs cancel`
- **必要識別碼**：`job_id`、`request_key`。
- **成功語意**：`ok`。更新工作控制狀態；對未知外部狀態不盲目重送。

### 7. `metamuse_search`
- **使用者意圖**：在專案範圍內檢索已收集之證據項目與素材。
- **對應 CLI 命令**：`metamuse search`
- **必要識別碼**：`project_id`。
- **成功語意**：`ok`。回傳命中之證據項目與分頁指標（`next_cursor`）。

### 8. `metamuse_packet_prepare`
- **使用者意圖**：依選定素材之固定修訂版本，建立外送前預覽資料包（packet）。
- **對應 CLI 命令**：`metamuse packet prepare`
- **必要識別碼**：`project_id`、`request_key`。
- **成功語意**：`ok`。回傳固定修訂版本之 packet 資訊。

### 9. `metamuse_muse_open`
- **使用者意圖**：在已授權範圍內開啟新的 Muse 主題討論。
- **對應 CLI 命令**：`metamuse muse open`
- **必要識別碼**：`project_id`、`request_key`（缺access_grant_id回approval）。
- **成功語意**：`accepted`。discussion 與 job 已 commit；沒有 worker 則 queued。可明示原 `discussion_id` 排 list 對帳，不再 create。

### 10. `metamuse_muse_send`
- **使用者意圖**：向 Muse 討論主題發送問題與分享資料包。
- **對應 CLI 命令**：`metamuse muse send`
- **必要識別碼**：`discussion_id`、`packet_id`、`request_key`（缺share_grant_id回approval）。
- **成功語意**：`accepted`。外送交換請求已提交，不等於研究已完成。

### 11. `metamuse_muse_read`
- **使用者意圖**：讀取 Muse 之交換回應或刷新最新狀態。
- **對應 CLI 命令**：`metamuse muse read`
- **必要識別碼**：`exchange_id`（若 refresh 需 `access_grant_id`）。
- **成功語意**：`ok` 只表示本地有界讀取；`refresh=true` 回 `accepted` 與 refresh job。progress 不代表完整；fixture 回應有 backend/mock 標記。

### 12. `metamuse_muse_close_exchange`
- **使用者意圖**：結算當前討論輪次並標記討論成果狀態。
- **對應 CLI 命令**：`metamuse muse close-exchange`
- **必要識別碼**：`exchange_id`、`request_key`。
- **成功語意**：`ok`。結算輪次成果；不等於遠端取消。

### 13. `metamuse_draft_save`
- **使用者意圖**：儲存主 Agent 撰寫之草稿文字、事實引用與版本。
- **對應 CLI 命令**：`metamuse draft save`
- **必要識別碼**：`project_id`、`request_key`（更新既有草稿需 `draft_id` 與 `expected_revision`）。
- **成功語意**：`ok`。草稿已安全持久化至本地 store。

### 14. `metamuse_draft_preview`
- **使用者意圖**：檢查與預覽指定版本之草稿內容（絕不發布）。
- **對應 CLI 命令**：`metamuse draft preview`
- **必要識別碼**：`draft_id`、`revision`。
- **成功語意**：`ok`。回傳草稿預覽資料。

### 15. `metamuse_export`
- **使用者意圖**：將選定素材與草稿匯出為本地檔案製品（不發布）。
- **對應 CLI 命令**：`metamuse export`
- **必要識別碼**：`project_id`、`request_key`。
- **成功語意**：`ok`。同步產出並驗證本地artifact／manifest；fixture-derived內容保留backend/mock/research_complete=false。

### 16. `metamuse_publication_preview`
- **使用者意圖**：預覽已保存草稿將送到哪個 Facebook 粉專，以及完整文字。
- **對應 CLI 命令**：`metamuse publication preview`。
- **必要識別碼**：`project_id`、`draft_id`、`revision`、`account`、`destination`。
- **成功語意**：`ok`，`data.approval` 提供給操作者核對，沒有發文或授權。

### 17. `metamuse_publication_submit`
- **使用者意圖**：送出操作者已核對並批准的一篇粉專文字貼文。
- **對應 CLI 命令**：`metamuse publication submit`。
- **必要識別碼**：預覽的相同欄位、`request_key`、人類核發的 `publication_grant`。
- **成功語意**：`accepted` 只代表排隊；用 `publication_id` 查詢。`sent_verified` 才代表收到回執並核對回讀；fixture 不是真實發文。

### 18. `metamuse_publication_get`
- **使用者意圖**：查閱原始文字、目標、發送嘗試及回執。
- **對應 CLI 命令**：`metamuse publication get`。
- **必要識別碼**：`publication_id`。
- **成功語意**：`ok` 只表示讀取本地紀錄。`needs_reconcile` 不可改 key、草稿版本或批准後重送。

### 19. `metamuse_publication_reconcile`
- **使用者意圖**：用原始貼文回執重新核對送達結果。
- **對應 CLI 命令**：`metamuse publication reconcile`。
- **必要識別碼**：`publication_id`、`request_key`、人類另行批准的 `publication_grant`。
- **成功語意**：`accepted` 代表排隊回讀。只讀取原回執，不重發、不接受任意網址。沒有回執仍為未知。

---

## 2. 僅限人類操作之授權命令（Human-only authorization commands）

以下管理操作涉及授權、憑證與安全邊界，僅限人類操作者在 CLI 手動執行，無對應 MCP 工具。Skill 與模型輸出絕無權限自動執行或自我簽發授權：
- `metamuse auth import`：匯入外部帳號驗證資訊或憑證檔案。
- `metamuse permit create`：建立限定範圍、到期日與使用次數之操作許可。
- `metamuse permit approve`：人類重算並確認approval.request；Agent只顯示next_action命令，絕不可執行或自批。預設24h到期，改request不能沿用digest。
- `metamuse permit research`：人類一次批准固定discussion/topic/open key、packet與account/provider/backend，回傳access/share IDs；現有open/send/read工具重用，不新增MCP。問題可變但不能夾帶其他資料；remote read/輪次/expiry不重置。open JSON使用project_id/topic/request_key；CLI另需--packet、--rounds、--max-reads、--expires-at、--request-key。之後使用上述open/send/read/close，再以原始來源和兩輪observation evidence_ref呼叫draft_save、draft_preview、export（draft_revision ref）。
- `metamuse permit revoke`：撤銷特定操作許可。
- `metamuse permit list`：檢視專案內現有許可清單。

---

## 3. 本機專用 CLI 命令（CLI-only local commands）

以下本機維護與環境命令僅能在 CLI 終端機執行，無對應 MCP 工具：
- `metamuse packet preview ID`：人類查看固定packet文字；MCP可重播同packet_prepare request/key取得相同preview。
- `metamuse init`：初始化 MetaMuse 工作區目錄與資料庫。
- `metamuse worker run`：手動啟動本機背景工作執行程序以推動佇列。
- `metamuse skill export --dest DIR`：把已打包的 Skill 寫到操作者指定的目錄。已有檔案時要加 `--force`。此命令不會自行選擇 agent 或 host 設定目錄，也不授予權限。安裝步驟見 [Skill 安裝](../../../docs/dev/SKILL.md)。

---

## 4. 結果狀態語意

所有 MCP 工具與 CLI `--json` 皆回傳標準 JSON Envelope，其 `status` 欄位代表嚴格狀態定義：
- **`ok`**：同步操作已完全成功，結果資料直接於 `data` 欄位取用。
- **`accepted`**：非同步工作或交換已持久化排入隊列，**不等於完成**。後續需透過 `metamuse_job_get` 或 `metamuse_muse_read` 查詢。若 `worker_available: false`，背景不會自動執行。
- **`partial`**：僅取得部分資料（如分頁中段或多段回應尚在進行），進度不可冒稱為最終成果。
- **`error`**：操作失敗或拒絕，`error` 欄位包含 `code`、`message`、`retry_class` 與 `next_action`。
