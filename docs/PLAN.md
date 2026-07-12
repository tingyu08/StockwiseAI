# AI 股票分析與模擬交易平台 — 開發計畫

> 專案代號：`stock-ai-advisor`
> 目標市場：台股＋美股雙市場（前端 Radio Button 切換，含 ETF）
> AI 方案：全免費雲端（Gemini 3.1 Flash Lite ＋ Gemini 3.5 Flash ＋ Gemma 4 31B ＋ Antigravity，不用本地模型）
> 核心價值：AI 驅動的個股分析、策略建議、走勢預測、模擬買賣、多股報酬率與折溢價比較

**文件結構**：本文件（產品計畫）｜[docs/SA.md](docs/SA.md)（系統分析：使用案例、功能/非功能需求）｜[docs/SD.md](docs/SD.md)（系統設計：技術決策 ADR、前後端目錄結構、API 規格、DB Schema、部署設計）

## 2026-07-12 穩定性里程碑

- [x] AI quota reservation、洛杉磯 RPD reset、timeout accounting 與 provider retry。
- [x] 可恢復 DB job queue、工作中心、manual retry 與依序等待的外部排程。
- [x] AI 語意驗證、prompt-injection 邊界、input-hash cache invalidation。
- [x] 市場時區、rolling sync、模擬現金預留、alert outbox 與 retention。
- [x] production fail-closed、健康檢查、依賴鎖定、CI/audit 與 non-root images。

逐項證據與仍需部署環境確認的邊界見 [AUDIT_REMEDIATION.md](AUDIT_REMEDIATION.md)。

---

## 1. 產品功能總覽

### 1.1 核心功能模組

| 模組 | 功能說明 | 優先級 |
|------|---------|--------|
| A. 個股走勢呈現 | K 線圖 / 折線圖、技術指標疊圖（MA、KD、MACD、RSI、布林通道）、成交量 | P0 |
| A2. 雙市場切換 | 全站頂部 Radio Button 切換「台股 / 美股」，切換後所有頁面（走勢、比較、折溢價、模擬交易）都套用該市場的股票池與交易規則 | P0 |
| B. AI 分析與策略建議 | 串接 Gemini/Gemma，綜合技術面＋籌碼面＋新聞面產生分析報告、買賣建議與信心分數 | P0 |
| C. 走勢預測 | 兩層並行：(1) 統計/量化模型預測區間（如 Prophet、簡單回歸）；(2) AI 情境推演（樂觀/中性/悲觀三情境） | P1 |
| D. 報酬率比較 | 多股票報酬率列表（日/週/月/YTD/年化）＋正規化折線圖疊加比較 | P0 |
| E. 折溢價分析 | ETF 市價 vs 淨值（NAV）折溢價率列表、歷史折溢價走勢圖、異常折溢價警示 | P1 |
| F. AI 模擬買賣 | 虛擬帳戶、AI 依策略自動下單（模擬）、持倉損益追蹤、交易日誌與 AI 決策理由 | P1 |
| G. 策略回測 | 用歷史資料驗證 AI/規則策略績效（勝率、最大回撤、Sharpe） | P2 |
| H. 自選股與警示 | 自選清單、價格/指標/折溢價觸發通知 | P2 |

### 1.2 明確不做（v1 範圍外）
- 真實下單（不串券商 API）
- 高頻/分鐘級即時報價（日線＋盤中延遲報價即可）
- 多使用者/會員系統（先做單機或單人使用）

---

## 2. 系統架構

```
┌─────────────────────────────────────────────────┐
│                Frontend (Next.js)                │
│  走勢圖表 │ 比較儀表板 │ AI 報告 │ 模擬交易面板   │
└──────────────────────┬──────────────────────────┘
                       │ REST API（統一回應格式）
┌──────────────────────┴──────────────────────────┐
│              Backend (Python FastAPI)            │
│ ┌───────────┐ ┌───────────┐ ┌────────────────┐  │
│ │ 行情服務   │ │ AI 分析服務│ │ 模擬交易引擎    │  │
│ │ (quotes)  │ │ (Claude)  │ │ (paper trading)│  │
│ └───────────┘ └───────────┘ └────────────────┘  │
│ ┌───────────┐ ┌───────────┐ ┌────────────────┐  │
│ │ 指標計算   │ │ 預測模型   │ │ 回測引擎        │  │
│ └───────────┘ └───────────┘ └────────────────┘  │
└──────────────────────┬──────────────────────────┘
                       │
        ┌──────────────┼──────────────────┐
        │              │                  │
┌───────┴──────┐ ┌─────┴─────┐  ┌────────┴────────┐
│ SQLite/      │ │ 外部資料源 │  │ AI Provider 層  │
│ PostgreSQL   │ │ 台股:FinMind│ │ Gemini 免費額度  │
│ (價格快取、   │ │  /TWSE API │ │ (主) / Gemma 4  │
│  交易紀錄)    │ │ 美股:yfinance│ (備援，全雲端)   │
└──────────────┘ └───────────┘  └─────────────────┘
```

### 2.1 技術選型（建議）

| 層級 | 技術 | 理由 |
|------|------|------|
| 前端 | Next.js 15 + TypeScript + Tailwind + shadcn/ui | 生態成熟、開發快 |
| 圖表 | **Lightweight Charts**（K 線，TradingView 開源）＋ Recharts（比較折線/儀表板） | 專業金融圖表＋通用圖表各取所長 |
| 後端 | Python FastAPI | 量化生態（pandas、TA-Lib、backtesting.py）最完整 |
| 資料庫 | SQLite（本機）／ PostgreSQL Neon 免費層（雲端），SQLAlchemy 統一兩者 | 單人使用 SQLite 足夠；部署細節見 [SD.md §6](docs/SD.md) |
| 部署 | 方案 A：本機 Docker Compose（預設）；方案 B：Vercel＋Render Free＋Neon＋GitHub Actions cron | 兩案皆 $0，見 [SD.md §6](docs/SD.md) |
| AI | **Gemini API 免費額度**（`gemini-3.1-flash-lite` 批次主力、`gemini-3.5-flash` 深度分析、`gemma-4-31b-it` 大額度備援），全雲端同一把 key；經 AI Provider 抽象層可隨時換供應商 | 免費、支援結構化輸出 |
| 台股行情 | FinMind（免費有 API）＋ TWSE/TPEX OpenAPI（折溢價、官方資料） | 免費、涵蓋日線與基本面 |
| 美股行情 | yfinance（日線、ETF NAV）＋ FinMind USStockPrice（備援） | 免費、雲端 IP 限流時可降級 |
| 排程 | APScheduler（每日收盤後更新資料、觸發 AI 模擬交易） | 輕量夠用 |

### 2.2 關鍵設計原則
- **Market Provider 抽象**：`MarketDataProvider` 介面定義 `get_daily_prices() / get_etf_nav() / get_institutional_flows() / search_stocks()`，台股（FinMind/TWSE）與美股（yfinance）各自實作。所有市場差異（交易時間、幣別、漲跌幅限制、法人資料有無）封裝在 Provider 內，上層業務邏輯完全不分市場。
- **AI Provider 抽象**：`AIProvider` 介面定義 `analyze(context) -> StructuredReport`，實作 Gemini（3.1 Flash Lite / 3.5 Flash）與 Gemma 4 兩個 Provider（同一把 Gemini API key），依額度自動降級；日後要換 Claude/OpenAI 只需新增實作。
- **Repository Pattern**：所有資料存取（價格、交易、AI 報告）走抽象介面，儲存層可替換。
- **統一 API 回應格式**：`{ success, data, error, meta }`。
- **不可變資料流**：模擬交易帳戶狀態每次變更產生新紀錄（事件溯源式交易日誌），方便稽核 AI 決策。
- **AI 輸出結構化**：所有 Claude 呼叫使用 tool use / JSON schema 強制結構化輸出（建議動作、信心分數、理由、風險），不解析自由文字。
- **快取優先**：外部 API 有頻率限制，所有行情落地到 DB，前端一律讀本地快取。

---

## 3. 資料模型（核心表）

```
stocks           股票主檔（代號、名稱、market: 'TW'|'US'、currency、類型：個股/ETF）
daily_prices     日線 OHLCV（stock_id, date, open, high, low, close, volume）
etf_nav          ETF 淨值與折溢價（stock_id, date, nav, close, premium_pct）
indicators       技術指標快取（stock_id, date, ma5/ma20/rsi/kd/macd...）
ai_reports       AI 分析報告（stock_id, created_at, model, action, confidence,
                  reasoning, scenarios_json, prompt_version）
predictions      走勢預測（stock_id, horizon, method, predicted_json, created_at）
sim_accounts     模擬帳戶（market、currency、初始資金、現金餘額）— 台股/美股各一
ai_usage_log     AI 用量紀錄（provider, model, tokens, created_at）
sim_orders       模擬委託單（account_id, stock_id, side, qty, price, status,
                  decided_by: 'ai'|'user', ai_report_id, created_at）
sim_positions    持倉快照（由 orders 推導，事件溯源）
watchlists       自選股清單
```

---

## 4. AI 整合設計（重點）— 免費方案

### 4.0 免費 AI 供應商策略

| 方案 | 模型 | 免費額度 | 定位 |
|------|------|---------|------|
| **Gemini 3.1 Flash Lite**（例行批次主力） | `gemini-3.1-flash-lite` | 本帳號實測：**15 RPM / 250K TPM / 500 RPD** | 額度最寬裕，支援 structured output，例行技術面批次分析品質足夠 |
| **Gemini 3.5 Flash**（重要決策） | `gemini-3.5-flash` | 本帳號實測：**5 RPM / 250K TPM / 20 RPD** | 優先用於單檔深度分析、每日簡報總結與模擬交易分析；額度盡或上游失敗時，後兩者自動降級 |
| **Gemma 4 31B**（大額度備援） | `gemma-4-31b-it`（Gemini API 同一把 key） | 本帳號實測：**1,500 RPD**，全系統額度最大 | 開放權重模型，支援 function calling 與 JSON 輸出、256K context；flash-lite 限流時無縫接手，額度大到可承接全部批次量 |
| **Antigravity Agent**（研究型任務） | `antigravity-preview-05-2026`（Interactions API，底層 Gemini 3.5 Flash） | 免費層 60 RPM / 100K TPM / 100 RPD | 自帶沙箱＋Google 搜尋＋URL 抓取＋程式碼執行的託管 agent，適合「個股新聞/事件研究」這種需要自己上網查資料的任務；**不支援 structured output、不支援 temperature 等參數、preview 狀態隨時可能變動**，故不當主分析管線 |
| **OpenRouter 免費模型**（最後備援，選配） | `gemma-4-31b-it:free` 等 | OpenRouter 獨立免費額度 | Google 整把 key 被限流時的異地備援，同模型品質一致 |

**Antigravity 的定位（分工）**：
- 主分析管線（技術面/籌碼面 → 結構化報告）維持用 Gemini（flash-lite/3.5-flash）的 `response_schema`——因為模擬交易引擎必須吃到可靠的 JSON，Antigravity 明確不支援 structured output，只能 prompt 要求 JSON 再解析，可靠度不足以驅動自動下單。
- Antigravity 負責 P2 的「新聞面研究」模組：每日對自選股跑一次「搜尋近期新聞與重大事件並摘要」，它能自己上網省掉我們串新聞 API；輸出以自由文字存入 `ai_reports`，再由 Gemini 主管線把摘要納入分析輸入。100 RPD 對這種每日批次綽綽有餘。
- Preview 期間沙箱運算不計費，但 agent 每次任務 token 消耗遠大於單次 generateContent，需納入用量記錄；GA 後若改計價策略，Provider 抽象層可直接停用。

**配額策略（依儀表板實測額度設計）**：
1. **批次分析**：一次請求分析多檔股票（structured output 回傳陣列，每批 8~10 檔）。30 檔託管清單 = 台股 2 請求＋美股 2 請求，**每日例行批次只吃 4 個請求**（TPM 250K 遠夠裝下多檔的輸入資料）
2. **模型分流（四層）**：
   - 例行每日批次 → `gemini-3.1-flash-lite`（500 RPD，主力）
   - 單檔深度分析（使用者觸發）→ `gemini-3.5-flash`（品質不可替代，不降級）
   - 每日簡報總結、模擬交易分析 → `gemini-3.5-flash` 優先，失敗時依序降級至 Flash Lite、Gemma
   - flash-lite 限流或量大時 → `gemma-4-31b-it`（1,500 RPD，同一把 key 但額度獨立計算）
   - Google 整把 key 被限流（罕見）→ OpenRouter `gemma-4-31b-it:free`（選配，獨立額度）
3. **請求節流器**：全域 rate limiter 按模型別對齊儀表板實際額度，超出自動降級到下一層
4. **分析快取**：同一檔股票同一交易日的分析只跑一次
5. **用量記錄**：每次呼叫記錄 provider/model/token 數，儀表板可見各模型剩餘額度
6. **額度設定化**：額度數字不寫死，放設定檔（Google 改額度頻繁且各帳號不同），對照 AI Studio 儀表板填入

預估每日用量 vs 額度：
| 任務 | 模型 | 請求數/日 | 額度 | 餘裕 |
|------|------|-----------|------|------|
| 例行批次分析（30 檔，批次化） | 3.1 Flash Lite | ~4 | 500 RPD | 125 倍 |
| 模擬交易批次＋每日簡報總結 | 3.5 Flash（可降級） | ~5 | 20 RPD | 約 4 倍 |
| 單檔深度分析（個股頁觸發，含快取） | 3.5 Flash | ~5–10 | 20 RPD | 2~4 倍 |
| 溢出/備援 | Gemma 4 31B | 0（平時） | 1,500 RPD | 全系統最大口袋 |
| 新聞/事件研究 | Antigravity | ~10–30 | 100 RPD（獨立額度） | 3~10 倍 |


### 4.1 AI 分析管線
```
輸入組裝 → Claude 分析 → 結構化輸出 → 落地 DB → 前端呈現
```
1. **輸入組裝**（後端負責，不讓 AI 自己抓資料）：
   - 近 120 日 OHLCV 摘要＋技術指標現值與趨勢
   - 台股：三大法人買賣超（FinMind）；美股：改用成交量趨勢與 52 週高低點位置（無法人資料）
   - ETF 加上折溢價現況
   - （P2）近期新聞與事件摘要——由 Antigravity Agent 每日自主搜尋產出（見 4.0），免串新聞 API
2. **Prompt 設計**：
   - System prompt 定義角色（量化分析師）、輸出 JSON schema、免責立場
   - Prompt 版本化（`prompt_version` 存 DB），方便迭代比較品質
   - Gemini/Gemma 都用原生 `response_schema` / JSON mode 強制 JSON，落地前一律過 Pydantic 驗證，驗證失敗自動重試一次
3. **結構化輸出 schema**：
   ```json
   {
     "action": "buy | sell | hold",
     "confidence": 0.0~1.0,
     "target_price_range": [low, high],
     "stop_loss": number,
     "reasoning": "...",
     "scenarios": { "bull": {...}, "base": {...}, "bear": {...} },
     "risks": ["..."]
   }
   ```

### 4.2 AI 模擬交易引擎
- 每日收盤資料更新後，排程對「AI 託管清單」內每檔股票跑分析
- 決策規則：`confidence >= 門檻` 且符合資金/持倉限制（單一持股上限 20%、保留現金下限）才下單
- 每筆單記錄 `ai_report_id`，前端可點開看「AI 當時為什麼買/賣」
- 風控硬規則寫在程式（停損、部位上限），**不交給 AI 判斷**

### 4.3 走勢預測（雙軌）
- **量化軌**：以簡單可解釋的方法起步（移動平均延伸、線性回歸通道、可選 Prophet），輸出未來 5/20 日預測區間（含信賴帶），畫在 K 線圖右側
- **AI 軌**：Claude 產出三情境（樂觀/中性/悲觀）的目標價與觸發條件，以文字＋區間帶呈現
- UI 上明確標示「預測僅供參考，非投資建議」

---

## 5. 前端頁面規劃

**全域市場切換**：頂部導覽列放 Radio Button「🇹🇼 台股 / 🇺🇸 美股」，狀態存於全域 store（Zustand）＋ URL query（`?market=tw|us`，可分享連結）。切換後：
- 所有 API 請求帶 `market` 參數，股票池、搜尋、比較清單、模擬帳戶皆依市場隔離
- 幣別顯示切換（TWD / USD）、漲跌顏色慣例切換（台股紅漲綠跌 / 美股綠漲紅跌，可於設定中固定其一）
- 各市場各自獨立的自選清單與模擬帳戶

| 頁面 | 內容 |
|------|------|
| `/` 儀表板 | 自選股摘要卡、大盤指數（加權指數 / S&P 500）、AI 今日重點、模擬帳戶損益 |
| `/stock/[id]` 個股頁 | K 線圖＋指標疊圖＋預測區間帶、AI 分析報告卡（動作/信心/理由/情境）、折溢價圖（ETF） |
| `/compare` 比較頁 | 多選股票 → 報酬率表格（可排序：日/週/月/YTD/年化、波動率、折溢價）＋ 正規化報酬折線圖（基準日=100） |
| `/simulation` 模擬交易 | 帳戶總覽（權益曲線）、持倉表、交易日誌（含 AI 決策理由展開）、AI 託管設定 |
| `/backtest`（P2） | 策略選擇、回測參數、績效報告（權益曲線、回撤、勝率） |

---

## 6. 開發階段（Milestones）

### Phase 0：專案骨架（~2 天）
- [x] Monorepo 結構：`frontend/`（Next.js）＋ `backend/`（FastAPI）
- [x] SQLite/PostgreSQL schema + SQLAlchemy + Alembic migration
- [x] 統一 API 回應格式與錯誤處理；排程入口使用獨立 Job Token
- [x] 環境變數管理（`GEMINI_API_KEY`、`FINMIND_TOKEN`、`JOB_TOKEN` 等）
- [x] `MarketDataProvider`、`MarketDataGateway` 與 `AIProvider` 抽象

### Phase 1：行情資料與走勢呈現（~1.5 週）→ 功能 A、A2
- [x] 台股 Provider：FinMind/TWSE（日線、法人、ETF 淨值）
- [x] 美股 Provider：yfinance（日線、ETF NAV）＋ FinMind 備援
- [x] 每日排程更新（兩市場依收盤時間）
- [x] 技術指標計算服務（MA/KD/MACD/RSI/布林）
- [x] 前端全域市場切換（Zustand + URL query）
- [x] 個股頁 K 線、成交量、布林通道與 RSI/KD/MACD 副圖
- [x] 股票搜尋、自選清單、群組與排序（依市場隔離）

### Phase 2：AI 分析與報酬率比較（~1 週）→ 功能 B、D
- [x] Gemini Provider（flash-lite / 3.5-flash，structured output）＋ Gemma 4 fallback
- [x] RPD/RPM/TPM 節流、分析快取、用量記錄
- [x] 分析管線（輸入組裝、結構化報告落地）
- [x] 個股頁 AI 報告卡 UI
- [x] 同市場報酬率列表＋正規化折線圖

### Phase 3：折溢價與走勢預測（~1 週）→ 功能 C、E
- [x] ETF 折溢價每日計算、歷史走勢圖與列表排序
- [x] 交易所日曆感知的量化回歸通道預測帶
- [x] AI 三情境推演整合進報告

### Phase 4：AI 模擬買賣（~1.5 週）→ 功能 F
- [x] 模擬帳戶與隔日開盤撮合（原子 claim、防重單、防賣超）
- [x] AI 自動決策排程與風控硬規則
- [x] 模擬交易面板（權益曲線、持倉、含理由的交易日誌）

### Phase 5：回測與警示（P2，視需求）→ 功能 G、H
- [x] 內建 MA/RSI/布林回測引擎，含滑價、Sharpe、最大回撤與績效報告
- [x] 價格/折溢價警示事件與選配 webhook 通知

### Phase 6：新聞面研究（P2）→ §4.0 Antigravity 分工（2026-07-09 完成）
- [x] `AntigravityProvider`（Interactions API：background 建立＋輪詢，quota 檢查與用量記錄）
- [x] `news_service`：每檔每日曆日一次（快取入 `ai_reports` kind='news'），保鮮期 4 天
- [x] `build_context` 注入 `news_summary` → 例行批次/深度分析自動吃到新聞面
- [x] 排程：台股 13:40、美股 04:40（批次分析前；額度盡提前收工）
- [x] API：`GET/POST /stocks/{symbol}/news[:run]`
- 實測備註：background interaction 的 GET 回應**沒有頂層 `output_text`**，最終回覆在
  `steps[]` 最後一個 `type='model_output'` 的 `content[].text`；用量欄位為
  `usage.total_input_tokens`/`total_output_tokens`（字串型）。

**每個 Phase 完成後**：跑 code review + 補測試（目標 80% 覆蓋率：指標計算、撮合引擎、折溢價計算為重點單元測試對象）。

---

## 7. 風險與對策

| 風險 | 對策 |
|------|------|
| 免費資料源限流/斷供 | 全部落地快取；資料源抽象成 Provider 介面，可替換（台股 FinMind ↔ TWSE；美股 yfinance ↔ Stooq）；yfinance 為非官方爬蟲，介面偶爾失效，需鎖版本＋監控 |
| Gemini 免費額度不足/限流 | 節流器對齊各模型實際額度；分析快取（同一天同一檔不重跑）；自動降級鏈 flash-lite → Gemma 4（1,500 RPD）→ OpenRouter；用量儀表板 |
| Google 免費層政策再變動（2025/12 已大砍過一次） | 額度數字放設定檔不寫死；報告標示產生模型；Provider 抽象層可快速切換供應商 |
| AI 建議品質不穩 | Prompt 版本化＋模擬交易績效即品質回饋迴路；回測驗證 |
| 法規/責任疑慮 | 全站免責聲明；只做模擬交易，不串真實下單 |
| 預測誤導使用者 | 一律以「區間＋情境」呈現，不給單點預測；標示方法與信賴度 |

## 8. 安全檢查清單
- API key（`GEMINI_API_KEY` 等）只存環境變數，啟動時驗證存在
- 後端對所有輸入（股票代號、日期、數量）做 schema 驗證（Pydantic）
- 外部 API 回應視為不可信資料，驗證後才入庫
- SQL 一律走 ORM/參數化查詢
