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

B19 additionally exposes preview_publication, submit_publication, get_publication and
reconcile_publication through the same app layer. These four P1 functions do not add a
second worker, store, configuration or orchestration loop.

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

Live database／WAL／SHM在open前拒絕symlink、hardlink及非regular file。
SQLite最後一個connection關閉時可unlink sidecar；racing lstat也可能回regular inode但
st_nlink=0。僅此WAL／SHM情況對current pathname作一次strict recheck：已消失可繼續，
替換成link或非regular仍拒絕。database與archive不接受zero-link inode；第二次stat再race
仍可拒絕open，不循環重試，不replay transaction／job／external action。

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
| transcript_segments | project、可空 asset、可空 item/source、cue_index、overlap、start_ms/end_ms、text、language、method、review、removed（default false；移除列text空、revision≥2）；end≥start≥0。至少要有 asset、item 或 source 之一。相同 bytes 的字幕不跨 project 可見。 |
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

native IDs為opaque字串，不自行解碼。可靠ID用(platform,scope,id)識別；無ID保持弱識別，不以文字hash假裝同留言。parent不得自我／循環。revision追加，不原地抹掉舊來源；修正同transaction標記派生stale。移除cue追加removed=true/text=""的較新revision，需同id/project的前一文字列，拒絕double removal；舊文字明示引用仍可讀且stale，移除列不能解析為證據。

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
B16a extends the shared name-based redactor for bare session-cookie pairs, JSON
cookie headers and query separators encoded through three layers. Packet digests and
new bundle members use the redacted text; Muse checks questions before approval previews
and constructs bodies from checked questions plus redacted packets. The AT-042 bare-cookie
bundle regression is a normal passing test. Auth list/revoke are human-only private-file
operations; resolution errors leave no cause/context chain holding corrupt file contents.

## 9. Grant與packet

Grant由可信人類本地流程建立：kind、project/provider/account、可用snapshot或packet digests、expiry、max_uses、issuer。MCP無自我批准工具。對已展示snapshot可預先授權有界討論，不必每句打斷；新增來源／帳號不繼承。

packet從明確field selection生成，保留來源方法與限制；不帶headers、Cookie、token、localpath或不必要個資。自動redaction只是防呆，不能保證所有私密文字已識別。

送出前重驗source可分享、revision、grant有效性、question內容範圍和bytes。無法自動證明文本合法就由操作者／host負責明示輸入，不給假的合規標章。隱去姓名仍可能由URL或原文識別，必要時只傳聚合／摘錄或不分享。

## 10. 草稿、發布與快照

Draft是host提供的文字。revision避免覆蓋；共同正文和platform variant有關聯。沒有策略LLM在save背後。檢查字元／bytes算法要說明，平台限制記來源與驗證日期，未知就warning，不硬編不可靠數字。

Export先staging再manifest/hash/ready。引用不存在、跨project或已刪除不成功；引用存在仍需host確認語義支持。

P1 Action綁kind/platform/account/destination/parent、draft revision、assets、digest、grant和期限。變任何一項都需新批准。提交前驗真實帳號/target並寫attempt(submitting)，然後只送一次。

平台receipt或回讀才sent_verified。按過按鈕不算成功，timeout進needs_reconcile；換attempt ID不能繞防重發。沒看到貼文不是沒發出的充分證據。跨平台部分成功分別記，不預設刪成功貼文rollback。

B19 implementation: migration 007 adds immutable publications (exact target/payload)
and publication_attempts (one entered attempt per publication). A partial unique index
quarantines unresolved draft/account/destination across revisions. Proven pre-boundary
failure with no attempt may be explicitly requeued with a new grant and job; an entered
attempt forbids this path. The existing external-action transaction consumes the exact
publication Permit, inserts the attempt and commits submitting before a single send.
Receipts are saved immediately before exact read-back. A fixed, optional database-only
Operation.on_finish callback runs in the existing executor finalization transaction,
including settle_uncertain; publication sent_verified and original job/step success are
atomic. A callback failure rolls back both and retains uncertainty, never another send.
All previous operations default to no callback. Reconcile verifies remote evidence
outside SQLite, then marks the original proof checkpoint and settles that original step.
Missing receipts cannot be inferred from text search or caller-supplied post URLs.

The built-in Graph provider accepts only an explicit Page binding and API version;
uses /me to check Page identity before sending; uses existing locked curl-cffi HTTPS
transport with public DNS pinning, TLS/hostname/peer verification, no redirects/proxy,
bounded DNS wait/HTTP timeout and 64 KiB responses. Every request rechecks current
network/config/credential state. Token bytes stay only in Authorization headers; fixed
errors omit transport exception text and response bodies. No platform SDK source is
adopted. Offline fixtures do not establish B20 LIVE permission or platform availability.

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
transcript revisions, including removal revisions, mark directly derived records stale; B07/B11 also checks transitive
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

## Bounded research on existing Permits (B08/B09/B10)

`safety.AccessScope` / `ShareScope` accept an optional strict `research` JSON binding;
legacy exact scopes retain their validation. Access research permits only muse.open
and muse.read with the one exact open action digest; share research requires exactly
one packet digest and rejects snapshots. The binding stores deterministic discussion
ID, topic, original open request key/digest, backend, packet ID/digest/selection digest,
rounds and read ceiling. Project/provider/account remain the existing Permit columns.
No core models, SQL schema or migration changes are needed.

`_muse_research` recomputes a human review and its canonical approval digest before
write ownership. A private, process-owned immutable preflight binds exact validated
review/configuration/packet bytes and open-request state. Inside one Store.transaction,
creation rechecks raw inputs, current configuration, packet/hash/selection and source
revisions without redaction, sanitization or privacy preparation, then issues the pair
with Store.insert, events and mutate_once. A changed input refuses atomically; serialized
review data or a caller prechecked flag cannot substitute for this internal handoff.
It never nests create_permit transactions. The receipt operation
is permit.research; it identifies both existing Permit IDs, not a second grant store.
The review includes absolute expiry, one open plus the finite read ceiling, question
ceiling and explicit consent to host-authored question variation. CLI uses the existing
quoted/escaped terminal review and terminal yes or matching digest. MCP creates no grants.
Manual human permit create validates this scope against current bindings and limits too.
Preparation delegates topic checking to prepare_open and checks the fixed packet against
the current preview redaction fixed point before review and in the issuance preflight.
Manual create_permit uses prepare_creation before its writer and validate_creation with
that process-owned snapshot inside it. Existing manual receipts replay current Permit
state without new privacy work or renewal; a replay that becomes new creation fails
closed without a snapshot. Research verifier wire reconstruction uses only pure builders.
Question checking remains in the shared lifecycle submit/pre-send boundaries.

Research queue checks run against persisted discussion/exchange after insertion in the
same queue transaction, with rollback on denial. Read queue and all reservation checks
revalidate stored packet bytes/hash, exact selection/current revisions, discussion/open
identity and prepared wire reconstructed from durable fields. Configuration is reloaded
at verification. The existing conditional max_uses update and use_reserved events enforce
concurrent round/read consumption. Access max_uses is reads + one open; existing reservation
events additionally enforce the separate remote-read ceiling even if an old exact grant
paid for the open. Unknown-open sessions.list is only the existing original-marker
reconciliation route, never a fresh create. Packet changes also invalidate remote reads;
local stored reads remain available for review/reconciliation planning.

Only validated research shares bypass _share_target's extra verbatim-question equality;
old exact mode retains it. PermitUse carries trusted discussion context; legacy reservation
fingerprints exclude the newly optional discussion_id but retain the old packet_id:null
field. Research fingerprints additionally bind discussion_id. No counter reset, reservation
refund, external-submit retry or automatic expiry extension is introduced. Topic meaning
and exclusion of other data from arbitrary free text remain host instructions, explicitly
separate from enforced identity/budget checks. The implemented workflow has synthetic
OFFLINE/FIXTURE coverage; real-account LIVE verification is still pending.

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

B16a privacy guard classifies credentials by name and syntax, never by the value's
alphabet. Keep the existing name list; normalize case, punctuation and up to three
percent-encoding layers. Sensitive suffixes include `sess`, `sessid`, `session`,
`token`, `secret`, `cookie`, `password`, `passwd`, `pwd`, `pass`, `api_key`/`apikey`,
`secret_key`, `access_key` and `private_key`. Whole names include `sid`, `passphrase`,
`auth`, `credentials`, `x-api-key`, `cookie_header` and `Proxy-Authorization`.

Assignment operators are `=`, `:`, `=>`, `＝` and `：`. Values in JSON/TOML/YAML,
XML local-name elements (including namespaces and CDATA), quoted subscript keys,
flags (`--name`, `-name`, `-Dname`), and quoted type annotations with defaults are
covered. Sensitive structured strings, numbers, arrays and objects are redacted
whole. YAML indented containers/block scalars and TOML triple-quoted strings retain
their enclosing extent, including incomplete and escaped documents. HAR name/value
objects follow the sensitive-name rule, and every HAR cookies-array entry is private.

Sensitive JSON scalars and containers use a quoted sentinel at their actual source
layer: raw, percent/form encoded or inside JSON-string wrappers. Proven enclosing
objects supply exact scalar/container ends, including minified public siblings;
a standalone fragment or failed enclosing object cannot shorten an ambiguous value.
A sparse key-quote anchor carries the replacement's JSON/percent escaping back
through the existing offset maps. A covering core structured mask keeps its quoted
replacement even when a shorter additive span starts at the same offset; union
coverage never shrinks. Generic literal/placeholder exemptions remain unchanged.

A proven JSON string may contain a complete percent-encoded JSON container. The
existing HAR lexer inspects that container at its actual percent layer within the
independent three-layer budgets. Structural punctuation must belong to that layer;
mixed-depth punctuation remains string data. This does not turn an encoded HAR
fragment inside unrelated URL data into a new HAR document. Public siblings and
each enclosing JSON representation retain their independent boundaries.
SQL IDENTIFIED BY/WITH PASSWORD, sshpass -p, mysql/mariadb/mysqldump attached -p,
and curl -u identify credential values.

Credential extents follow delimiter ownership D1–D4 below. Unquoted values end
at whitespace, a contextual list separator, or an owned D1/D2 closer. Unowned
quotes/brackets are value data. List separators are &/; in query/form context,
commas in owned/proven call arguments, and ;/, in cookie contexts.
Ambiguous raw assignments extend conservatively rather than exposing a password
suffix. If a consumed value ends in another sensitive assignment name/operator, its
following value is included conservatively, including across a newline. Code equality
comparisons with spaces around the operator are not assignments.

Authorization/Proxy-Authorization with a scheme and following material is private
through end of line or its enclosing string, for every scheme. A bare standard scheme
such as Authorization: Bearer has no credential. Any nonempty Cookie/Set-Cookie value
is private. Outside cookie contexts a chain requires at least two name=value pairs
joined by semicolons and optional spaces; commas, ampersands and spaces alone do not
make a chain. Call arguments and signatures are never chains. Generic members are
private only in chains or Cookie/Set-Cookie, document.cookie, curl -b/--cookie,
Netscape jars and HAR cookies contexts. Sensitive names remain private independently.

Sensitive header ownership includes following CRLF/LF continuation lines indented
with spaces or tabs, including members written as name=value or name: value.
An unindented line starts an independent group. Quoting the first value
does not truncate its continuation group. Proven JSON string extents preserve
independent logical lines and their enclosing quotes, including JSON-in-JSON;
without that proof, a deeper escaped newline is value data under the layer rule.
The same semicolon cookie-chain rule applies at URL path and query boundaries:
two or more joined pairs are private, including through percent/form/JSON views.
A single assignment, a lone semicolon path parameter and ordinary ampersand query
parameters do not become a cookie chain. URL path/query candidates have an
independent forward cursor so an outer form field cannot hide them.

URL passwords in every scheme extend from the first userinfo colon to the last @ in
the WHATWG authority. The authority ends at /, ?, #, whitespace, or a backslash for
special schemes; those raw terminators cannot occur inside a URL password. A candidate
also ends at the next same-layer or shallower scheme:// or an owned D1/D2 quote/bracket at its layer or a
shallower one. Every scheme gets its own scan, even after a candidate without userinfo.
Unenclosed commas, semicolons and brackets remain password data. Sensitive
query values end at &, #, whitespace, encoded ?, or an owned D1/D2 URL quote/bracket;
a literal ? remains data. A decoded delimiter ends a value only at the operator's
layer or a shallower layer; deeper percent/JSON/form-plus characters are value data.
The encoded-? query terminator is the explicit query exception. Original-view spans
are never discarded for containing percent escapes; masks union original and decoded
spans and map to the smallest whole original escape spans.

Only these value exemptions apply, after determining the entire value extent:
empty values; null, None, nil, true, false; one complete <...>, ${...}, $VAR, %VAR%,
{{...}}, {}, {name} or %s placeholder; exact [REDACTED]/REDACTED; three or more *, x
or X; ... or …; the key's own identifier or a dotted attribute whose final segment
matches ignoring case/underscores; os.environ[...], os.getenv(...), process.env.X;
closed type names str, string, bytes, int, bool, Any, None, SecretStr, Optional[...]
and unions with None of those types. Nested ${A:-${B}} uses balanced braces, and
placeholder prefix/suffix lookalikes are credentials. Numeric signs, decimals and
percentages are exempt only for session-family counters. Other calls and changeme
are credentials. Names excluded altogether: pagination *page*token, next*token,
continuation*token, sync*token, cursor; tokenizer pad/eos/bos/unk/sep/cls/mask_token,
max_tokens, max_new_tokens, num_tokens and *_token_id, including dotted attributes.

Self-identifying credentials include bare case-insensitive Bearer with token68 of
at least 16 characters containing a digit or mixed case, or a JWT. Its separators
include whitespace, NBSP, newline, + and encodings. Bare Bearer runs on original text
as well as decoded views. Known prefixes cover GitHub, GitLab, Slack (including
xoxa-2), AWS, Google, Stripe live keys, npm, Hugging Face, OpenAI/Anthropic-style sk
keys (minimum body 8, no upper bound), and JWTs including empty signature segments.
PEM labels are case-insensitive; matching/unterminated PEM and PGP private-key blocks
and PuTTY private-key files are private. "The bearer instrument was signed" is benign.

Canonical percent and JSON-escape views each allow three layers, including JSON
strings containing JSON. Literal + means space only inside each actual URL query,
through that URL's end, or a complete whitespace-free form body of name=value pairs
joined by &. Independent body lines and quoted captured bodies retain their own form
scope. Scopes revealed by canonical decoding follow the same rule: only pluses at
the form operator layer or shallower become spaces. A question mark earlier in prose
never changes a later +. Deeper percent-encoded %2B remains a literal plus. Unaffected original characters are preserved.

One redact_text call applies the single pass at most four times: three changing
applications and one confirming application. For every input without
`redaction_unstable`, `redact_text(redact_text(x)[0])[0] == redact_text(x)[0]`.
A fourth changing application adds redaction_unstable and replaces the complete
output with [REDACTED], so even consumers that discard finding metadata fail closed.
Packets and bundles refuse that finding with fixed E_INPUT_INVALID "Packet redaction
did not stabilize" before hashing or any write. Muse topics/questions refuse it;
nonrefusable logs/details/displays use the whole mask. There is one shared bound,
not a separate packet backstop. Counts expose only rule class and count.

New Muse topics/questions refuse any finding except local_path with fixed
E_INPUT_INVALID, never echoing a value, its length or offset. Accepted text is sent
and previewed byte-for-byte. Prose mentioning names without assignment syntax,
closed code references, pagination/vocabulary, /docs/plan.md and /etc/hosts are benign.
By design "Password: required", "Secret: the meeting moved", "Session: Opening keynote"
and "Authorization: Basic or Bearer?" are refused. Rephrase, e.g. "the password is
required". Credentials cannot create a new approval preview.

Out of scope: .netrc/.pgpass need separate positional file grammars; token-only URL
userinfo has no password delimiter (known prefixes still apply); localized names
such as 密碼 need an explicitly maintained language policy. These omissions do not
establish that a document is public.

B16a submit/prepare order is a read-only exact-payload receipt replay, the credential
check on new text, then preview/grant checks, persistence and work. Exact replay keeps
its original result after a rule upgrade. The worker checks session.start topics,
questions, packet fixpoints and body equality again before any token or gateway.
Frozen refusals prove not_submitted, close sends as abandoned and retain the refusal's
own next action. No refused send quarantines a discussion as unknown delivery.

Stored discussion titles are never re-checked as new outbound text. Previews show a
display-redacted "Stored discussion title", outside the send/read body. Reconciliation
identifies a discussion by discussion_id and project; its request topic must equal
the stored title or its current display-redacted form. approval.request and saved
request files carry only that display-redacted title. If the display changed since
saving, E_REFERENCE_INVALID asks to request reconciliation again. Approval failures
propagate their real fixed code, message and next action.

Muse sends exactly stored packet.rendered_text. Current rules changing an older
packet cause E_REFERENCE_INVALID "Packet is stale; prepare a new packet" in prepare,
preview, submit and wire, without a preview or rewriting stored bytes. Send guidance
is "Prepare a new packet and send request with a new exact share grant". The body is
the accepted original question, two newlines and that packet. Private original
exports retain original_private and their documented original bytes.

Redaction uses bounded possessive name grammars, forward scans and precomputed
quote/container/tag terminators. Scans advance past consumed values; a fixed number
of ordered interval streams merge and each pass builds output with one join.
Original-offset maps are sparse/coalesced; changed canonical views additionally use
one byte of decoding depth per character. The four-application bound is constant.
CPU growth of the iterated public call is checked at four doubling sizes for all
review and new-rule families. Raw canonical sizes are checked before rendering;
packet/bundle rendering and redaction precede BEGIN IMMEDIATE. Writes repeat only
reference/snapshot checks. _save_send redacts the full remote detail (capped at 1 MiB)
before its batch, then cuts to 512 characters. Association computes its sanitized outbound view before opening the write
transaction. Settlement verifies the complete stored outbound body is unchanged,
then reuses that view for echo comparison. Neither redact_text nor sanitize_document
runs under the write lock. Oversized legacy text refuses before redaction with
fixed E_LIMIT_REACHED; this bound does not prove delivery.

Auth list returns partial for corrupt entries: valid metadata in references, opaque
refs and fixed reasons in unavailable. It suggests working revoke commands for
regular records/symlinks, and a fixed manual instruction for FIFO/directory/unowned
entries. When --workspace was given, revoke suggestions retain it, shell quoted.
Revoke unlinks a symlink via dir_fd without opening its target; other non-regular
entries require manual cleanup. List/revoke read existing config for config_bound
without opening or initializing a workspace/Store. Noninteractive auth/permit
commands refuse before opening anything; scripted human permit approval needs no
TTY. Auth values and resolved secret-target paths never appear; explicitly supplied
workspace paths remain in the requested operator command. Revoked bindings ask to
import and rebind.

Quote and bracket ownership is evaluated independently at every decoding layer,
including inside an outer string. Quoted arguments own their opening quote at
its layer, and only a D2-valid matching closer can shorten them. Spaces inside
remain value data.
Exempt environment lookups end at their own closing bracket or parenthesis.

HAR structure is read by an escape-aware JSON lexer at its JSON layer, before
percent or JSON escapes inside its string values become data in canonical views.
Complete documents and whole JSON-string layers share the lexer. Partial documents
stop at the first syntax error within each candidate; independent later candidates remain eligible. Name/value order
is immaterial; sensitive HAR scalars, including numbers and boolean/null literals,
are redacted whole, as are cookies arrays.

Reconciliation preparation and receipt replay use one topic-identity predicate:
the stored title or its current display-redacted form, together with discussion ID
and project. Replaying the human-approved request, before or after settlement, and
re-previewing an existing job retain the original job identity. A saved display title
that no longer matches current rules requires requesting reconciliation again.

PuTTY private-key headers match after a non-identifier boundary at every layer,
including inline labels and JSON/form captures. The mask extends through Private-MAC
or Private-Hash; without either line, to its proven JSON string end or text end. Raw, JSON-escaped
and percent-encoded line breaks share this behaviour. PEM and PGP begin markers
have no line-start anchor; their existing matching-end/unterminated rule remains.

A whole-value exemption applies to that outer value only. Assignment scanning
continues after its key/operator to inspect nested assignments. Balanced shell
placeholder extents use the precomputed delimiter index, avoiding repeated suffix
parsing when defaults nest.

Redaction memory budget: a fresh subprocess making one public redact_text call
on 20 MiB of raw text must peak at no more than 1 GiB RSS. Syntax positions share
one lazy signed numeric index; quote intervals, newline offsets, edit coordinates
JSON lexer nesting, and retained mask spans use compact numeric arrays; decode depths use bytes.
Decoded syntax is released before the original-view syntax is constructed. Each
measurement uses a fresh parent and child; RUSAGE_CHILDREN reports that child only.

Layered grammar details: a bracket or separator inside a quoted value cannot close
an outer container or turn into a call-argument separator. Only an owned, same-layer D2-valid closer
can shorten that value; unrelated or deeper-layer quotes are data.
Raw replay recognizes the whole escaped closing quote, preserving whole-value
exemptions inside JSON captures. Shell quoting of option names, including curl
--user/--cookie, sshpass -p and attached mysql -p, retains the argument rule.
A whole form field is decoded before lexing a HAR document it contains, including
whole percent-encoded form bodies. The HAR lexer never decodes string data into
structure. A failed container stops at its first error; independently indexed later containers remain eligible, even after prose or a damaged outer candidate.

The generated matrix labels these grammar exclusions explicitly:
`literal_syntax_terminator` means an unescaped quote, whitespace or contextual list
separator has ended the value; the following text is outside that credential.
`literal_url_terminator` means a raw /, ?, # or special-scheme backslash has ended
the URL authority. `encoded_query_question_terminator` is the documented encoded-?
query exception. These do not exempt an encoded delimiter deeper than a form
assignment. Unenclosed port/email lists that are syntactically URL userinfo retain
the credential interpretation; use enclosing JSON/string boundaries for a list.

Known form boundaries are carried through decoded layers, including encoded newlines.
An encoded nested form prefix gets its own operator layer within its parent field;
its plus signs decode only at that layer or shallower. The scan never grants form
semantics to unrelated prose after a question mark. Whole escaped quoted placeholders
are canonicalized before applying the same closed exemption list; their nested
assignments remain independently inspected. Public-key block mirrors stay public.

B16a delimiter ownership (D1–D11): ambiguity may only lengthen a redaction.

D1. A construct owns its value's opening quote; a stack of openers immediately
adjacent to its key, flag, header, scheme or //, with no intervening space; and a
containing string proven by the JSON lexer. An owned closer must be at its
opener's layer and on its line, except for a proven JSON string or a multiline
triple-quote/heredoc token. The innermost owned opener closes first. Line-wide
quote pairing, pending quotes and unowned brackets never shorten an extent.
Contextual separators, including call-argument commas, require an owned or
proven enclosure. Other quotes and brackets are value data.

D2. A matching owned closer counts only when the run of closing quotes/brackets
is followed by end of text, whitespace, comma, semicolon, ampersand, />,
.!?: followed by whitespace/end, or a literal method call (`.identifier(`).
Other punctuation and identifier characters do not validate a closer. A deeper form-plus space is value data; a newline proved by the JSON lexer retains its JSON line-boundary meaning. Accepted
category `owned_raw_closer_with_follower`: a raw owned closer followed by this
follower ends the value even if private-looking material follows. Source strings
must escape an internal quote; D4 keeps those escaped spellings private. No
other closer/follower category is accepted.

D3. For a value without its own opening quote, leave out its longest trailing
suffix consisting only of closing quotes/brackets and .,!?:;. This same extent
is used for both masking and the exemption decision; letters are never trimmed.
A complete placeholder owns its own closing delimiter. Set password=${PW}.
therefore remains unchanged, as do exact [REDACTED] sentinels inside punctuation.

D4. Own opening quotes include ', ", backtick, “/‘, $', and triple single/double
quotes. Doubled quotes, backslash-escaped quotes, PowerShell backtick escapes,
and the shell single-quote idioms are value data, never closing tokens. Curly
quotes pair as “…” and ‘…’; an apostrophe between letters remains prose.
An unmatched own quote extends to line end or the bound of its proven JSON
string/multiline token. Triple quotes and heredocs retain their own closing token.

D5. All scanners use the same whole-value exemption decision after D3, without
its own quotes. Bracketed placeholders, fixed environment lookups, Optional,
percent variables and braces require genuine matching delimiters at one layer.
Mixed raw/encoded delimiters cannot complete an exemption. There is no repeated
unquote of a raw slice. Only a complete serialized value may be canonicalized as
one whole layer. URL userinfo and curl credentials use this decision too.
Every exemption leaves its contents eligible for a bounded, single re-entry.

D6. Inside a shell ${…}, the parameter name and operator are not an assignment
or header key. Operators include :- := :? :+ - = ? + # ## % %% / // ^ ^^ , ,,
and :offset. An operand begins a new token and belongs to its containing simple
command. A whole expansion is exempt only when every operand is empty, a $NAME
or nested ${…} reference, or a ?/:? message. A literal default is private.

D7. Markup is scanned per tag. Attribute strings own their quote (including
backtick); an unquoted attribute ends at whitespace, > or />. HTML-unescaped,
ASCII-whitespace-stripped and case-folded name/type selectors determine private
value/content attributes. Comments are independent contexts bounded by -->,
including commented-out sensitive elements. A malformed sensitive start tag ends
at its first > and still owns its private body. A malformed/unclosed tag never
ends the rest of the scan: its next same-level < starts another tag. Quoted
attribute contents are entered once as bounded sub-contexts.

D8. PuTTY blocks run through the end of Private-MAC: or Private-Hash:. PEM/PGP
blocks run through their matching END line. Quotes and brackets cannot shorten
these blocks. A missing final line extends to text end or its proven JSON string.
A sensitive YAML key owns deeper-indented following lines when its header has
only an optional |/> block indicator, optional +/− chomping and indentation digit
1–9 in either order, whitespace and an optional comment. This includes a bare
key followed by a comment and an indented body. Malformed pasted indentation
extends conservatively through the following indented run.

D9. Embedded form bodies continue through quotes and angle brackets; whitespace,
line breaks and owned D1/D2 closers bound them. Percent escapes and plus-space
conversion retain their form layer. Every scanner, including markup, inspects
the decoded body; unrelated prose after a question mark never gains form scope.

D10. Short -u/-b flags use a conservative lexical anchor: a curl token
(optionally with a path) in the same simple-command segment establishes scope,
including pasted prose. Assignments and sudo/env/time/command/exec/nohup may
precede it. A curl token can also be an argument to another command: for example,
echo curl -b public is intentionally redacted and refused. This is an explicit
narrowing of the benign-acceptance promise, not a shell command-word parser.
Pipes, semicolons, &&, || and unescaped line ends reset the scope.
Long --user/--cookie retain their independent rule. SQL IDENTIFIED BY and WITH
PASSWORD require a CREATE/ALTER/GRANT/SET statement naming USER/ROLE/LOGIN before
the clause; the next semicolon bounds the statement. The six prescribed benign
examples remain unchanged: git checkout -b and python -b questions, the UUID and
filesets identification sentences, mktime()-based conversion and the len array
phrase. This does not promise acceptance of every benign shell-like phrase.
Mandatory natural prose contexts, every privacy rule and the established 0.40%
CPython refusal budget remain binding. This policy adjustment is not merge
acceptance.

Curl boundary state advances once from the lexical anchor; preceding prose
quotes never supply its argument delimiters. An odd backslash escapes the next
separator, including LF/CRLF continuation. Separators inside an owned preceding
quoted argument, a complete exempt argument or a shell parameter expansion do
not reset scope. A real unquoted, unescaped separator still resets scope when
encountered while advancing over a consumed credential. If the selected private
extent spans it, both selected parts remain masked and the separator is retained
as control syntax, so no credential tail is exposed and a subsequent application
preserves the same scope reset. Per-class counts count masked spans, which can
be two spans for that argument. Preceding argument scope
also ends before a pipe after its close quote; this is not an addition to D2's
credential-value followers. Long flags keep their independent scope.

Curl source spans preserve shell backslash parity: percent/form views and exact
proven JSON string contents are scanned at their own layers, then mapped back
and merged with canonical spans. General repeated JSON unescaping must not erase
a source-level escape before the boundary decision. Existing JSON lexing and
sparse span maps are reused; syntax tables for these views are released before
the next is constructed. SQL eligibility starts at the first principal token,
never at an earlier clause, and ends at the statement's semicolon.

D11. Every scanner performs constant amortized work per character. Terminator,
fold, quote, block and exemption extents are indexed once or memoized. Exempt
values are entered once as bounded sub-contexts. A scanner never restarts an
unbounded forward search for each candidate. CPU growth uses the iterated public
redactor, slope ≤1.20 and at most 12× from 16 to 128 KiB; fresh-process peak RSS
is at most 1 GiB at the 20 MiB raw cap. No redaction or sanitize_document call
may run while a store write transaction is held.

Nested JSON proof mapping also obeys this bound. Opening-ordered proof intervals
use one monotone sparse-edit traversal when disjoint, or two when overlapping,
independent of structural nesting depth. Overlapping endpoints are ordered with
fixed-width numeric radix passes, not comparison sorting or a mapper restart per
nesting level. Starts round down and ends round up within the contributing
coalesced escape; duplicate intervals and collapsed starts retain tuple order.
Temporary numeric storage is O(proofs), with no per-depth edit streams or copied
decoded layers. The codec budgets do not impose a JSON structural-depth limit.

Escaped-header newline indexing begins only at the first backslash of each
contiguous run. A possessive run alone does not ensure linear work in an
unanchored search: a missing `r` or `n` must not retry every remaining suffix.
This start guard preserves the existing escaped CR/LF matches and adds no
decoding layer, offset table or change to literal backslash output.


Exemptions require a complete genuine closing delimiter at the same layer.
Forced closure at an enclosing quote or end of input is not a match. Unclosed
placeholders and lookups are credentials. Fixed environment lookups have locally
quoted arguments, so parentheses or brackets within an argument are data. Lookup
and placeholder extents are indexed once; nested lookups never repeatedly copy
or decode a suffix per assignment.

Every scanner continues inside an exempt outer value. Assignment, header, XML,
query, curl and SQL/sshpass/mysql passes advance only past the matched key or
operator until masking a value. Cookie-chain discovery likewise revisits exempt
single members. URL candidates always resume at the next candidate; HAR walks
all string/container children; jar, prefix, JWT, block and path passes enumerate
independent matches. Only a selected redaction permits skipping its consumed value.

Protocol-relative // authorities are URL candidates, independent of preceding
URLs. A scheme or // exposed at a deeper layer inside a password remains value
data. A root candidate with no userinfo operator before a deeper scheme still
yields to that independently scanned URL. One monotone successor cursor per
bounded layer prevents rescanning. Markdown autolinks end at their proven > in both authority and query
passes. A raw backslash ends a special-scheme authority even when a permissive
decoded view would treat its escape as data. Accepted benign cost: an unenclosed
scheme://host:port,user@host contains WHATWG userinfo, as does a literal backslash
in a non-special scheme's userinfo; both are redacted by design.

HAR JSON containers are candidates at every offset and decoded layer, including
prose, fences, log prefixes and packet headers. A strict lexer evaluates completed
objects before an error; an independent candidate swallowed as string content is
not lost. Lexical ends and container outcomes are memoized once, so late failures
and candidate recovery remain linear. Any sensitive repeated name marks every
value in that object private, without overwriting an earlier sensitivity decision.

Private-key block labels accept space, form plus and percent-encoded space at
every supported layer. Quotes in PuTTY comments never shorten the block; it
extends through Private-MAC or Private-Hash, or its proven JSON/text bound.
Quoted form-body discovery pairs quotes per decoding layer, and decoded-only
whitespace remains form value data.


B16a context assurance: natural and seeded malformed contexts surround in-scope
rows both inside and outside raw, JSON (one/two), percent and form layers.
Representations stay within the supported decoding budgets: a row already using
three percent layers retains its context in raw/JSON form instead of receiving a
fourth percent/form wrapper. Count these representation adjustments; no natural
context is excluded. Whole-input peeling preserves percent escapes inside URL
password data.
The property checks marker fragments as well as whole values, and checks the
one-call fixpoint. Natural-context benign mirrors must remain unchanged;
malformed random-context benign changes are measured, since ambiguous structure
may lengthen a mask. The installed CPython corpus retains a 0.40% refusal budget.
HTML/XML elements make value/content private when the same element has a sensitive
name; type=password makes value private. Attribute order and quoting do not
change that rule. Benign username/description elements remain unchanged.


Grammar-proven HTML/XML attribute strings may contain line breaks; their local
matching quote is indexed separately from prose quote pairing. Attribute name/type
classification resolves HTML character references. Oversized (over 1,024-character)
name/type selectors fail closed before decoding. Exempt attribute values remain
searchable for independent nested elements. A malformed unquoted nested tag starts
an independent candidate instead of causing a repeated suffix walk.

A shell-parameter placeholder must have a complete balanced closing brace and a
parameter header (identifier/positional/special parameter, optionally followed by
its parameter operator). Random prose before the brace cannot turn an incomplete
credential into a valid placeholder. Fixed lookup and complete double-brace extents
are indexed once; exemption checks do not copy an enclosing suffix per assignment.
Exact JSON string boundaries stay mapped through form/percent/JSON decoding;
quoted defaults cannot jump over a proven enclosing JSON close. Raw special-scheme
backslash barriers exclude serialization backslashes and Unicode-escape data.
Netscape jar fields remain recognizable after a surrounding context prefix and
retain CR/LF line endings. These rules preserve the existing four-application bound.
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
The B16a integration carries the linear core scanner and converts the five
inherited core-growth xfails into ordinary tests, at the standard 128 KiB, 256 KiB,
512 KiB and 1 MiB sizes. Each size retains three CPU samples and two calls per
sample (the existing authorization family retains four calls). The slope limit
remains 1.25, the endpoint ratio limit remains 12, and the 1 MiB CPU ceiling
remains 12 s for the adversarial Muse grammars. The bare-cookie AT-042 regression
is also an ordinary passing test. Focused measurements do not certify the full
integrated application or replace exact-source hosted integration gates.


B12's target includes the complete canonical remote-media approval table: extractor key,
normalized sorted hosts, and opaque auth_ref or null. Preparation preserves configured
extractor priority and orders entries within each key canonically; selected first-hop
keys are also bound. The preview identifies first-hop and later-hop entries, hosts and
cookie labels/accounts. Execution selects hops only from the bound entries. A change
to any entry, including an unrelated approval, invalidates the grant before use or
egress and requires a fresh preview. No network-derived resolved URL enters the digest.

B12 preparation computes one ASCII-only canonical URL: lower-case scheme and
IDNA2003/punycode host, non-default port only, UTF-8 percent-encoded non-ASCII path/query.
Decode unreserved escapes, upper-case remaining escapes, and pre-encode ASCII outside
the pinned yt-dlp/curl_cffi safe-set intersection. Only m.facebook.com maps to www;
host approval checks www. Refuse IDNA deviation characters ß/ς and mapped trailing dots.
The digest, preview, request echo and operator stderr bind that string. The extractor
receives exactly the displayed URL. The transport sends exactly each URL the checker
approved, without requoting or dot-segment removal (quote=False, PATH_AS_IS and an explicit
REQUEST_TARGET, preserving escape hex case too). Extractors
may derive further requests, e.g. FacebookReel watch URLs, added query parameters or
API endpoints; each is checked against bound approval entries before any dial. Wire
tests prove that the first page request bytes equal the display for the fixture
extractor and FacebookIE watch, video and story URLs. The job request and original_url
provenance retain the original input. Refuse fragments (even empty #), shared url_token
query keys, invalid escapes, dot segments (including escapes and parameters), duplicate
canonical query keys and structural redaction, with fixed E_INPUT_INVALID, no preview
or URL echo. At preview and before extraction, assert the pinned-library fixed point:
display == requote_uri(normalize_url(display)); drift fails closed. Signed extractor-hop
URLs retain existing redaction; credential-bearing page URLs are outside B12 inputs.
B12 preparation checks metadata for all bound cookie references without reading secret
bytes: existence and provider/account must match, otherwise E_AUTH_REQUIRED without a
preview. Execution resolves and parses every bound secret before permit consumption,
including unused later-hop bindings. Parsed cookies are scoped to bound entries.

B12 receipt lookup precedes permit verification: an identical request including its grant
returns its original job after consumption or workspace-cap edits. Any changed payload
is E_IDEMPOTENCY_CONFLICT. Workspace caps stay outside target and job identity, but the four displayed caps
(safe_read_retries, read_timeout_seconds, media_max_bytes, text_max_bytes) enter the
action digest payload. Any edit before grant submission requires new approval.
Atomic acceptance stores exactly the reviewed values in the B12-owned step checkpoint
`remote_media_caps`. Execution recomputes the digest from that snapshot.

At the B12 boundary each workspace cap is min(reviewed snapshot, current value).
Lower caps apply to queued jobs; increases cannot exceed the reviewed snapshot.
Request limits remain digest-bound and are checked against current workspace limits.
Retries above the effective B12 cap stop before consumption or egress, even if the
core executor schedules another local attempt. That terminal local guard preserves
the original read error code/message, sets retry_class=never, and explains the accepted
retry cap in next_action.

B12 preparation enforces the shared 8192-byte UTF-8 URL bound and cheap HTTP(S)
syntax checks before redaction, display, digest, preview or job creation. One byte
over the bound returns the same outbound-policy error as the dial-time guard,
without a preview. The ASCII canonical URL must also fit this outbound bound.
URLInput still needs a core-owned schema/validation bound.

Remote configuration labels that are invalid or credential-shaped return fixed
E_INPUT_INVALID naming only the setting. Approval callback E_UNSUPPORTED means no
applicable preview and preserves the original refusal. Other CoreError refusals
propagate their fixed code, message and next action. Unexpected exceptions preserve
the original refusal and log only a fixed line and type. These paths produce no preview.
Challenge normalization scans every response body in overlapping 32 KiB blocks,
checking the step cancellation/wall deadline once per block and retaining the existing
timeout error on expiry. Measured CPU costs per 512 MiB are documented in COLLECT.md.
Login forms keep the first duplicate attribute value. Only the first base with an
href counts; an unparsable first href uses the document URL, ignoring later bases.
Unparsable actions are skipped while later forms are checked.
Challenge token __rd_verify matches case-insensitively with or without a preceding
slash, after HTML entities, one percent-decoding, JavaScript \u005f/\x5f underscore
escapes and NFKC folding. Split JavaScript string literals are not detected.
Cookie domains require at least two labels and cannot normalize to IP literals;
multi-label public suffix detection needs a public-suffix list.

B12 durable canonical URLs are reviewable bound-host displays selected in webpage_url,
extract_url, reviewed order, with canonical_basis in use_basis and revision evidence.
Signed hop/resource URLs never persist. Changed subtitle files atomically append new
cues and removed=true/empty-text revisions for omitted prior indexes; source basis
records zero-cue replacements too. Search excludes latest removals, resolution and
exports refuse removal refs without text, and historical text-bearing refs stay stale.
Migration `006_transcript_removal.sql` upgrades schema 5→6 after Muse trust
migration 005; its additive checked column preserves existing rows, triggers and indexes.
Listing omissions alone remain B18, not source-removal proof.
Subtitle/media final HTML responses (type or BOM/whitespace/opening-tag sniff) are
parsed for login forms over the full body, retaining original context across redirects;
E_AUTH_REQUIRED stops before later requests or resource writes. Page requests remain
extractor-classified. Challenge/login block scans share a cheap deadline on every block
and durable guard pacing: first, at most every 250 ms, and after last.
### B13 browser boundary clarifications

Facebook uses a nonempty exact host allowlist validated at configuration load.
Wildcard, IP, local, invalid and empty entries fail with a fixed E_INPUT_INVALID
without reflecting the operator value; B08's generic optional allowlist is unchanged.
Entries must be bare hostnames: URL query/fragment delimiters are invalid, never
parsed away before validation.

B13 egress diagnostics expose fixed reason/purpose counts only; denied destination
strings never enter job results, checkpoints, events or errors (AT-028).

Authorized post identity requires exact, case-sensitive actor and post-token equality.
Primary-host aliases and accepted permalink syntaxes with those same tokens are
equivalent; numeric story_fbid and pfbid tokens are never inferred equivalent.
Main-frame routes, every observed main-frame navigation and the final URL are checked
before extraction; extracted actor/id mismatch stops with E_REFERENCE_INVALID.
New document requests are aborted where Playwright routing (or the plaintext
fixture proxy) exposes them. HTTPS 302 paths inside CONNECT can already have been
requested before Playwright observes the final navigation; no data, source or item
from that document is stored or reported. Script/meta navigation is route-blocked.

Page WebSockets and WebSocketStream are refused by Chromium per-URL offline
network rules for ws/wss and all ports. Network.setBlockedURLs alone is insufficient;
context.route_web_socket is supplemental. Any observed handshake response yields a
fixed E_PROTOCOL_MISMATCH before import; native creation and handshake events stop with the same fixed error; attempts count websocket_blocked.
Workers are verified debugger-paused, then destroyed without resuming.
CONNECT host/IP checks remain a separate boundary.

Proxy policy/resolution waits are bounded by the read timeout and interruptible on
close. Closure fences dialing and socket registration; once close returns, no
upstream connection can occur later. Python cannot cancel an in-flight NSS lookup;
abandoned daemon resolution tasks have no dialing capability and a process-wide
16-slot bound, released only when their lookup returns. Capacity exhaustion refuses
requests. An already entered dial is bounded by the socket timeout and closure waits
for it before closing every registered socket.

Facebook checkpoints final wire-byte consumption after transport shutdown, even
when navigation stops at a barrier before returning a DOM observation. Resume
allocates only the original budget minus all recorded session consumption.

B13 also closes ordinary page sockets with Playwright routing; the network rules
cover native bypasses. Dedicated/shared workers are terminated at CDP’s pre-script pause.
The protocol/worker controls are installed before navigation; worker-dependent
Facebook rendering remains unverified until AT-039.

Malformed storage-state origin entries, missing/non-string origins, non-HTTPS schemes,
invalid/out-of-range/non-443 ports, credentials, path/query/fragment or malformed
authority fail with fixed
E_AUTH_REQUIRED: Invalid Facebook storage-state origin. Capability inspection still
returns the normal needs_auth row; no raw origin value enters diagnostics.

CONNECT port strings are bounded to five ASCII digits before integer conversion.
Numeric and other parser failures produce a fixed 403 protocol refusal, recorded
as classification/count only; malformed input never emits a worker traceback.

B13 Chromium launches require chromium_sandbox=True. Failure is E_DEPENDENCY_MISSING
with AppArmor userns and explicit operator opt-in remedies; no automatic downgrade.
Only [facebook] allow_unsandboxed_browser=true authorizes a disabled/unverified mode.
Results/checkpoints record the running observation; inspect/doctor warn on the exception.
A bounded cached headless blank-page launch without navigation/state verifies command-line
and Linux renderer Seccomp state; an unavailable sandbox never yields environment=ready without the opt-in.
The risk and generic AppArmor template are in docs/dev/FACEBOOK.md. Renderer compromise
under the opt-in has operator UID access, so separate OS isolation is recommended.

B13 share resolution never widens a permit: target/action payload bind the normalized
share URL while browser navigation/extraction bind its first observed main-frame single-post
actor/token. A resumed session compares against the persisted resolved identity
before import; no numeric-token/pfbid or actor-alias equivalence is inferred.
requested_locator and resolved_permalink use existing freeform result/provenance
fields; no core model, migration or schema extension is required.

B13 associates each accumulated Record with its observation SHA-256, without using
text/position for identity. It compares the latest state per reliable native key, so
reversions remain revisions. The existing Book receives each contributing hash; its
frozen revisions retain those hashes and verified observation provenance. New Source
use_basis identifies the first contributing snapshot, not the attempt’s final DOM.


A present but unparseable Facebook comment count adds comment_count_unparsed to post and job gaps; affirmative exhaustion never converts that uncertainty into scope_satisfied.


Facebook counts and remaining hints accept only nonnegative integers or 1–18 ASCII decimal digits. Other present forms add fixed *_count_unparsed gaps; Unicode digits and oversized decimals never reach an unchecked int conversion.


Facebook parent identifiers are stripped and validated against [A-Za-z0-9._:-]{1,128} in both DOM and JSON. Invalid/empty reply parents are unknown and add parent_comment to the item and job, independently of unresolved_parent.


Facebook comment identifiers use the same stripped nonempty ASCII token validation as parents. An invalid token is absent/weak; distinct weak records are never merged by text or position.


Facebook weak-identity resume suppression begins only after an accepted batch has committed. A usage-only checkpoint from a login wall does not suppress a first import of weak comments; cumulative byte/expansion usage still survives.


Facebook blocks ws:/wss: at Chromium’s network layer with Network.emulateNetworkConditionsByRule offline rules matching both schemes and all ports. setBlockedURLs alone is insufficient. Page WebSocket routing is supplemental. Workers are verified paused before their first script and destroyed without any resume command. Each observed attempt has the fixed websocket_blocked reason; any native socket creation or observed handshake response fails E_PROTOCOL_MISMATCH before import, including events drained at close.


Facebook navigation failures use only the main-document request/redirect chain and its uncompleted CONNECT authorities. Refusals of WebSockets, workers, subresources or already-responded document authorities do not choose that error. Resolver capacity/timeouts use E_NETWORK_TIMEOUT with DNS-oriented advice, never a permit/policy denial.


Only DOM progress authorizes expansion progress: a new relevant comment/reply
node or changed identified content within the read deadline. Requests, response
statuses, counts and disappearing controls are neither progress nor failure evidence.
The page defines request meaning, and delegated listeners and async scheduling
cannot establish reliable request ownership; GraphQL errors may also use HTTP 200.
No owned CDP session enables the Debugger domain or async stack depth. The AST
source-constant audit test_r10_no_debugger_or_expansion_network_authority rejects
Debugger commands across the session and capture modules and checks compiled
session constants too, so a runtime wrapper cannot evade the audit.
Network.enable remains for socket guards.

A click that cannot be performed records expansion_failed: identity revalidation,
scroll, usable-quad retrieval, hit lookup, node resolution, composed hit check or
CDP mouseMoved, mousePressed or mouseReleased fails before mouseReleased completes.
After a completed click, no relevant DOM progress by the read deadline records
expansion_progress_timeout, partial coverage and comments_empty=false. Both outcomes
retain observed data; document and guard stops keep their existing precedence.
Known limitation (async_hidden_500): DOM evidence is page content. A page that
renders comments and claims exhaustion before its own request fails cannot be
distinguished from a truthful page. LIVE exhaustion mappings remain unverified.


Facebook navigation barriers, page shapes and identity adoption apply only to the main frame. All network subframe document requests are aborted with blocked_subframe without setting navigation_error. A share identity is adopted only from the main frame.


Facebook sandbox state is observed from Browser.getBrowserCommandLine and, on Linux, renderer /proc/<pid>/status Seccomp: 2. No --no-sandbox is allowed for enabled. Failed observation is unverified and refused without the operator exception. The production executable environment override is removed; tests inject an executable through a seam. Capability launch checks verify this running state, not exit status alone.


Facebook cookie path/expiry/boolean/sameSite/name/value shapes are validated before launch; expiry is -1 or finite Unix seconds through 253402300799, and path starts with /. Invalid state returns fixed E_AUTH_REQUIRED and needs_auth. Context/state failures suggest auth repair. Only an actual required sandbox launch failure offers the unsandboxed exception.


After a Facebook post identity is bound, a main-frame navigation to an unknown non-post shape returns E_CHECKPOINT_REQUIRED with the distinct message “Facebook navigation left the approved post” and browser-session review advice. Unsupported operator inputs retain E_UNSUPPORTED. Share destinations before any post is bound retain the separately documented resolution-type refusal.


Facebook rendered text includes inline image alt text (including emoji) and follows the structured capture/rendering contract below. JSON text remains unchanged.

Structural JSON scripts require both data-fb-post-payload and application/json MIME
type. Every other script is excluded from body text, even if it carries that marker.

Embedded JSON integer tokens over 128 characters stay lexical for field validation.
Oversized counts add their fixed *_count_unparsed gap and retain the observed post;
they do not turn a valid JSON document into a malformed-payload error.


Facebook captures the main HTML document through CDP in an isolated world, never
through main-world evaluation. An explicit-stack read-only walker uses pristine
DOM getters, `checkVisibility()` and computed styles. It creates no elements,
clones no document and writes no attributes, so capture cannot invoke custom-element
constructors, attribute reactions or mutation observers. CDP discovers closed shadow
roots and resolves their handles into that world. The same isolated world is scoped
to the current navigation/frame. Iframe documents are not post evidence.

Every evaluated DOM function captures its complete accessor table before touching
the tree. WebIDL getters come from prototype descriptors and methods from their
prototypes, invoked with captured `Reflect.apply`; Window global methods use their
own descriptors. This defeats form/document named-property clobbering even across
isolated worlds. The table covers Document, Node, Element, Text, Attr, template,
slot, shadow-root, Range, DOM collections and computed-style access. HTML template
content and slot assignment are used only in the XHTML namespace.

`test_r9_no_direct_dom_members` tokenises the narrow observation JavaScript dialect
(including strings/comments), checks postfix chains and rejects direct, indexed or
optional member access on platform handles or DOM-valued accessor results. DOM
handles use `n_*`; aliases cannot become plain-data locals. The permitted `v_*`
collections/primitives and `r_node` records are built by the walker itself, and
record fields have an explicit allowlist. Unknown receivers are rejected. Negative
alias/chain tests enforce that boundary. `test_r9_evaluated_function_inventory`
uses Python ASTs to enumerate every evaluation payload: only the checked walker,
hit test and blank-page popup probe may access DOM objects. The remaining payloads
only store/resolve isolated-world handles, close paused workers or evaluate zero.
The clobbering corpus derives its names from this table and the global-method
table, with six element patterns in barrier and painted-comment contexts.

The observation is a flat structured node list: kind, namespace, tag, ordinary
attributes, text, light-tree children, composed-tree children and a rendered boolean.
Expand controls also carry a CDP backend node identity. There is no reserved HTML
attribute channel or trusted-HTML flag; every page-authored `data-metamuse-*`
attribute is ordinary markup, regardless of case, namespace or creation method.
Chromium observations are never HTML-reparsed. Progress compares structured comment
identities/text and immediately classifies new barrier evidence during polling. CDP clicks the exact captured rendered control, including when
another hidden control has the same identifier. Serialized structured data supplies
the observation digest and byte accounting; it is not parsed as HTML.

| Content | Rendered evidence |
| --- | --- |
| Ordinary laid-out content | yes |
| Open, declarative and closed shadow content | yes |
| Light children assigned to a slot | yes |
| Opacity zero, off-screen, zero-size or clipped | yes; painted-but-not-seen limit |
| Off-screen `content-visibility:auto` descendants | yes, even without layout nodes |
| `aria-hidden=true` | yes; accessibility does not determine painting |
| `hidden` with a CSS display override | yes, by the cascade |
| `display:none`, or `hidden` without an override | no |
| `hidden=until-found` | no |
| `content-visibility:hidden` descendants | no where computed display supports containment; otherwise yes |
| Closed details content other than its first summary | no |
| Scripting-on `noscript`, and template content | no |
| Unslotted light children of a shadow host | no |
| `visibility:hidden` or `collapse` | no; descendants may restore `visible` |
| Text, including through `display:contents` | its text box and inherited visible state; skipped auto content remains eligible |


An element's composed parent is its assigned slot, otherwise its parent element,
otherwise its shadow root's host. A `display:none` composed ancestor blocks every
composed descendant, including slotted `display:contents` children. Visibility can
be restored by descendants, but an unrendered container never establishes a post,
comments-section or comment identity. Extractor stack frames restore exactly the
state opened at entry. `display:contents` suppresses replaced elements and controls:
img, input, textarea, select, video, audio, canvas, iframe, embed, object, br, wbr,
meter, progress, frame and frameset. Look-through never climbs out of these nodes.

The locked Chromium build computes `display:block` for `frame` and `frameset`
even with authored `display:contents`; the Appendix B differential checks record
this exception. They are refused by fixtures. The other listed replaced/form
elements are unrendered with computed `display:contents`.

One `capture.controls` traversal assigns rendered controls in composed order to
both the extractor queue and the CDP identity map. IDs use `data-expand-id` or
`expand_N`; the first rendered duplicate wins. The hit test follows assigned slots,
parents and shadow hosts through cached accessors, accepting labels inside a
control's shadow root or assigned through a slot. An overlay, removed control or
unusable quad fails expansion without clicking a substitute node.

Default `checkVisibility()` preserves auto-skipped content. Text-range boxes exclude
non-painting direct text in closed details; looking through `display:contents`
preserves text whose immediate parent has no box. Opacity, clipping, size and viewport
position do not filter evidence. Possible painted `aria-hidden` duplicates must be
measured in AT-039, not excluded by assumption. JSON text keeps its existing rules.

`FixtureTree` is a static test double accepting a balanced subset, not a general
HTML5 parser. Its HTML tag vocabulary is explicit (`HTML_TAGS`); foreign SVG and
MathML vocabularies exclude HTML breakout and integration-point rules. Fragments
use a body-fragment context. Explicit documents require all three html/head/body wrappers in order; a template
fragment cannot admit them.
It models scripting-on HTML `noscript`, the serializer's exact 18 HTML-only void
tags, inline declaration order and `!important`, hidden/display overrides, content
visibility, details and inherited visibility. Unsupported parser modes, unbalanced
or implied end tags, nested form/a/button corrections, foster parenting and
implicit table sections refuse with the fixed capture failure. Rawtext/RCDATA/
script-data/PLAINTEXT elements other than inert script/style and HTML noscript are
refused: xmp, iframe, noembed, noframes, plaintext, textarea and title. Foreign HTML
names (including noscript/template/slot), dialog, popover and declarative shadow DOM
are refused. Executable scripts, event handlers, stylesheets, external stylesheet
links, refresh metadata and unsupported inline rendering declarations also refuse;
fixtures do not simulate execution, resources or the external CSS cascade. Inert script payloads admit application/json, application/ld+json and text/plain; other non-empty script bodies refuse. Legacy executable-script and embed examples remain real Chromium regressions, with explicit fixture refusals.


A vocabulary audit also checks native defaults and valid parent contexts, including
all admitted tags and inline display/visibility values. `area` and `basefont` are
non-rendered by default; standalone `source` and `track` remain eligible under the
capture rule. Their display overrides refuse. SVG fixtures admit only svg/g
containers, text/tspan text contexts and empty geometric leaves; defs, symbol and
use refuse. A tspan outside text/tspan and any element child of a geometric leaf
refuse. Foreign inline display values other than none, and HTML table-column or
table-column-group display values, refuse because their used display or descendant
box suppression requires layout. These frozen audit inputs remain in the parity
corpus even when the admitted vocabulary is narrowed.

The differential corpus includes committed HTML literals/files, runtime fixture
inputs recorded by the suite, reviewer-provided markup and a seeded depth-five
generator. Its marker/role vocabulary is checked against AST literals from the
extractor, control traversal and DOM parse; every marker also has a deterministic
sample independent of random selection. Accepted inputs must agree with sandboxed Chromium on node kind, tag,
namespace, attributes, text, rendered flag, light-parent relation and composed
order. Normalisation removes document/fragment/doctype/comment records and empty
html/head/body scaffolding; it preserves evidence nodes and their parent/order.
Unsupported inputs are counted as refusals. The capture-only audit opens one sandboxed, policy-proxied loopback session
without a job store, permit setup or TLS key generation. It waits for static load
and flushes DOM events before each capture; job-level parity is checked separately.
The default Chromium audit has an
independent 90-second deadline; the job/browser budgets remain 300 seconds.
Potentially rendered whitespace-only text nodes are refused: their boxes depend
on layout and whitespace collapse. Mixed text keeps its source whitespace. MathML
text-integration-point element children, paragraph-implying block starts and
foreign-content foster parenting also refuse. HTML5 `</br>` creates a `br` node.
Literal NULs, foreign CDATA/conditional declarations, processing instructions and
ambiguous legacy character references in start tags are refused. Exact named
references ending in semicolons retain the HTML5 decoding; text is never decoded
a second time. These lexical refusals cover differences in the standard-library
parser rather than claiming it is a full HTML5 tokenizer.
Metadata display overrides are refused rather than approximated. Display inheritance (`inherit`) also refuses; its resolved value is not the inline token. The tested `revert` and `revert-layer` behavior remains supported. Implicit HTML,
head and body nodes are materialised before bounds checking, so the document
scaffold is charged exactly like Chromium; misplaced explicit wrappers refuse.

The fixture-only raw UTF-8 markup precheck is 8 MiB; every parity input is below it.

Capture permits at most 65,536 nodes and 8 MiB of UTF-8 text plus attribute names and
values, independently of the operator-selected `limits.max_bytes`. The existing
HTML/observation and proxy-byte budget remains binding; these additional structural
bounds also cover a tiny script that creates a large DOM. Flat records add overhead
beyond equivalent HTML, and their serialized bytes count against the same job budget. Bounds, unsupported non-HTML main MIME
types (XHTML/XML/SVG), and capture failures refuse with fixed Facebook-specific
`E_SELECTOR_DRIFT`, without page text or `E_STORAGE`. Inaccessible closed roots add
`closed_shadow_capture_unavailable` and force partial coverage. Subtree insertions or shadow-root attachments observed
across CDP discovery/capture commands add `capture_changed_during_observation`, so
a closed root created during handle discovery cannot be silently omitted. Incomplete captures
never claim `scope_satisfied`.

Stop markers (`data-fb-state`, `data-page-state`, `data-checkpoint`, login/checkpoint
form IDs, head/meta states) are inspected across the whole light DOM, unslotted
children, templates and shadow roots, regardless of rendering. Completeness evidence
such as `data-comments-exhausted` counts only when rendered. Text retains the existing
single decode, image-alt, newline and edge-whitespace contract.


Facebook browser routes validate the destination through the shared URL policy
(scheme, credentials, port, exact host allowlist and all resolved IPs) before
classifying login/checkpoint or off-post navigation. Denied destinations return
`E_SCOPE_DENIED` with the fixed message “Facebook browser destination is denied
by egress policy”. Production requires HTTPS. Chromium route DNS checks share
the proxy's bounded resolver slots/deadline; DNS capacity and timeout failures
retain `E_NETWORK_TIMEOUT`. The proxy still independently checks every dial/hop.


Facebook guard failures fail closed with fixed `E_PROTOCOL_MISMATCH`: a failed
close/cancel/abort is ignored only if the target or request is provably gone;
missing liveness evidence is failure. Worker attachments must report
`waitingForDebugger=true`, and `Target.getTargets` must confirm destruction. No
worker resume command exists. The redundant per-worker network-rule step is
removed: no untrusted worker source executes, including nested worker creation.
Dedicated workers need a fixed CDP `Runtime.evaluate("self.close()")` in their
paused realm because `Target.closeTarget` alone leaves them alive in this build.
Shared and service workers use `Target.closeTarget`. Success, refusal and lost
close replies all enter the same pending-target set: destruction is asynchronous.
Verification runs after callbacks return, before import, with a bounded five-second
wait, including dedicated workers already sent the fixed `self.close()`. The verifier
iterates a copy and discards targets only after proving disappearance. Attachments
delivered while checking are included in that same five-second deadline; new targets
never extend it. Unexpected verification exceptions become the fixed guard failure. A browser
disconnect is never disappearance evidence; a target still listed past the bound
fails closed. No worker resume or detach-to-run command is sent. Popups,
downloads, route aborts and session teardown use the same fail-closed rule.


Facebook requires Chromium `--block-new-web-contents`: renderer popup creation
returns null, including URL/blank `window.open`, target=_blank links/forms,
noopener, pristine iframe open and nested popup attempts. Before init scripts or
navigation, the same sandbox verification blank page tests native and fresh
iframe open; the proxy is bound but closed (not listening) and Chromium DNS is
disabled. A failed or inconclusive check returns fixed `E_DEPENDENCY_MISSING`
“Facebook browser popup creation control is unavailable”, with no operator opt-out.
CDP `Page.windowOpen` counts refused attempts as `popup_blocked`; refusal alone
permits success. Browser-level auto-attach pauses foreign page/iframe targets
before their first script, installs network event detection and closes them.
Native WebSocket creation or handshake responses in any attached target stop
with the existing fixed `E_PROTOCOL_MISMATCH` before import. Workers never resume.
The `WebSocketStream` init shim is defense in depth alongside Playwright's
ordinary WebSocket routing; it is not the popup or socket boundary.


A Facebook proxy byte cutoff immediately shuts down both client and upstream
sockets for all active tunnels and refuses subsequent dials/registrations.
Upstream candidates are registered before connect can block; teardown closes
them before waiting for the dial barrier, so pending connects are included. A
50 ms browser-loop watcher reacts to byte cutoff and latched socket/guard
failures, closing the affected page to interrupt in-flight
navigation, click and expansion operations, instead of waiting for the full
`read_timeout_seconds`. Only navigation/socket/wait interruptions caused by the
cutoff become `E_LIMIT_REACHED` with partial coverage. Existing fatal errors are
never rewritten merely because the byte latch is set. Error precedence is guard
failure or unverified target shutdown, denied destination (`E_SCOPE_DENIED`),
login/checkpoint/challenge/rate-limit barrier, off-post navigation (including share-link
`E_UNSUPPORTED` and `E_DATA_STALE`), then the byte budget. Every other CoreError also
outranks the cutoff. Only the named aborted-navigation, closed-socket and interrupted-wait
errors caused by the cutoff may be dropped. Error recording retains the strongest
stop, including at close after the final observation with no expansion. When HTML has been observed, barrier classification runs before the byte
budget decision. Fatal stops preserve their normal job state, error, records and
next action. Every operation checks guard state first, and close always verifies
pending target destruction before tearing down CDP, including after a cutoff.
Already pinned authorities are revalidated synchronously through the complete URL
policy using their pinned addresses, without resolving or dialing, even after a
cutoff. This permits completed observations to reach barrier classification before
the budget decision while preserving destination checks. For unpinned URLs, all
DNS-free syntax, scheme, credential, hostname/IP-literal and port checks run before
the latch; port validation therefore precedes DNS. Allowlisted navigation paths are
then classified, so an allowlisted login URL retains its login barrier. DNS-free
proxy refusals enter the same audit counters exactly once as later policy refusals.
Only the remaining DNS-requiring allow path is interruptible by the latch. Its wait remains bounded. The watcher uses the pinned Playwright
implementation loop, as does the existing async WebSocket close adapter. Tests
bound hangs at an independent 90-second subprocess deadline, with 300-second read
and job budgets.


Route validation pins its validated address set by authority for this bounded
Facebook session. Every later URL still passes the shared scheme/credential/port/
host/IP policy, using that pinned set through the proxy dial and peer check. The
proxy does not resolve a different address between the browser check and its
validated dial. Redirect destinations on new authorities get independent checks.


A disconnected browser transport is not proof that a route, download or browser
process is gone. A failed route action is ignorable only with an observed request
failure or a closed page observed while the browser remains connected. Failed download cancellation and context/browser teardown therefore
fail closed even when CDP is disconnected; a page close exception may be ignored
only when the page's closed event proves that target has already disappeared.


The byte-limit latch also interrupts bounded route/proxy policy and DNS waits
within the existing 50 ms polling interval, without waiting for resolution or the
read deadline. Abandoned resolver tasks cannot dial, remain slot-bounded, and
release their slot when the underlying lookup returns. A sandboxed streaming
regression trips the byte budget while another URL is waiting on DNS.


Stop evidence is monotonic: both state attributes are examined independently,
and a later benign state cannot erase a barrier or form marker. When multiple
recognized stop states coexist, the parser uses the existing classification
order: rate limit, checkpoint/challenge, login wall, then access denial.


Round 10 fixture insertion-mode contract: fixtures are either body-context fragments
with no html/head/body tags, or explicit html/head/body documents in that order.
Nothing but whitespace may follow head before body, or follow body/html; unsupported
structural repairs refuse. Script content containing <!-- refuses, including inert
JSON scripts, because HTML5 script-data escape states differ from rawtext. Style
is rawtext and has no script escape states (non-empty stylesheets still refuse).
The seeded parity corpus covers every insertion boundary and implied-end-tag or
parser-inserted-element class. The comparator retains head/body/none container
identity and has negative tests for tag, namespace, attributes, text, rendering,
order and container. Fixture-only raw UTF-8 prechecks remain 8 MiB; all parity
inputs are below that bound.

All Python consumers of captured tag names use capture.html_tag: only XHTML-
namespace nodes have HTML form/script/void/block semantics. Foreign nodes remain
generic containers. test_r10_namespace_tag_table derives its SVG and MathML rows
from the extractor's HTML_TAGS table; broad attribute stop markers still apply
regardless of namespace or rendering.

An unrendered post/comment/reply/comments-section identity isolates its subtree:
painted descendants cannot attach to an enclosing or sibling identity. A rendered
nested identity owns its own content. Painted text without such an identity is
dropped and adds unattributed_rendered_text, making coverage partial. The same
isolation applies to image alt text and derived block boundaries. Each extractor
stack frame saves and restores this isolation state as well as body/comment state.

Before WALK and HIT_TEST, one shared bounded helper indexes assignedNodes from
every discovered slot, including closed roots, without flattening. Composed parent
uses that reverse map when Element/Text assignedSlot returns null for a closed
root. Both consumers use the same captured accessors and the same 65,536-node
bound. Slotted text and labels therefore retain their shadow ancestors for both
rendering and click validation.

Content-visibility skipping is decided from Chromium's computed display, proved
by test_r10_content_visibility_display_oracle against an independent child-span
checkVisibility() and getClientRects().length oracle, plus direct text. The pinned
build skips hidden contents for block, inline-block, flow-root, flex, inline-flex,
grid, inline-grid, list-item and table-cell. It does not skip for inline, contents,
table, inline-table, row/group/caption table boxes, ruby or ruby-text. Column boxes
already suppress their children. Unsupported ruby-* declarations compute to block.
This measured table-cell result differs from a blanket exclusion of internal table
boxes. Fixture content-visibility is accepted only for independently proved display
values; unmodelled/unsupported display values refuse with the fixed capture error.
Auto-skipped content remains eligible.

A trusted click moves the pointer to the quad first, then resolves and checks
the hit node through its composed ancestors, and immediately sends mousePressed
and mouseReleased. Hover-revealed overlays therefore fail the hit check without
receiving a click; the observed data remains with expansion_failed/partial.

Read-only clobbering, foreign-template/slot and barrier-location rows share one
sandboxed capture session per group. Each row replaces the document and flushes
pending DOM events before capture; no row relies on a previous row's DOM. Click
and expansion regressions retain separate browser sessions and stores, preventing
state, navigation, listener or budget leakage between state-changing cases.
Normalisation preserves each evidence node's head/body/none container as well as
the previously compared dimensions.

The explicit-document fixture form also refuses leading non-whitespace text,
duplicate doctypes and doctype-bearing fragments. Those trigger HTML5 document
insertion rather than the documented body-fragment context. The expanded
insertion-mode corpus and container comparison expose these placements.
