# MetaMuse 工具與命令對照

本文件提供 MetaMuse P0 支援之 15 個 MCP 工具、對應 CLI 命令、必要識別碼（IDs）與結果語意。相關規則請參閱 [Skill 主文件](../SKILL.md)、[降級處理](degradation.md) 與 [安全邊界與限制](limits.md)。

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
- **必要識別碼**：`project_id`、`access_grant_id`、`request_key`。
- **成功語意**：`accepted`。discussion 與 job 已 commit；沒有 worker 則 queued。可明示原 `discussion_id` 排 list 對帳，不再 create。

### 10. `metamuse_muse_send`
- **使用者意圖**：向 Muse 討論主題發送問題與分享資料包。
- **對應 CLI 命令**：`metamuse muse send`
- **必要識別碼**：`discussion_id`、`packet_id`、`share_grant_id`、`request_key`。
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

---

## 2. 僅限人類操作之授權命令（Human-only authorization commands）

以下管理操作涉及授權、憑證與安全邊界，僅限人類操作者在 CLI 手動執行，無對應 MCP 工具。Skill 與模型輸出絕無權限自動執行或自我簽發授權：
- `metamuse auth import`：匯入外部帳號驗證資訊或憑證檔案。
- `metamuse permit create`：建立限定範圍、到期日與使用次數之操作許可。
- `metamuse permit revoke`：撤銷特定操作許可。
- `metamuse permit list`：檢視專案內現有許可清單。

---

## 3. 本機專用 CLI 命令（CLI-only local commands）

以下本機維護與環境命令僅能在 CLI 終端機執行，無對應 MCP 工具：
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
