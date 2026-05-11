"""
Alteryx → Google Cloud Dataform publisher.

Converts a dbt-style project dict (produced by generate_dbt_project) into
Dataform SQLX definitions, then creates/updates a Dataform repository and
workspace, compiles, and triggers an invocation that writes final tables into
BigQuery – producing the same output as the dbt publish path.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from pathlib import PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


# ---------------------------------------------------------------------------
# Credentials / auth
# ---------------------------------------------------------------------------

def _build_credentials(env: dict[str, str]):
    """Return a google-auth Credentials object from env, or None for ADC."""
    service_account_json = env.get("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    cred_path = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()

    if service_account_json:
        try:
            from google.oauth2 import service_account
            info = json.loads(service_account_json)
            return service_account.Credentials.from_service_account_info(
                info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as exc:
            logger.warning("Could not build credentials from GCP_SERVICE_ACCOUNT_JSON: %s", exc)

    if cred_path and os.path.exists(cred_path):
        try:
            from google.oauth2 import service_account
            return service_account.Credentials.from_service_account_file(
                cred_path,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
        except Exception as exc:
            logger.warning("Could not build credentials from GOOGLE_APPLICATION_CREDENTIALS: %s", exc)

    return None  # fall back to ADC


def _set_adc_env(env: dict[str, str]) -> None:
    """Copy credential env vars into os.environ so google libraries pick them up."""
    for key in ("GOOGLE_APPLICATION_CREDENTIALS", "GCP_SERVICE_ACCOUNT_JSON"):
        if env.get(key):
            os.environ[key] = env[key]


# ---------------------------------------------------------------------------
# SQL conversion: dbt SQL → Dataform SQLX
# ---------------------------------------------------------------------------

_DBT_CONFIG_RE = re.compile(
    r"\{\{\s*config\s*\(.*?\)\s*\}\}\s*", re.DOTALL | re.IGNORECASE
)
_DBT_SOURCE_RE = re.compile(
    r"\{\{\s*source\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
    re.IGNORECASE,
)
_DBT_REF_RE = re.compile(
    r"\{\{\s*ref\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}",
    re.IGNORECASE,
)


def _dbt_config_to_dataform(sql: str, default_type: str = "table") -> dict[str, str]:
    """
    Extract dbt {{ config(...) }} block, infer materialized type,
    return cleaned SQL + config dict for SQLX header.
    """
    mat = default_type
    m = _DBT_CONFIG_RE.search(sql)
    if m:
        config_str = m.group(0)
        mat_match = re.search(r"materialized\s*=\s*['\"](\w+)['\"]", config_str)
        if mat_match:
            mat = mat_match.group(1)

    clean_sql = _DBT_CONFIG_RE.sub("", sql).strip()
    return {"materialized": mat, "sql": clean_sql}


def _sql_to_sqlx(
    model_name: str,
    sql: str,
    dataset: str,
    is_staging: bool = False,
    dependencies: list[str] | None = None,
) -> str:
    """
    Convert a dbt-style SQL model to a Dataform SQLX file.

    - {{ source('schema', 'table') }} → ${ref("table")} (Dataform declaration ref)
    - {{ ref('model') }}             → ${ref("model")}
    - {{ config(materialized=...) }} → config block in SQLX header
    """
    parsed = _dbt_config_to_dataform(sql, default_type="view" if is_staging else "table")
    mat = parsed["materialized"]
    body = parsed["sql"]

    # Replace dbt source() calls
    body = _DBT_SOURCE_RE.sub(lambda m: '${ref("' + m.group(2) + '")}', body)

    # Replace dbt ref() calls
    body = _DBT_REF_RE.sub(lambda m: '${ref("' + m.group(1) + '")}', body)

    dep_lines = ""
    if dependencies:
        dep_list = ", ".join(f'"{d}"' for d in dependencies)
        dep_lines = f"  dependencies: [{dep_list}],\n"

    # Dataform SQLX format
    sqlx = (
        f"config {{\n"
        f'  type: "{mat}",\n'
        f'  schema: "{dataset}",\n'
        f"{dep_lines}"
        f"}}\n\n"
        f"{body}\n"
    )
    return sqlx


# ---------------------------------------------------------------------------
# Convert dbt project dict → Dataform file tree
# ---------------------------------------------------------------------------

def _dbt_project_to_dataform_files(
    project: dict[str, Any],
    dataset: str,
    project_id: str,
) -> dict[str, str]:
    """
    Convert the generate_dbt_project() output into Dataform repository files.

    Returns a dict of {relative_path: content} suitable for the Dataform API
    (file paths inside the workspace).
    """
    files = project.get("files") or {}
    project_name = project.get("project_name") or "alteryx_dataform"
    dataform_files: dict[str, str] = {}

    # Parse schema.yml to discover source table names
    source_table_names: list[str] = []
    schema_yml = files.get("models/schema.yml") or ""
    if schema_yml:
        for m in re.finditer(r"^\s*-\s*name:\s*(\S+)", schema_yml, re.MULTILINE):
            name = m.group(1)
            # Filter out the final model name and staging model names
            if not name.startswith("stg_") and name != project_name:
                source_table_names.append(name)

    # --- workflow_settings.yaml (replaces dataform.json in newer SDK) ---
    dataform_files["workflow_settings.yaml"] = (
        f"defaultProject: {project_id}\n"
        f"defaultDataset: {dataset}\n"
        "defaultLocation: US\n"
        f"dataformCoreVersion: 3.0.0\n"
    )

    # --- Source declarations (.sqlx files for source tables) ---
    for table_name in source_table_names:
        sqlx_content = (
            "config {\n"
            '  type: "declaration",\n'
            f'  schema: "{dataset}",\n'
            f'  name: "{table_name}",\n'
            f'  description: "Source table landed in BigQuery from Alteryx workflow"\n'
            "}\n"
        )
        dataform_files[f"definitions/sources/{table_name}.sqlx"] = sqlx_content

    # --- Staging models ---
    staging_deps = source_table_names[:]
    for file_path, content in files.items():
        if not file_path.startswith("models/staging/"):
            continue
        # e.g. models/staging/stg_large_fact_100k.sqlx
        stem = PurePosixPath(file_path).stem  # stg_<name>
        sqlx = _sql_to_sqlx(stem, content, dataset, is_staging=True)
        dataform_files[f"definitions/staging/{stem}.sqlx"] = sqlx

    # --- Final model ---
    final_sql_key = f"models/{project_name}.sql"
    if final_sql_key in files:
        deps = [f"stg_{t}" for t in source_table_names] if source_table_names else []
        sqlx = _sql_to_sqlx(
            project_name,
            files[final_sql_key],
            dataset,
            is_staging=False,
            dependencies=deps if deps else None,
        )
        dataform_files[f"definitions/{project_name}.sqlx"] = sqlx

    # --- package.json required by Dataform ---
    dataform_files["package.json"] = json.dumps(
        {
            "name": project_name,
            "dependencies": {"@dataform/core": "3.0.0"},
        },
        indent=2,
    )

    return dataform_files


# ---------------------------------------------------------------------------
# Dataform REST API helpers (uses google-api-python-client)
# ---------------------------------------------------------------------------

def _dataform_service(credentials=None):
    """Build the Dataform v1beta1 API client."""
    from googleapiclient.discovery import build

    kwargs: dict[str, Any] = {"cache_discovery": False}
    if credentials:
        kwargs["credentials"] = credentials
    return build("dataform", "v1beta1", **kwargs)


def _location_path(project_id: str, location: str) -> str:
    return f"projects/{project_id}/locations/{location}"


def _repo_path(project_id: str, location: str, repo_id: str) -> str:
    return f"{_location_path(project_id, location)}/repositories/{repo_id}"


def _workspace_path(project_id: str, location: str, repo_id: str, workspace_id: str) -> str:
    return f"{_repo_path(project_id, location, repo_id)}/workspaces/{workspace_id}"


def _get_or_create_repository(
    svc, project_id: str, location: str, repo_id: str, dataset: str
) -> dict[str, Any]:
    """Ensure the Dataform repository exists, create if missing."""
    parent = _location_path(project_id, location)
    repo_name = _repo_path(project_id, location, repo_id)

    try:
        return svc.projects().locations().repositories().get(name=repo_name).execute()
    except Exception:
        pass  # doesn't exist yet

    body = {
        "displayName": repo_id,
        "gitRemoteSettings": None,
        "workspaceCompilationOverrides": {
            "defaultDatabase": project_id,
            "schemaSuffix": "",
            "tablePrefix": "",
        },
    }
    return (
        svc.projects()
        .locations()
        .repositories()
        .create(parent=parent, repositoryId=repo_id, body=body)
        .execute()
    )


def _get_or_create_workspace(
    svc, project_id: str, location: str, repo_id: str, workspace_id: str
) -> dict[str, Any]:
    """Ensure the Dataform workspace exists."""
    ws_name = _workspace_path(project_id, location, repo_id, workspace_id)
    repo_name = _repo_path(project_id, location, repo_id)

    try:
        return svc.projects().locations().repositories().workspaces().get(name=ws_name).execute()
    except Exception:
        pass

    body = {"displayName": workspace_id}
    return (
        svc.projects()
        .locations()
        .repositories()
        .workspaces()
        .create(parent=repo_name, workspaceId=workspace_id, body=body)
        .execute()
    )


def _write_workspace_files(
    svc,
    project_id: str,
    location: str,
    repo_id: str,
    workspace_id: str,
    files: dict[str, str],
) -> list[dict[str, Any]]:
    """Write all SQLX + config files into the Dataform workspace."""
    ws_name = _workspace_path(project_id, location, repo_id, workspace_id)
    results = []
    for rel_path, content in files.items():
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        body = {"path": rel_path, "contents": encoded}
        try:
            result = (
                svc.projects()
                .locations()
                .repositories()
                .workspaces()
                .writeFile(workspace=ws_name, body=body)
                .execute()
            )
            results.append({"path": rel_path, "status": "written", "result": result})
        except Exception as exc:
            logger.warning("Failed to write %s: %s", rel_path, exc)
            results.append({"path": rel_path, "status": "error", "error": str(exc)})
    return results


def _commit_workspace(
    svc,
    project_id: str,
    location: str,
    repo_id: str,
    workspace_id: str,
    message: str = "Alteryx migration scaffold",
) -> dict[str, Any]:
    """Commit all staged changes in the workspace."""
    ws_name = _workspace_path(project_id, location, repo_id, workspace_id)
    body = {
        "author": {"name": "AlteryxAI", "emailAddress": "alteryxai@sorim.ai"},
        "commitMessage": message,
    }
    try:
        return (
            svc.projects()
            .locations()
            .repositories()
            .workspaces()
            .commit(name=ws_name, body=body)
            .execute()
        )
    except Exception as exc:
        logger.warning("Commit step skipped (not required for compilation): %s", exc)
        return {"skipped": True, "reason": str(exc)}


def _create_compilation_result(
    svc,
    project_id: str,
    location: str,
    repo_id: str,
    workspace_id: str,
    dataset: str,
) -> dict[str, Any]:
    """Compile the workspace and return the compilation result resource."""
    repo_name = _repo_path(project_id, location, repo_id)
    ws_name = _workspace_path(project_id, location, repo_id, workspace_id)
    body = {
        "workspace": ws_name,
        "codeCompilationConfig": {
            "defaultDatabase": project_id,
            "defaultSchema": dataset,
            "defaultLocation": location,
        },
    }
    return (
        svc.projects()
        .locations()
        .repositories()
        .compilationResults()
        .create(parent=repo_name, body=body)
        .execute()
    )


def _create_workflow_invocation(
    svc,
    project_id: str,
    location: str,
    repo_id: str,
    compilation_result_name: str,
) -> dict[str, Any]:
    """Trigger a workflow invocation (runs the compiled SQL in BigQuery)."""
    repo_name = _repo_path(project_id, location, repo_id)
    body = {
        "compilationResult": compilation_result_name,
        "invocationConfig": {
            "fullyRefreshIncrementalTablesEnabled": False,
            "transitiveDependenciesIncluded": True,
            "transitiveDependentsIncluded": False,
        },
    }
    return (
        svc.projects()
        .locations()
        .repositories()
        .workflowInvocations()
        .create(parent=repo_name, body=body)
        .execute()
    )


def _poll_invocation(
    svc,
    invocation_name: str,
    timeout_seconds: int = 600,
    poll_interval: int = 8,
) -> dict[str, Any]:
    """Poll until the invocation completes or times out."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = (
            svc.projects()
            .locations()
            .repositories()
            .workflowInvocations()
            .get(name=invocation_name)
            .execute()
        )
        state = result.get("state", "")
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return result
        time.sleep(poll_interval)
    return {"state": "TIMED_OUT", "name": invocation_name}


def _fetch_invocation_actions(
    svc, invocation_name: str
) -> list[dict[str, Any]]:
    """List the individual SQL actions run during the invocation."""
    try:
        resp = (
            svc.projects()
            .locations()
            .repositories()
            .workflowInvocations()
            .queryWorkflowInvocationActions(name=invocation_name)
            .execute()
        )
        return resp.get("workflowInvocationActions") or []
    except Exception as exc:
        logger.warning("Could not fetch invocation actions: %s", exc)
        return []


# ---------------------------------------------------------------------------
# BigQuery metadata (reuse same pattern as dbt publisher)
# ---------------------------------------------------------------------------

def _fetch_bigquery_table_metadata(
    project_id: str, dataset: str, table: str, location: str, env: dict[str, str]
) -> dict[str, Any]:
    try:
        from google.cloud import bigquery as bq
    except ImportError:
        return {"success": False, "message": "google-cloud-bigquery not installed"}

    _set_adc_env(env)
    try:
        client = bq.Client(project=project_id)
        table_ref = client.get_table(f"{project_id}.{dataset}.{table}")
        return {
            "success": True,
            "total_rows": table_ref.num_rows,
            "row_count": table_ref.num_rows,
            "total_columns": len(table_ref.schema),
            "column_count": len(table_ref.schema),
            "available_columns": [f.name for f in table_ref.schema],
        }
    except Exception as exc:
        return {"success": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# Main publish entry point
# ---------------------------------------------------------------------------

def publish_dataform_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
    """
    Convert the dbt project dict into Dataform SQLX, publish to GCP Dataform,
    run the invocation, and return a result dict compatible with the DBT publish
    response shape so the frontend can display it uniformly.
    """
    project_id = _required_env("GCP_PROJECT_ID")
    dataset = _required_env("GCP_BIGQUERY_DATASET")
    location = _env("GCP_BIGQUERY_LOCATION", "US")
    timeout_seconds = int(_env("DBT_COMMAND_TIMEOUT_SECONDS", "600") or "600")

    project_name = str(project.get("project_name") or "alteryx_dataform")
    macro_complexity = project.get("macro_complexity") or {}
    tool_count = int(project.get("tool_count") or 0)
    connection_count = int(project.get("connection_count") or 0)
    files = project.get("files") or {}

    if not files:
        raise ValueError("No project files were generated for this workflow.")

    run_env = os.environ.copy()

    # Write service account JSON to disk if provided
    sa_json = _env("GCP_SERVICE_ACCOUNT_JSON")
    if sa_json:
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
        try:
            tmp.write_text(sa_json)
            run_env["GOOGLE_APPLICATION_CREDENTIALS"] = str(tmp)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(tmp)
        except Exception:
            pass

    credentials = _build_credentials(run_env)
    _set_adc_env(run_env)

    repo_id = re.sub(r"[^a-z0-9\-]", "-", project_name.lower())
    workspace_id = "main"

    steps: list[dict[str, Any]] = []

    try:
        svc = _dataform_service(credentials)

        # 1. Ensure repository exists
        repo = _get_or_create_repository(svc, project_id, location, repo_id, dataset)
        steps.append({"step": "create_repository", "status": "ok", "name": repo.get("name")})

        # 2. Ensure workspace exists
        ws = _get_or_create_workspace(svc, project_id, location, repo_id, workspace_id)
        steps.append({"step": "create_workspace", "status": "ok", "name": ws.get("name")})

        # 3. Convert dbt → Dataform SQLX files
        dataform_files = _dbt_project_to_dataform_files(project, dataset, project_id)
        steps.append({"step": "convert_to_sqlx", "status": "ok", "file_count": len(dataform_files)})

        # 4. Write files to workspace
        write_results = _write_workspace_files(
            svc, project_id, location, repo_id, workspace_id, dataform_files
        )
        write_errors = [r for r in write_results if r["status"] == "error"]
        steps.append({
            "step": "write_workspace_files",
            "status": "ok" if not write_errors else "partial",
            "written": len(write_results) - len(write_errors),
            "errors": len(write_errors),
        })

        # 5. Commit workspace
        commit_result = _commit_workspace(
            svc, project_id, location, repo_id, workspace_id,
            message=f"AlteryxAI: publish {project_name}"
        )
        steps.append({"step": "commit_workspace", "status": "ok", "commit": commit_result})

        # 6. Compile
        compilation = _create_compilation_result(
            svc, project_id, location, repo_id, workspace_id, dataset
        )
        compilation_name = compilation.get("name", "")
        compile_errors = compilation.get("compilationErrors") or []
        steps.append({
            "step": "compile",
            "status": "ok" if not compile_errors else "warnings",
            "name": compilation_name,
            "errors": compile_errors,
        })

        if compile_errors:
            error_msgs = [e.get("message", str(e)) for e in compile_errors[:3]]
            return _failure_response(
                project_id, dataset, location, project_name,
                macro_complexity, tool_count, connection_count, steps,
                message=f"Dataform compilation failed: {'; '.join(error_msgs)}",
            )

        # 7. Invoke
        invocation = _create_workflow_invocation(svc, project_id, location, repo_id, compilation_name)
        invocation_name = invocation.get("name", "")
        steps.append({"step": "invoke", "status": "running", "name": invocation_name})

        # 8. Poll
        final_state = _poll_invocation(svc, invocation_name, timeout_seconds=timeout_seconds)
        state = final_state.get("state", "UNKNOWN")

        actions = _fetch_invocation_actions(svc, invocation_name)
        succeeded_actions = [a for a in actions if a.get("state") == "SUCCEEDED"]
        failed_actions = [a for a in actions if a.get("state") == "FAILED"]

        steps.append({
            "step": "poll_invocation",
            "status": state,
            "succeeded_actions": len(succeeded_actions),
            "failed_actions": len(failed_actions),
        })

        if state != "SUCCEEDED":
            failed_msgs = [
                a.get("failureReason", "") for a in failed_actions if a.get("failureReason")
            ]
            return _failure_response(
                project_id, dataset, location, project_name,
                macro_complexity, tool_count, connection_count, steps,
                message=f"Dataform invocation {state}. "
                        + (f"Failures: {'; '.join(failed_msgs[:3])}" if failed_msgs else ""),
            )

        # 9. Fetch BigQuery metadata
        bigquery_metadata = _fetch_bigquery_table_metadata(
            project_id, dataset, project_name, location, run_env
        )

        final_model = f"{project_id}.{dataset}.{project_name}"
        dataform_console_url = (
            f"https://console.cloud.google.com/bigquery/dataform"
            f"?project={project_id}"
        )

        return {
            "success": True,
            "status": "published",
            "publish_method": "DATAFORM",
            "project_id": project_id,
            "target_dataset": dataset,
            "source_dataset": dataset,
            "location": location,
            "project_name": project_name,
            "repository_id": repo_id,
            "workspace_id": workspace_id,
            "compilation_result": compilation_name,
            "invocation_name": invocation_name,
            "invocation_state": state,
            "dataform_console_url": dataform_console_url,
            "final_model": final_model,
            "macro_complexity": macro_complexity,
            "tool_count": tool_count,
            "connection_count": connection_count,
            "steps": steps,
            "succeeded_actions": len(succeeded_actions),
            "failed_actions": len(failed_actions),
            "bigquery_metadata": bigquery_metadata,
            "row_count": bigquery_metadata.get("row_count"),
            "total_rows": bigquery_metadata.get("total_rows"),
            "column_count": bigquery_metadata.get("column_count"),
            "total_columns": bigquery_metadata.get("total_columns"),
            "available_columns": bigquery_metadata.get("available_columns") or [],
            "dataform_files": list(dataform_files.keys()),
            "message": "Dataform project published to BigQuery successfully.",
        }

    except Exception as exc:
        logger.exception("Dataform publish failed")
        return _failure_response(
            project_id, dataset, location, project_name,
            macro_complexity, tool_count, connection_count, steps,
            message=f"Dataform publish failed: {exc}",
        )


def _failure_response(
    project_id: str,
    dataset: str,
    location: str,
    project_name: str,
    macro_complexity: dict,
    tool_count: int,
    connection_count: int,
    steps: list,
    message: str,
) -> dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "publish_method": "DATAFORM",
        "project_id": project_id,
        "target_dataset": dataset,
        "source_dataset": dataset,
        "location": location,
        "project_name": project_name,
        "macro_complexity": macro_complexity,
        "tool_count": tool_count,
        "connection_count": connection_count,
        "steps": steps,
        "message": message,
    }
