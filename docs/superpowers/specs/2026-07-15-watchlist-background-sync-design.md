# Watchlist Background Price Sync Design

## Problem

Adding a new watchlist symbol currently waits for the initial 400-day price sync and indicator rebuild before returning. The price persistence loop performs one `db.get` per price row inside an async request. In production, syncing 267 rows blocked the single Uvicorn event loop for roughly 50 seconds, prevented even `/health/live` from responding within Render's five-second deadline, caused instance restarts, and exceeded the frontend's 30-second timeout.

The same production log also exposed the configured FinMind token because HTTPX supplied the request URL to logging as a non-string argument that the current secret filter does not redact.

## Goals

- Return a successful watchlist-add response immediately after the symbol is persisted and a durable sync job is queued.
- Keep the watchlist item even when its price sync fails.
- Track and retry the sync through the existing database-backed job system.
- Keep the Uvicorn event loop responsive while remote requests, database persistence, and indicator calculation run.
- Replace per-row price lookups with one batch query.
- Prevent configured secrets from appearing in logs even when logging arguments are URL-like objects.

## Non-goals

- Replacing SQLAlchemy with its async engine.
- Changing market-data providers or their retry policies.
- Redesigning the existing job-center UI.
- Adding a second Render process or paid instance.
- Changing scheduled full-market synchronization behavior beyond adopting the non-blocking sync primitive.

## Architecture

### Watchlist API

`POST /api/v1/watchlist` continues to validate or discover the requested stock and persist the watchlist item. It no longer awaits price synchronization. After persistence, it enqueues a durable job with:

- `job_type`: `stock_sync`
- `name`: `sync-{market.lower()}-{symbol.lower()}`
- `payload`: `{ "market": market, "symbol": symbol }`
- active idempotency key: `stock-sync:{market}:{symbol}`
- maximum attempts: 3

The response includes the existing stock fields plus `started`, `job`, and `run_id`. Re-adding a symbol never creates a duplicate watchlist row. If an active sync job already exists, the endpoint returns that job's ID; otherwise it creates a fresh job so an existing symbol can be refreshed intentionally.

Watchlist persistence remains authoritative. A later sync failure changes only the `JobRun` status and does not delete or roll back the watchlist item.

### Frontend tracking

The add-watch mutation consumes the returned `run_id`, registers it with the existing `trackActiveJob` mechanism, and invalidates the watchlist query immediately. No new job UI is introduced. Existing job polling, failure display, and retry behavior are reused.

### Job dispatch

The existing worker dispatcher gains a `stock_sync` branch. It resolves the stock by `(market, symbol)`, calls the shared price-sync primitive, and returns a small result containing the market, symbol, and number of changed rows. Missing stocks fail with `NotFoundError`; provider and persistence failures use the existing job retry and terminal-failure behavior.

### Non-blocking price synchronization

The shared sync interface accepts scalar identity values instead of a live ORM object crossing thread boundaries:

```python
async def sync_prices(stock_id: int, market: str, symbol: str) -> int:
    ...
```

Its phases are:

1. Load the latest stored date in a worker thread using a short-lived `SessionLocal` session.
2. Await the market provider request normally on the event loop.
3. Persist returned rows and rebuild indicators in a worker thread using a separate short-lived `SessionLocal` session.

No SQLAlchemy session or ORM instance is shared between the event-loop thread and worker thread. Existing scheduler and stock API callers adopt the scalar interface.

### Batch persistence

The persistence phase extracts all returned dates and loads matching `DailyPrice` rows in one query. It builds a date-to-row map, applies inserts or updates in memory, flushes once when changes exist, recalculates indicators, and commits once.

Indicator rebuilding remains functionally unchanged and runs in the worker thread. This design removes the production N+1 round trips while keeping current indicator results and transaction boundaries.

### Secret redaction

The logging filter continues to preserve native numeric and unrelated argument types. For each logging argument, it also examines the string representation; when that representation contains a configured secret, the argument is replaced with its redacted string form. This covers `httpx.URL` without breaking `%d` and other typed logging placeholders.

The production FinMind token exposed in the supplied log must be revoked and replaced outside the code deployment. Code changes prevent recurrence but cannot invalidate the leaked credential.

## Error handling

- Stock discovery failure: return the existing 404/upstream response and do not add the watchlist item.
- Duplicate watchlist symbol: keep the existing row and return the active or newly queued sync job.
- Job enqueue failure after watchlist persistence: log the failure and return a successful watchlist response with no `run_id`, so a queue outage does not turn a successful add into an ambiguous client retry. The next scheduled market sync remains a fallback.
- Price-provider or persistence failure: retry through `JobRun`; preserve the watchlist item and expose terminal failure through the job center.
- Process restart: queued jobs remain durable; running jobs are recovered after their lease expires according to the existing worker policy.

## Testing

- API test: adding a watchlist item does not call `sync_prices`, returns promptly with a job ID, and remains idempotent.
- API test: enqueue failure still returns the persisted watchlist item without a job ID.
- Dispatcher test: `stock_sync` resolves the stock and reports changed rows.
- Sync-service test: persistence uses one existing-price query rather than one query per input row.
- Responsiveness test: blocking persistence work runs on a worker thread while the event loop continues to advance.
- Regression tests: scheduled sync and stock API callers use the scalar sync interface.
- Frontend test: successful add registers the returned job and refreshes the watchlist.
- Logging test: a URL-like logging argument containing the configured FinMind token is redacted.
- Full backend and frontend test, lint, and build suites remain green.

## Deployment and verification

After deployment, adding a new symbol should produce an immediate `POST /api/v1/watchlist` success followed by an independently tracked `stock_sync` job. Render `/health/live` requests should continue returning during price persistence, and no FinMind token should appear in HTTPX request logs. Render events and job history provide production verification.
