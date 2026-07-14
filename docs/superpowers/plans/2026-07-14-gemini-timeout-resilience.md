# Gemini Timeout Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduled Gemini analysis tolerate transient timeout and 503 failures and expose enough Render diagnostics to identify the failing model and request.

**Architecture:** Keep retry policy inside `GeminiProvider._call_api`, with a separate quota reservation for every sent attempt. Keep routing unchanged except for readable fallback logs, and make the per-stock batch size an explicit service constant.

**Tech Stack:** Python 3.12, HTTPX, FastAPI, SQLAlchemy, pytest, pytest-asyncio, Ruff.

## Global Constraints

- Routine batches contain at most four stocks.
- Gemini read timeout is 300 seconds.
- Timeout and HTTP 503 receive at most two retries with exponential backoff and jitter.
- HTTP 4xx responses are not retried.
- Every sent attempt is finalized in the local quota ledger.
- No new runtime dependency is introduced.

---

### Task 1: Batch size regression

**Files:**
- Modify: `backend/app/services/analysis_service.py`
- Test: `backend/tests/test_analysis_race.py`

**Interfaces:**
- Produces: `AI_ANALYSIS_BATCH_SIZE: int = 4`, consumed by `run_batch`.

- [ ] Write a test that passes five pending stocks and asserts AI calls contain four and one contexts.
- [ ] Run the test and confirm it fails with the existing eight-stock batch.
- [ ] Add `AI_ANALYSIS_BATCH_SIZE = 4` and use it for both the range step and slice size.
- [ ] Run the test and confirm it passes.

### Task 2: Gemini transient retry policy

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/test_ai_timeout_routing.py`
- Test: `backend/tests/test_db_pool.py`

**Interfaces:**
- Produces settings `gemini_read_timeout_seconds=300` and `gemini_max_retries=2`.
- Produces retry behavior from `GeminiProvider._call_api(prompt, output_model) -> str`.

- [ ] Write tests for the 300-second read timeout, timeout-then-success, 503-then-success, three-attempt exhaustion, 1/2-second exponential delays with bounded jitter, and per-attempt quota finalization.
- [ ] Run the tests and confirm failures are caused by missing retry/configuration behavior.
- [ ] Add the settings and implement the minimal retry loop with `asyncio.sleep` and `random.uniform`.
- [ ] Run the targeted tests and confirm they pass.

### Task 3: Render diagnostic logs

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/app/providers/ai/router.py`
- Test: `backend/tests/test_ai_timeout_routing.py`

**Interfaces:**
- Produces logs containing `model`, `attempt`, `prompt_chars`, `elapsed_ms`, and `status`.
- Produces router warnings containing the failed model and exception message.

- [ ] Write log-capture tests for a timed-out request and a primary-model fallback.
- [ ] Run the tests and confirm the required fields are absent.
- [ ] Add structured key-value log messages without logging prompts or API keys.
- [ ] Run the targeted tests and confirm they pass.

### Task 4: Full verification

**Files:**
- Review all modified production, test, and documentation files.

- [ ] Run `python -m pytest` from `backend` and confirm zero failures with coverage at or above 75%.
- [ ] Run `python -m ruff check app tests` from `backend` and confirm zero errors.
- [ ] Review `git diff --check` and `git diff` for accidental changes or secret exposure.
