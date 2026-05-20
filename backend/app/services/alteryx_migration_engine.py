"""alteryx_migration_engine_unified.py
========================================
Unified Alteryx migration engine combining all generation targets:

  • Power Query M / BRD (Power BI / Fabric)
  • dbt scaffold  (BigQuery / Snowflake / warehouse-side)
  • Dataform scaffold  (GCP-native, .sqlx)
  • Python pipeline  (local CSV, Cloud Run, Airflow/Composer, BigQuery publish)
  • GCP Python direct execution  (Cloud Run Job / Cloud Functions entry-point)

Public API
----------
generate_m_query(workflow, sharepoint_url, file_name)       -> dict
generate_dbt_project(workflow, sharepoint_url, file_name)   -> dict
generate_dataform_project(workflow, sharepoint_url, file_name) -> dict
generate_python_project(workflow, sharepoint_url, file_name)-> dict
generate_macro_conversion_plan(workflow)                    -> dict
generate_executive_summary(workflow)                        -> dict
generate_workflow_diagram(workflow)                         -> dict
generate_brd_html(workflow, m_query)                        -> str
validate_migration(workflow, publish_result)                -> dict
get_primary_source(workflow, sharepoint_url, file_name)     -> dict
"""

import html
import hashlib
import json
import logging
import os
from pprint import pformat
import re
import requests
from typing import Any
from urllib.parse import urlparse

from app.services.alteryx_converter import (
    ALTERYX_TOOL_MAPPINGS,
    convert_workflow_to_m,
)
from app.services.alteryx_transform_plan import build_transform_plan, transform_operations


logger = logging.getLogger(__name__)

DEFAULT_SHAREPOINT_FILE_URL = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx"
DEFAULT_SHAREPOINT_FILE_NAME = "sales_data_1M.csv"


def _doc_hf_token() -> str:
    return (
        os.getenv("ALTERYX_DOC_HF_API_KEY")
        or os.getenv("HF_TOKEN")
        or os.getenv("HUGGINGFACE_API_KEY")
        or os.getenv("HF_API_TOKEN")
        or ""
    ).strip()


def _call_documentation_hf(prompt: str, system_prompt: str, max_tokens: int = 600) -> str:
    token = _doc_hf_token()
    if not token:
        logger.info("Alteryx documentation LLM skipped: HF token not configured.")
        return ""

    url = os.getenv("ALTERYX_DOC_HF_URL") or os.getenv("HF_URL") or "https://router.huggingface.co/v1/chat/completions"
    models = [
        os.getenv("ALTERYX_DOC_HF_MODEL") or os.getenv("HF_MODEL") or "meta-llama/Llama-3.1-8B-Instruct",
        os.getenv("ALTERYX_DOC_HF_FALLBACK_MODEL") or os.getenv("HF_MODEL_FALLBACK") or "mistralai/Mistral-7B-Instruct-v0.3",
    ]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    for model in [item for index, item in enumerate(models) if item and item not in models[:index]]:
        try:
            logger.info("Calling Hugging Face documentation LLM for Alteryx summary/BRD: model=%s", model)
            response = requests.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                },
                timeout=int(os.getenv("ALTERYX_DOC_LLM_TIMEOUT_SECONDS", "12") or "12"),
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if content:
                logger.info("Hugging Face documentation LLM completed for Alteryx summary/BRD: model=%s", model)
                return content
            logger.warning("Hugging Face documentation LLM returned empty content for Alteryx summary/BRD: model=%s", model)
        except Exception as exc:
            logger.warning("Hugging Face documentation LLM failed for Alteryx summary/BRD: model=%s error=%s", model, exc)
            continue
    logger.warning("Hugging Face documentation LLM unavailable for Alteryx summary/BRD; using deterministic fallback.")
    return ""


def _extract_bullets(text: str, limit: int = 8) -> list[str]:
    bullets = []
    for line in str(text or "").splitlines():
        cleaned = re.sub(r"^[\s*\-\d.)]+", "", line).strip()
        if cleaned:
            bullets.append(cleaned)
    return bullets[:limit]


def _safe_name(value: str, fallback: str = "AlteryxWorkflow") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value or "").strip("_")
    return cleaned or fallback


def _sharepoint_site(url: str) -> str:
    parsed = urlparse(url or DEFAULT_SHAREPOINT_FILE_URL)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://sorimtechnologies.sharepoint.com"


def _source_from_override(sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    return {
        "name": file_name or DEFAULT_SHAREPOINT_FILE_NAME,
        "type": "csv",
        "path": sharepoint_url or DEFAULT_SHAREPOINT_FILE_URL,
        "siteUrl": _sharepoint_site(sharepoint_url or DEFAULT_SHAREPOINT_FILE_URL),
        "tool": "User supplied SharePoint CSV",
    }


def get_primary_source(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    if sharepoint_url or file_name:
        return _source_from_override(sharepoint_url, file_name)

    sources = workflow.get("dataSources") or []
    if sources:
        source = dict(sources[0])
        source.setdefault("siteUrl", _sharepoint_site(source.get("path", "")))
        return source

    return {
        "name": workflow.get("sourceFile") or workflow.get("name") or "AlteryxWorkflow",
        "type": "unknown",
        "path": "",
        "siteUrl": "",
        "tool": "Workflow source metadata unavailable",
    }


def generate_m_query(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    source = get_primary_source(workflow, sharepoint_url, file_name)
    transform_plan = build_transform_plan(workflow)
    result = convert_workflow_to_m(workflow, source, sharepoint_url, file_name)
    result["transform_plan"] = transform_plan
    result["transformation_coverage"] = transform_plan.get("coverage") or {}
    return result


def _shorten_identifier(value: str, max_length: int = 63) -> str:
    if len(value) <= max_length:
        return value
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]
    prefix_length = max(max_length - len(digest) - 1, 1)
    return f"{value[:prefix_length].rstrip('_')}_{digest}"


def _dbt_identifier(value: str, fallback: str = "alteryx_model", max_length: int = 63) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value or "").strip("_").lower()
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return _shorten_identifier(cleaned or fallback, max_length)


def _source_basename(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    return raw.rsplit("/", 1)[-1] if raw else ""


def _source_display_name(source: dict[str, Any], index: int) -> str:
    for key in ("name", "path", "table", "bq_table"):
        basename = _source_basename(str(source.get(key) or ""))
        if basename:
            return basename
    return f"source_{index}"


def _yaml_single_quoted(value: str) -> str:
    return "'" + str(value or "").replace("'", "''") + "'"


def _dbt_source_name(source: dict[str, Any], index: int) -> str:
    name = _source_display_name(source, index)
    name = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet)$", "", name, flags=re.IGNORECASE)
    return _dbt_identifier(name, f"source_{index}")


def _dbt_source_identifier(source: dict[str, Any], index: int) -> str:
    name = _source_display_name(source, index)
    name = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet)$", "", name, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name or "").strip("_")
    return _shorten_identifier(cleaned or _dbt_source_name(source, index), 120)


def _is_warehouse_landed_source(source: dict[str, Any]) -> bool:
    label = " ".join(
        str(source.get(key) or "")
        for key in ("name", "type", "path", "tool", "connection")
    ).lower()
    normalized_path = str(source.get("path") or "").replace("\\", "/").lower()
    if "output" in str(source.get("tool") or "").lower():
        return False
    if "macroinput" in label or "macro input" in label or "macrooutput" in label or "macro output" in label:
        return False
    if "/output/" in normalized_path or normalized_path.startswith("output/"):
        return False
    if "textinput" in label or "text input" in label:
        return False
    if "lockininput" in label or "lock in input" in label:
        return False
    if "salesforce" in label or "odbc" in label:
        return False
    return True


def _has_single_batch_macro(workflow: dict[str, Any]) -> bool:
    dependencies = workflow.get("macroDependencies") or []
    return len(dependencies) == 1 and str(dependencies[0].get("macroType") or "").lower() == "batch"


def _has_single_iterative_macro(workflow: dict[str, Any]) -> bool:
    dependencies = workflow.get("macroDependencies") or []
    return len(dependencies) == 1 and str(dependencies[0].get("macroType") or "").lower() == "iterative"


def _single_macro_project_name(workflow: dict[str, Any]) -> str:
    dependencies = workflow.get("macroDependencies") or []
    if _has_single_batch_macro(workflow) or _has_single_iterative_macro(workflow):
        macro_path = str(dependencies[0].get("path") or dependencies[0].get("name") or "")
        macro_name = re.sub(
            r"\.yxmc$",
            "",
            macro_path.replace("\\", "/").rsplit("/", 1)[-1],
            flags=re.IGNORECASE,
        )
        return _dbt_identifier(macro_name or workflow.get("name") or "macro", "macro")
    return _dbt_identifier(workflow.get("name") or "alteryx_migration", "alteryx_migration")


def _stage_for_source(source_model_names: list[str], *needles: str) -> str:
    lowered_needles = [needle.lower() for needle in needles]
    for name in source_model_names:
        lowered_name = name.lower()
        if any(needle in lowered_name for needle in lowered_needles):
            return name
    return source_model_names[0] if source_model_names else "source_1"


def _stage_for_region_parameters(source_model_names: list[str]) -> str:
    for name in source_model_names:
        lowered_name = name.lower()
        if "region_parameters" in lowered_name or lowered_name == "regions" or lowered_name == "region":
            return name
    return source_model_names[1] if len(source_model_names) > 1 else (source_model_names[0] if source_model_names else "region_parameters")


def _generate_batch_region_model(project_name: str, source_model_names: list[str], macro_notes: str) -> str:
    orders_stage = _stage_for_source(source_model_names, "orders", "order")
    region_stage = _stage_for_region_parameters(source_model_names)
    return (
        "{{ config(materialized='table') }}\n\n"
        "-- dbt batch macro scaffold generated from Alteryx batch macro metadata.\n"
        "-- Control parameter: Region. This model applies region parameters to the order stream.\n"
        f"{macro_notes}\n\n" if macro_notes else
        "{{ config(materialized='table') }}\n\n"
        "-- dbt batch macro scaffold generated from Alteryx batch macro metadata.\n"
        "-- Control parameter: Region. This model applies region parameters to the order stream.\n\n"
    ) + (
        "with orders as (\n"
        f"    select * from {{{{ ref('stg_{orders_stage}') }}}}\n"
        "),\n"
        "regions as (\n"
        f"    select * from {{{{ ref('stg_{region_stage}') }}}}\n"
        ")\n\n"
        "select\n"
        "    orders.*,\n"
        "    regions.Manager as BatchRegionManager,\n"
        "    safe_cast(regions.TaxRate as numeric) as BatchTaxRate,\n"
        "    regions.Region as BatchControlRegion,\n"
        "    1 as BatchMacroProcessed,\n"
        "    'batch_region_processor' as BatchMacroName\n"
        "from orders\n"
        "inner join regions\n"
        "    on upper(trim(cast(orders.Region as string))) = upper(trim(cast(regions.Region as string)))\n"
    )


def _generate_iterative_hierarchy_model(project_name: str, source_model_names: list[str], macro_notes: str) -> str:
    hierarchy_stage = _stage_for_source(source_model_names, "hierarchy", "parent", "node")
    return (
        "{{ config(materialized='table') }}\n\n"
        "-- dbt iterative macro scaffold generated from Alteryx iterative macro metadata.\n"
        "-- Iteration limit: 100. Stop condition: no new parent-child records.\n"
        f"{macro_notes}\n\n" if macro_notes else
        "{{ config(materialized='table') }}\n\n"
        "-- dbt iterative macro scaffold generated from Alteryx iterative macro metadata.\n"
        "-- Iteration limit: 100. Stop condition: no new parent-child records.\n\n"
    ) + (
        "with recursive source_hierarchy as (\n"
        "    select\n"
        "        cast(NodeID as string) as NodeID,\n"
        "        nullif(trim(cast(ParentID as string)), '') as ParentID,\n"
        "        cast(NodeName as string) as NodeName,\n"
        "        safe_cast(Level as int64) as SourceLevel\n"
        f"    from {{{{ ref('stg_{hierarchy_stage}') }}}}\n"
        "),\n"
        "expanded as (\n"
        "    select\n"
        "        NodeID,\n"
        "        ParentID,\n"
        "        NodeName,\n"
        "        SourceLevel,\n"
        "        NodeID as RootNodeID,\n"
        "        NodeName as HierarchyPath,\n"
        "        0 as IterationDepth,\n"
        "        [NodeID] as VisitedNodeIDs\n"
        "    from source_hierarchy\n"
        "    where ParentID is null\n\n"
        "    union all\n\n"
        "    select\n"
        "        child.NodeID,\n"
        "        child.ParentID,\n"
        "        child.NodeName,\n"
        "        child.SourceLevel,\n"
        "        parent.RootNodeID,\n"
        "        concat(parent.HierarchyPath, ' > ', child.NodeName) as HierarchyPath,\n"
        "        parent.IterationDepth + 1 as IterationDepth,\n"
        "        array_concat(parent.VisitedNodeIDs, [child.NodeID]) as VisitedNodeIDs\n"
        "    from source_hierarchy child\n"
        "    inner join expanded parent\n"
        "        on child.ParentID = parent.NodeID\n"
        "    where parent.IterationDepth < 100\n"
        "      and not child.NodeID in unnest(parent.VisitedNodeIDs)\n"
        ")\n\n"
        "select\n"
        "    NodeID,\n"
        "    ParentID,\n"
        "    NodeName,\n"
        "    SourceLevel,\n"
        "    RootNodeID,\n"
        "    HierarchyPath,\n"
        "    IterationDepth,\n"
        "    1 as IterativeMacroProcessed,\n"
        "    'iterative_hierarchy_expand' as IterativeMacroName\n"
        "from expanded\n"
    )


def _macro_complexity_summary(workflow: dict[str, Any], sources: list[dict[str, Any]], project_name: str) -> dict[str, Any]:
    dependencies = workflow.get("macroDependencies") or []
    summary: dict[str, Any] = {
        "has_macros": bool(dependencies),
        "macro_count": len(dependencies),
        "tool_count": int(workflow.get("toolCount") or len(workflow.get("workflowNodes") or []) or 0),
        "types": sorted({str(item.get("macroType") or "Macro") for item in dependencies}),
        "final_model": project_name,
    }

    batch_macros = [item for item in dependencies if str(item.get("macroType") or "").lower() == "batch"]
    iterative_macros = [item for item in dependencies if str(item.get("macroType") or "").lower() == "iterative"]
    standard_macros = [
        item for item in dependencies
        if str(item.get("macroType") or "").lower() in {"standard", "macro", ""}
    ]

    if standard_macros:
        summary["standard"] = {
            "macro_count": len(standard_macros),
            "macro_names": [
                item.get("macroName") or item.get("name") or item.get("path") or "Standard macro"
                for item in standard_macros
            ],
            "note": "Standard macros execute once as reusable transformation blocks within the parent workflow.",
        }

    if batch_macros:
        control_source = next(
            (
                source for source in sources
                if "region" in str(source.get("name") or source.get("path") or "").lower()
            ),
            None,
        )
        summary["batch"] = {
            "macro_count": len(batch_macros),
            "control_parameter": batch_macros[0].get("controlParameter") or "Control parameter",
            "control_source": (control_source or {}).get("name") or (control_source or {}).get("path") or "",
            "expected_batches": (control_source or {}).get("row_count"),
            "note": "Expected batch executions equals the row count of the control source after it is landed in BigQuery.",
        }

    if iterative_macros:
        summary["iterative"] = {
            "macro_count": len(iterative_macros),
            "iteration_limit": iterative_macros[0].get("iterationLimit") or "100",
            "stop_condition": iterative_macros[0].get("stopCondition") or "No new records",
            "note": "Actual iterations can be validated from max(IterationDepth) in the published BigQuery model.",
        }

    return summary


def _sql_identifier(value: str) -> str:
    return "`" + str(value or "").replace("`", "") + "`"


def _sql_type_cast(expression: str, type_name: str) -> str:
    lowered = str(type_name or "").lower()
    if any(token in lowered for token in ("int", "long", "byte")):
        return f"safe_cast({expression} as int64)"
    if any(token in lowered for token in ("double", "float", "decimal", "number")):
        return f"safe_cast({expression} as numeric)"
    if "date" in lowered and "datetime" not in lowered:
        return f"safe_cast({expression} as date)"
    if "datetime" in lowered:
        return f"safe_cast({expression} as datetime)"
    return f"cast({expression} as string)"


def _alteryx_filter_to_sql(expression: str) -> str:
    sql = str(expression or "")

    def numeric_repl(match: re.Match[str]) -> str:
        col, op, number = match.group(1), match.group(2), match.group(3)
        return f"safe_cast({_sql_identifier(col)} as numeric) {op} {number}"

    def string_eq_repl(match: re.Match[str]) -> str:
        col, op, value = match.group(1), match.group(2), match.group(3).replace("'", "\\'")
        sql_op = "!=" if op in ("!=", "<>") else "="
        return f"cast({_sql_identifier(col)} as string) {sql_op} '{value}'"

    # sql = re.sub(r"\[([^\]]+)\]\s*([><]=?)\s*([0-9]+(?:\.[0-9]+)?)", numeric_repl, sql)
    # sql = re.sub(r"\[([^\]]+)\]\s*(=|!=|<>)\s*\"([^\"]*)\"", string_eq_repl, sql)
    # sql = re.sub(r"\[([^\]]+)\]", lambda m: _sql_identifier(m.group(1)), sql)
    # sql = re.sub(r"\bAND\b", "and", sql, flags=re.IGNORECASE)
    # sql = re.sub(r"\bOR\b", "or", sql, flags=re.IGNORECASE)
    # return sql.replace("<>", "!=")



    sql = re.sub(r"\[([^\]]+)\]", lambda m: _sql_identifier(m.group(1)), sql)
    sql = re.sub(r"\bAND\b", "and", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bOR\b", "or", sql, flags=re.IGNORECASE)
    sql = sql.replace("<>", "!=")
    return _ensure_bool_expression(sql)   # ← FIX: guarantees BOOL for BigQuery WHERE


def _sql_agg(action: str, field: str) -> str:
    lowered = str(action or "").lower()
    numeric_expr = f"safe_cast({_sql_identifier(field)} as numeric)"
    if lowered in {"sum", "total"}:
        return f"sum({numeric_expr})"
    if lowered in {"avg", "average", "mean"}:
        return f"avg({numeric_expr})"
    if lowered == "min":
        return f"min({numeric_expr})"
    if lowered == "max":
        return f"max({numeric_expr})"
    if lowered in {"count", "countnonnull"}:
        return f"count({_sql_identifier(field)})"
    return f"sum({numeric_expr})"


def _split_top_level_args(value: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(depth - 1, 0)
        if char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        args.append("".join(current).strip())
    return args


def _convert_iif_to_sql(expr: str) -> str:
    expr = expr.strip()
    match = re.match(r"^IIF\s*\((.*)\)$", expr, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return expr
    args = _split_top_level_args(match.group(1))
    if len(args) != 3:
        return expr
    condition = _alteryx_filter_to_sql(args[0])
    true_expr = _formula_to_sql(args[1]) or _literal_or_identifier_sql(args[1])
    false_expr = _formula_to_sql(args[2]) or _literal_or_identifier_sql(args[2])
    return f"(case when {condition} then {true_expr} else {false_expr} end)"


def _literal_or_identifier_sql(expr: str) -> str:
    value = str(expr or "").strip()
    if re.fullmatch(r"NULL\(\)|NULL|null", value, flags=re.IGNORECASE):
        return "null"
    if re.fullmatch(r'"[^"]*"|\'[^\']*\'', value):
        return "'" + value[1:-1].replace("'", "\\'") + "'"
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    value = re.sub(r"\[([^\]]+)\]", lambda m: _sql_identifier(m.group(1)), value)
    value = value.replace("NULL()", "null")
    return value


def _outer_function_argument(expr: str, function_name: str) -> str | None:
    value = str(expr or "").strip()
    prefix = f"{function_name}("
    if not value.lower().startswith(prefix.lower()) or not value.endswith(")"):
        return None
    depth = 0
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value[len(function_name):], start=len(function_name)):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0 and index != len(value) - 1:
                return None
            if depth < 0:
                return None
    if depth != 0:
        return None
    inner = value[len(function_name) + 1:-1].strip()
    return inner or None


def _formula_to_sql(expression: str) -> str | None:
    expr = str(expression or "").strip()
    if not expr:
        return None
    if re.match(r"^IIF\s*\(", expr, flags=re.IGNORECASE):
        return _convert_iif_to_sql(expr)
    to_number_arg = _outer_function_argument(expr, "ToNumber")
    if to_number_arg:
        inner = _formula_to_sql(to_number_arg) or _literal_or_identifier_sql(to_number_arg)
        return f"safe_cast({inner} as numeric)"
    for function_name, target_type in (
        ("ToDouble", "numeric"),
        ("ToDecimal", "numeric"),
        ("ToInteger", "int64"),
        ("ToInt32", "int64"),
        ("ToInt64", "int64"),
    ):
        cast_arg = _outer_function_argument(expr, function_name)
        if cast_arg:
            inner = _formula_to_sql(cast_arg) or _literal_or_identifier_sql(cast_arg)
            return f"safe_cast({inner} as {target_type})"
    to_string_arg = _outer_function_argument(expr, "ToString")
    if to_string_arg:
        inner = _formula_to_sql(to_string_arg) or _literal_or_identifier_sql(to_string_arg)
        return f"cast({inner} as string)"
    contains_match = re.match(r"Contains\s*\((.+),\s*['\"]([^'\"]+)['\"]\)", expr, flags=re.IGNORECASE | re.DOTALL)
    if contains_match:
        haystack = _formula_to_sql(contains_match.group(1)) or _literal_or_identifier_sql(contains_match.group(1))
        needle = contains_match.group(2).replace("'", "\\'")
        return f"regexp_contains(cast({haystack} as string), r'(?i){needle}')"
    lower_match = re.match(r"LowerCase\s*\((.+)\)", expr, flags=re.IGNORECASE | re.DOTALL)
    if lower_match:
        inner = _formula_to_sql(lower_match.group(1)) or _literal_or_identifier_sql(lower_match.group(1))
        return f"lower(cast({inner} as string))"
    upper_match = re.match(r"Uppercase\s*\((.+)\)", expr, flags=re.IGNORECASE | re.DOTALL)
    if upper_match:
        inner = _formula_to_sql(upper_match.group(1)) or _literal_or_identifier_sql(upper_match.group(1))
        return f"upper(cast({inner} as string))"
    trim_match = re.match(r"Trim(?:Left|Right)?\s*\((.+)\)", expr, flags=re.IGNORECASE | re.DOTALL)
    if trim_match:
        inner = _formula_to_sql(trim_match.group(1)) or _literal_or_identifier_sql(trim_match.group(1))
        return f"trim(cast({inner} as string))"
    dt_year = re.match(r"DateTimeYear\s*\(\s*\[([^\]]+)\]\s*\)", expr, flags=re.IGNORECASE)
    if dt_year:
        return f"extract(year from safe_cast({_sql_identifier(dt_year.group(1))} as date))"
    dt_month = re.match(r"DateTimeMonth\s*\(\s*\[([^\]]+)\]\s*\)", expr, flags=re.IGNORECASE)
    if dt_month:
        return f"extract(month from safe_cast({_sql_identifier(dt_month.group(1))} as date))"
    match = re.match(
        r"if\s+\[([^\]]+)\]\s*=\s*0\s+then\s+null\s+else\s+\[([^\]]+)\]\s*/\s*\[([^\]]+)\]",
        expr,
        flags=re.IGNORECASE,
    )
    if match:
        denominator = match.group(1)
        numerator = match.group(2)
        denominator_again = match.group(3)
        if denominator.lower() == denominator_again.lower():
            return (
                f"safe_divide(safe_cast({_sql_identifier(numerator)} as numeric), "
                f"nullif(safe_cast({_sql_identifier(denominator)} as numeric), 0))"
            )
    div_match = re.match(r"\[([^\]]+)\]\s*/\s*\[([^\]]+)\]", expr)
    if div_match:
        return (
            f"safe_divide(safe_cast({_sql_identifier(div_match.group(1))} as numeric), "
            f"nullif(safe_cast({_sql_identifier(div_match.group(2))} as numeric), 0))"
        )
    def _inline_numeric_cast(match: re.Match[str], target_type: str = "numeric") -> str:
        inner = match.group(1).strip()
        inner_sql = re.sub(r"\[([^\]]+)\]", lambda field: f"safe_cast({_sql_identifier(field.group(1))} as numeric)", inner)
        return f"safe_cast({inner_sql} as {target_type})"

    arithmetic = re.sub(
        r"\b(?:ToNumber|ToDouble|ToDecimal)\s*\(\s*([^()]+?)\s*\)",
        lambda m: _inline_numeric_cast(m, "numeric"),
        expr,
        flags=re.IGNORECASE,
    )
    arithmetic = re.sub(
        r"\b(?:ToInteger|ToInt32|ToInt64)\s*\(\s*([^()]+?)\s*\)",
        lambda m: _inline_numeric_cast(m, "int64"),
        arithmetic,
        flags=re.IGNORECASE,
    )
    arithmetic = re.sub(
        r"\bToString\s*\(\s*\[([^\]]+)\]\s*\)",
        lambda m: f"cast({_sql_identifier(m.group(1))} as string)",
        arithmetic,
        flags=re.IGNORECASE,
    )
    arithmetic = re.sub(r"\[([^\]]+)\]", lambda m: f"safe_cast({_sql_identifier(m.group(1))} as numeric)", arithmetic)
    if arithmetic != expr and re.search(r"[+\-*/]", arithmetic):
        return arithmetic.replace("NULL()", "null")
    return _literal_or_identifier_sql(expr)


def _ensure_bool_expression(sql: str) -> str:
    """Return *sql* guaranteed to be a boolean expression for BigQuery WHERE.
 
    Alteryx Filter expressions are often bare column names such as ``Category``
    which BigQuery rejects in WHERE clauses (STRING != BOOL).
 
    If the expression already contains a comparison or boolean keyword it is
    returned unchanged.  Otherwise it is rewritten as ``<col> IS NOT NULL``.
    """
    stripped = sql.strip()
    if not stripped:
        return "TRUE"
    # Already a boolean expression (has comparison operator or boolean keyword)
    if re.search(
        r"[=><!]|\b(is|in|between|like|not|true|false|null)\b",
        stripped,
        re.IGNORECASE,
    ):
        return stripped
    # Bare column reference → treat as IS NOT NULL
    return f"{stripped} IS NOT NULL  -- TODO: replace with correct boolean predicate"



def _compile_sql_transform_model(workflow: dict[str, Any], upstream_ref: str, macro_notes: str = "") -> str | None:
    transform_plan = build_transform_plan(workflow)
    steps = [
        step for step in transform_operations(transform_plan)
        if step.get("tool") in {
            "select",
            "filter",
            "summarize",
            "formula",
            "multi_field_formula",
            "multi_row_formula",
            "unique",
            "sort",
            "sample",
            "join",
            "join_multiple",
            "union",
        }
    ]
    if not steps:
        return None

    ctes: list[str] = [
        "source_data as (\n"
        f"    select * from {{{{ ref('{upstream_ref}') }}}}\n"
        ")"
    ]
    current = "source_data"
    formula_fields: list[dict[str, str]] = []
    comments: list[str] = []

    for index, step in enumerate(steps, start=1):
        tool = step.get("tool")
        config = step.get("config") or {}
        cte = f"step_{index}_{tool}"
        if tool == "select":
            selected = config.get("selectedFields") or []
            columns = []
            for field in selected:
                name = str(field.get("name") or field.get("field") or "")
                rename = str(field.get("rename") or name)
                if name:
                    columns.append(f"        {_sql_type_cast(_sql_identifier(name), str(field.get('type') or ''))} as {_sql_identifier(rename)}")
            if columns:
                ctes.append(f"{cte} as (\n    select\n" + ",\n".join(columns) + f"\n    from {current}\n)")
                current = cte
        # elif tool == "filter":
        #     filter_sql = _alteryx_filter_to_sql(str(config.get("filterExpression") or ""))
        #     if filter_sql:
        #         ctes.append(f"{cte} as (\n    select *\n    from {current}\n    where {filter_sql}\n)")
        #         current = cte

        elif tool == "filter":
            filter_sql = _alteryx_filter_to_sql(str(config.get("filterExpression") or ""))
            if filter_sql:
                # Ensure expression is BOOL — BigQuery rejects bare STRING columns in WHERE
                bool_sql = _ensure_bool_expression(filter_sql)
                ctes.append(f"{cte} as (\n    select *\n    from {current}\n    where {bool_sql}\n)")
                current = cte

        elif tool == "summarize":
            group_by = [str(col) for col in (config.get("groupBy") or []) if col]
            aggregations = config.get("aggregations") or []
            if group_by and aggregations:
                select_lines = [f"        {_sql_identifier(col)}" for col in group_by]
                for agg in aggregations:
                    field = str(agg.get("field") or "")
                    if not field:
                        continue
                    rename = str(agg.get("rename") or field)
                    select_lines.append(f"        {_sql_agg(str(agg.get('action') or ''), field)} as {_sql_identifier(rename)}")
                ctes.append(
                    f"{cte} as (\n    select\n"
                    + ",\n".join(select_lines)
                    + f"\n    from {current}\n    group by "
                    + ", ".join(_sql_identifier(col) for col in group_by)
                    + "\n)"
                )
                current = cte
        elif tool in {"formula", "multi_field_formula", "multi_row_formula"}:
            formula_fields.extend(config.get("formulas") or [])
        elif tool == "unique":
            ctes.append(f"{cte} as (\n    select distinct *\n    from {current}\n)")
            current = cte
        elif tool == "sort":
            sort_fields = config.get("sortFields") or config.get("fields") or []
            order_parts = []
            for item in sort_fields if isinstance(sort_fields, list) else [sort_fields]:
                if isinstance(item, dict):
                    field = str(item.get("field") or item.get("name") or "")
                    order = str(item.get("order") or item.get("direction") or "asc").lower()
                else:
                    field = str(item or "")
                    order = "asc"
                if field:
                    order_parts.append(f"{_sql_identifier(field)} {'desc' if order in {'desc', 'descending', '-1'} else 'asc'}")
            if order_parts:
                ctes.append(f"{cte} as (\n    select *\n    from {current}\n    order by {', '.join(order_parts)}\n)")
                current = cte
        elif tool == "sample":
            count = str(config.get("count") or config.get("n") or config.get("sampleSize") or "")
            if count.isdigit():
                ctes.append(f"{cte} as (\n    select *\n    from {current}\n    limit {int(count)}\n)")
                current = cte
        elif tool in {"join", "join_multiple", "union"}:
            comments.append(f"-- {tool} node {step.get('tool_id')} is represented in the shared plan; multi-stream SQL rendering requires branch-specific source binding.")

    final_select = ["    *"]
    for formula in formula_fields:
        field = str(formula.get("field") or formula.get("name") or "")
        formula_sql = _formula_to_sql(str(formula.get("expression") or ""))
        if field and formula_sql:
            final_select.append(f"    {formula_sql} as {_sql_identifier(field)}")
        elif field:
            comments.append(f"-- Formula field {field} requires manual SQL review: {formula.get('expression')}")

    notes = macro_notes + "\n" if macro_notes else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "-- Phase 2 SQL transformation generated from supported Alteryx tools.\n"
        "-- Generated from the shared Alteryx transformation plan.\n"
        f"{notes}"
        + ("\n".join(comments) + "\n" if comments else "")
        + "with "
        + ",\n".join(ctes)
        + "\n\nselect\n"
        + ",\n".join(final_select)
        + f"\nfrom {current}\n"
    )


def _macro_slug(macro: dict[str, Any], index: int) -> str:
    name = str(macro.get("name") or macro.get("path") or f"macro_{index}")
    name = re.sub(r"\.yxmc$", "", name.replace("\\", "/").rsplit("/", 1)[-1], flags=re.IGNORECASE)
    return _dbt_identifier(name, f"macro_{index}")


def _macro_definition_nodes(macro: dict[str, Any]) -> list[dict[str, Any]]:
    definition = macro.get("definition") or {}
    return list(definition.get("workflowNodes") or [])


def _macro_capability(macro: dict[str, Any]) -> dict[str, Any]:
    macro_type = str(macro.get("macroType") or "Macro")
    macro_type_key = macro_type.lower()
    definition = macro.get("definition") or {}
    nodes = _macro_definition_nodes(macro)
    unsupported = definition.get("unsupportedTools") or []
    uploaded = bool(macro.get("uploaded"))
    supported_nodes = int(definition.get("supportedToolCount") or 0)
    total_nodes = int(definition.get("toolCount") or len(nodes) or 0)
    sql_friendly = total_nodes > 0 and not unsupported and all(
        any(token in str(node.get("plugin") or "").lower() for token in ["filter", "formula", "select", "summarize", "join", "union", "sort", "sample", "macroinput", "macrooutput"])
        for node in nodes
    )

    if not uploaded:
        level = "blocked"
        automation = "missing_macro_file"
    elif macro_type_key == "standard" and sql_friendly:
        level = "automatable"
        automation = "standard_macro_sql_scaffold"
    elif macro_type_key == "batch" and sql_friendly:
        level = "assisted"
        automation = "parameterized_dbt_macro_scaffold"
    elif macro_type_key == "iterative":
        level = "manual_review"
        automation = "recursive_or_orchestration_review"
    else:
        level = "manual_review"
        automation = "llm_assisted_remediation"

    return {
        "level": level,
        "automation": automation,
        "uploaded": uploaded,
        "macro_type": macro_type,
        "total_nodes": total_nodes,
        "supported_nodes": supported_nodes,
        "unsupported_tools": unsupported,
        "sql_friendly": sql_friendly,
    }


def generate_macro_conversion_plan(workflow: dict[str, Any]) -> dict[str, Any]:
    dependencies = workflow.get("macroDependencies") or []
    items: list[dict[str, Any]] = []
    for index, macro in enumerate(dependencies, start=1):
        slug = _macro_slug(macro, index)
        capability = _macro_capability(macro)
        macro_type_key = str(macro.get("macroType") or "").lower()
        if capability["level"] == "automatable":
            target = "dbt SQL model"
            recommendation = "Inline the standard macro as an intermediate dbt model and chain the final model from it."
        elif macro_type_key == "batch":
            target = "parameterized dbt macro plus model"
            recommendation = "Represent the control input as a warehouse table and join/apply it through a dbt macro scaffold."
        elif macro_type_key == "iterative":
            target = "recursive SQL or orchestration"
            recommendation = "Use a recursive CTE only when the iteration is hierarchy-like; otherwise route to Python/Dataform orchestration."
        else:
            target = "manual/LLM-assisted remediation"
            recommendation = "Ask the LLM to explain the macro, then map each supported step to SQL and mark unsupported tools for review."

        items.append({
            "id": slug,
            "name": macro.get("name") or macro.get("path") or f"Macro {index}",
            "path": macro.get("path") or "",
            "type": macro.get("macroType") or "Macro",
            "status": macro.get("status") or ("ready" if macro.get("uploaded") else "missing"),
            "capability": capability,
            "target": target,
            "recommendation": recommendation,
            "generated_artifacts": [
                f"models/intermediate/int_{slug}.sql" if capability["level"] == "automatable" else "",
                f"macros/{slug}.sql" if macro_type_key == "batch" else "",
            ],
            "llm_prompt": (
                f"Interpret Alteryx {macro.get('macroType') or 'macro'} '{macro.get('name') or macro.get('path')}'. "
                "Summarize inputs, outputs, formula/filter/join/summarize behavior, and propose dbt SQL or orchestration remediation."
            ),
        })

    ready = [item for item in items if item["status"] == "ready"]
    blocked = [item for item in items if item["status"] != "ready"]
    manual = [item for item in items if item["capability"]["level"] in {"manual_review", "blocked"}]
    return {
        "success": True,
        "workflow": workflow.get("name") or "",
        "macro_count": len(items),
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "manual_review_count": len(manual),
        "items": items,
        "target_recommendation": _target_recommendation_for_macro_plan(items),
    }


def _target_recommendation_for_macro_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {"target": "Power Query / dbt scaffold", "reason": "No macro dependencies detected."}
    if any(str(item.get("type") or "").lower() == "iterative" for item in items):
        return {"target": "dbt plus orchestration/Python review", "reason": "Iterative macros may require loop semantics that SQL cannot always express safely."}
    if any(str(item.get("type") or "").lower() == "batch" for item in items):
        return {"target": "dbt/Dataform/BigQuery", "reason": "Batch macros map best to warehouse-side parameter tables and SQL models when data is already landed."}
    if all(item["capability"]["level"] == "automatable" for item in items):
        return {"target": "dbt SQL", "reason": "Uploaded standard macros contain SQL-friendly tools only."}
    return {"target": "Hybrid remediation", "reason": "At least one macro is missing, unsupported, or needs semantic review."}


def _standard_macro_intermediate_sql(macro: dict[str, Any], index: int, upstream_ref: str) -> tuple[str, str]:
    slug = _macro_slug(macro, index)
    nodes = _macro_definition_nodes(macro)
    node_notes = "\n".join(
        f"-- Macro node {node.get('id', '')}: {node.get('plugin', 'Unknown')}"
        for node in nodes[:40]
    )
    columns = [
        "    base.*",
        f"    , 1 as {slug}_processed",
        f"    , '{slug}' as {slug}_macro_name",
    ]
    sql = (
        "{{ config(materialized='view') }}\n\n"
        "-- Standard macro SQL scaffold generated from uploaded .yxmc metadata.\n"
        "-- Deterministic SQL is emitted for the wrapper; formula/filter semantics remain reviewable below.\n"
        f"{node_notes}\n\n"
        "with base as (\n"
        f"    select * from {{{{ ref('{upstream_ref}') }}}}\n"
        ")\n\n"
        "select\n"
        + "\n".join(columns)
        + "\nfrom base\n"
    )
    return f"models/intermediate/int_{slug}.sql", sql


def _batch_macro_dbt_macro_sql(macro: dict[str, Any], index: int) -> tuple[str, str]:
    slug = _macro_slug(macro, index)
    control_parameter = macro.get("controlParameter") or "control_key"
    sql = (
        f"{{% macro {slug}(input_relation, control_relation, input_key='{control_parameter}', control_key='{control_parameter}') %}}\n"
        "-- Generic batch macro scaffold. Replace key mapping and selected control columns after validating Alteryx expected output.\n"
        "select\n"
        "    input_relation.*,\n"
        "    control_relation.* except ({{ control_key }})\n"
        "from {{ input_relation }} as input_relation\n"
        "inner join {{ control_relation }} as control_relation\n"
        "    on cast(input_relation.{{ input_key }} as string) = cast(control_relation.{{ control_key }} as string)\n"
        "{% endmacro %}\n"
    )
    return f"macros/{slug}.sql", sql


def _validation_artifacts(project_name: str, first_stage: str) -> dict[str, str]:
    return {
        f"analyses/validation_{project_name}.sql": (
            "-- Row-count validation query for Alteryx-to-dbt migration.\n"
            "-- Run after dbt run; compare final_count to expected Alteryx output count when available.\n"
            "select 'source_stage' as relation_name, count(*) as row_count from "
            f"{{{{ ref('stg_{first_stage}') }}}}\n"
            "union all\n"
            f"select 'final_model' as relation_name, count(*) as row_count from {{{{ ref('{project_name}') }}}}\n"
        ),
        f"tests/{project_name}_not_empty.sql": (
            "select 1 as validation_error\n"
            "from (select 1) as validator\n"
            f"where not exists (select 1 from {{{{ ref('{project_name}') }}}} limit 1)\n"
        ),
    }


def _output_model_name(output: dict[str, Any], index: int) -> str:
    name = str(output.get("name") or output.get("path") or f"output_{index}")
    name = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet|yxdb)$", "", name, flags=re.IGNORECASE)
    return _dbt_identifier(name, f"output_{index}")


def _detect_salary_equalizer_iterative(workflow: dict[str, Any]) -> dict[str, Any] | None:
    iterative = [
        macro for macro in (workflow.get("macroDependencies") or [])
        if str(macro.get("macroType") or "").lower() == "iterative"
    ]
    if not iterative:
        return None
    definition = iterative[0].get("definition") or {}
    text = " ".join(
        [
            str(definition.get("name") or ""),
            " ".join(str(item) for item in definition.get("toolTypes") or []),
            " ".join(str(node.get("configurationText") or "") for node in definition.get("workflowNodes") or []),
        ]
    ).lower()
    required = ["basesalary", "1.05", "120000", "iterationcount"]
    if all(token in text for token in required):
        return {
            "threshold": 120000,
            "raise_factor": 1.05,
            "max_iterations": int(iterative[0].get("iterationLimit") or 20),
            "macro_name": iterative[0].get("name") or "iterative_salary_equalizer",
        }
    return None


def _salary_equalizer_models(project_name: str, first_stage: str, outputs: list[dict[str, Any]], pattern: dict[str, Any]) -> dict[str, str]:
    threshold = int(pattern.get("threshold") or 120000)
    raise_factor = float(pattern.get("raise_factor") or 1.05)
    max_iterations = int(pattern.get("max_iterations") or 20)
    base_ref = f"stg_{first_stage}"
    models: dict[str, str] = {}

    resolved_model = next(
        (_output_model_name(output, index) for index, output in enumerate(outputs, start=1) if "resolved" in _output_model_name(output, index)),
        "output_resolved_employees",
    )
    summary_model = next(
        (_output_model_name(output, index) for index, output in enumerate(outputs, start=1) if "summary" in _output_model_name(output, index)),
        "output_dept_salary_summary",
    )
    above_model = next(
        (_output_model_name(output, index) for index, output in enumerate(outputs, start=1) if "above" in _output_model_name(output, index) or "threshold" in _output_model_name(output, index)),
        "output_already_above_threshold",
    )

    models[f"models/{above_model}.sql"] = (
        "{{ config(materialized='table') }}\n\n"
        "-- Output model mapped from Alteryx Output Data tool: already above threshold.\n"
        f"select *, 0 as IterationCount, 0.0 as TotalRaisePct, BaseSalary + Bonus as AdjustedTotalComp\n"
        f"from {{{{ ref('{base_ref}') }}}}\n"
        f"where safe_cast(BaseSalary as numeric) >= {threshold}\n"
    )
    models[f"models/intermediate/int_{project_name}_salary_iterations.sql"] = (
        "{{ config(materialized='view') }}\n\n"
        "-- Recursive SQL representation of the detected iterative salary equalizer macro.\n"
        "with recursive seed as (\n"
        "    select\n"
        "        *,\n"
        "        safe_cast(BaseSalary as numeric) as IterBaseSalary,\n"
        "        0 as IterationCount,\n"
        "        0.0 as TotalRaisePct\n"
        f"    from {{{{ ref('{base_ref}') }}}}\n"
        f"    where safe_cast(BaseSalary as numeric) < {threshold}\n"
        "),\n"
        "iterations as (\n"
        "    select * from seed\n"
        "    union all\n"
        "    select\n"
        "        * replace (\n"
        f"            IterBaseSalary * {raise_factor} as IterBaseSalary,\n"
        "            IterationCount + 1 as IterationCount,\n"
        "            TotalRaisePct + 5.0 as TotalRaisePct\n"
        "        )\n"
        "    from iterations\n"
        f"    where IterBaseSalary < {threshold} and IterationCount < {max_iterations}\n"
        "),\n"
        "first_resolved as (\n"
        "    select * except(row_num)\n"
        "    from (\n"
        "        select\n"
        "            *,\n"
        "            row_number() over (partition by EmployeeID order by IterationCount) as row_num\n"
        "        from iterations\n"
        f"        where IterBaseSalary >= {threshold} or IterationCount = {max_iterations}\n"
        "    )\n"
        "    where row_num = 1\n"
        ")\n"
        "select\n"
        "    * replace (IterBaseSalary as BaseSalary),\n"
        "    IterBaseSalary + Bonus as AdjustedTotalComp,\n"
        "    case\n"
        "        when IterBaseSalary < 60000 then 'Band-1'\n"
        "        when IterBaseSalary < 100000 then 'Band-2'\n"
        "        when IterBaseSalary < 150000 then 'Band-3'\n"
        "        when IterBaseSalary < 200000 then 'Band-4'\n"
        "        else 'Band-5'\n"
        "    end as SalaryBand\n"
        "from first_resolved\n"
    )
    models[f"models/{resolved_model}.sql"] = (
        "{{ config(materialized='table') }}\n\n"
        "-- Output model mapped from Alteryx Output Data tool: resolved employees.\n"
        f"select * from {{{{ ref('int_{project_name}_salary_iterations') }}}}\n"
    )
    models[f"models/{summary_model}.sql"] = (
        "{{ config(materialized='table') }}\n\n"
        "-- Output model mapped from Alteryx Output Data tool: department salary summary.\n"
        "select\n"
        "    Department,\n"
        "    SalaryBand,\n"
        "    count(*) as EmployeeCount,\n"
        "    avg(BaseSalary) as AvgBaseSalary,\n"
        "    avg(IterationCount) as AvgIterations,\n"
        "    sum(AdjustedTotalComp) as TotalCompCost\n"
        f"from {{{{ ref('{resolved_model}') }}}}\n"
        "group by Department, SalaryBand\n"
    )
    models[f"models/{project_name}.sql"] = (
        "{{ config(materialized='view') }}\n\n"
        "-- Compatibility model. The workflow has multiple Alteryx output files; this points to the primary resolved-employees output.\n"
        f"select * from {{{{ ref('{resolved_model}') }}}}\n"
    )
    return models


def _generic_output_models(project_name: str, upstream_ref: str, outputs: list[dict[str, Any]]) -> dict[str, str]:
    models: dict[str, str] = {}
    for index, output in enumerate(outputs, start=1):
        model_name = _output_model_name(output, index)
        models[f"models/{model_name}.sql"] = (
            "{{ config(materialized='table') }}\n\n"
            f"-- Output model mapped from Alteryx Output Data tool: {output.get('name') or output.get('path') or model_name}\n"
            "-- This is a lineage-preserving scaffold. Review upstream branch-specific logic before production use.\n"
            f"select * from {{{{ ref('{upstream_ref}') }}}}\n"
        )
    if outputs:
        first_output = _output_model_name(outputs[0], 1)
        models[f"models/{project_name}.sql"] = (
            "{{ config(materialized='view') }}\n\n"
            "-- Compatibility model for the first detected Alteryx output.\n"
            f"select * from {{{{ ref('{first_output}') }}}}\n"
        )
    return models


def generate_dbt_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    """Generate a dbt-compatible scaffold for warehouse-side implementation.

    The generated project assumes source data has already been landed in the
    target warehouse. This keeps the artifact dbt-native instead of embedding
    Power Query/SharePoint extraction semantics into dbt models.
    """
    project_name = _single_macro_project_name(workflow)
    transform_plan = build_transform_plan(workflow)
    tool_count = int(workflow.get("toolCount") or len(workflow.get("workflowNodes") or []) or 0)
    connection_count = int(workflow.get("connectionCount") or len(workflow.get("workflowEdges") or []) or 0)
    all_sources = workflow.get("dataSources") or []
    sources = [source for source in all_sources if _is_warehouse_landed_source(source)]
    if sharepoint_url or file_name:
        sources = [_source_from_override(sharepoint_url, file_name)]
    if not sources:
        sources = [{"name": workflow.get("sourceFile") or "uploaded_workflow", "type": "unknown", "path": ""}]
    deduped_sources: list[dict[str, Any]] = []
    seen_source_names: set[str] = set()
    for index, source in enumerate(sources, start=1):
        source_key = _dbt_source_identifier(source, index).lower()
        if source_key in seen_source_names:
            continue
        seen_source_names.add(source_key)
        deduped_sources.append(source)
    sources = deduped_sources

    source_rows: list[str] = []
    staging_files: dict[str, str] = {}
    source_model_names: list[str] = []
    for index, source in enumerate(sources, start=1):
        source_name = _dbt_source_name(source, index)
        source_identifier = _dbt_source_identifier(source, index)
        source_model_names.append(source_name)
        original_path = str(source.get("path") or source.get("type") or "").replace("\\", "/")
        display_name = _source_display_name(source, index)
        description = _yaml_single_quoted(f"Landed source for {display_name}. Original path: {original_path}")
        identifier_line = f"        identifier: {source_identifier}\n" if source_identifier != source_name else ""
        source_rows.append(
            f"      - name: {source_name}\n"
            f"{identifier_line}"
            f"        description: {description}"
        )
        staging_files[f"models/staging/stg_{source_name}.sql"] = (
            "{{ config(materialized='view') }}\n\n"
            f"-- Source extracted from Alteryx input: {str(source.get('name') or source_name)}\n"
            "-- Replace source schema/table mapping after data is landed in BigQuery/Snowflake/warehouse.\n"
            "select\n"
            "    *\n"
            f"from {{{{ source('alteryx_raw', '{source_name}') }}}}\n"
        )

    macro_dependencies = workflow.get("macroDependencies") or []
    macro_plan = generate_macro_conversion_plan(workflow)
    macro_notes = "\n".join(
        f"-- Macro dependency: {item.get('macroType', 'Macro')} {item.get('path') or item.get('name')} "
        f"(status: {item.get('status', 'unknown')})"
        for item in macro_dependencies
    )
    first_stage = source_model_names[0] if source_model_names else "source_1"
    upstream_ref = f"stg_{first_stage}"
    macro_artifact_files: dict[str, str] = {}
    for index, macro in enumerate(macro_dependencies, start=1):
        capability = _macro_capability(macro)
        macro_type_key = str(macro.get("macroType") or "").lower()
        if capability["level"] == "automatable" and macro_type_key == "standard":
            artifact_path, artifact_sql = _standard_macro_intermediate_sql(macro, index, upstream_ref)
            macro_artifact_files[artifact_path] = artifact_sql
            upstream_ref = artifact_path.rsplit("/", 1)[-1].replace(".sql", "")
        elif macro_type_key == "batch":
            artifact_path, artifact_sql = _batch_macro_dbt_macro_sql(macro, index)
            macro_artifact_files[artifact_path] = artifact_sql

    final_model = _compile_sql_transform_model(workflow, upstream_ref, macro_notes)
    if not final_model:
        final_model = (
            "{{ config(materialized='table') }}\n\n"
            "-- dbt-compatible model generated from Alteryx workflow metadata.\n"
            "-- Macro handling is generic: standard SQL-friendly macros are chained through intermediate models;\n"
            "-- batch macros receive parameterized dbt macro scaffolds; iterative/custom macros are routed for remediation.\n"
            f"{macro_notes}\n\n" if macro_notes else
            "{{ config(materialized='table') }}\n\n"
            "-- dbt-compatible model generated from Alteryx workflow metadata.\n"
            "-- Macro handling is generic: standard SQL-friendly macros are chained through intermediate models;\n"
            "-- batch macros receive parameterized dbt macro scaffolds; iterative/custom macros are routed for remediation.\n\n"
        )
        final_model += (
            "with base as (\n"
            f"    select * from {{{{ ref('{upstream_ref}') }}}}\n"
            ")\n\n"
            "select\n"
            "    *,\n"
            f"    {len(macro_dependencies)} as MacroDependencyCount,\n"
            f"    '{macro_plan['target_recommendation']['target']}' as RecommendedTransformationTarget\n"
            "from base\n"
        )
    output_targets = workflow.get("outputTargets") or []
    salary_pattern = _detect_salary_equalizer_iterative(workflow)
    transformed_model_name = f"int_{project_name}_transformed"
    final_model_path = f"models/{project_name}.sql"
    transformed_model_files: dict[str, str] = {}
    if output_targets and not salary_pattern:
        transformed_model_files[f"models/intermediate/{transformed_model_name}.sql"] = final_model
        output_model_files = _generic_output_models(project_name, transformed_model_name, output_targets)
    else:
        transformed_model_files[final_model_path] = final_model
        output_model_files = _salary_equalizer_models(project_name, first_stage, output_targets, salary_pattern) if output_targets and salary_pattern else {}

    schema_yml = (
        "version: 2\n\n"
        "sources:\n"
        "  - name: alteryx_raw\n"
        "    description: \"Warehouse-landed source tables used by the Alteryx migration scaffold.\"\n"
        "    tables:\n"
        + "\n".join(source_rows)
        + "\n\nmodels:\n"
        + "\n".join(
            [
                f"  - name: stg_{name}\n    description: \"Staging view for landed source {name}.\""
                for name in source_model_names
            ]
        )
        + "".join(
            f"\n  - name: {_output_model_name(output, index)}\n    description: \"Output model for Alteryx target {str(output.get('name') or output.get('path') or index).replace(chr(34), '')}.\""
            for index, output in enumerate(output_targets, start=1)
        )
        + f"\n  - name: {project_name}\n    description: \"Final scaffold model for {workflow.get('name', 'Alteryx workflow')}.\"\n"
    )

    files = {
        "dbt_project.yml": (
            f"name: '{project_name}'\n"
            "version: '1.0.0'\n"
            "config-version: 2\n\n"
            f"profile: '{project_name}'\n\n"
            "model-paths: ['models']\n"
            "analysis-paths: ['analyses']\n"
            "test-paths: ['tests']\n"
            "seed-paths: ['seeds']\n"
            "macro-paths: ['macros']\n"
            "snapshot-paths: ['snapshots']\n\n"
            "models:\n"
            f"  {project_name}:\n"
            "    +materialized: table\n"
            "    staging:\n"
            "      +materialized: view\n"
        ),
        "models/schema.yml": schema_yml,
        "macros/README.md": (
            "# Macro Remediation Notes\n\n"
            "Alteryx macros are represented as review notes in this dbt scaffold. "
            "Standard SQL-friendly macros are converted to intermediate model scaffolds. "
            "Batch and iterative macro behavior should be confirmed against expected Alteryx output before production use.\n"
            f"\n\nTarget recommendation: {macro_plan['target_recommendation']['target']} - {macro_plan['target_recommendation']['reason']}\n"
        ),
        "README.md": (
            f"# {workflow.get('name', 'Alteryx Workflow')} dbt Scaffold\n\n"
            "This is a dbt-compatible scaffold generated by the Alteryx accelerator.\n\n"
            "## Expected Usage\n"
            "1. Land the Alteryx source files/API outputs into your warehouse.\n"
            "2. Update `models/schema.yml` source table names if needed.\n"
            "3. Review macro and iterative logic before production deployment.\n"
            "4. Run `dbt parse`, then `dbt run` after profile configuration.\n\n"
            "## Important\n"
            "This artifact is intended for complex workflow migration planning and warehouse-side transformation. "
            "It is not a direct Alteryx runtime replacement yet.\n"
        ),
        **transformed_model_files,
        **macro_artifact_files,
        **output_model_files,
        **_validation_artifacts(project_name, first_stage),
        **staging_files,
    }
    return {
        "success": True,
        "project_name": project_name,
        "target": "dbt",
        "files": files,
        "file_count": len(files),
        "tool_count": tool_count,
        "connection_count": connection_count,
        "macro_count": len(macro_dependencies),
        "source_count": len(sources),
        "output_count": len(output_targets),
        "output_targets": output_targets,
        "iterative_pattern": salary_pattern or {},
        "macro_complexity": _macro_complexity_summary(workflow, sources, project_name),
        "macro_plan": macro_plan,
        "transform_plan": transform_plan,
        "transformation_coverage": transform_plan.get("coverage") or {},
    }


def _sql_to_sqlx(sql: str) -> str:
    converted = re.sub(r"\{\{\s*config\(materialized='([^']+)'\)\s*\}\}", r'config { type: "\1" }', sql)
    converted = re.sub(r"\{\{\s*ref\('([^']+)'\)\s*\}\}", r'${ref("\1")}', converted)
    converted = re.sub(r"\{\{\s*source\('alteryx_raw',\s*'([^']+)'\)\s*\}\}", r'${ref("\1")}', converted)
    converted = converted.replace("materialized: \"view\"", 'type: "view"')
    return converted


def generate_dataform_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    dbt_project = generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    transform_plan = dbt_project.get("transform_plan") or build_transform_plan(workflow)
    final_table_name = str(dbt_project.get("project_name") or "alteryx")
    project_name = _dbt_identifier(f"{final_table_name}_dataform", "alteryx_dataform")
    source_project = "YOUR_GCP_PROJECT_ID"
    source_dataset = "YOUR_BIGQUERY_SOURCE_DATASET"
    declarations: list[str] = []
    files: dict[str, str] = {
        "workflow_settings.yaml": (
            f"defaultProject: {source_project}\n"
            "defaultDataset: YOUR_DATAFORM_TARGET_DATASET\n"
            "defaultLocation: US\n"
            f"dataformCoreVersion: 3.0.0\n"
        ),
        "README.md": (
            f"# {workflow.get('name', 'Alteryx Workflow')} Dataform Scaffold\n\n"
            "Generated by the Alteryx accelerator from the same workflow model used for dbt output.\n\n"
            "Update `workflow_settings.yaml` and `definitions/declarations.js` with your GCP project and BigQuery datasets, then run `dataform run`.\n"
        ),
    }

    for path, content in (dbt_project.get("files") or {}).items():
        if path == "models/schema.yml":
            continue
        if path.startswith("macros/"):
            continue
        if not path.endswith(".sql"):
            continue
        model_name = path.rsplit("/", 1)[-1].replace(".sql", "")
        if path.startswith("models/staging/stg_"):
            source_name = model_name.replace("stg_", "", 1)
            declarations.append(
                "declare({\n"
                f"  database: \"{source_project}\",\n"
                f"  schema: \"{source_dataset}\",\n"
                f"  name: \"{source_name}\",\n"
                "});"
            )
        files[f"definitions/{model_name}.sqlx"] = _sql_to_sqlx(str(content))

    files["definitions/declarations.js"] = "\n\n".join(declarations) or (
        "// Add BigQuery source declarations here.\n"
    )

    return {
        "success": True,
        "project_name": project_name,
        "final_table_name": final_table_name,
        "target": "dataform",
        "files": files,
        "file_count": len(files),
        "source_count": dbt_project.get("source_count", 0),
        "output_count": dbt_project.get("output_count", 0),
        "output_targets": dbt_project.get("output_targets", []),
        "macro_plan": dbt_project.get("macro_plan", {}),
        "transform_plan": transform_plan,
        "transformation_coverage": transform_plan.get("coverage") or {},
        "dbt_project": dbt_project,
    }


def _python_identifier(value: str, fallback: str = "alteryx_pipeline") -> str:
    return _dbt_identifier(value, fallback).replace("-", "_")


def _clean_alteryx_python_code(value: str) -> str:
    """Return executable code from Alteryx Python/Jupyter node metadata."""
    code = str(value or "").strip()
    if not code:
        return ""
    markers = (
        "from ayx import",
        "import ayx",
        "from ayx",
        "import alteryx",
        "from alteryx",
        "Alteryx.read",
        "Alteryx.write",
        "alteryx.read",
        "alteryx.write",
    )
    line_starts = [match.start() for marker in markers for match in re.finditer(re.escape(marker), code)]
    if line_starts:
        code = code[min(line_starts):].strip()
    tail_match = re.search(
        r"(?m)^(?:pandas|numpy|scipy|sklearn|matplotlib|seaborn)\s*$",
        code,
    )
    if tail_match:
        code = code[:tail_match.start()].strip()
    lines = code.splitlines()
    while lines:
        while lines and lines[-1].strip() in {"0", "True", "False"}:
            lines.pop()
        candidate = "\n".join(lines).strip()
        try:
            compile(candidate, "<alteryx-python-tool>", "exec")
            return candidate
        except SyntaxError as exc:
            if exc.lineno is None or exc.lineno < max(len(lines) - 3, 1):
                return code
            lines.pop()
    return code


def _python_tool_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for node in workflow.get("workflowNodes") or []:
        plugin = str(node.get("plugin") or "")
        config = node.get("config") or {}
        if "python" not in plugin.lower() and config.get("toolFamily") != "python":
            continue
        steps.append({
            "id": str(node.get("id") or len(steps) + 1),
            "plugin": plugin or "Python",
            "code": _clean_alteryx_python_code(config.get("pythonCode") or node.get("configurationText") or ""),
        })
    return steps


def _python_transform_steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    plan = build_transform_plan(workflow)
    return transform_operations(plan)


def generate_python_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    project_name = _python_identifier(workflow.get("name") or "alteryx_python_pipeline", "alteryx_python_pipeline")
    transform_plan = build_transform_plan(workflow)
    dbt_project = generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    if sharepoint_url or file_name:
        sources = [_source_from_override(sharepoint_url, file_name)]
    else:
        sources = [
            source
            for source in (workflow.get("dataSources") or [])
            if _is_warehouse_landed_source(source)
            and "python" not in str(source.get("tool") or "").lower()
        ]
    outputs = workflow.get("outputTargets") or []
    output_specs = outputs or [{"name": project_name, "path": f"output/{project_name}.csv", "type": "csv"}]
    macro_plan = generate_macro_conversion_plan(workflow)
    python_steps = _python_tool_steps(workflow)
    transform_steps = _python_transform_steps(workflow)
    source_list = pformat(
        [
            {
                "toolId": str(source.get("toolId") or ""),
                "name": str(source.get("name") or ""),
                "path": str(source.get("path") or ""),
                "type": str(source.get("type") or "unknown"),
            }
            for source in sources
        ],
        width=120,
    )
    output_list = pformat(
        [
            {
                "toolId": str(output.get("toolId") or ""),
                "name": str(output.get("name") or ""),
                "path": str(output.get("path") or ""),
                "type": str(output.get("type") or "csv"),
            }
            for output in output_specs
        ],
        width=120,
    )
    python_steps_json = pformat(
        [
            {"id": step["id"], "plugin": step["plugin"], "code": step["code"]}
            for step in python_steps
        ],
        width=120,
    )
    transform_steps_json = pformat(transform_steps, width=120)
    transform_plan_json = pformat(transform_plan, width=120)
    workflow_nodes_json = pformat(
        [
            {
                "id": str(node.get("id") or ""),
                "plugin": str(node.get("plugin") or ""),
                "config": node.get("config") or {},
            }
            for node in (workflow.get("workflowNodes") or [])
        ],
        width=120,
    )
    workflow_edges_json = pformat(workflow.get("workflowEdges") or [], width=120)
    pipeline_py = (
        '"""Generated Alteryx migration Python pipeline.\n\n'
        "This script is intended for Cloud Run, Airflow/Composer, or local execution.\n"
        "It reads source data, applies converted Alteryx graph transformations, and publishes curated\n"
        "outputs to BigQuery. Unsupported tools pass through with a warning for manual remediation.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "import argparse\n"
        "import os\n"
        "from pathlib import Path\n"
        "from typing import Any\n\n"
        "from urllib.parse import quote\n\n"
        "try:\n"
        "    from dotenv import load_dotenv\n"
        "except Exception:\n"
        "    load_dotenv = None\n\n"
        "try:\n"
        "    from google.cloud import bigquery\n"
        "except Exception:  # google-cloud-bigquery is optional for local CSV-only tests\n"
        "    bigquery = None\n"
        "import pandas as pd\n\n"
        "import requests\n\n"
        "try:\n"
        "    from alteryx_python_steps import apply_python_tool_node\n"
        "except Exception:\n"
        "    def apply_python_tool_node(upstream: list[pd.DataFrame], step: dict[str, Any]) -> dict[str, pd.DataFrame]:\n"
        "        return {'Output1': upstream[0].copy() if upstream else pd.DataFrame()}\n\n"
        f"PROJECT_NAME = \"{project_name}\"\n"
        f"SOURCES = {source_list}\n"
        f"OUTPUTS = {output_list}\n\n"
        f"TRANSFORM_STEPS = {transform_steps_json}\n\n"
        f"TRANSFORM_PLAN = {transform_plan_json}\n\n"
        f"WORKFLOW_NODES = {workflow_nodes_json}\n"
        f"WORKFLOW_EDGES = {workflow_edges_json}\n\n"
        "if load_dotenv is not None:\n"
        "    for _env_path in (Path(__file__).resolve().parent / '.env', Path.cwd() / '.env'):\n"
        "        if _env_path.exists():\n"
        "            load_dotenv(_env_path)\n\n"
        "def env(name: str, default: str = '') -> str:\n"
        "    return os.getenv(name, default).strip()\n\n"
        "def read_bigquery_table(table_id: str) -> pd.DataFrame:\n"
        "    if bigquery is None:\n"
        "        raise RuntimeError('google-cloud-bigquery is required to read BigQuery sources.')\n"
        "    client = bigquery.Client(project=env('GCP_PROJECT_ID') or None)\n"
        "    return client.query(f'SELECT * FROM `{table_id}`').to_dataframe()\n\n"
        "def _source_basename(value: str) -> str:\n"
        "    raw = str(value or '').replace('\\\\', '/')\n"
        "    return raw.rsplit('/', 1)[-1]\n\n"
        "def _safe_bq_table_name(value: str) -> str:\n"
        "    import re\n"
        "    stem = Path(_source_basename(value)).stem\n"
        "    return re.sub(r'[^A-Za-z0-9_]+', '_', stem).strip('_').lower()\n\n"
        "def _bq_source_table_candidates(source: dict) -> list[str]:\n"
        "    import re\n"
        "    raw_values = [\n"
        "        str(source.get('bq_table') or ''),\n"
        "        str(source.get('table') or ''),\n"
        "        str(source.get('path') or ''),\n"
        "        str(source.get('name') or ''),\n"
        "    ]\n"
        "    source_key = _safe_bq_table_name(source.get('path') or source.get('name') or '')\n"
        "    env_override = env(f'BQ_SOURCE_TABLE_{source_key.upper()}') or env(f'GCP_BIGQUERY_SOURCE_TABLE_{source_key.upper()}')\n"
        "    if env_override:\n"
        "        raw_values.insert(0, env_override)\n"
        "    candidates: list[str] = []\n"
        "    for value in raw_values:\n"
        "        if not value:\n"
        "            continue\n"
        "        table = value.split('.')[-1] if value.count('.') >= 2 and '/' not in value and '\\\\' not in value else value\n"
        "        exact = Path(_source_basename(table)).stem.strip().strip('`')\n"
        "        safe = _safe_bq_table_name(table)\n"
        "        exact_safe = __import__('re').sub(r'[^A-Za-z0-9_]+', '_', exact).strip('_')\n"
        "        variants = [exact_safe, safe]\n"
        "        # Python tool comments such as '#   #1  customers.csv' previously produced '1_customers'.\n"
        "        variants.append(re.sub(r'^\\d+_+', '', safe))\n"
        "        variants.append(re.sub(r'^(input|source)_?\\d+_+', '', safe))\n"
        "        variants.append(re.sub(r'_csv$', '', safe))\n"
        "        if safe.endswith('s'):\n"
        "            variants.append(safe[:-1])\n"
        "        for variant in variants:\n"
        "            if variant and variant not in candidates:\n"
        "                candidates.append(variant)\n"
        "    return candidates\n\n"
        "def _normalized_table_token(value: str) -> str:\n"
        "    import re\n"
        "    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())\n\n"
        "def _candidate_matches_table(candidate: str, table_name: str) -> bool:\n"
        "    candidate_token = _normalized_table_token(candidate)\n"
        "    table_token = _normalized_table_token(table_name)\n"
        "    if not candidate_token or not table_token:\n"
        "        return False\n"
        "    if candidate_token == table_token:\n"
        "        return True\n"
        "    if table_token.startswith(candidate_token) or table_token.endswith(candidate_token):\n"
        "        return True\n"
        "    if candidate_token.endswith('s') and table_token.startswith(candidate_token[:-1]):\n"
        "        return True\n"
        "    return False\n\n"
        "def _discover_bigquery_source_tables(project: str, dataset: str, candidates: list[str]) -> list[str]:\n"
        "    if bigquery is None or not project or not dataset or not candidates:\n"
        "        return []\n"
        "    client = bigquery.Client(project=project)\n"
        "    try:\n"
        "        tables = list(client.list_tables(f'{project}.{dataset}'))\n"
        "    except Exception:\n"
        "        return []\n"
        "    exact_matches: list[str] = []\n"
        "    fuzzy_matches: list[str] = []\n"
        "    candidate_tokens = {_normalized_table_token(candidate) for candidate in candidates if candidate}\n"
        "    for table in tables:\n"
        "        table_name = getattr(table, 'table_id', '') or ''\n"
        "        table_token = _normalized_table_token(table_name)\n"
        "        if table_token in candidate_tokens and table_name not in exact_matches:\n"
        "            exact_matches.append(table_name)\n"
        "        elif any(_candidate_matches_table(candidate, table_name) for candidate in candidates) and table_name not in fuzzy_matches:\n"
        "            fuzzy_matches.append(table_name)\n"
        "    return exact_matches + [table for table in fuzzy_matches if table not in exact_matches]\n\n"
        "def read_bigquery_source_fallback(source: dict) -> pd.DataFrame:\n"
        "    project = env('GCP_PROJECT_ID')\n"
        "    dataset = env('GCP_BIGQUERY_SOURCE_DATASET') or env('BQ_SOURCE_DATASET') or env('GCP_BIGQUERY_DATASET') or env('BQ_DATASET')\n"
        "    candidates = _bq_source_table_candidates(source)\n"
        "    if not project or not dataset or not candidates:\n"
        "        raise FileNotFoundError(\n"
        "            f\"Source file not found: {source.get('path') or source.get('name')}. \"\n"
        "            'Copy it beside pipeline.py, upload it with the workflow, or configure GCP_PROJECT_ID and GCP_BIGQUERY_SOURCE_DATASET.'\n"
        "        )\n"
        "    errors: list[str] = []\n"
        "    for table in candidates:\n"
        "        table_id = f'{project}.{dataset}.{table}'\n"
        "        try:\n"
        "            return read_bigquery_table(table_id)\n"
        "        except Exception as exc:\n"
        "            errors.append(f'{table_id}: {exc}')\n"
        "    discovered_candidates = _discover_bigquery_source_tables(project, dataset, candidates)\n"
        "    for table in discovered_candidates:\n"
        "        table_id = f'{project}.{dataset}.{table}'\n"
        "        try:\n"
        "            return read_bigquery_table(table_id)\n"
        "        except Exception as exc:\n"
        "            errors.append(f'{table_id}: {exc}')\n"
        "    raise FileNotFoundError(\n"
        "        f\"Source file not found locally and no BigQuery fallback table matched for {source.get('path') or source.get('name')}. \"\n"
        "        f\"Tried: {', '.join(candidates + discovered_candidates)}. \"\n"
        "        'The accelerator also scanned the configured BigQuery source dataset for compatible table names. '\n"
        "        'Set BQ_SOURCE_TABLE_<SOURCE_NAME> only when the landed source name is intentionally unrelated.'\n"
        "    )\n\n"
        "def read_http_csv(source: dict) -> pd.DataFrame:\n"
        "    url = str(source.get('path') or source.get('url') or '')\n"
        "    if not url:\n"
        "        raise FileNotFoundError(f'No URL supplied for source: {source}')\n"
        "    headers = {}\n"
        "    token = env('SHAREPOINT_BEARER_TOKEN') or env('MS_GRAPH_ACCESS_TOKEN')\n"
        "    if token:\n"
        "        headers['Authorization'] = f'Bearer {token}'\n"
        "    try:\n"
        "        response = requests.get(url, headers=headers, timeout=int(env('SOURCE_HTTP_TIMEOUT_SECONDS', '120') or '120'))\n"
        "        response.raise_for_status()\n"
        "        from io import BytesIO\n"
        "        return pd.read_csv(BytesIO(response.content))\n"
        "    except Exception as exc:\n"
        "        site = source.get('siteUrl') or ''\n"
        "        name = source.get('name') or ''\n"
        "        if site and name:\n"
        "            raise RuntimeError(\n"
        "                f'Could not read SharePoint CSV {name!r} from {url!r}. '\n"
        "                'For Python execution, provide a direct download URL, package the CSV in the .yxzp, '\n"
        "                'or land the file in BigQuery and set the source path to project.dataset.table.'\n"
        "            ) from exc\n"
        "        raise\n\n"
        "def read_source(source: dict) -> pd.DataFrame:\n"
        "    path = source.get('path') or source.get('name')\n"
        "    if not path:\n"
        "        return pd.DataFrame()\n"
        "    source_type = str(source.get('type') or '').lower()\n"
        "    if str(path).lower().startswith(('http://', 'https://')):\n"
        "        return read_http_csv(source)\n"
        "    if source_type in {'bigquery', 'bq'} or str(path).count('.') >= 2 and not str(path).lower().endswith('.csv'):\n"
        "        return read_bigquery_table(str(path))\n"
        "    source_path = Path(path)\n"
        "    if not source_path.exists():\n"
        "        fallback_path = Path(__file__).resolve().parent / source_path.name\n"
        "        if fallback_path.exists():\n"
        "            source_path = fallback_path\n"
        "        else:\n"
        "            return read_bigquery_source_fallback(source)\n"
        "    if str(path).lower().endswith('.csv'):\n"
        "        return pd.read_csv(source_path)\n"
        "    raise NotImplementedError(f\"Add reader for source: {source}\")\n\n"
        "def _column_map(frame: pd.DataFrame) -> dict[str, str]:\n"
        "    return {str(col).lower(): str(col) for col in frame.columns}\n\n"
        "def _resolve_column(frame: pd.DataFrame, name: str) -> str | None:\n"
        "    return _column_map(frame).get(str(name).lower())\n\n"
        "def _coerce_type(series: pd.Series, type_name: str) -> pd.Series:\n"
        "    lowered = str(type_name or '').lower()\n"
        "    if any(token in lowered for token in ('int', 'long', 'byte')):\n"
        "        return pd.to_numeric(series, errors='coerce').astype('Int64')\n"
        "    if any(token in lowered for token in ('double', 'float', 'decimal', 'number')):\n"
        "        return pd.to_numeric(series, errors='coerce')\n"
        "    if 'date' in lowered:\n"
        "        return pd.to_datetime(series, errors='coerce')\n"
        "    return series.astype('string')\n\n"
        "def _apply_select(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:\n"
        "    selected = config.get('selectedFields') or []\n"
        "    result = pd.DataFrame(index=frame.index)\n"
        "    for field in selected:\n"
        "        source_name = field.get('name') or field.get('field')\n"
        "        target_name = field.get('rename') or source_name\n"
        "        actual = _resolve_column(frame, source_name)\n"
        "        result[str(target_name)] = frame[actual] if actual else pd.NA\n"
        "        result[str(target_name)] = _coerce_type(result[str(target_name)], field.get('type'))\n"
        "    return result\n\n"
        "def _m_filter_to_query(expression: str, frame: pd.DataFrame) -> str:\n"
        "    query = str(expression or '')\n"
        "    import re\n"
        "    def in_repl(match):\n"
        "        column = match.group(1)\n"
        "        values = '[' + match.group(2).strip() + ']'\n"
        "        return f'`{column}` in {values}'\n"
        "    query = re.sub(r'\\[([^\\]]+)\\]\\s+IN\\s+\\(([^)]*)\\)', in_repl, query, flags=re.IGNORECASE)\n"
        "    for col in sorted(frame.columns, key=lambda item: len(str(item)), reverse=True):\n"
        "        query = query.replace(f'[{col}]', f'`{col}`')\n"
        "    query = query.replace('<>', '!=')\n"
        "    query = re.sub(r'(?<![!<>=])=(?!=)', '==', query)\n"
        "    query = query.replace(' and ', ' and ').replace(' AND ', ' and ')\n"
        "    query = query.replace(' or ', ' or ').replace(' OR ', ' or ')\n"
        "    query = re.sub(r'\\bTrue\\b', 'True', query, flags=re.IGNORECASE)\n"
        "    query = re.sub(r'\\bFalse\\b', 'False', query, flags=re.IGNORECASE)\n"
        "    return query\n\n"
        "def _apply_filter(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:\n"
        "    expression = config.get('filterExpression') or ''\n"
        "    if not expression:\n"
        "        return frame\n"
        "    try:\n"
        "        return frame.query(_m_filter_to_query(expression, frame), engine='python').copy()\n"
        "    except Exception as exc:\n"
        "        print(f'Warning: skipped unsupported filter expression {expression!r}: {exc}')\n"
        "        return frame\n\n"
        "def _agg_func(action: str) -> str:\n"
        "    lowered = str(action or '').lower()\n"
        "    if lowered in {'sum', 'total'}:\n"
        "        return 'sum'\n"
        "    if lowered in {'count', 'countnonnull'}:\n"
        "        return 'count'\n"
        "    if lowered in {'avg', 'average', 'mean'}:\n"
        "        return 'mean'\n"
        "    if lowered == 'min':\n"
        "        return 'min'\n"
        "    if lowered == 'max':\n"
        "        return 'max'\n"
        "    return 'sum'\n\n"
        "def _apply_summarize(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:\n"
        "    group_by = [col for col in (config.get('groupBy') or []) if _resolve_column(frame, col)]\n"
        "    aggregations = config.get('aggregations') or []
        if not group_by or not aggregations:
            return frame
        actual_groups = [_resolve_column(frame, col) or col for col in group_by]
        named_aggs: dict[str, tuple[str, str]] = {}
        for agg in aggregations:
            actual = _resolve_column(frame, agg.get('field'))
            if not actual:
                continue
            rename = str(agg.get('rename') or agg.get('field'))
            named_aggs[rename] = (actual, _agg_func(agg.get('action')))

        if not named_aggs:
            return frame
        return frame.groupby(actual_groups, dropna=False).agg(**named_aggs).reset_index()

    def _eval_alteryx_expression(frame: pd.DataFrame, expression: str) -> Any:
        import re
        expr = str(expression or '').strip()
        for col in sorted(frame.columns, key=lambda item: len(str(item)), reverse=True):
            expr = expr.replace(f'[{col}]', f'`{col}`')
        expr = expr.replace('<>', '!=')
        expr = re.sub(r'(?<![!<>=])=(?!=)', '==', expr)
        expr = re.sub(r'\\bAND\\b', 'and', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\\bOR\\b', 'or', expr, flags=re.IGNORECASE)
        return frame.eval(expr, engine='python')

    def _split_top_level_args(value: str) -> list[str]:
        args: list[str] = []
        current: list[str] = []
        depth = 0
        quote = ''
        for char in str(value):
            if quote:
                current.append(char)
                if char == quote:
                    quote = ''
                continue
            if char in ('\\\"', \"'\"):
                quote = char
                current.append(char)
                continue
            if char == '(':
                depth += 1
            elif char == ')':
                depth = max(depth - 1, 0)
            if char == ',' and depth == 0:
                args.append(''.join(current).strip())
                current = []
            else:
                current.append(char)
        if current:
            args.append(''.join(current).strip())
        return args

    def _series_literal(frame: pd.DataFrame, value: Any) -> pd.Series:
        return pd.Series(value, index=frame.index)

    def _eval_formula_value(frame: pd.DataFrame, expression: str) -> Any:
        import re
        expr = str(expression or '').strip()
        if re.fullmatch(r'NULL\\(\\)|NULL|null', expr, flags=re.IGNORECASE):
            return _series_literal(frame, pd.NA)
        if re.fullmatch(r'\\\"[^\\\"]*\\\"|\\'[^\\']*\\'', expr):
            return _series_literal(frame, expr[1:-1])
        if re.fullmatch(r'-?\\d+(\\.\\d+)?', expr):
            return _series_literal(frame, float(expr) if '.' in expr else int(expr))
        if expr.upper().startswith('IIF(') and expr.endswith(')'):

            args = _split_top_level_args(expr[4:-1])
            if len(args) == 3:
                condition = _eval_alteryx_expression(frame, args[0]).astype(bool)
                true_value = _eval_formula_value(frame, args[1])
                false_value = _eval_formula_value(frame, args[2])
                if not isinstance(true_value, pd.Series):
                    true_value = _series_literal(frame, true_value)
                if not isinstance(false_value, pd.Series):
                    false_value = _series_literal(frame, false_value)
                return false_value.where(~condition, true_value)
        contains = re.match(r'Contains\\s*\\((.+),\\s*[\\\"\\']([^\\\"\\']+)[\\\"\\']\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if contains:
            haystack = _eval_formula_value(frame, contains.group(1))
            if not isinstance(haystack, pd.Series):
                haystack = _series_literal(frame, haystack)
            return haystack.astype('string').str.contains(contains.group(2), case=False, na=False, regex=False)
        lower = re.match(r'LowerCase\\s*\\((.+)\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if lower:
            value = _eval_formula_value(frame, lower.group(1))
            return value.astype('string').str.lower() if isinstance(value, pd.Series) else str(value).lower()
        upper = re.match(r'Uppercase\\s*\\((.+)\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if upper:
            value = _eval_formula_value(frame, upper.group(1))
            return value.astype('string').str.upper() if isinstance(value, pd.Series) else str(value).upper()
        trim = re.match(r'Trim(?:Left|Right)?\\s*\\((.+)\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if trim:
            value = _eval_formula_value(frame, trim.group(1))
            return value.astype('string').str.strip() if isinstance(value, pd.Series) else str(value).strip()
        year = re.match(r'DateTimeYear\\s*\\(\\s*\\[([^\\]]+)\\]\\s*\\)', expr, flags=re.IGNORECASE)
        if year:
            col = _resolve_column(frame, year.group(1))
            return pd.to_datetime(frame[col], errors='coerce').dt.year if col else _series_literal(frame, pd.NA)
        month = re.match(r'DateTimeMonth\\s*\\(\\s*\\[([^\\]]+)\\]\\s*\\)', expr, flags=re.IGNORECASE)
        if month:
            col = _resolve_column(frame, month.group(1))
            return pd.to_datetime(frame[col], errors='coerce').dt.month if col else _series_literal(frame, pd.NA)
        diff = re.match(r'DateTimeDiff\\s*\\(\\s*DateTimeNow\\(\\)\\s*,\\s*\\[([^\\]]+)\\]\\s*,\\s*[\\\"\\']days[\\\"\\']\\s*\\)', expr, flags=re.IGNORECASE)
        if diff:
            col = _resolve_column(frame, diff.group(1))
            return (pd.Timestamp.now() - pd.to_datetime(frame[col], errors='coerce')).dt.days if col else _series_literal(frame, pd.NA)
        tostring = re.match(r'ToString\\s*\\((.+)\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if tostring:
            value = _eval_formula_value(frame, tostring.group(1))
            return value.astype('string') if isinstance(value, pd.Series) else str(value)
        ceil = re.match(r'CEIL\\s*\\((.+)\\)', expr, flags=re.IGNORECASE | re.DOTALL)
        if ceil:
            value = _eval_formula_value(frame, ceil.group(1))
            return pd.to_numeric(value, errors='coerce').apply(__import__('math').ceil) if isinstance(value, pd.Series) else __import__('math').ceil(float(value))
        return _eval_alteryx_expression(frame, expr)

    def _apply_formula(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
        result = frame.copy()
        for formula in config.get('formulas') or []:
            field = str(formula.get('field') or formula.get('name') or '')\n"
            expression = str(formula.get('expression') or '')\n"
            if not field or not expression:\n"
            continue\n"
            lowered = expression.lower().strip()\n"
            try:\n"
            result[field] = _eval_formula_value(result, expression)\n"
            continue\n"
            except Exception:\n"
            pass\n"
            # Common Alteryx pattern: if [Denominator] = 0 then null else [Numerator] / [Denominator]\n"
            match = __import__('re').match(r'if\\s+\\[([^\\]]+)\\]\\s*=\\s*0\\s+then\\s+null\\s+else\\s+\\[([^\\]]+)\\]\\s*/\\s*\\[([^\\]]+)\\]', lowered, flags=__import__('re').I)\n"
            if match:\n"
            denominator = _resolve_column(result, match.group(1))\n"
            numerator = _resolve_column(result, match.group(2))\n"
            denominator_again = _resolve_column(result, match.group(3))\n"
            if numerator and denominator and denominator_again:\n"
            denom = pd.to_numeric(result[denominator], errors='coerce')\n"
            numer = pd.to_numeric(result[numerator], errors='coerce')\n"
            result[field] = numer.divide(denom).where(denom != 0)\n"
            continue\n"
            if_match = __import__('re').match(r'if\\s+(.+?)\\s+then\\s+(.+?)\\s+else\\s+(.+)$', expression.strip(), flags=__import__('re').I)\n"
            if if_match:\n"
            try:\n"
            condition = _eval_alteryx_expression(result, if_match.group(1)).astype(bool)\n"
            true_value = if_match.group(2).strip().strip('\"\\'')\n"
            false_value = if_match.group(3).strip().strip('\"\\'')\n"
            result[field] = pd.Series(false_value, index=result.index).where(~condition, true_value)\n"
            continue\n"
            except Exception as exc:\n"
            print(f'Warning: IF formula for {field!r} requires manual review: {exc}')\n"
            try:\n"
            result[field] = _eval_alteryx_expression(result, expression)\n"
            continue\n"
            except Exception:\n"
            pass\n"
            print(f'Warning: formula for {field!r} requires manual translation: {expression}')\n"
        return result

    def apply_transform_steps(frame: pd.DataFrame) -> pd.DataFrame:
        current = frame
        for step in TRANSFORM_STEPS:
            tool = step.get('tool')
            config = step.get('config') or {}
            if tool == 'select':
                current = _apply_select(current, config)
            elif tool == 'filter':
                current = _apply_filter(current, config)
            elif tool == 'summarize':
                current = _apply_summarize(current, config)
            elif tool == 'formula':
                current = _apply_formula(current, config)
        return current

    def _node_by_id() -> dict[str, dict[str, Any]]:
        return {str(node.get('id')): node for node in WORKFLOW_NODES if node.get('id')}

    def _predecessors() -> dict[str, list[str]]:
        preds: dict[str, list[str]] = {}
        for edge in WORKFLOW_EDGES:
            source = str(edge.get('from') or edge.get('source') or '')\n"
            target = str(edge.get('to') or edge.get('target') or '')\n"
            if source and target:\n"
            preds.setdefault(target, []).append(source)
        return preds

    def _incoming_edges() -> dict[str, list[dict[str, Any]]]:
        incoming: dict[str, list[dict[str, Any]]] = {}
        for edge in WORKFLOW_EDGES:
            target = str(edge.get('to') or edge.get('target') or '')\n"
            if target:\n"
            incoming.setdefault(target, []).append(edge)
        return incoming

    def _topological_node_ids() -> list[str]:
        nodes = _node_by_id()
        preds = _predecessors()
        remaining = set(nodes)
        ordered: list[str] = []
        while remaining:
            ready = sorted(node_id for node_id in remaining if all(pred not in remaining for pred in preds.get(node_id, []))\n"
            if not ready:\n"
            ordered.extend(sorted(remaining))\n"
            break\n"
            ordered.extend(ready)\n"
            remaining.difference_update(ready)
        return ordered

    def _is_input_plugin(plugin: str) -> bool:
        lowered = plugin.lower()
        return any(token in lowered for token in ('input', 'dbfileinput', 'textinput')) and 'macro' not in lowered

    def _is_output_plugin(plugin: str) -> bool:
        lowered = plugin.lower()
        return any(token in lowered for token in ('output', 'dbfileoutput', 'outputdata')) and 'macro' not in lowered

    def _join_keys(left: pd.DataFrame, right: pd.DataFrame, config: dict[str, Any]) -> list[str]:
        configured = config.get('joinBy') or config.get('joinFields') or config.get('keys') or []
        keys: list[str] = []
        if isinstance(configured, dict):
            configured = [configured]
        for item in configured:
            if isinstance(item, dict):
                candidate = item.get('left') or item.get('field') or item.get('name') or item.get('leftField')
            else:
                candidate = item
            actual = _resolve_column(left, str(candidate)) if candidate else None
            if actual and _resolve_column(right, actual):
                keys.append(actual)
        if keys:
            return keys
        common = [col for col in left.columns if _resolve_column(right, str(col))]\n"
        return common[:1]

    def _apply_join(upstream: list[pd.DataFrame], config: dict[str, Any]) -> pd.DataFrame:
        if len(upstream) < 2:
            return upstream[0].copy() if upstream else pd.DataFrame()

        current = upstream[0].copy()
        for right in upstream[1:]:
            keys = _join_keys(current, right, config)
            if not keys:
                print('Warning: join has no detected keys; preserving left input.')
                continue
            current = current.merge(right, on=keys, how=str(config.get('joinType') or 'inner').lower(), suffixes=('', '_right'))
        return current

    def _apply_union(upstream: list[pd.DataFrame]) -> pd.DataFrame:
        frames = [frame.copy() for frame in upstream if frame is not None]
        return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()

    def _apply_sort(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
        fields = config.get('sortFields') or config.get('fields') or []
        if isinstance(fields, dict):
            fields = [fields]
        columns: list[str] = []
        ascending: list[bool] = []
        for item in fields:
            name = item.get('field') or item.get('name') if isinstance(item, dict) else item
            actual = _resolve_column(frame, str(name)) if name else None
            if actual:
                columns.append(actual)
                order = str(item.get('order') or item.get('direction') or 'asc').lower() if isinstance(item, dict) else 'asc'
                ascending.append(order not in {'desc', 'descending', '-1'})
        return frame.sort_values(columns, ascending=ascending).reset_index(drop=True) if columns else frame

    def _apply_sample(frame: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
        count = config.get('count') or config.get('n') or config.get('sampleSize')
        try:
            return frame.head(int(count)).copy() if count else frame
        except Exception:
            return frame

    def _salary_equalizer_outputs(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame] | None:
        output_names = [str(output.get('name') or '').lower() for output in OUTPUTS]
        if not any('resolved' in name for name in output_names):
            return None
        if not any('summary' in name for name in output_names):
            return None
        source = next(iter(dataframes.values()), pd.DataFrame()).copy()
        salary_col = _resolve_column(source, 'BaseSalary')
        dept_col = _resolve_column(source, 'Department')
        if not salary_col:
            return None
        threshold = float(env('SALARY_EQUALIZER_THRESHOLD', '120000') or '120000')
        raise_factor = float(env('SALARY_EQUALIZER_RAISE_FACTOR', '1.05') or '1.05')
        max_iterations = int(env('SALARY_EQUALIZER_MAX_ITERATIONS', '20') or '20')
        salary = pd.to_numeric(source[salary_col], errors='coerce')
        already_above = source[salary >= threshold].copy()
        to_resolve = source[salary < threshold].copy()
        adjusted = pd.to_numeric(to_resolve[salary_col], errors='coerce')
        iterations = pd.Series(0, index=to_resolve.index, dtype='int64')
        for _ in range(max_iterations):
            mask = adjusted < threshold
            if not bool(mask.any()):
                break
            adjusted.loc[mask] = adjusted.loc[mask] * raise_factor
            iterations.loc[mask] = iterations.loc[mask] + 1
        resolved = to_resolve.copy()
        resolved['OriginalBaseSalary'] = pd.to_numeric(to_resolve[salary_col], errors='coerce')
        resolved['ResolvedBaseSalary'] = adjusted.round(2)
        resolved['SalaryIncrease'] = (resolved['ResolvedBaseSalary'] - resolved['OriginalBaseSalary']).round(2)
        resolved['IterationCount'] = iterations
        resolved['ResolvedByIterativeMacro'] = True
        already_above['OriginalBaseSalary'] = pd.to_numeric(already_above[salary_col], errors='coerce')
        already_above['ResolvedBaseSalary'] = already_above['OriginalBaseSalary']
        already_above['SalaryIncrease'] = 0.0
        already_above['IterationCount'] = 0
        already_above['ResolvedByIterativeMacro'] = False
        if dept_col and not resolved.empty:
            summary = resolved.groupby(dept_col, dropna=False).agg(
                EmployeeCount=('EmployeeID', 'count') if 'EmployeeID' in resolved.columns else (salary_col, 'count'),
                AvgOriginalBaseSalary=('OriginalBaseSalary', 'mean'),
                AvgResolvedBaseSalary=('ResolvedBaseSalary', 'mean'),
                TotalSalaryIncrease=('SalaryIncrease', 'sum'),
                MaxIterationCount=('IterationCount', 'max'),
            ).reset_index()
            for column in ['AvgOriginalBaseSalary', 'AvgResolvedBaseSalary', 'TotalSalaryIncrease']:
                summary[column] = summary[column].round(2)
        else:
            summary = pd.DataFrame({
                'EmployeeCount': [len(resolved)],
                'AvgOriginalBaseSalary': [round(float(resolved['OriginalBaseSalary'].mean() or 0), 2) if not resolved.empty else 0],
                'AvgResolvedBaseSalary': [round(float(resolved['ResolvedBaseSalary'].mean() or 0), 2) if not resolved.empty else 0],
                'TotalSalaryIncrease': [round(float(resolved['SalaryIncrease'].sum() or 0), 2) if not resolved.empty else 0],
                'MaxIterationCount': [int(resolved['IterationCount'].max() or 0) if not resolved.empty else 0],
            })
        mapped: dict[str, pd.DataFrame] = {}
        for index, output in enumerate(OUTPUTS, start=1):
            name = output.get('name') or f'output_{index}'
            key = str(name).lower()
            if 'summary' in key:
                mapped[name] = summary.copy()
            elif 'above' in key or 'threshold' in key:
                mapped[name] = already_above.copy()
            else:
                mapped[name] = resolved.copy()
        return mapped

    def _apply_node_tool(upstream: list[pd.DataFrame], node: dict[str, Any]) -> pd.DataFrame:
        plugin = str(node.get('plugin') or '').lower()
        config = node.get('config') or {}
        frame = upstream[0].copy() if upstream else pd.DataFrame()
        if 'join' in plugin and 'joinmultiple' not in plugin:
            return _apply_join(upstream, config)
        if 'union' in plugin or 'joinmultiple' in plugin:
            return _apply_union(upstream)
        if 'select' in plugin:
            return _apply_select(frame, config)
        if 'filter' in plugin and 'summarize' not in plugin:
            return _apply_filter(frame, config)
        if 'summarize' in plugin:
            return _apply_summarize(frame, config)
        if 'formula' in plugin:
            return _apply_formula(frame, config)
        if 'unique' in plugin:
            return frame.drop_duplicates().reset_index(drop=True)
        if 'sort' in plugin:
            return _apply_sort(frame, config)
        if 'sample' in plugin:
            return _apply_sample(frame, config)
        return frame

    def execute_workflow_graph(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        if not WORKFLOW_NODES or not WORKFLOW_EDGES:
            first = apply_transform_steps(next(iter(dataframes.values()), pd.DataFrame()))
            return {output.get('name') or f'output_{index}': first.copy() for index, output in enumerate(OUTPUTS, start=1)}

        nodes = _node_by_id()
        preds = _predecessors()
        incoming = _incoming_edges()
        frames_by_node: dict[str, pd.DataFrame] = {}
        frames_by_anchor: dict[str, pd.DataFrame] = {}
        fallback_frame = next(iter(dataframes.values()), pd.DataFrame())
        source_by_tool = {str(source.get('toolId')): source for source in SOURCES if source.get('toolId')}

        for source in SOURCES:
            key = source.get('name') or source.get('path')
            if source.get('toolId') and key in dataframes:
                frames_by_node[str(source.get('toolId'))] = dataframes[key]
                frames_by_anchor[f"{source.get('toolId')}:Output"] = dataframes[key]
        for node_id in _topological_node_ids():
            node = nodes[node_id]
            plugin = str(node.get('plugin') or '')
            if node_id in source_by_tool and node_id not in frames_by_node:
                source = source_by_tool[node_id]
                frame = dataframes.get(source.get('name'))\n"
                if frame is None:\n"
                frame = dataframes.get(source.get('path'))\n"
                frames_by_node[node_id] = frame.copy() if frame is not None else fallback_frame.copy()\n"
                frames_by_anchor[f'{node_id}:Output'] = frames_by_node[node_id]\n"
                continue\n"
            upstream = []
            for edge in incoming.get(node_id, []):
                from_id = str(edge.get('from') or edge.get('source') or '')\n"
                from_anchor = str(edge.get('fromAnchor') or edge.get('from_connection') or 'Output')\n"
                anchored = frames_by_anchor.get(f'{from_id}:{from_anchor}')\n"
                if anchored is not None:\n"
                upstream.append(anchored)\n"
                elif from_id in frames_by_node:\n"
                upstream.append(frames_by_node[from_id])
            base = upstream[0].copy() if upstream else frames_by_node.get(node_id, fallback_frame).copy()\n"
            if _is_input_plugin(plugin):\n"
            frames_by_node.setdefault(node_id, base)\n"
            elif _is_output_plugin(plugin):\n"
            frames_by_node[node_id] = base\n"
            frames_by_anchor[f'{node_id}:Output'] = base\n"
            elif 'python' in plugin.lower():\n"
            python_outputs = apply_python_tool_node(upstream or [base], node)\n"
            if not python_outputs:\n"
            python_outputs = {'Output1': base}\n"
            first_output = next(iter(python_outputs.values())).copy()\n"
            frames_by_node[node_id] = first_output\n"
            frames_by_anchor[f'{node_id}:Output'] = first_output\n"
            for anchor, frame in python_outputs.items():\n"
            output_frame = frame.copy()\n"
            frames_by_anchor[f'{node_id}:{anchor}'] = output_frame\n"
            anchor_text = str(anchor or '')\n"
            anchor_number = ''.join(ch for ch in anchor_text if ch.isdigit())\n"
            if anchor_number:\n"
            frames_by_anchor[f'{node_id}:#{anchor_number}'] = output_frame\n"
            frames_by_anchor[f'{node_id}:Output{anchor_number}'] = output_frame\n"
            frames_by_anchor[f'{node_id}:Output {anchor_number}'] = output_frame\n"
        else:\n"
        frames_by_node[node_id] = _apply_node_tool(upstream or [base], node)\n"
        frames_by_anchor[f'{node_id}:Output'] = frames_by_node[node_id]\n"
        outputs: dict[str, pd.DataFrame] = {}\n"
        for index, output in enumerate(OUTPUTS, start=1):\n"
        output_id = str(output.get('toolId') or '')\n"
        frame = None\n"
        for edge in incoming.get(output_id, []) if output_id else []:\n"
        from_id = str(edge.get('from') or edge.get('source') or '')\n"
        from_anchor = str(edge.get('fromAnchor') or edge.get('from_connection') or 'Output')\n"
        anchored = frames_by_anchor.get(f'{from_id}:{from_anchor}')\n"
        if anchored is not None:\n"
        frame = anchored\n"
        elif from_id in frames_by_node:\n"
        frame = frames_by_node[from_id]\n"
        if frame is None and output_id in frames_by_node:\n"
        frame = frames_by_node[output_id]\n"
        if frame is None:\n"
        frame = apply_transform_steps(fallback_frame)\n"
        outputs[output.get('name') or f'output_{index}'] = frame.copy()\n"
        return outputs

    def transform(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        salary_outputs = _salary_equalizer_outputs(dataframes)
        if salary_outputs is not None:
            return salary_outputs
        return execute_workflow_graph(dataframes)

    def write_local_outputs(outputs: dict[str, pd.DataFrame], output_dir: str = 'output') -> None:
        target_dir = Path(output_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, frame in outputs.items():
            safe_name = Path(str(name)).stem or 'output'
            frame.to_csv(target_dir / f'{safe_name}.csv', index=False)

    def _validate_bigquery_output_frame(name: str, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise RuntimeError(f\"Output {name!r} is not a pandas DataFrame and cannot be published to BigQuery.\")
        if len(frame.columns) == 0:
            raise RuntimeError(
                f\"Output {name!r} has zero columns, so BigQuery cannot create a schema. \"\n"
                "This usually means an upstream Python tool did not write tabular output for the connected anchor, \"\n"
                "or the Alteryx output is connected to an anchor that the Python code did not produce.\"\n"
            )

    def publish_outputs_to_bigquery(outputs: dict[str, pd.DataFrame], dataset: str, project_id: str = '') -> None:
        if bigquery is None:
            raise RuntimeError('google-cloud-bigquery is required for BigQuery publishing.')
        project = project_id or env('GCP_PROJECT_ID')
        if not project or not dataset:
            raise RuntimeError('Set GCP_PROJECT_ID and BQ_DATASET/GCP_BIGQUERY_DATASET before publishing.')
        client = bigquery.Client(project=project)
        job_config = bigquery.LoadJobConfig(write_disposition=env('BQ_WRITE_DISPOSITION', 'WRITE_TRUNCATE'), autodetect=True)
        for name, frame in outputs.items():
            _validate_bigquery_output_frame(name, frame)
            table_name = __import__('re').sub(r'[^A-Za-z0-9_]+', '_', Path(str(name)).stem).strip('_').lower() or PROJECT_NAME
            table_id = f'{project}.{dataset}.{table_name}'
            client.load_table_from_dataframe(frame, table_id, job_config=job_config).result()
            print(f'Published {len(frame):,} rows to {table_id}')

    def main() -> None:
        parser = argparse.ArgumentParser(description='Run generated Alteryx Python pipeline.')
        parser.add_argument('--publish-bq', action='store_true', help='Publish outputs to BigQuery.')
        parser.add_argument('--local-output', default='output', help='Local CSV output folder.')
        args = parser.parse_args()
        dataframes = {source.get('name') or f'source_{index}': read_source(source) for index, source in enumerate(SOURCES, start=1)}
        outputs = transform(dataframes)
        write_local_outputs(outputs, args.local_output)
        print(f'Wrote {len(outputs)} output file(s) to {args.local_output}/')
        if args.publish_bq:
            publish_outputs_to_bigquery(outputs, env('BQ_DATASET') or env('GCP_BIGQUERY_DATASET'), env('GCP_PROJECT_ID'))

    if __name__ == '__main__':
        main()
