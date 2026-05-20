import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import json
from pathlib import Path
from typing import Any

from app.services.alteryx_dbt_publisher import fetch_bigquery_table_metadata, publish_dbt_project_to_bigquery
from app.services.alteryx_dataform_repo_publisher import publish_dataform_project_to_repository
from app.services.gcp_credentials import write_service_account_file_from_env


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _required_env(name: str) -> str:
    value = _env(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _write_project_files(project_dir: Path, files: dict[str, Any]) -> None:
    for relative_path, content in files.items():
        target = project_dir / str(relative_path).replace("\\", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _patch_workflow_settings(project_dir: Path, project_id: str, target_dataset: str, location: str) -> None:
    settings_path = project_dir / "workflow_settings.yaml"
    settings_path.write_text(
        f"defaultProject: {project_id}\n"
        f"defaultDataset: {target_dataset}\n"
        f"defaultLocation: {location}\n"
        "dataformCoreVersion: 3.0.0\n",
        encoding="utf-8",
    )


def _patch_declarations(project_dir: Path, project_id: str, source_dataset: str) -> None:
    declarations_path = project_dir / "definitions" / "declarations.js"
    if not declarations_path.exists():
        return

    text = declarations_path.read_text(encoding="utf-8")
    text = re.sub(r'database:\s*"[^"]*"', f'database: "{project_id}"', text)
    text = re.sub(r'schema:\s*"[^"]*"', f'schema: "{source_dataset}"', text)
    declarations_path.write_text(text, encoding="utf-8")


def _write_dataform_credentials(project_dir: Path, project_id: str, location: str) -> None:
    credentials_path = project_dir / ".df-credentials.json"
    credentials_path.write_text(
        json.dumps({"projectId": project_id, "location": location}, indent=2),
        encoding="utf-8",
    )


def _normalize_dataform_project_for_cli(project_dir: Path) -> None:
    # Dataform CLI 3.x rejects package.json/package-lock.json when
    # workflow_settings.yaml is present because packages are resolved at runtime.
    if not (project_dir / "workflow_settings.yaml").exists():
        return
    for filename in ("package.json", "package-lock.json"):
        path = project_dir / filename
        if path.exists():
            path.unlink()


def _write_service_account_json(work_dir: Path, env: dict[str, str]) -> None:
    write_service_account_file_from_env(work_dir, env)


def _format_duration(value: str, default_seconds: int) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        return f"{default_seconds}s"
    if cleaned.isdigit():
        return f"{int(cleaned)}s"
    return cleaned


def _resolve_dataform_command(dataform_executable: str) -> list[str] | None:
    command_parts = shlex.split(dataform_executable)
    if not command_parts:
        return None

    resolved = shutil.which(command_parts[0])
    if resolved:
        if os.name == "nt" and resolved.lower().endswith(".cmd"):
            return ["cmd", "/c", *command_parts]
        return command_parts

    if len(command_parts) == 1:
        executable_name = command_parts[0].strip().lower()
        if executable_name in {"dataform", "dataform.exe", "@dataform/cli"}:
            for fallback in (
                ["npx", "--yes", "@dataform/cli"],
                ["npx", "--yes", "dataform"],
                ["npm", "exec", "--yes", "@dataform/cli"],
                ["npm", "exec", "--yes", "dataform"],
            ):
                if shutil.which(fallback[0]):
                    if os.name == "nt":
                        return ["cmd", "/c", *fallback]
                    return fallback
        if shutil.which("npx"):
            if os.name == "nt":
                return ["cmd", "/c", "npx", "--yes", command_parts[0]]
            return ["npx", "--yes", command_parts[0]]
        if shutil.which("npm"):
            if os.name == "nt":
                return ["cmd", "/c", "npm", "exec", "--yes", command_parts[0]]
            return ["npm", "exec", "--yes", command_parts[0]]

    return None


def _run_dataform_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    return {
        "command": " ".join(command),
        "return_code": completed.returncode,
        "duration_seconds": round(time.time() - started, 2),
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
        "success": completed.returncode == 0,
    }


def _extract_missing_bigquery_tables(result: dict[str, Any]) -> list[str]:
    output = "\n".join([str(result.get("stdout") or ""), str(result.get("stderr") or "")])
    missing = re.findall(r"Not found: Table\s+([^\s]+)\s+was not found", output)
    return sorted(set(missing))


def _env_flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def _publish_dbt_fallback(project: dict[str, Any], reason: str) -> dict[str, Any] | None:
    if not _env_flag("DATAFORM_BIGQUERY_FALLBACK_TO_DBT", True):
        return None
    dbt_project = project.get("dbt_project")
    if not isinstance(dbt_project, dict) or not dbt_project.get("files"):
        return None
    result = publish_dbt_project_to_bigquery(dbt_project)
    result.update({
        "target": "dataform",
        "status": "published_via_dbt_fallback" if result.get("success", True) else "failed_via_dbt_fallback",
        "used_dbt_fallback": True,
        "fallback_reason": reason,
        "message": (
            "Dataform native publish was unavailable, so the equivalent dbt project was published to BigQuery successfully."
            if result.get("success", True)
            else result.get("message", "Dataform native publish failed and dbt fallback also failed.")
        ),
    })
    return result


def _format_dataform_failure(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    details = []
    if stderr:
        details.append(f"stderr:\n{stderr[-4000:]}")
    if stdout:
        details.append(f"stdout:\n{stdout[-4000:]}")
    suffix = "\n\n" + "\n\n".join(details) if details else ""
    return f"Publish to BigQuery failed while running: {result['command']}{suffix}"


def _safe_table_name(value: str, fallback: str) -> str:
    raw = str(value or fallback).split("\\")[-1].split("/")[-1]
    raw = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet|yxdb|yxmd|yxmc)$", "", raw, flags=re.IGNORECASE)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return safe or fallback


def _output_table_names(final_table_name: str, output_targets: list[dict[str, Any]]) -> list[str]:
    if not output_targets:
        return [final_table_name]
    names: list[str] = []
    for index, output in enumerate(output_targets, start=1):
        if not isinstance(output, dict):
            continue
        names.append(_safe_table_name(str(output.get("name") or output.get("path") or f"output_{index}"), f"output_{index}"))
    return names or [final_table_name]


def _aggregate_published_table_metadata(items: list[dict[str, Any]]) -> dict[str, Any]:
    row_counts = [int(item.get("row_count")) for item in items if item.get("row_count") is not None]
    column_counts = [int(item.get("column_count")) for item in items if item.get("column_count") is not None]
    columns: dict[str, Any] = {}
    numeric_columns: list[str] = []
    available_columns: list[str] = []
    for item in items:
        table = str(item.get("table") or "").strip() or "target"
        metadata = item.get("metadata") or {}
        profile = metadata.get("profile") or {}
        available_columns.extend(f"{table}.{column}" for column in metadata.get("available_columns") or [])
        for column_name, column_profile in (profile.get("columns") or {}).items():
            aggregate_name = f"{table}.{column_name}"
            columns[aggregate_name] = {**(column_profile or {}), "name": aggregate_name}
            if (column_profile or {}).get("numeric_count", 0) > 0:
                numeric_columns.append(aggregate_name)
    return {
        "row_count": sum(row_counts) if row_counts else None,
        "total_rows": sum(row_counts) if row_counts else None,
        "record_count": sum(row_counts) if row_counts else None,
        "total_records": sum(row_counts) if row_counts else None,
        "column_count": sum(column_counts) if column_counts else None,
        "total_columns": sum(column_counts) if column_counts else None,
        "available_columns": available_columns,
        "profile": {
            "name": "BigQuery output tables",
            "row_count": sum(row_counts) if row_counts else None,
            "column_count": sum(column_counts) if column_counts else None,
            "columns": columns,
            "numeric_columns": numeric_columns,
        },
        "numeric_columns": numeric_columns,
    }


def publish_dataform_project_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
    project_id = _required_env("GCP_PROJECT_ID")
    target_dataset = _required_env("GCP_BIGQUERY_DATASET")
    source_dataset = _env("GCP_BIGQUERY_SOURCE_DATASET", target_dataset)
    location = _env("GCP_BIGQUERY_LOCATION", "US")
    dataform_executable = _env("DATAFORM_EXECUTABLE", "dataform")
    timeout_seconds = int(_env("DATAFORM_COMMAND_TIMEOUT_SECONDS", "600") or "600")
    dataform_cli_timeout = _format_duration(_env("DATAFORM_CLI_TIMEOUT_SECONDS", "1200"), 1200)
    project_name = str(project.get("project_name") or "alteryx_dataform_project")
    final_table_name = str(project.get("final_table_name") or project_name.removesuffix("_dataform"))
    final_model = f"{project_id}.{target_dataset}.{final_table_name}"
    files = project.get("files") or {}
    output_targets = project.get("output_targets") or []

    if not files:
        raise ValueError("No Dataform project files were generated for this workflow.")
    resolved_dataform_command = _resolve_dataform_command(dataform_executable)
    if not resolved_dataform_command:
        if _env("GCP_DATAFORM_REPOSITORY") and _env("GCP_DATAFORM_LOCATION"):
            try:
                result = publish_dataform_project_to_repository(project, run=True)
                result.update({
                    "target": "dataform",
                    "project_id": project_id,
                    "target_dataset": target_dataset,
                    "source_dataset": source_dataset,
                    "bigquery_location": location,
                    "project_name": project_name,
                    "final_table_name": final_table_name,
                    "final_model": final_model,
                    "used_dataform_api_fallback": True,
                })
                return result
            except Exception as exc:
                fallback = _publish_dbt_fallback(project, f"Dataform API fallback failed: {exc}")
                if fallback is not None:
                    return fallback
                raise
        fallback = _publish_dbt_fallback(project, f"Dataform executable '{dataform_executable}' was not found.")
        if fallback is not None:
            return fallback
        raise RuntimeError(
            f"Dataform executable '{dataform_executable}' was not found and no GCP_DATAFORM_REPOSITORY and GCP_DATAFORM_LOCATION are configured. "
            "Set DATAFORM_EXECUTABLE to a valid command, install @dataform/cli, or configure GCP_DATAFORM_LOCATION and GCP_DATAFORM_REPOSITORY."
        )

    with tempfile.TemporaryDirectory(prefix="alteryx_dataform_publish_") as temp_root:
        temp_path = Path(temp_root)
        project_dir = temp_path / project_name
        project_dir.mkdir(parents=True, exist_ok=True)

        run_env = os.environ.copy()
        _write_service_account_json(temp_path, run_env)
        _write_project_files(project_dir, files)
        _patch_workflow_settings(project_dir, project_id, target_dataset, location)
        _patch_declarations(project_dir, project_id, source_dataset)
        _normalize_dataform_project_for_cli(project_dir)
        _write_dataform_credentials(project_dir, project_id, location)

        commands = []
        if _env_flag("DATAFORM_RUN_COMPILE", True):
            commands.append([*resolved_dataform_command, "compile", str(project_dir), "--timeout", dataform_cli_timeout])
        commands.append([*resolved_dataform_command, "run", str(project_dir), "--timeout", dataform_cli_timeout])
        command_results: list[dict[str, Any]] = []
        for command in commands:
            result = _run_dataform_command(command, project_dir, run_env, timeout_seconds)
            command_results.append(result)
            if not result["success"]:
                missing_tables = _extract_missing_bigquery_tables(result)
                message = _format_dataform_failure(result)
                if missing_tables:
                    message = (
                        "Dataform connected successfully, but required source table(s) "
                        f"were not found in BigQuery: {', '.join(missing_tables)}"
                    )
                return {
                    "success": False,
                    "status": "failed",
                    "project_id": project_id,
                    "target_dataset": target_dataset,
                    "source_dataset": source_dataset,
                    "location": location,
                    "project_name": project_name,
                    "final_table_name": final_table_name,
                    "final_model": final_model,
                    "commands": command_results,
                    "missing_source_tables": missing_tables,
                    "output_targets": output_targets,
                    "output_count": len(output_targets),
                    "message": message,
                }

        published_tables = []
        for table_name in _output_table_names(final_table_name, output_targets):
            metadata = fetch_bigquery_table_metadata(project_id, target_dataset, table_name, location, run_env)
            published_tables.append({
                "table": table_name,
                "name": table_name,
                "final_model": f"{project_id}.{target_dataset}.{table_name}",
                "metadata": metadata,
                "row_count": metadata.get("row_count"),
                "record_count": metadata.get("record_count"),
                "total_records": metadata.get("total_records"),
                "column_count": metadata.get("column_count"),
                "total_columns": metadata.get("total_columns"),
                "available_columns": metadata.get("available_columns") or [],
                "profile": metadata.get("profile") or {},
            })
        bigquery_metadata = _aggregate_published_table_metadata(published_tables) if len(published_tables) > 1 else (published_tables[0].get("metadata") if published_tables else {})

    return {
        "success": True,
        "status": "published",
        "project_id": project_id,
        "target_dataset": target_dataset,
        "source_dataset": source_dataset,
        "location": location,
        "project_name": project_name,
        "final_table_name": final_table_name,
        "final_model": final_model,
        "commands": command_results,
        "bigquery_metadata": bigquery_metadata,
        "target_profile": bigquery_metadata.get("profile") or {},
        "published_tables": published_tables,
        "tables_deployed": len(published_tables),
        "row_count": bigquery_metadata.get("row_count"),
        "total_rows": bigquery_metadata.get("total_rows"),
        "record_count": bigquery_metadata.get("record_count"),
        "total_records": bigquery_metadata.get("total_records"),
        "column_count": bigquery_metadata.get("column_count"),
        "total_columns": bigquery_metadata.get("total_columns"),
        "available_columns": bigquery_metadata.get("available_columns") or [],
        "output_targets": output_targets,
        "output_count": len(output_targets),
        "message": "Dataform project published to BigQuery successfully.",
    }
