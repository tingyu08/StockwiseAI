# StockwiseAI — 給 Claude 的專案須知

AI 股票分析與模擬交易。後端 FastAPI（Zeabur）＋前端 Next.js（Vercel）＋Neon PostgreSQL。
設計文件在 `docs/`（SA/SD/PLAN），**現況以程式碼與本檔為準**。

## 接手時先做這件事

```bash
git log --oneline -15
```

commit message 記的是「為什麼這樣改、踩到什麼坑、反轉驗證了什麼」，
而不只是改了哪些檔案。要接續前一輪的工作、或想知道某個奇怪的寫法
為何存在，看它比問使用者快，也比翻 docs/ 準（docs/ 會過期）。

需要更細的來龍去脈時，可用 session 搜尋工具找舊對話，但多數情況
git log ＋ 程式碼註解就夠了。

## 開發指令

```bash
# 後端（一定要用 venv 的 python，系統 Python 沒裝依賴）
cd backend && ./.venv/Scripts/python.exe -m pytest -q
cd backend && ./.venv/Scripts/python.exe -m ruff check .
cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head

# 前端
cd frontend && npx vitest run          # 測試
cd frontend && npx vitest run --coverage
cd frontend && npx tsc --noEmit        # 應為 0 錯誤
cd frontend && npx eslint .
```

- 中文輸出在 Windows console 會亂碼 → 需要看中文時加 `PYTHONIOENCODING=utf-8`
- 起前端一律用 preview 工具（`.claude/launch.json` 的 `frontend`），不要用 Bash 跑 dev server

## 資料來源（皆為官方 API，無爬蟲）

| 用途 | 來源 | 備援 |
|---|---|---|
| 台股日線／法人／ETF 淨值 | FinMind | TWSE/TPEX |
| 美股日線／指數／名稱分類 | FinMind | 無 |
| 美股盤中報價（出場哨兵） | Finnhub | 無 |
| 台股盤中報價 | 證交所 MIS | 無 |
| 台指期夜盤 | 期交所 MIS | 無 |
| 新聞標題 | FinMind／Finnhub | Google News RSS |

**yfinance 已完整移除**（Yahoo 封鎖機房 IP，在雲端從未成功過）。
`tests/test_us_market_sources.py` 有守門測試擋它回來。

## AI 模型

- 例行批次／新聞摘要：`gemini-3.5-flash-lite`（500 RPD）
- 交易決策／每日簡報：`gemini-3.8-flash` → `gemini-3.7-flash` → `gemini-3.6-flash`
  → `flash-lite` 四級降級（2026-08-28 換代）。
- **新發表的 flash 會常態性 503**，這是 Google 端容量不足，不是設定問題：
  3.7 從 2026-08-14 用到 08-28 整整兩週，正式環境 0/5 全滅；論壇上連付費
  Tier 2 + Priority tier 也回報 0%，**升級付費救不了**，Google 也沒有公告
  （Gemini Developer API 根本沒有官方狀態頁）。同期 3.6 是 5/5 全中，
  所以備援保留兩級，主力掛掉也不會直接掉到 lite。
- 額度定義在 `app/core/quotas.yaml`，啟動時 `validate_configured_models()` 會檢查
- **503 會計入 RPD**（已向 Google 確認）。premium 每日只有 20 次，所以鏈上還有
  備援的模型遇到 503 只送一次就降級（`router._provider`）；鏈尾沒得降才重試
- **Google 的額度日以太平洋時間午夜為界＝台北 15:00**，因此台股晨間批次與前一晚
  的美股批次算同一天、共用 20 次，台股排在最後最容易被餓死

## 排程（台北時間，`app/scheduler/jobs.py`）

```
台股  06:10 新聞 → 06:40 AI批次 → 06:55 簡報 → 07:10 產生委託
      （09:00 開盤價成交）14:45 淨值 → 18:00 同步 → 18:10 撮合 → 18:20 警示
美股  19:40 新聞 → 20:10 AI批次 → 20:25 簡報 → 20:40 委託
      08:00 同步 → 08:10 撮合 → 08:25 警示
哨兵  台股 9-13:10 每小時、美股 21-23,0-4:40 每小時
```

委託於 07:10 產生（pending），18:10 才以當日開盤價撮合——`filled_at` 記的是
當地開盤時刻換算的 UTC，不是撮合當下。

## 工作慣例

- **TDD**：先寫測試看它失敗，再實作
- **反轉驗證**：改完後把修正拆掉，確認對應測試真的會紅。這抓出過好幾次
  「測試其實沒在守」的漏洞
- 每個 commit 前跑完整檢查；報告結果時據實說明哪些沒驗到
- commit message 說明「為什麼」與踩到的坑，不只說「改了什麼」

## 已知的環境特性

- **Zeabur log 的紅色 ERROR 可能只是 Python 的 WARNING**（severity 看 stderr 判定）。
  判讀看訊息前綴，不要看色塊。免費方案 log 只留約一天，`Search Logs` 是付費功能。
- **依賴指令**（`npm install`、`pip-compile`）會被 guardrail 攔下，需使用者明確授權
- 前端覆蓋率門檻刻意訂在實際值附近（`vitest.config.ts` 有說明），是「防止退步的
  地板」而非品質目標。統計範圍涵蓋全部原始碼，補測試永遠是加分

## 容易誤判的地方

- **AI 回傳的 symbol 不保證乾淨**：實際出現過 `"00407A 主動凱基台灣"`、`"TW/2330"`。
  凡是拿 symbol 當 key 查找，都要先正規化
- **簡報的價格數字由系統直供**（`analysis_service.stock_facts`），不取自 AI 複述的
  字串——AI 隨時可能改寫格式
- 模擬帳戶的可動用資金 ＝ `min(權益×20%, 現金 − 權益×10%)`。現金接近保留線時
  AI 會完全不買進，那是預期行為不是故障
