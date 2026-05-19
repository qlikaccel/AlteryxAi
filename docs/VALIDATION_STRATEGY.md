# Data Reconciliation and Validation Strategy

## Principle

The validation verdict must be deterministic. LLMs may explain failures and recommend investigation steps, but they must not decide whether migrated data is accurate.

## Validation Layers

1. Structural validation
   - table exists
   - expected columns exist
   - unexpected target columns are flagged
   - data types are compared where available

2. Row-level volume validation
   - source row count
   - target row count
   - accepted variance only when explicitly configured

3. Column completeness validation
   - not-null count per important column
   - null count per important column
   - required/business-key columns treated as high severity

4. Numeric profile validation
   - min
   - max
   - sum
   - average
   - configurable absolute and relative tolerance

5. Aggregated business validation
   - group-by summaries for important dimensions such as region, period, customer, product, or workflow-specific keys
   - source and target aggregate comparison

6. Complex workflow validation
   - compare expected macro output counts
   - compare batch control parameter coverage
   - compare iterative macro depth or convergence signals when available

## LLM Usage

Use LLMs for:

- interpreting complex Alteryx formula or macro intent
- recommending validation focus areas
- explaining why reconciliation may have failed
- generating human-readable remediation notes

Do not use LLMs for:

- row count verdicts
- not-null verdicts
- min/max/sum/average verdicts
- final accuracy score
- deciding whether production migration is accepted

## Provider Split

- Hugging Face: BRD generation and executive summaries.
- Anthropic: complex workflow/macro reasoning.
- OpenAI: fallback for complex workflow/macro reasoning.
- Deterministic engine: final conversion checks and validation verdicts.

## Accuracy Score

The initial score is the percentage of deterministic checks that pass. Later versions can weight checks:

- critical: row count, missing columns, required not-null keys
- high: not-null count mismatch
- medium: min/max/sum/average mismatch
- low: extra target columns or advisory warnings

