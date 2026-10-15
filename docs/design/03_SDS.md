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
redials或240s，超界E_RESOURCE_UNAVAILABLE並請操作者VM尚未running、稍後再試。
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

Confidential write後notify全部workspace live listeners，跨local labels立即close sockets；下一call仍重查durable state。



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
