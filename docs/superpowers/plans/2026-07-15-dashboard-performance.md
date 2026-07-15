# Dashboard Loading Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make warm navigation render from cache, reduce the stock-detail initial load to one dashboard request, and expose application-versus-database latency without logging private data.

**Architecture:** A new read-only dashboard service composes existing price, prediction, analysis, news, and quota readers behind one backward-compatible endpoint. The frontend consumes that endpoint with query-specific stale times and explicit mutation invalidation. A FastAPI middleware and SQLAlchemy cursor events expose request-local timing in structured logs and `Server-Timing` headers.

**Tech Stack:** FastAPI, SQLAlchemy 2, ContextVar, pytest, React 19, TanStack Query, Vitest, TypeScript.

## Global Constraints

- Keep all existing detail endpoints available and backward compatible.
- The dashboard read path must never invoke an AI provider or market-data provider.
- `stock`, `series`, and `usage` are always present; `prediction`, `analysis`, and `news` are nullable.
- Use authenticated TanStack Query memory caching only; do not add public/shared HTTP caching or persistent browser storage.
- Dashboard and price stale time is exactly 5 minutes; watchlist, stored analysis, and stored news stale time is exactly 10 minutes; AI usage stale time is exactly 1 minute.
- Mutations must update or invalidate every affected dashboard cache entry immediately.
- Timing logs include the URL path only and never include query strings, bodies, headers, cookies, SQL text, SQL parameters, or credentials.
- `/api/v1/health/live` does not emit normal INFO timing logs.
- Requests with `total_ms >= 1000` log at WARNING; faster requests log at INFO.
- Keep `/api/v1/health/live`, AI models, quotas, prompts, retries, and JobRun behavior unchanged.
- Follow TDD for every behavior change and never lower existing coverage thresholds.

---

### Task 1: Read-only stock dashboard endpoint

**Files:**
- Create: `backend/app/services/stock_read_service.py`
- Create: `backend/app/services/dashboard_service.py`
- Modify: `backend/app/core/rate_limiter.py`
- Modify: `backend/app/api/v1/stocks.py`
- Modify: `backend/app/api/v1/usage.py`
- Create: `backend/tests/test_dashboard.py`
- Modify: `backend/tests/test_stocks_api.py`

**Interfaces:**
- Consumes: `prediction_service.get_predictions`, `analysis_service.latest_report`, `analysis_service.report_dto`, `news_service.latest_news_report`, `news_service.news_dto`, and `used_today`.
- Produces: `get_stock(db, market, symbol) -> Stock`, `get_price_series(db, stock, range_key) -> dict`, `usage_snapshot(db) -> list[dict]`, `build_dashboard(db, market, symbol, range_key) -> dict`, and `GET /api/v1/stocks/{symbol}/dashboard`.

- [ ] **Step 1: Write failing endpoint tests**

Create `backend/tests/test_dashboard.py` with a unique seeded stock and tests for the complete and partial response:

```python
import json
from datetime import timedelta

from app.core.db import SessionLocal
from app.models import AiReport, DailyPrice, Stock
from app.services.time_service import market_today


def _seed_dashboard_stock(symbol: str = "DASH1", days: int = 40) -> None:
    today = market_today("TW")
    with SessionLocal() as db:
        stock = Stock(
            symbol=symbol,
            market="TW",
            name="Dashboard",
            currency="TWD",
            kind="stock",
        )
        db.add(stock)
        db.commit()
        db.refresh(stock)
        for offset in range(days):
            value = 100 + offset
            db.add(
                DailyPrice(
                    stock_id=stock.id,
                    date=today - timedelta(days=days - offset - 1),
                    open=value,
                    high=value + 1,
                    low=value - 1,
                    close=value + 0.5,
                    volume=1000 + offset,
                )
            )
        db.commit()


def test_dashboard_returns_one_complete_payload_without_external_calls(client, monkeypatch):
    _seed_dashboard_stock()

    async def external_call_forbidden(*_args, **_kwargs):
        raise AssertionError("dashboard must not call an external provider")

    monkeypatch.setattr(
        "app.services.market_gateway.market_data.get_daily_prices",
        external_call_forbidden,
    )
    monkeypatch.setattr(
        "app.providers.ai.gemini.GeminiProvider.generate",
        external_call_forbidden,
    )

    response = client.get(
        "/api/v1/stocks/DASH1/dashboard",
        params={"market": "TW", "range": "3m"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["stock"]["symbol"] == "DASH1"
    assert len(data["series"]) == 40
    assert data["prediction"]["method"] == "regression_channel"
    assert data["analysis"] is None
    assert data["news"] is None
    assert isinstance(data["usage"], list)


def test_dashboard_includes_stored_analysis_and_news(client):
    _seed_dashboard_stock("DASH2")
    today = market_today("TW")
    with SessionLocal() as db:
        stock = db.query(Stock).filter_by(market="TW", symbol="DASH2").one()
        db.add_all(
            [
                AiReport(
                    stock_id=stock.id,
                    trade_date=today,
                    provider="gemini",
                    model="gemini-3.1-flash-lite",
                    prompt_version="v2",
                    input_hash="dashboard-analysis",
                    kind="routine",
                    action="hold",
                    confidence=0.7,
                    payload_json=json.dumps(
                        {
                            "symbol": "DASH2",
                            "action": "hold",
                            "confidence": 0.7,
                            "target_price_low": 120,
                            "target_price_high": 130,
                            "stop_loss": 110,
                            "reasoning": "stored",
                            "scenarios": {},
                            "risks": [],
                        }
                    ),
                ),
                AiReport(
                    stock_id=stock.id,
                    trade_date=today,
                    provider="antigravity",
                    model="antigravity-preview-05-2026",
                    prompt_version="news-v2",
                    input_hash="",
                    kind="news",
                    action=None,
                    confidence=None,
                    payload_json=json.dumps({"summary": "stored news"}),
                ),
            ]
        )
        db.commit()

    data = client.get(
        "/api/v1/stocks/DASH2/dashboard",
        params={"market": "TW", "range": "1y"},
    ).json()["data"]

    assert data["analysis"]["report"]["reasoning"] == "stored"
    assert data["news"]["summary"] == "stored news"


def test_dashboard_unknown_stock_returns_404(client):
    response = client.get(
        "/api/v1/stocks/NO-DASH/dashboard",
        params={"market": "TW", "range": "1y"},
    )
    assert response.status_code == 404


def test_dashboard_returns_null_prediction_when_history_is_short(client):
    _seed_dashboard_stock("DASH3", days=10)
    response = client.get(
        "/api/v1/stocks/DASH3/dashboard",
        params={"market": "TW", "range": "3m"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["prediction"] is None
```

Update `backend/tests/test_stocks_api.py` so the existing price endpoint test remains a compatibility assertion after extraction.

- [ ] **Step 2: Run the dashboard tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_dashboard.py tests/test_stocks_api.py
```

Expected: dashboard tests fail with HTTP 404 because the route does not exist; existing stock tests pass.

- [ ] **Step 3: Extract stock readers**

Create `backend/app/services/stock_read_service.py`:

```python
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.models import DailyPrice, Indicator, Stock
from app.services.time_service import market_today

RANGE_DAYS = {"3m": 90, "6m": 180, "1y": 365}


def get_stock(db: Session, market: str, symbol: str) -> Stock:
    stock = db.execute(
        select(Stock).where(Stock.market == market, Stock.symbol == symbol)
    ).scalar_one_or_none()
    if stock is None:
        raise NotFoundError(f"Stock not found: {market}/{symbol}")
    return stock


def stock_dto(stock: Stock) -> dict:
    return {
        "symbol": stock.symbol,
        "market": stock.market,
        "name": stock.name,
        "currency": stock.currency,
        "kind": stock.kind,
        "tracked": True,
    }


def get_price_series(db: Session, stock: Stock, range_key: str) -> dict:
    since = market_today(stock.market) - timedelta(days=RANGE_DAYS[range_key])
    prices = db.execute(
        select(DailyPrice)
        .where(DailyPrice.stock_id == stock.id, DailyPrice.date >= since)
        .order_by(DailyPrice.date)
    ).scalars().all()
    indicators = db.execute(
        select(Indicator)
        .where(Indicator.stock_id == stock.id, Indicator.date >= since)
        .order_by(Indicator.date)
    ).scalars().all()
    by_date = {row.date: row for row in indicators}
    series = []
    for price in prices:
        indicator = by_date.get(price.date)
        series.append(
            {
                "date": price.date.isoformat(),
                "open": _num(price.open),
                "high": _num(price.high),
                "low": _num(price.low),
                "close": _num(price.close),
                "volume": price.volume,
                "ma5": _num(indicator.ma5) if indicator else None,
                "ma20": _num(indicator.ma20) if indicator else None,
                "ma60": _num(indicator.ma60) if indicator else None,
                "rsi14": _num(indicator.rsi14) if indicator else None,
                "kd_k": _num(indicator.kd_k) if indicator else None,
                "kd_d": _num(indicator.kd_d) if indicator else None,
                "macd": _num(indicator.macd) if indicator else None,
                "macd_signal": _num(indicator.macd_signal) if indicator else None,
                "bb_upper": _num(indicator.bb_upper) if indicator else None,
                "bb_lower": _num(indicator.bb_lower) if indicator else None,
            }
        )
    return {"stock": stock_dto(stock), "series": series}


def _num(value) -> float | None:
    return float(value) if value is not None else None
```

Update `backend/app/api/v1/stocks.py` to import `get_stock`, `get_price_series`, and `stock_dto`; make `get_prices` return `ok(get_price_series(db, get_stock(db, market, symbol), range_))`; make search and add responses use `stock_dto`; remove duplicated range and DTO helpers.

- [ ] **Step 4: Extract reusable quota output and compose the dashboard**

Add to `backend/app/core/rate_limiter.py`:

```python
def usage_snapshot(db: Session) -> list[dict]:
    quotas = get_settings().load_quotas()
    return [
        {
            "model": model,
            "rpd": quota.rpd,
            "used": (used := used_today(db, model)),
            "remaining": max(0, quota.rpd - used),
        }
        for model, quota in quotas.items()
    ]
```

Change `backend/app/api/v1/usage.py` to return `ok(usage_snapshot(db))`.

Create `backend/app/services/dashboard_service.py`:

```python
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.core.rate_limiter import usage_snapshot
from app.services import analysis_service, news_service, prediction_service
from app.services.stock_read_service import get_price_series, get_stock


def build_dashboard(
    db: Session, market: str, symbol: str, range_key: str
) -> dict:
    stock = get_stock(db, market, symbol)
    data = get_price_series(db, stock, range_key)
    try:
        prediction = prediction_service.get_predictions(db, stock)
    except NotFoundError:
        prediction = None
    analysis = analysis_service.latest_report(db, stock)
    news = news_service.latest_news_report(db, stock)
    return {
        **data,
        "prediction": prediction,
        "analysis": analysis_service.report_dto(analysis) if analysis else None,
        "news": news_service.news_dto(news) if news else None,
        "usage": usage_snapshot(db),
    }
```

Add to `backend/app/api/v1/stocks.py` before the prices route:

```python
@router.get("/stocks/{symbol}/dashboard", response_model=Envelope)
def get_dashboard(
    symbol: str,
    market: Literal["TW", "US"] = Query(...),
    range_: Literal["3m", "6m", "1y"] = Query("1y", alias="range"),
    db: Session = Depends(get_db),
) -> Envelope:
    from app.services.dashboard_service import build_dashboard

    return ok(build_dashboard(db, market, symbol, range_))
```

- [ ] **Step 5: Run focused and full backend tests**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_dashboard.py tests/test_stocks_api.py tests/test_auth.py
.\.venv\Scripts\ruff.exe check app tests
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 6: Commit the dashboard backend**

```powershell
git add backend/app/services/stock_read_service.py backend/app/services/dashboard_service.py backend/app/core/rate_limiter.py backend/app/api/v1/stocks.py backend/app/api/v1/usage.py backend/tests/test_dashboard.py backend/tests/test_stocks_api.py
git commit -m "feat: add stock dashboard endpoint"
```

---

### Task 2: Request and SQL timing observability

**Files:**
- Create: `backend/app/core/request_timing.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_request_timing.py`

**Interfaces:**
- Consumes: the global SQLAlchemy `engine` and FastAPI `Request`/`call_next` middleware contract.
- Produces: `install_db_timing() -> None`, `request_timing_middleware(request, call_next)`, structured `app.performance` logs, and `Server-Timing` headers.

- [ ] **Step 1: Write failing timing tests**

Create `backend/tests/test_request_timing.py`:

```python
import logging


def test_api_response_exposes_app_and_db_timing(client, caplog):
    caplog.set_level(logging.INFO, logger="app.performance")

    response = client.get(
        "/api/v1/watchlist",
        params={"market": "TW", "secret": "synthetic-query-secret"},
    )

    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("app;dur=")
    assert ", db;dur=" in response.headers["Server-Timing"]
    records = [r.getMessage() for r in caplog.records if r.name == "app.performance"]
    message = next(item for item in records if "path=/api/v1/watchlist" in item)
    assert "method=GET" in message
    assert "status=200" in message
    assert "total_ms=" in message
    assert "db_ms=" in message
    assert "db_queries=" in message
    assert "synthetic-query-secret" not in message
    assert "secret=" not in message


def test_liveness_does_not_emit_info_timing_log(client, caplog):
    caplog.set_level(logging.INFO, logger="app.performance")
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert "Server-Timing" in response.headers
    assert not any(
        record.name == "app.performance"
        and "path=/api/v1/health/live" in record.getMessage()
        for record in caplog.records
    )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_request_timing.py
```

Expected: FAIL because `Server-Timing` is absent and no `app.performance` log is emitted.

- [ ] **Step 3: Implement request-local SQL counters**

Create `backend/app/core/request_timing.py`:

```python
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter

from fastapi import Request
from sqlalchemy import event

from app.core.db import engine

logger = logging.getLogger("app.performance")
SLOW_REQUEST_MS = 1000.0
LIVENESS_PATH = "/api/v1/health/live"


@dataclass
class TimingState:
    db_ms: float = 0.0
    db_queries: int = 0


_current_timing: ContextVar[TimingState | None] = ContextVar(
    "request_timing", default=None
)


def _before_cursor_execute(conn, _cursor, _statement, _parameters, _context, _many):
    conn.info.setdefault("stockwise_query_started", []).append(perf_counter())


def _after_cursor_execute(conn, _cursor, _statement, _parameters, _context, _many):
    starts = conn.info.get("stockwise_query_started", [])
    if not starts:
        return
    elapsed_ms = (perf_counter() - starts.pop()) * 1000
    state = _current_timing.get()
    if state is not None:
        state.db_ms += elapsed_ms
        state.db_queries += 1


def _handle_error(exception_context):
    starts = exception_context.connection.info.get("stockwise_query_started", [])
    if not starts:
        return
    elapsed_ms = (perf_counter() - starts.pop()) * 1000
    state = _current_timing.get()
    if state is not None:
        state.db_ms += elapsed_ms
        state.db_queries += 1


def install_db_timing() -> None:
    if not event.contains(engine, "before_cursor_execute", _before_cursor_execute):
        event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    if not event.contains(engine, "after_cursor_execute", _after_cursor_execute):
        event.listen(engine, "after_cursor_execute", _after_cursor_execute)
    if not event.contains(engine, "handle_error", _handle_error):
        event.listen(engine, "handle_error", _handle_error)


def _log_request(request: Request, status: int, total_ms: float, state: TimingState) -> None:
    if request.url.path == LIVENESS_PATH:
        return
    log = logger.warning if total_ms >= SLOW_REQUEST_MS else logger.info
    log(
        "method=%s path=%s status=%d total_ms=%.1f db_ms=%.1f db_queries=%d",
        request.method,
        request.url.path,
        status,
        total_ms,
        state.db_ms,
        state.db_queries,
    )


async def request_timing_middleware(request: Request, call_next):
    state = TimingState()
    token = _current_timing.set(state)
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        total_ms = (perf_counter() - started) * 1000
        _log_request(request, 500, total_ms, state)
        raise
    else:
        total_ms = (perf_counter() - started) * 1000
        response.headers["Server-Timing"] = (
            f"app;dur={total_ms:.1f}, db;dur={state.db_ms:.1f}"
        )
        _log_request(request, response.status_code, total_ms, state)
        return response
    finally:
        _current_timing.reset(token)
```

- [ ] **Step 4: Register timing once in the app factory**

In `backend/app/main.py`, import `install_db_timing` and `request_timing_middleware`. In `create_app()`, call `install_db_timing()` after logging configuration, then register timing after the existing login middleware:

```python
configure_sensitive_logging(settings)
install_db_timing()
app = FastAPI(title="stock-ai-advisor", version="0.1.0", lifespan=lifespan)

app.middleware("http")(add_security_headers)
app.middleware("http")(require_login)
app.middleware("http")(request_timing_middleware)
```

- [ ] **Step 5: Run timing, auth, health, and full backend checks**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q --no-cov tests/test_request_timing.py tests/test_auth.py tests/test_health.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests alembic
```

Expected: focused and full suites pass, coverage remains at least 75%, and timing logs contain no query string.

- [ ] **Step 6: Commit timing observability**

```powershell
git add backend/app/core/request_timing.py backend/app/main.py backend/tests/test_request_timing.py
git commit -m "feat: log request and database timing"
```

---

### Task 3: Single-request stock detail page

**Files:**
- Modify: `frontend/lib/types.ts`
- Create: `frontend/hooks/use-dashboard.ts`
- Create: `frontend/hooks/use-dashboard.test.tsx`
- Modify: `frontend/app/stock/[symbol]/page.tsx`
- Create: `frontend/app/stock/[symbol]/page.test.tsx`
- Modify: `frontend/components/analysis/report-card.tsx`
- Modify: `frontend/components/analysis/news-card.tsx`
- Modify: `frontend/hooks/use-analysis.ts`
- Modify: `frontend/hooks/use-news.ts`
- Modify: `frontend/hooks/use-premium.ts`

**Interfaces:**
- Consumes: the Task 1 dashboard response and existing analysis/news mutation hooks.
- Produces: shared `AnalysisData`, `NewsData`, `PredictionData`, `UsageRow`, and `StockDashboard` types plus `useStockDashboard(symbol, range)`.

- [ ] **Step 1: Add failing dashboard-hook tests**

Create `frontend/hooks/use-dashboard.test.tsx`:

```tsx
/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiGet } from "@/lib/api";
import { useMarketStore } from "@/stores/market";
import { DASHBOARD_STALE_MS, useStockDashboard } from "./use-dashboard";

vi.mock("@/lib/api", () => ({ apiGet: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
});

it("loads the complete stock page with one dashboard request", async () => {
  vi.mocked(apiGet).mockResolvedValue({
    stock: { symbol: "2330", market: "TW", name: "TSMC", currency: "TWD", kind: "stock", tracked: true },
    series: [],
    prediction: null,
    analysis: null,
    news: null,
    usage: [],
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  renderHook(() => useStockDashboard("2330", "1y"), { wrapper });

  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
  expect(apiGet).toHaveBeenCalledWith(
    "/stocks/2330/dashboard",
    { range: "1y" },
    "tw",
  );
  const query = queryClient.getQueryCache().find({
    queryKey: ["stock-dashboard", "tw", "2330", "1y"],
  });
  expect(query?.options.staleTime).toBe(DASHBOARD_STALE_MS);
});

it("separates market and range in the dashboard cache key", async () => {
  vi.mocked(apiGet).mockResolvedValue({ stock: {}, series: [], prediction: null, analysis: null, news: null, usage: [] });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  const { rerender } = renderHook(
    ({ range }) => useStockDashboard("AAPL", range),
    { initialProps: { range: "3m" }, wrapper },
  );
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
  rerender({ range: "1y" });
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  expect(queryClient.getQueryData(["stock-dashboard", "tw", "AAPL", "3m"])).toBeDefined();
  expect(queryClient.getQueryData(["stock-dashboard", "tw", "AAPL", "1y"])).toBeDefined();
});
```

Create `frontend/app/stock/[symbol]/page.test.tsx` to prove the aggregate result feeds every section:

```tsx
/** @vitest-environment jsdom */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import StockPage from "./page";

const dashboardMock = vi.fn();
vi.mock("@/hooks/use-dashboard", () => ({
  useStockDashboard: (...args: unknown[]) => dashboardMock(...args),
}));
vi.mock("@/stores/market", () => ({
  useMarketStore: (selector: (state: { market: string }) => unknown) => selector({ market: "tw" }),
}));
vi.mock("@/components/charts/candlestick", () => ({
  CandlestickChart: ({ data }: { data: unknown[] }) => <div>candles:{data.length}</div>,
}));
vi.mock("@/components/charts/technical-indicators", () => ({
  TechnicalIndicatorsChart: ({ data }: { data: unknown[] }) => <div>indicators:{data.length}</div>,
}));
vi.mock("@/components/analysis/report-card", () => ({
  ReportCard: ({ data }: { data: { report: { reasoning: string } } | null }) => <div>analysis:{data?.report.reasoning}</div>,
}));
vi.mock("@/components/analysis/news-card", () => ({
  NewsCard: ({ data }: { data: { summary: string } | null }) => <div>news:{data?.summary}</div>,
}));

beforeEach(() => {
  dashboardMock.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      stock: { symbol: "2330", market: "TW", name: "TSMC", currency: "TWD", kind: "stock", tracked: true },
      series: [{ date: "2026-07-15", close: 100 }],
      prediction: null,
      analysis: { report: { reasoning: "stored analysis" } },
      news: { summary: "stored news" },
      usage: [],
    },
  });
});

it("renders all stock sections from one dashboard result", async () => {
  render(<StockPage params={Promise.resolve({ symbol: "2330" })} />);
  expect(await screen.findByText("TSMC")).toBeInTheDocument();
  expect(screen.getByText("candles:1")).toBeInTheDocument();
  expect(screen.getByText("indicators:1")).toBeInTheDocument();
  expect(screen.getByText("analysis:stored analysis")).toBeInTheDocument();
  expect(screen.getByText("news:stored news")).toBeInTheDocument();
  expect(dashboardMock).toHaveBeenCalledWith("2330", "1y");
});
```

- [ ] **Step 2: Run the hook tests and verify RED**

```powershell
cd frontend
npm.cmd test -- hooks/use-dashboard.test.tsx app/stock/[symbol]/page.test.tsx
```

Expected: FAIL because `use-dashboard.ts` and `DASHBOARD_STALE_MS` do not exist.

- [ ] **Step 3: Define shared dashboard types and hook**

Move the existing analysis, news, prediction, and usage interfaces from their hook files into `frontend/lib/types.ts`; re-export them from the old hook modules to preserve imports. Add:

```typescript
export interface StockDashboard extends PriceSeries {
  prediction: PredictionData | null;
  analysis: AnalysisData | null;
  news: NewsData | null;
  usage: UsageRow[];
}
```

Create `frontend/hooks/use-dashboard.ts`:

```typescript
"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import type { StockDashboard } from "@/lib/types";
import { useMarketStore } from "@/stores/market";

export const DASHBOARD_STALE_MS = 5 * 60_000;

export function useStockDashboard(symbol: string, range: string) {
  const market = useMarketStore((state) => state.market);
  return useQuery({
    queryKey: ["stock-dashboard", market, symbol, range],
    queryFn: () =>
      apiGet<StockDashboard>(
        `/stocks/${symbol}/dashboard`,
        { range },
        market,
      ),
    enabled: Boolean(symbol),
    staleTime: DASHBOARD_STALE_MS,
  });
}
```

- [ ] **Step 4: Convert the page and cards to dashboard data**

In `frontend/app/stock/[symbol]/page.tsx`, replace `usePrices` and `usePredictions` with one `useStockDashboard(symbol, range)` call. Read the chart data from `dashboard.series`, the stock from `dashboard.stock`, and predictions from `dashboard.prediction`. Pass stored sections into the cards:

```tsx
<ReportCard
  symbol={symbol}
  data={dashboard?.analysis ?? null}
  usage={dashboard?.usage ?? []}
  isLoading={isLoading}
/>
<NewsCard
  symbol={symbol}
  data={dashboard?.news ?? null}
  usage={dashboard?.usage ?? []}
  isLoading={isLoading}
/>
```

Change `ReportCard` to accept:

```typescript
interface ReportCardProps {
  symbol: string;
  data: AnalysisData | null;
  usage: UsageRow[];
  isLoading: boolean;
}
```

Remove `useAnalysis()` and `useUsage()` from `ReportCard`; retain `useRunRoutine()` and `useRunDeep()`. Treat `data === null` as the current no-report state.

Change `NewsCard` to accept:

```typescript
interface NewsCardProps {
  symbol: string;
  data: NewsData | null;
  usage: UsageRow[];
  isLoading: boolean;
}
```

Remove `useNews()` and `useUsage()` from `NewsCard`; retain `useRunNews()`. Treat `data === null` as the current no-news state.

- [ ] **Step 5: Run dashboard and existing frontend tests**

```powershell
cd frontend
npm.cmd test -- hooks/use-dashboard.test.tsx app/stock/[symbol]/page.test.tsx hooks/use-stocks.test.tsx lib/api.test.ts
npm.cmd run lint
```

Expected: selected tests and lint pass; the stock page imports no separate initial price, prediction, analysis, news, or usage query hook.

- [ ] **Step 6: Commit the one-request frontend**

```powershell
git add frontend/lib/types.ts frontend/hooks/use-dashboard.ts frontend/hooks/use-dashboard.test.tsx frontend/app/stock/[symbol]/page.tsx frontend/app/stock/[symbol]/page.test.tsx frontend/components/analysis/report-card.tsx frontend/components/analysis/news-card.tsx frontend/hooks/use-analysis.ts frontend/hooks/use-news.ts frontend/hooks/use-premium.ts
git commit -m "feat: load stock details from dashboard"
```

---

### Task 4: Query-specific caching and mutation invalidation

**Files:**
- Create: `frontend/lib/query-policy.ts`
- Modify: `frontend/hooks/use-stocks.ts`
- Modify: `frontend/hooks/use-analysis.ts`
- Modify: `frontend/hooks/use-news.ts`
- Modify: `frontend/hooks/use-premium.ts`
- Modify: `frontend/components/job-center.tsx`
- Modify: `frontend/hooks/use-stocks.test.tsx`
- Modify: `frontend/components/job-center.test.tsx`
- Create: `frontend/hooks/use-cache-policy.test.tsx`
- Create: `frontend/hooks/use-report-mutations.test.tsx`

**Interfaces:**
- Consumes: Task 3 `StockDashboard`, existing mutation results, and active stock-sync job names `sync-{market}-{symbol}`.
- Produces: exact stale-time constants and immediate cache updates/invalidations after mutations or completed stock synchronization.

- [ ] **Step 1: Write failing cache-policy and mutation tests**

Create `frontend/hooks/use-cache-policy.test.tsx` using the existing QueryClient wrapper pattern. Render `useWatchlist`, `useAnalysis`, `useNews`, `usePredictions`, and `useUsage`; assert these option values:

```typescript
expect(watchlistQuery?.options.staleTime).toBe(10 * 60_000);
expect(analysisQuery?.options.staleTime).toBe(10 * 60_000);
expect(newsQuery?.options.staleTime).toBe(10 * 60_000);
expect(predictionQuery?.options.staleTime).toBe(5 * 60_000);
expect(usageQuery?.options.staleTime).toBe(60_000);
```

Extend `frontend/hooks/use-stocks.test.tsx` so `usePrices` is asserted at five minutes and watchlist is asserted at ten minutes.

Extend `frontend/components/job-center.test.tsx` with a QueryClientProvider, return a succeeded run for `sync-tw-2330`, and assert:

```typescript
expect(invalidateQueries).toHaveBeenCalledWith({
  queryKey: ["stock-dashboard", "tw", "2330"],
});
expect(invalidateQueries).toHaveBeenCalledWith({
  queryKey: ["prices", "tw", "2330"],
});
```

Create `frontend/hooks/use-report-mutations.test.tsx`. Mock `apiRequest`, `waitForJob`, `trackActiveJob`, and `removeActiveJob`, then use this shared setup:

```tsx
/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { apiRequest, waitForJob } from "@/lib/api";
import type { AnalysisData, NewsData, StockDashboard } from "@/lib/types";
import { useMarketStore } from "@/stores/market";
import { useRunRoutine } from "./use-analysis";
import { useRunNews } from "./use-news";

vi.mock("@/lib/api", () => ({
  apiRequest: vi.fn(),
  waitForJob: vi.fn(),
  trackActiveJob: vi.fn(),
  removeActiveJob: vi.fn(),
}));

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  const base = {
    stock: { symbol: "2330", market: "TW", name: "TSMC", currency: "TWD", kind: "stock", tracked: true },
    series: [], prediction: null, analysis: null, news: null, usage: [],
  } as StockDashboard;
  queryClient.setQueryData(["stock-dashboard", "tw", "2330", "3m"], base);
  queryClient.setQueryData(["stock-dashboard", "tw", "2330", "1y"], base);
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
  return { queryClient, wrapper };
}

beforeEach(() => {
  vi.clearAllMocks();
  useMarketStore.setState({ market: "tw" });
});
```

Add the two concrete mutation cases:

```tsx
it("updates every cached dashboard range after routine analysis", async () => {
  const analysis = { trade_date: "2026-07-15", kind: "routine", model: "flash", report: {} } as AnalysisData;
  vi.mocked(apiRequest).mockResolvedValue(analysis);
  const { queryClient, wrapper } = setup();
  const { result } = renderHook(() => useRunRoutine("2330"), { wrapper });
  await act(async () => { await result.current.mutateAsync(); });
  expect(queryClient.getQueryData<StockDashboard>(["stock-dashboard", "tw", "2330", "3m"])?.analysis).toEqual(analysis);
  expect(queryClient.getQueryData<StockDashboard>(["stock-dashboard", "tw", "2330", "1y"])?.analysis).toEqual(analysis);
});

it("updates every cached dashboard range after news research", async () => {
  const news = { date: "2026-07-15", model: "antigravity", summary: "stored", created_at: null } as NewsData;
  vi.mocked(apiRequest).mockResolvedValue({ started: true, job: "news-tw-2330", run_id: 8 });
  vi.mocked(waitForJob).mockResolvedValue(news);
  const { queryClient, wrapper } = setup();
  const { result } = renderHook(() => useRunNews("2330"), { wrapper });
  await act(async () => { await result.current.mutateAsync(); });
  expect(queryClient.getQueryData<StockDashboard>(["stock-dashboard", "tw", "2330", "3m"])?.news).toEqual(news);
  expect(queryClient.getQueryData<StockDashboard>(["stock-dashboard", "tw", "2330", "1y"])?.news).toEqual(news);
});
```

- [ ] **Step 2: Run new frontend tests and verify RED**

```powershell
cd frontend
npm.cmd test -- hooks/use-cache-policy.test.tsx hooks/use-report-mutations.test.tsx hooks/use-stocks.test.tsx components/job-center.test.tsx
```

Expected: FAIL because query-specific stale times and dashboard invalidation do not exist.

- [ ] **Step 3: Add exact cache constants**

Create `frontend/lib/query-policy.ts`:

```typescript
export const PRICE_STALE_MS = 5 * 60_000;
export const DASHBOARD_STALE_MS = PRICE_STALE_MS;
export const WATCHLIST_STALE_MS = 10 * 60_000;
export const STORED_REPORT_STALE_MS = 10 * 60_000;
export const USAGE_STALE_MS = 60_000;
```

Import `DASHBOARD_STALE_MS` into `use-dashboard.ts` and re-export it there. Apply:

- `PRICE_STALE_MS` to `usePrices` and `usePredictions`.
- `WATCHLIST_STALE_MS` to `useWatchlist`.
- `STORED_REPORT_STALE_MS` to `useAnalysis` and `useNews`.
- `USAGE_STALE_MS` to `useUsage`.

- [ ] **Step 4: Update dashboard caches after analysis and news mutations**

In `use-analysis.ts` success handling, preserve the existing analysis query update and add:

```typescript
qc.setQueriesData<StockDashboard>(
  { queryKey: ["stock-dashboard", market, symbol] },
  (current) => current ? { ...current, analysis: data } : current,
);
```

In `use-news.ts`, add the equivalent update with `{ ...current, news: data }`. Both continue invalidating `usage`.

- [ ] **Step 5: Invalidate price data when stock sync succeeds**

In `frontend/components/job-center.tsx`, call `useQueryClient()`. When a run succeeds, parse only the exact stock-sync name shape:

```typescript
const match = /^sync-(tw|us)-(.+)$/.exec(job.name);
if (match) {
  const [, market, symbol] = match;
  void queryClient.invalidateQueries({
    queryKey: ["stock-dashboard", market, symbol],
  });
  void queryClient.invalidateQueries({
    queryKey: ["prices", market, symbol],
  });
}
removeActiveJob(job.runId);
```

Do not invalidate dashboard data for unrelated job names.

- [ ] **Step 6: Run focused and full frontend verification**

```powershell
cd frontend
npm.cmd test -- hooks/use-cache-policy.test.tsx hooks/use-report-mutations.test.tsx hooks/use-stocks.test.tsx hooks/use-dashboard.test.tsx components/job-center.test.tsx
npm.cmd run test:coverage
npm.cmd run lint
npm.cmd run build
```

Expected: all tests pass, every configured coverage threshold passes, lint is clean, and the production build succeeds.

- [ ] **Step 7: Commit caching and invalidation**

```powershell
git add frontend/lib/query-policy.ts frontend/hooks/use-stocks.ts frontend/hooks/use-analysis.ts frontend/hooks/use-news.ts frontend/hooks/use-premium.ts frontend/components/job-center.tsx frontend/hooks/use-stocks.test.tsx frontend/components/job-center.test.tsx frontend/hooks/use-cache-policy.test.tsx frontend/hooks/use-report-mutations.test.tsx frontend/hooks/use-dashboard.ts
git commit -m "perf: cache dashboard reads and refresh mutations"
```

---

### Task 5: Operator documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/PLAN.md`

**Interfaces:**
- Consumes: completed Tasks 1–4.
- Produces: deployment/operator guidance for cache freshness, dashboard aggregation, `Server-Timing`, and performance logs.

- [ ] **Step 1: Document the behavior**

Add a README performance section stating:

```markdown
- 股票詳情首次載入透過單一 dashboard API 取得價格、預測、既有分析、新聞與 AI 額度；讀取既有報告不會重新呼叫 AI。
- 前端記憶體快取：dashboard／價格 5 分鐘，自選股／既有分析／新聞 10 分鐘；新增、同步或手動更新後會立即刷新相關資料。
- API 回應提供 `Server-Timing`，後端日誌記錄 `total_ms`、`db_ms` 與 `db_queries`；日誌不包含 query string、SQL 或憑證。
```

Mark the corresponding Phase 1 performance work complete in `docs/PLAN.md`. Do not claim that this removes Render Free cold starts.

- [ ] **Step 2: Run complete backend verification**

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check app tests alembic
```

Expected: all backend tests pass, total coverage remains at least 75%, and Ruff reports no errors.

- [ ] **Step 3: Run complete frontend verification**

```powershell
cd frontend
npm.cmd run test:coverage
npm.cmd run lint
npm.cmd run build
```

Expected: all frontend tests and coverage thresholds pass, lint is clean, and the production build exits successfully.

- [ ] **Step 4: Verify diff, security, and endpoint wiring**

From the repository root:

```powershell
git diff --check
$diff = git diff main...HEAD --no-ext-diff
$highRisk = [regex]::Matches(($diff -join "`n"), '(?i)(AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)')
if ($highRisk.Count -ne 0) { throw "credential-shaped value found" }
rg -n "dashboard|Server-Timing|db_queries|DASHBOARD_STALE_MS" backend/app frontend
git status --short --branch
```

Expected: no whitespace errors, no credential-shaped values, endpoint and cache wiring are present, and only intended files are modified.

- [ ] **Step 5: Commit documentation**

```powershell
git add README.md docs/PLAN.md
git commit -m "docs: describe dashboard performance telemetry"
git status --short --branch
```

Expected: the feature branch is clean. Do not push unless explicitly requested.
