from app.routers.alteryx_router import extract_workflow_items


def test_extract_workflow_items_accepts_direct_list():
    payload = [{"id": "wf-1", "name": "Workflow 1"}]

    assert extract_workflow_items(payload) == payload


def test_extract_workflow_items_supports_nested_payload_shapes():
    payload = {
        "payload": {
            "data": {
                "items": [
                    {"id": "wf-2", "name": "Workflow 2"},
                    {"id": "wf-3", "name": "Workflow 3"},
                ]
            }
        }
    }

    assert extract_workflow_items(payload) == payload["payload"]["data"]["items"]


def test_extract_workflow_items_supports_flow_and_asset_wrappers():
    payload = {
        "assets": [
            {"assetId": "wf-asset-1", "assetName": "Workflow Asset"}
        ]
    }

    assert extract_workflow_items(payload) == payload["assets"]


def test_extract_workflow_items_ignores_non_dict_entries():
    payload = {
        "results": [
            {"id": "wf-4", "name": "Workflow 4"},
            "invalid",
            123,
        ]
    }

    assert extract_workflow_items(payload) == [{"id": "wf-4", "name": "Workflow 4"}]
