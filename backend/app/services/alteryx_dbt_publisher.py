# import os
# import json
# import re
# import shutil
# import subprocess
# import tempfile
# import time
# from pathlib import Path
# from typing import Any


# def _env(name: str, default: str = "") -> str:
#     return (os.getenv(name) or default).strip()


# def _required_env(name: str) -> str:
#     value = _env(name)
#     if not value:
#         raise ValueError(f"Missing required environment variable: {name}")
#     return value


# def _write_project_files(project_dir: Path, files: dict[str, Any]) -> None:
#     for relative_path, content in files.items():
#         target = project_dir / str(relative_path).replace("\\", "/")
#         target.parent.mkdir(parents=True, exist_ok=True)
#         target.write_text(str(content), encoding="utf-8")


# def _patch_schema_source_dataset(project_dir: Path, source_dataset: str) -> None:
#     schema_path = project_dir / "models" / "schema.yml"
#     if not schema_path.exists():
#         return

#     lines = schema_path.read_text(encoding="utf-8").splitlines()
#     patched: list[str] = []
#     inserted = False
#     for index, line in enumerate(lines):
#         patched.append(line)
#         if not inserted and line.strip() == "- name: alteryx_raw":
#             next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
#             if not next_line.startswith("schema:"):
#                 patched.append(f"    schema: {source_dataset}")
#             inserted = True
#     schema_path.write_text("\n".join(patched) + "\n", encoding="utf-8")


# def _write_profiles(
#     profiles_dir: Path,
#     profile_name: str,
#     project_id: str,
#     dataset: str,
#     location: str,
#     auth_method: str,
#     threads: int,
#     keyfile: str = "",
# ) -> None:
#     profiles_dir.mkdir(parents=True, exist_ok=True)
#     keyfile_yaml = keyfile.replace("\\", "/")
#     keyfile_line = f"      keyfile: \"{keyfile_yaml}\"\n" if auth_method == "service-account" and keyfile else ""
#     profile = (
#         f"{profile_name}:\n"
#         "  target: dev\n"
#         "  outputs:\n"
#         "    dev:\n"
#         "      type: bigquery\n"
#         f"      method: {auth_method}\n"
#         f"      project: {project_id}\n"
#         f"      dataset: {dataset}\n"
#         f"      location: {location}\n"
#         f"{keyfile_line}"
#         f"      threads: {threads}\n"
#         "      priority: interactive\n"
#         "      job_execution_timeout_seconds: 300\n"
#         "      job_retries: 1\n"
#     )
#     profiles_path = profiles_dir / "profiles.yml"
#     profiles_path.write_text(profile, encoding="utf-8")


# def _write_service_account_json(work_dir: Path, env: dict[str, str]) -> None:
#     service_account_json = _env("GCP_SERVICE_ACCOUNT_JSON")
#     if not service_account_json:
#         return

#     credentials_path = work_dir / "gcp_service_account.json"
#     try:
#         parsed = json.loads(service_account_json)
#         credentials_path.write_text(json.dumps(parsed), encoding="utf-8")
#     except json.JSONDecodeError:
#         credentials_path.write_text(service_account_json, encoding="utf-8")
#     env["GOOGLE_APPLICATION_CREDENTIALS"] = str(credentials_path)


# def _validate_service_account_config(auth_method: str, env: dict[str, str]) -> str:
#     if auth_method != "service-account":
#         return ""

#     keyfile = env.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
#     if not keyfile:
#         raise RuntimeError(
#             "GCP_DBT_AUTH_METHOD is set to service-account, but no service account key was configured. "
#             "Set GOOGLE_APPLICATION_CREDENTIALS to a valid JSON key file path or set GCP_SERVICE_ACCOUNT_JSON."
#         )

#     keyfile_path = Path(keyfile)
#     if not keyfile_path.exists():
#         raise RuntimeError(f"Configured GOOGLE_APPLICATION_CREDENTIALS file was not found: {keyfile}")

#     try:
#         data = json.loads(keyfile_path.read_text(encoding="utf-8"))
#     except Exception as exc:
#         raise RuntimeError(f"Configured service account key file is not valid JSON: {keyfile}") from exc

#     if data.get("type") != "service_account" or not data.get("client_email") or not data.get("private_key"):
#         raise RuntimeError(
#             "Configured service account key JSON is missing required fields: type, client_email, or private_key."
#         )
#     return str(keyfile_path)


# def _create_bigquery_dataset(project_id: str, dataset: str, location: str, env: dict[str, str]) -> dict[str, Any]:
#     try:
#         from google.cloud import bigquery
#     except Exception as exc:
#         return {
#             "dataset": f"{project_id}.{dataset}",
#             "created": False,
#             "location": location,
#             "status": "skipped",
#             "message": (
#                 "google-cloud-bigquery is not installed in the backend runtime. "
#                 "Skipping explicit dataset creation; dbt will create the BigQuery schema if permissions allow."
#             ),
#             "error": str(exc),
#         }

#     credentials_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
#     if credentials_path:
#         os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

#     client = bigquery.Client(project=project_id)
#     dataset_ref = bigquery.Dataset(f"{project_id}.{dataset}")
#     dataset_ref.location = location
#     created = False
#     try:
#         client.get_dataset(dataset_ref)
#     except Exception:
#         client.create_dataset(dataset_ref, exists_ok=True)
#         created = True
#     return {"dataset": f"{project_id}.{dataset}", "created": created, "location": location}


# def _run_dbt_command(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
#     started = time.time()
#     completed = subprocess.run(
#         command,
#         cwd=str(cwd),
#         env=env,
#         text=True,
#         capture_output=True,
#         timeout=timeout_seconds,
#     )
#     return {
#         "command": " ".join(command),
#         "return_code": completed.returncode,
#         "duration_seconds": round(time.time() - started, 2),
#         "stdout": completed.stdout[-12000:],
#         "stderr": completed.stderr[-12000:],
#         "success": completed.returncode == 0,
#     }


# def _extract_missing_bigquery_tables(result: dict[str, Any]) -> list[str]:
#     output = "\n".join([str(result.get("stdout") or ""), str(result.get("stderr") or "")])
#     missing = re.findall(r"Not found: Table\s+([^\s]+)\s+was not found", output)
#     return sorted(set(missing))


# def publish_dbt_project_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
#     project_id = _required_env("GCP_PROJECT_ID")
#     target_dataset = _required_env("GCP_BIGQUERY_DATASET")
#     source_dataset = _env("GCP_BIGQUERY_SOURCE_DATASET", target_dataset)
#     location = _env("GCP_BIGQUERY_LOCATION", "US")
#     auth_method = _env("GCP_DBT_AUTH_METHOD", "oauth")
#     dbt_executable = _env("DBT_EXECUTABLE", "dbt")
#     threads = int(_env("DBT_THREADS", "4") or "4")
#     timeout_seconds = int(_env("DBT_COMMAND_TIMEOUT_SECONDS", "600") or "600")
#     project_name = str(project.get("project_name") or "alteryx_dbt_project")
#     files = project.get("files") or {}
#     macro_complexity = project.get("macro_complexity") or {}
#     tool_count = int(project.get("tool_count") or 0)
#     connection_count = int(project.get("connection_count") or 0)
#     output_targets = project.get("output_targets") or []

#     # Load Google Cloud credentials from GOOGLE_CREDENTIALS_JSON env var
#     creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
#     if creds_json:
#         tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
#         tmp.write(creds_json)
#         tmp.flush()
#         os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = tmp.name

#     if not files:
#         raise ValueError("No dbt project files were generated for this workflow.")
#     if not shutil.which(dbt_executable):
#         raise RuntimeError(
#             f"dbt executable '{dbt_executable}' was not found. Set DBT_EXECUTABLE or install dbt-bigquery."
#         )

#     with tempfile.TemporaryDirectory(prefix="alteryx_dbt_publish_") as temp_root:
#         temp_path = Path(temp_root)
#         project_dir = temp_path / project_name
#         profiles_dir = temp_path / "profiles"
#         project_dir.mkdir(parents=True, exist_ok=True)

#         run_env = os.environ.copy()
#         _write_service_account_json(temp_path, run_env)
#         _write_project_files(project_dir, files)
#         _patch_schema_source_dataset(project_dir, source_dataset)
#         keyfile = _validate_service_account_config(auth_method, run_env)
#         _write_profiles(profiles_dir, project_name, project_id, target_dataset, location, auth_method, threads, keyfile)

#         dataset_status = _create_bigquery_dataset(project_id, target_dataset, location, run_env)
#         commands = [
#             [dbt_executable, "debug", "--profiles-dir", str(profiles_dir)],
#             [dbt_executable, "parse", "--profiles-dir", str(profiles_dir)],
#             [dbt_executable, "run", "--profiles-dir", str(profiles_dir)],
#         ]
#         command_results = []
#         for command in commands:
#             result = _run_dbt_command(command, project_dir, run_env, timeout_seconds)
#             command_results.append(result)
#             if not result["success"]:
#                 missing_tables = _extract_missing_bigquery_tables(result)
#                 message = f"Publish to BigQuery failed while running: {result['command']}"
#                 if missing_tables:
#                     message = (
#                         "Publish to BigQuery connected successfully, but required source table(s) "
#                         f"were not found in BigQuery: {', '.join(missing_tables)}"
#                     )
#                 return {
#                     "success": False,
#                     "status": "failed",
#                     "project_id": project_id,
#                     "target_dataset": target_dataset,
#                     "source_dataset": source_dataset,
#                     "location": location,
#                     "project_name": project_name,
#                     "dataset_status": dataset_status,
#                     "commands": command_results,
#                     "macro_complexity": macro_complexity,
#                     "tool_count": tool_count,
#                     "connection_count": connection_count,
#                     "output_targets": output_targets,
#                     "output_count": len(output_targets),
#                     "missing_source_tables": missing_tables,
#                     "message": message,
#                 }

#     return {
#         "success": True,
#         "status": "published",
#         "project_id": project_id,
#         "target_dataset": target_dataset,
#         "source_dataset": source_dataset,
#         "location": location,
#         "project_name": project_name,
#         "dataset_status": dataset_status,
#         "commands": command_results,
#         "macro_complexity": macro_complexity,
#         "tool_count": tool_count,
#         "connection_count": connection_count,
#         "output_targets": output_targets,
#         "output_count": len(output_targets),
#         "final_model": f"{project_id}.{target_dataset}.{project_name}",
#         "message": "dbt project published to BigQuery successfully.",
#     }





import os
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from app.services.gcp_credentials import service_account_info_from_env, write_service_account_file_from_env
 
 
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
    write_service_account_file_from_env(work_dir, env)
 
 
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
 
 
def fetch_bigquery_table_metadata(
    project_id: str,
    dataset: str,
    table: str,
    location: str = "",
    env: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """Read row, column, and lightweight data profile metrics from BigQuery."""
    try:
        from google.cloud import bigquery
    except Exception as exc:
        return {
            "success": False,
            "status": "unavailable",
            "project_id": project_id,
            "dataset": dataset,
            "table": table,
            "message": "google-cloud-bigquery is not installed in the backend runtime.",
            "error": str(exc),
        }
 
    credentials_path = (env or {}).get("GOOGLE_APPLICATION_CREDENTIALS")
    if credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
 
    try:
        service_account_info = service_account_info_from_env(env)
        if not credentials_path and service_account_info:
            from google.oauth2 import service_account
 
            credentials = service_account.Credentials.from_service_account_info(service_account_info)
            client = bigquery.Client(project=project_id, credentials=credentials)
        else:
            client = bigquery.Client(project=project_id)
        table_id = f"{project_id}.{dataset}.{table}"
        table_ref = client.get_table(table_id)
        columns = [field.name for field in table_ref.schema]
        schema_by_name = {field.name: str(field.field_type or "").upper() for field in table_ref.schema}
 
        count_sql = f"select count(1) as row_count from `{project_id}.{dataset}.{table}`"
        job_config = bigquery.QueryJobConfig(use_legacy_sql=False)
        count_job = client.query(
            count_sql,
            job_config=job_config,
            location=location or None,
        )
        row_count = next(iter(count_job.result())).row_count
        row_count = int(row_count or 0)
        column_count = len(columns)
        profile_columns: dict[str, Any] = {}
        numeric_columns: list[str] = []

        def _quote_identifier(value: str) -> str:
            return f"`{str(value).replace('`', '')}`"

        def _safe_alias(value: str) -> str:
            return re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_") or "Column"

        numeric_types = {"INT64", "INTEGER", "FLOAT64", "FLOAT", "NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"}
        profile_limit = int(_env("BQ_PROFILE_MAX_COLUMNS", "40") or "40")
        profile_fields = table_ref.schema[: max(profile_limit, 0)]
        if profile_fields:
            select_parts = []
            for field in profile_fields:
                name = field.name
                alias = _safe_alias(name)
                quoted = _quote_identifier(name)
                field_type = schema_by_name.get(name, "")
                select_parts.append(f"count({quoted}) as `{alias}__NotNull`")
                if field_type in numeric_types:
                    numeric_columns.append(name)
                    select_parts.extend([
                        f"count({quoted}) as `{alias}__NumericCount`",
                        f"min({quoted}) as `{alias}__Min`",
                        f"max({quoted}) as `{alias}__Max`",
                        f"sum(cast({quoted} as FLOAT64)) as `{alias}__Sum`",
                        f"avg(cast({quoted} as FLOAT64)) as `{alias}__Average`",
                    ])
            profile_sql = f"select {', '.join(select_parts)} from `{project_id}.{dataset}.{table}`"
            profile_job = client.query(
                profile_sql,
                job_config=bigquery.QueryJobConfig(use_legacy_sql=False),
                location=location or None,
            )
            profile_row = dict(next(iter(profile_job.result())))
            for field in profile_fields:
                name = field.name
                alias = _safe_alias(name)
                not_null_count = int(profile_row.get(f"{alias}__NotNull") or 0)
                column_profile = {
                    "name": name,
                    "row_count": row_count,
                    "not_null_count": not_null_count,
                    "null_count": max(row_count - not_null_count, 0),
                    "numeric_count": int(profile_row.get(f"{alias}__NumericCount") or 0),
                    "data_type": schema_by_name.get(name),
                }
                if name in numeric_columns:
                    column_profile.update({
                        "min_value": None if profile_row.get(f"{alias}__Min") is None else str(profile_row.get(f"{alias}__Min")),
                        "max_value": None if profile_row.get(f"{alias}__Max") is None else str(profile_row.get(f"{alias}__Max")),
                        "sum_value": None if profile_row.get(f"{alias}__Sum") is None else str(profile_row.get(f"{alias}__Sum")),
                        "average_value": None if profile_row.get(f"{alias}__Average") is None else str(profile_row.get(f"{alias}__Average")),
                    })
                profile_columns[name] = column_profile

        profile = {
            "name": table_id,
            "row_count": row_count,
            "column_count": column_count,
            "columns": profile_columns,
            "numeric_columns": numeric_columns,
            "profiled_column_count": len(profile_columns),
            "profile_limit": profile_limit,
        }
 
        return {
            "success": True,
            "status": "available",
            "project_id": project_id,
            "dataset": dataset,
            "table": table,
            "final_model": table_id,
            "row_count": row_count,
            "total_rows": row_count,
            "record_count": row_count,
            "total_records": row_count,
            "column_count": column_count,
            "total_columns": column_count,
            "available_columns": columns,
            "profile": profile,
            "columns_profile": profile_columns,
            "numeric_columns": numeric_columns,
            "table_type": getattr(table_ref, "table_type", None),
            "num_rows_metadata": getattr(table_ref, "num_rows", None),
            "source": "bigquery_count_query_and_table_schema",
        }
    except Exception as exc:
        return {
            "success": False,
            "status": "failed",
            "project_id": project_id,
            "dataset": dataset,
            "table": table,
            "final_model": f"{project_id}.{dataset}.{table}",
            "message": f"Failed to fetch BigQuery table metadata: {exc}",
            "error": str(exc),
        }
 
 
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
 
 
def _env_flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def _format_dbt_failure(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    details = []
    if stderr:
        details.append(f"stderr:\n{stderr[-4000:]}")
    if stdout:
        details.append(f"stdout:\n{stdout[-4000:]}")
    suffix = "\n\n" + "\n\n".join(details) if details else ""
    return f"Publish to BigQuery failed while running: {result['command']}{suffix}"


def _parse_compact_row_count(value: str) -> Optional[int]:
    raw = str(value or "").strip().lower().replace(",", "")
    match = re.match(r"^(\d+(?:\.\d+)?)([kmb])?$", raw)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


def _extract_dbt_stdout_row_count(command_results: list[dict[str, Any]], project_name: str) -> Optional[int]:
    """Best-effort fallback when BigQuery metadata lookup is unavailable."""
    target = str(project_name or "").lower()
    candidates: list[int] = []
    for result in command_results:
        stdout = str(result.get("stdout") or "")
        for line in stdout.splitlines():
            lowered = line.lower()
            if " rows" not in lowered or "create" not in lowered:
                continue
            row_match = re.search(r"\(([\d,.]+(?:[kmb])?)\s+rows?\b", lowered)
            if not row_match:
                continue
            parsed = _parse_compact_row_count(row_match.group(1))
            if parsed is None:
                continue
            if target and target in lowered:
                return parsed
            candidates.append(parsed)
    return candidates[-1] if candidates else None


def _safe_table_name(value: str, fallback: str) -> str:
    raw = str(value or fallback).split("\\")[-1].split("/")[-1]
    raw = re.sub(r"\.(csv|xlsx?|json|xml|txt|parquet|yxdb|yxmd|yxmc)$", "", raw, flags=re.IGNORECASE)
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    return safe or fallback


def _output_table_names(project_name: str, output_targets: list[dict[str, Any]]) -> list[str]:
    if not output_targets:
        return [project_name]
    names: list[str] = []
    for index, output in enumerate(output_targets, start=1):
        if not isinstance(output, dict):
            continue
        names.append(_safe_table_name(str(output.get("name") or output.get("path") or f"output_{index}"), f"output_{index}"))
    return names or [project_name]


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
    output_targets = project.get("output_targets") or []
 
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
        commands = []
        if _env_flag("DBT_RUN_DEBUG", False):
            commands.append([dbt_executable, "debug", "--profiles-dir", str(profiles_dir)])
        commands.extend([
            [dbt_executable, "parse", "--profiles-dir", str(profiles_dir)],
            [dbt_executable, "run", "--profiles-dir", str(profiles_dir)],
        ])
        command_results = []
        for command in commands:
            result = _run_dbt_command(command, project_dir, run_env, timeout_seconds)
            command_results.append(result)
            if not result["success"]:
                missing_tables = _extract_missing_bigquery_tables(result)
                message = _format_dbt_failure(result)
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
                    "output_targets": output_targets,
                    "output_count": len(output_targets),
                    "missing_source_tables": missing_tables,
                    "message": message,
                }
 
        published_tables = []
        for table_name in _output_table_names(project_name, output_targets):
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
        stdout_row_count = _extract_dbt_stdout_row_count(command_results, project_name)
        if bigquery_metadata.get("row_count") is None and stdout_row_count is not None:
            bigquery_metadata = {
                **bigquery_metadata,
                "row_count": stdout_row_count,
                "total_rows": stdout_row_count,
                "record_count": stdout_row_count,
                "total_records": stdout_row_count,
                "source": "dbt_run_stdout_fallback",
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
        "output_targets": output_targets,
        "output_count": len(output_targets),
        "published_tables": published_tables,
        "tables_deployed": len(published_tables),
        "final_model": f"{project_id}.{target_dataset}.{project_name}",
        "bigquery_metadata": bigquery_metadata,
        "target_profile": bigquery_metadata.get("profile") or {},
        "row_count": bigquery_metadata.get("row_count"),
        "total_rows": bigquery_metadata.get("total_rows"),
        "record_count": bigquery_metadata.get("record_count"),
        "total_records": bigquery_metadata.get("total_records"),
        "column_count": bigquery_metadata.get("column_count"),
        "total_columns": bigquery_metadata.get("total_columns"),
        "available_columns": bigquery_metadata.get("available_columns") or [],
        "message": "dbt project published to BigQuery successfully.",
    }
