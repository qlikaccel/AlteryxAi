import os
import re
import shutil
import subprocess
import tempfile
import time
import json
from pathlib import Path
from typing import Any

from app.services.alteryx_dbt_publisher import fetch_bigquery_table_metadata


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
    service_account_json = _env("GCP_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        credentials_path = work_dir / "gcp_service_account.json"
        credentials_path.write_text(service_account_json, encoding="utf-8")
        env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)


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


def publish_dataform_project_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
    project_id = _required_env("GCP_PROJECT_ID")
    target_dataset = _required_env("GCP_BIGQUERY_DATASET")
    source_dataset = _env("GCP_BIGQUERY_SOURCE_DATASET", target_dataset)
    location = _env("GCP_BIGQUERY_LOCATION", "US")
    dataform_executable = _env("DATAFORM_EXECUTABLE", "dataform")
    timeout_seconds = int(_env("DATAFORM_COMMAND_TIMEOUT_SECONDS", "600") or "600")
    project_name = str(project.get("project_name") or "alteryx_dataform_project")
    final_table_name = str(project.get("final_table_name") or project_name.removesuffix("_dataform"))
    final_model = f"{project_id}.{target_dataset}.{final_table_name}"
    files = project.get("files") or {}

    if not files:
        raise ValueError("No Dataform project files were generated for this workflow.")
    resolved_dataform_executable = shutil.which(dataform_executable)
    if not resolved_dataform_executable:
        raise RuntimeError(
            f"Dataform executable '{dataform_executable}' was not found. "
            "Install @dataform/cli and set DATAFORM_EXECUTABLE=dataform."
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
            commands.append([resolved_dataform_executable, "compile", str(project_dir)])
        commands.append([resolved_dataform_executable, "run", str(project_dir)])
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
                    "output_targets": project.get("output_targets", []),
                    "output_count": project.get("output_count", 0),
                    "message": message,
                }

        bigquery_metadata = fetch_bigquery_table_metadata(
            project_id,
            target_dataset,
            final_table_name,
            location,
            run_env,
        )

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
        "row_count": bigquery_metadata.get("row_count"),
        "total_rows": bigquery_metadata.get("total_rows"),
        "record_count": bigquery_metadata.get("record_count"),
        "total_records": bigquery_metadata.get("total_records"),
        "column_count": bigquery_metadata.get("column_count"),
        "total_columns": bigquery_metadata.get("total_columns"),
        "available_columns": bigquery_metadata.get("available_columns") or [],
        "output_targets": project.get("output_targets", []),
        "output_count": project.get("output_count", 0),
        "message": "Dataform project published to BigQuery successfully.",
    }
