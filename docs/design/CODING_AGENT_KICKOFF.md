# 開工訊息

請依這份文件包開發MetaMuse CLI。先讀AGENTS.md、01_PLAN.md、02_SPEC.md、03_SDS.md，開始B01–B04：來源與相容性、單一package與契約、SQLite與素材、持久化任務及恢復。

接著完成「匯入資料／字幕→來源關聯→packet→Muse討論→host寫稿→預覽／匯出」。一個package、共用core，不把舊repo分別啟動。不接管Muse harness，不放作者策略。

未知平台能力做有界實驗。只有schema／mock／help通過不能稱LIVE成功；沒測試帳號就列blocked。不要自動建立或公開遠端repo、發布內容或操作使用者帳號。

請回報各FR/B/AT對應的實作、實際測試與剩餘限制，不以寫完文件作為程式完成。
