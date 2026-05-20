from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .architecture_config import config_bool, config_float, config_int, config_list, load_architecture_config


CONTEXT_SCHEMA_VERSION = "2026-05-16-context-v1"
ARCHITECTURE_CONFIG = load_architecture_config()


LLM_ROUTE_TOOL_KEYS = set(config_list(ARCHITECTURE_CONFIG, "routing", "complex_tools", []))
PARTIAL_SUPPORT_TOOL_KEYS = set(config_list(ARCHITECTURE_CONFIG, "routing", "partial_support_tools", []))


@dataclass(frozen=True)
class ConversionPolicy:
    deterministic_first: bool = config_bool(ARCHITECTURE_CONFIG, "routing", "deterministic_first", True)
    llm_is_source_of_truth: bool = config_bool(ARCHITECTURE_CONFIG, "routing", "llm_is_source_of_truth", False)
    llm_primary_for_complex_workflows: str = str(ARCHITECTURE_CONFIG.get("routing", {}).get("llm_primary_for_complex_workflows") or "anthropic")
    llm_fallback_for_complex_workflows: str = str(ARCHITECTURE_CONFIG.get("routing", {}).get("llm_fallback_for_complex_workflows") or "openai")
    llm_for_brd_and_summary: str = str(ARCHITECTURE_CONFIG.get("routing", {}).get("llm_for_brd_and_summary") or "huggingface")
    require_deterministic_validation: bool = True
    target_platforms: tuple[str, ...] = ("power_query_m", "dbt", "dataform", "python")
    max_llm_retries: int = config_int(ARCHITECTURE_CONFIG, "routing", "max_llm_retries", 2)


@dataclass(frozen=True)
class ValidationContract:
    row_count: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "row_count", True)
    column_presence: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "column_presence", True)
    not_null_counts: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "not_null_counts", True)
    min_max: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "min_max", True)
    sum_average: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "sum_average", True)
    distinct_counts: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "distinct_counts", False)
    numeric_tolerance_absolute: float = config_float(ARCHITECTURE_CONFIG, "validation", "numeric_tolerance_absolute", 0.0001)
    numeric_tolerance_relative: float = config_float(ARCHITECTURE_CONFIG, "validation", "numeric_tolerance_relative", 0.001)
    llm_allowed_for_verdict: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "llm_allowed_for_verdict", False)
    llm_allowed_for_explanation: bool = config_bool(ARCHITECTURE_CONFIG, "validation", "llm_allowed_for_explanation", True)


@dataclass(frozen=True)
class WorkflowGraphContext:
    workflow_id: str
    workflow_name: str
    complexity: str
    tool_count: int
    connection_count: int
    selected_node_ids: tuple[str, ...] = ()
    skipped_node_ids: tuple[str, ...] = ()
    nodes: tuple[dict[str, Any], ...] = ()
    edges: tuple[dict[str, Any], ...] = ()
    macros: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MigrationContext:
    schema_version: str
    workflow: WorkflowGraphContext
    sources: tuple[dict[str, Any], ...] = ()
    conversion_steps: tuple[dict[str, Any], ...] = ()
    unresolved_constructs: tuple[dict[str, Any], ...] = ()
    validation_contract: ValidationContract = field(default_factory=ValidationContract)
    policy: ConversionPolicy = field(default_factory=ConversionPolicy)
    target_context: dict[str, Any] = field(default_factory=dict)
    context_controls: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True, separators=(",", ":"))

    @property
    def context_hash(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()


def compact_node(node: dict[str, Any]) -> dict[str, Any]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    expression = (
        config.get("filterExpression")
        or config.get("expression")
        or config.get("formula")
        or node.get("expression")
        or node.get("configurationText")
        or ""
    )
    return {
        "id": str(node.get("id") or ""),
        "plugin": str(node.get("plugin") or node.get("tool") or "Unknown"),
        "tool": str(node.get("tool") or node.get("tool_key") or ""),
        "expression_excerpt": str(expression)[:1000],
        "fields": _compact_fields(node.get("fields") or config.get("fields") or []),
    }


def compact_edge(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": str(edge.get("from") or ""),
        "to": str(edge.get("to") or ""),
        "from_anchor": str(edge.get("fromAnchor") or ""),
        "to_anchor": str(edge.get("toAnchor") or ""),
    }


def build_migration_context(
    workflow: dict[str, Any],
    conversion_steps: list[dict[str, Any]] | None = None,
    *,
    selected_node_ids: list[str] | None = None,
    skipped_node_ids: list[str] | None = None,
    target_context: dict[str, Any] | None = None,
    validation_contract: ValidationContract | None = None,
    policy: ConversionPolicy | None = None,
) -> MigrationContext:
    node_limit = config_int(ARCHITECTURE_CONFIG, "context", "node_limit", 120)
    edge_limit = config_int(ARCHITECTURE_CONFIG, "context", "edge_limit", 200)
    source_limit = config_int(ARCHITECTURE_CONFIG, "context", "source_limit", 40)
    macro_limit = config_int(ARCHITECTURE_CONFIG, "context", "macro_limit", 40)
    step_limit = config_int(ARCHITECTURE_CONFIG, "context", "conversion_step_limit", 120)
    unresolved_limit = config_int(ARCHITECTURE_CONFIG, "context", "unresolved_construct_limit", 10)
    unresolved_batch_size = config_int(ARCHITECTURE_CONFIG, "context", "unresolved_batch_size", unresolved_limit)
    nodes = tuple(compact_node(node) for node in (workflow.get("workflowNodes") or [])[:node_limit])
    edges = tuple(compact_edge(edge) for edge in (workflow.get("workflowEdges") or [])[:edge_limit])
    steps = tuple(_compact_conversion_step(step) for step in (conversion_steps or [])[:step_limit])
    all_unresolved = _rank_unresolved_constructs(_unresolved_constructs(workflow, list(steps)))
    unresolved = tuple(all_unresolved[:unresolved_limit])
    graph = WorkflowGraphContext(
        workflow_id=str(workflow.get("id") or workflow.get("workflow_id") or ""),
        workflow_name=str(workflow.get("name") or workflow.get("workflow_name") or "Selected workflow"),
        complexity=str(workflow.get("complexity") or "unknown"),
        tool_count=int(workflow.get("toolCount") or len(workflow.get("workflowNodes") or []) or 0),
        connection_count=int(workflow.get("connectionCount") or len(workflow.get("workflowEdges") or []) or 0),
        selected_node_ids=tuple(str(item) for item in (selected_node_ids or [])),
        skipped_node_ids=tuple(str(item) for item in (skipped_node_ids or [])),
        nodes=nodes,
        edges=edges,
        macros=tuple(_compact_macro(item) for item in (workflow.get("macroDependencies") or [])[:macro_limit]),
    )
    return MigrationContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        workflow=graph,
        sources=tuple(_compact_source(item) for item in (workflow.get("dataSources") or [])[:source_limit]),
        conversion_steps=steps,
        unresolved_constructs=unresolved,
        validation_contract=validation_contract or ValidationContract(),
        policy=policy or ConversionPolicy(),
        target_context=target_context or {},
        context_controls={
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "source_limit": source_limit,
            "macro_limit": macro_limit,
            "conversion_step_limit": step_limit,
            "unresolved_construct_total": len(all_unresolved),
            "unresolved_construct_included": len(unresolved),
            "unresolved_construct_limit": unresolved_limit,
            "unresolved_batch_size": unresolved_batch_size,
            "context_truncated": len(all_unresolved) > len(unresolved),
        },
    )


def build_llm_messages(context: MigrationContext) -> list[dict[str, str]]:
    payload = {
        "task": "Analyze unresolved Alteryx migration constructs and return remediation guidance.",
        "strict_output": {
            "format": "json",
            "keys": ["summary", "remediation_steps", "risks", "validation_focus", "requires_human_review"],
        },
        "rules": [
            "Do not invent source fields or target behavior.",
            "Do not produce a final validation verdict.",
            "Use deterministic validation metrics as the source of truth.",
            "Prefer rule-based mappings where the context marks them as mapped.",
            "If generated logic fails deterministic parse or validation, use the failure reason for one bounded retry.",
        ],
        "retry_policy": {
            "max_retries": context.policy.max_llm_retries,
            "after_retry_exhausted": "manual_review",
        },
        "context_hash": context.context_hash,
        "context": context.to_dict(),
    }
    return [
        {
            "role": "system",
            "content": "You are an enterprise Alteryx migration engineer. Return strict JSON only.",
        },
        {
            "role": "user",
            "content": json.dumps(payload, sort_keys=True, ensure_ascii=True),
        },
    ]


def _compact_fields(fields: Any) -> list[dict[str, str]]:
    compacted = []
    if not isinstance(fields, list):
        return compacted
    for field_item in fields[:80]:
        if not isinstance(field_item, dict):
            continue
        name = str(field_item.get("name") or field_item.get("field") or "").strip()
        if not name:
            continue
        compacted.append(
            {
                "name": name,
                "type": str(field_item.get("type") or field_item.get("dataType") or "unknown"),
                "rename": str(field_item.get("rename") or field_item.get("alias") or ""),
            }
        )
    return compacted


def _compact_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(source.get("name") or source.get("path") or "source"),
        "type": str(source.get("type") or "unknown"),
        "path_hint": str(source.get("path") or "")[:500],
        "row_count": source.get("row_count") or source.get("no_of_rows") or source.get("rowCount"),
        "fields": _compact_fields(source.get("fields") or []),
    }


def _compact_macro(macro: dict[str, Any]) -> dict[str, Any]:
    definition = macro.get("definition") if isinstance(macro.get("definition"), dict) else {}
    return {
        "name": str(macro.get("name") or macro.get("path") or "macro"),
        "type": str(macro.get("macroType") or "macro"),
        "uploaded": bool(macro.get("uploaded")),
        "status": str(macro.get("status") or ""),
        "tool_count": definition.get("toolCount") or len(definition.get("workflowNodes") or []),
        "unsupported_tools": definition.get("unsupportedTools") or [],
    }


def _compact_conversion_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(step.get("node_id") or ""),
        "tool": str(step.get("tool") or ""),
        "mapped": bool(step.get("mapped")),
        "m_function": str(step.get("m_function") or ""),
        "note": str(step.get("note") or "")[:1000],
    }


def _unresolved_constructs(workflow: dict[str, Any], conversion_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unresolved = []
    for step in conversion_steps:
        tool = str(step.get("tool") or "").lower()
        note = str(step.get("note") or "")
        partial = tool in PARTIAL_SUPPORT_TOOL_KEYS and re.search(r"edge|partial|warning|risk|schema|mismatch|review", note, re.I)
        if not step.get("mapped") or tool in LLM_ROUTE_TOOL_KEYS or partial or re.search(r"manual|review|required|preserved", note, re.I):
            unresolved.append(
                {
                    "node_id": step.get("node_id"),
                    "tool": tool,
                    "reason": note or "requires semantic review",
                    "route_hint": "partial_support" if partial else "llm_complex" if tool in LLM_ROUTE_TOOL_KEYS else "review",
                }
            )
    for macro in workflow.get("macroDependencies") or []:
        unresolved.append(
            {
                "node_id": "",
                "tool": str(macro.get("macroType") or "macro").lower(),
                "reason": f"macro dependency: {macro.get('name') or macro.get('path')}",
                "route_hint": "llm_complex",
            }
        )
    return unresolved


def _rank_unresolved_constructs(unresolved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(item: dict[str, Any]) -> tuple[int, int, str]:
        route_hint = str(item.get("route_hint") or "")
        tool = str(item.get("tool") or "")
        reason = str(item.get("reason") or "")
        priority = 3 if route_hint == "llm_complex" else 2 if route_hint == "partial_support" else 1
        blocking = 1 if re.search(r"block|fail|mismatch|required|manual", reason, re.I) else 0
        return (priority, blocking, tool)

    return sorted(unresolved, key=score, reverse=True)
