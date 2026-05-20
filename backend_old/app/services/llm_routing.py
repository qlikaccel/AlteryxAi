from __future__ import annotations

from dataclasses import dataclass

from .ai_context import LLM_ROUTE_TOOL_KEYS, PARTIAL_SUPPORT_TOOL_KEYS, MigrationContext


@dataclass(frozen=True)
class RoutingDecision:
    route: str
    primary_provider: str
    fallback_provider: str
    reason: str
    requires_human_review: bool


def decide_llm_route(context: MigrationContext) -> RoutingDecision:
    unresolved_tools = {str(item.get("tool") or "").lower() for item in context.unresolved_constructs}
    complex_tools = sorted(tool for tool in unresolved_tools if tool in LLM_ROUTE_TOOL_KEYS)
    partial_tools = sorted(tool for tool in unresolved_tools if tool in PARTIAL_SUPPORT_TOOL_KEYS)
    skipped_count = len(context.workflow.skipped_node_ids)
    macro_count = len(context.workflow.macros)
    complexity = context.workflow.complexity.lower()

    if not context.unresolved_constructs and complexity == "low":
        return RoutingDecision(
            route="deterministic_only",
            primary_provider="",
            fallback_provider="",
            reason="Low-complexity workflow with no unresolved constructs.",
            requires_human_review=False,
        )

    if complex_tools or macro_count or skipped_count or complexity in {"medium", "high", "manual_review"}:
        indicators = []
        if complex_tools:
            indicators.append("complex tools: " + ", ".join(complex_tools[:8]))
        if macro_count:
            indicators.append(f"{macro_count} macro dependency item(s)")
        if skipped_count:
            indicators.append(f"{skipped_count} skipped graph node(s)")
        if complexity != "low":
            indicators.append(f"{complexity} complexity")
        return RoutingDecision(
            route="llm_assisted_remediation",
            primary_provider=context.policy.llm_primary_for_complex_workflows,
            fallback_provider=context.policy.llm_fallback_for_complex_workflows,
            reason="; ".join(indicators),
            requires_human_review=True,
        )

    if partial_tools:
        return RoutingDecision(
            route="deterministic_with_risk_flag",
            primary_provider="",
            fallback_provider="",
            reason="Partial-support tools should attempt deterministic conversion first: " + ", ".join(partial_tools[:8]),
            requires_human_review=False,
        )

    return RoutingDecision(
        route="llm_guidance_only",
        primary_provider=context.policy.llm_primary_for_complex_workflows,
        fallback_provider=context.policy.llm_fallback_for_complex_workflows,
        reason="Unresolved non-complex items need explanatory guidance but deterministic conversion remains primary.",
        requires_human_review=True,
    )
