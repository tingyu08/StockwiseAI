# stock-ai-advisor

AI 股票分析與模擬交易平台（台股＋美股）。免費 AI（Gemini/Gemma）驅動的個股分析、
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
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload        # http://localhost:8000

# 前端
cd frontend
npm install
npm run dev                          # http://localhost:3000

# 測試
cd backend && pytest

# 完整驗證
cd backend && pytest -q && ruff check app tests
cd ../frontend && npm test && npm run lint && npm run build
```

## 安全與通知

- 公開部署時請設定 `API_TOKEN`。除 `/health` 與帶 `X-Job-Token` 的排程觸發外，API 會要求 `Authorization: Bearer <token>`。
- 前端右上角的「API Token」按鈕只將 token 保存在當次瀏覽器 session。
- 設定 `ALERT_WEBHOOK_URL` 後，價格與 ETF 折溢價警示會以 JSON webhook 送出。
- 外部排程會寫入 `job_runs`，可查詢 queued/running/succeeded/failed 狀態並重試失敗工作。

## 主要功能

- 台股／美股搜尋、自選群組與拖曳排序
- K 線、成交量、MA、布林通道、RSI、KD、MACD
- Gemini/Gemma 結構化分析與 Antigravity 新聞研究
- 交易所日曆感知的 5／20 日回歸通道預測
- 多股績效比較與 ETF 折溢價歷史
- 具持倉限制、原子撮合與交易成本的 AI 模擬交易
- MA、RSI、布林策略回測，包含滑價、Sharpe、最大回撤及勝率
- 價格／折溢價警示、資料新鮮度與 AI 額度監控

## 部署路線（已定案）

開發＝本機 Docker（方案 A）→ 上線＝Vercel＋Render＋Neon（方案 B，$0）
→ 使用一段時間後視情況遷 Zeabur（方案 C，$5/月）。細節見 [docs/SD.md §6](docs/SD.md)。
