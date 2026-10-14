# MetaMuse 降級處理與錯誤處置

本文件規範在後端未就緒、功能降級或執行錯誤時的處置流程。相關說明請參閱 [Skill 主文件](../SKILL.md)、[工具索引](tools.md) 與 [安全邊界與限制](limits.md)。

> 動態限制數值、錯誤重試策略與工具標註屬性，皆在程式碼中集中維護。Host 必須動態從 `metamuse_inspect` / `metamuse doctor`、回應 envelope 中的 `retry_class` / `next_action` 以及 MCP `tools/list` 讀取，不得依賴文件靜態硬編碼。

---

## 1. 能力狀態（Inspect States）處置流程

透過 MCP `metamuse_inspect` 或 CLI `metamuse doctor` 檢驗所得之 capability 欄位，若 `effective` 為 `false`，應依各項狀態分別處置：

- **`absent`（未實作）**：
  - 現象：工具在當前軟體版本中未包含實作代碼（`implementation: "absent"`）。呼叫該工具將回傳 `E_UNSUPPORTED`。
  - 處置：立即中止該操作步驟，明確告知使用者該工具功能在此版本尚未實作。嚴禁編造或模擬執行結果。
- **`unavailable`（環境未就緒）**：
  - 現象：執行環境缺少必要條件（`environment: "unavailable"` 或 `environment: "missing_dependency"`），如離線網路模式阻擋外部存取，或系統缺少執行依賴。
  - 處置：告知使用者缺少之環境配置，中止外部請求，引導使用者檢查環境或改用本機離線流程。
- **`not_configured`（未配置授權）**：
  - 現象：缺少存取憑證或許可（`permission: "not_configured"` 或 `permission: "denied"`）。呼叫將回傳 `E_AUTH_REQUIRED` 或 `E_SCOPE_REQUIRED`。
  - 處置：告知使用者需由人類操作者於 CLI 執行 `metamuse auth import` 或 `metamuse permit create` 進行授權。Skill 與 Agent 絕不可自行偽造或假定已獲得授權。
- **`unverified`（未通過驗證）**：
  - 現象：功能實作尚未通過單元 fixture 或 live 驗證（`verification: "unverified"` 或 `verification: "regressed"`）。
  - 處置：不得宣稱該功能已經過驗證就緒，視同無效工具，遵循降級路徑。

---

## 2. 嚴格回報 Blocked，絕不編造結果（AT-027）

- 當任何後端未安裝、連線失敗、缺少依賴或工具回報 `effective: false` 時，該步驟之真實狀態為 `blocked`（受阻）或 unavailable。
- **零偽造原則**：模型絕不可因為缺乏後端回應，而自行發明、模擬或幻覺出假資料、假 job、假 discussion 或假工具成功訊息。所有對外回覆必須誠實反映真實系統狀態。

---

## 3. 本機離線路徑（Local-only Path，當 Muse 不可用時）

當 Muse 相關工具（`metamuse_muse_open`、`metamuse_muse_send`、`metamuse_muse_read`、`metamuse_muse_close_exchange`）之 `effective` 為 `false` 時，本地工作流程完全不受阻礙：

1. **本機證據整理**：仍可使用 `metamuse_collect`（本地檔案模式）或本機匯入，並使用 `metamuse_search` 檢索已保存之素材。
2. **資料包封裝**：使用 `metamuse_packet_prepare` 固定證據修訂版本。
3. **主 Agent 獨立研究**：主 Agent 自行研讀本機資料包內容，提煉事實證據、標記推測與資訊缺口，無需將問題外送至 Muse。
4. **草稿保存與製品匯出**：使用 `metamuse_draft_save` 保存文案與事實引用，使用 `metamuse_draft_preview` 檢查修訂版，並使用 `metamuse_export` 產出本地匯出檔案製品。

---

## 4. 背景 Worker 缺席語意（Worker Absent）

當 `metamuse_inspect` 回報 `worker_available: false` 時：
- 所有非同步提交（如 `metamuse_collect`、`metamuse_export`）在回傳狀態為 `accepted` 後，僅是將 job 寫入本機 SQLite 資料庫之佇列。
- **語意誠實**：絕不可向使用者宣稱「工作正在背景執行中」或「稍後背景完成」。
- **操作指引**：應明確告知使用者背景無 worker 運作，提示使用者可於終端機執行 `metamuse worker run` 推動佇列。

---

## 5. 動態錯誤處理與重試策略

當工具回傳錯誤（status 為 `error`）時，Agent 應讀取 envelope 內之 `retry_class` 與 `next_action`，遵循規範行動：
- **`never`**：例如參數格式錯誤或功能未實作（`E_UNSUPPORTED`），嚴禁重試。
- **`safe_read`**：唯讀操作在網路瞬斷或頻率限制緩解後可嘗試讀取，應尊重重試間隔。
- **`reconcile`**：外部狀態未知（如 `E_EXTERNAL_STATE_UNKNOWN`），嚴禁盲目重新發送相同請求；應先查詢歷史紀錄（`metamuse_job_get` 或 `metamuse_muse_read`）進行對帳。
- **`user_action`**：需要人類介入，應明確向使用者提示 `next_action` 所指示之手動命令。
