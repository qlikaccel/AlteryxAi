from app.services.alteryx_migration_engine import generate_dbt_project


def test_dbt_schema_descriptions_normalize_windows_paths():
    workflow = {
        "name": "Category Batch Macro",
        "sourceFile": "category_batch_macro.yxmd",
        "dataSources": [
            {
                "name": r"e: C:\My_Project\kavie\alteryx_demo\data\large_fact_100k.csv",
                "type": "csv",
                "path": r"e: C:\My_Project\kavie\alteryx_demo\data\large_fact_100k.csv",
            }
        ],
        "workflowNodes": [],
        "workflowEdges": [],
    }

    schema_yml = generate_dbt_project(workflow)["files"]["models/schema.yml"]

    assert r"C:\My_Project" not in schema_yml
    assert "C:/My_Project" in schema_yml
    assert 'description: "Landed source for e: C:/My_Project' in schema_yml
