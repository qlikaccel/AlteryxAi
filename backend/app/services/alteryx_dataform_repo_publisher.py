"""Publish generated Dataform projects into a persistent GCP Dataform workspace."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from app.services.gcp_credentials import service_account_info_from_env


DATAFORM_API_BASE = "https://dataform.googleapis.com/v1"
DATAFORM_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _safe_workspace_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return cleaned[:63] or "alteryx-generated"


def _load_credentials():
    try:
        import google.auth
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "google-auth is required for publishing to a GCP Dataform repository. "
            "Install backend requirements or run: pip install google-auth"
        ) from exc

    info = service_account_info_from_env()
    if info:
        return service_account.Credentials.from_service_account_info(info, scopes=DATAFORM_SCOPES)

    keyfile = _env("GOOGLE_APPLICATION_CREDENTIALS")
    if keyfile:
        keyfile_path = Path(keyfile)
        if not keyfile_path.exists():
            raise RuntimeError(f"Configured GOOGLE_APPLICATION_CREDENTIALS file was not found: {keyfile}")
        return service_account.Credentials.from_service_account_file(str(keyfile_path), scopes=DATAFORM_SCOPES)

    credentials, _ = google.auth.default(scopes=DATAFORM_SCOPES)
    return credentials


def _auth_headers() -> dict[str, str]:
    try:
        from google.auth.transport.requests import Request
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "google-auth is required for publishing to a GCP Dataform repository. "
            "Install backend requirements or run: pip install google-auth"
        ) from exc

    credentials = _load_credentials()
    credentials.refresh(Request())
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, headers: dict[str, str], **kwargs: Any) -> requests.Response:
    response = requests.request(method, url, headers=headers, timeout=60, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"Dataform API {method} {url} failed: {response.status_code} {response.text}")
    return response


def _request_allowing(method: str, url: str, headers: dict[str, str], allowed: set[int], **kwargs: Any) -> requests.Response:
    response = requests.request(method, url, headers=headers, timeout=60, **kwargs)
    if response.status_code >= 400 and response.status_code not in allowed:
        raise RuntimeError(f"Dataform API {method} {url} failed: {response.status_code} {response.text}")
    return response


def _workspace_exists(headers: dict[str, str], workspace_name: str) -> bool:
    get_url = f"{DATAFORM_API_BASE}/{workspace_name}"
    response = requests.get(get_url, headers=headers, timeout=60)
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    raise RuntimeError(f"Dataform API GET {get_url} failed: {response.status_code} {response.text}")


def _wait_for_workspace(headers: dict[str, str], workspace_name: str) -> None:
    timeout_seconds = int(_env("GCP_DATAFORM_WORKSPACE_READY_TIMEOUT_SECONDS", "45") or "45")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _workspace_exists(headers, workspace_name):
            return
        time.sleep(2)
    raise RuntimeError(
        f"Dataform workspace was created but was not readable within {timeout_seconds}s: {workspace_name}. "
        "Check GCP_DATAFORM_LOCATION and GCP_DATAFORM_REPOSITORY match an existing Dataform repository."
    )


def _ensure_workspace(headers: dict[str, str], repository_name: str, workspace_id: str) -> str:
    workspace_name = f"{repository_name}/workspaces/{workspace_id}"
    if _workspace_exists(headers, workspace_name):
        return workspace_name

    create_url = f"{DATAFORM_API_BASE}/{repository_name}/workspaces"
    created = _request_allowing(
        "POST",
        create_url,
        headers,
        {404, 409},
        params={"workspaceId": workspace_id},
        json={},
    )
    if created.status_code == 404:
        raise RuntimeError(
            f"Dataform repository was not found: {repository_name}. "
            "Set GCP_DATAFORM_LOCATION and GCP_DATAFORM_REPOSITORY to the exact repository id shown in GCP Dataform."
        )
    _wait_for_workspace(headers, workspace_name)
    return workspace_name


def _make_directories(headers: dict[str, str], workspace_name: str, files: dict[str, str]) -> list[str]:
    directories: list[str] = []
    seen: set[str] = set()
    for path in files:
        parts = Path(path).parts[:-1]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            if current not in seen:
                seen.add(current)
                directories.append(current.replace("\\", "/"))

    for directory in directories:
        url = f"{DATAFORM_API_BASE}/{workspace_name}:makeDirectory"
        response = _request_allowing("POST", url, headers, {404, 409}, json={"path": directory})
        if response.status_code == 404:
            _wait_for_workspace(headers, workspace_name)
            _request_allowing("POST", url, headers, {409}, json={"path": directory})
    return directories


def _write_file(headers: dict[str, str], workspace_name: str, path: str, content: str) -> None:
    url = f"{DATAFORM_API_BASE}/{workspace_name}:writeFile"
    payload = {
        "path": path.replace("\\", "/"),
        "contents": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    response = _request_allowing("POST", url, headers, {404}, json=payload)
    if response.status_code == 404:
        _wait_for_workspace(headers, workspace_name)
        _request("POST", url, headers, json=payload)


def _patched_files(files: dict[str, str], project_id: str, target_dataset: str, source_dataset: str, location: str) -> dict[str, str]:
    patched = {path: str(content) for path, content in files.items()}
    patched["workflow_settings.yaml"] = (
        f"defaultProject: {project_id}\n"
        f"defaultDataset: {target_dataset}\n"
        f"defaultLocation: {location}\n"
        "dataformCoreVersion: 3.0.0\n"
    )

    declarations_path = "definitions/declarations.js"
    if declarations_path in patched:
        text = patched[declarations_path]
        text = re.sub(r'database:\s*"[^"]*"', f'database: "{project_id}"', text)
        text = re.sub(r'schema:\s*"[^"]*"', f'schema: "{source_dataset}"', text)
        patched[declarations_path] = text
    return patched


def _write_project_files(headers: dict[str, str], workspace_name: str, files: dict[str, str]) -> list[str]:
    _make_directories(headers, workspace_name, files)
    written: list[str] = []
    for path, content in sorted(files.items()):
        _write_file(headers, workspace_name, path, str(content))
        written.append(path.replace("\\", "/"))
    return written


def _commit_workspace(
    headers: dict[str, str],
    workspace_name: str,
    paths: list[str],
    commit_message: str,
) -> bool:
    if _env("GCP_DATAFORM_COMMIT", "false").lower() not in {"1", "true", "yes"}:
        return False

    author_name = _env("GCP_DATAFORM_COMMIT_AUTHOR_NAME", "Alteryx Accelerator")
    author_email = _env("GCP_DATAFORM_COMMIT_AUTHOR_EMAIL", "alteryx-accelerator@example.com")
    url = f"{DATAFORM_API_BASE}/{workspace_name}:commit"
    _request(
        "POST",
        url,
        headers,
        json={
            "author": {"name": author_name, "emailAddress": author_email},
            "commitMessage": commit_message,
            "paths": paths,
        },
    )
    return True


def _compile_workspace(headers: dict[str, str], repository_name: str, workspace_name: str) -> dict[str, Any]:
    url = f"{DATAFORM_API_BASE}/{repository_name}/compilationResults"
    return _request("POST", url, headers, json={"workspace": workspace_name}).json()


def _invoke_compilation(headers: dict[str, str], repository_name: str, compilation_result_name: str) -> dict[str, Any]:
    url = f"{DATAFORM_API_BASE}/{repository_name}/workflowInvocations"
    return _request("POST", url, headers, json={"compilationResult": compilation_result_name}).json()


def _wait_for_invocation(headers: dict[str, str], invocation_name: str) -> dict[str, Any]:
    timeout_seconds = int(_env("GCP_DATAFORM_INVOCATION_TIMEOUT_SECONDS", "900") or "900")
    poll_seconds = max(int(_env("GCP_DATAFORM_INVOCATION_POLL_SECONDS", "5") or "5"), 1)
    deadline = time.time() + timeout_seconds
    url = f"{DATAFORM_API_BASE}/{invocation_name}"
    last = {}
    while time.time() < deadline:
        last = _request("GET", url, headers).json()
        state = str(last.get("state") or "").upper()
        if state in {"SUCCEEDED", "FAILED", "CANCELLED", "CANCELING"}:
            return last
        time.sleep(poll_seconds)
    last["state"] = last.get("state") or "TIMEOUT"
    last["timeoutSeconds"] = timeout_seconds
    return last


def publish_dataform_project_to_repository(project: dict[str, Any], run: bool = False) -> dict[str, Any]:
    project_id = _required_env("GCP_PROJECT_ID")
    target_dataset = _required_env("GCP_BIGQUERY_DATASET")
    source_dataset = _env("GCP_BIGQUERY_SOURCE_DATASET", target_dataset)
    bigquery_location = _env("GCP_BIGQUERY_LOCATION", "US")
    location = _required_env("GCP_DATAFORM_LOCATION")
    repository_id = _required_env("GCP_DATAFORM_REPOSITORY")
    workspace_id = _safe_workspace_id(
        _env("GCP_DATAFORM_WORKSPACE")
        or str(project.get("final_table_name") or project.get("project_name") or "alteryx-generated")
    )
    files = project.get("files") or {}
    if not files:
        raise ValueError("No Dataform project files were generated for this workflow.")
    files = _patched_files(files, project_id, target_dataset, source_dataset, bigquery_location)

    repository_name = f"projects/{project_id}/locations/{location}/repositories/{repository_id}"
    workspace_name = f"{repository_name}/workspaces/{workspace_id}"
    commit_message = (
        _env("GCP_DATAFORM_COMMIT_MESSAGE")
        or f"Add generated Dataform project for {project.get('final_table_name') or project.get('project_name')}"
    )

    with tempfile.TemporaryDirectory(prefix="alteryx_dataform_repo_"):
        headers = _auth_headers()
        workspace_name = _ensure_workspace(headers, repository_name, workspace_id)
        written_files = _write_project_files(headers, workspace_name, files)
        committed = _commit_workspace(headers, workspace_name, written_files, commit_message)
        compilation_result = _compile_workspace(headers, repository_name, workspace_name) if run else {}
        workflow_invocation = (
            _invoke_compilation(headers, repository_name, str(compilation_result.get("name") or ""))
            if run and compilation_result.get("name")
            else {}
        )
        workflow_invocation_result = (
            _wait_for_invocation(headers, str(workflow_invocation.get("name") or ""))
            if run and workflow_invocation.get("name")
            else {}
        )

    invocation_state = str(workflow_invocation_result.get("state") or workflow_invocation.get("state") or "").upper()
    invocation_success = not run or invocation_state == "SUCCEEDED"

    console_url = (
        "https://console.cloud.google.com/bigquery/dataform"
        f"/locations/{location}/repositories/{repository_id}/workspaces/{workspace_id}"
        f"?project={project_id}"
    )
    return {
        "success": invocation_success,
        "status": "published_to_dataform_repo" if not run else ("published_and_invoked" if invocation_success else "invocation_failed"),
        "project_id": project_id,
        "location": location,
        "repository": repository_id,
        "repository_name": repository_name,
        "workspace": workspace_id,
        "workspace_name": workspace_name,
        "workspace_url": console_url,
        "project_name": project.get("project_name"),
        "final_table_name": project.get("final_table_name"),
        "file_count": len(written_files),
        "written_files": written_files,
        "committed": committed,
        "compilation_result": compilation_result,
        "workflow_invocation": workflow_invocation_result or workflow_invocation,
        "message": (
            "Dataform project written to GCP Dataform workspace successfully."
            if not run
            else (
                "Dataform project published and workflow invocation completed successfully."
                if invocation_success
                else f"Dataform project was written, but workflow invocation did not succeed: {invocation_state or 'UNKNOWN'}"
            )
        ),
    }
