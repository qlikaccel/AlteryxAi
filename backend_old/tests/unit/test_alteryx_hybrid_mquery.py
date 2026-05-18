from app.services.alteryx_converter import choose_generation_strategy, convert_workflow_to_m


def test_simple_supported_workflow_uses_rule_based_generation():
    workflow = {
        "name": "Simple Filter",
        "toolCount": 3,
        "connectionCount": 2,
        "complexity": "low",
        "unsupportedToolCount": 0,
        "workflowNodes": [
            {"id": "1", "plugin": "AlteryxBasePluginsGui.DbFileInput.DbFileInput"},
            {"id": "2", "plugin": "AlteryxBasePluginsGui.Filter.Filter", "expression": "[Region] = \"West\""},
            {"id": "3", "plugin": "AlteryxBasePluginsGui.Browse.Browse"},
        ],
    }

    strategy = choose_generation_strategy(workflow)

    assert strategy["generation_method"] == "rule_based"
    assert strategy["generation_label"] == "Rule-based mapping"


def test_complex_join_workflow_uses_llm_assisted_generation_metadata():
    workflow = {
        "name": "Complex Join",
        "toolCount": 8,
        "connectionCount": 9,
        "complexity": "medium",
        "unsupportedToolCount": 0,
        "workflowNodes": [
            {"id": "1", "plugin": "AlteryxBasePluginsGui.DbFileInput.DbFileInput"},
            {"id": "2", "plugin": "AlteryxBasePluginsGui.Join.Join"},
            {"id": "3", "plugin": "AlteryxBasePluginsGui.Formula.Formula", "expression": 'IIF([Amount] > 0, "Y", "N")'},
        ],
    }
    source = {"name": "sales.csv", "type": "csv", "path": "sales.csv"}

    result = convert_workflow_to_m(workflow, source)

    assert result["generation_method"] == "llm"
    assert result["generation_label"] == "LLM-assisted mapping"
    assert "complex mapping tools" in result["routing_reason"]
    assert result["llm_mapping_guidance"]
