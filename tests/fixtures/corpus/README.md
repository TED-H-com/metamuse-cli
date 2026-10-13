# MetaMuse CLI — 測試用合成資料集 (Synthetic Test Fixture Corpus)

## 1. 來源聲明與免責條款 (Provenance & Disclaimer)

- **來源 (Provenance)**：本資料集全部為**針對 MetaMuse CLI 自動化測試與驗證所自製之合成資料 (synthetic, created for MetaMuse CLI tests)**。
- **虛構保證 (Fictional Data)**：本資料集內之所有人名、粉專、頻道、帳號、文章、留言、對話、憑證、金鑰與網址皆為**完全虛構且顯然為測試用途**。除 `example.org`、`example.com`、`example.net` 及 IETF 保留測試網段外，不包含任何真實人物、真實頁面、真實帳號或真實秘密。
- **非真實使用者資料**：本資料夾內**絕對不含任何真實使用者資料、個資或線上隱私紀錄**。所有安全測試案例中的金鑰皆包含顯式 `FAKE` 標記。

## 2. 授權條款 (License)

- **程式碼與腳本 (Code & Scripts)**：遵循本專案本體之 **Apache License 2.0** 授權（詳見專案根目錄 `LICENSE`）。
- **測試資料與文案 (Data & Fixtures)**：依據 **CC0 1.0 Universal 公眾領域貢獻宣告 (CC0-1.0 Universal Public Domain Dedication)** 釋出，允許無限制再散布與測試使用。

---

## 3. 匯入資料格式定義 (Import Record Format)

本資料集各 JSONL / JSON / CSV 匯入檔均依循 `docs/design/02_SPEC.md` (§3 資料契約) 與 `docs/design/examples/comments.jsonl` 所定之通用資料結構：

| 欄位名稱 | 型別 | 必填／可為空 | 說明 |
|---|---|---|---|
| `platform` | string | 必填 | 來源平台識別字串（如 `generic`, `facebook`, `youtube`, `threads`）。 |
| `kind` | string enum | 必填 | 項目類型：`post`（貼文）、`comment`（留言）、`reply`（回覆）。 |
| `native_id` | string \| null | 可為空 | 平台原始不透明字串識別碼。若為 `null` 則由系統指派弱識別碼 (weak identity)。 |
| `native_scope` | string \| null | 可為空 | 平台所屬範圍（如社團 ID、粉專 ID、頻道 ID 或專案隔離識別）。 |
| `parent_native_id` | string \| null | 可為空 | 父層項目的 `native_id`。貼文為 `null`；回覆則指向其直接父層項目。 |
| `text` | string \| null | 可為空 | 內文字串（保留繁中、換行、CJK 標點、全形符號與 Emoji）。純媒體貼文可為 `null`。 |
| `source_url` | string | 必填 | 該項目之原始參照 URL（僅限合法保留網域，如 `example.org`）。 |
| `upstream_collection_method` | string | 選填/可為空 | 上游取得方式（如 `synthetic_fixture`, `manual_export`, `browser_automation`, `api`, `unknown`；未提供則預設為 `unknown`）。 |
| `created_at_raw` | string \| null | 可為空 | 原始顯示時間字串（可為相對時間，如 `"3小時前"`、`"昨天 14:30"`）。 |
| `created_at` | string \| null | 可為空 | ISO 8601 UTC 格式解析時間戳記（如 `"2026-10-13T16:00:00Z"`）。 |
| `author_display` | string \| null | 可為空 | 虛構作者顯示名稱（可含表情符號）。 |
| `media` | list[dict] \| null | 選填/可為空 | 關聯媒體陣列，每一項目為 `{"ref": "<路徑或URL>", "kind": "video"|"image"|"audio"}`。 |

---

## 4. 目錄結構與檔案清單

```text
tests/fixtures/corpus/
├── README.md                          # 本說明文件（資料格式、授權與再生方式）
├── manifest.json                      # 機器可校驗之完整清單與預期結果
├── make_media.py                      # 媒體檔與超長行產生腳本 (ffmpeg 固定 argv)
├── check_corpus.py                    # 資料集自檢腳本 (Python 標準庫)
├── import/
│   ├── posts_zh.jsonl                 # 繁體中文、Emoji (ZWJ/國旗)、換行、全形字元貼文
│   ├── posts.json                     # JSON 陣列格式貼文
│   ├── posts.csv                      # 含換行引號、Emoji 與公式注入測試之 CSV
│   ├── posts_bom.csv                  # 帶有 UTF-8 BOM 之 CSV 變形
│   ├── notes.txt                      # 純文字筆記匯入
│   ├── comments_tree.jsonl            # 多層回覆樹、孤立回覆、自循環、2節點/3節點環狀依賴、重複ID、弱識別
│   ├── comments_mixed_bad.jsonl       # 正常列與壞資料交錯（語法錯、型別錯、缺必填、>1MiB超長欄位、NaN/Inf）
│   ├── media_only.jsonl               # 無文字純媒體貼文
│   └── project_b_comments.jsonl       # 專案 B 測試資料（父層引用專案 A ID，測試跨專案拒絕）
├── subtitles/
│   ├── valid_zh.vtt                   # WebVTT 繁中字幕（提示樣式、排版標籤、多行）
│   ├── valid.srt                      # 標準 SubRip SRT 字幕
│   ├── overlap.vtt                    # 合理時間重疊字幕
│   ├── end_before_start.srt           # end < start 錯誤字幕
│   ├── negative_time.vtt              # 負數時間戳錯誤字幕
│   ├── malformed_timestamp.vtt        # 時間戳格式損毀字幕
│   ├── header_only.vtt                # 僅有標頭無提示之空字幕
│   ├── html_disguised.vtt             # 偽裝成 .vtt 之 HTML 網頁
│   └── bom_crlf.srt                   # UTF-8 BOM + CRLF 換行之 SRT
├── media/
│   ├── tiny.mp4                       # 1秒迷你 MP4 影音 (testsrc + sine)
│   ├── tiny.wav                       # 1秒迷你 WAV 音訊 (sine 440Hz PCM)
│   ├── tiny.png                       # 16x16 迷你 PNG 圖片
│   ├── truncated.mp4                  # 損毀截斷之 MP4（切半）
│   ├── zero_bytes.mp4                 # 0 位元組空檔案
│   ├── html_as.mp4                    # 偽裝成 .mp4 之 HTML 內容
│   ├── same_bytes_a.mp4               # 與 tiny.mp4 完全相同 bytes 之複本 A
│   └── same_bytes_b.mp4               # 與 tiny.mp4 完全相同 bytes 之複本 B
├── security/
│   ├── prompt_injection.jsonl         # 提示注入防禦測試（嘗試覆寫指令、獲取權限、偷竊 Cookie、執行指令）
│   ├── secrets.jsonl                  # 假金鑰防外洩遮蔽測試（AWS 簽名、Access Token、Bearer、Cookie、API Key）
│   ├── urls.jsonl                     # URL 與 SSRF 安全檢查測試（Loopback, Private, IPv6, Metadata, Punycode, Ports）
│   └── paths.jsonl                    # 路徑與參數注入安全測試（Traversal, Windows, NUL, Option Flag, Symlink）
└── search/
    └── search_corpus.jsonl            # 260 則繁體中文項目（專案A: 230, 專案B: 30，具精確命中計數與分頁）
```

---

## 5. 驗收標準對應矩陣 (Acceptance Test Mapping)

| 驗收 ID | 驗收情境 | 對應測試檔案 | 預期檢驗重點 |
|---|---|---|---|
| **AT-002** | 保真匯入 | `import/posts_zh.jsonl`<br>`import/posts.csv`<br>`import/notes.txt` | 繁中、換行、ZWJ 表情符號、國旗 Emoji、全形標點及未知 ID 保留。 |
| **AT-003** | 部分壞資料 | `import/comments_mixed_bad.jsonl` | 壞列逐項拒絕，正常行順利解析（7 筆有效、7 筆異常）。 |
| **AT-004** | 回覆樹 | `import/comments_tree.jsonl`<br>`import/project_b_comments.jsonl` | 正確建立階層；缺父層 unresolved；拒絕自循環、2節點/3節點循環與跨專案父層引用。 |
| **AT-005** | 字幕時間軸 | `subtitles/*.vtt`<br>`subtitles/*.srt` | `end < start` 與負數時間拒絕；合理重疊保留並標記；BOM/CRLF 正確解析。 |
| **AT-006** | 媒體檔驗證 | `media/truncated.mp4`<br>`media/zero_bytes.mp4`<br>`media/html_as.mp4` | 零位元組、截斷損毀或非媒體格式檔案不標記為 ready。 |
| **AT-007** | 多來源同素材 | `media/same_bytes_a.mp4`<br>`media/same_bytes_b.mp4` | 相同 SHA-256 位元組共用儲存，但保留各自相異之貼文與來源關聯。 |
| **AT-013** | 版本追加 | `import/comments_tree.jsonl` | 同一 native ID 內容修訂時追加 revision，不覆蓋舊有紀錄。 |
| **AT-014** | 中文搜尋與分頁 | `search/search_corpus.jsonl` | 260 筆資料分屬專案 A (230) 與專案 B (30)；分頁 50 筆無縫銜接無漏重；繁簡不自動轉換。 |
| **AT-017** | 來源保真 | `import/posts_zh.jsonl` | 保留真實取得方式，不將自動擷取誤稱為手動。 |
| **AT-025** | 離線匯出安全 | `import/posts.csv`<br>`import/posts_bom.csv` | 試算表公式注入符號安全跳脫；CSV 正確處理多行與 BOM。 |
| **AT-028** | 秘密防漏 | `security/secrets.jsonl` | 識別並過濾 URL token、簽署網址、授權標頭、Cookie 及 API Key。 |
| **AT-030** | URL / SSRF 防護 | `security/urls.jsonl` | 拒絕 loopback, RFC1918, 云端 metadata, CGNAT, 特殊編碼 IP 與高危端口。 |
| **AT-031** | 路徑與指令注入 | `security/paths.jsonl` | 拒絕路徑穿越 `../`、根目錄外絕對路徑、NUL 位元組、參數型檔名 `--exec`。 |
| **AT-032** | 提示詞注入 | `security/prompt_injection.jsonl` | 來源文字一律視為中立資料，不執行提權或腳本指令。 |
| **AT-041** | 限制與空資料 | `subtitles/header_only.vtt`<br>`subtitles/html_disguised.vtt`<br>`import/media_only.jsonl` | 區分正常空資料、權限拒絕、網頁偽裝與格式損毀。 |

---

## 6. 如何重新產生媒體與超長測試列 (Regeneration)

媒體檔與超長文字測試資料可透過 `make_media.py` 重新產生。該腳本完全採用系統 `ffmpeg` 與固定參數列表（不透過 shell 執行）：

```bash
python3 tests/fixtures/corpus/make_media.py
```

執行後將會：
1. 產生 8 個測試媒體檔案（每個檔案小於 64 KiB，總大小小於 400 KiB）。
2. 自動於 `import/comments_mixed_bad.jsonl` 第 10 行產生超過 1 MiB 限制之超長文字欄位。

---

## 7. 驗證資料集完整性 (Verification)

本資料集附帶自驗腳本 `check_corpus.py`（使用 Python 標準庫，無第三方依賴）：

```bash
python3 tests/fixtures/corpus/check_corpus.py
```

該腳本將完整檢驗：
1. `manifest.json` 所列所有檔案皆存在於磁碟上，且磁碟上所有檔案皆已登錄於清單。
2. 每個檔案之 SHA-256 雜湊值與檔案大小完全吻合。
3. 所有 JSONL 檔案之有效／無效列數與錯誤代碼完全符合預期。
4. 搜尋資料集之 260 筆資料分佈與 8 組精確查詢命中計數（含繁簡分離與分頁）完全吻合。
5. 字幕時間軸、重疊標記、媒體檔大小上限 (<64 KiB / 總計 <400 KiB) 及安全 URL/Path 判定全數正確。
