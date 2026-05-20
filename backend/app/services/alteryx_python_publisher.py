import json
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from app.services.alteryx_dbt_publisher import fetch_bigquery_table_metadata, publish_dbt_project_to_bigquery
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


def _portable_basename(value: str) -> str:
    raw = str(value or "")
    return PureWindowsPath(raw).name or PurePosixPath(raw).name or Path(raw).name


def _copy_accessible_source_files(project_dir: Path, sources: list[dict[str, Any]]) -> None:
    search_roots = [
        Path(path.strip())
        for path in re.split(r"[;|]", _env("ALTERYX_SOURCE_SEARCH_PATHS"))
        if path.strip()
    ]
    for source in sources:
        source_path = Path(str(source.get("path") or ""))
        if not source_path.exists() or not source_path.is_file():
            source_name = _portable_basename(str(source.get("path") or source.get("name") or ""))
            matched = None
            for root in search_roots:
                candidates = [
                    root / source_path,
                    root / source_name,
                ]
                for candidate in candidates:
                    if candidate.exists() and candidate.is_file():
                        matched = candidate
                        break
                if matched is not None:
                    break
                try:
                    matches = list(root.rglob(source_name))
                except Exception:
                    matches = []
                if matches:
                    matched = matches[0]
                    break
            if matched is None:
                continue
            source_path = matched
        target = project_dir / _portable_basename(str(source_path))
        if target.exists():
            continue
        target.write_bytes(source_path.read_bytes())


def _write_embedded_source_assets(project_dir: Path, assets: list[dict[str, Any]]) -> None:
    for asset in assets:
        name = Path(str(asset.get("name") or asset.get("path") or "")).name
        content = str(asset.get("content") or "")
        if not name or not content:
            continue
        target = project_dir / name
        if target.exists():
            continue
        encoding = str(asset.get("encoding") or "base64").lower()
        if encoding == "base64":
            target.write_bytes(base64.b64decode(content))
        else:
            target.write_text(content, encoding="utf-8")


def _write_inline_service_account(work_dir: Path, env: dict[str, str]) -> None:
    write_service_account_file_from_env(work_dir, env)


def _create_bigquery_dataset(project_id: str, dataset: str, location: str, env: dict[str, str]) -> dict[str, Any]:
    try:
        from google.cloud import bigquery
    except Exception as exc:
        return {
            "dataset": f"{project_id}.{dataset}",
            "created": False,
            "location": location,
            "status": "skipped",
            "message": "google-cloud-bigquery is not installed; pipeline publish will fail if it is needed.",
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


def _safe_table_name(value: str, fallback: str) -> str:
    candidate = Path(str(value or "")).stem or fallback
    candidate = re.sub(r"[^A-Za-z0-9_]+", "_", candidate).strip("_").lower()
    return candidate or fallback


def _expected_output_tables(project: dict[str, Any]) -> list[str]:
    project_name = _safe_table_name(str(project.get("project_name") or "alteryx_python_pipeline"), "alteryx_python_pipeline")
    output_targets = project.get("output_targets") or []
    if not output_targets:
        return [project_name]

    tables: list[str] = []
    for index, output in enumerate(output_targets, start=1):
        name = str(output.get("name") or output.get("path") or f"output_{index}")
        tables.append(_safe_table_name(name, f"output_{index}"))
    return sorted(set(tables))


def _run_python_pipeline(command: list[str], cwd: Path, env: dict[str, str], timeout_seconds: int) -> dict[str, Any]:
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


def _published_row_counts_from_stdout(stdout: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    pattern = re.compile(r"Published\s+([\d,]+)\s+rows\s+to\s+([^\s]+)", re.IGNORECASE)
    for match in pattern.finditer(stdout or ""):
        try:
            row_count = int(match.group(1).replace(",", ""))
        except ValueError:
            continue
        table_id = match.group(2).strip().strip("`")
        table_name = table_id.split(".")[-1]
        if table_name:
            counts[table_name] = row_count
    return counts


def _python_executable_sibling(executable: str) -> str:
    path = Path(executable)
    sibling = path.with_name("python.exe" if os.name == "nt" else "python")
    return str(sibling) if sibling.exists() else ""


def _pipeline_runtime_ready(executable: str) -> bool:
    if not executable:
        return False
    probe = (
        "import importlib.util, sys; "
        "required=['pandas','google.cloud.bigquery','pyarrow']; "
        "missing=[m for m in required if importlib.util.find_spec(m) is None]; "
        "sys.exit(1 if missing else 0)"
    )
    try:
        completed = subprocess.run(
            [executable, "-c", probe],
            text=True,
            capture_output=True,
            timeout=20,
        )
        return completed.returncode == 0
    except Exception:
        return False


def _resolve_python_executable() -> str:
    configured = _env("PYTHON_PIPELINE_EXECUTABLE") or _env("PYTHON_EXECUTABLE")
    if configured:
        return configured

    candidates: list[str] = []

    dbt_executable = _env("DBT_EXECUTABLE")
    if dbt_executable:
        sibling_python = _python_executable_sibling(dbt_executable)
        if sibling_python:
            candidates.append(sibling_python)

    candidates.append(sys.executable)

    for candidate in ("python", "py"):
        resolved = shutil.which(candidate)
        if resolved:
            candidates.append(resolved)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if _pipeline_runtime_ready(candidate):
            return candidate

    if candidates:
        return candidates[0]
    return sys.executable


def _format_pipeline_failure(result: dict[str, Any]) -> str:
    stdout = str(result.get("stdout") or "").strip()
    stderr = str(result.get("stderr") or "").strip()
    details = []
    if stderr:
        details.append(f"stderr:\n{stderr[-4000:]}")
    if stdout:
        details.append(f"stdout:\n{stdout[-4000:]}")
    suffix = "\n\n" + "\n\n".join(details) if details else ""
    return f"Publish to BigQuery failed while running: {result['command']}{suffix}"


def _env_flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value == "":
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def _publish_dbt_fallback(project: dict[str, Any], reason: str) -> dict[str, Any] | None:
    if not _env_flag("PYTHON_BIGQUERY_FALLBACK_TO_DBT", True):
        return None
    dbt_project = project.get("dbt_project")
    if not isinstance(dbt_project, dict) or not dbt_project.get("files"):
        return None
    result = publish_dbt_project_to_bigquery(dbt_project)
    result.update({
        "target": "python",
        "status": "published_via_dbt_fallback" if result.get("success", True) else "failed_via_dbt_fallback",
        "used_dbt_fallback": True,
        "fallback_reason": reason,
        "message": (
            "Python native publish was unavailable for this workflow, so the equivalent dbt project was published to BigQuery successfully."
            if result.get("success", True)
            else result.get("message", "Python native publish failed and dbt fallback also failed.")
        ),
    })
    return result


def publish_python_project_to_bigquery(project: dict[str, Any]) -> dict[str, Any]:
    publish_started = time.time()
    project_id = _required_env("GCP_PROJECT_ID")
    target_dataset = _required_env("GCP_BIGQUERY_DATASET")
    location = _env("GCP_BIGQUERY_LOCATION", "US")
    python_executable = _resolve_python_executable()
    timeout_seconds = int(_env("PYTHON_PIPELINE_TIMEOUT_SECONDS", "1200") or "1200")
    project_name = str(project.get("project_name") or "alteryx_python_pipeline")
    files = project.get("files") or {}

    if not files:
        raise ValueError("No Python project files were generated for this workflow.")
    if "pipeline.py" not in files:
        raise ValueError("Generated Python project does not include pipeline.py.")

    with tempfile.TemporaryDirectory(prefix="alteryx_python_publish_") as temp_root:
        temp_path = Path(temp_root)
        project_dir = temp_path / project_name
        project_dir.mkdir(parents=True, exist_ok=True)

        setup_started = time.time()
        run_env = os.environ.copy()
        run_env["GCP_PROJECT_ID"] = project_id
        run_env["GCP_BIGQUERY_DATASET"] = target_dataset
        run_env.setdefault("BQ_DATASET", target_dataset)
        run_env.setdefault("GCP_BIGQUERY_LOCATION", location)
        run_env.setdefault("BQ_WRITE_DISPOSITION", _env("BQ_WRITE_DISPOSITION", "WRITE_TRUNCATE"))
        _write_inline_service_account(temp_path, run_env)
        _write_project_files(project_dir, files)
        _write_embedded_source_assets(project_dir, project.get("source_assets") or [])
        _copy_accessible_source_files(project_dir, project.get("sources") or [])
        setup_duration = round(time.time() - setup_started, 2)

        dataset_started = time.time()
        dataset_status = _create_bigquery_dataset(project_id, target_dataset, location, run_env)
        dataset_duration = round(time.time() - dataset_started, 2)
        command = [python_executable, "pipeline.py", "--publish-bq"]
        result = _run_python_pipeline(command, project_dir, run_env, timeout_seconds)
        if not result["success"]:
            fallback = _publish_dbt_fallback(project, _format_pipeline_failure(result))
            if fallback is not None:
                fallback.setdefault("commands", [result])
                fallback.setdefault("timings", {})
                fallback["timings"].setdefault("python_pipeline_execution_seconds", result.get("duration_seconds"))
                fallback["timings"].setdefault("total_seconds", round(time.time() - publish_started, 2))
                return fallback
            return {
                "success": False,
                "status": "failed",
                "target": "python",
                "project_id": project_id,
                "target_dataset": target_dataset,
                "location": location,
                "project_name": project_name,
                "dataset_status": dataset_status,
                "commands": [result],
                "timings": {
                    "setup_seconds": setup_duration,
                    "dataset_prepare_seconds": dataset_duration,
                    "pipeline_execution_seconds": result.get("duration_seconds"),
                    "total_seconds": round(time.time() - publish_started, 2),
                },
                "message": _format_pipeline_failure(result),
            }

        metadata_started = time.time()
        published_tables = []
        stdout_row_counts = _published_row_counts_from_stdout(result.get("stdout") or "")
        for table in _expected_output_tables(project):
            metadata = fetch_bigquery_table_metadata(project_id, target_dataset, table, location, run_env)
            metadata_row_count = metadata.get("row_count")
            row_count = metadata_row_count if metadata_row_count is not None else stdout_row_counts.get(table)
            published_tables.append({
                "table": table,
                "name": table,
                "final_model": f"{project_id}.{target_dataset}.{table}",
                "metadata": metadata,
                "row_count": row_count,
                "record_count": metadata.get("record_count") if metadata.get("record_count") is not None else row_count,
                "total_records": metadata.get("total_records") if metadata.get("total_records") is not None else row_count,
                "column_count": metadata.get("column_count"),
                "total_columns": metadata.get("total_columns"),
                "available_columns": metadata.get("available_columns") or [],
                "profile": metadata.get("profile") or {},
            })
        metadata_duration = round(time.time() - metadata_started, 2)

    primary = published_tables[0] if published_tables else {}
    known_row_counts = [
        int(table["row_count"])
        for table in published_tables
        if table.get("row_count") is not None
    ]
    aggregate_row_count = sum(known_row_counts) if known_row_counts else primary.get("row_count")
    known_column_counts = [
        int(table["column_count"])
        for table in published_tables
        if table.get("column_count") is not None
    ]
    aggregate_column_count = sum(known_column_counts) if known_column_counts else primary.get("column_count")
    total_duration = round(time.time() - publish_started, 2)
    return {
        "success": True,
        "status": "published",
        "target": "python",
        "project_id": project_id,
        "target_dataset": target_dataset,
        "location": location,
        "project_name": project_name,
        "dataset_status": dataset_status,
        "commands": [result],
        "timings": {
            "setup_seconds": setup_duration,
            "dataset_prepare_seconds": dataset_duration,
            "pipeline_execution_seconds": result.get("duration_seconds"),
            "metadata_fetch_seconds": metadata_duration,
            "total_seconds": total_duration,
        },
        "analysis_duration_seconds": setup_duration,
        "publish_duration_seconds": total_duration,
        "published_tables": published_tables,
        "tables_deployed": len(published_tables),
        "final_model": primary.get("final_model") or f"{project_id}.{target_dataset}.{_safe_table_name(project_name, 'alteryx_python_pipeline')}",
        "bigquery_metadata": primary.get("metadata") or {},
        "target_profile": (primary.get("metadata") or {}).get("profile") or {},
        "row_count": aggregate_row_count,
        "total_rows": aggregate_row_count,
        "record_count": aggregate_row_count,
        "total_records": aggregate_row_count,
        "column_count": aggregate_column_count,
        "total_columns": aggregate_column_count,
        "available_columns": primary.get("available_columns") or [],
        "output_count": len(published_tables),
        "message": "Python pipeline published to BigQuery successfully.",
    }
