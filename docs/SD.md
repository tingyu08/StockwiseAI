# 系統設計文件（SD）— stock-ai-advisor

> 版本：1.0 ／ 日期：2026-07-08
> 關聯文件：[SA.md](SA.md)（系統分析）、[PLAN.md](../PLAN.md)（產品計畫）

---

## 1. 技術決策記錄（ADR 摘要）

| # | 決策 | 選擇 | 理由 | 捨棄方案 |
|---|------|------|------|---------|
| ADR-01 | 後端語言/框架 | Python 3.12 + FastAPI | pandas/TA 量化生態、Pydantic 原生驗證、async 支援 | Node.js（量化生態弱）、Django（過重） |
| ADR-02 | 前端框架 | Next.js 15 (App Router) + TypeScript | SSR 首屏快、Vercel 免費部署、生態成熟 | Vite SPA（無 SSR）、Nuxt |
| ADR-03 | 資料庫 | **開發＝SQLite；雲端部署＝PostgreSQL（Neon 免費層）**，以 SQLAlchemy 統一，兩者皆可跑 | 單人低併發 SQLite 足夠；Neon 免費 0.5GB 夠放多年日線；ORM 隔離讓兩者無痛切換 | MySQL（免費雲端選擇少）、MongoDB（關聯查詢多不適合） |
| ADR-04 | ORM/Migration | SQLAlchemy 2.0 + Alembic | 業界標準、SQLite/PG 雙支援 | 裸 SQL（migration 難管理） |
| ADR-05 | 圖表庫 | Lightweight Charts（K 線）＋ Recharts（比較/儀表板） | 前者為 TradingView 開源專業金融圖表；後者宣告式好維護 | ECharts（單套通吃但 K 線體驗較差） |
| ADR-06 | 狀態管理 | Zustand（市場切換等全域）＋ TanStack Query（伺服器資料快取） | 輕量、與 App Router 相容 | Redux（樣板碼多） |
| ADR-07 | 排程 | APScheduler（進程內） | 單體部署最簡單；雲端免費層配 GitHub Actions cron 觸發 API 補位 | Celery+Redis（多一個付費依賴） |
| ADR-08 | 部署 | **已定案**：開發期＝方案 A（本機 Docker Compose）→ 上線＝方案 B（Vercel+Render+Neon）→ 用一陣子後視情況遷方案 C（Zeabur） | 先 $0 驗證價值再決定花錢 | — |
| ADR-09 | 雲端 DB | **已定案：Neon**（開發期本機 SQLite，上 B 時切 `DATABASE_URL`） | 生態最穩；連線池紀律見 §6.2 | Turso（dialect 年輕）、Supabase（功能過剩） |

## 2. 整體架構

```
┌────────────────────────── Frontend (Next.js) ──────────────────────────┐
│  app/                     頁面（App Router）                            │
│  components/charts/       K線、折溢價圖、正規化比較圖、權益曲線           │
│  stores/                  Zustand（market、settings）                   │
│  lib/api.ts               API client（統一錯誤處理、market 參數注入）    │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │ REST /api/v1/*（JSON envelope）
┌────────────────────────────────┴───────────────────────────────────────┐
│                        Backend (FastAPI 單體)                           │
│  api/          routers（薄層：驗證→呼叫 service→包 envelope）            │
│  services/     業務邏輯（分析、比較、模擬交易、預測）                     │
│  providers/    外部依賴抽象（MarketDataProvider、AIProvider）            │
│  repositories/ 資料存取（SQLAlchemy，介面化）                            │
│  scheduler/    APScheduler jobs（資料更新、AI 批次、模擬下單、新聞）       │
│  core/         設定、額度管理、節流器、logging                           │
└────────────────────────────────┬───────────────────────────────────────┘
                                 │
     SQLite（dev）/ PostgreSQL-Neon（cloud） ＋ 外部 API（FinMind/yfinance/Gemini）
```

## 3. 後端詳細設計

### 3.1 目錄結構

```
backend/
├── app/
│   ├── main.py                    # FastAPI app、middleware、router 掛載
│   ├── core/
│   │   ├── config.py              # Pydantic Settings（env 驗證、額度設定載入）
│   │   ├── quotas.yaml            # 各模型額度設定（不寫死在程式）
│   │   ├── rate_limiter.py        # 按模型別的節流器（token bucket）
│   │   └── exceptions.py          # 統一例外 → error envelope
│   ├── api/v1/
│   │   ├── stocks.py              # /stocks 搜尋、明細、價格序列
│   │   ├── analysis.py            # /analysis AI 報告、深度分析觸發
│   │   ├── compare.py             # /compare 報酬率、正規化序列
│   │   ├── premium.py             # /premium 折溢價
│   │   ├── simulation.py          # /simulation 帳戶、持倉、委託、權益曲線
│   │   ├── predictions.py         # /predictions 預測區間
│   │   └── usage.py               # /usage AI 用量
│   ├── providers/
│   │   ├── market/
│   │   │   ├── base.py            # MarketDataProvider 介面
│   │   │   ├── finmind.py         # 台股主源
│   │   │   ├── twse.py            # 台股備援＋折溢價
│   │   │   └── yfinance_us.py     # 美股（含 Stooq fallback）
│   │   └── ai/
│   │       ├── base.py            # AIProvider 介面 + 降級鏈 Router
│   │       ├── gemini.py          # flash-lite / 3.5-flash（response_schema）
│   │       ├── gemma.py           # gemma-4-31b-it（JSON mode）
│   │       ├── antigravity.py     # Interactions API（新聞研究）
│   │       └── schemas.py         # AnalysisReport 等 Pydantic 輸出模型
│   ├── services/
│   │   ├── indicator_service.py   # MA/KD/MACD/RSI/布林（純函式、無 IO）
│   │   ├── analysis_service.py    # 輸入組裝→AI→驗證→落地（含當日快取）
│   │   ├── compare_service.py     # 報酬率/年化/波動率計算
│   │   ├── premium_service.py     # 折溢價計算
│   │   ├── prediction_service.py  # 回歸通道/Prophet 區間
│   │   └── sim/
│   │       ├── engine.py          # 撮合（隔日開盤價成交、手續費/稅）
│   │       ├── risk.py            # 硬風控（部位上限、停損、現金下限）
│   │       └── decision.py        # AI 報告 → 委託單
│   ├── repositories/              # 每張表一個 repo（介面 + SQLAlchemy 實作）
│   ├── models/                    # SQLAlchemy ORM models
│   └── scheduler/
│       └── jobs.py                # 每日 jobs（見 §5 時序）
├── alembic/                       # migrations
├── tests/                         # pytest（單元＋API 整合）
└── pyproject.toml
```

### 3.2 API 規格（v1，統一 envelope `{success, data, error, meta}`）

| Method | Path | 說明 |
|--------|------|------|
| GET | `/api/v1/stocks?market=tw&q=台積` | 搜尋股票 |
| GET | `/api/v1/stocks/{symbol}/prices?range=1y` | 日線＋指標序列 |
| GET | `/api/v1/stocks/{symbol}/analysis` | 當日 AI 報告（無則回 404，前端顯示「尚未分析」） |
| POST | `/api/v1/stocks/{symbol}/analysis:routine` | 觸發例行分析（當日快取） |
| POST | `/api/v1/stocks/{symbol}/analysis:deep` | 觸發深度分析（3.5 Flash，檢查額度後執行） |
| GET/POST | `/api/v1/stocks/{symbol}/news[:run]` | 讀取／觸發 Antigravity 新聞研究 |
| GET | `/api/v1/stocks/{symbol}/predictions` | 預測區間帶 |
| GET | `/api/v1/compare?symbols=A,B,C&market=tw` | 報酬率表＋正規化序列 |
| GET | `/api/v1/premium?market=tw` | ETF 折溢價列表 |
| GET | `/api/v1/premium/{symbol}/history` | 單檔折溢價歷史 |
| GET | `/api/v1/simulation/{market}/account` | 帳戶＋持倉＋權益曲線 |
| GET | `/api/v1/simulation/{market}/orders` | 交易日誌（含 ai_report 連結） |
| POST | `/api/v1/simulation/{market}:decide` / `:fill` | 手動觸發決策／原子撮合 |
| GET/POST/PATCH/DELETE | `/api/v1/groups`、`/api/v1/watchlist` | 自選股群組、排序與 AI 託管 |
| GET/POST | `/api/v1/backtest/strategies`、`/api/v1/backtest` | 策略回測（滑價、Sharpe、回撤） |
| GET/POST/DELETE | `/api/v1/alerts` | 價格／折溢價警示 |
| GET | `/api/v1/usage` | 各模型今日用量 |
| GET | `/api/v1/data-status` | 台／美股行情、NAV、AI 資料新鮮度 |
| POST | `/api/v1/jobs/{name}:run`（需 `X-Job-Token`） | 供 GitHub Actions cron 觸發排程（雲端部署用） |
| GET | `/api/v1/jobs/runs/{id}` | 查詢 queued/running/succeeded/failed 與結果 |
| POST | `/api/v1/jobs/runs/{id}:retry` | 重試失敗工作 |

設定 `API_TOKEN` 後，除 `/health` 與排程觸發端點外，所有 API 皆需
`Authorization: Bearer <API_TOKEN>`。排程觸發仍獨立使用 `X-Job-Token`。

### 3.3 資料庫 Schema（DDL 摘要）

```sql
CREATE TABLE stocks (
  id          INTEGER PRIMARY KEY,
  symbol      TEXT NOT NULL,            -- '2330' / 'AAPL'
  market      TEXT NOT NULL CHECK (market IN ('TW','US')),
  name        TEXT NOT NULL,
  currency    TEXT NOT NULL,            -- 'TWD' / 'USD'
  kind        TEXT NOT NULL CHECK (kind IN ('stock','etf')),
  UNIQUE (market, symbol)
);

CREATE TABLE daily_prices (
  stock_id INTEGER REFERENCES stocks(id),
  date     DATE NOT NULL,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC,
  volume BIGINT,
  PRIMARY KEY (stock_id, date)
);

CREATE TABLE etf_nav (
  stock_id INTEGER REFERENCES stocks(id),
  date DATE NOT NULL,
  nav NUMERIC, close NUMERIC,
  premium_pct NUMERIC,                  -- (close-nav)/nav*100
  PRIMARY KEY (stock_id, date)
);

CREATE TABLE ai_reports (
  id INTEGER PRIMARY KEY,
  stock_id INTEGER REFERENCES stocks(id),
  trade_date DATE NOT NULL,
  provider TEXT, model TEXT, prompt_version TEXT,
  kind TEXT CHECK (kind IN ('routine','deep','news')),
  action TEXT CHECK (action IN ('buy','sell','hold')),
  confidence NUMERIC,
  payload_json TEXT NOT NULL,           -- 完整結構化報告
  created_at TIMESTAMP,
  UNIQUE (stock_id, trade_date, kind)   -- 當日快取的資料庫保證
);

CREATE TABLE sim_accounts (
  id INTEGER PRIMARY KEY,
  market TEXT UNIQUE, currency TEXT,
  initial_cash NUMERIC, cash NUMERIC
);

CREATE TABLE sim_orders (                -- 事件溯源：持倉由 orders 重放推導
  id INTEGER PRIMARY KEY,
  account_id INTEGER REFERENCES sim_accounts(id),
  stock_id INTEGER REFERENCES stocks(id),
  side TEXT CHECK (side IN ('buy','sell')),
  qty NUMERIC, fill_price NUMERIC, fee NUMERIC,
  status TEXT CHECK (status IN ('pending','filled','rejected')),
  decided_by TEXT CHECK (decided_by IN ('ai','user')),
  ai_report_id INTEGER REFERENCES ai_reports(id),
  reject_reason TEXT,
  created_at TIMESTAMP, filled_at TIMESTAMP
);

CREATE TABLE ai_usage_log (
  id INTEGER PRIMARY KEY,
  provider TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER,
  created_at TIMESTAMP
);
-- 另有 indicators（指標快取）、predictions、watchlists，結構同 PLAN.md §3
```

索引：`daily_prices(stock_id, date DESC)`、`ai_reports(trade_date)`、`ai_usage_log(created_at)`。

## 4. 前端詳細設計

```
frontend/
├── app/
│   ├── layout.tsx                 # 頂欄（MarketSwitch radio、導覽、用量指示燈）
│   ├── page.tsx                   # 儀表板
│   ├── stock/[symbol]/page.tsx    # 個股頁
│   ├── compare/page.tsx           # 比較頁
│   ├── premium/page.tsx           # 折溢價頁
│   └── simulation/page.tsx        # 模擬交易頁
├── components/
│   ├── market-switch.tsx          # Radio Button；寫入 store＋URL query
│   ├── charts/
│   │   ├── candlestick.tsx        # Lightweight Charts 封裝（指標疊圖、預測帶）
│   │   ├── compare-line.tsx       # 正規化報酬折線（Recharts）
│   │   ├── premium-line.tsx       # 折溢價歷史
│   │   └── equity-curve.tsx       # 權益曲線
│   ├── analysis/report-card.tsx   # AI 報告卡（action 徽章、信心條、情境 tabs）
│   └── simulation/                # 持倉表、交易日誌（可展開理由）
├── stores/market.ts               # Zustand：market、與 URL 同步
├── lib/
│   ├── api.ts                     # fetch 封裝：market 注入、envelope 解包、錯誤 toast
│   └── format.ts                  # 幣別/漲跌色（TW 紅漲 vs US 綠漲）
└── hooks/                         # TanStack Query hooks（usePrices、useAnalysis…）
```

關鍵互動規則：
- `market` 變更 → TanStack Query key 全部帶 market → 自動重抓，無需手動清快取
- 深度分析按鈕顯示剩餘額度（`/usage`），額度 0 時 disabled 並提示明日恢復
- 所有 AI 內容元件底部固定免責聲明

## 5. 關鍵時序（每日排程）

```
台股日：14:30 資料更新(UC-B1) → 15:00 AI批次(UC-B2, flash-lite×2請求)
        → 15:30 產生委託(UC-B3, pending) → 次日 09:05 以開盤價成交
美股日：台灣時間 05:30 資料更新 → 06:00 AI批次 → 06:30 產生委託
        → 美股次日開盤後成交
每  日：07:00 Antigravity 新聞研究(UC-B4, 自選股逐檔, ≤30 請求)
失敗處理：每步獨立 try/except＋log；資料更新失敗則跳過當日 AI 批次（避免用舊資料決策）
```

## 6. 部署設計

### 6.1 方案 A（v1 預設）：本機 Docker Compose

```yaml
services:
  backend:   # FastAPI + APScheduler，SQLite 掛 volume ./data
  frontend:  # next start（或 next build 後由 backend 靜態服務）
```
- 成本 $0、資料在自己手上、排程直接由 APScheduler 跑
- 缺點：電腦要開著才會跑排程（可設 Windows 工作排程器喚醒）

### 6.2 方案 B（$0 雲端）：Vercel ＋ Render ＋ Neon

| 元件 | 平台 | 免費層備註 |
|------|------|-----------|
| 前端 | Vercel | Next.js 原生支援，免費層足夠 |
| 後端 API | Render Free Web Service | 15 分鐘無流量會休眠 |
| 排程 | GitHub Actions cron | 呼叫 `POST /jobs/{name}:run`（帶 `X-Job-Token`），順便喚醒 Render |
| 資料庫 | Neon PostgreSQL Free | 0.5GB，日線資料數年份足夠 |

- 注意：Render 免費層休眠 → 排程一律由 GitHub Actions 觸發，不依賴進程內 APScheduler
- 切換方式：`DATABASE_URL` 指向 Neon、`SCHEDULER_MODE=external` 即可，程式碼不變

**Neon 免費層限制與對策**（2026 現況：0.5GB 儲存／100 CU-hours 月運算／閒置 5 分鐘強制休眠／5GB egress）：
- **100 CU-hours 是硬上限，用完資料庫停到下個月**——本 app 估計月用 15~30 CU-hours，安全餘裕 3 倍以上，但必須遵守：
  1. SQLAlchemy 連線池設 `pool_pre_ping=True` ＋ `pool_recycle=300`，**不得**使用長駐 keep-alive 連線（連線不斷 = 資料庫永不休眠 = 額度必爆）
  2. 排程 job 結束即釋放連線
  3. `/usage` 儀表板順帶顯示 Neon 當月 CU-hours（打 Neon API 取得），超過 70% 發警示
- 冷啟動 0.5~1 秒：發生在第一個查詢，前端已有 loading 狀態，可接受

### 6.3 方案 C（$5/月）：Zeabur 全家桶

前後端＋資料庫放在同一個 Zeabur project，架構最簡單：

| 元件 | Zeabur 內部署方式 |
|------|------------------|
| 前端 | Next.js service（git push 自動建置） |
| 後端 | FastAPI service（常駐容器，不休眠 → APScheduler 直接跑，**不需要** GitHub Actions cron 和 `SCHEDULER_MODE=external`） |
| 資料庫 | 一鍵 PostgreSQL service，或掛 volume 直接用 SQLite |

- **為什麼不是免費**：Zeabur 免費層（Serverless Plan）閒置會自動休眠（與 Render Free 同病），且共享叢集 2026/4 起不再接受新服務；常駐後端＋排程要穩定跑，實務上需 Developer Plan（US$5/月，用量計費另計但本 app 量級極小）
- **適用時機**：如果願意每月花 $5 換「單一平台、免休眠、免外部 cron、部署最省事」，方案 C 優於 B；堅持 $0 則維持方案 B

### 6.4 雲端資料庫選型：Neon vs Turso

| 面向 | Neon（PostgreSQL） | Turso（libSQL/SQLite 方言） |
|------|-------------------|---------------------------|
| 免費額度 | 0.5GB 儲存 | 3 個 DB、1GB、5 億 row reads/月 |
| 與本地開發一致性 | dev 用 SQLite、prod 用 PG，SQL 方言有差（靠 SQLAlchemy 抹平，仍需兩邊測試） | **dev/prod 同為 SQLite 方言，一致性最好** |
| SQLAlchemy/Alembic 成熟度 | psycopg 生態最成熟，零風險 | `sqlalchemy-libsql` dialect 較年輕，Alembic 相容性需在 Phase 0 先驗證 |
| 計費模型陷阱 | 無（額度制） | row reads 計「掃描列數」，全表掃描也算——本 app 量級小（日線數萬列）遠碰不到上限，但查詢要記得加索引 |
| 結論 | **預設選擇**：走方案 B 時選 Neon（最穩） | 替代選項：重視 dev/prod 一致可換 Turso，條件是 Phase 0 驗證 Alembic migration 通過；走方案 C（Zeabur）則兩者都不需要——直接 volume + SQLite |

### 6.3 設定與密鑰

```
GEMINI_API_KEY=            # 必填
FINMIND_TOKEN=             # 必填（免費註冊）
OPENROUTER_API_KEY=        # 選配（最後備援）
DATABASE_URL=sqlite:///data/app.db   # 雲端改 postgres://...
SCHEDULER_MODE=internal    # internal | external
JOB_TOKEN=                 # 雲端排程觸發用（隨機長字串）
API_TOKEN=                 # 公開部署必填；私人 API Bearer Token
ALERT_WEBHOOK_URL=         # 選配；警示觸發 webhook
CORS_ORIGINS=http://localhost:3000
```
啟動時 `config.py` 驗證必填項，缺少即 fail fast。

## 7. 測試策略對應

| 層 | 工具 | 重點 |
|----|------|------|
| 單元 | pytest | 指標、回測、交易日曆、配額、撮合與風控 |
| 整合 | pytest + TestClient + 測試 DB | API envelope、認證、市場隔離、工作狀態與 migration |
| 前端 | Vitest + Testing Library | API Token、資料狀態與圖表元件 |
| 建置 | ESLint + Next production build | TypeScript、SSR/SSG 與 bundle 驗證 |
| AI 品質 | 離線評測腳本 | 固定輸入集跑各模型，人工評分報告合理性（prompt 迭代用） |
