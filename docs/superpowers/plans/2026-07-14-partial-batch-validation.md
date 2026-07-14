# Partial Batch Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate and repair stock reports independently so one bad model result cannot fail a complete daily batch.

**Architecture:** `AnalysisReport` owns action-aware business validation. `GeminiProvider.analyze_batch` uses a dedicated batch path that decodes the envelope, validates each report, repairs only invalid symbols once, merges valid repairs by expected symbol order, and skips remaining invalid reports.

**Tech Stack:** Python 3.12, Pydantic 2, HTTPX, pytest.

## Global Constraints

- Never synthesize or clamp model-generated price values in application code.
- Preserve native Gemini `responseSchema`, HTTP retries, quota accounting, and generic structured generation behavior.
- Never log complete raw model output or prompts.

---

### Task 1: Make stop-loss validation action-aware

**Files:**
- Modify: `backend/app/providers/ai/schemas.py`
- Modify: `backend/app/providers/ai/gemini.py`
- Test: `backend/tests/test_ai_schema.py`

- [ ] Write failing tests showing hold/sell accept `stop_loss >= target_price_low`, buy rejects it, and the prompt states the buy-only rule.
- [ ] Run focused tests with `--no-cov` and confirm failure against the unconditional validator/prompt.
- [ ] Implement the conditional validator and matching prompt text.
- [ ] Re-run focused tests and confirm they pass.

### Task 2: Validate and repair batch reports independently

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Test: `backend/tests/test_ai_schema.py`

- [ ] Write a failing mixed-batch test where one invalid buy report causes a repair call containing only that symbol/context, then merges the corrected report in original order.
- [ ] Write a failing test where the targeted repair remains invalid and only that symbol is omitted and logged.
- [ ] Implement outer-envelope decoding, per-report validation, targeted repair prompting, merge-by-symbol, and skip logging.
- [ ] Preserve the existing whole-output repair for unusable outer JSON.
- [ ] Run all AI schema/routing tests and confirm they pass.

### Task 3: Correct error wording and documentation

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `docs/PLAN.md`
- Test: `backend/tests/test_ai_schema.py`

- [ ] Add a failing assertion for structure/business-rule error wording.
- [ ] Replace misleading `連續輸出無效 JSON` wording.
- [ ] Document action-aware validation and partial batch salvage.

### Task 4: Verify and commit

**Files:**
- Verify all modified source, test, spec, plan, and documentation files.

- [ ] Run `backend/.venv/Scripts/python.exe -m pytest -q` from `backend`.
- [ ] Run `backend/.venv/Scripts/ruff.exe check app tests alembic` from `backend`.
- [ ] Run `git diff --check` and review the final diff for raw-output logging or secrets.
- [ ] Commit with `fix: salvage valid AI batch reports`.
