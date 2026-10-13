# MetaMuse CLI — Development Plan

文件版 1.1｜2026-10-13｜重新提供的開發交接文件，不是產品發行。  
命名：MetaMuse CLI；repo／distribution 暫定 `metamuse-cli`；指令、Python package 與 Skill：`metamuse`。

## 1. 產品與成功定義

一套本地優先的社群資料與內容工具：收集／匯入 → 貼文、留言、影音和字幕關聯 → 有來源的資料包 → 主 Agent 與 Muse 討論 → 草稿／報告 → 預覽及匯出。後續才加入明確獲准的發布、回覆與成效。

不是五個 repo 的 launcher。共用資料、任務、設定、來源、素材和錯誤處理；使用者不用管理三套 queue 或在 CSV 之間拼接資料。

主 Agent 決定研究與文案。Muse 是對話協作者，保留自己的 harness 和服務邊界。MetaMuse 保存並執行有界功能，不隱藏另一個主 Agent。

## 2. 已定案的工程方向

一個 public repo、一個 Python package、一個版本、一個 workspace SQLite。CLI 和 MCP 直接呼叫相同 application functions；Skill 是中性操作指引。少量按責任分檔是正常維護，不是拆成多個產品。

吸收授權明確的功能程式並自行維護，不必永久兼容所有舊 repo。yt-dlp、Playwright、ffmpeg、MCP SDK 保持正常依賴，不重造下載器或密碼學。

第一版不做微服務、Redis、Kubernetes、外掛市場、多租戶 SaaS、向量庫或大型 Dashboard。先完整流程，再擴平台。

## 3. 上游採納

| 來源 | 採用 | 排除／限制 |
|---|---|---|
| 自有 castle_fb_crawl | 影音連結與 yt-dlp 的使用經驗，直接寫入共用 jobs／assets。 | 舊 links.txt 佇列、失敗後全清除及私人部署資料。 |
| tlyu0419/facebook_crawler | 有用的貼文解析／分頁概念和授權允許的程式。 | 不繼承「免登入永遠可用」或舊重試行為。 |
| boo283/Facebook_comment_crawler | 留言／回覆的中性功能需求。 | 明確授權前不複製或機械翻寫原碼。 |
| nikships/muse-cli | 最小 auth／session／send／history 及必要 framing，登記來源與授權。 | 不搬全 CLI UI／更新器／258 個 raw 路由，不修改 Muse harness。 |
| Hao0321/claude-skill-social-post | 草稿版本、客觀格式、批准與對帳、成效快照。 | 作者聲線、人設、公式、hook／CTA、最佳時間、爆款策略和私人治理系統。 |

這是採納計畫，不是宣稱程式已經移植。授權與平台使用許可不同；採用前鎖定 commit、路徑、license 及修改摘要。來源與先前查核界線見 `06_SOURCES.md`。

## 4. 範圍與發行門檻

P0 是本地與共用基礎；P0-LIVE 是整合 beta 必須另測的真實服務。P1 是操作能力，P2 才做排程／額外入口／UI。

- `0.1.0aN`：完整本地匯入、關聯、packet、草稿和匯出；未完成 LIVE 的功能如實標 experimental 或 blocked。
- `0.1.0bN`：上述基礎加真實 Muse 對話及追問、獲准影音來源、至少一種獲准 Facebook 貼文／留言目標，並完成故障恢復驗收。
- `0.2.x`：逐平台驗收的發文／回覆、素材處理和成效。

沒有可用的合法測試環境可以繼續 alpha，但不能用 fixture 宣告完整 beta。命名與版本是計畫，尚未建立或發布任何套件。

## 5. 里程碑

| 階段 | 交付 | 出口條件 |
|---|---|---|
| M0 | 來源授權、SDK／host 相容性、Muse 最小 spike、依賴鎖定。 | 外部能力和未解條件有測試計画，不以路由名作證據。 |
| M1 | package、models、store、jobs、init／doctor。 | 一個權威資料庫、單 worker、可恢復狀態。 |
| M2 | 匯入、素材、字幕、搜尋、packet、草稿、匯出。 | 完整離線縱向流程，不只有 help。 |
| M3 | Muse client、session／exchange／raw messages、對帳。 | 真實有界往返與追問；逾時不重送。 |
| M4 | 影音來源、一種 Facebook 頁型與留言。 | 人工核對內容、父層和缺漏；部分結果不冒稱全部。 |
| M5 | MCP／Skill 整合、故障、打包、notice。 | P0 及 P0-LIVE 驗收與能力表相符。 |
| M6 | 素材處理、留言清單、單平台發布與成效。 | 精確批准、平台回讀及不明副作用處置。 |

M0 Muse spike 可與 M1 交錯，避免最後才發現服務限制；不把 spike 當正式 runtime。工程任務與依賴在 `04_BACKLOG.md`。本計畫不承諾日曆工期。

## 6. Public repo 與私人資料

建立乾淨的新 public repo，不直接公開既有私人歷史。真實憑證、browser profile、下載、私人留言、Muse 對話、bot 路由、機器與帳號設定留在 repo 外。

README 清楚寫非官方，不宣稱官方 API、TOS 合規或免封號。程式名字、低流量、private repo 都不能代替平台許可。資料收集、向 Muse 分享及對外發布需要分開的使用範圍。

## 7. 工程流程

一個核心 owner 維護 models/store/jobs/public contract，避免多個 Agent 同時改同一欄位。其餘可依實際功能分派，但不是建立永久的多個服務。

每個 PR 附 FR／B／AT、重現命令、實際結果、migration 和來源 notice。CI 聚焦靜態／契約、unit／fixture E2E、wheel smoke、秘密及授權檢查；真實帳號測試手動獲准後執行。用 CODEOWNERS＋維護者 review，不複製上游作者的龐大治理規則。

## 8. 風險與停止條件

Muse 介面漂移先標不相容，不改密碼學或關閉驗證。未知送達查原對話，找不到也不代表沒送達。存取拒絕／驗證／平台限制暫停，不換帳號、代理或 Muse 繞過。授權不明的程式不直接進公開包。

產品價值不能完全依賴 Facebook 當天可否收集：無遠端 backend 時，本地匯入、素材、搜尋和草稿仍有完整用途。這不是降低整合 beta 驗收，而是保留可用的離線 alpha。

## 9. 第一條驗收故事

使用獲准活動影片／字幕和指定貼文留言，在同一 project 建立來源及時間碼關聯。選取 packet，主 Agent 向 Muse 提問並追問一次，再保存附來源回顧草稿與匯出 manifest。此流程不發布。

離線測試可用假 Muse；整合 beta 必須另附真實往返與來源證據，不能混用。
