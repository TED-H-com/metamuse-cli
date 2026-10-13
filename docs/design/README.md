# MetaMuse CLI — 開發交接文件 v1.1

2026-10-13｜重新提供的文件包。原ZIP在目前session不可取得，本包從保存設計及已定案內容重建。**不是應用程式發行，不表示已部署、登入或完成產品測試。**

| 文件 | 用途 |
|---|---|
| [Plan](01_PLAN.md) | 定位、範圍、上游採納、里程碑、發行門檻。 |
| [Spec](02_SPEC.md) | 32項FR、CLI/MCP、資料與權限、狀態和預設。 |
| [SDS](03_SDS.md) | 單一應用、資料庫、jobs、Muse、packet及恢復設計。 |
| [Backlog](04_BACKLOG.md) | 22個工程任務與依賴。 |
| [Acceptance](05_ACCEPTANCE.md) | 54個待執行案例，fixture與LIVE分開。 |
| [Sources](06_SOURCES.md) | 來源與本次恢復限制。 |
| [Coding Agent](AGENTS.md) | 防止架構與範圍走偏。 |
| [開工訊息](CODING_AGENT_KICKOFF.md) | 可交給coding agent的第一輪工作。 |
| [Skill草稿](skills/metamuse/SKILL.md) | 使用端的中性指引。 |
| [功能明細](appendices/FEATURE_INVENTORY.md) | 保存先前功能目錄，正式需求以FR為準。 |

正式名字MetaMuse CLI，repo/distribution暫定metamuse-cli，command/package/Skill metamuse。名稱未代表registry或商標已保留。

本包不包含已移植第三方runtime。實作時以型別模型產生schemas；文件與contracts同PR更新。CLI/MCP共用一套app，沒有第二個隱藏AI。public程式、private runtime在repo外。

先B01–B04，再離線縱切片；Muse小範圍spike早做，整合beta仍必須有真正驗收。
