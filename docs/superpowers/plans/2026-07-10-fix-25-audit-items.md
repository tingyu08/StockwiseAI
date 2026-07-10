# StockwiseAI 25-Item Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修復程式碼審查確認的 25 項可靠性、資料正確性、安全性、部署與使用體驗問題。

**Architecture:** 以 PostgreSQL `job_runs` 作為可恢復工作佇列與 lease 狀態來源，HTTP 只建立工作並由同程序 worker claim；GitHub Actions 以 run_id 輪詢。AI 呼叫使用原子 quota reservation、語意驗證與不可信新聞隔離。其餘修改維持既有 FastAPI、SQLAlchemy、React Query 架構，以小型服務函式與資料庫約束補強。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy/Alembic、PostgreSQL/SQLite、httpx、Pydantic 2、Next.js 15、React Query、Vitest、GitHub Actions、Render。

## Global Constraints

- 所有行為變更先寫失敗測試並確認 RED，再實作 GREEN。
- 不新增付費服務依賴；repository 內先提供 DB-backed worker，保留日後切換 Render Workflow/Celery 的介面。
- 時間一律以 UTC aware/naive DB 邊界清楚處理；交易日與 Google quota 使用各自官方時區。
- 所有 API 保持既有 `{success,data,error,meta}` envelope。
- 每批完成後跑相關測試，最後跑完整 backend/frontend/build/migration 驗證。

---

### Task 1: AI 額度、逾時與外部資料重試

**Files:**
- Modify: `backend/app/core/rate_limiter.py`
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/app/providers/ai/antigravity.py`
- Modify: `backend/app/providers/market/finmind.py`
- Test: `backend/tests/test_rate_limiter.py`
- Test: `backend/tests/test_ai_timeout_routing.py`
- Test: `backend/tests/test_finmind_fallback.py`

**Interfaces:**
- Produces: `provider_day_bounds_utc(now)`, monotonic Antigravity deadline、可重試 FinMind fetch、quota reservation/release API。

- [x] 新增測試：RPD 以 `America/Los_Angeles` 午夜重置，並覆蓋 DST。
- [x] 執行測試確認目前 Taipei 邏輯失敗。
- [x] 實作 provider day bounds，更新 usage 查詢。
- [x] 新增測試：Antigravity HTTP read timeout 也計入 480 秒 wall-clock deadline。
- [x] 實作 monotonic deadline 與可測 clock 注入。
- [x] 新增測試：FinMind status error 會 backoff 後重試三次。
- [x] 實作統一 retry/backoff，保留最後錯誤 cause。
- [x] 新增 quota reservation 模型與 migration，測試未完成 reservation 會占用 RPM/RPD。
- [x] 在 Gemini/Antigravity 呼叫前 reserve、完成後 finalize，JSON 重試也重新 reserve。
- [x] 跑 rate limiter、AI、FinMind、完整 backend 與 migration smoke 測試並提交。

### Task 2: 可恢復背景工作與排程

**Files:**
- Modify: `backend/app/models/job.py`
- Create: `backend/app/services/job_service.py`
- Modify: `backend/app/api/v1/jobs.py`
- Modify: `backend/app/api/v1/analysis.py`
- Modify: `backend/app/main.py`
- Create: `backend/alembic/versions/*_durable_jobs.py`
- Modify: `.github/workflows/cron.yml`
- Test: `backend/tests/test_job_runs.py`

**Interfaces:**
- Produces: `enqueue_job(type, payload, idempotency_key)`, `claim_next_job()`, `heartbeat_job()`, `recover_stale_jobs()`, `run_worker_loop()`。

- [x] 新增測試：動態 news/overview 工作保存 type/payload 並可 retry。
- [x] 新增測試：相同 idempotency key 只建立一個 queued/running 工作。
- [x] 新增測試：過期 running 工作會回 queued 或 failed，超過 attempts 才 failed。
- [x] 新增 JobRun payload、idempotency、heartbeat、lease、max_attempts 欄位與 migration。
- [x] 抽出 job registry，讓固定排程與動態工作共用 handler。
- [x] lifespan 啟動 DB worker loop，關閉時取消並等待。
- [x] API 僅 enqueue；GET/retry 使用新 service，不再直接 `create_task()`。
- [x] Cron 改為 POST 取得 run_id，再輪詢狀態；每日 sequence 每步成功才前進。
- [x] 每日 sequence 先等待 news orchestrator 完成，再執行 AI batch。
- [x] 跑 job/API、完整 backend、Ruff、migration 與 Cron YAML tests 並提交。

### Task 3: AI 語意安全、快取與版本

**Files:**
- Modify: `backend/app/providers/ai/schemas.py`
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/app/services/analysis_service.py`
- Modify: `backend/app/services/news_service.py`
- Modify: `backend/app/models/analysis.py`
- Create: `backend/alembic/versions/*_analysis_input_hash.py`
- Test: `backend/tests/test_ai_schema.py`
- Test: `backend/tests/test_analysis_race.py`
- Test: `backend/tests/test_news.py`

**Interfaces:**
- Produces: semantic Pydantic validators、`analysis_input_hash(context,prompt_version)`、明確 untrusted-news delimiters。

- [ ] 新增測試拒絕負價格、low>high、stop loss 不合理、機率總和偏離 1、symbol 不符。
- [ ] 實作 schema/model validators 與批次 symbol set 驗證。
- [ ] 新增 prompt-injection 測試，確認新聞置於 `UNTRUSTED_NEWS` delimiter 且 system prompt 禁止執行內文指令。
- [ ] 實作新聞隔離、來源欄位與長度限制。
- [ ] 新增測試：新聞、prompt、自選內容變更時 input hash 改變並重新生成；內容相同才命中快取。
- [ ] 新增 input_hash/prompt_version 欄位與 migration，更新 unique/cache 策略。
- [ ] Overview 以 portfolio input hash upsert，提供 force rebuild 但保留 quota 防護。
- [ ] 跑 AI/analysis/news tests 並提交。

### Task 4: 交易、警示與資料正確性

**Files:**
- Modify: `backend/app/services/sim/decision.py`
- Modify: `backend/app/services/sim/engine.py`
- Modify: `backend/app/services/alert_service.py`
- Modify: `backend/app/models/alert.py`
- Modify: `backend/app/services/sync_service.py`
- Create: `backend/app/services/time_service.py`
- Create: `backend/alembic/versions/*_alert_outbox_prediction_unique.py`
- Test: `backend/tests/test_simulation.py`
- Test: `backend/tests/test_alert_notifications.py`
- Test: `backend/tests/test_stocks_api.py`

**Interfaces:**
- Produces: market-aware dates、portfolio cash reservation、O(1) affordable quantity、alert outbox、recent-window price upsert。

- [ ] 新增測試：多檔買進合計不超過可用現金且配置不受迭代順序意外影響。
- [ ] 新增測試：縮量以公式完成並符合費用/台股整股規則。
- [ ] 實作預留現金與二分/公式 sizing。
- [ ] 新增測試：webhook 失敗保留 pending notification，成功後標 sent；同日事件不可重複。
- [ ] 新增 alert event unique/outbox 欄位與 retry service。
- [ ] 新增市場時區與業務日測試，替換交易/新聞/同步的 host `date.today()`。
- [ ] 新增測試：同步會重抓最近 10 天、upsert 修正值並補缺口。
- [ ] 實作 rolling refresh/upsert，不再只 append。
- [ ] 跑 simulation/alert/sync tests 並提交。

### Task 5: API 與前端背景工作體驗

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/api.test.ts`
- Create: `frontend/hooks/use-jobs.ts`
- Create: `frontend/components/job-center.tsx`
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/hooks/use-news.ts`
- Modify: `frontend/hooks/use-groups.ts`
- Modify: `frontend/hooks/use-simulation.ts`

**Interfaces:**
- Produces: content-type-safe API parsing、AbortSignal/timeout、持久 active run ids、可取消/恢復 polling 的 job center。

- [ ] 新增測試：HTML/空白 502 回應轉為 ApiError，而不是 JSON parse error。
- [ ] 新增測試：request timeout 與 waitForJob abort 停止輪詢。
- [ ] 實作 safe envelope parser、AbortController、Retry-After 顯示資料。
- [ ] 新增測試：run_id 存 sessionStorage，reload 後 job center 恢復 queued/running 工作。
- [ ] 實作 job store/hook/工作中心與 retry action。
- [ ] simulation decide 改走 background job，按鈕顯示實際階段。
- [ ] 跑 Vitest、ESLint、build 並提交。

### Task 6: DB 約束、效能、健康與保留政策

**Files:**
- Modify: `backend/app/models/analysis.py`
- Modify: `backend/app/models/job.py`
- Modify: `backend/app/services/sim/portfolio.py`
- Modify: `backend/app/api/v1/health.py`
- Create: `backend/app/services/maintenance_service.py`
- Create: `backend/alembic/versions/*_constraints_and_indexes.py`
- Test: `backend/tests/test_health.py`
- Test: `backend/tests/test_performance_contracts.py`

**Interfaces:**
- Produces: liveness/readiness/status、Prediction/Alert/Job indexes、批次持倉 query、retention cleanup job。

- [ ] 新增 migration 測試與 unique constraint 測試。
- [ ] 加 Prediction composite unique、Job status/created index、必要 FK/index。
- [ ] 以批次 query 改寫 positions DTO/average cost，保留查詢數 contract。
- [ ] 新增 `/health/live` 與 DB-aware `/health/ready` 測試及實作。
- [ ] data-status 分開 news/routine/trade dates 與最近成功 job。
- [ ] 新增 maintenance job 清理過期成功 JobRun/usage log，保留失敗紀錄較久。
- [ ] 跑 health/performance/migration tests 並提交。

### Task 7: Supply chain、CI、容器與安全預設

**Files:**
- Create: `backend/requirements.lock`
- Modify: `backend/Dockerfile`
- Modify: `frontend/next.config.ts`
- Modify: `frontend/Dockerfile`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/core/auth.py`
- Modify: `backend/app/main.py`
- Create: `.github/workflows/ci.yml`
- Modify: `render.yaml`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Produces: production fail-closed config、security headers、reproducible dependency install、non-root/standalone images、CI gate。

- [ ] 新增測試：production 缺 API_TOKEN/JOB_TOKEN 時 Settings 驗證失敗，development 可空白。
- [ ] 實作 environment mode、trimmed CORS、security headers、request correlation id 與敏感資訊遮罩。
- [ ] 產生並驗證 Python lock，Docker 使用 locked install。
- [ ] Next standalone build、non-root runtime、production dependency-only image。
- [ ] CI 加 backend tests/Ruff、frontend tests/lint/build、PostgreSQL Alembic smoke、audit、Docker build。
- [ ] 修正 Render 文件與 health path，migration 保持單實例安全並記錄擴展限制。
- [ ] 跑 auth/config tests、Docker builds 並提交。

### Task 8: 測試覆蓋、文件與總驗收

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `frontend/vitest.config.ts`
- Create: `frontend/e2e/*`
- Modify: `README.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/SA.md`
- Modify: `docs/SD.md`

**Interfaces:**
- Produces: PostgreSQL integration profile、coverage thresholds、核心 E2E、操作/恢復 runbook。

- [ ] 加 backend coverage 與 PostgreSQL integration markers，覆蓋 partial indexes、job lease、quota reservation。
- [ ] 加 frontend job flow、401、502、simulation E2E/元件測試與 coverage。
- [ ] 更新架構、時區、queue、cache、alert outbox、部署與故障恢復文件。
- [ ] 執行完整 backend pytest/Ruff/Alembic upgrade。
- [ ] 執行完整 frontend Vitest/ESLint/build。
- [ ] 執行 dependency audits、Docker build、git diff check。
- [ ] 逐項核對 25 項清單，記錄任何只能在外部 Render/Neon 驗證的剩餘事項。
- [ ] 使用 superpowers:finishing-a-development-branch 完成分支交付。
