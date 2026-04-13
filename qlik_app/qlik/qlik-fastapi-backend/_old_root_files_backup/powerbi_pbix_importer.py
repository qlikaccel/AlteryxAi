"""
Power BI PBIX Import Helper

Imports a PBIX into a workspace and optionally waits for completion.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

PBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"


class PowerBIImportError(Exception):
    pass


def _headers(access_token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
    }


def _extract_primary_ids(import_payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    datasets = import_payload.get("datasets") or []
    reports = import_payload.get("reports") or []

    dataset_id = datasets[0].get("id") if datasets else None
    report_id = reports[0].get("id") if reports else None

    return {
        "dataset_id": dataset_id,
        "report_id": report_id,
    }


def import_pbix_and_wait(
    access_token: str,
    workspace_id: str,
    dataset_display_name: str,
    pbix_bytes: bytes,
    pbix_filename: str,
    name_conflict: str = "CreateOrOverwrite",
    timeout_seconds: int = 600,
    poll_interval_seconds: int = 5,
) -> Dict[str, Any]:
    """
    Import PBIX file into workspace and wait for completion.

    Returns a dict containing import status and generated dataset/report IDs.
    """
    if not access_token:
        raise PowerBIImportError("access_token is required")
    if not workspace_id:
        raise PowerBIImportError("workspace_id is required")
    if not dataset_display_name:
        raise PowerBIImportError("dataset_display_name is required")
    if not pbix_bytes:
        raise PowerBIImportError("pbix file is empty")

    import_url = f"{PBI_API_ROOT}/groups/{workspace_id}/imports"
    params = {
        "datasetDisplayName": dataset_display_name,
        "nameConflict": name_conflict,
    }

    files = {
        "file": (
            pbix_filename,
            pbix_bytes,
            "application/octet-stream",
        )
    }

    response = requests.post(
        import_url,
        headers=_headers(access_token),
        params=params,
        files=files,
        timeout=180,
    )

    if response.status_code not in (200, 201, 202):
        raise PowerBIImportError(
            f"PBIX import request failed: HTTP {response.status_code} - {response.text[:500]}"
        )

    payload = response.json() if response.text else {}
    import_id = payload.get("id")
    if not import_id:
        raise PowerBIImportError("Import started but no import ID was returned")

    import_status_url = f"{PBI_API_ROOT}/groups/{workspace_id}/imports/{import_id}"
    deadline = time.time() + timeout_seconds

    last_payload: Dict[str, Any] = payload
    while time.time() < deadline:
        status_resp = requests.get(
            import_status_url,
            headers=_headers(access_token),
            timeout=60,
        )

        if status_resp.status_code != 200:
            raise PowerBIImportError(
                f"Failed to get import status: HTTP {status_resp.status_code} - {status_resp.text[:500]}"
            )

        last_payload = status_resp.json() if status_resp.text else {}
        state = (last_payload.get("importState") or "").lower()

        if state == "succeeded":
            ids = _extract_primary_ids(last_payload)
            return {
                "success": True,
                "import_id": import_id,
                "import_state": last_payload.get("importState"),
                "dataset_id": ids.get("dataset_id"),
                "report_id": ids.get("report_id"),
                "raw": last_payload,
            }

        if state in {"failed", "cancelled"}:
            raise PowerBIImportError(
                f"PBIX import finished with state '{last_payload.get('importState')}'"
            )

        time.sleep(poll_interval_seconds)

    raise PowerBIImportError(
        f"Timed out waiting for import completion after {timeout_seconds} seconds"
    )
