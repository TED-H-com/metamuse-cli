# MetaMuse CLI — 54個驗收案例

以下全部為產品待執行測試，不是本次通過記錄。設計檔格式檢查不能取代runtime或LIVE。

| ID | 級別 | 證據 | 情境 | 通過條件 |
|---|---|---|---|---|
| AT-001 | P0 | OFFLINE/FIXTURE/INTEGRATION | 無憑證初始化 | 離線init／doctor成功；不觸網。 |
| AT-002 | P0 | OFFLINE/FIXTURE/INTEGRATION | 保真匯入 | 繁中、emoji、換行、來源及未知ID保留。 |
| AT-003 | P0 | OFFLINE/FIXTURE/INTEGRATION | 部分壞資料 | 壞列逐項拒絕，正常資料不丟。 |
| AT-004 | P0 | OFFLINE/FIXTURE/INTEGRATION | 回覆樹 | parent正確，缺父層unresolved，循環／跨project拒絕。 |
| AT-005 | P0 | OFFLINE/FIXTURE/INTEGRATION | 字幕時間軸 | end<start拒絕，合理重疊保留並標記。 |
| AT-006 | P0 | OFFLINE/FIXTURE/INTEGRATION | 媒體檔驗證 | exit0但無檔／損壞不標ready。 |
| AT-007 | P0 | OFFLINE/FIXTURE/INTEGRATION | 多來源同素材 | bytes共用，來源不合併。 |
| AT-008 | P0 | OFFLINE/FIXTURE/INTEGRATION | 無worker提交 | accepted+queued+worker_available:false。 |
| AT-009 | P0 | OFFLINE/FIXTURE/INTEGRATION | 多入口工作鎖 | 只有一個executor；其他入口排隊。 |
| AT-010 | P0 | OFFLINE/FIXTURE/INTEGRATION | 一成一敗重啟 | 成功不重做，失敗保留。 |
| AT-011 | P0 | OFFLINE/FIXTURE/INTEGRATION | request_key | 同key同payload回原物件；異payload拒絕。 |
| AT-012 | P0 | OFFLINE/FIXTURE/INTEGRATION | Hung舊process | heartbeat過期不搶仍持有OS鎖的工作。 |
| AT-013 | P0 | OFFLINE/FIXTURE/INTEGRATION | 版本及看不到 | 追加revision；不可見不判刪除。 |
| AT-014 | P0 | OFFLINE/FIXTURE/INTEGRATION | 中文搜尋／分頁 | 預期命中、固定範圍無漏重、不跨project。 |
| AT-015 | P0 | OFFLINE/FIXTURE/INTEGRATION | 精確packet | 引用固定revision；新資料需新packet。 |
| AT-016 | P0 | OFFLINE/FIXTURE/INTEGRATION | 分享越界 | snapshot A批准不涵蓋B／其他帳號。 |
| AT-017 | P0 | OFFLINE/FIXTURE/INTEGRATION | 來源保真 | 自動取得不改成manual。 |
| AT-018 | P0 | OFFLINE/FIXTURE/INTEGRATION | Session建立不明 | open_uncertain，不自動再建立。 |
| AT-019 | P0 | OFFLINE/FIXTURE/INTEGRATION | Muse send timeout | 讀原session，無第二次send。 |
| AT-020 | P0 | OFFLINE/FIXTURE/INTEGRATION | 多段回答 | progress不當完成，late內容保留。 |
| AT-021 | P0 | OFFLINE/FIXTURE/INTEGRATION | 無關消息 | 別session／舊序號／外部手動訊息不誤配。 |
| AT-022 | P0 | OFFLINE/FIXTURE/INTEGRATION | Gateway framing | 單consumer、不完整body不成功、有界buffer。 |
| AT-023 | P0 | OFFLINE/FIXTURE/INTEGRATION | 中性寫稿 | save不另調模型、不強加CTA或永久偏好。 |
| AT-024 | P0 | OFFLINE/FIXTURE/INTEGRATION | 草稿引用 | 不存在來源／asset列錯誤。 |
| AT-025 | P0 | OFFLINE/FIXTURE/INTEGRATION | 離線匯出 | manifest/hash正確，CSV防公式且JSONL保真。 |
| AT-026 | P0 | OFFLINE/FIXTURE/INTEGRATION | MCP stdio | 只有協定上stdout；全部schema與host配對實測。 |
| AT-027 | P0 | OFFLINE/FIXTURE/INTEGRATION | 只有Skill | 回報backend不可用，不假造執行。 |
| AT-028 | P0 | OFFLINE/FIXTURE/INTEGRATION | 秘密防漏 | 假secret／signedURL不出log、packet或公開bundle。 |
| AT-029 | P0 | OFFLINE/FIXTURE/INTEGRATION | 偽批准／到期 | approved:true無效，過期／撤銷／超次數拒絕。 |
| AT-030 | P0 | OFFLINE/FIXTURE/INTEGRATION | URL／SSRF | query假網域、redirect、DNS、subresources都檢查。 |
| AT-031 | P0 | OFFLINE/FIXTURE/INTEGRATION | 路徑／命令注入 | 拒絕越root／symlink及來源option，無shell。 |
| AT-032 | P0 | OFFLINE/FIXTURE/INTEGRATION | Prompt injection | 來源要求傳秘密／改權限僅當資料。 |
| AT-033 | P0 | OFFLINE/FIXTURE/INTEGRATION | Backup／migration | 新workspace還原FK/hash一致；升級失敗不刪庫。 |
| AT-034 | P0 | OFFLINE/FIXTURE/INTEGRATION | 刪除／共用asset | 其他合法引用不誤刪；撤分享／索引，遠端限制明示。 |
| AT-035 | P0 | OFFLINE/FIXTURE/INTEGRATION | 公開包掃描 | 无秘密、私有內容、未授權程式；notices完整。 |
| AT-036 | P0 | MEASURE | 性能 | 固定環境測30次p95/RSS，列實值與deviation。 |
| AT-037 | P0 | OFFLINE/FIXTURE/INTEGRATION | 取消邊界 | 本地停止不說遠端confirmed；不重送。 |
| AT-038 | P0-LIVE | LIVE | Muse LIVE往返 | 核准帳號開題、送虛構/可分享資料、讀回並追問。 |
| AT-039 | P0-LIVE | LIVE | Facebook LIVE | 核准小範圍對照正文、留言、父層與缺漏。 |
| AT-040 | P0-LIVE | LIVE | 媒體 LIVE | 可處理來源metadata／字幕／必要下載可核對。 |
| AT-041 | P0 | OFFLINE/FIXTURE/INTEGRATION | 限制與假零結果 | 登入、challenge、403、429、parser失敗、空資料各異。 |
| AT-042 | P0 | OFFLINE/FIXTURE/INTEGRATION | 離線縱切片 | 假資料→packet preview→mock Muse同discussion追問→restart read-back→草稿引用兩輪→bundle＋另一格式，標示mock；host僅MCP、human僅init/config/CLI批准，不調internals或DB。 |
| AT-043 | P0-LIVE | LIVE | 整合 LIVE旅程 | 獲准來源→真Muse→草稿匯出，不發布。 |
| AT-044 | P1 | OFFLINE/FIXTURE/INTEGRATION | 素材處理 | 只處理核准asset，雲端ASR另分享許可。 |
| AT-045 | P1 | OFFLINE/FIXTURE/INTEGRATION | 來源變更影響 | 標記相關草稿stale，不改已發布。 |
| AT-046 | P1 | OFFLINE/FIXTURE/INTEGRATION | 留言清單 | 已處理不重列待送，弱ID draft_only。 |
| AT-047 | P1 | OFFLINE/FIXTURE/INTEGRATION | 批准綁定 | 改文字／附件／帳號／parent使舊批准失效。 |
| AT-048 | P1 | OFFLINE/FIXTURE/INTEGRATION | 發文結果不明 | needs_reconcile；重啟不重送。 |
| AT-049 | P1-LIVE | LIVE | 單平台LIVE發布 | 核准操作有正確帳號／parent和remote receipt。 |
| AT-050 | P1 | OFFLINE/FIXTURE/INTEGRATION | 跨平台部分成功 | 分別回報，不重送成功項，不自動回滾。 |
| AT-051 | P1 | OFFLINE/FIXTURE/INTEGRATION | 中性metrics | 保留原值／時間／qualifier，null不變0，不生成策略。 |
| AT-052 | P2 | OFFLINE/FIXTURE/INTEGRATION | 排程時區 | DST歧義要明確；過期批准／stale不執行。 |
| AT-053 | P2 | OFFLINE/FIXTURE/INTEGRATION | 额外入口 | 重複事件去重，發送者／workspace驗證，共用store。 |
| AT-054 | P2 | OFFLINE/FIXTURE/INTEGRATION | UI不解鎖 | 沒有批准／實測backend就只有預覽。 |

## Release gate

Alpha須完成P0離線／fixture／integration，有性能實值或明示deviation。整合beta另需AT-038/039/040/043，不用mock替代。外部發布另需AT-047–050，單平台LIVE不升級其他平台能力。

LIVE記backend revision、dependencies、OS、帳號類型／頁型、日期、範圍與人工核對；私有receipt不進public。所有test狀態只能依實際結果改pass/fail/blocked/not_run。不能刪測試來讓功能冒稱完成。

測試用自製或明確可再散布素材；不把私人留言、完整cookie或真實Muse記憶當公開fixture。

## Journey entrypoint evidence

`tests/test_journey.py` covers AT-042 using installed CLI init/permit approve and the
official MCP stdio client for every host action, default attached worker, follow-up and
restart/read-back. `tests/test_approval_cli.py` covers human confirmation, exact digest,
expiry/replay and preview privacy (AT-016/028/029). `tests/test_mcp_worker.py` covers
AT-008/009/012/019/037 with real subprocess lock contention and synthetic checkpoint
SIGTERM/SIGINT/SIGKILL injection. These are OFFLINE/FIXTURE/INTEGRATION, never LIVE;
execution counts/results belong to task reports, not the design cases' not_run baseline.
