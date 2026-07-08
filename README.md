# stock-ai-advisor

AI 股票分析與模擬交易平台（台股＋美股）。免費 AI（Gemini/Gemma）驅動的個股分析、
策略建議、走勢預測、AI 模擬買賣、多股報酬率與 ETF 折溢價比較。

## 文件

| 文件 | 內容 |
|------|------|
| [PLAN.md](PLAN.md) | 產品計畫：功能、AI 配額策略、開發階段 |
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
```

## 部署路線（已定案）

開發＝本機 Docker（方案 A）→ 上線＝Vercel＋Render＋Neon（方案 B，$0）
→ 使用一段時間後視情況遷 Zeabur（方案 C，$5/月）。細節見 [docs/SD.md §6](docs/SD.md)。
