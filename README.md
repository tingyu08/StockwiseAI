# stock-ai-advisor

AI 股票分析與模擬交易平台（台股＋美股）。免費 AI（Gemini）驅動的個股分析、
策略建議、走勢預測、AI 模擬買賣、多股報酬率與 ETF 折溢價比較。

## 文件

| 文件 | 內容 |
|------|------|
| [docs/PLAN.md](docs/PLAN.md) | 產品計畫：功能、AI 配額策略、開發階段 |
| [docs/SA.md](docs/SA.md) | 系統分析：使用案例、功能/非功能需求 |
| [docs/SD.md](docs/SD.md) | 系統設計：ADR、架構、API 規格、DB Schema、部署 |

## 快速開始（本機 Docker，方案 A）

```bash
cp .env.example .env    # 填入 GEMINI_API_KEY 與 FINMIND_TOKEN
docker compose up --build
# 前端 http://localhost:3000 ／ 後端 API http://localhost:8123/api/v1/health
```

## 本機開發（不透過 Docker）

```bash
# 後端
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install --require-hashes -r requirements.lock
pip install -e ".[dev]" --no-deps
alembic upgrade head
uvicorn app.main:app --reload        # http://localhost:8000

# 前端
cd frontend
npm install
npm run dev                          # http://localhost:3000

# 測試
cd backend && pytest

# 完整驗證
cd backend && pytest -q && ruff check app tests alembic
cd ../frontend && npm run test:coverage && npm run lint && npm run build
```

## 安全與通知

- 網站 API 不需要登入或瀏覽器 Token；公開部署時，任何知道網址的人都能操作自選、警示、AI 與模擬交易功能。
- 設定 `ALERT_WEBHOOK_URL` 後，價格與 ETF 折溢價警示會以 JSON webhook 送出。
- 外部排程會寫入 `job_runs`，可查詢 queued/running/succeeded/failed 狀態並重試失敗工作。
- production 必須設定 `ENVIRONMENT=production` 與 `JOB_TOKEN`；後者只保護 GitHub Actions 排程入口。
- `/health/live` 只檢查程序，`/health/ready` 會實際檢查 DB；Render health check 使用後者。
- 長任務會顯示在頁首「工作」中心；換頁後可恢復追蹤，失敗工作可直接重試。

## 維運與故障恢復

- 背景工作由 DB queue 執行，具 idempotency key、lease、heartbeat 與 stale-job recovery；不要以 HTTP request 存活時間判斷任務成敗。
- GitHub Actions 的排程腳本會等待 `JobRun` 結束才執行下一步；`maintenance` 每週清理 30 天前成功工作、90 天前失敗工作與 90 天前 AI 用量。
- 新聞/AI 上游逾時時，可在頁首工作中心重試；行情同步會重抓最近 14 天並 upsert，能修正缺口與上游歷史修訂。
- Render Free 僅使用單一 instance，migration 可在 start command 執行；升級多 instance 前必須改為單一 release/pre-deploy job。
- 完整修復與外部驗證邊界見 [docs/AUDIT_REMEDIATION.md](docs/AUDIT_REMEDIATION.md)。

## 主要功能

- 台股／美股搜尋、自選群組與拖曳排序
- K 線、成交量、MA、布林通道、RSI、KD、MACD
- Gemini 結構化分析與 Antigravity 新聞研究
- 交易所日曆感知的 5／20 日回歸通道預測
- 多股績效比較與 ETF 折溢價歷史
- 具持倉限制、原子撮合與交易成本的 AI 模擬交易
- MA、RSI、布林策略回測，包含滑價、Sharpe、最大回撤及勝率
- 價格／折溢價警示、資料新鮮度與 AI 額度監控

## 部署路線（已定案）

開發＝本機 Docker（方案 A）→ 上線＝Vercel＋Render＋Neon（方案 B，$0）
→ 使用一段時間後視情況遷 Zeabur（方案 C，$5/月）。細節見 [docs/SD.md §6](docs/SD.md)。
