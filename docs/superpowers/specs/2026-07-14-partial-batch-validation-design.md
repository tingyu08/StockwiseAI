# Partial Batch Validation Design

## Goal

Prevent one semantically invalid stock report from failing an entire daily analysis job, while preserving strict validation and never inventing or silently adjusting AI-generated prices.

## Business Rule

`target_price_low <= target_price_high` remains mandatory for every action. The rule `stop_loss < target_price_low` applies only when `action == "buy"`, because the simulation engine consumes `stop_loss` only from the report attached to an executed buy order. Hold and sell reports still require all price fields to be positive but are not rejected by that buy-entry relationship.

The system prompt must communicate the same conditional rule so generation and validation use one contract.

## Batch Data Flow

1. Request the complete batch with native Gemini structured output.
2. Decode the outer JSON object and `reports` array.
3. Validate every report independently with `AnalysisReport`.
4. Preserve valid reports immediately.
5. Build a repair request containing only invalid reports, their validation errors, and only the matching stock contexts.
6. Validate repaired reports independently and merge successful repairs back into the original symbol order.
7. Log and omit any symbol that remains invalid after the repair request.

The downstream analysis service already treats a missing report as skippable, so omitted symbols do not fail the remaining batch or job.

## Structural Failure

If the initial output is not JSON or does not contain a `reports` list, the provider cannot identify independently salvageable records. It uses the existing one-time whole-output repair. If the repaired output is still structurally unusable, it raises `UpstreamError`.

If the initial batch is structurally valid and only individual reports are invalid, failure of the targeted repair does not raise for the whole batch. Those individual symbols are skipped with a warning.

## Logging and Errors

- Validation logs include attempt, report symbol or index, and concise errors without the full model response.
- Permanently skipped reports log their symbols.
- Generic structured generation errors say the output failed structure or business-rule validation, rather than incorrectly calling every validation failure invalid JSON.

## Tests

- Hold and sell reports may have `stop_loss >= target_price_low`; buy reports may not.
- The prompt states the conditional buy rule.
- A mixed batch repairs only invalid symbols and preserves output order.
- A failed targeted repair omits and logs only the bad symbol.
- Structurally invalid output still uses one whole-output repair and then fails clearly if unrecoverable.
- Full backend tests and lint remain green.
