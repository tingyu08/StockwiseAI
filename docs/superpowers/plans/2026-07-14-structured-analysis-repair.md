# Structured Analysis Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Gemma from AI routing and make one validation retry repair the actual invalid structured response.

**Architecture:** All configured Google models use native Gemini structured output. Pydantic remains authoritative; after the first validation error, `_generate` constructs a repair prompt from the original task, prior response, and serialized validation errors, then performs exactly one more structured request.

**Tech Stack:** Python 3.12, FastAPI service layer, Pydantic 2, HTTPX, pytest.

## Global Constraints

- Routine analysis has no fallback model.
- Preserve the existing HTTP timeout, retry, quota, and logging behavior.
- Never log the raw model response or complete repair prompt.
- Keep exactly two validation attempts: initial generation plus one informed repair.

---

### Task 1: Remove Gemma from routing

**Files:**
- Modify: `backend/app/providers/ai/router.py`
- Modify: `backend/app/core/quotas.yaml`
- Modify: `backend/tests/test_ai_timeout_routing.py`
- Modify: `backend/tests/test_health.py`

**Interfaces:**
- Consumes: `GeminiProvider(model: str, db: Session)`.
- Produces: `ROUTINE_CHAIN` containing only `gemini-3.1-flash-lite`; premium routing remains `gemini-3.5-flash` then routine.

- [ ] **Step 1: Replace the fallback-success test with a failing single-model exhaustion test**

Assert that `router.ROUTINE_CHAIN` contains no Gemma entry, that one provider call is made, and that `analyze_batch` raises `UpstreamError("所有例行分析模型皆不可用")` after Flash-Lite fails. Update the usage API test to assert Gemma is absent.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest backend/tests/test_ai_timeout_routing.py -k "routine_chain or single_model" -v && pytest backend/tests/test_health.py -k "usage_lists" -v`

Expected: FAIL because Gemma remains in `ROUTINE_CHAIN` and the fake provider succeeds on the second call.

- [ ] **Step 3: Apply the minimal routing change**

Set:

```python
ROUTINE_CHAIN = [("gemini-3.1-flash-lite", True)]
```

Remove Gemma from `quotas.yaml` and the AI usage isolation fixture, and update the module documentation so it no longer describes a Gemma fallback.

- [ ] **Step 4: Run the focused routing tests and verify GREEN**

Run: `pytest backend/tests/test_ai_timeout_routing.py -k "router or routing or routine_chain or single_model" -v && pytest backend/tests/test_health.py -k "usage_lists" -v`

Expected: PASS.

### Task 2: Add explicit semantic prompt rules

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/tests/test_ai_schema.py`

**Interfaces:**
- Consumes: existing `SYSTEM_PROMPT`.
- Produces: prompt contract containing `0 < stop_loss < target_price_low <= target_price_high` and probability tolerance `0.98` through `1.02`.

- [ ] **Step 1: Write a failing prompt-contract test**

Import `SYSTEM_PROMPT` and assert the exact price invariant and probability tolerance are present.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `pytest backend/tests/test_ai_schema.py -k "system_prompt" -v`

Expected: FAIL because the price relationship and exact tolerance are not currently stated.

- [ ] **Step 3: Add the minimal prompt instructions**

Add explicit rules requiring:

```text
0 < stop_loss < target_price_low <= target_price_high
bull/base/bear probability total must be between 0.98 and 1.02
```

- [ ] **Step 4: Run the prompt test and verify GREEN**

Run: `pytest backend/tests/test_ai_schema.py -k "system_prompt" -v`

Expected: PASS.

### Task 3: Repair validation failures with error context

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/tests/test_ai_schema.py`

**Interfaces:**
- Consumes: `_call_api(prompt: str, output_model: type[BaseModel]) -> str` and Pydantic `ValidationError`.
- Produces: `_repair_prompt(original_prompt: str, raw: str, error: ValidationError) -> str` and informed second request behavior in `_generate`.

- [ ] **Step 1: Write three failing behavioral tests**

Cover:

1. A valid first response calls `_call_api` once.
2. An invalid `stop_loss` response triggers a second call whose prompt contains the prior JSON, `stop_loss`, and `stop_loss must be below target_price_low`, then returns the corrected report.
3. Two invalid responses raise `UpstreamError` after exactly two calls.

- [ ] **Step 2: Run the repair tests and verify RED**

Run: `pytest backend/tests/test_ai_schema.py -k "repair or valid_first or two_invalid" -v`

Expected: the repair-context assertion fails because `_generate` currently repeats the original prompt unchanged.

- [ ] **Step 3: Implement minimal repair prompting**

After the first `ValidationError`, construct the next prompt using `exc.errors(include_url=False, include_input=False)` serialized with `json.dumps(..., ensure_ascii=False)`, include the original prompt and prior raw output in delimited blocks, and instruct the model to return corrected JSON only. Do not construct another repair prompt after the final failed attempt.

- [ ] **Step 4: Run schema tests and verify GREEN**

Run: `pytest backend/tests/test_ai_schema.py -v`

Expected: PASS.

### Task 4: Remove obsolete Gemma request branching

**Files:**
- Modify: `backend/app/providers/ai/gemini.py`
- Modify: `backend/app/providers/ai/router.py`
- Modify: `backend/tests/test_ai_timeout_routing.py`

**Interfaces:**
- Consumes: every configured route now uses structured-output Gemini models.
- Produces: `GeminiProvider(model, db)` always sends `responseMimeType`, `responseSchema`, and `systemInstruction`.

- [ ] **Step 1: Write a failing request-shape test**

Capture the outgoing HTTP JSON body and assert it always contains `generationConfig.responseSchema` and a separate `systemInstruction`; assert provider/router construction no longer accepts or passes `use_schema`.

- [ ] **Step 2: Run the request-shape test and verify RED**

Run: `pytest backend/tests/test_ai_timeout_routing.py -k "always_uses_schema" -v`

Expected: FAIL while the optional Gemma branch and constructor parameter remain.

- [ ] **Step 3: Simplify the provider and router**

Remove `use_schema`, delete the prompt-embedded-schema branch, always create native `responseSchema`, always set `systemInstruction`, and change route chains to model-name strings so all constructors use `GeminiProvider(model, db)`.

- [ ] **Step 4: Run all AI tests and verify GREEN**

Run: `pytest backend/tests/test_ai_schema.py backend/tests/test_ai_timeout_routing.py -v`

Expected: PASS.

### Task 5: Synchronize architecture documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/PLAN.md`
- Modify: `docs/SA.md`
- Modify: `docs/SD.md`
- Modify: `backend/app/providers/ai/base.py`

**Interfaces:**
- Consumes: the final single-model routine routing architecture.
- Produces: user-facing and code-level documentation that no longer advertises Gemma or a routine fallback.

- [ ] **Step 1: Update current architecture descriptions**

Replace Gemini/Gemma and Flash-Lite-to-Gemma routing descriptions with Gemini-only wording. Document that routine analysis uses Flash-Lite without a fallback and premium analysis tries Gemini 3.5 Flash before Flash-Lite.

- [ ] **Step 2: Verify obsolete runtime claims are gone**

Run: `rg -n -i "gemma-4|gemma" README.md docs/PLAN.md docs/SA.md docs/SD.md backend/app/providers/ai backend/app/core/quotas.yaml`

Expected: no matches except historical design/spec text that explicitly discusses the removed alternative.

### Task 6: Full verification

**Files:**
- Verify all modified production, test, spec, and plan files.

**Interfaces:**
- Consumes: completed Tasks 1 through 5.
- Produces: release-ready working tree with evidence from tests and lint.

- [ ] **Step 1: Run backend suite**

Run: `pytest backend/tests -q`

Expected: all tests pass and configured coverage threshold is met.

- [ ] **Step 2: Run lint and diff checks**

Run: `ruff check backend/app backend/tests`

Run: `git diff --check`

Expected: both exit successfully with no findings.

- [ ] **Step 3: Review final diff for scope and secrets**

Confirm Gemma is absent from runtime routing, raw AI output is not logged, no API keys are present, and only approved files changed.

- [ ] **Step 4: Commit the implementation**

Stage the implementation and test files, then commit with:

```text
fix: repair invalid structured AI output
```
