import os
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


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


def _patch_schema_source_dataset(project_dir: Path, source_dataset: str) -> None:
    schema_path = project_dir / "models" / "schema.yml"
    if not schema_path.exists():
        return

    lines = schema_path.read_text(encoding="utf-8").splitlines()
    patched: list[str] = []
    inserted = False
    for index, line in enumerate(lines):
        patched.append(line)
        if not inserted and line.strip() == "- name: alteryx_raw":
            next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if not next_line.startswith("schema:"):
                patched.append(f"    schema: {source_dataset}")
            inserted = True
    schema_path.write_text("\n".join(patched) + "\n", encoding="utf-8")


def _write_profiles(
    profiles_dir: Path,
    profile_name: str,
    project_id: str,
    dataset: str,
    location: str,
    auth_method: str,
    threads: int,
    keyfile: str = "",
) -> None:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    keyfile_yaml = keyfile.replace("\\", "/")
    keyfile_line = f"      keyfile: \"{keyfile_yaml}\"\n" if auth_method == "service-account" and keyfile else ""
    profile = (
        f"{profile_name}:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: bigquery\n"
        f"      method: {auth_method}\n"
        f"      project: {project_id}\n"
        f"      dataset: {dataset}\n"
        f"      location: {location}\n"
        f"{keyfile_line}"
        f"      threads: {threads}\n"
        "      priority: interactive\n"
        "      job_execution_timeout_seconds: 300\n"
        "      job_retries: 1\n"
    )
    profiles_path = profiles_dir / "profiles.yml"
    profiles_path.write_text(profile, encoding="utf-8")


def _write_service_account_json(work_dir: Path, env: dict[str, str]) -> None:
    service_account_json = _env("GCP_SERVICE_ACCOUNT_JSON")
    if not service_account_json:
        return

    credentials_path = work_dir / "gcp_service_account.json"
    try:
        parsed = json.loads(service_account_json)
        credentials_path.write_text(json.dumps(parsed), encoding="utf-8")
    except json.JSONDecodeError:
        credentials_path.write_text(service_account_json, encoding="utf-8")
    env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)


def _validate_service_account_config(auth_method: str, env: dict[str, str]) -> str:
    if auth_method != "service-account":
        return ""

    keyfile = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not keyfile:
        raise RuntimeError(
            "GCP_DBT_AUTH_METHOD is set to service-account, but no service account key was configured. "
            "Set GOOGLE_APPLICATION_CREDENTIALS to a valid JSON key file path or set GCP_SERVICE_ACCOUNT_JSON."
        )

    keyfile_path = Path(keyfile)
    if not keyfile_path.exists():
        raise RuntimeError(f"Configured GOOGLE_APPLICATION_CREDENTIALS file was not found: {keyfile}")

    try:
        data = json.loads(keyfile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Configured service account key file is not valid JSON: {keyfile}") from exc

    if data.get("type") != "service_account" or not data.get("client_email") or not data.get("private_key"):
        raise RuntimeError(
            "Configured service account key JSON is missing required fields: type, client_email, or private_key."
        )
    return str(keyfile_path)


def _create_bigquery_dataset(project_id: str, dataset: str, location: str, env: dict[str, str]) -> dict[str, Any]:
    try:
        from google.cloud import bigquery
    except Exception as exc:
        return {
            "dataset": f"{project_id}.{dataset}",
            "created": False,
            "location": location,
            "status": "skipped",
            "message": (
                "google-cloud-bigquery is not installed in the backend runtime. "
                "Skipping explicit dataset creation; dbt will create the BigQuery schema if permissions allow."
            ),
            "error": str(exc),
        }

    credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

    client = bigquery.Client(project=project_id)
    dataset_ref = bigquery.Dataset(f"{project_id}.{dataset}")
    dataset_ref.location = location
    created = False
    try:
        client.get_dataset(dataset_ref)
    except Exception:
        client.create_dataset(dataset_ref, exists_ok=True)
        created = True
    return {"dataset": f"{project_id}.{dataset}", "created": created, "location": location}


def _run_dbt_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
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


def publish_dbt_project_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
    project_id = _required_env("GCP_PROJECT_ID")
    target_dataset = _required_env("GCP_BIGQUERY_DATASET")
    source_dataset = _env("GCP_BIGQUERY_SOURCE_DATASET", target_dataset)
    location = _env("GCP_BIGQUERY_LOCATION", "US")
    auth_method = _env("GCP_DBT_AUTH_METHOD", "oauth")
    dbt_executable = _env("DBT_EXECUTABLE", "dbt")
    threads = int(_env("DBT_THREADS", "4") or "4")
    timeout_seconds = int(_env("DBT_COMMAND_TIMEOUT_SECONDS", "600") or "600")
    project_name = str(project.get("project_name") or "alteryx_dbt_project")
    files = project.get("files") or {}
    macro_complexity = project.get("macro_complexity") or {}
    tool_count = int(project.get("tool_count") or 0)
    connection_count = int(project.get("connection_count") or 0)

    if not files:
        raise ValueError("No dbt project files were generated for this workflow.")
    if not shutil.which(dbt_executable):
        raise RuntimeError(
            f"dbt executable '{dbt_executable}' was not found. Set DBT_EXECUTABLE or install dbt-bigquery."
        )

    with tempfile.TemporaryDirectory(prefix="alteryx_dbt_publish_") as temp_root:
        temp_path = Path(temp_root)
        project_dir = temp_path / project_name
        profiles_dir = temp_path / "profiles"
        project_dir.mkdir(parents=True, exist_ok=True)

        run_env = os.environ.copy()
        _write_service_account_json(temp_path, run_env)
        _write_project_files(project_dir, files)
        _patch_schema_source_dataset(project_dir, source_dataset)
        keyfile = _validate_service_account_config(auth_method, run_env)
        _write_profiles(profiles_dir, project_name, project_id, target_dataset, location, auth_method, threads, keyfile)

        dataset_status = _create_bigquery_dataset(project_id, target_dataset, location, run_env)
        commands = [
            [dbt_executable, "debug", "--profiles-dir", str(profiles_dir)],
            [dbt_executable, "parse", "--profiles-dir", str(profiles_dir)],
            [dbt_executable, "run", "--profiles-dir", str(profiles_dir)],
        ]
        command_results = []
        for command in commands:
            result = _run_dbt_command(command, project_dir, run_env, timeout_seconds)
            command_results.append(result)
            if not result["success"]:
                missing_tables = _extract_missing_bigquery_tables(result)
                message = f"Publish to BigQuery failed while running: {result['command']}"
                if missing_tables:
                    message = (
                        "Publish to BigQuery connected successfully, but required source table(s) "
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
                    "dataset_status": dataset_status,
                    "commands": command_results,
                    "macro_complexity": macro_complexity,
                    "tool_count": tool_count,
                    "connection_count": connection_count,
                    "missing_source_tables": missing_tables,
                    "message": message,
                }

    return {
        "success": True,
        "status": "published",
        "project_id": project_id,
        "target_dataset": target_dataset,
        "source_dataset": source_dataset,
        "location": location,
        "project_name": project_name,
        "dataset_status": dataset_status,
        "commands": command_results,
        "macro_complexity": macro_complexity,
        "tool_count": tool_count,
        "connection_count": connection_count,
        "final_model": f"{project_id}.{target_dataset}.{project_name}",
        "message": "dbt project published to BigQuery successfully.",
    }
