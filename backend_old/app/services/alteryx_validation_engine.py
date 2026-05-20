from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

from app.services.alteryx_dbt_publisher import fetch_bigquery_table_metadata

logger = logging.getLogger(__name__)


def validation_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def safe_metric_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_") or "Column"


def to_float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def within_numeric_tolerance(expected: Any, actual: Any, absolute: float = 0.0001, relative: float = 0.001) -> bool:
    expected_num = to_float_or_none(expected)
    actual_num = to_float_or_none(actual)
    if expected_num is None or actual_num is None:
        return expected == actual
    delta = abs(actual_num - expected_num)
    if delta <= absolute:
        return True
    denominator = max(abs(expected_num), 1.0)
    return (delta / denominator) <= relative


def profile_columns(profile: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    columns = profile.get("columns") or profile.get("columns_profile") or {}
    return columns if isinstance(columns, dict) else {}


def aggregate_profile_metric(columns: dict[str, Any], metric: str, names: list[str]) -> Any:
    values = [
        (columns.get(name) or {}).get(metric)
        for name in names
        if (columns.get(name) or {}).get(metric) is not None
    ]
    if not values:
        return None
    if metric in {"not_null_count", "numeric_count", "null_count"}:
        return int(sum(values))
    return sum(values)


def build_profile_validation_checks(source_profile: dict[str, Any], powerbi_validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not source_profile or not powerbi_validation:
        return []
    actual = powerbi_validation.get("actual") or {}
    queried_columns = powerbi_validation.get("queried_numeric_columns") or []
    queried_by_key = {validation_match_key(column): column for column in queried_columns}
    checks: list[dict[str, Any]] = []
    for source_name, source_column in (source_profile.get("columns") or {}).items():
        target_name = queried_by_key.get(validation_match_key(source_name))
        if not target_name:
            continue
        metric_key = safe_metric_key(target_name)
        metric_pairs = [
            ("not_null_count", source_column.get("not_null_count"), actual.get(f"{metric_key}__NotNull"), "high"),
            ("min_value", source_column.get("min_value"), actual.get(f"{metric_key}__Min"), "medium"),
            ("max_value", source_column.get("max_value"), actual.get(f"{metric_key}__Max"), "medium"),
            ("sum_value", source_column.get("sum_value"), actual.get(f"{metric_key}__Sum"), "medium"),
            ("average_value", source_column.get("average_value"), actual.get(f"{metric_key}__Average"), "medium"),
        ]
        for metric_name, expected, target, severity in metric_pairs:
            if expected is None and target is None:
                continue
            status = "PASS" if within_numeric_tolerance(expected, target) else "WARNING"
            checks.append({
                "name": f"{source_name}.{metric_name}",
                "expected": expected,
                "actual": target,
                "variance": (
                    to_float_or_none(target) - to_float_or_none(expected)
                    if to_float_or_none(target) is not None and to_float_or_none(expected) is not None
                    else None
                ),
                "status": status,
                "severity": severity,
                "details": "Deterministic source-vs-target profile check. LLM receives this only if it fails or warns.",
            })
    return checks


def build_dataset_profile_summary_checks(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    *,
    target_label: str,
) -> list[dict[str, Any]]:
    source_columns = profile_columns(source_profile)
    target_columns = profile_columns(target_profile)
    if not source_columns and not target_columns:
        return []
    if not source_columns:
        numeric_count = len(target_profile.get("numeric_columns") or [])
        return [
            {
                "name": "not_null_count",
                "expected": "Target profile validation",
                "actual": f"{target_label} profiled for {len(target_columns)} column(s)",
                "variance": None,
                "status": "PASS",
                "severity": "high",
                "details": "Target column completeness profile was calculated for the published model.",
            },
            {
                "name": "numeric_min_max",
                "expected": "Target numeric profile validation",
                "actual": f"{numeric_count} numeric target column(s)" if numeric_count else "No numeric target columns",
                "variance": None,
                "status": "PASS" if numeric_count else "NOT_APPLICABLE",
                "severity": "medium",
                "details": "Numeric min/max values were calculated from the published target model to validate transformed data quality.",
            },
            {
                "name": "numeric_sum_average",
                "expected": "Target numeric profile validation",
                "actual": f"{numeric_count} numeric target column(s)" if numeric_count else "No numeric target columns",
                "variance": None,
                "status": "PASS" if numeric_count else "NOT_APPLICABLE",
                "severity": "medium",
                "details": "Numeric sum and average values were calculated from the published target model to validate transformed data quality.",
            },
        ]
    if not target_columns:
        return []

    target_by_key = {validation_match_key(name): name for name in target_columns}
    common_pairs = [
        (source_name, target_by_key[validation_match_key(source_name)])
        for source_name in source_columns
        if validation_match_key(source_name) in target_by_key
    ]
    if not common_pairs:
        return [
            {
                "name": "not_null_count",
                "expected": list(source_columns),
                "actual": list(target_columns),
                "variance": None,
                "status": "WARNING",
                "severity": "high",
                "details": "No matching source and target column names were found for profile comparison.",
            }
        ]

    source_common = [source for source, _ in common_pairs]
    target_common = [target for _, target in common_pairs]
    source_not_null = aggregate_profile_metric(source_columns, "not_null_count", source_common)
    target_not_null = aggregate_profile_metric(target_columns, "not_null_count", target_common)
    source_numeric = [name for name in source_common if (source_columns.get(name) or {}).get("numeric_count", 0)]
    target_numeric = [target for source, target in common_pairs if source in source_numeric]

    checks = [
        {
            "name": "not_null_count",
            "expected": source_not_null,
            "actual": target_not_null,
            "variance": (
                target_not_null - source_not_null
                if isinstance(source_not_null, int) and isinstance(target_not_null, int)
                else None
            ),
            "status": "PASS" if source_not_null == target_not_null else "WARNING",
            "severity": "high",
            "details": f"Compared aggregate not-null counts across {len(common_pairs)} matched column(s).",
        }
    ]

    if not source_numeric or not target_numeric:
        checks.extend([
            {
                "name": "numeric_min_max",
                "expected": "Not applicable",
                "actual": "No comparable numeric columns",
                "variance": None,
                "status": "NOT_APPLICABLE",
                "severity": "medium",
                "details": "No matching numeric columns were available for min/max validation.",
            },
            {
                "name": "numeric_sum_average",
                "expected": "Not applicable",
                "actual": "No comparable numeric columns",
                "variance": None,
                "status": "NOT_APPLICABLE",
                "severity": "medium",
                "details": "No matching numeric columns were available for sum/average validation.",
            },
        ])
        return checks

    metric_pairs = [
        ("numeric_min_max", ("min_value", "max_value")),
        ("numeric_sum_average", ("sum_value", "average_value")),
    ]
    for check_name, metrics in metric_pairs:
        comparisons = []
        warnings = 0
        for source_name, target_name in zip(source_numeric, target_numeric):
            for metric in metrics:
                expected = (source_columns.get(source_name) or {}).get(metric)
                actual = (target_columns.get(target_name) or {}).get(metric)
                if expected is None and actual is None:
                    continue
                matched = within_numeric_tolerance(expected, actual)
                warnings += 0 if matched else 1
                comparisons.append(f"{source_name}.{metric}: {expected} -> {actual}")
        checks.append({
            "name": check_name,
            "expected": "; ".join(comparisons[:6]) or "Not applicable",
            "actual": f"{len(comparisons)} metric comparison(s)",
            "variance": warnings,
            "status": "PASS" if comparisons and warnings == 0 else "WARNING" if comparisons else "NOT_APPLICABLE",
            "severity": "medium",
            "details": "Compared numeric profile metrics across matched columns.",
        })
    return checks


def parse_bigquery_model_name(table_name: str) -> tuple[str, str, str]:
    parts = [part for part in str(table_name or "").split(".") if part]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    project_id = (os.getenv("GCP_PROJECT_ID") or "").strip()
    dataset = (os.getenv("GCP_BIGQUERY_DATASET") or os.getenv("BQ_DATASET") or "").strip()
    return project_id, dataset, parts[0] if parts else ""


def build_bigquery_validation_payload(table_name: str) -> dict[str, Any] | None:
    project_id, dataset, table = parse_bigquery_model_name(table_name)
    if not project_id or not dataset or not table:
        return None
    metadata = fetch_bigquery_table_metadata(
        project_id=project_id,
        dataset=dataset,
        table=table,
        location=os.getenv("GCP_BIGQUERY_LOCATION", "US"),
        env=os.environ.copy(),
    )
    if not metadata.get("success"):
        logger.warning(
            "[validation-engine] BigQuery target profile unavailable for %s.%s.%s: %s",
            project_id,
            dataset,
            table,
            metadata.get("message") or metadata.get("error"),
        )
        return None
    row_count = metadata.get("row_count")
    return {
        "success": True,
        "target_type": "bigquery",
        "table_name": metadata.get("final_model") or f"{project_id}.{dataset}.{table}",
        "column_count": metadata.get("column_count"),
        "available_columns": metadata.get("available_columns") or [],
        "queried_numeric_columns": metadata.get("numeric_columns") or [],
        "profile": metadata.get("profile") or {},
        "actual": {"RowCount": row_count},
        "checks": [
            {
                "name": "Row count",
                "actual": row_count,
                "variance": None,
                "status": "INFO",
            }
        ],
        "metadata": metadata,
    }


def aggregate_bigquery_validation_payload(table_names: list[str]) -> dict[str, Any] | None:
    payloads = [build_bigquery_validation_payload(table_name) for table_name in table_names if table_name]
    payloads = [payload for payload in payloads if payload]
    if not payloads:
        return None
    if len(payloads) == 1:
        return payloads[0]

    total_rows = 0
    total_columns = 0
    available_columns: list[str] = []
    profile_columns: dict[str, Any] = {}
    numeric_columns: list[str] = []
    for payload in payloads:
        table_name = str(payload.get("table_name") or "")
        table_label = parse_bigquery_model_name(table_name)[2] or table_name or "target"
        actual = payload.get("actual") or {}
        if isinstance(actual.get("RowCount"), (int, float)):
            total_rows += int(actual["RowCount"])
        if isinstance(payload.get("column_count"), (int, float)):
            total_columns += int(payload["column_count"])
        available_columns.extend(f"{table_label}.{column}" for column in payload.get("available_columns") or [])
        profile = payload.get("profile") or {}
        for column_name, column_profile in (profile.get("columns") or {}).items():
            aggregate_name = f"{table_label}.{column_name}"
            profile_columns[aggregate_name] = {**(column_profile or {}), "name": aggregate_name}
            if (column_profile or {}).get("numeric_count", 0) > 0:
                numeric_columns.append(aggregate_name)

    return {
        "success": True,
        "target_type": "bigquery",
        "table_name": ", ".join(str(payload.get("table_name") or "") for payload in payloads),
        "column_count": total_columns,
        "available_columns": available_columns,
        "queried_numeric_columns": numeric_columns,
        "profile": {
            "name": "BigQuery output tables",
            "row_count": total_rows,
            "column_count": total_columns,
            "columns": profile_columns,
            "numeric_columns": numeric_columns,
        },
        "actual": {"RowCount": total_rows},
        "checks": [
            {
                "name": "Row count",
                "actual": total_rows,
                "variance": None,
                "status": "INFO",
            }
        ],
        "tables": payloads,
    }


def build_validation_response(
    *,
    workflow_id: str,
    workflow_name: str | None,
    requested_table_name: str,
    alteryx_count: dict[str, Any],
    target_validation: dict[str, Any] | None,
    powerbi_validation: dict[str, Any] | None = None,
    bigquery_validation: dict[str, Any] | None = None,
    numeric_columns: list[str] | None = None,
) -> dict[str, Any]:
    expected = alteryx_count.get("row_count")
    source_profile = alteryx_count.get("profile") or {}
    target_actual = None
    if target_validation:
        target_row_check = next(
            (check for check in target_validation.get("checks", []) if check.get("name") == "Row count"),
            {},
        )
        target_actual = target_row_check.get("actual")
        if not isinstance(target_actual, int):
            actual_payload = target_validation.get("actual") or {}
            actual_value = actual_payload.get("RowCount")
            if isinstance(actual_value, (int, float)):
                target_actual = int(actual_value)

    if expected is None and isinstance(target_actual, int) and target_actual > 0:
        alteryx_count = {
            **alteryx_count,
            "row_count": target_actual,
            "method": "target_count_fallback",
            "source": "Target count used because source Alteryx output count was not available.",
            "confidence": "medium",
        }
        expected = target_actual

    row_check = next(
        (check for check in (target_validation or {}).get("checks", []) if check.get("name") == "Row count"),
        {},
    )
    actual = row_check.get("actual") if row_check else None
    variance = (
        actual - expected
        if isinstance(actual, int) and isinstance(expected, int)
        else row_check.get("variance")
    )
    status = (
        "PASS" if isinstance(variance, int) and variance == 0 else "WARNING"
        if isinstance(variance, int) else row_check.get("status") or "INFO"
    )

    target_profile = (target_validation or {}).get("profile") or {}
    target_label = "BigQuery" if bigquery_validation else "Power BI"
    profile_checks = (
        build_dataset_profile_summary_checks(source_profile, target_profile, target_label=target_label)
        if target_profile
        else build_profile_validation_checks(source_profile, powerbi_validation)
    )
    checks = [
        {
            "name": "Row count",
            "expected": expected,
            "actual": actual,
            "variance": variance,
            "status": status,
            "alteryx_method": alteryx_count.get("method"),
            "alteryx_confidence": alteryx_count.get("confidence"),
            "severity": "critical",
            "details": "Deterministic row-count comparison between Alteryx output and target semantic model.",
        }
    ] + profile_checks

    return {
        "success": True,
        "workflow_id": workflow_id,
        "workflow_name": workflow_name,
        "table_name": (target_validation or {}).get("table_name") or requested_table_name,
        "available_columns": (target_validation or {}).get("available_columns", []),
        "column_count": (target_validation or {}).get("column_count"),
        "source_profile": source_profile,
        "target_profile": target_profile,
        "queried_numeric_columns": numeric_columns or [],
        "alteryx": alteryx_count,
        "powerbi": powerbi_validation,
        "bigquery": bigquery_validation,
        "checks": checks,
    }
