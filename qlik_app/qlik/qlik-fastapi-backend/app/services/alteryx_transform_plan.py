"""Canonical Alteryx transformation plan.

This module is the target-neutral layer for Alteryx migration.  It converts
parsed workflow metadata into one logical plan that can be rendered as M Query,
dbt SQL, Dataform SQLX, Python, or future targets.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


SQL_FRIENDLY_OPS = {
    "input",
    "output",
    "select",
    "filter",
    "formula",
    "summarize",
    "join",
    "join_multiple",
    "union",
    "unique",
    "sort",
    "sample",
    "record_id",
    "data_cleansing",
    "multi_field_formula",
}

PYTHON_FRIENDLY_OPS = SQL_FRIENDLY_OPS | {
    "multi_row_formula",
    "regex",
    "transpose",
    "cross_tab",
    "python",
}

MANUAL_REVIEW_OPS = {
    "download",
    "api",
    "run_command",
    "email",
    "unknown",
}

BLOCKING_PUBLISH_STATUSES = {"partial", "manual_review", "python_supported"}


def _stable_id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:12]


def _plugin_lower(node: dict[str, Any]) -> str:
    return str(node.get("plugin") or "").lower()


def detect_operation_type(node: dict[str, Any]) -> str:
    plugin = _plugin_lower(node)
    if "macroinput" in plugin:
        return "macro_input"
    if "macrooutput" in plugin:
        return "macro_output"
    if "dbfileinput" in plugin or "inputdata" in plugin or "textinput" in plugin or "lockininput" in plugin:
        return "input"
    if "dbfileoutput" in plugin or "outputdata" in plugin:
        return "output"
    if "joinmultiple" in plugin:
        return "join_multiple"
    if "join" in plugin:
        return "join"
    if "multirowformula" in plugin or "multi-row" in plugin:
        return "multi_row_formula"
    if "multifieldformula" in plugin or "multi-field" in plugin:
        return "multi_field_formula"
    if "formula" in plugin:
        return "formula"
    if "filter" in plugin and "summarize" not in plugin:
        return "filter"
    if "summarize" in plugin:
        return "summarize"
    if "select" in plugin:
        return "select"
    if "union" in plugin:
        return "union"
    if "unique" in plugin:
        return "unique"
    if "sort" in plugin:
        return "sort"
    if "sample" in plugin:
        return "sample"
    if "recordid" in plugin or "record id" in plugin:
        return "record_id"
    if "regex" in plugin:
        return "regex"
    if "cleansing" in plugin or "datacleanse" in plugin:
        return "data_cleansing"
    if "transpose" in plugin:
        return "transpose"
    if "crosstab" in plugin or "cross tab" in plugin:
        return "cross_tab"
    if "python" in plugin:
        return "python"
    if "download" in plugin:
        return "download"
    if "runcommand" in plugin:
        return "run_command"
    if "email" in plugin:
        return "email"
    if "browse" in plugin:
        return "browse"
    return "unknown"


def _predecessors(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    preds: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if source and target:
            preds.setdefault(target, []).append(source)
    return preds


def _successors(edges: list[dict[str, Any]]) -> dict[str, list[str]]:
    succs: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge.get("from") or edge.get("source") or "")
        target = str(edge.get("to") or edge.get("target") or "")
        if source and target:
            succs.setdefault(source, []).append(target)
    return succs


def _topological_nodes(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(node.get("id")): node for node in nodes if node.get("id") is not None}
    preds = _predecessors(edges)
    remaining = set(by_id)
    ordered_ids: list[str] = []
    while remaining:
        ready = sorted(node_id for node_id in remaining if all(pred not in remaining for pred in preds.get(node_id, [])))
        if not ready:
            ordered_ids.extend(sorted(remaining))
            break
        ordered_ids.extend(ready)
        remaining.difference_update(ready)
    return [by_id[node_id] for node_id in ordered_ids if node_id in by_id]


def _operation_status(op_type: str, config: dict[str, Any]) -> tuple[str, str]:
    if op_type in {"input", "output", "browse", "macro_input", "macro_output"}:
        return "pass_through", "Boundary/checkpoint tool."
    if op_type in SQL_FRIENDLY_OPS:
        if op_type in {"select", "filter", "formula", "summarize", "join", "sort", "sample"} and not config:
            return "partial", "Tool detected but detailed configuration was not extracted."
        return "supported", "Can be rendered by shared transformation plan."
    if op_type in PYTHON_FRIENDLY_OPS:
        return "python_supported", "Requires Python or target-specific advanced rendering."
    if op_type in MANUAL_REVIEW_OPS:
        return "manual_review", "Requires connector/orchestration/manual remediation."
    return "manual_review", "Unsupported or unknown Alteryx tool family."


def _macro_type(value: dict[str, Any]) -> str:
    text = " ".join(str(value.get(key) or "") for key in ("macroType", "type", "path", "name")).lower()
    if "batch" in text:
        return "batch"
    if "iterative" in text:
        return "iterative"
    if "standard" in text:
        return "standard"
    return "standard"


def _macro_status(macro: dict[str, Any]) -> tuple[str, str]:
    macro_kind = _macro_type(macro)
    definition = macro.get("definition") or {}
    uploaded = bool(macro.get("uploaded")) or bool(definition)
    if not uploaded:
        return "missing", "Macro definition was referenced but not uploaded."
    if macro_kind == "standard":
        nested = build_transform_plan(definition, include_llm_recommendations=False) if definition else {}
        coverage = nested.get("coverage", {})
        if coverage.get("manual_review_count", 0):
            return "partial", "Standard macro contains tools requiring review."
        return "supported", "Standard macro can be expanded into the shared plan."
    if macro_kind == "batch":
        return "parameterized", "Batch macro requires control-table parameterization."
    if macro_kind == "iterative":
        return "orchestration_required", "Iterative macro requires loop/recursive SQL/Python orchestration."
    return "manual_review", "Macro type requires semantic review."


def _llm_recommendations(plan: dict[str, Any]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for op in plan.get("operations", []):
        if op.get("status") in {"partial", "manual_review", "python_supported"}:
            recommendations.append({
                "operation_id": op.get("id"),
                "tool": op.get("tool"),
                "recommendation": "Use LLM-assisted mapping to translate or remediate this operation before production publish.",
                "reason": op.get("reason"),
            })
    for macro in plan.get("macros", []):
        if macro.get("status") != "supported":
            recommendations.append({
                "operation_id": macro.get("id"),
                "tool": f"{macro.get('macro_type')} macro",
                "recommendation": "Use LLM-assisted macro interpretation and validate against Alteryx output.",
                "reason": macro.get("reason"),
            })
    return recommendations


def build_transform_plan(workflow: dict[str, Any], include_llm_recommendations: bool = True) -> dict[str, Any]:
    nodes = list(workflow.get("workflowNodes") or [])
    edges = list(workflow.get("workflowEdges") or [])
    preds = _predecessors(edges)
    succs = _successors(edges)

    operations: list[dict[str, Any]] = []
    for index, node in enumerate(_topological_nodes(nodes, edges), start=1):
        node_id = str(node.get("id") or index)
        op_type = detect_operation_type(node)
        config = dict(node.get("config") or {})
        status, reason = _operation_status(op_type, config)
        operations.append({
            "id": f"op_{node_id}",
            "sequence": index,
            "node_id": node_id,
            "tool": op_type,
            "plugin": node.get("plugin") or "Unknown",
            "status": status,
            "reason": reason,
            "inputs": preds.get(node_id, []),
            "outputs": succs.get(node_id, []),
            "config": config,
            "expression": node.get("expression") or "",
            "configurationText": node.get("configurationText") or "",
        })

    macros: list[dict[str, Any]] = []
    for index, macro in enumerate(workflow.get("macroDependencies") or [], start=1):
        macro_kind = _macro_type(macro)
        status, reason = _macro_status(macro)
        definition = macro.get("definition") or {}
        nested_plan = build_transform_plan(definition, include_llm_recommendations=False) if definition else None
        macros.append({
            "id": f"macro_{index}_{_stable_id(str(macro.get('path') or macro.get('name') or index))}",
            "name": macro.get("name") or macro.get("path") or f"macro_{index}",
            "path": macro.get("path") or "",
            "macro_type": macro_kind,
            "status": status,
            "reason": reason,
            "control_parameter": macro.get("controlParameter") or "",
            "iteration_limit": macro.get("iterationLimit") or "",
            "stop_condition": macro.get("stopCondition") or "",
            "nested_plan": nested_plan,
        })

    blocking = [
        op for op in operations
        if op.get("status") in {"manual_review"}
    ] + [
        macro for macro in macros
        if macro.get("status") in {"missing", "manual_review", "orchestration_required"}
    ]
    partial = [
        op for op in operations
        if op.get("status") in {"partial", "python_supported"}
    ] + [
        macro for macro in macros
        if macro.get("status") in {"partial", "parameterized"}
    ]
    renderable = [
        op for op in operations
        if op.get("status") in {"supported", "pass_through", "python_supported"}
    ]
    coverage_score = round((len(renderable) / max(len(operations), 1)) * 100)
    coverage_status = "fully_converted"
    if blocking:
        coverage_status = "manual_remediation_required"
    elif partial:
        coverage_status = "partially_converted"

    plan = {
        "success": True,
        "plan_id": _stable_id(str(workflow.get("id") or workflow.get("name") or ""), str(len(nodes)), str(len(edges))),
        "workflow_id": workflow.get("id") or "",
        "workflow_name": workflow.get("name") or "Alteryx workflow",
        "sources": workflow.get("dataSources") or [],
        "outputs": workflow.get("outputTargets") or [],
        "operations": operations,
        "macros": macros,
        "coverage": {
            "status": coverage_status,
            "score": coverage_score,
            "operation_count": len(operations),
            "renderable_count": len(renderable),
            "partial_count": len(partial),
            "manual_review_count": len(blocking),
            "macro_count": len(macros),
        },
        "recommendations": [],
    }
    if include_llm_recommendations:
        plan["recommendations"] = _llm_recommendations(plan)
    return plan


def transform_operations(plan: dict[str, Any], include_boundaries: bool = False) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    boundary_tools = {"input", "output", "browse", "macro_input", "macro_output"}
    for op in plan.get("operations") or []:
        tool = str(op.get("tool") or "")
        if not include_boundaries and tool in boundary_tools:
            continue
        operations.append({
            "tool": tool,
            "tool_id": op.get("node_id"),
            "config": op.get("config") or {},
            "status": op.get("status"),
            "reason": op.get("reason"),
            "inputs": op.get("inputs") or [],
            "outputs": op.get("outputs") or [],
        })
    return operations


def blocking_transform_items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    blocked = [
        {
            "type": "tool",
            "id": op.get("id"),
            "tool": op.get("tool"),
            "node_id": op.get("node_id"),
            "status": op.get("status"),
            "reason": op.get("reason"),
        }
        for op in (plan.get("operations") or [])
        if op.get("status") in BLOCKING_PUBLISH_STATUSES
    ]
    blocked += [
        {
            "type": "macro",
            "id": macro.get("id"),
            "tool": macro.get("macro_type"),
            "name": macro.get("name"),
            "status": macro.get("status"),
            "reason": macro.get("reason"),
        }
        for macro in (plan.get("macros") or [])
        if macro.get("status") != "supported"
    ]
    return blocked


def transform_publish_blocker_detail(plan: dict[str, Any], target: str) -> dict[str, Any] | None:
    coverage = plan.get("coverage") or {}
    status = str(coverage.get("status") or "").strip()
    if status in {"", "fully_converted"}:
        return None
    blocked = blocking_transform_items(plan)
    return {
        "message": (
            f"{target} publish is blocked because the workflow is not fully converted. "
            "The accelerator generated a transformation plan, but one or more Alteryx tools/macros still need "
            "LLM-assisted mapping, renderer support, or manual remediation before publishing."
        ),
        "coverage": coverage,
        "blocked_items": blocked[:25],
        "blocked_item_count": len(blocked),
        "override": "Set ALLOW_PARTIAL_TRANSFORM_PUBLISH=1 only for controlled demos or manual validation runs.",
    }
