import html
import hashlib
import json
import re
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
    return convert_workflow_to_m(workflow, source, sharepoint_url, file_name)


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
        description = str(source.get("path") or source.get("type") or "")
        identifier_line = f"        identifier: {source_identifier}\n" if source_identifier != source_name else ""
        source_rows.append(
            f"      - name: {source_name}\n"
            f"{identifier_line}"
            f"        description: \"Landed source for {str(source.get('name') or source_name).replace(chr(34), '')}. Original path: {description.replace(chr(34), '').replace(chr(92), '/')}\""
            # f"        description: \"Landed source for {str(source.get('name') or source_name).replace(chr(34), '')}. Original path: {description.replace(chr(34), '')}\""
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
    output_model_files = (
        _salary_equalizer_models(project_name, first_stage, output_targets, salary_pattern)
        if output_targets and salary_pattern
        else _generic_output_models(project_name, upstream_ref, output_targets)
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
            "    +materialized: view\n"
            "    staging:\n"
            "      +materialized: view\n"
        ),
        "models/schema.yml": schema_yml,
        f"models/{project_name}.sql": final_model,
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
    }


def _sql_to_sqlx(sql: str) -> str:
    converted = re.sub(r"\{\{\s*config\(materialized='([^']+)'\)\s*\}\}", r'config { type: "\1" }', sql)
    converted = re.sub(r"\{\{\s*ref\('([^']+)'\)\s*\}\}", r'${ref("\1")}', converted)
    converted = re.sub(r"\{\{\s*source\('alteryx_raw',\s*'([^']+)'\)\s*\}\}", r'${ref("\1")}', converted)
    converted = converted.replace("materialized: \"view\"", 'type: "view"')
    return converted


def generate_dataform_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    dbt_project = generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
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
    }


def _python_identifier(value: str, fallback: str = "alteryx_pipeline") -> str:
    return _dbt_identifier(value, fallback).replace("-", "_")


def generate_python_project(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    project_name = _python_identifier(workflow.get("name") or "alteryx_python_pipeline", "alteryx_python_pipeline")
    sources = [source for source in (workflow.get("dataSources") or []) if _is_warehouse_landed_source(source)]
    outputs = workflow.get("outputTargets") or []
    macro_plan = generate_macro_conversion_plan(workflow)
    source_list = ",\n".join(
        f"        {{\"name\": \"{str(source.get('name') or '').replace(chr(34), '')}\", \"path\": \"{str(source.get('path') or '').replace(chr(34), '')}\", \"type\": \"{source.get('type') or 'unknown'}\"}}"
        for source in sources
    )
    output_list = ",\n".join(
        f"        {{\"name\": \"{str(output.get('name') or '').replace(chr(34), '')}\", \"path\": \"{str(output.get('path') or '').replace(chr(34), '')}\", \"type\": \"{output.get('type') or 'csv'}\"}}"
        for output in outputs
    )
    pipeline_py = (
        '"""Generated Alteryx migration Python scaffold.\n\n'
        "This script is intended for complex workflows that need Python orchestration,\n"
        "API calls, iterative logic, or manual remediation beyond SQL/dbt/Dataform.\n"
        '"""\n\n'
        "from __future__ import annotations\n\n"
        "from pathlib import Path\n"
        "import pandas as pd\n\n"
        f"PROJECT_NAME = \"{project_name}\"\n"
        f"SOURCES = [\n{source_list}\n]\n"
        f"OUTPUTS = [\n{output_list}\n]\n\n"
        "def read_source(source: dict) -> pd.DataFrame:\n"
        "    path = source.get('path') or source.get('name')\n"
        "    if not path:\n"
        "        return pd.DataFrame()\n"
        "    if str(path).lower().endswith('.csv'):\n"
        "        return pd.read_csv(path)\n"
        "    raise NotImplementedError(f\"Add reader for source: {source}\")\n\n"
        "def transform(dataframes: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:\n"
        "    # TODO: Replace this scaffold with converted Formula/Filter/Join/Summarize/Macro logic.\n"
        "    first = next(iter(dataframes.values()), pd.DataFrame())\n"
        "    return {output.get('name') or f'output_{index}': first.copy() for index, output in enumerate(OUTPUTS, start=1)}\n\n"
        "def write_outputs(outputs: dict[str, pd.DataFrame], output_dir: str = 'output') -> None:\n"
        "    target_dir = Path(output_dir)\n"
        "    target_dir.mkdir(parents=True, exist_ok=True)\n"
        "    for name, frame in outputs.items():\n"
        "        safe_name = Path(str(name)).stem or 'output'\n"
        "        frame.to_csv(target_dir / f'{safe_name}.csv', index=False)\n\n"
        "def main() -> None:\n"
        "    dataframes = {source.get('name') or f'source_{index}': read_source(source) for index, source in enumerate(SOURCES, start=1)}\n"
        "    write_outputs(transform(dataframes))\n\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )
    files = {
        "README.md": (
            f"# {workflow.get('name', 'Alteryx Workflow')} Python Scaffold\n\n"
            "Generated for workflows that need Python execution, iterative behavior, API orchestration, or manual remediation.\n\n"
            "Run locally with `python pipeline.py` after updating source paths and transformation logic.\n"
        ),
        "requirements.txt": "pandas>=2.2.0\nrequests>=2.31.0\n",
        "pipeline.py": pipeline_py,
        "macro_remediation_plan.json": json.dumps(macro_plan, indent=2),
    }
    return {
        "success": True,
        "project_name": project_name,
        "target": "python",
        "files": files,
        "file_count": len(files),
        "source_count": len(sources),
        "output_count": len(outputs),
        "output_targets": outputs,
        "macro_plan": macro_plan,
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
    checks = [
        {
            "name": "Workflow parsed",
            "status": "pass" if workflow.get("toolCount", 0) > 0 else "warning",
            "detail": f"{workflow.get('toolCount', 0)} tool(s) detected.",
        },
        {
            "name": "Source detected",
            "status": "pass" if workflow.get("dataSources") else "warning",
            "detail": f"{len(workflow.get('dataSources') or [])} source candidate(s) detected.",
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
