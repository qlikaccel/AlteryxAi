import html
import hashlib
import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from app.services.alteryx_converter import (
    ALTERYX_TOOL_MAPPINGS,
    convert_workflow_to_m,
)


DEFAULT_SHAREPOINT_FILE_URL = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx"
DEFAULT_SHAREPOINT_FILE_NAME = "sales_data_1M.csv"


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
    result = convert_workflow_to_m(workflow, source, sharepoint_url, file_name)
    result["workflow_statistics"] = generate_workflow_statistics(workflow, result)
    return result


def _source_identity(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    match = re.search(r"([^/\s]+?\.(?:csv|xlsx?|json|xml|txt|parquet))\b", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return raw.rsplit("/", 1)[-1].lower()


def generate_workflow_statistics(
    workflow: dict[str, Any],
    mquery_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mquery_payload = mquery_payload or {}
    nodes = workflow.get("workflowNodes") or []
    edges = workflow.get("workflowEdges") or []
    sources = workflow.get("dataSources") or []
    unique_sources = {}
    for source_item in sources:
        if not isinstance(source_item, dict):
            continue
        raw_key = str(source_item.get("name") or source_item.get("path") or "").strip()
        key = _source_identity(raw_key)
        if key and key not in unique_sources:
            unique_sources[key] = source_item
    tool_counts = Counter(
        str(node.get("plugin") or "Unknown").rsplit(".", 1)[-1]
        for node in nodes
    )

    tables = mquery_payload.get("source_queries") or []
    table_count = len(tables) + (1 if mquery_payload.get("table_name") else 0)
    if table_count == 0:
        table_count = int(mquery_payload.get("source_count") or (1 if workflow else 0))

    source_fields_map = mquery_payload.get("source_fields_map") or {}
    source_column_names = {
        str(field.get("name") or "").strip()
        for fields in source_fields_map.values()
        for field in (fields or [])
        if isinstance(field, dict) and str(field.get("name") or "").strip()
    }
    final_columns: set[str] = set()
    for node in nodes:
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        for field in config.get("selectedFields") or []:
            if isinstance(field, dict) and field.get("name"):
                source_column_names.add(str(field.get("name")).strip())
        for formula in config.get("formulas") or []:
            if isinstance(formula, dict) and formula.get("field"):
                final_columns.add(str(formula.get("field")).strip())
        for group in config.get("groupBy") or []:
            if group:
                final_columns.add(str(group).strip())
        for agg in config.get("aggregations") or []:
            if isinstance(agg, dict) and (agg.get("rename") or agg.get("field")):
                final_columns.add(str(agg.get("rename") or agg.get("field")).strip())

    total_records = None
    counted_source_keys: set[str] = set()
    for source_item in unique_sources.values():
        raw_source_key = str(source_item.get("name") or source_item.get("path") or "").strip()
        source_key = _source_identity(raw_source_key)
        if source_key and source_key in counted_source_keys:
            continue
        for key in ("row_count", "no_of_rows", "rowCount", "record_count"):
            value = source_item.get(key) if isinstance(source_item, dict) else None
            if isinstance(value, (int, float)) and value >= 0:
                total_records = int(value) if total_records is None else total_records + int(value)
                if source_key:
                    counted_source_keys.add(source_key)
                break
    if total_records is None:
        hinted_records = 0
        hinted_files: set[str] = set()
        for node in nodes:
            blob = str(node.get("configurationText") or "")
            if "input" not in str(node.get("plugin") or "").lower() and ".csv" not in blob.lower():
                continue
            file_match = re.search(r"([^\\/\s]+\.csv)\b", blob, flags=re.IGNORECASE)
            file_key = file_match.group(1).lower() if file_match else str(node.get("id") or "")
            if file_key in hinted_files:
                continue
            count_match = re.search(
                r"[~(]?\s*([\d,.]+)\s*([kKmMbB]?)\s*(?:rows?|records?)\b",
                blob,
                flags=re.IGNORECASE,
            )
            if not count_match:
                count_match = re.search(r"\b([\d,.]+)\s*([kKmMbB])\b", blob, flags=re.IGNORECASE)
            if not count_match:
                continue
            value = float(count_match.group(1).replace(",", ""))
            suffix = (count_match.group(2) or "").lower()
            if suffix == "k":
                value *= 1_000
            elif suffix == "m":
                value *= 1_000_000
            elif suffix == "b":
                value *= 1_000_000_000
            hinted_records += int(round(value))
            hinted_files.add(file_key)
        if hinted_records > 0:
            total_records = hinted_records

    validation_checks = validate_migration(workflow).get("checks", [])
    return {
        "total_records": total_records,
        "total_tools_used": len(nodes),
        "table_count": table_count,
        "column_count": len(final_columns or source_column_names),
        "source_column_count": len(source_column_names),
        "final_column_count": len(final_columns),
        "connection_count": int(workflow.get("connectionCount") or len(edges) or 0),
        "source_count": len(unique_sources),
        "supported_tool_count": int(workflow.get("supportedToolCount") or 0),
        "unsupported_tool_count": int(workflow.get("unsupportedToolCount") or 0),
        "tool_type_count": len(tool_counts),
        "tool_counts": dict(sorted(tool_counts.items())),
        "validation_checks": validation_checks,
    }


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


def _dbt_source_name(source: dict[str, Any], index: int) -> str:
    name = str(source.get("name") or source.get("path") or f"source_{index}")
    name = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet)$", "", name, flags=re.IGNORECASE)
    return _dbt_identifier(name, f"source_{index}")


def _dbt_source_identifier(source: dict[str, Any], index: int) -> str:
    name = str(source.get("name") or source.get("path") or f"source_{index}")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
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


# def _generate_batch_region_model(project_name: str, source_model_names: list[str], macro_notes: str) -> str:
#     orders_stage = _stage_for_source(source_model_names, "orders", "order")
#     region_stage = _stage_for_region_parameters(source_model_names)
#     return (
#         "{{ config(materialized='table') }}\n\n"
#         "-- dbt batch macro scaffold generated from Alteryx batch macro metadata.\n"
#         "-- Control parameter: Region. This model applies region parameters to the order stream.\n"
#         f"{macro_notes}\n\n" if macro_notes else
#         "{{ config(materialized='table') }}\n\n"
#         "-- dbt batch macro scaffold generated from Alteryx batch macro metadata.\n"
#         "-- Control parameter: Region. This model applies region parameters to the order stream.\n\n"
#     ) + (
#         "with orders as (\n"
#         f"    select * from {{{{ ref('stg_{orders_stage}') }}}}\n"
#         "),\n"
#         "regions as (\n"
#         f"    select * from {{{{ ref('stg_{region_stage}') }}}}\n"
#         ")\n\n"
#         "select\n"
#         "    orders.*,\n"
#         "    regions.Manager as BatchRegionManager,\n"
#         "    safe_cast(regions.TaxRate as numeric) as BatchTaxRate,\n"
#         "    regions.Region as BatchControlRegion,\n"
#         "    1 as BatchMacroProcessed,\n"
#         "    'batch_region_processor' as BatchMacroName\n"
#         "from orders\n"
#         "inner join regions\n"
#         "    on upper(trim(cast(orders.Region as string))) = upper(trim(cast(regions.Region as string)))\n"
#     )

def _generate_batch_region_model(project_name: str, source_model_names: list[str], macro_notes: str) -> str:
    first_stage = source_model_names[0] if source_model_names else "source_1"
    notes_block = f"{macro_notes}\n\n" if macro_notes else ""
    return (
        "{{ config(materialized='table') }}\n\n"
        "-- dbt batch macro scaffold generated from Alteryx batch macro metadata.\n"
        f"{notes_block}"
        "with base as (\n"
        f"    select * from {{{{ ref('stg_{first_stage}') }}}}\n"
        "),\n"
        "filtered as (\n"
        "    select\n"
        "        *,\n"
        "        safe_cast(MetricA as numeric) + safe_cast(MetricB as numeric) as TotalMetric,\n"
        "        1 as BatchMacroProcessed,\n"
        "        'category_batch_macro' as BatchMacroName\n"
        "    from base\n"
        "    where Category = 'A'\n"
        ")\n\n"
        "select * from filtered\n"
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
        "tool_count": len(workflow.get("workflowNodes") or []),
        "types": sorted({str(item.get("macroType") or "Macro") for item in dependencies}),
        "final_model": project_name,
    }

    batch_macros = [item for item in dependencies if str(item.get("macroType") or "").lower() == "batch"]
    iterative_macros = [item for item in dependencies if str(item.get("macroType") or "").lower() == "iterative"]

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


def generate_dbt_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    """Generate a dbt-compatible scaffold for warehouse-side implementation.

    The generated project assumes source data has already been landed in the
    target warehouse. This keeps the artifact dbt-native instead of embedding
    Power Query/SharePoint extraction semantics into dbt models.
    """
    project_name = _single_macro_project_name(workflow)
    tool_count = len(workflow.get("workflowNodes") or [])
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
        source_key = _dbt_source_name(source, index)
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
        # description = str(source.get("path") or source.get("type") or "")
        # identifier_line = f"        identifier: {source_identifier}\n" if source_identifier != source_name else ""
        # source_rows.append(
        #     f"      - name: {source_name}\n"
        #     f"{identifier_line}"
        #     f"        description: \"Landed source for {str(source.get('name') or source_name).replace(chr(34), '')}. Original path: {description.replace(chr(34), '')}\""
        # )
        description = (
            str(source.get("path") or source.get("type") or "")
            .replace("\\", "/")
            .replace("'", "")
        )
        source_name_clean = str(source.get('name') or source_name).replace("'", "")
        identifier_line = f"        identifier: {source_identifier}\n" if source_identifier != source_name else ""
        source_rows.append(
            f"      - name: {source_name}\n"
            f"{identifier_line}"
            f"        description: 'Landed source for {source_name_clean}. Original path: {description}'"
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
    macro_notes = "\n".join(
        f"-- Macro dependency: {item.get('macroType', 'Macro')} {item.get('path') or item.get('name')} "
        f"(status: {item.get('status', 'unknown')})"
        for item in macro_dependencies
    )
    if _has_single_batch_macro(workflow):
        final_model = _generate_batch_region_model(project_name, source_model_names, macro_notes)
    elif _has_single_iterative_macro(workflow):
        final_model = _generate_iterative_hierarchy_model(project_name, source_model_names, macro_notes)
    else:
        first_stage = source_model_names[0] if source_model_names else "source_1"
        final_model = (
            "{{ config(materialized='table') }}\n\n"
            "-- dbt-compatible scaffold generated from Alteryx workflow metadata.\n"
            "-- Review macro, iterative, batch, Python, API, and multi-input semantics before production use.\n"
            f"{macro_notes}\n\n" if macro_notes else
            "{{ config(materialized='table') }}\n\n"
            "-- dbt-compatible scaffold generated from Alteryx workflow metadata.\n"
            "-- Review macro, iterative, batch, Python, API, and multi-input semantics before production use.\n\n"
        )
        final_model += (
            "with base as (\n"
            f"    select * from {{{{ ref('stg_{first_stage}') }}}}\n"
            ")\n\n"
            "select\n"
            "    *\n"
            "from base\n"
        )

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
            "    +materialized: view\n"
            "    staging:\n"
            "      +materialized: view\n"
        ),
        "models/schema.yml": schema_yml,
        f"models/{project_name}.sql": final_model,
        "macros/README.md": (
            "# Macro Remediation Notes\n\n"
            "Alteryx macros are represented as review notes in this dbt scaffold. "
            "Batch and iterative macro behavior should be rewritten as SQL models, dbt macros, or orchestration logic after expected outputs are confirmed.\n"
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
        "macro_complexity": _macro_complexity_summary(workflow, sources, project_name),
    }


def generate_executive_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    name = workflow.get("name", "Selected workflow")
    tool_count = workflow.get("toolCount", 0)
    unsupported_count = workflow.get("unsupportedToolCount", 0)
    supported_count = workflow.get("supportedToolCount", max(tool_count - unsupported_count, 0))
    automation_score = round((supported_count / max(tool_count, 1)) * 100) if tool_count else 0
    sources = workflow.get("dataSources") or []
    source_labels = ", ".join(sorted({s.get("type", "unknown") for s in sources})) or "user supplied source metadata"
    fit = workflow.get("convertibility", "manual_review")
    complexity = workflow.get("complexity", "manual_review")
    mapped_names = sorted({
        (tool.rsplit(".", 1)[-1] if "." in tool else tool)
        for tool in (workflow.get("toolTypes") or [])
    })[:8]

    bullets = [
        f"{name} contains {tool_count} Alteryx tool(s) and {workflow.get('connectionCount', 0)} workflow connection(s).",
        f"Detected source type coverage: {source_labels}.",
        f"Automated conversion fit is classified as {fit} with {complexity} complexity and an estimated {automation_score}% mapping score.",
        f"Primary mapped tool families include {', '.join(mapped_names) if mapped_names else 'Input, Select, Filter, Summarize, Formula, and Browse'}.",
        f"{unsupported_count} tool instance(s) require remediation before a fully automated Power BI implementation.",
        "The migration approach converts Alteryx input and transformation intent into Power Query M for Power BI/Fabric.",
        "Power BI should fetch business data directly from governed sources such as SharePoint, databases, Excel, or APIs.",
        "Validation should compare source row counts, target refresh status, schema coverage, and unsupported-tool remediation closure.",
    ]
    return {
        "bullets": bullets,
        "model": "llama_mistral_ready_deterministic_fallback",
        "success": True,
        "automation_score": automation_score,
        "source_types": source_labels,
    }


def generate_workflow_diagram(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow.get("workflowNodes") or []
    edges = workflow.get("workflowEdges") or []
    if not nodes:
        return {
            "type": "workflow",
            "mermaid": "flowchart LR\n    A[Uploaded Alteryx Workflow] --> B[Power BI Conversion Plan]",
            "message": "No node-level workflow inventory was available; showing migration flow.",
        }

    lines = ["flowchart LR"]
    node_ids = set()
    for node in nodes[:80]:
        node_id = _safe_name(str(node.get("id", "")), "Node")
        node_ids.add(str(node.get("id", "")))
        label = f"{node.get('id', '')}: {node.get('plugin', 'Tool')}"
        lines.append(f'    {node_id}["{label}"]')

    for edge in edges[:120]:
        from_raw = str(edge.get("from", ""))
        to_raw = str(edge.get("to", ""))
        if from_raw in node_ids and to_raw in node_ids:
            lines.append(f"    {_safe_name(from_raw, 'From')} --> {_safe_name(to_raw, 'To')}")

    return {"type": "workflow", "mermaid": "\n".join(lines), "message": "Workflow diagram generated from Alteryx tool connections."}


def generate_brd_html(workflow: dict[str, Any], m_query: str = "") -> str:
    summary = generate_executive_summary(workflow)["bullets"]
    diagram = generate_workflow_diagram(workflow)["mermaid"]
    recommendations = workflow.get("recommendations") or []
    sources = workflow.get("dataSources") or []
    mquery_payload = generate_m_query(workflow)
    conversion_steps = mquery_payload.get("conversion_steps") or []

    bullet_html = "".join(f"<li>{html.escape(item)}</li>" for item in summary)
    source_html = "".join(
        f"<tr><td>{html.escape(s.get('name', ''))}</td><td>{html.escape(s.get('type', ''))}</td><td>{html.escape(s.get('path', ''))}</td></tr>"
        for s in sources
    ) or "<tr><td colspan='3'>Source will be supplied during migration configuration.</td></tr>"
    rec_html = "".join(f"<li>{html.escape(item)}</li>" for item in recommendations) or "<li>No blocking remediation detected.</li>"
    mapping_html = "".join(
        "<tr>"
        f"<td>{html.escape(step.get('plugin', ''))}</td>"
        f"<td>{html.escape(step.get('tool', ''))}</td>"
        f"<td>{html.escape(step.get('m_function', ''))}</td>"
        f"<td>{'Mapped' if step.get('mapped') else 'Manual Review'}</td>"
        "</tr>"
        for step in conversion_steps
    ) or "".join(
        f"<tr><td>{html.escape(name.title())}</td><td>{html.escape(meta.get('category', ''))}</td><td>{html.escape(meta.get('m', ''))}</td><td>Available</td></tr>"
        for name, meta in list(ALTERYX_TOOL_MAPPINGS.items())[:18]
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{html.escape(workflow.get('name', 'Alteryx Workflow'))} BRD</title>
  <style>
    :root {{ --ink:#0e0e0e; --paper:#f8f4ee; --cream:#ede8df; --gold:#c49a2d; --rust:#a83a1e; --teal:#1a5c5a; --rule:#c9bfad; --muted:#6b6254; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#d4cfc6; color:var(--ink); font-family:'Segoe UI', Arial, sans-serif; font-size:13px; line-height:1.65; }}
    .doc-wrapper {{ max-width:900px; margin:0 auto; padding:24px 0 80px; }}
    .page {{ position:relative; overflow:hidden; margin-bottom:24px; background:var(--paper); box-shadow:0 4px 32px rgba(0,0,0,.18); }}
    .page::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:5px; background:linear-gradient(180deg,var(--gold),var(--teal)); }}
    .page-inner {{ min-height:960px; padding:60px 64px; }}
    .cover {{ background:var(--ink); color:var(--paper); padding:58px 64px; margin:-60px -64px 36px; }}
    .doc-type {{ color:var(--gold); letter-spacing:.35em; font-size:10px; text-transform:uppercase; margin-bottom:22px; }}
    h1 {{ font-family:Georgia, serif; font-size:58px; line-height:1; margin:0 0 16px; }}
    h2 {{ margin:32px 0 14px; padding-bottom:6px; border-bottom:1px solid var(--rule); color:var(--teal); font-size:13px; letter-spacing:.1em; text-transform:uppercase; }}
    h3 {{ margin:22px 0 10px; font-family:Georgia, serif; font-size:22px; }}
    p {{ color:#333; }}
    .meta-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:22px 0; }}
    .meta-card {{ background:var(--cream); border-left:3px solid var(--teal); padding:14px 16px; }}
    .meta-card span {{ display:block; color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.12em; }}
    .meta-card strong {{ display:block; margin-top:6px; overflow-wrap:anywhere; }}
    .brd-table {{ width:100%; border-collapse:collapse; margin:18px 0 28px; font-size:11.5px; }}
    .brd-table thead tr {{ background:var(--ink); color:var(--paper); }}
    .brd-table th {{ padding:10px 14px; text-align:left; font-size:10px; letter-spacing:.12em; text-transform:uppercase; }}
    .brd-table td {{ padding:9px 14px; border-bottom:1px solid var(--rule); vertical-align:top; overflow-wrap:anywhere; }}
    .brd-table tbody tr:nth-child(even) {{ background:var(--cream); }}
    .callout {{ padding:16px 20px; margin:16px 0; border-left:4px solid var(--teal); background:var(--cream); }}
    .scope-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin:16px 0 24px; }}
    .scope-box {{ padding:16px 20px; background:#fff; border-top:3px solid var(--teal); }}
    pre {{ background:var(--ink); color:#a8e6cf; padding:20px 24px; overflow:auto; border-left:3px solid var(--gold); white-space:pre-wrap; }}
    .pg-watermark {{ position:absolute; bottom:20px; left:32px; font-size:9px; color:var(--rule); letter-spacing:.08em; text-transform:uppercase; }}
    .pg-num {{ position:absolute; bottom:20px; right:32px; font-size:10px; color:var(--muted); }}
  </style>
</head>
<body>
  <div class="doc-wrapper">
    <section class="page"><div class="page-inner">
      <div class="cover">
        <div class="doc-type">Business Requirements Document</div>
        <h1>{html.escape(workflow.get('name', 'Alteryx Workflow'))}</h1>
        <p style="color:#d7d0c4">Alteryx to Power BI migration accelerator output for a workflow-specific assessment, conversion, publication, and reconciliation plan.</p>
      </div>
      <div class="meta-grid">
        <div class="meta-card"><span>Workflow File</span><strong>{html.escape(workflow.get('sourceFile', 'Uploaded workflow'))}</strong></div>
        <div class="meta-card"><span>Conversion Fit</span><strong>{html.escape(workflow.get('convertibility', 'manual_review'))}</strong></div>
        <div class="meta-card"><span>Tools</span><strong>{workflow.get('toolCount', 0)} tool(s), {workflow.get('connectionCount', 0)} connection(s)</strong></div>
        <div class="meta-card"><span>Target</span><strong>Power BI semantic model / dataflow</strong></div>
      </div>
      <h2>Executive Summary</h2>
      <ul>{bullet_html}</ul>
      <h2>Source Inventory</h2>
      <table class="brd-table"><thead><tr><th>Name</th><th>Type</th><th>Path</th></tr></thead><tbody>{source_html}</tbody></table>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">01</div>
    </div></section>
    <section class="page"><div class="page-inner">
      <h2>Functional Scope</h2>
      <div class="scope-grid">
        <div class="scope-box"><h3>In Scope</h3><p>Parse Alteryx workflow metadata, infer source paths, convert supported tools to M Query, publish to Power BI, and reconcile migration status.</p></div>
        <div class="scope-box"><h3>Requires Review</h3><p>Macros, custom code, spatial/predictive tools, dynamic input, multi-stream joins, and credential-bound database/API connections.</p></div>
      </div>
      <h2>Tool Mapping Register</h2>
      <table class="brd-table"><thead><tr><th>Alteryx Plugin</th><th>Tool Family</th><th>Power Query M Mapping</th><th>Status</th></tr></thead><tbody>{mapping_html}</tbody></table>
      <h2>Migration Requirements</h2>
      <div class="callout">The migrated Power BI artifact must retrieve data directly from governed source paths, preserve Alteryx transformation intent where deterministic mappings exist, and isolate unsupported logic for remediation.</div>
      <ul>
        <li>Convert supported tools such as Filter, Formula, Select, Join, Union, Summarize, Sort, Unique, and Record ID to Power Query M.</li>
        <li>Use SharePoint.Files, File.Contents, Odbc.DataSource, Excel.Workbook, Web.Contents, Json.Document, and Xml.Tables based on the detected source type.</li>
        <li>Publish the generated artifact to the configured Power BI workspace and expose the publish API endpoint for operational traceability.</li>
        <li>Generate validation checks for source detection, conversion completeness, publish status, dataset identifier, and remediation closure.</li>
      </ul>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">02</div>
    </div></section>
    <section class="page"><div class="page-inner">
      <h2>Workflow Diagram</h2>
      <pre>{html.escape(diagram)}</pre>
      <h2>Remediation Notes</h2>
      <ul>{rec_html}</ul>
      <h2>Generated Power Query</h2>
      <pre>{html.escape(m_query or mquery_payload.get('combined_mquery') or 'Generate M Query before publication.')}</pre>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">03</div>
    </div></section>
  </div>
</body>
</html>"""


def validate_migration(workflow: dict[str, Any], publish_result: dict[str, Any] | None = None) -> dict[str, Any]:
    publish_result = publish_result or {}
    sources = workflow.get("dataSources") or []
    unique_source_keys = {
        str(source.get("name") or source.get("path") or "").strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
        for source in sources
        if isinstance(source, dict) and str(source.get("name") or source.get("path") or "").strip()
    }
    checks = [
        {
            "name": "Workflow parsed",
            "status": "pass" if workflow.get("toolCount", 0) > 0 else "warning",
            "detail": f"{workflow.get('toolCount', 0)} tool(s) detected.",
        },
        {
            "name": "Source detected",
            "status": "pass" if unique_source_keys else "warning",
            "detail": f"{len(unique_source_keys)} unique source candidate(s) detected.",
        },
        {
            "name": "Unsupported tools",
            "status": "pass" if not workflow.get("unsupportedTools") else "warning",
            "detail": f"{workflow.get('unsupportedToolCount', 0)} unsupported tool instance(s).",
        },
        {
            "name": "Power BI publish",
            "status": "pass" if publish_result.get("success") else "pending",
            "detail": publish_result.get("message") or "Publish has not completed in this session.",
        },
    ]
    return {
        "success": all(check["status"] in {"pass", "warning"} for check in checks),
        "checks": checks,
        "publish_result": publish_result,
    }
