import os

from unittest.mock import Mock

from app.services.alteryx_migration_engine import _python_tool_steps, generate_python_project
from app.services.alteryx_python_publisher import publish_python_project_to_bigquery


def test_python_tool_steps_extracts_python_components():
    workflow = {
        "workflowNodes": [
            {
                "id": "1",
                "plugin": "Python",
                "config": {"pythonCode": "frames['out']=frames['in']"},
            },
            {
                "id": "2",
                "plugin": "Formula",
                "config": {"expression": "[A] + [B]"},
            },
        ]
    }
    steps = _python_tool_steps(workflow)
    assert len(steps) == 1
    assert steps[0]["id"] == "1"
    assert steps[0]["plugin"] == "Python"
    assert "frames['out']=frames['in']" in steps[0]["code"]


def test_generate_python_project_includes_python_steps_and_env_example():
    workflow = {
        "name": "TestWorkflow",
        "dataSources": [],
        "outputTargets": [],
        "workflowNodes": [
            {
                "id": "1",
                "plugin": "Python",
                "config": {"pythonCode": "frames['out']=frames['in']"},
            }
        ],
    }
    project = generate_python_project(workflow)
    assert project["success"] is True
    assert "pipeline.py" in project["files"]
    assert "alteryx_python_steps.py" in project["files"]
    assert "ALLOW_ALTERYX_PYTHON_EXEC=1" in project["files"][".env.example"]
    assert "apply_python_tool_steps" in project["files"]["alteryx_python_steps.py"]


def test_publish_python_project_enables_alteryx_python_exec(monkeypatch, tmp_path):
    project = {
        "project_name": "test_python_pipeline",
        "files": {
            "pipeline.py": "print('hello')",
        },
        "sources": [],
    }
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCP_BIGQUERY_DATASET", "test_dataset")
    monkeypatch.setenv("GCP_BIGQUERY_LOCATION", "US")

    captured_env = {}

    def fake_run_python_pipeline(command, cwd, env, timeout_seconds):
        captured_env.update(env)
        return {"success": True, "command": " ".join(command), "duration_seconds": 0}

    monkeypatch.setattr("app.services.alteryx_python_publisher._run_python_pipeline", fake_run_python_pipeline)
    monkeypatch.setattr("app.services.alteryx_python_publisher._create_bigquery_dataset", lambda project_id, dataset, location, env: {"dataset": f"{project_id}.{dataset}", "created": False, "location": location})
    result = publish_python_project_to_bigquery(project)
    assert result["success"] is True
    assert captured_env.get("ALLOW_ALTERYX_PYTHON_EXEC") == "1"