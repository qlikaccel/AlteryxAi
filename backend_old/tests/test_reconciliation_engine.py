from app.services.reconciliation_engine import (
    build_llm_validation_explanation_context,
    profile_rows,
    reconcile_profiles,
)


def test_reconciliation_passes_for_matching_profiles():
    source = profile_rows(
        "alteryx",
        [
            {"id": 1, "amount": 10, "region": "West"},
            {"id": 2, "amount": 20, "region": "East"},
        ],
    )
    target = profile_rows(
        "powerbi",
        [
            {"id": 1, "amount": "10", "region": "West"},
            {"id": 2, "amount": "20", "region": "East"},
        ],
    )

    report = reconcile_profiles(source, target)

    assert report.status == "pass"
    assert report.accuracy_score == 100.0


def test_reconciliation_fails_for_missing_column_and_metric_mismatch():
    source = profile_rows("alteryx", [{"id": 1, "amount": 10, "required": "Y"}])
    target = profile_rows("powerbi", [{"id": 1, "amount": 11}])

    report = reconcile_profiles(source, target)

    assert report.status == "fail"
    assert any(check.name == "column_presence" and check.status == "fail" for check in report.checks)
    assert any(check.name == "amount.sum_value" and check.status == "fail" for check in report.checks)


def test_llm_explanation_context_never_overrides_verdict():
    source = profile_rows("alteryx", [{"id": 1, "amount": 10}])
    target = profile_rows("powerbi", [{"id": 1, "amount": 12}])
    report = reconcile_profiles(source, target)

    context = build_llm_validation_explanation_context(report)

    assert context["status"] == "fail"
    assert "Do not override deterministic validation status." in context["rules"]
    assert context["failed_or_warned_checks"]

