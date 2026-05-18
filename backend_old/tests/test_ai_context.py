from app.services.ai_context import build_llm_messages, build_migration_context
from app.services.llm_routing import decide_llm_route


def test_context_hash_changes_when_formula_changes():
    workflow_a = {
        "id": "wf1",
        "name": "Formula workflow",
        "complexity": "medium",
        "workflowNodes": [{"id": "1", "plugin": "Formula", "expression": "[Amount] * 2"}],
    }
    workflow_b = {
        **workflow_a,
        "workflowNodes": [{"id": "1", "plugin": "Formula", "expression": "[Amount] * 3"}],
    }

    context_a = build_migration_context(workflow_a)
    context_b = build_migration_context(workflow_b)

    assert context_a.context_hash != context_b.context_hash


def test_complex_context_routes_to_anthropic_with_openai_fallback():
    workflow = {
        "id": "wf2",
        "name": "Macro workflow",
        "complexity": "high",
        "workflowNodes": [{"id": "1", "plugin": "Join"}],
        "macroDependencies": [{"name": "BatchRegion", "macroType": "batch", "uploaded": True}],
    }
    steps = [{"node_id": "1", "tool": "join", "mapped": True, "note": "join partner tables must be bound"}]

    context = build_migration_context(workflow, steps, skipped_node_ids=["99"])
    decision = decide_llm_route(context)

    assert decision.route == "llm_assisted_remediation"
    assert decision.primary_provider == "anthropic"
    assert decision.fallback_provider == "openai"
    assert decision.requires_human_review is True


def test_llm_messages_include_context_hash_and_strict_json_contract():
    context = build_migration_context({"id": "wf3", "name": "Simple", "complexity": "low"})

    messages = build_llm_messages(context)

    assert messages[0]["role"] == "system"
    assert context.context_hash in messages[1]["content"]
    assert "strict_output" in messages[1]["content"]
    assert "retry_policy" in messages[1]["content"]


def test_unresolved_context_is_capped_and_reports_controls():
    workflow = {"id": "wf4", "name": "Many unresolved", "complexity": "high"}
    steps = [
        {"node_id": str(index), "tool": "join", "mapped": True, "note": "requires review"}
        for index in range(25)
    ]

    context = build_migration_context(workflow, steps)

    assert len(context.unresolved_constructs) <= context.context_controls["unresolved_construct_limit"]
    assert context.context_controls["unresolved_construct_total"] == 25
    assert context.context_controls["context_truncated"] is True


def test_partial_support_routes_to_deterministic_with_risk_flag():
    workflow = {"id": "wf5", "name": "Partial formula", "complexity": "low"}
    steps = [{"node_id": "1", "tool": "formula", "mapped": True, "note": "partial edge-case parameter risk"}]

    context = build_migration_context(workflow, steps)
    decision = decide_llm_route(context)

    assert decision.route == "deterministic_with_risk_flag"
    assert decision.primary_provider == ""
