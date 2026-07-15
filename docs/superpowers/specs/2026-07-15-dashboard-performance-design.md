# Dashboard Loading Performance Design

## Goal

Reduce repeated loading delays when navigating between the watchlist and stock-detail pages, while preserving current data correctness and making backend latency diagnosable in production.

The change combines three improvements:

1. Longer, mutation-aware client caching.
2. One aggregate stock dashboard endpoint instead of approximately five initial requests.
3. Per-request application and database timing in logs and `Server-Timing` response headers.

## Current Behavior and Root Cause

- The global TanStack Query default marks cached queries stale after 60 seconds.
- A stock-detail page independently requests prices, predictions, the latest analysis, the latest news report, and AI usage. The browser therefore makes several Vercel-to-Render round trips on a cold page visit.
- Existing analysis and news reads do not invoke an AI model, but each still requires a backend and Neon round trip.
- `GET /watchlist` is already one joined query, so slow warm responses cannot be attributed to an N+1 query in that endpoint.
- Existing logs do not separate total application time from SQL execution time, making Render wake-up, network latency, Neon latency, and slow SQL difficult to distinguish.

Render cold starts remain outside the scope of an application-only fix. The design reduces warm-navigation cost and exposes evidence that distinguishes infrastructure delay from application or database delay.

## Selected Approach

Use a backward-compatible aggregate endpoint plus query-specific client caching and request timing. Existing detail endpoints remain available for manual actions and compatibility. No Redis, new queue, new database table, or public browser cache is introduced.

## Backend Architecture

### Stock dashboard service

Create a focused dashboard service that owns read-only composition for one stock. It receives a SQLAlchemy `Session`, market, symbol, and range, and returns a plain dictionary suitable for the API envelope.

The service will:

1. Resolve the stock once by `(market, symbol)`.
2. Load the selected price range and indicators.
3. Load or calculate the cached prediction through the existing prediction service.
4. Load the current stored analysis without running Gemini.
5. Load the current stored news report without running Antigravity.
6. Read current AI quota usage through the existing quota reporting function.

The service must not call an AI provider or market-data provider. It uses one request-scoped session and returns no ORM objects.

### API contract

Add:

```text
GET /api/v1/stocks/{symbol}/dashboard?market=TW&range=1y
```

Supported ranges remain `3m`, `6m`, and `1y`.

Successful data shape:

```json
{
  "stock": {
    "symbol": "2330",
    "market": "TW",
    "name": "台積電",
    "currency": "TWD",
    "kind": "stock"
  },
  "series": [],
  "prediction": null,
  "analysis": null,
  "news": null,
  "usage": []
}
```

`stock` and `series` are required. `prediction`, `analysis`, and `news` are nullable so one unavailable optional section does not fail the entire page. `usage` is always an array.

If the stock does not exist, return the existing 404 envelope. Unexpected database or serialization failures keep the existing global error behavior.

### Reuse and compatibility

Extract or reuse pure DTO/read helpers instead of calling FastAPI endpoint functions from the dashboard service. Existing endpoints remain unchanged:

- `/stocks/{symbol}/prices`
- `/stocks/{symbol}/predictions`
- `/stocks/{symbol}/analysis`
- `/stocks/{symbol}/news`
- `/usage`

Manual analysis and news mutations continue to use their current endpoints.

## Frontend Data Flow

Add `useStockDashboard(symbol, range)` with query key:

```text
["stock-dashboard", market, symbol, range]
```

The stock-detail page uses this query as its only initial data request. Charts, prediction display, analysis card, news card, and quota controls receive data from the dashboard result. Existing mutation hooks remain responsible for starting routine analysis, deep analysis, and news jobs.

The UI keeps independent section states:

- Missing analysis or news displays the current empty-state message.
- A failed dashboard request displays the existing page-level API error.
- Manual analysis or news failures remain local to their respective cards.

## Cache Policy

Use TanStack Query memory caching only. Do not add shared HTTP caching because responses contain authenticated, user-specific data.

Exact stale times:

| Query | Stale time |
|---|---:|
| Stock dashboard and price series | 5 minutes |
| Watchlist | 10 minutes |
| Stored analysis | 10 minutes |
| Stored news | 10 minutes |
| AI usage | 1 minute |

Mutation invalidation rules:

- Add, remove, reorder, regroup, or change AI management: invalidate the selected market watchlist.
- Add a stock and finish its `stock_sync` job: invalidate its dashboard and price queries.
- Complete routine or deep analysis: update or invalidate the stock dashboard and analysis queries.
- Complete news research: update or invalidate the stock dashboard and news queries.
- Market changes use distinct query keys and cannot reuse another market's data.

Existing cached data remains visible while a stale query refreshes in the background. A hard browser reload still requires a backend request because no persistent client cache is introduced.

## Request and Database Timing

### Request timing middleware

Add one FastAPI middleware that measures application processing time with a monotonic clock. For every non-liveness API response, log structured fields:

```text
method=GET path=/api/v1/watchlist status=200 total_ms=42 db_ms=11 db_queries=1
```

Rules:

- Log the URL path only, never the query string.
- Do not log request or response bodies.
- Do not log headers, cookies, API keys, or tokens.
- `/api/v1/health/live` is excluded from normal INFO logs to avoid health-check noise.
- Requests taking at least 1000 ms are logged at WARNING; other requests use INFO.
- Exceptions are logged through the existing secret-redacting logging configuration.

### SQL timing

Use SQLAlchemy engine events around cursor execution. Store request-local counters in a `ContextVar` timing state:

- cumulative SQL execution milliseconds;
- SQL query count.

Event handlers do not log SQL statements or parameters. Work outside an HTTP request has no timing state and incurs only a constant-time context lookup.

### Browser-visible timing

Successful and handled-error responses include:

```text
Server-Timing: app;dur=42.1, db;dur=11.0
```

This allows browser Network tools to distinguish application and SQL time from proxy, network, and Render wake-up time. The header is diagnostic only and contains no private values.

## Performance Expectations

- Warm navigation back to the watchlist within 10 minutes should render from cache without waiting for a network response.
- Warm navigation back to the same stock/range within 5 minutes should render from cache.
- A cold stock-detail visit should make one initial dashboard request instead of the current multiple detail requests.
- Manual mutations must show fresh data after completion through explicit cache updates or invalidation.
- Timing instrumentation should add no extra SQL query and negligible processing overhead.

## Testing

Backend tests will verify:

- dashboard success with all sections;
- nullable analysis, news, and prediction sections;
- stock-not-found behavior;
- the dashboard read path never invokes AI or market providers;
- timing logs contain method, path, status, total time, DB time, and query count;
- `Server-Timing` is returned;
- query strings and synthetic secrets are absent from timing logs;
- liveness requests do not emit normal timing logs.

Frontend tests will verify:

- the stock-detail page performs one dashboard query for initial data;
- the exact 5-minute and 10-minute stale times;
- dashboard data populates charts, prediction, analysis, news, and usage sections;
- relevant mutations invalidate or update dashboard queries;
- market and range remain part of cache identity.

Full backend pytest and Ruff, plus frontend coverage, lint, and production build, remain required before completion.

## Non-Goals

- Eliminating Render Free cold starts.
- Adding Redis or a shared server-side response cache.
- Persisting TanStack Query data across browser reloads.
- Changing AI models, quotas, prompts, retries, or background-job behavior.
- Removing existing detail endpoints.

## Rollout and Observability

The aggregate endpoint and frontend switch ship together. Existing endpoints provide a rollback path for the frontend without a database migration. After deployment, compare browser request duration with `Server-Timing` and backend timing logs before deciding whether a paid Render instance, region change, or SQL-specific optimization is warranted.
