# MetaMuse 降級處理與錯誤處置

本文件規範在後端未就緒、功能降級或執行錯誤時的處置流程。相關說明請參閱 [Skill 主文件](../SKILL.md)、[工具索引](tools.md) 與 [安全邊界與限制](limits.md)。

> 動態限制數值、錯誤重試策略與工具標註屬性，皆在程式碼中集中維護。Host 必須動態從 `metamuse_inspect` / `metamuse doctor`、回應 envelope 中的 `retry_class` / `next_action` 以及 MCP `tools/list` 讀取，不得依賴文件靜態硬編碼。

---

## 1. 能力狀態（Inspect States）維度與處置流程

`Capability` 模型由四個正交維度判定是否就緒（僅當 `implementation == "implemented"`、`verification in ("fixture_passed", "live_verified")`、`environment == "ready"` 且 `permission == "granted_scope"` 時，`effective` 方為 `true`）。

透過 MCP `metamuse_inspect` 或 CLI `metamuse doctor` 檢驗所得之 capability 欄位，若 `effective` 為 `false`，應依各維度狀態分別處置：

### 1.1 實作狀態（`implementation`）
- **`absent`（未實作）**：工具在當前軟體版本中未包含實作代碼（`implementation: "absent"`）。呼叫該工具將回傳 `E_UNSUPPORTED`。立即中止該操作步驟，明確告知使用者該工具功能在此版本尚未實作，嚴禁編造或模擬執行結果。
- **`implemented`（已實作）**：工具代碼已具備（`implementation: "implemented"`），但若其他驗證、環境或授權條件未滿足，`effective` 仍為 `false`。

### 1.2 驗證狀態（`verification`）
- **`unverified`（未驗證）**：功能實作尚未通過基準測試驗證（`verification: "unverified"`）。不得宣稱該功能就緒。
- **`regressed`（退化）**：功能實作曾通過驗證但發生退化（`verification: "regressed"`）。視同無效工具，遵循降級路徑。
- **`fixture_passed`（單元/Fixture 驗證通過）**：已通過離線合成資料與單元測試驗證（`verification: "fixture_passed"`），可在離線或本機模式運作。
- **`live_verified`（LIVE 驗證通過）**：已通過操作者授權之真實外部後端驗證（`verification: "live_verified"`）。

### 1.3 環境狀態（`environment`）
- **`ready`（環境就緒）**：執行環境與系統依賴皆已滿足（`environment: "ready"`）。
- **`missing_dependency`（缺少依賴）**：缺少必要的外部工具、函式庫或執行檔（`environment: "missing_dependency"`）。呼叫將回傳 `E_DEPENDENCY_MISSING`，引導使用者安裝必要依賴。
- **`needs_auth`（需要憑證）**：環境需要帳號或憑證才能連線外部服務（`environment: "needs_auth"`）。呼叫將回傳 `E_AUTH_REQUIRED`，提示操作者手動匯入憑證。
- **`unavailable`（環境不可用）**：執行環境阻擋（`environment: "unavailable"`，例如離線網路模式阻擋外部網路存取）。中止外部請求，引導使用者改用本機離線流程。

### 1.4 權限狀態（`permission`）
- **`granted_scope`（範圍已授權）**：人類操作者已建立有效之範圍許可（`permission: "granted_scope"`）。
- **`not_configured`（未配置授權）**：尚未建立存取許可或金鑰（`permission: "not_configured"`）。呼叫將回傳 `E_SCOPE_REQUIRED` 或 `E_AUTH_REQUIRED`。提示操作者以 CLI 執行 `metamuse permit create` 或 `metamuse auth import`。
- **`denied`（授權遭拒）**：許可已被撤銷或範圍受限（`permission: "denied"`）。呼叫將回傳 `E_SCOPE_DENIED`。Skill 與 Agent 絕不可自行偽造或假定已獲得授權。

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
