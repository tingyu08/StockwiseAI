# Structured Analysis Repair Design

## Goal

Make routine stock analysis deterministic at the application boundary by removing the unreliable Gemma fallback, explicitly communicating semantic price rules, and giving Gemini one informed opportunity to repair an invalid response.

## Selected Approach

Routine analysis uses only `gemini-3.1-flash-lite` with Gemini structured output enabled. There is no routine fallback model. If the request itself times out or returns a retryable upstream status, the existing HTTP retry policy remains responsible for retries. If the response is returned successfully but fails Pydantic validation, the provider makes exactly one repair request containing the validation errors and the previous raw response.

Alternatives rejected:

- Keep Gemma and extract JSON from free-form text: this remains probabilistic and cannot satisfy the strict application data contract reliably.
- Add another structured-output Gemini fallback: this introduces another quota and routing dependency that the user does not want.

## Prompt Contract

The system prompt must state these semantic invariants explicitly:

- `0 < stop_loss < target_price_low <= target_price_high`
- The bull, base, and bear probabilities must total approximately 1; the existing Pydantic tolerance of `0.98` through `1.02` remains authoritative.
- Return only data matching the requested structured-output schema.

The Pydantic models remain the final source of truth. Prompt instructions reduce invalid generations but never replace server-side validation.

## Validation Repair Flow

1. Send the original prompt with the requested structured-output schema.
2. Parse the raw response using `output_model.model_validate_json`.
3. On success, return the validated model.
4. On `ValidationError`, build a repair prompt that includes:
   - the original task prompt;
   - the prior raw response;
   - concise machine-readable Pydantic error details;
   - an instruction to correct only the invalid result and return JSON matching the same schema.
5. Send one repair request using the same output schema.
6. Validate again. If it still fails, raise `UpstreamError` as before.

The raw response and complete repair prompt must not be written to logs. Logs may include model name, validation attempt number, and concise validation errors.

## Routing Changes

`ROUTINE_CHAIN` contains only `("gemini-3.1-flash-lite", True)`. `PREMIUM_CHAIN` continues to try `gemini-3.5-flash` first and then the routine model. Existing router behavior remains unchanged otherwise: after the only routine model fails, `analyze_batch` raises `UpstreamError("所有例行分析模型皆不可用")`.

Gemma-specific prompt/schema branching in `GeminiProvider` is removed because no configured route uses it. `GeminiProvider` always sends `responseSchema` and uses a separate system instruction.

## Tests

Automated tests must prove:

- The routine route contains no Gemma fallback and fails after the single configured model fails.
- The system prompt contains the exact price-ordering invariant.
- A semantically invalid first response triggers a second request whose prompt contains the previous response and validation error, then accepts a corrected response.
- A valid first response makes only one request.
- Two invalid responses still raise `UpstreamError`.
- Existing timeout, quota, premium routing, schema validation, and full backend tests continue to pass.

## Out of Scope

- Adding or configuring a replacement fallback model.
- Changing the existing timeout and HTTP retry policy.
- Relaxing Pydantic business rules.
- Automatically rotating the exposed Google API key; that remains an external credential-management action.
