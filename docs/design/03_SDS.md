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
| workspace_keys | private name/version/key_material/created_at/status；active key及保留的舊verification版本。 |

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
reference. Store initialization provisions a private versioned workspace key before read-only search.
Active/retired keys survive backup/restore, and legacy receipt keys are imported once for
existing cursor verification. Pre-release schema 001 is amended without a version bump;
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
