# Frontend Validation UI

The reconciliation checks should be shown after publish or target generation, alongside the existing migration summary.

## User Flow

1. User converts or publishes an Alteryx workflow.
2. Backend runs deterministic reconciliation checks.
3. Frontend shows a reconciliation dashboard.
4. Failed checks can be filtered.
5. User can request an LLM explanation for failed checks.
6. The explanation is advisory only; the deterministic status remains visible.

## Dashboard Sections

- Overall status: pass, warn, fail, or pending.
- Accuracy score: percentage of deterministic checks that passed.
- Summary metrics: total checks, passed, warnings, failed, critical failures.
- Checks table:
  - check name
  - status
  - severity
  - source value
  - target value
  - details
- Investigation notes: optional LLM-generated explanation of failed checks.

## API Contract

```http
GET /api/context-engineering/batches/{batch_id}/workflows/{workflow_id}/reconciliation
```

Returns:

```json
{
  "source_name": "Alteryx output",
  "target_name": "Power BI semantic model",
  "status": "fail",
  "accuracy_score": 76.92,
  "checks": [
    {
      "name": "Amount.sum_value",
      "status": "fail",
      "severity": "medium",
      "source_value": "84220390.22",
      "target_value": "84219910.22",
      "details": "Compare numeric sum value."
    }
  ]
}
```

Optional explanation endpoint:

```http
POST /api/context-engineering/reconciliation/explain
```

The explanation endpoint should call the complex-workflow LLM provider chain only for narrative support. It must not change `status`, `accuracy_score`, or individual check statuses.

