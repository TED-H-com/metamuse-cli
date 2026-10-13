# 來源與恢復說明

文件版1.1｜2026-10-13。本次重新生成文件下載包，使用本對話既定MetaMuse CLI決策、先前保存的單一工具v3及功能v2，沒有執行新的平台查核或真實帳號操作。

原先的ZIP在目前session不可取得；這是重新提供的交接版，不主張與先前ZIP逐位元相同。文件規格已保存的設計不等於產品程式已完成。

## 先前討論採用的一手來源（實作前重驗）

- Muse client與protocol：[repository](https://github.com/nikships/muse-cli)、[protocol](https://github.com/nikships/muse-cli/blob/main/docs/PROTOCOL.md)。最小session/send/history的使用基線，不是官方穩定API保證。
- Facebook貼文概念：[tlyu0419/facebook_crawler](https://github.com/tlyu0419/facebook_crawler)。先前對話已核對MIT；移植時鎖commit／license／原路徑。
- 留言需求：[boo283/Facebook_comment_crawler](https://github.com/boo283/Facebook_comment_crawler)。先前未見明確LICENSE，不推定可任意移植；未解授權不复制／機械翻寫。
- 中性內容功能：[Hao0321/claude-skill-social-post](https://github.com/Hao0321/claude-skill-social-post)。先前核對MIT；不帶作者策略或私人治理內容。
- 自有影音wrapper：使用者先前提供的程式與工作經驗；不附私人部署或帳號設定。
- 依賴：[yt-dlp](https://github.com/yt-dlp/yt-dlp)、[Playwright auth](https://playwright.dev/python/docs/auth)、[SQLite WAL](https://sqlite.org/wal.html)、[MCP工具規格](https://modelcontextprotocol.io/specification/latest/server/tools)、[Agent Skills](https://agentskills.io/specification)。B01選定實測版本，不把歷史查核當今天所有環境都相容。
- 授權說明：[GitHub licensing](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。公開可讀、軟體授權與平台資料使用許可是不同問題。
- 私密資料：[GitHub removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)。已曝光憑證先輪換，gitignore不是刪除歷史。

本包是原創文件／契約，不包含移植的第三方runtime。實際採用每段程式記repo、commit、原path/blob、license、採用範圍、local path、修改與review；保留所需完整license文字。

沒有完整核實當前帳號適用的Facebook／Muse條款，本包不保證TOS合規、帳號安全或名稱商標可用。不將改名、低調或資料來源偽裝視為許可。
