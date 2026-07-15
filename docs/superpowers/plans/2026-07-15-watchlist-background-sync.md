# Watchlist Background Price Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make watchlist additions return immediately while a durable, retryable job synchronizes prices without blocking Render health checks, and prevent FinMind credentials from reaching logs.

**Architecture:** `POST /watchlist` persists the item and enqueues an idempotent `stock_sync` job. The shared sync primitive awaits provider I/O but moves all synchronous SQLAlchemy and indicator work into worker threads with thread-local sessions and one batch lookup for existing prices. The frontend tracks the returned job through the existing job center, and the logging filter redacts URL-like arguments.

**Tech Stack:** FastAPI, SQLAlchemy 2, asyncio, pandas, pytest, React 19, TanStack Query, Vitest, TypeScript.

## Global Constraints

- Preserve `/api/v1/health/live` as Render's health check path.
- Do not share a SQLAlchemy `Session` or ORM instance across threads.
- Keep watchlist persistence successful even when sync enqueueing or execution fails.
- Use the existing `JobRun` worker, lease, retry, and job-center behavior; do not add another queue dependency.
- Use active idempotency key `stock-sync:{market}:{symbol}` and `max_attempts=3`.
- Never log configured credentials or reproduce the exposed FinMind token in source, tests, commits, or command output.
- Keep existing market-provider retry policies unchanged.

---

### Task 1: Non-blocking, batch-oriented price persistence

**Files:**
- Modify: `backend/app/services/sync_service.py`
- Modify: `backend/app/scheduler/jobs.py`
- Modify: `backend/app/api/v1/stocks.py`
- Modify: `backend/tests/test_sync_service.py`

**Interfaces:**
- Consumes: `market_data.get_daily_prices(market, symbol, start, end)` and `SessionLocal`.
- Produces: `async def sync_prices(stock_id: int, market: str, symbol: str) -> int`, `_load_last_price_date(stock_id: int) -> date | None`, and `_persist_price_rows(stock_id: int, rows: list[OhlcvRow]) -> int`.

- [ ] **Step 1: Update the existing sync test to the scalar interface and add failing batch/thread tests**

Add imports and tests in `backend/tests/test_sync_service.py`:

```python
import asyncio
import threading

from sqlalchemy import event


async def test_sync_persistence_runs_off_the_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    persisted_on = None

    monkeypatch.setattr(sync_service, "_load_last_price_date", lambda _stock_id: None)

    async def fake_prices(_market, _symbol, _start, _end):
        return []

    def fake_persist(_stock_id, _rows):
        nonlocal persisted_on
        persisted_on = threading.get_ident()
        return 0

    monkeypatch.setattr(sync_service.market_data, "get_daily_prices", fake_prices)
    monkeypatch.setattr(sync_service, "_persist_price_rows", fake_persist)

    assert await sync_service.sync_prices(1, "TW", "2330") == 0
    assert persisted_on is not None
    assert persisted_on != main_thread


def test_persist_price_rows_loads_existing_prices_in_one_query(monkeypatch):
    db = SessionLocal()
    try:
        stock = Stock(symbol="BATCH", market="US", name="Batch", currency="USD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        rows = [
            OhlcvRow(date=market_today("US") - timedelta(days=index), open=10, high=11, low=9, close=10, volume=100)
            for index in range(3)
        ]
        monkeypatch.setattr(sync_service, "_recompute_indicators", lambda *_args: None)
        selects = []

        def count_select(_conn, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith("SELECT") and "daily_prices" in statement:
                selects.append(statement)

        event.listen(sync_service.engine, "before_cursor_execute", count_select)
        try:
            assert sync_service._persist_price_rows(stock.id, rows) == 3
        finally:
            event.remove(sync_service.engine, "before_cursor_execute", count_select)
        assert len(selects) == 1
    finally:
        db.close()
```

Change the existing call to:

```python
changed = await sync_service.sync_prices(stock.id, stock.market, stock.symbol)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_sync_service.py
```

Expected: FAIL because the scalar signature and private batch helpers do not exist and DB persistence still runs on the event-loop thread.

- [ ] **Step 3: Implement thread-local sessions and one existing-price query**

In `backend/app/services/sync_service.py`, import `asyncio`, `engine`, and `OhlcvRow`, then replace the persistence portion with:

```python
def _load_last_price_date(stock_id: int) -> date | None:
    with SessionLocal() as db:
        return db.execute(
            select(DailyPrice.date)
            .where(DailyPrice.stock_id == stock_id)
            .order_by(DailyPrice.date.desc())
            .limit(1)
        ).scalar_one_or_none()


def _persist_price_rows(stock_id: int, rows: list[OhlcvRow]) -> int:
    with SessionLocal() as db:
        dates = [row.date for row in rows]
        existing_rows = (
            db.execute(
                select(DailyPrice).where(
                    DailyPrice.stock_id == stock_id,
                    DailyPrice.date.in_(dates),
                )
            ).scalars().all()
            if dates
            else []
        )
        existing_by_date = {row.date: row for row in existing_rows}
        changed = 0
        for row in rows:
            existing = existing_by_date.get(row.date)
            values = {
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
            }
            if existing is None:
                db.add(DailyPrice(stock_id=stock_id, date=row.date, **values))
                changed += 1
                continue
            before = (
                _clean_number(existing.open),
                _clean_number(existing.high),
                _clean_number(existing.low),
                _clean_number(existing.close),
                existing.volume,
            )
            if before != (row.open, row.high, row.low, row.close, row.volume):
                for key, value in values.items():
                    setattr(existing, key, value)
                changed += 1
        if changed:
            db.flush()
            stock = db.get(Stock, stock_id)
            if stock is None:
                raise NotFoundError(f"找不到股票 id={stock_id}")
            _recompute_indicators(db, stock)
        db.commit()
        return changed


async def sync_prices(stock_id: int, market: str, symbol: str) -> int:
    last = await asyncio.to_thread(_load_last_price_date, stock_id)
    today = market_today(market)
    start = last - timedelta(days=REFRESH_LOOKBACK_DAYS) if last else today - timedelta(days=INITIAL_LOOKBACK_DAYS)
    if start > today:
        return 0
    rows = await market_data.get_daily_prices(market, symbol, start, today)
    changed = await asyncio.to_thread(_persist_price_rows, stock_id, rows)
    logger.info("synced %s/%s: %d rows changed", market, symbol, changed)
    return changed
```

Update callers:

```python
await sync_prices(stock.id, stock.market, stock.symbol)
```

and in `add_stock`:

```python
added = await sync_prices(stock.id, stock.market, stock.symbol)
```

- [ ] **Step 4: Run focused and caller tests and verify GREEN**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_sync_service.py tests/test_stocks_api.py
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the non-blocking sync primitive**

```powershell
git add backend/app/services/sync_service.py backend/app/scheduler/jobs.py backend/app/api/v1/stocks.py backend/tests/test_sync_service.py
git commit -m "fix: move price persistence off event loop"
```

---

### Task 2: Durable per-stock sync job and immediate watchlist response

**Files:**
- Modify: `backend/app/api/v1/watchlist.py`
- Modify: `backend/app/services/job_service.py`
- Create: `backend/tests/test_watchlist_add.py`
- Modify: `backend/tests/test_job_runs.py`

**Interfaces:**
- Consumes: `enqueue_job(...)`, `sync_prices(stock_id, market, symbol)`, and `JobRun` retry behavior.
- Produces: `job_type="stock_sync"` dispatch and an add-watch response with `started: bool`, `job: str | None`, and `run_id: int | None`.

- [ ] **Step 1: Add failing API tests for immediate enqueue and enqueue failure**

Create `backend/tests/test_watchlist_add.py`:

```python
from sqlalchemy import select

from app.api.v1 import watchlist
from app.core.db import SessionLocal
from app.models import Stock, WatchlistItem


def _stock(symbol: str) -> Stock:
    db = SessionLocal()
    try:
        stock = Stock(symbol=symbol, market="TW", name=symbol, currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        db.refresh(stock)
        db.expunge(stock)
        return stock
    finally:
        db.close()


def test_add_watch_enqueues_sync_without_waiting_for_prices(client, monkeypatch):
    stock = _stock("QADD1")
    queued = {}

    async def fake_ensure(_db, _market, _symbol):
        return stock

    def fake_enqueue(name, **kwargs):
        queued.update({"name": name, **kwargs})
        return 321

    monkeypatch.setattr(watchlist, "ensure_stock", fake_ensure)
    monkeypatch.setattr(watchlist, "enqueue_job", fake_enqueue)

    response = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD1"})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "symbol": "QADD1",
        "market": "TW",
        "name": "QADD1",
        "started": True,
        "job": "sync-tw-qadd1",
        "run_id": 321,
    }
    assert queued["job_type"] == "stock_sync"
    assert queued["idempotency_key"] == "stock-sync:TW:QADD1"
    with SessionLocal() as db:
        assert db.scalar(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))


def test_add_watch_survives_sync_enqueue_failure(client, monkeypatch):
    stock = _stock("QADD2")

    async def fake_ensure(_db, _market, _symbol):
        return stock

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(watchlist, "ensure_stock", fake_ensure)
    monkeypatch.setattr(watchlist, "enqueue_job", fail_enqueue)

    response = client.post("/api/v1/watchlist", json={"market": "TW", "symbol": "QADD2"})

    assert response.status_code == 200
    assert response.json()["data"]["started"] is False
    assert response.json()["data"]["run_id"] is None
    with SessionLocal() as db:
        assert db.scalar(select(WatchlistItem).where(WatchlistItem.stock_id == stock.id))
```

- [ ] **Step 2: Add a failing dispatcher test**

Append to `backend/tests/test_job_runs.py`:

```python
async def test_stock_sync_job_dispatches_by_scalar_identity(monkeypatch):
    db = SessionLocal()
    try:
        stock = Stock(symbol="QJOB", market="TW", name="Job", currency="TWD", kind="stock")
        db.add(stock)
        db.commit()
        stock_id = stock.id
    finally:
        db.close()

    seen = {}

    async def fake_sync(current_id, market, symbol):
        seen.update({"stock_id": current_id, "market": market, "symbol": symbol})
        return 7

    monkeypatch.setattr("app.services.sync_service.sync_prices", fake_sync)
    result = await services.job_service.dispatch_job(
        "stock_sync", {"market": "TW", "symbol": "QJOB"}
    )

    assert seen == {"stock_id": stock_id, "market": "TW", "symbol": "QJOB"}
    assert result == {"market": "TW", "symbol": "QJOB", "synced_rows": 7}
```

Add `from app.models import Stock` to the imports in `backend/tests/test_job_runs.py`.

- [ ] **Step 3: Run new tests and verify RED**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_watchlist_add.py tests/test_job_runs.py -k "add_watch or stock_sync"
```

Expected: FAIL because `add_watch` still awaits `sync_prices`, does not return job metadata, and the dispatcher rejects `stock_sync`.

- [ ] **Step 4: Implement enqueueing and job dispatch**

In `watchlist.py`, replace the direct sync with:

```python
job_name = f"sync-{stock.market.lower()}-{stock.symbol.lower()}"
try:
    run_id = enqueue_job(
        job_name,
        job_type="stock_sync",
        payload={"market": stock.market, "symbol": stock.symbol},
        idempotency_key=f"stock-sync:{stock.market}:{stock.symbol}",
        max_attempts=3,
    )
except Exception:
    logger.exception("failed to enqueue stock sync %s/%s", stock.market, stock.symbol)
    run_id = None
return ok({
    "symbol": stock.symbol,
    "market": stock.market,
    "name": stock.name,
    "started": run_id is not None,
    "job": job_name if run_id is not None else None,
    "run_id": run_id,
})
```

Remove the `sync_prices` import and add `logging`, `logger`, and `enqueue_job` imports.

In `job_service.dispatch_job`, add:

```python
if job_type == "stock_sync":
    from sqlalchemy import select as sa_select
    from app.models import Stock
    from app.services.sync_service import sync_prices

    market, symbol = payload["market"], payload["symbol"]
    db = SessionLocal()
    try:
        stock = db.execute(
            sa_select(Stock).where(Stock.market == market, Stock.symbol == symbol)
        ).scalar_one_or_none()
        if stock is None:
            raise NotFoundError(f"找不到股票 {market}/{symbol}")
        stock_id = stock.id
    finally:
        db.close()
    changed = await sync_prices(stock_id, market, symbol)
    return {"market": market, "symbol": symbol, "synced_rows": changed}
```

- [ ] **Step 5: Run watchlist and job tests and verify GREEN**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_watchlist_add.py tests/test_job_runs.py tests/test_groups.py
```

Expected: all selected tests pass and no market provider is called by `POST /watchlist`.

- [ ] **Step 6: Commit the durable stock job**

```powershell
git add backend/app/api/v1/watchlist.py backend/app/services/job_service.py backend/tests/test_watchlist_add.py backend/tests/test_job_runs.py
git commit -m "fix: queue watchlist price synchronization"
```

---

### Task 3: Track watchlist sync in the frontend job center

**Files:**
- Modify: `frontend/hooks/use-stocks.ts`
- Create: `frontend/hooks/use-stocks.test.tsx`

**Interfaces:**
- Consumes: add-watch response `{ started, job, run_id }` and `trackActiveJob({ runId, name })`.
- Produces: immediate watchlist invalidation plus job-center tracking when `run_id` is non-null.

- [ ] **Step 1: Write the failing hook test**

Create `frontend/hooks/use-stocks.test.tsx`:

```tsx
/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { describe, expect, it, vi } from "vitest";

import { apiRequest, trackActiveJob } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { useAddWatch } from "./use-stocks";

vi.mock("@/lib/api", () => ({
  apiGet: vi.fn(),
  apiRequest: vi.fn(),
  trackActiveJob: vi.fn(),
}));

describe("useAddWatch", () => {
  it("tracks the durable stock sync job", async () => {
    useMarketStore.setState({ market: "tw" });
    vi.mocked(apiRequest).mockResolvedValue({
      symbol: "2434",
      market: "TW",
      name: "統懋",
      started: true,
      job: "sync-tw-2434",
      run_id: 44,
    });
    const queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useAddWatch(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("2434");
    });

    expect(trackActiveJob).toHaveBeenCalledWith({ runId: 44, name: "sync-tw-2434" });
  });
});
```

- [ ] **Step 2: Run the hook test and verify RED**

```powershell
cd frontend
npm.cmd test -- hooks/use-stocks.test.tsx
```

Expected: FAIL because `useAddWatch` does not call `trackActiveJob`.

- [ ] **Step 3: Implement typed tracking**

In `frontend/hooks/use-stocks.ts`, import `trackActiveJob`, define:

```typescript
interface AddWatchResult {
  symbol: string;
  market: string;
  name: string;
  started: boolean;
  job: string | null;
  run_id: number | null;
}
```

Then update the mutation:

```typescript
mutationFn: (symbol: string) => apiRequest<AddWatchResult>("/watchlist", {
  method: "POST", body: { market: market.toUpperCase(), symbol },
}),
onSuccess: (result) => {
  if (result.run_id !== null && result.job) {
    trackActiveJob({ runId: result.run_id, name: result.job });
  }
  qc.invalidateQueries({ queryKey: ["watchlist", market] });
},
```

- [ ] **Step 4: Run frontend unit tests and verify GREEN**

```powershell
cd frontend
npm.cmd test -- hooks/use-stocks.test.tsx components/job-center.test.tsx lib/api.test.ts
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit frontend tracking**

```powershell
git add frontend/hooks/use-stocks.ts frontend/hooks/use-stocks.test.tsx
git commit -m "feat: track watchlist synchronization jobs"
```

---

### Task 4: Redact secrets in URL-like logging arguments

**Files:**
- Modify: `backend/app/core/logging_config.py`
- Modify: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: configured secret strings and arbitrary `LogRecord.args` values.
- Produces: `_redact_arg(value: object, settings: Settings) -> object`, preserving unaffected typed values and replacing only arguments whose string form contains a secret.

- [ ] **Step 1: Write the failing URL-argument test**

Add imports `logging`, `httpx`, and `SecretRedactingFilter` to `test_auth.py`, then add:

```python
def test_sensitive_url_object_is_redacted_from_log_arguments():
    settings = Settings(_env_file=None, finmind_token="finmind-private-token")
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "HTTP Request: %s status=%d",
        (httpx.URL("https://example.test/data?token=finmind-private-token"), 200),
        None,
    )

    assert SecretRedactingFilter(settings).filter(record) is True
    assert "finmind-private-token" not in record.getMessage()
    assert "[REDACTED]" in record.getMessage()
    assert record.args[1] == 200
```

- [ ] **Step 2: Run the test and verify RED**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_auth.py -k sensitive_url
```

Expected: FAIL because `httpx.URL` is not a string and remains unredacted.

- [ ] **Step 3: Implement type-safe argument redaction**

In `logging_config.py`, add:

```python
def _redact_arg(value: object, settings: Settings) -> object:
    if isinstance(value, str):
        return redact_sensitive(value, settings)
    rendered = str(value)
    redacted = redact_sensitive(rendered, settings)
    return redacted if redacted != rendered else value
```

Use `_redact_arg` for mapping values and tuple members instead of only handling strings. Leave mapping keys untouched.

- [ ] **Step 4: Run logging and auth tests and verify GREEN**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_auth.py
```

Expected: all auth tests pass, the URL object is redacted, and integer `%d` arguments remain integers.

- [ ] **Step 5: Commit logging protection**

```powershell
git add backend/app/core/logging_config.py backend/tests/test_auth.py
git commit -m "fix: redact secrets from URL log arguments"
```

---

### Task 5: Documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `docs/PLAN.md`

**Interfaces:**
- Consumes: completed behavior from Tasks 1-4.
- Produces: operator-facing documentation that watchlist additions queue durable price sync jobs and that the exposed credential must be rotated.

- [ ] **Step 1: Update operator documentation**

Add this behavior to `README.md`:

```markdown
- 新增自選股會立即寫入並建立可重試的背景價格同步工作；同步狀態可在「工作」中心查看，不會阻塞 Render health check。
```

Under `docs/PLAN.md` Phase 1, immediately after the completed daily price-sync item, add:

```markdown
- [x] 新增自選股立即建立 durable `stock_sync` 工作；既有價格採批次查詢，DB 寫入與指標重算移出 event loop
```

Do not include any credential value.

- [ ] **Step 2: Run the complete backend verification**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests alembic
```

Expected: all backend tests pass, coverage remains above 75%, and Ruff reports no errors.

- [ ] **Step 3: Run the complete frontend verification**

```powershell
cd frontend
npm.cmd run test:coverage
npm.cmd run lint
npm.cmd run build
```

Expected: all frontend tests pass, configured coverage thresholds pass, ESLint reports no errors, and the production build succeeds.

- [ ] **Step 4: Verify diffs and credential hygiene**

From the repository root:

```powershell
git diff --check
git diff --stat
git diff | Select-String -Pattern 'token=[^& ]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+'
```

Expected: `git diff --check` succeeds and the credential scan returns no matches. Review `git diff` to confirm `POST /watchlist` does not await `sync_prices` and `sync_prices` uses `asyncio.to_thread` around database phases.

- [ ] **Step 5: Commit documentation and final integration state**

```powershell
git add README.md docs/PLAN.md
git commit -m "docs: describe background watchlist sync"
git status --short --branch
```

Expected: working tree is clean and `main` is ahead of `origin/main` only by the intentional commits. Do not push without explicit user authorization.
