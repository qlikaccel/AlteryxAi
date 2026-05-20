from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .architecture_config import config_float, config_int, load_architecture_config

ARCHITECTURE_CONFIG = load_architecture_config()

@dataclass(frozen=True)
class ColumnProfile:
    name: str
    row_count: int
    not_null_count: int
    null_count: int
    numeric_count: int
    min_value: str | None = None
    max_value: str | None = None
    sum_value: str | None = None
    average_value: str | None = None


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    row_count: int
    columns: dict[str, ColumnProfile]


@dataclass(frozen=True)
class ValidationCheck:
    name: str
    status: str
    severity: str
    source_value: Any
    target_value: Any
    details: str


@dataclass(frozen=True)
class ReconciliationReport:
    source_name: str
    target_name: str
    status: str
    accuracy_score: float
    checks: list[ValidationCheck]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["checks"] = [asdict(check) for check in self.checks]
        return payload


def profile_rows(name: str, rows: Iterable[dict[str, Any]]) -> DatasetProfile:
    materialized = list(rows)
    column_names = sorted({str(key) for row in materialized for key in row.keys()})
    columns = {
        column_name: _profile_column(column_name, [row.get(column_name) for row in materialized], len(materialized))
        for column_name in column_names
    }
    return DatasetProfile(name=name, row_count=len(materialized), columns=columns)


def reconcile_profiles(
    source: DatasetProfile,
    target: DatasetProfile,
    *,
    absolute_tolerance: float | None = None,
    relative_tolerance: float | None = None,
) -> ReconciliationReport:
    if absolute_tolerance is None:
        absolute_tolerance = config_float(ARCHITECTURE_CONFIG, "validation", "numeric_tolerance_absolute", 0.0001)
    if relative_tolerance is None:
        relative_tolerance = config_float(ARCHITECTURE_CONFIG, "validation", "numeric_tolerance_relative", 0.001)
    checks: list[ValidationCheck] = []
    checks.append(
        ValidationCheck(
            name="row_count",
            status="pass" if source.row_count == target.row_count else "fail",
            severity="critical",
            source_value=source.row_count,
            target_value=target.row_count,
            details="Source and target row counts must match.",
        )
    )

    source_columns = set(source.columns)
    target_columns = set(target.columns)
    missing_columns = sorted(source_columns - target_columns)
    extra_columns = sorted(target_columns - source_columns)
    checks.append(
        ValidationCheck(
            name="column_presence",
            status="pass" if not missing_columns else "fail",
            severity="critical",
            source_value=sorted(source_columns),
            target_value=sorted(target_columns),
            details=f"Missing columns: {missing_columns}; extra target columns: {extra_columns}",
        )
    )

    for column_name in sorted(source_columns & target_columns):
        source_column = source.columns[column_name]
        target_column = target.columns[column_name]
        checks.extend(_compare_column(source_column, target_column, absolute_tolerance, relative_tolerance))

    failed = [check for check in checks if check.status == "fail"]
    warned = [check for check in checks if check.status == "warn"]
    status = "fail" if failed else "warn" if warned else "pass"
    accuracy_score = round(100.0 * sum(1 for check in checks if check.status == "pass") / max(len(checks), 1), 2)
    return ReconciliationReport(
        source_name=source.name,
        target_name=target.name,
        status=status,
        accuracy_score=accuracy_score,
        checks=checks,
    )


def build_llm_validation_explanation_context(report: ReconciliationReport) -> dict[str, Any]:
    failed_or_warned = [check for check in report.checks if check.status != "pass"]
    failed_check_limit = config_int(ARCHITECTURE_CONFIG, "context", "failed_check_limit", 50)
    return {
        "task": "Explain deterministic reconciliation failures and suggest investigation steps.",
        "rules": [
            "Do not override deterministic validation status.",
            "Do not claim the data is accurate if checks failed.",
            "Explain likely causes and next diagnostic steps.",
        ],
        "source_name": report.source_name,
        "target_name": report.target_name,
        "status": report.status,
        "accuracy_score": report.accuracy_score,
        "failed_or_warned_checks": [asdict(check) for check in failed_or_warned[:failed_check_limit]],
        "exception_controls": {
            "failed_or_warned_total": len(failed_or_warned),
            "failed_or_warned_included": min(len(failed_or_warned), failed_check_limit),
            "failed_check_limit": failed_check_limit,
            "raw_rows_sent_to_llm": False,
        },
    }


def _profile_column(name: str, values: list[Any], row_count: int) -> ColumnProfile:
    non_null_values = [value for value in values if not _is_null(value)]
    decimals = [_to_decimal(value) for value in non_null_values]
    numeric_values = [value for value in decimals if value is not None]

    min_value = max_value = sum_value = average_value = None
    if numeric_values:
        total = sum(numeric_values, Decimal("0"))
        min_value = str(min(numeric_values))
        max_value = str(max(numeric_values))
        sum_value = str(total)
        average_value = str(total / Decimal(len(numeric_values)))

    return ColumnProfile(
        name=name,
        row_count=row_count,
        not_null_count=len(non_null_values),
        null_count=row_count - len(non_null_values),
        numeric_count=len(numeric_values),
        min_value=min_value,
        max_value=max_value,
        sum_value=sum_value,
        average_value=average_value,
    )


def _compare_column(
    source: ColumnProfile,
    target: ColumnProfile,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> list[ValidationCheck]:
    checks = [
        ValidationCheck(
            name=f"{source.name}.not_null_count",
            status="pass" if source.not_null_count == target.not_null_count else "fail",
            severity="high",
            source_value=source.not_null_count,
            target_value=target.not_null_count,
            details="Compare not-null counts for source and target column.",
        )
    ]
    for metric_name in ("min_value", "max_value", "sum_value", "average_value"):
        source_value = getattr(source, metric_name)
        target_value = getattr(target, metric_name)
        if source_value is None and target_value is None:
            continue
        checks.append(
            ValidationCheck(
                name=f"{source.name}.{metric_name}",
                status="pass" if _within_tolerance(source_value, target_value, absolute_tolerance, relative_tolerance) else "fail",
                severity="medium",
                source_value=source_value,
                target_value=target_value,
                details=f"Compare numeric {metric_name.replace('_', ' ')}.",
            )
        )
    return checks


def _is_null(value: Any) -> bool:
    return value is None or value == ""


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or _is_null(value):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        return None


def _within_tolerance(source_value: Any, target_value: Any, absolute_tolerance: float, relative_tolerance: float) -> bool:
    source_decimal = _to_decimal(source_value)
    target_decimal = _to_decimal(target_value)
    if source_decimal is None or target_decimal is None:
        return source_value == target_value
    delta = abs(source_decimal - target_decimal)
    if delta <= Decimal(str(absolute_tolerance)):
        return True
    denominator = max(abs(source_decimal), Decimal("1"))
    return (delta / denominator) <= Decimal(str(relative_tolerance))
