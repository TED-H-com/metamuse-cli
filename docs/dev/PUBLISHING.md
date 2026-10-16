# 把已核對的文字草稿發到 Facebook 粉專

這個實驗功能讓你預覽已保存的草稿、確認要發到哪個粉專，再由你批准送出。
送出後會保存貼文回執，並回讀核對文字與粉專身分。目前只有虛構資料測試，
尚未完成真實帳號驗收。這不是已驗證的正式發布服務。

第一版只接受 **Facebook 粉專的新文字貼文**。個人動態、社團、留言回覆、圖片、
影片及草稿變體會直接拒絕。其他平台、排程、批次發布與刪除貼文尚未提供。
每個目的地有自己的結果；沒有跨平台原子操作或自動回滾。

## 準備粉專與權杖

你需要自己管理的粉專、該粉專可用的 Page access token，以及你的 Meta app
目前可用的 Graph API 版本與讀寫權限。請在你自己的 Meta developer 帳號確認，
並參考 [Pages API 文件](https://developers.facebook.com/docs/pages-api/posts/)。
本工具不替你建立 Meta app、取得平台核准或擴大帳號權限。

把原始 Page token 存到 repo 外的私人檔案，限制為只有你可讀，檔案內不要有
JSON、標頭或換行。由你在終端機匯入；不要把 token 貼給 AI 助理或放在指令參數：

```bash
metamuse --workspace "$WORKSPACE" --json auth import \
  --file /private/path/page-token.txt --provider facebook-pages --account my-page
```

保留回傳的 `auth_ref`。編輯同一個 workspace 的 `metamuse.toml`，開啟
`[app]` 的 `network_mode = "live"`，並設定既有的 `[publishing]` 區塊：

```toml
[publishing]
enabled = true
backend = "live"
account = "my-page"
page_id = "123456"
auth_ref = "auth_00000000000000000000000000000000"
api_version = "v25.0"
```

以上 ID、auth_ref 與版本都是示例，必須換成你自己的設定。`v25.0` **不表示最新
或已實測可用的版本**；此工具要求你明確選擇，沒有默認版本。未設定或仍離線時
不會發文。不要把這份私人設定提交到 Git。

## 預覽、批准、送出

先依[入門說明](../GETTING_STARTED.md)保存草稿，記下 `project_id`、`draft_id`
與目前版本。以下 `$PROJECT`、`$DRAFT` 都使用實際回傳的 ID：

```bash
metamuse --workspace "$WORKSPACE" --json publication preview \
  --project "$PROJECT" --draft "$DRAFT" --revision 1 \
  --account my-page --destination 123456
```

核對 `data.text`、`data.destination` 和 `data.approval`。預覽完全不觸網。
將 `data.approval.request` 的 JSON 存成 `data.approval.request_file` 指定的私人檔案。
由你執行該預覽的 `next_action` 所示 `permit approve` 指令；它會重新核對
草稿、顯示全文／粉專／帳號／有效期限，再要求終端機確認或精確的 digest。
AI 助理可以準備草稿和預覽，**不得自行執行授權指令**。

批准預設 24 小時到期，僅可使用一次；可明示 `--expires-at` 縮短期限。
若草稿已更新或來源變更使草稿過期，必須重新預覽。取得批准回傳的 permit ID 後：

```bash
metamuse --workspace "$WORKSPACE" --json publication submit \
  --project "$PROJECT" --draft "$DRAFT" --revision 1 \
  --account my-page --destination 123456 \
  --publication-grant "$PERMIT" --request-key post-1 --run
```

`--run` 使用同一個共用 worker 執行；不加它只會持久保存排隊工作。`accepted`
表示已排隊，不表示背景一定有人執行。保存回傳的 `publication_id` 和 `job_id`：

```bash
metamuse --workspace "$WORKSPACE" --json publication get "$PUBLICATION"
metamuse --workspace "$WORKSPACE" --json jobs get "$JOB"
```

只有 `state = "sent_verified"` 才表示取得回執，且回讀的貼文 ID、粉專 ID、
原文與已發布狀態都吻合。fixture 的這個狀態只代表虛構測試結果。
發布程式不做改寫、換行正規化或靜默刪除附件；平台可能改寫文字，造成需要人工核對。
平台長度限制尚未以真實帳號驗收，不硬編未確認的字數。

## 送達不確定時

`needs_reconcile` 或 `submitting` 表示可能已發出。不要換 request key、草稿版本、
批准或 attempt ID 重送。同一草稿／帳號／粉專的未結算發布會阻擋新版本。
已成功的同一版本也不會因為換 key 而再發一次；成功後的新版本可以另行預覽與批准。

有保存回執時，可請工具準備 `publication reconcile` 請求，再由你另外批准
`publication.reconcile` 的精確讀取操作，使用回傳的新 permit：

```bash
metamuse --workspace "$WORKSPACE" --json publication reconcile "$PUBLICATION" \
  --publication-grant "$READ_PERMIT" --request-key check-post-1 --run
```

這只回讀原始回執，會結算原本不確定的發文工作，不重送。若 POST 沒有留下回執，
此版會保持不確定；沒有搜尋結果不代表沒有發文，也不提供猜測貼文 ID／任意網址
來解除保護的功能。需要操作者到平台核對並等待後續受控的人工對帳支援。

送出前就被拒絕、且 durable attempt 尚未建立時，紀錄會顯示 `not_submitted`。
修正設定或重新取得批准後，可用新 request key 再排隊。這不適用於已進入發送邊界的操作。
更換帳號、Page、API 版本、backend 或 auth_ref 會使原批准失效；原回執讀取也必須
保留原設定，不能靜默換帳號對帳。

## Application、MCP 與資料庫

CLI／MCP 共用 `app.preview_publication`、`submit_publication`、`get_publication`、
`reconcile_publication`。四個 MCP 工具為 `metamuse_publication_preview`、
`metamuse_publication_submit`、`metamuse_publication_get`、`metamuse_publication_reconcile`。
沒有 MCP 授權工具；`approved: true` 等額外欄位拒絕。

能力檢查會讀取磁碟中的目前設定，只有已啟用且完整綁定的預覽才標為可用；
缺少設定時預覽仍是本地功能，但 `permission=not_configured`、`effective=false`。
本地 `publication get` 不受目前發布設定影響。送出及對帳沒有精確請求的批准時
保持不可用；fixture 不需要帳號憑證，但也不因此取得送出批准。
這些檢查不讀 token 或探測平台，不代表 LIVE 已驗證。

Migration 007 新增 `publications` 與 `publication_attempts`。publication 保存精確
文字／草稿版本／目的地／帳號／backend／API 版本／附件空集合與 digest。
同一 publication 最多一個已進入提交的 attempt，並以 SQLite 唯一約束保護
跨版本的未結算草稿／帳號／目的地。只有證明尚未進入 submitting 的失敗可重新排隊；
舊工作不能沿用新的綁定。升級仍使用既有備份及 guarded migration。

發送前以 Page token 的 `/me?fields=id` 驗證粉專身分。提交邊界在同一個 transaction
檢查現行草稿及批准的期限／撤銷／用途，消耗一次批准，新增 attempt，再 commit
`submitting`，接著只送一次。HTTP 不在 SQLite transaction 內。receipt 先落地再回讀。
重啟不會重新提交外部步驟，對帳透過 `Executor.settle_uncertain` 結算原始工作。
本地 transaction 無法消除 commit 與遠端網路操作之間的時間差。

Graph 只能連 `https://graph.facebook.com/{operator-version}/...`，使用既有鎖定
`curl-cffi==0.16.3` HTTPS adapter：公用 DNS 位址驗證、固定 DNS 結果、TLS／hostname
驗證、驗證連線 peer、禁止 redirect 與環境 proxy。token 只放 Authorization header，
不進 URL、工具參數、event 或錯誤。回應上限 64 KiB，逾時有界；POST 不重試。

## 驗證與參考

`tests/test_publishing.py` 使用注入的虛構 gateway 與 HTTP responses，沒有真實網路或帳號。
其驗證類別是 OFFLINE／FIXTURE／INTEGRATION，不是 LIVE。AT-047／048 的離線部分
可驗證；B20／AT-049 真實粉專驗收仍 blocked，AT-050 跨平台部分成功尚無對應平台實作。

API 介面參考 Meta 官方
[PageFeedPost.py](https://github.com/facebook/facebook-python-business-sdk/blob/efd8423a2e595ea8d4c04eb824ce113f2f1d68cd/examples/PageFeedPost.py)、
[Page.create_feed](https://github.com/facebook/facebook-python-business-sdk/blob/efd8423a2e595ea8d4c04eb824ce113f2f1d68cd/facebook_business/adobjects/page.py)
和 [Post fields](https://github.com/facebook/facebook-python-business-sdk/blob/efd8423a2e595ea8d4c04eb824ce113f2f1d68cd/facebook_business/adobjects/post.py)，
固定 commit `efd8423a2e595ea8d4c04eb824ce113f2f1d68cd`，2026-10-16 查閱。
只查 API 名稱與欄位，未複製或翻寫 SDK 程式碼、未採用其授權；新增程式為本專案實作。
Meta developer Pages 文檔此次讀取受限，不能據此保證最新版本、權限或 app review 可用。
