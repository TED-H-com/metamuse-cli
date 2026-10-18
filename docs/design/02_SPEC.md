# MetaMuse CLI — Product & Interface Specification

文件版 1.1｜2026-10-13｜下列命令與工具是待實作契約。  
MUST 必須；SHOULD 應；MAY 可選。數字是工程預設或驗收目標，不是平台安全頻率或既有效能。

## 1. 功能要求

| ID | 級別 | 功能 | 必須交付 |
|---|---|---|---|
| FR-001 | P0 | 單一產品與共用核心 | 一個套件／版本／workspace store，CLI／MCP 呼叫相同 application functions。 |
| FR-002 | P0 | 初始化與診斷 | 無憑證可離線 init／doctor，區分實作、驗證、環境與權限。 |
| FR-003 | P0 | 本地匯入 | 文字、CSV／JSON／JSONL、VTT／SRT 及指定媒體；逐項錯誤與真實來源。 |
| FR-004 | P0-LIVE | 有界來源收集 | 已獲准且已驗收的來源，指定 URL 與上限；不是全帳號歷史巡查。 |
| FR-005 | P0-LIVE | 留言及回覆 | 保留來源 ID、父子關係、原文和缺漏；真實目標另驗收。 |
| FR-006 | P0 | 影音與字幕 | metadata、可用字幕、必要下載；沒有字幕保持 unavailable，遠端路線另驗收。 |
| FR-007 | P0 | 持久化任務 | 先保存再排程；單 workspace 執行所有權；重啟不丟 queue。 |
| FR-008 | P0 | 續跑與防重複 | 讀取與外部發送重試分離；request_key 去重，不明副作用先對帳。 |
| FR-009 | P0 | 來源與版本 | 原文、原始時間、取得時間、來源方法、可靠 ID 及版本證據。 |
| FR-010 | P0 | 素材關聯與去重 | 相同 bytes 可共用，不能把不同來源、發布或留言身分合併。 |
| FR-011 | P0 | 搜尋與讀取 | project 隔離、分頁、片段與版本引用；中文測試。 |
| FR-012 | P0 | 對外分享資料包 | 固定資料版本、必要摘錄、預覽、來源方法與分享範圍。 |
| FR-013 | P0-LIVE | Muse 對話 | 主題 session 映射、文字問答和追問；不接管 harness。 |
| FR-014 | P0 | Muse 失敗恢復 | 送達不明先讀原對話，保存多段回答；回答不等於研究完成。 |
| FR-015 | P0 | 主 Agent 主導 | 主 Agent 提問題和文案，工具沒有第二個隱藏策略 Agent。 |
| FR-016 | P0 | 草稿與偏好 | 草稿版本、平台變體及明示偏好；無作者公式或自動永久學習。 |
| FR-017 | P0 | 檢查與匯出 | 客觀格式、引用缺口、文字／Markdown／素材包；不冒稱發布。 |
| FR-018 | P0 | CLI／MCP／Skill | 同一份工具契約、乾淨 stdio、明確錯誤；Skill 不授予權限。 |
| FR-019 | P0 | 憑證與權限 | 平台帳號隔離，秘密不進 prompt／log／public repo。 |
| FR-020 | P0 | 輸入與程序安全 | URL、出口、路徑、大小、固定 argv；來源文字不能變程式。 |
| FR-021 | P0 | 公開發行 | 授權清冊、乾淨歷史、虛構樣本、非官方聲明、能力實測表。 |
| FR-022 | P0 | 刪除與備份 | 索引與派生物、剩餘引用、backup restore；不保證遠端抹除。 |
| FR-023 | P0 | 觀測与容量 | 真實 count／bytes／duration；未知用量／費用不捏造。 |
| FR-024 | P1 | 基礎素材處理 | 可選 ASR、音訊、指定裁切、轉檔、圖片壓縮。 |
| FR-025 | P1 | 來源變更與影響 | 追加新觀測、標記引用草稿過期；不可見不推定刪除。 |
| FR-026 | P1 | 留言工作清單 | 待回覆／草稿／已處理，正確 parent；預設 draft_only。 |
| FR-027 | P1-LIVE | 發布與回覆 | 預覽及批准綁目標／內容／附件／期限；單目的地驗收。 |
| FR-028 | P1 | 發布對帳 | 不明禁止重送；各平台結果分開，不保證原子發布。 |
| FR-029 | P1 | 中性成效快照 | 原值、定義、時間、分母、缺值；不自動制定策略。 |
| FR-030 | P2 | 指定排程與監測 | 只有使用者指定時區、日期及範圍；無最佳時段策略。 |
| FR-031 | P2 | 其他入口與協作者 | 訊息入口／provider 共用核心，不先做外掛框架。 |
| FR-032 | P2 | 輕量工作台 | 現有工具的視圖，不新增資料真相、SaaS 或隱藏 AI。 |

## 2. 範圍與能力表

一個 workspace 對應本機使用者，一個 MCP process 啟動時綁定一個 workspace；工具不能任意切 data root。初版不是多租戶隔離服務。project 是資料選擇邊界。

資料模型可以保存多種 platform 名稱，但不代表每一種都可讀取／發布。每項能力分開保存 implementation（absent/implemented）、verification（unverified/fixture_passed/live_verified/regressed）、environment（ready/missing_dependency/needs_auth/unavailable）、permission（not_configured/granted_scope/denied），再計算 effective。驗收附 backend version、帳號類型、頁型、時間與證據。

離線 doctor 不觸網；明示 live 才做獲准健康檢查。缺 Muse 或 browser 不阻擋本地功能。

## 3. 共用資料契約

| 物件 | 必要資訊與規則 |
|---|---|
| Source | platform、native scope/id、原始／正規化 URL、collection_method、取得時間、使用依據引用。 |
| ItemObservation | project/source、kind(post/comment/reply)、native ID、parent、原文、原始與解析時間、revision、完整性與缺值。 |
| Asset | SHA-256、MIME、實際 bytes、時長、私有路徑引用、所有來源關係；相同bytes不抹掉不同來源。 |
| TranscriptSegment | asset、revision、start/end毫秒、語言、原文、provided/auto/asr/manual、校對狀態；removed預設false。移除是revision≥2且text空字串的追加修正，同transaction標記派生stale；目前搜尋與解析不回已移除cue，明示舊文字版本仍可解析為stale。 |
| Packet | 固定來源revision／字幕區間、實際送出文本、redactions、來源方式、範圍、digest。 |
| Discussion/Exchange | project、remote session、question、packet版本、提交意圖／狀態、cursor、raw messages與關聯狀態。 |
| Draft | 完整文字、revision、platform variants、facts／assets、明示偏好、digest。 |
| Publication（P1） | 目標帳號／目的地／parent、精確payload／附件、批准、attempt、remote receipt及對帳。 |
| MetricSnapshot（P1） | 平台／指標定義、原值、單位、取得時間、觀測期間、qualifier；missing不等於0。 |

來源方法保留真實值。file_import只表示這次讀檔，上游原取得方式另記 upstream_collection_method；不知道就 unknown，不能將自動來源改稱人工找到。操作者聲明與系統實際觀测分開。

native ID 缺少就建立內部 ID＋weak identity；不得用本次第幾筆當穩定留言 ID。parent沒取得時保留 unresolved，不能亂接到其他留言。相似文字不是同一筆資料。

## 4. 收集與素材

輸入為指定 URL、本地檔或文字。resources 明列 post/comments/media_metadata/subtitles/media_file。預設不下載完整影片；僅 metadata和所需可用字幕。無字幕回 unavailable；ASR另須啟用與分享許可。

JSON/JSONL/CSV逐項驗證，保留繁中、emoji、換行及無文字媒體項目。VTT/SRT驗證start≥0、end≥start；合理重疊可保留並標記。格式不支援就具體報錯，不當成HTML或可執行檔。

取得上限和資料完整性分開。多次滾動無新增只是一種停止理由，不能證明全部留言。登入頁、checkpoint、權限拒絕、限流、selector變動、timeout與真實空資料必須有不同結果。

同媒體bytes可共用asset，但保留貼文、來源與字幕關聯。只讀字幕只能說依字幕判讀，不能說已分析影片畫面。

## 5. Jobs 與結果

```text
queued → running → succeeded | partial | failed | awaiting_user | cancelled
```

accepted僅表示已commit job；沒有worker時必須queued+worker_available:false。完成只指本次有界工作，另存 coverage_status（unknown/scope_satisfied/bounded/partial）、requested/observed_scope、stop_reason、missing_fields。

同operation＋request_key相同payload回原結果；同key異payload回E_IDEMPOTENCY_CONFLICT。要新觀測用新key。本地去重不是遠端exactly-once保證。

job取消先持久化，在安全邊界停止。已發送Muse／發文只能回本地停止與remote cancel狀態；沒證據不可回remote confirmed。中止MCP等待不自動取消已接受job，需明確job_control。

未claim step的worker遇暫時SQLite BUSY／LOCKED須保持存活，以有界jitter backoff重試並可stop；
每個unclaimed lock attempt最多1秒，stop加exit cleanup最多2秒lock wait；claimed transaction仍30秒，
finalization/fallback及zero-handoff保存可合計120秒，細節及限制見SDS。
既有worker status顯示有界storage_busy_retries。已claim步驟仍遵循原failure／uncertainty規則，
不自動重送external_submit。非busy的attached worker失敗顯示failed與固定worker_error_code，
不暴露exception內容。一般CLI等待storage lock逾時仍回E_STORAGE；詳細timeout與停止界線見SDS。

## 6. Muse 對話契約

只依賴最小session、send和history。主Agent提供問題和文案，MetaMuse不自動重規劃研究。Muse自然語言原文照存，不假定strict JSON或可註冊任意tools。

```text
prepared → submitting → sent | send_uncertain | failed_before_send
sent / send_uncertain → reply_observed → closed
```

一個discussion同時一個未結算exchange。新topic本身也會外送，open需要存取範圍。send須驗packet和分享grant，保存intent後只送一次。timeout先查原session；沒有看見回覆不等於沒有送達。

保留progress、多段回答與晚到訊息。關聯必須檢查session、baseline、可辨識user echo、role和時間，不能只靠最新一則assistant訊息。歧義保存為ambiguous，不直接配對。回覆存在不等於研究完成；由host結算answered/needs_more_data/abandoned。
echo關聯以同一deterministic redaction比較outbound與observation；wire保留host原文。prepared與已證明not_submitted的exchange不參與關聯。訊息首次observation必須晚於該send boundary的高水位；enrichment不改首次時間。無marker且同文符合多個已提交exchange時保存sticky ambiguous，不認領訊息或建立echo proof。history proof與第一次transport outcome分開保存，後到ack不可丟失。

abandoned時若外部提交仍不明，discussion應quarantined，不能立即派下一題繞過未解提交。close_exchange不是遠端cancel。side chat不是跨客戶保密隔離；本地path不是Muse可讀資源。分析prompt不是停用Muse既有寫入工具的權限鎖。

B10實作補充：零bytes證據只接受同process內transport記帳；持久化結果前crash仍unknown。已證明零handoff的記帳先於cancel保存，原step為local failed/remote not_submitted；同時host close不重開exchange，settlement後才解除原step隔離。記帳或settlement寫入失敗時持久化失敗事件、保留uncertain並繼續queue。
`not_submitted`不退grant、不requeue；精確模式新提交要新key與grant；有界研究可使用原grant剩餘額度但不能重送unknown。binding只含provider/account/auth_ref，
discussion固定snapshot。fixture backend須明示選擇，discussion/message/observation及content envelope標mock，
inspect只fixture_passed。read預設local；refresh排safe_read job＋current access grant。open對帳可明示原discussion_id，
unknown只排list job，不再create；已mapped discussion則排history baseline read。cursor由workspace key簽名，綁discussion/exchange與observation高水位。
open在提交與boundary兩次檢查同project/account的未解create；已queued的第二次create也不得越過，grant未使用、step本地failed。

B09安全狀態：authenticated CVM hint bind以單一bulk transaction保存全部usable identities，
closure只計算一次；confidential evidence先獨立commit，才檢查local label。無recording cap。

Notary freshness is only a cache hint, not local signature/expiry verification.
Missing or undecodable third-segment JSON, a non-object document or non-list restrictions
means no hint. Skip non-strings, non-timeout prefixes and entries whose stripped timeout:
remainder fails Python float(), is non-finite or is <= 0. The first positive finite
value decides stale iff expiry <= now + 60 seconds; ignore later entries. None usable
means not stale. Mint at most twice; a twice-stale remint stops before wake or dial.

## 7. 分享與草稿

Packet固定版本；修正建立新packet。批准可以涵蓋已展示的project snapshot，允許host在其中自行選資料和有限追問；新增來源或帳號需新範圍。不需要每個讀取都打斷使用者，但每次實際外送內容要可回查。

Question也是分享資料。去姓名不代表匿名，URL和原文仍可能識別人物。來源內容和Muse回答都不能建立grant、執行shell、修改取得方式或擴大授權。

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

Draft save只保存host提供文字；更新必須expected_revision，不默默覆蓋。預設language=und、emoji=unspecified、plain_text、this_draft；使用者明示才改。沒有作者人設或策略。

AT-042引用補充：EvidenceRef新增`muse_observation`，id為host讀回的immutable observation，revision固定1。
Store驗證同project的discussion與observation，不接受host自填backend/mock。Draft save、preview及所有export
（bundle/jsonl/markdown/text/csv）的Muse-derived records保留`backend`、`mock`、`research_complete:false`
及`muse_evidence`（exact ref、stored digest、discussion、observed_at）；transitive draft引用也保留。
fixture的mock固定true；live的backend照實保存，不憑內容升級verification。Export manifest亦保留同樣attribution。
Text在原文前加Muse evidence notice；Markdown/CSV的source攜帶attribution；JSONL保真text欄位。
Packet只接受原始item/transcript，不接受Muse observation或draft；引用Muse不授權、不重分享、不證明研究完成。
Stored JSON拒絕所有層級的duplicate object keys，使用既有固定E_STORAGE且不echo值。
Draft/packet SQL guards拒絕重複kind/id/revision；Muse revision必須JSON integer 1，不接受true。
Product bundle verifier從stored citations核對manifest muse_evidence、JSONL records及Markdown source的
backend/mock/research_complete；即使重算hash一致，缺少或偽造標記仍是固定export unavailable錯誤。
Bare session-cookie pairs are covered by the shared name-based redactor; the AT-042
bundle regression is a normal passing test.

Preview檢查客觀格式、素材可用、引用存在、日期/數字衝突候選。引用存在不代表語義被支持。Export生成本地文件與manifest，不發布或上傳。

### Bounded Muse research (B08/B09/B10; implemented, OFFLINE/FIXTURE)

Research approval applies the current Muse topic credential check and refuses packets
that no longer pass the existing preview redaction fixed-point check before issuing
either Permit. Varied questions retain the existing submit and pre-send privacy checks.

Exact-action `permit approve` remains available and binds each verbatim question/wire
payload. Human-only `permit research --open-request-file PATH --packet ID --rounds N
--max-reads N --expires-at TIMESTAMP --request-key KEY [--exact-digest SHA256]` instead
creates one existing access Permit and one existing share Permit atomically. No MCP
grant tool, new Permit kind, permission table, worker or orchestrator is added.
The open file is an existing MuseOpenRequest with project/topic/request_key and no
grant or reconciliation discussion_id. The discussion ID is the existing deterministic
prepare_open identity. Review binds current configured provider/account/backend,
project, topic/open key, actual prepared open payload/digest, one packet ID/digest and
exact evidence selection, absolute expiry and both budgets. Creation rechecks those
bindings after confirmation; a changed review cannot mint a different pair.
Topic and packet privacy checks run before write ownership. Pair issuance and manual
research Permit creation then compare the exact validated inputs, current configuration,
packet bytes/digest/selection and latest revisions inside the transaction. Any intervening
change refuses without a Permit, event or receipt; caller-supplied prechecked flags grant
no authority. No redaction or sanitization runs while the writer is held.

One access grant covers that one open and at most N remote history/reconciliation reads
of its discussion (including future exchanges and the remote mapping learned later).
The share grant covers at most N host-authored questions with exactly that packet;
question variation is explicit human consent. Bounds: 1–20 rounds, 1–200 remote reads,
timezone-aware expiry within 24h, and current workspace exchange/text caps. Defaults
are not silently enlarged. Local stored reads spend no remote-read uses. Every new
question shares the original round counter; concurrent reservations cannot exceed it.
A retry of a reserved safe read rechecks scope/expiry/revocation without another use.
The pair is issued in one transaction with one durable request receipt: replay returns
the same IDs/current counters and never renews, refills or unrevokes them; changed
payload at the same key conflicts and a failed second insert leaves neither grant.

Queue and reservation checks load actual persisted discussion, prepared exchange/wire,
packet selection/revisions and current configuration. Changed packet/data revision,
account/backend, project, discussion, topic or open key needs a new human scope.
No collect/post/reply/social/external-publishing permission follows. Actual questions,
targets, packet versions/digests and results remain in the existing durable audit.
Topic labels cannot prove arbitrary question semantics: the UI distinguishes enforced
identities/bounds from the host instruction to stay on topic and include no other data.
It does not claim free text is derived only from the packet. Unknown delivery still
requires reconciliation of the original intent, with no resend or refunded use.
This implemented workflow has synthetic evidence only; it does not establish
real-account LIVE success.

## 8. CLI

所有命令是開發目標。全域 --workspace、--json、--non-interactive 放子命令前。

```text
metamuse demo
metamuse init
metamuse doctor [--live]
metamuse project create --name NAME --request-key KEY
metamuse add --project ID --file PATH --kind FORMAT --request-key KEY [--run]
metamuse add --project ID --url URL --resources LIST --access-grant ID --request-key KEY [--run]
metamuse jobs list [--project ID] [--cursor CURSOR] [--limit N]
metamuse jobs get ID
metamuse jobs resume ID --request-key KEY
metamuse jobs cancel ID --request-key KEY
metamuse worker run [--once]
metamuse search --project ID --query TEXT [--cursor CURSOR] [--limit N]
metamuse packet prepare --project ID --selection-file PATH --request-key KEY
metamuse packet preview ID
metamuse muse open --project ID --topic TEXT [--access-grant ID] --request-key KEY [--discussion ID]
metamuse muse send --discussion ID --packet ID --question-file PATH [--share-grant ID] --request-key KEY
metamuse muse read --exchange ID [--refresh] [--access-grant ID] [--cursor CURSOR]
metamuse muse close-exchange ID --outcome answered|needs_more_data|abandoned --request-key KEY
metamuse draft save --project ID --text-file PATH --platform PLATFORM --request-key KEY
metamuse draft preview ID --revision N
metamuse export --project ID --refs-file PATH --format FORMAT --request-key KEY
metamuse auth import --file PATH [--provider LABEL] [--account LABEL]
metamuse auth list
metamuse auth revoke --auth-ref REF
metamuse connect muse --account LABEL --auth-file PATH [--provider LABEL] [--enable-network]
metamuse connect facebook --account LABEL --auth-file PATH [--enable-network]
metamuse connect publishing --account LABEL --auth-file PATH --page ID --api-version VERSION [--enable-network]
metamuse skill export --dest DIR [--force]
metamuse mcp [--no-worker]
metamuse permit approve --operation OP --request-file PATH --request-key KEY [--exact-digest SHA256] [--expires-at TIMESTAMP] [--max-uses N]
metamuse permit research --open-request-file PATH --packet ID --rounds N --max-reads N --expires-at TIMESTAMP --request-key KEY [--exact-digest SHA256]
```

更新草稿加 --draft ID --expected-revision N。明示引用／附件／變體／偏好可用 --facts-file、--assets-file、--variant-file、--preferences-file（JSON）。人類本地auth import／permit create另提供；不是能讓MCP自己approved:true的工具。憑證匯入需操作者指定檔案，不讀任意主瀏覽器profile。

`metamuse demo` 使用套件內的合成留言與預寫草稿，在新的私有工作區執行既有 CLI 匯入、搜尋、引用與匯出。無帳號、無網路或 AI 呼叫；既有路徑拒絕覆寫。`--json` 仍只輸出一個 envelope，`--workspace` 可指定尚不存在的範例工作區。

CLI add預設持久化；--run前景執行。沒有長駐worker時前景命令結束不代表工作會在背景繼續。MCP預設附同套件worker；--no-worker退出。使用與worker run相同Executor／OS lock，同workspace只能一個executor。若鎖被持有，server仍服務但worker_available:false、附next action；本process不重試取得鎖，原owner退出後重啟MCP。附worker使用獨立Store instance連同一workspace，MCP reader不排在worker write wait後；startup ready handoff最多2秒，未commit仍starting不可用。只有附帶worker持鎖且運作時MCP envelope才回true，停止server後不承諾背景續跑。

B08 local safety commands: `auth import --file PATH [--provider LABEL] [--account LABEL]`;
`auth list`; `auth revoke --auth-ref REF`;
`permit create --project ID --kind access|share --scope-file PATH --provider LABEL
--account LABEL --issuer LABEL --expires-at TIMESTAMP --max-uses N --request-key KEY
[--exact-digest SHA256]`; `permit revoke ID --request-key KEY`; `permit list --project ID`.
Expiry is required, timezone-aware, and at most 30 days after issuance; uses are 1–1000.
Exact access targets, exact packets or an immutable displayed evidence snapshot bind
project/provider/account. [Safety API](../dev/SAFETY_API.md) defines scope formats and
backend obligations. These are human-only commands, never MCP self-approval tools.

`auth list` returns provider, account, auth_ref, created_at, storage_kind and config_bound
for every imported reference, without values or filesystem paths. Every import creates
a new reference; revocation unlinks exactly that owned record or symlink without
following it. FIFOs/directories require fixed manual cleanup guidance. It reports any current Muse config binding still pointing at
the revoked ref, with a next action to import and rebind. Config is not rewritten.
Resolving a revoked, missing or corrupt ref returns fixed `E_AUTH_REQUIRED`, with neither
an exception cause nor context retaining the file contents. Auth lifecycle commands
require human interaction and have no MCP tools.

Human-only `connect` validates the named Muse cookie-header JSON, Facebook Playwright
storage-state JSON or raw Page token using the runtime's format validator, imports it
and atomically binds that service's existing configuration. TOML comments and unrelated
settings remain. Setup is local and returns account_verified=false; it never grants an
action or claims a working remote login. `--enable-network` explicitly sets the workspace
network mode to live; otherwise the current mode remains. Concurrent connect commands
use an exclusive workspace lock; a detected config edit or failed file replacement
removes the new credential reference and preserves the prior configuration. Old refs
are not implicitly revoked. Auth list/revoke report config_bound for Muse, Facebook,
publishing and remote-media bindings.

B13 Facebook collect without/with an unknown permit uses the shared collect.remote
approval preview, following the same human permit approve workflow as remote media.
Review performs no credential resolution, browser launch or network IO. Existing access
digests bind project/account, normalized single-post URL and resources; request/browser
limits remain operator controlled and are displayed as such. Review/submit reload current
workspace configuration. Disabled, offline, invalid-input and missing-account/auth-ref
settings produce their actual refusal without a review. Denied grants never acquire a
new review implicitly. Publication reviews also return an executable operator command,
full text and authoritative Page/account/project/digest, with a fresh approval key when
a prior key has been used. Local publication data.text remains the verbatim draft text.

`metamuse skill export` copies the packaged Skill into the directory named by `--dest`.
It refuses to overwrite an existing file unless `--force` is set. It does not choose an
agent or host config directory. It is not an MCP tool.

Grant-requiring Muse open/send/read (refresh and open reconciliation) accepts an omitted or
unknown grant and returns E_SCOPE_REQUIRED with null data and a nullable `approval` preview.
It creates no grant, discussion, exchange or job. Preview includes operation/permit_operation,
kind, project, configured provider/account, exact action digest, destination, topic/question,
and for shares packet ID/digest/UTF-8 bytes plus the exact `packet preview` command.
`request` is a non-secret copy to save at `request_file`; `next_action` is the exact human
CLI command. Topics and questions pass the credential check before preview creation and
are displayed exactly as sent, using the same strings in the summary and request.
Other metadata is redacted per field before JSON encoding. The digest binds the original
bytes; a changed request cannot reuse it. Expired, revoked, exhausted,
mismatched grants (and non-exact shares in exact-action mode) retain E_SCOPE_DENIED
and their actual message,
without a preview; repeat the call without a grant for a fresh review. Stale packets
return E_REFERENCE_INVALID "Packet is stale; prepare a new packet" in submit and preview.
Policy and authentication failures retain their actual errors without a preview.
For B12 remote media, the exact digest also binds the whole configured approval table:
canonical sorted extractor keys, normalized sorted hosts and opaque auth_ref or null.
The review identifies first-hop and later-hop entries and shows each cookie label/account.
Selection uses those bound entries; even an unrelated approval edit invalidates an
outstanding grant before consumption or egress and requires a fresh review.
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
The preview's operator command uses the next unused digest-based approval key, so a
fresh review cannot replay an exhausted/revoked/expired grant. Repeating a previously
submitted approval command still replays its original permit and mutable use state.

`permit approve` recomputes the preview through the same preparation code and displays it
on stderr, including the effective absolute UTC expires_at, remaining duration and
max_uses before confirmation. These are the stored values (defaults: 24 hours and one
use); replay displays the original expiry. Every callback-summary line is quoted;
recomputed operation/destination/account/project/digest fields appear separately before
confirmation. Terminal controls and Unicode formatting/line-separator characters are
escaped for display, preserving original stored text and JSON data. It requires typed `yes` at a terminal or equality with `--exact-digest` in a
non-TTY operator CLI. `--non-interactive` refuses this command even with an exact digest.
Provider/account cannot be supplied in model text or flags. Every approval has an expiry:
default 24 hours, or explicit timezone-aware `--expires-at` within 30 days. Exact action
and packet scope are stored in the existing permit; repeated approval with the same key
and default expiry replays the original permit without extending it. No MCP approval tool.

JSON envelope：contract_version、request_id、status(ok/accepted/partial/error)、data、error、warnings、next_cursor、approval（nullable）。exit 0代表本命令成功，可能僅queued；2輸入錯、3需要人／權限、4backend不相容、5部分或等待逾時、6需對帳、7儲存／內部錯、130中止。

## 9. MCP與Skill

十五個P0工具見contracts/mcp-tools.json：inspect、project_create、collect、job_get、job_list、job_control、search、packet_prepare、muse_open、muse_send、muse_read、muse_close_exchange、draft_save、draft_preview、export。P0不曝光publish_commit或raw gateway。

B19 adds four shared P1 tools: publication_preview, publication_submit, publication_get,
publication_reconcile. The first provider is experimental Facebook Page new text posts,
using an explicit operator-selected Graph API version and a Page token. Personal profiles,
groups, replies, assets and nonempty variants are refused. Local preview/get never use
the network. Preview returns the human review in data.approval; a missing grant on submit
or reconcile uses the existing E_SCOPE_REQUIRED envelope.approval contract. Only the
human CLI may issue the dedicated publication Permit, with one exact operation/digest,
account and expiry. Source/MCP fields cannot issue or widen that Permit.

The exact action binds project, draft identity/current revision/digest, verbatim text,
platform/account/Page/parent, empty attachment identity, backend, API version and auth_ref.
Review and issuance compare the current non-stale draft again in the grant transaction.
Submission and the committed external boundary recheck current draft and permission.
An unresolved publication quarantines that draft/account/Page across revisions and keys.
An entered attempt is unique per publication and cannot be replaced, refunded or resent.
A proven failure before the committed boundary can accept a fresh human grant and new
queued job; the prior step cannot execute the replacement action.

Read-back must match the saved post receipt, Page identity, message and published state.
The Facebook Page provider permits exactly one observed difference: omission of one
terminal LF from the approved text (not a terminal CRLF). All remaining characters must
match verbatim; no trimming of spaces, other line endings, leading or internal whitespace
is allowed. The outbound payload, approval digest and saved draft stay unchanged.
The job result and original step checkpoint record the comparison mode and SHA-256 of
both approved and observed message bytes, so this exception is not reported as exact.
Saving the receipt precedes read-back; domain sent_verified and original job/step
success commit together. Reconcile is a separately approved safe read of only that saved
receipt and settles the original uncertain step. No receipt remains unknown; lack of a
search result never permits resending. B19 has synthetic evidence only. B20/AT-049 real
account verification and AT-050 cross-platform partial success remain unverified/out of
this single-provider scope. Setup and limitations are in docs/dev/PUBLISHING.md.

所有state handle顯式傳ID，不靠connection上次選過project。stdout只給SDK協定；log、下載器和warning導向stderr。資料分頁或受保護resource引用，不回整部影片。

MCP SDK／host／協定版本在B01選定並實測，不宣稱任何host必然支援。輸出schema是我們的結果格式，不是Muse受約束生成。tool annotations不能代替授權；muse_send和refresh不可假稱read-only。

Skill短主檔＋按需references，先查實際工具；只有Skill不代表backend已安裝或登入。
安裝已打包的 Skill 用人類 CLI `metamuse skill export --dest DIR [--force]`，不新增 MCP 工具，也不會自己寫入 agent 或 host 設定目錄。

## 10. 起始限制

| 項目 | 工程預設 |
|---|---|
| 同workspace executor | 1 |
| 單job URL／item | 10／500 |
| 單media／單job bytes | 512 MiB／1 GiB |
| 文字匯入 | 20 MiB |
| Packet／question外送 | 48 KiB／8 KiB UTF-8；不是provider token限制 |
| Job wall clock／單read | 15分鐘／30秒 |
| 安全讀取重試 | 最多2次，尊重Retry-After；拒絕／驗證不套用 |
| Muse polling | 5秒退避到30秒，120秒未讀回就回等待警告，不重送 |
| 授權內自主追問 | 起始最多3個exchange，host決定，不是自動互聊 |
| 結果分頁 | 預設50，最多200 |

提高上限不擴大平台許可。實際bytes持續計量，不信任Content-Length或來源宣稱。

## 11. 錯誤與非功能需求

錯誤碼：E_INPUT_INVALID、E_REFERENCE_INVALID、E_SCOPE_REQUIRED、E_SCOPE_DENIED、E_AUTH_REQUIRED、E_DEPENDENCY_MISSING、E_UNSUPPORTED、E_PROTOCOL_MISMATCH、E_ACCESS_DENIED、E_CHECKPOINT_REQUIRED、E_RATE_LIMITED、E_SELECTOR_DRIFT、E_NETWORK_TIMEOUT、E_JOB_BUSY、E_IDEMPOTENCY_CONFLICT、E_EXTERNAL_STATE_UNKNOWN、E_STORAGE、E_LIMIT_REACHED、E_DATA_STALE、E_RESOURCE_UNAVAILABLE。

附retry_class（never/safe_read/reconcile/user_action）與next_action，不吞錯回空列表。

參考性能目標（尚未量測）：4vCPU／8GiB／SSD Linux，固定10,000筆樣本，至少30次，submit p95≤1秒、搜尋首50筆p95≤2秒；20MiB匯入core peak RSS≤512MiB，browser/codec另量測。未達列deviation，不捏造數據。

Linux／WSL2為首個執行目標；其他平台按實測列。無憑證離線路線零背景網路。升級須migration/backup restore，不刪庫過關。product tests初始not_run。

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

Within an HTML/XML element, a sensitive name attribute makes all value and
content attributes private; type=password makes value private. Attribute order,
quoting and encoding do not alter this rule. Username and description fields
remain ordinary public attributes.


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
its parameter operator), or a complete `${{ … }}` template expression. Random prose before the brace cannot turn an incomplete
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

B12 canonical provenance stores only reviewable display URLs on bound approved
hosts, preferring webpage_url, then extract URL, then reviewed input; canonical_basis
records the winner. Refused/signed candidates never persist. Subtitle language/method
and upstream identities are bounded patterns; text is bounded and redacted.
A 2xx subtitle/media HTML or BOM/whitespace-sniffed HTML login form stops with
E_AUTH_REQUIRED before later dials or resource rows; page classification stays with
the extractor. Every body block checks the cheap deadline; durable scan guards are
paced at 250 ms plus first/last. Changed subtitle bytes retire omitted cue indexes
atomically, including changed zero-valid-cue replacements. Failed fetches and listing
omissions do not imply removal. See COLLECT.md for bounds and measured costs.
B13 classifies login URLs as E_AUTH_REQUIRED and checkpoint/challenge URLs as
E_CHECKPOINT_REQUIRED from observed main-frame navigations and the final URL,
before parsing any underlying post container. All browser barriers stop without
retry; no synthetic DOM marker is needed for an encrypted redirect barrier.

Resume checkpoints persist cumulative expansion, byte and accepted-item consumption.
Clicks are charged before execution; observations persist consumption before parsing.
A fresh proxy receives only remaining bytes. Accepted reliable identities are stored
as bounded digests so unchanged re-observation does not refill or consume the item
budget again; edits remain revisions of the same accepted identity. Fixture byte
accounting uses cumulative UTF-8 observation bytes; real sessions use the larger
of that accounting and relayed wire bytes. Wall-clock fencing remains core-owned.

After a committed batch, resumed weak observations without native IDs are never re-imported. Neither text
nor DOM position is a deduplication key. Previously committed weak items remain; new
weak observations may be omitted, with weak_identity_resume and partial coverage.
Reliable identities still support edits and new observations.

After an expansion, the production browser waits within the read deadline for a
new relevant comment/reply node or changed identified content. An unchanged loaded document or disappearing button is
not progress evidence. Deadline expiry records expansion_progress_timeout with
partial coverage and comments_empty=false. Unperformed clicks retain observed data with expansion_failed/partial; only
document failures retain auth/access codes.

B13 accumulates reliable observations across expansions by platform/scope/native ID
and preserves changed text as ordered revisions. Pagination/virtualization cannot
discard a previously seen reliable comment. The union consumes the item budget;
retention has at most max_items plus one lookahead identity, with expansion-bounded
revisions. Weak records are retained only from the initial snapshot; later weak
observations add weak_identity_expansion/partial instead of text or position merging.

Genuine empty comments require an identified post, a present comments section,
explicit comments-exhausted evidence, a rendered count of zero, no observed comments
across snapshots, no controls and no progress uncertainty. For fixtures that evidence
is data-comments-exhausted=true (or JSON comments_exhausted=true); LIVE mappings
remain unverified. Absence of controls alone never proves exhaustion. Contradictory
counts add rendered_count_conflict; positive counts without sufficient comments or
controls add missing_comments_controls; absent exhaustion evidence adds
comments_scope_unverified. Every gap yields partial coverage and incomplete post
evidence, never scope_satisfied. Counts whose semantics are unknown are not proof.

A reply without a parent identifier adds parent_comment independently of the
core unresolved_parent field. New reply evidence is incomplete and the job is
partial; null parent is never interpreted as a proven complete parent tree.

DOM text is decoded once by HTMLParser, preserves edge whitespace and explicit
empty bodies, and preserves br as newline. Nested block starts/ends introduce
newline boundaries without adding a second boundary beside an existing newline.
JSON text is retained as given. No normalization replaces the preserved text;
revision hashes use canonical JSON {text: preserved_text}, with no Unicode or
whitespace normalization. Reliable identity uses native tokens, never text hashes.

B13 post share inputs accept primary-host /share/p/<1..64 ASCII alphanumerics>/ with
no query/fragment and normalize to https://www.facebook.com/share/p/<code>/. B08's
access digest binds the normalized share URL/resources; page type remains
post_permalink. The browser resolves through the proxy/barrier boundary to one
supported post; other final kinds, including group posts, return E_UNSUPPORTED
with remote-media (B12) guidance for Reels/videos. Step data/observed scope and
Source.use_basis contain requested_locator/resolved_permalink; source original_url
is the requested input and canonical_url is the resolved query-free post URL.
Resume resolves the share again and requires the saved actor/token; a different
identity is E_DATA_STALE, preserves committed data and imports no new target.

B13 retains ordered text transitions by reliable native identity, including A → B → A.
Only consecutive identical observations are discarded. Revision evidence carries the
contributing HTML snapshot hash and verified requested/resolved locator, permit and
account; a new Source identifies its first contributing snapshot.


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
