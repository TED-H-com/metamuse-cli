# MetaMuse CLI — 工程 Backlog

全部初始not_started；依賴不是工期估算。先B01–B04，其他Agent可準備fixtures，不同時亂改models/store/jobs。

| ID | 階段 | 工作 | 依賴 | 交付 |
|---|---|---|---|---|
| B01 | M0 | 來源／授權／相容性 | — | 來源清冊、Muse最小spike、SDK/host與版本選擇 |
| B02 | M1 | 單一package與contracts | B01 | entrypoint、models、init／doctor、schema |
| B03 | M1 | Store與migrations | B02 | SQLite、revision、objects、backup restore |
| B04 | M1 | Jobs與executor | B03 | request_key、OS lock、queue、epoch、恢復／取消 |
| B05 | M2 | 匯入與身分 | B03,B04 | 文字／CSV／JSONL、來源與留言父子 |
| B06 | M2 | 本地媒體／字幕 | B05 | asset hash、metadata、VTT/SRT及關聯 |
| B07 | M2 | 搜尋／evidence／packet | B05,B06 | 分頁、精確revision、分享前預覽 |
| B08 | M2 | Safety與permits | B02,B03 | scope、expiry、secret refs、URL/path/egress |
| B09 | M3 | 最小Muse transport | B01,B04,B08 | 標準crypto、serial receive、限定路由 |
| B10 | M3 | Discussion lifecycle | B07,B09 | session、exchange、raw messages、對帳／cursor |
| B11 | M2 | 草稿／匯出 | B07 | host提供文案、revision、preview與manifest |
| B12 | M4 | 遠端影音 | B06,B08 | 受控yt-dlp、字幕、必要下載及LIVE |
| B13 | M4 | 有界Facebook讀取 | B05,B08 | 一種獲准頁型的post/comments/replies及LIVE |
| B14 | M5 | CLI／MCP完整入口 | B04,B07,B10,B11 | 同app、schema、stdio、resource及host實測 |
| B15 | M5 | 中性Skill | B14 | 使用路由、降階、限制、無作者策略 |
| B16 | M5 | 故障／性能 | B10,B12,B13,B14 | crash、不明副作用、privacy、backup、實測 |
| B17 | M5 | 整合beta／打包 | B15,B16 | LIVE旅程、能力表、wheel／source／notice掃描 |
| B18 | M6 | 素材與來源變更 | B17 | 可選ASR、裁切、stale關聯 |
| B19 | M6 | 留言與發布契約 | B17 | draft_only、exact approval、fixture對帳 |
| B20 | M6 | 發布LIVE | B19 | 一個獲准平台／帳號／目的地receipt |
| B21 | M6 | 中性成效 | B17 | 定義／原值／時間／null／修正 |
| B22 | P2 | 排程／入口／UI | B17；發布排程另需B20 | 按明確需求擴充，沿用core |

每個PR回報FR／驗收ID、實際命令、結果與證據類型、migration與notice。不得以help／mock／文件檢查宣稱LIVE成功。
