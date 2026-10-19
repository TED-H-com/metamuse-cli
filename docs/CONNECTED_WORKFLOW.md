# 接上帳號，把資料一路整理成貼文

你可以把指定貼文與留言收進專案，選好資料問 Muse、繼續追問，再保存附來源的草稿，
最後核對全文並發到你管理的 Facebook 粉專。這些步驟共用同一份工作區，不用搬 CSV。

程式已提供這條流程；完整串接目前使用虛構資料驗證。真實帳號的連線、收集與發布
尚未完成端到端驗收。下面的連線指令只檢查本機檔案與設定，不代表已登入成功。

## 先準備自己的工作區

已從 PyPI 安裝的人，不需要下載原始碼或切換到專案目錄。先選擇需要的功能：

**版本提醒：** 以下指令適用於 `0.1.0a2`，可安裝版本以
[PyPI 清單](https://pypi.org/project/metamuse-cli/#history)為準。`0.1.0a2` 包含
Muse 連線／請求格式及粉專貼文讀回修正；`0.1.0a1` 不含這些修正。
真實測試跨修正版執行，不要把安裝或設定成功當作完整流程已通過。驗證範圍見
[Muse spike 紀錄](dev/MUSE_SPIKE.md)。

```bash
# 本機檔案、Muse 對話與粉專文字發布
uv tool install --python 3.12.13 'metamuse-cli==0.1.0a2'

# 另外需要 Facebook 收集與遠端影音時，改用這行安裝或更新同一個工具
uv tool install --python 3.12.13 'metamuse-cli[facebook,remote]==0.1.0a2'
```

只需要其中一種收集功能時，可將 `[facebook,remote]` 改成 `[facebook]` 或 `[remote]`。
只有 Facebook 收集需要 Chromium；安裝與套件相同版本的瀏覽器：

```bash
uv tool run --python 3.12.13 --from 'playwright==1.63.0' playwright install chromium
```

這些是 [uv 的工具安裝指令](https://docs.astral.sh/uv/guides/tools/)，不依賴目前目錄。
若終端機找不到 `metamuse`，執行 `uv tool update-shell` 後重新開啟終端機。

工作區要放在 Git 專案外；後續都使用同一個路徑：

```bash
export WORKSPACE="$HOME/.local/share/metamuse/my-content"
metamuse --workspace "$WORKSPACE" init
```

開發者若已經有 checkout，也可以從專案目錄執行：

```bash
export WORKSPACE="$HOME/.local/share/metamuse/my-content"
uv sync --locked --extra facebook --extra remote
uv run metamuse --workspace "$WORKSPACE" init
uv run playwright install chromium
```

以下使用已安裝的 `metamuse`；從原始碼執行時，請在它前面加上 `uv run`。

## 只接上你需要的帳號

把憑證檔放在 repo 外、限制成只有自己可讀，例如 `chmod 600 /private/path/file`。
不要把 Cookie 或權杖貼給 AI，也不要直接放進指令參數。

**讀取 Facebook 貼文：** 準備你指定的 Playwright storage-state JSON。
`connect` 會匯入它、綁定帳號，並啟用單篇貼文讀取；不掃描你的瀏覽器資料。

```bash
metamuse --workspace "$WORKSPACE" connect facebook \
  --account my-reader --auth-file /private/path/facebook-state.json --enable-network
```

**問 Muse：** 準備 UTF-8 JSON 連線檔，格式為
`{"format":"cookie-header","cookie_header":"name=value; name2=value2"}`，
其中名稱與值必須來自你自己的有效 Muse 登入資料，示例不能拿來登入。

```bash
metamuse --workspace "$WORKSPACE" connect muse \
  --account my-muse --auth-file /private/path/muse.json --enable-network
```

**發到粉專：** 準備該粉專的原始 Page token 純文字檔，不含 JSON、標頭或換行。
指定你管理的粉專 ID，以及你的 Meta app 可用的 Graph API 版本。

```bash
metamuse --workspace "$WORKSPACE" connect publishing \
  --account my-page --auth-file /private/path/page-token.txt \
  --page YOUR_PAGE_ID --api-version YOUR_API_VERSION --enable-network
```

`YOUR_PAGE_ID` 與 `YOUR_API_VERSION` 必須換成實際值；版本格式如 `v25.0`，
這只是格式示例。粉專權限與限制見[發布說明](dev/PUBLISHING.md)。

`--enable-network` 明確允許工作區使用網路；省略時保留原本的網路設定，預設離線。
連線指令不讀取遠端、不分享資料、不建立發文批准。每次動作仍要通過自己的授權。
重新連線會建立新的私人憑證參照；舊參照不會自動撤銷，以免中斷既有討論或回執對帳。
用 `auth list` 檢查綁定狀態，不再需要的參照可用 `auth revoke --auth-ref REF` 撤銷。

檢查本機缺少什麼：

```bash
metamuse --workspace "$WORKSPACE" doctor
```

請分清楚目前到哪一步：

| 狀態 | 可以根據什麼判斷 |
|---|---|
| 已設定 | `connect` 成功，回傳 `configured: true`、`account_verified: false`。憑證格式與帳號綁定已保存。 |
| 已確認能連線 | 獲准的遠端工作完成登入及服務通訊；只有 `connect` 或離線 `doctor` 不算。 |
| 已完成指定工作 | 檢查該 job 的完成狀態與實際內容，例如讀到的 Muse 回答、收集範圍或粉專回執；部分結果仍須保留缺漏。 |

目前沒有單獨的遠端登入驗證指令；`doctor` 不檢查遠端登入是否有效。
Facebook 需要可用的 Chromium sandbox；若啟動受阻，
依 [Facebook 設定指南](dev/FACEBOOK.md#browser-sandbox-is-required)處理。
如果你的助理已連上 MCP，帳號設定完成後重新啟動該 MCP 連線。

## 從一篇貼文開始

將 [MetaMuse 接到你的 AI 助理](dev/SKILL.md)後，可以直接這樣交代：

> 請把我指定且有權讀取的這篇 Facebook 貼文與留言收進 MetaMuse。
> 先顯示網址、帳號、讀取範圍與上限，等我批准後再讀取。
> 找出原始重點，選成資料包給我看，再問 Muse 並追問一次。
> 最後寫一份附原文與兩輪討論引用的 Facebook 文字草稿，讓我核對。
> 我另外批准全文與粉專後才發布，並把回執給我。

助理依序使用既有的收集、搜尋、packet、Muse、草稿與 publication 工具。
沒有批准時，工具回傳 `approval` 預覽，不會先建立收集工作或外送。
助理可以把預覽中的 `request` 存成 `request_file` 指定的私人檔案。
**由你**核對 `next_action` 指令、在自己的終端機執行 `permit approve`，
再把回傳的 permit ID 交給助理繼續。同一份收集批准不會同時批准分享或發文。

只想用 CLI 收集時，先建立專案，使用回傳的專案 ID 與實際單篇網址：

```bash
metamuse --workspace "$WORKSPACE" --json project create --name "活動回顧" --request-key project-1
metamuse --workspace "$WORKSPACE" --json add \
  --project PROJECT_ID --url POST_URL --resources post,comments --request-key collect-1
```

第一次會回傳授權預覽。由你批准後，使用同一組參數加上
`--access-grant PERMIT_ID --run`。不加 `--run` 只會排隊，需另外啟動 worker。
MCP 預設附帶 worker；停止 MCP 後不保證背景繼續執行。

Facebook 讀取只接受單篇 permalink 或 `/share/p/` 貼文連結，不讀整個帳號或社團。
遠端影片與字幕沿用[影音收集設定](dev/COLLECT.md)，需要指定可用的 extractor
與可連線的主機範圍；貼文讀取與影片下載是不同操作。

發布第一版只接受粉專的新文字貼文。送達不確定時，先對帳原始回執，不能換 key 重送。
逐步發布指令與讀回方式見[發布說明](dev/PUBLISHING.md)。
