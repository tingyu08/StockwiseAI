# Gemini Timeout Resilience Design

## Goal

Prevent daily AI analysis jobs from failing after a single slow response from both the primary and fallback Gemini models, while making the exact failure mode visible in Render logs.

## Approved behavior

- Analyze at most four stocks in each routine batch instead of eight.
- Use an HTTPX timeout with a 300-second read timeout and shorter connect, write, and pool timeouts.
- Retry transient Gemini failures up to two additional times, for three total attempts.
- Retry only HTTP timeouts and HTTP 503 responses. Do not retry 4xx responses, including 429.
- Wait with exponential backoff of 1 and 2 seconds plus bounded random jitter before retries.
- Record every sent attempt in the application quota ledger, including timed-out and HTTP 503 attempts.
- Log model, attempt number, prompt character count, elapsed milliseconds, and HTTP status or failure category.
- Log the primary model failure clearly before routing to the fallback model.

## Design

`GeminiProvider._call_api` remains responsible for one logical model invocation. Internally it performs up to three provider attempts. Each attempt reserves quota immediately before the request and finalizes that reservation after a response or timeout, preserving the existing audit rule that sent attempts count toward local usage.

The retry loop uses `asyncio.sleep` and `random.uniform` so tests can replace both without waiting. Timeout and response handling remain centralized in `gemini.py`; routing behavior stays in `router.py`. The stock batch size is a named constant in `analysis_service.py` so the operational limit is explicit.

## Error handling

- Timeout before the final attempt: finalize usage, log `status=timeout`, wait, retry.
- HTTP 503 before the final attempt: finalize usage, log `status=503`, wait, retry.
- Final timeout or 503: raise `UpstreamError` containing the model and attempt count, allowing the router to fall back.
- HTTP 429 or any other non-200 response: finalize usage and fail immediately.
- HTTP transport errors other than timeout: finalize usage and fail immediately.
- Successful response: finalize usage with provider token metadata and parse it as before.

## Verification

Tests cover the four-stock batch boundary, timeout configuration, timeout recovery, 503 recovery, retry exhaustion, backoff shape, per-attempt quota accounting, diagnostic logging, and readable router fallback logging. The complete backend test suite and Ruff run after the targeted tests pass.
