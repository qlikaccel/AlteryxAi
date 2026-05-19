# Alteryx accelerator backend routes.

import asyncio
import logging
import json
import base64
import csv
import os
import zipfile
from io import BytesIO
import requests
import re
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi import APIRouter, File, HTTPException, Header, Query, Response, UploadFile
from fastapi.responses import HTMLResponse
from typing import Any, Optional
from pydantic import BaseModel
from urllib.parse import quote

from app.utils.alteryx_workspace_utils import (
    AlteryxSession,
    create_alteryx_session,
    _get_with_refresh,
    ALTERYX_BASE_URL,
    list_alteryx_workflows,
    persist_alteryx_tokens,
    get_alteryx_token_diagnostics,
    ensure_fresh_token,
    looks_like_access_token,
    looks_like_refresh_token,
    is_token_expired,
    token_expiry_summary,
)
from app.services.alteryx_bulk_ingestion import ingest_uploaded_files, load_batch
from app.services.alteryx_migration_engine import (
    DEFAULT_SHAREPOINT_FILE_NAME,
    DEFAULT_SHAREPOINT_FILE_URL,
    generate_brd_html,
    generate_dataform_project,
    generate_executive_summary,
    generate_dbt_project,
    generate_m_query,
    generate_python_project,
    generate_workflow_diagram,
    validate_migration,
)
from app.services.alteryx_dbt_publisher import fetch_bigquery_table_metadata, publish_dbt_project_to_bigquery
from app.services.alteryx_dataform_publisher import publish_dataform_project_to_bigquery
from app.services.alteryx_dataform_repo_publisher import publish_dataform_project_to_repository
from app.services.alteryx_python_publisher import publish_python_project_to_bigquery
from app.services.alteryx_transform_plan import build_transform_plan, transform_publish_blocker_detail
from app.services.alteryx_validation_engine import (
    aggregate_bigquery_validation_payload,
    build_validation_response,
)
from app.services.reconciliation_engine import profile_rows

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx"])
logger = logging.getLogger(__name__)

MAX_BULK_UPLOAD_BYTES = 250 * 1024 * 1024
PUBLISH_JOB_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("ALTERYX_PUBLISH_JOB_WORKERS", "2") or "2"))
PUBLISH_JOBS: dict[str, dict[str, Any]] = {}


def _start_publish_job(target: str, work) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    PUBLISH_JOBS[job_id] = {
        "job_id": job_id,
        "target": target,
        "status": "running",
        "success": None,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }

    def _runner() -> None:
        try:
            result = work()
            PUBLISH_JOBS[job_id].update({
                "status": "completed",
                "success": bool(result.get("success", True)) if isinstance(result, dict) else True,
                "result": result,
                "updated_at": int(time.time()),
            })
        except HTTPException as exc:
            PUBLISH_JOBS[job_id].update({
                "status": "failed",
                "success": False,
                "error": exc.detail,
                "status_code": exc.status_code,
                "updated_at": int(time.time()),
            })
        except Exception as exc:
            logger.exception("Publish job failed")
            PUBLISH_JOBS[job_id].update({
                "status": "failed",
                "success": False,
                "error": str(exc),
                "traceback": traceback.format_exc()[-4000:],
                "updated_at": int(time.time()),
            })

    PUBLISH_JOB_EXECUTOR.submit(_runner)
    return PUBLISH_JOBS[job_id]


@router.get("/publish-jobs/{job_id}")
def get_publish_job(job_id: str):
    job = PUBLISH_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Publish job not found: {job_id}")
    return job


@router.get("/diagnostics/tokens")
def alteryx_token_diagnostics():
    return get_alteryx_token_diagnostics()


# ── Schemas ───────────────────────────────────────────────────────────────────

class AlteryxAuthRequest(BaseModel):
    access_token: Optional[str] = ""
    refresh_token: Optional[str] = None
    workspace_name: str
    username: Optional[str] = None


class AlteryxAuthResponse(BaseModel):
    status: str
    workspace_name: str
    workspace_id: str
    access_token: str
    refresh_token: Optional[str] = None


class AlteryxWorkflow(BaseModel):
    id: str
    name: str
    lastModifiedDate: Optional[str] = None
    runCount: Optional[int] = None
    credentialType: Optional[str] = None
    workerTag: Optional[str] = None


class AlteryxBulkUploadResponse(BaseModel):
    batch_id: str
    summary: dict[str, Any]
    workflows: list[dict[str, Any]]
    rejected: list[dict[str, str]]
    workspace_name: Optional[str] = None


class CloudWorkflowMaterializeRequest(BaseModel):
    workflow_id: str
    workflow_name: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None


class AlteryxRecordCountValidationRequest(BaseModel):
    dataset_id: str
    table_name: str
    target_tables: list[str] = []
    workspace_id: Optional[str] = ""
    expected_row_count: Optional[int] = None
    numeric_columns: list[str] = []


def _workflow_for_client(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return a compact UI-safe workflow summary.

    Full workflow graphs and embedded package assets stay in the backend batch
    cache. The frontend fetches those details through the analysis endpoints.
    """
    workflow = workflow or {}
    safe_fields = {
        "id",
        "name",
        "lastModifiedDate",
        "runCount",
        "credentialType",
        "workerTag",
        "sourceFile",
        "packageFile",
        "fileType",
        "toolCount",
        "connectionCount",
        "convertibility",
        "complexity",
        "supportedToolCount",
        "unsupportedToolCount",
        "toolTypes",
        "unsupportedTools",
        "recommendations",
        "isMacroDefinition",
        "macroValidation",
    }
    client_workflow = {key: workflow.get(key) for key in safe_fields if key in workflow}

    def compact_items(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        compacted: list[dict[str, Any]] = []
        allowed = {
            "id",
            "name",
            "fileName",
            "path",
            "connection",
            "siteUrl",
            "type",
            "sourceType",
            "targetType",
            "macroName",
            "macroType",
            "status",
            "resolved",
            "exists",
            "toolId",
            "tool",
            "plugin",
        }
        for item in items:
            if isinstance(item, dict):
                compacted.append({key: value for key, value in item.items() if key in allowed})
        return compacted

    for key in ("dataSources", "outputTargets", "macroDependencies", "packageAssets"):
        if key in workflow:
            client_workflow[key] = compact_items(workflow.get(key))

    return client_workflow


def _workflows_for_client(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_workflow_for_client(workflow) for workflow in workflows or []]


def _plugin_key(node: dict[str, Any]) -> str:
    return str(node.get("plugin") or node.get("tool") or "").lower()


def _node_blob(node: dict[str, Any]) -> str:
    pieces = [
        str(node.get("configurationText") or ""),
        json.dumps(node.get("config") or {}, default=str),
        json.dumps(node.get("configuration") or {}, default=str),
        str(node.get("path") or ""),
        str(node.get("name") or ""),
    ]
    return "\n".join(piece for piece in pieces if piece)


def _workflow_has_python_tools(workflow: dict[str, Any]) -> bool:
    values: list[str] = []
    for key in ("unsupportedTools", "toolTypes", "recommendations"):
        values.extend(str(item or "") for item in (workflow.get(key) or []))
    for node in workflow.get("workflowNodes") or []:
        config = node.get("config") or {}
        values.extend(
            [
                str(node.get("plugin") or ""),
                str(node.get("tool") or ""),
                str(node.get("name") or ""),
                str(config.get("toolFamily") or ""),
            ]
        )
    return any("python" in value.lower() for value in values)


def _terminal_output_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow.get("workflowNodes") or []
    if not nodes:
        return []

    output_nodes = [
        node for node in nodes
        if any(token in _plugin_key(node) for token in ("output", "browse"))
    ]
    if output_nodes:
        return output_nodes

    destinations = {str(edge.get("to") or "") for edge in workflow.get("workflowEdges") or []}
    return [node for node in nodes if str(node.get("id") or "") not in destinations] or nodes[-1:]


def _parse_count_hint(blob: str) -> Optional[int]:
    patterns = [
        r"~\s*([\d,.]+)\s*([kKmM]?)\s*(?:rows|records)\b",
        r"\b([\d,.]+)\s*([kKmM])\s*(?:rows|records)\b",
        r"\b(?:rows|records|row count|record count)\s*[:=]\s*([\d,.]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, blob or "", flags=re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1).replace(",", ""))
        suffix = match.group(2).lower() if len(match.groups()) > 1 and match.group(2) else ""
        if suffix == "k":
            value *= 1_000
        elif suffix == "m":
            value *= 1_000_000
        return int(round(value))
    return None


def _candidate_output_paths(node: dict[str, Any]) -> list[Path]:
    blob = _node_blob(node)
    candidates = [
        match.group(0).strip().strip(";,'\")")
        for match in re.finditer(
            r"(?:[A-Za-z]:|\\\\|/|\\)?[^\n\"'<>]+\.(?:csv|txt|tsv)",
            blob,
            flags=re.IGNORECASE,
        )
    ]
    paths: list[Path] = []
    for raw in candidates:
        if raw.lower().startswith(("http://", "https://", "tfs://")):
            continue
        path = Path(raw)
        if not path.is_absolute():
            paths.append(Path.cwd() / raw)
            paths.append(Path.cwd() / "output" / Path(raw).name)
        else:
            paths.append(path)
    return paths


def _configured_output_search_roots() -> list[Path]:
    roots = [Path.cwd(), Path.cwd() / "output", Path.cwd().parent, Path.cwd().parent / "output"]
    for raw_root in re.split(r"[;|]", os.getenv("ALTERYX_OUTPUT_SEARCH_ROOTS", "")):
        if raw_root.strip():
            roots.append(Path(raw_root.strip()))
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def _candidate_named_output_paths(name: str) -> list[Path]:
    filename = Path(str(name or "")).name
    if not filename:
        return []
    candidates: list[Path] = []
    raw_path = Path(str(name))
    if raw_path.is_absolute():
        candidates.append(raw_path)
    for root in _configured_output_search_roots():
        candidates.extend([root / filename, root / "output" / filename])
    for root in _configured_output_search_roots():
        if not root.exists() or not root.is_dir():
            continue
        try:
            candidates.extend(list(root.rglob(filename))[:5])
        except Exception:
            continue
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _count_delimited_file(path: Path) -> Optional[int]:
    if not path.exists() or not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            row_count = sum(1 for _ in handle)
        return max(row_count - 1, 0)
    except OSError:
        return None


def _delimited_file_columns(path: Path) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;") if sample.strip() else csv.excel
            rows = csv.reader(handle, dialect)
            return [str(col).strip() for col in next(rows, []) if str(col).strip()]
    except Exception:
        return []


def _profile_delimited_file(path: Path) -> dict[str, Any]:
    """Build deterministic source profile from an Alteryx output file."""
    if not path.exists() or not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t|;") if sample.strip() else csv.excel
            rows = list(csv.DictReader(handle, dialect=dialect))
        profile = profile_rows(path.name, rows)
        columns = {name: vars(column) for name, column in profile.columns.items()}
        numeric_columns = [
            name
            for name, column in columns.items()
            if column.get("numeric_count", 0) > 0
        ]
        return {
            "name": profile.name,
            "row_count": profile.row_count,
            "column_count": len(profile.columns),
            "columns": columns,
            "numeric_columns": numeric_columns,
        }
    except Exception as exc:
        logger.warning("[record-count-validation] Could not profile Alteryx output %s: %s", path, exc)
        return {}


def _name_match_score(value: str, target: str) -> int:
    value_key = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    target_key = re.sub(r"[^a-z0-9]+", "", str(target or "").lower())
    if not value_key or not target_key:
        return 0
    if value_key == target_key:
        return 100
    if value_key in target_key or target_key in value_key:
        return 80
    value_parts = {part for part in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(part) > 2}
    target_parts = {part for part in re.split(r"[^a-z0-9]+", str(target or "").lower()) if len(part) > 2}
    overlap = value_parts & target_parts
    return len(overlap) * 10


def _parse_workflow_row_count_hint(workflow: dict[str, Any], final_table_name: str = "") -> dict[str, Any]:
    """
    Extract Alteryx record count from workflow metadata — no local file access needed.

    Strategy (in priority order):
    1. Final output node configurationText — Alteryx designers annotate output nodes
       with comments like "~2M records" or "Output: sales.csv (~1.5M rows)" which are
       embedded as human-readable labels in the tool annotation text.
    2. All output/terminal nodes — pick the best match by name similarity to final_table_name.
    3. Summarize node description — a Summarize immediately before the final output often
       contains a label describing the grouped output row count.
    4. Input node hints as a last resort (source rows, not transformed rows).
    """
    import re as _re

    def _parse_count_from_text(text: str) -> Optional[int]:
        """Parse count from strings like '~2M records', '1.5M rows', '28,591 rows', '500K'."""
        patterns = [
            r"[~(]?\s*([\d,.]+)\s*([kKmMbB]?)\s*(?:rows?|records?)",
            r"([\d,.]+)\s*([kKmM])",  # bare suffix without rows/records label
        ]
        for pat in patterns:
            for m in _re.finditer(pat, text, flags=_re.IGNORECASE):
                try:
                    value = float(m.group(1).replace(",", ""))
                    suffix = (m.group(2) or "").lower()
                    if suffix == "k":
                        value *= 1_000
                    elif suffix == "m":
                        value *= 1_000_000
                    elif suffix == "b":
                        value *= 1_000_000_000
                    result = int(round(value))
                    if result > 0:
                        return result
                except (ValueError, IndexError):
                    continue
        return None

    nodes = workflow.get("workflowNodes") or []

    # ── Pass 1: output nodes scored by name match to final_table_name ──────
    output_candidates: list[dict[str, Any]] = []
    for node in nodes:
        plugin = str(node.get("plugin") or "").lower()
        if not any(tok in plugin for tok in ("output", "browse")):
            continue
        blob = _node_blob(node)
        count = _parse_count_from_text(blob)
        if count is None:
            continue
        score = max(
            _name_match_score(blob, final_table_name),
            _name_match_score(str(node.get("name") or ""), final_table_name),
        ) if final_table_name else 0
        output_candidates.append({"count": count, "score": score, "source": blob[:120], "node": node})

    if output_candidates:
        output_candidates.sort(key=lambda x: (x["score"], x["count"]), reverse=True)
        best = output_candidates[0]
        return {
            "row_count": best["count"],
            "method": "workflow_output_node_hint",
            "source": f"Alteryx output node annotation: {best['source'][:80]}",
            "confidence": "high" if best["score"] >= 60 else "medium",
        }

    # ── Pass 2: Summarize node directly upstream of output ──────────────────
    edges = workflow.get("workflowEdges") or []
    # Build reverse edge map: node_id -> list of upstream node_ids
    upstream_map: dict[str, list[str]] = {}
    for edge in edges:
        to_id = str(edge.get("to") or "")
        from_id = str(edge.get("from") or "")
        upstream_map.setdefault(to_id, []).append(from_id)

    terminal_ids = {str(n.get("id") or "") for n in _terminal_output_nodes(workflow)}
    node_by_id = {str(n.get("id") or ""): n for n in nodes}

    for t_id in terminal_ids:
        for up_id in upstream_map.get(t_id, []):
            upstream_node = node_by_id.get(up_id)
            if not upstream_node:
                continue
            plugin = str(upstream_node.get("plugin") or "").lower()
            if "summarize" not in plugin and "aggregate" not in plugin:
                continue
            blob = _node_blob(upstream_node)
            count = _parse_count_from_text(blob)
            if count is not None and count > 0:
                return {
                    "row_count": count,
                    "method": "workflow_summarize_node_hint",
                    "source": f"Upstream Summarize node annotation: {blob[:80]}",
                    "confidence": "medium",
                }

    # ── Pass 3: dataSources metadata ─────────────────────────────────────────
    # The workflow JSON carries a dataSources array with row_count / no_of_rows
    # fields populated by the Alteryx upload ingestion pipeline.
    data_sources = workflow.get("dataSources") or []
    ds_counts: list[tuple[int, str]] = []
    for ds in data_sources:
        for key in ("row_count", "no_of_rows", "rowCount", "record_count"):
            val = ds.get(key)
            if isinstance(val, (int, float)) and val > 0:
                ds_counts.append((int(val), str(ds.get("name") or ds.get("path") or key)))
                break
    if ds_counts:
        ds_counts.sort(key=lambda x: x[0], reverse=True)
        total = sum(c for c, _ in ds_counts)
        return {
            "row_count": total,
            "method": "workflow_datasource_metadata",
            "source": f"Sum of dataSources row counts: {', '.join(n for _, n in ds_counts[:3])}",
            "confidence": "medium",
        }

    # ── Pass 4: any node annotation hint (input nodes as last resort) ─────────
    all_hints: list[tuple[int, str]] = []
    for node in nodes:
        blob = _node_blob(node)
        count = _parse_count_from_text(blob)
        if count is not None and count > 0:
            all_hints.append((count, blob[:80]))

    if all_hints:
        # Prefer the largest count hint found across all nodes
        all_hints.sort(key=lambda x: x[0], reverse=True)
        return {
            "row_count": all_hints[0][0],
            "method": "workflow_node_hint_fallback",
            "source": f"Node annotation (fallback): {all_hints[0][1]}",
            "confidence": "low",
        }

    return {
        "row_count": None,
        "method": "unavailable",
        "source": "No record count hint found in workflow metadata or node annotations.",
        "confidence": "none",
    }


def _resolve_alteryx_output_row_count(
    workflow: dict[str, Any],
    final_table_name: str = "",
    fallback: Optional[int] = None,
) -> dict[str, Any]:
    """Resolve Alteryx row count — local file first, then workflow metadata hints."""
    counted_outputs: list[dict[str, Any]] = []
    output_names = [
        str(item.get("path") or item.get("name") or "")
        for item in workflow.get("outputTargets") or []
        if isinstance(item, dict) and (item.get("path") or item.get("name"))
    ]
    for node in reversed(_terminal_output_nodes(workflow)):
        node_blob = _node_blob(node)
        node_paths = _candidate_output_paths(node)
        for output_name in output_names:
            if output_name in node_blob:
                node_paths.extend(_candidate_named_output_paths(output_name))
        for path in node_paths:
            count = _count_delimited_file(path)
            if count is None:
                continue
            score = max(
                _name_match_score(path.stem, final_table_name),
                _name_match_score(path.name, final_table_name),
                _name_match_score(node_blob, final_table_name),
            )
            columns = _delimited_file_columns(path)
            profile = _profile_delimited_file(path)
            counted_outputs.append({
                "row_count": count,
                "column_count": profile.get("column_count") or len(columns) or None,
                "columns": list((profile.get("columns") or {}).keys()) or columns,
                "profile": profile,
                "numeric_columns": profile.get("numeric_columns", []),
                "method": "matched_output_file" if score >= 80 else "output_file",
                "source": str(path),
                "confidence": "high" if score >= 80 or not final_table_name else "medium",
                "match_score": score,
            })

    existing_output_files = {Path(str(item.get("source") or "")).name for item in counted_outputs}
    for output_name in output_names:
        if Path(output_name).name in existing_output_files:
            continue
        for path in _candidate_named_output_paths(output_name):
            count = _count_delimited_file(path)
            if count is None:
                continue
            columns = _delimited_file_columns(path)
            profile = _profile_delimited_file(path)
            counted_outputs.append({
                "row_count": count,
                "column_count": profile.get("column_count") or len(columns) or None,
                "columns": list((profile.get("columns") or {}).keys()) or columns,
                "profile": profile,
                "numeric_columns": profile.get("numeric_columns", []),
                "method": "output_file",
                "source": str(path),
                "confidence": "medium",
                "match_score": _name_match_score(path.stem, final_table_name),
            })
            break

    if counted_outputs:
        counted_outputs.sort(key=lambda item: (item.get("match_score", 0), item.get("confidence") == "high"), reverse=True)
        best = counted_outputs[0]
        if not final_table_name or best.get("match_score", 0) >= 80 or len(counted_outputs) == 1:
            return best
        total_rows = sum(int(item.get("row_count") or 0) for item in counted_outputs)
        total_columns = sum(int(item.get("column_count") or 0) for item in counted_outputs)
        aggregate_columns: dict[str, Any] = {}
        aggregate_numeric_columns: list[str] = []
        for item in counted_outputs:
            output_label = Path(str(item.get("source") or "output")).stem
            for column_name, column_profile in ((item.get("profile") or {}).get("columns") or {}).items():
                aggregate_name = f"{output_label}.{column_name}"
                aggregate_columns[aggregate_name] = {**column_profile, "name": aggregate_name}
                if column_profile.get("numeric_count", 0) > 0:
                    aggregate_numeric_columns.append(aggregate_name)
        return {
            "row_count": total_rows,
            "column_count": total_columns or None,
            "columns": list(aggregate_columns.keys()),
            "profile": {
                "name": "Alteryx output files",
                "row_count": total_rows,
                "column_count": total_columns,
                "columns": aggregate_columns,
                "numeric_columns": aggregate_numeric_columns,
            },
            "numeric_columns": aggregate_numeric_columns,
            "method": "aggregate_output_files",
            "source": "Aggregated local Alteryx output CSV files.",
            "confidence": "medium",
        }

    # Local file not found — extract count from workflow node annotations
    hint = _parse_workflow_row_count_hint(workflow, final_table_name)
    if hint.get("row_count") is not None:
        return hint

    if fallback is not None:
        return {
            "row_count": fallback,
            "method": "stored_expected_row_count",
            "source": "stored expected_row_count",
            "confidence": "low",
        }

    return {
        "row_count": None,
        "method": "unavailable",
        "source": "No accessible Alteryx output file or workflow annotation found.",
        "confidence": "none",
    }


def _validation_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _safe_metric_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", str(value or "")).strip("_") or "Column"


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _within_numeric_tolerance(expected: Any, actual: Any, absolute: float = 0.0001, relative: float = 0.001) -> bool:
    expected_num = _to_float_or_none(expected)
    actual_num = _to_float_or_none(actual)
    if expected_num is None or actual_num is None:
        return expected == actual
    delta = abs(actual_num - expected_num)
    if delta <= absolute:
        return True
    denominator = max(abs(expected_num), 1.0)
    return (delta / denominator) <= relative


def _build_profile_validation_checks(source_profile: dict[str, Any], powerbi_validation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not source_profile or not powerbi_validation:
        return []
    actual = powerbi_validation.get("actual") or {}
    queried_columns = powerbi_validation.get("queried_numeric_columns") or []
    queried_by_key = {_validation_match_key(column): column for column in queried_columns}
    checks: list[dict[str, Any]] = []
    for source_name, source_column in (source_profile.get("columns") or {}).items():
        target_name = queried_by_key.get(_validation_match_key(source_name))
        if not target_name:
            continue
        metric_key = _safe_metric_key(target_name)
        metric_pairs = [
            ("not_null_count", source_column.get("not_null_count"), actual.get(f"{metric_key}__NotNull"), "high"),
            ("min_value", source_column.get("min_value"), actual.get(f"{metric_key}__Min"), "medium"),
            ("max_value", source_column.get("max_value"), actual.get(f"{metric_key}__Max"), "medium"),
            ("sum_value", source_column.get("sum_value"), actual.get(f"{metric_key}__Sum"), "medium"),
            ("average_value", source_column.get("average_value"), actual.get(f"{metric_key}__Average"), "medium"),
        ]
        for metric_name, expected, target, severity in metric_pairs:
            if expected is None and target is None:
                continue
            status = "PASS" if _within_numeric_tolerance(expected, target) else "WARNING"
            checks.append({
                "name": f"{source_name}.{metric_name}",
                "expected": expected,
                "actual": target,
                "variance": (
                    _to_float_or_none(target) - _to_float_or_none(expected)
                    if _to_float_or_none(target) is not None and _to_float_or_none(expected) is not None
                    else None
                ),
                "status": status,
                "severity": severity,
                "details": "Deterministic source-vs-target profile check. LLM receives this only if it fails or warns.",
            })
    return checks


def _parse_bigquery_model_name(table_name: str) -> tuple[str, str, str]:
    parts = [part for part in str(table_name or "").split(".") if part]
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    project_id = (os.getenv("GCP_PROJECT_ID") or "").strip()
    dataset = (os.getenv("GCP_BIGQUERY_DATASET") or os.getenv("BQ_DATASET") or "").strip()
    return project_id, dataset, parts[0] if parts else ""


def _build_bigquery_validation_payload(table_name: str) -> dict[str, Any] | None:
    project_id, dataset, table = _parse_bigquery_model_name(table_name)
    if not project_id or not dataset or not table:
        return None
    metadata = fetch_bigquery_table_metadata(
        project_id=project_id,
        dataset=dataset,
        table=table,
        location=os.getenv("GCP_BIGQUERY_LOCATION", "US"),
        env=os.environ.copy(),
    )
    if not metadata.get("success"):
        logger.warning(
            "[record-count-validation] BigQuery target profile unavailable for %s.%s.%s: %s",
            project_id,
            dataset,
            table,
            metadata.get("message") or metadata.get("error"),
        )
        return None
    row_count = metadata.get("row_count")
    return {
        "success": True,
        "target_type": "bigquery",
        "table_name": metadata.get("final_model") or f"{project_id}.{dataset}.{table}",
        "column_count": metadata.get("column_count"),
        "available_columns": metadata.get("available_columns") or [],
        "queried_numeric_columns": metadata.get("numeric_columns") or [],
        "profile": metadata.get("profile") or {},
        "actual": {"RowCount": row_count},
        "checks": [
            {
                "name": "Row count",
                "actual": row_count,
                "variance": None,
                "status": "INFO",
            }
        ],
        "metadata": metadata,
    }


def _aggregate_bigquery_validation_payload(table_names: list[str]) -> dict[str, Any] | None:
    payloads = [_build_bigquery_validation_payload(table_name) for table_name in table_names if table_name]
    payloads = [payload for payload in payloads if payload]
    if not payloads:
        return None
    if len(payloads) == 1:
        return payloads[0]

    total_rows = 0
    total_columns = 0
    available_columns: list[str] = []
    profile_columns: dict[str, Any] = {}
    numeric_columns: list[str] = []
    for payload in payloads:
        table_name = str(payload.get("table_name") or "")
        table_label = _parse_bigquery_model_name(table_name)[2] or table_name or "target"
        actual = payload.get("actual") or {}
        if isinstance(actual.get("RowCount"), (int, float)):
            total_rows += int(actual["RowCount"])
        if isinstance(payload.get("column_count"), (int, float)):
            total_columns += int(payload["column_count"])
        available_columns.extend(f"{table_label}.{column}" for column in payload.get("available_columns") or [])
        profile = payload.get("profile") or {}
        for column_name, column_profile in (profile.get("columns") or {}).items():
            aggregate_name = f"{table_label}.{column_name}"
            profile_columns[aggregate_name] = {**(column_profile or {}), "name": aggregate_name}
            if (column_profile or {}).get("numeric_count", 0) > 0:
                numeric_columns.append(aggregate_name)

    return {
        "success": True,
        "target_type": "bigquery",
        "table_name": ", ".join(str(payload.get("table_name") or "") for payload in payloads),
        "column_count": total_columns,
        "available_columns": available_columns,
        "queried_numeric_columns": numeric_columns,
        "profile": {
            "name": "BigQuery output tables",
            "row_count": total_rows,
            "column_count": total_columns,
            "columns": profile_columns,
            "numeric_columns": numeric_columns,
        },
        "actual": {"RowCount": total_rows},
        "checks": [
            {
                "name": "Row count",
                "actual": total_rows,
                "variance": None,
                "status": "INFO",
            }
        ],
        "metadata": {
            "row_count": total_rows,
            "column_count": total_columns,
            "available_columns": available_columns,
            "profile": {
                "name": "BigQuery output tables",
                "row_count": total_rows,
                "column_count": total_columns,
                "columns": profile_columns,
                "numeric_columns": numeric_columns,
            },
        },
    }


def _profile_columns(profile: dict[str, Any]) -> dict[str, Any]:
    columns = profile.get("columns") if isinstance(profile, dict) else {}
    return columns if isinstance(columns, dict) else {}


def _aggregate_profile_metric(columns: dict[str, Any], metric: str, names: list[str]) -> float | int | None:
    values: list[float] = []
    for name in names:
        value = (columns.get(name) or {}).get(metric)
        parsed = _to_float_or_none(value)
        if parsed is not None:
            values.append(parsed)
    if not values:
        return None
    if metric in {"not_null_count", "numeric_count"}:
        return int(sum(values))
    return sum(values)


def _build_dataset_profile_summary_checks(
    source_profile: dict[str, Any],
    target_profile: dict[str, Any],
    *,
    target_label: str,
) -> list[dict[str, Any]]:
    source_columns = _profile_columns(source_profile)
    target_columns = _profile_columns(target_profile)
    if not source_columns and not target_columns:
        return []
    if not source_columns:
        numeric_count = len(target_profile.get("numeric_columns") or [])
        return [
            {
                "name": "not_null_count",
                "expected": "Target profile validation",
                "actual": f"{target_label} profiled for {len(target_columns)} column(s)",
                "variance": None,
                "status": "PASS",
                "severity": "high",
                "details": "Target column completeness profile was calculated for the published model.",
            },
            {
                "name": "numeric_min_max",
                "expected": "Target numeric profile validation",
                "actual": f"{numeric_count} numeric target column(s)" if numeric_count else "No numeric target columns",
                "variance": None,
                "status": "PASS" if numeric_count else "NOT_APPLICABLE",
                "severity": "medium",
                "details": "Numeric min/max values were calculated from the published target model to validate transformed data quality.",
            },
            {
                "name": "numeric_sum_average",
                "expected": "Target numeric profile validation",
                "actual": f"{numeric_count} numeric target column(s)" if numeric_count else "No numeric target columns",
                "variance": None,
                "status": "PASS" if numeric_count else "NOT_APPLICABLE",
                "severity": "medium",
                "details": "Numeric sum and average values were calculated from the published target model to validate transformed data quality.",
            },
        ]
    if not target_columns:
        return []

    target_by_key = {_validation_match_key(name): name for name in target_columns}
    common_pairs = [
        (source_name, target_by_key[_validation_match_key(source_name)])
        for source_name in source_columns
        if _validation_match_key(source_name) in target_by_key
    ]
    if not common_pairs:
        return [
            {
                "name": "not_null_count",
                "expected": list(source_columns),
                "actual": list(target_columns),
                "variance": None,
                "status": "WARNING",
                "severity": "high",
                "details": "No matching source and target column names were found for profile comparison.",
            }
        ]

    source_common = [source for source, _ in common_pairs]
    target_common = [target for _, target in common_pairs]
    source_not_null = _aggregate_profile_metric(source_columns, "not_null_count", source_common)
    target_not_null = _aggregate_profile_metric(target_columns, "not_null_count", target_common)
    source_numeric = [name for name in source_common if (source_columns.get(name) or {}).get("numeric_count", 0)]
    target_numeric = [target for source, target in common_pairs if source in source_numeric]

    checks = [
        {
            "name": "not_null_count",
            "expected": source_not_null,
            "actual": target_not_null,
            "variance": (
                target_not_null - source_not_null
                if isinstance(source_not_null, int) and isinstance(target_not_null, int)
                else None
            ),
            "status": "PASS" if source_not_null == target_not_null else "WARNING",
            "severity": "high",
            "details": f"Compared aggregate not-null counts across {len(common_pairs)} matched column(s).",
        }
    ]

    if not source_numeric or not target_numeric:
        checks.extend([
            {
                "name": "numeric_min_max",
                "expected": "Not applicable",
                "actual": "No comparable numeric columns",
                "variance": None,
                "status": "NOT_APPLICABLE",
                "severity": "medium",
                "details": "No matching numeric columns were available for min/max validation.",
            },
            {
                "name": "numeric_sum_average",
                "expected": "Not applicable",
                "actual": "No comparable numeric columns",
                "variance": None,
                "status": "NOT_APPLICABLE",
                "severity": "medium",
                "details": "No matching numeric columns were available for sum/average validation.",
            },
        ])
        return checks

    metric_pairs = [
        ("numeric_min_max", ("min_value", "max_value")),
        ("numeric_sum_average", ("sum_value", "average_value")),
    ]
    for check_name, metrics in metric_pairs:
        comparisons = []
        warnings = 0
        for source_name, target_name in zip(source_numeric, target_numeric):
            for metric in metrics:
                expected = (source_columns.get(source_name) or {}).get(metric)
                actual = (target_columns.get(target_name) or {}).get(metric)
                if expected is None and actual is None:
                    continue
                matched = _within_numeric_tolerance(expected, actual)
                warnings += 0 if matched else 1
                comparisons.append(f"{source_name}.{metric}: {expected} -> {actual}")
        checks.append({
            "name": check_name,
            "expected": "; ".join(comparisons[:6]) or "Not applicable",
            "actual": f"{len(comparisons)} metric comparison(s)",
            "variance": warnings,
            "status": "PASS" if comparisons and warnings == 0 else "WARNING" if comparisons else "NOT_APPLICABLE",
            "severity": "medium",
            "details": "Compared numeric profile metrics across matched columns.",
        })
    return checks


class WorkflowJsonMaterializeRequest(BaseModel):
    workflow_json: dict[str, Any]
    workflow_name: Optional[str] = None
    source: Optional[str] = "cloud_json"


def extract_workflow_items(payload: Any) -> list[dict]:
    """Pull workflow items out of common Alteryx response wrappers."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("workflows", "flows", "assets", "packages", "data", "items", "results", "payload", "records"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_workflow_items(value)
            if nested:
                return nested

    return []


def _mask_token(token: Optional[str]) -> str:
    value = (token or "").strip()
    if not value:
        return "missing"
    return f"present len={len(value)} prefix={value[:12]}..."


def _find_batch_workflow(batch_id: str, workflow_id: str) -> dict[str, Any]:
    try:
        batch = load_batch(batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    for workflow in batch.get("workflows", []):
        if str(workflow.get("id")) == workflow_id:
            return workflow

    raise HTTPException(status_code=404, detail=f"Alteryx workflow not found in batch: {workflow_id}")


def _safe_filename(name: str, extension: str = ".yxzp") -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "alteryx_workflow").strip()).strip("._")
    if not base:
        base = "alteryx_workflow"
    if not base.lower().endswith((".yxmd", ".yxmc", ".yxwz", ".json", ".yxzp", ".zip")):
        base += extension
    return base


def _json_contains_workflow_graph(value: Any) -> bool:
    """Return True only for JSON that appears to include actual tool nodes."""
    if isinstance(value, dict):
        for key in ("nodes", "Nodes", "tools", "Tools", "workflowNodes", "workflow_nodes"):
            items = value.get(key)
            if isinstance(items, list) and any(isinstance(item, dict) for item in items):
                return True
            if isinstance(items, dict):
                nested_nodes = items.get("Node") or items.get("node") or items.get("items") or items.get("Items")
                if isinstance(nested_nodes, list) and any(isinstance(item, dict) for item in nested_nodes):
                    return True
                if isinstance(nested_nodes, dict):
                    return True
        if "Connections" in value and "Nodes" in value:
            return True
        for item in value.values():
            if _json_contains_workflow_graph(item):
                return True
    elif isinstance(value, list):
        return any(_json_contains_workflow_graph(item) for item in value)
    return False


def _workflow_artifact_headers(token: str, accept: str = "application/json") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
    }


def _artifact_from_json_payload(data: Any) -> tuple[str, bytes] | None:
    """Extract embedded XML/JSON/zip content if the workflow API returns it inline."""
    if isinstance(data, dict):
        for key in ("xml", "workflowXml", "workflow_xml", "fileContent", "definition", "workflowDefinition"):
            value = data.get(key)
            if isinstance(value, str) and ("<AlteryxDocument" in value or value.lstrip().startswith("<?xml")):
                return "cloud_workflow.yxmd", value.encode("utf-8")
            if isinstance(value, dict) and _json_contains_workflow_graph(value):
                return "cloud_workflow.json", json.dumps(value, ensure_ascii=False).encode("utf-8")

        content = data.get("content")
        if isinstance(content, dict):
            embedded = _artifact_from_json_payload(content)
            if embedded:
                return embedded
            if _json_contains_workflow_graph(content):
                return "cloud_workflow.json", json.dumps(content, ensure_ascii=False).encode("utf-8")
        elif isinstance(content, str):
            stripped = content.strip()
            if "<AlteryxDocument" in stripped or stripped.startswith("<?xml"):
                return "cloud_workflow.yxmd", stripped.encode("utf-8")
            if stripped.startswith("PK"):
                return "cloud_workflow.yxzp", stripped.encode("latin1", errors="ignore")
            if stripped.startswith("UEsDB"):
                try:
                    return "cloud_workflow.yxzp", base64.b64decode(stripped)
                except Exception:
                    pass
            try:
                parsed = json.loads(stripped)
                if _json_contains_workflow_graph(parsed):
                    return "cloud_workflow.json", json.dumps(parsed, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass

        for item in data.values():
            embedded = _artifact_from_json_payload(item)
            if embedded:
                return embedded
    elif isinstance(data, list):
        for item in data:
            embedded = _artifact_from_json_payload(item)
            if embedded:
                return embedded
    return None


def _safe_value_summary(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, str):
        compact = value.replace("\n", " ").replace("\r", " ")
        return f"str len={len(value)} value={compact[:160]}"
    if isinstance(value, dict):
        return f"dict keys={list(value.keys())[:12]}"
    if isinstance(value, list):
        return f"list len={len(value)}"
    return f"{type(value).__name__} value={str(value)[:160]}"


def _collect_artifact_paths(value: Any) -> list[str]:
    """Collect VFS/download-like path values from nested workflow metadata."""
    path_keys = {
        "vfsyxzppath",
        "primaryworkflowpath",
        "vfspath",
        "path",
        "downloadpath",
        "artifactpath",
        "filepath",
        "uri",
        "url",
        "location",
    }
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in path_keys or ("path" in normalized and "checksum" not in normalized):
                if isinstance(item, str) and item.strip():
                    paths.append(item.strip())
                elif isinstance(item, dict):
                    for nested_key in ("path", "url", "uri", "href", "value"):
                        nested = item.get(nested_key)
                        if isinstance(nested, str) and nested.strip():
                            paths.append(nested.strip())
                elif isinstance(item, list):
                    for nested in item:
                        if isinstance(nested, str) and nested.strip():
                            paths.append(nested.strip())
                        elif isinstance(nested, dict):
                            paths.extend(_collect_artifact_paths(nested))
            paths.extend(_collect_artifact_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(_collect_artifact_paths(item))
    return list(dict.fromkeys(paths))


def _vfs_path_candidates(path: str) -> list[tuple[str, dict[str, Any], str]]:
    raw_path = (path or "").strip()
    if not raw_path:
        return []

    encoded = quote(raw_path.lstrip("/"), safe="")
    candidates: list[tuple[str, dict[str, Any], str]] = []

    if raw_path.startswith("http://") or raw_path.startswith("https://"):
        candidates.append((raw_path, {}, "application/octet-stream,application/json"))
    elif raw_path.startswith("/"):
        candidates.append((f"{ALTERYX_BASE_URL}{raw_path}", {}, "application/octet-stream,application/json"))

    candidates.extend([
        (f"{ALTERYX_BASE_URL}/svc-vfs/api/v1/files/{encoded}", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-vfs/api/v1/files", {"path": raw_path}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-vfs/api/v1/download", {"path": raw_path}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-vfs/api/v1/contents/{encoded}", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/vfs/api/v1/files/{encoded}", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/vfs/api/v1/files", {"path": raw_path}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/vfs/v1/files/{encoded}", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/vfs/v1/files", {"path": raw_path}, "application/octet-stream,application/json"),
    ])
    return candidates


def _download_cloud_workflow_artifact(session: AlteryxSession, workflow_id: str, workspace_id: Optional[str] = None) -> tuple[str, bytes, str]:
    """Try known Alteryx Cloud/Server package endpoints and return a workflow artifact."""
    token = ensure_fresh_token(session)
    workspace_params = {"workspaceId": workspace_id} if workspace_id else {}
    candidates: list[tuple[str, dict[str, Any], str]] = [
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/package", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/download", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/export", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/definition", {}, "application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/json", {}, "application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/content", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/versions/latest/package", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}/versions/latest/content", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}", {"includeXml": "true", "includeDefinition": "true"}, "application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}", {"includeJson": "true", "includeGraph": "true"}, "application/json"),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows/{workflow_id}", {"format": "json"}, "application/json"),
        (f"{ALTERYX_BASE_URL}/v3/workflows/{workflow_id}/package", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/v1/workflows/{workflow_id}/package", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/admin/v1/{workflow_id}/package", {}, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/v4/workflows/{workflow_id}/package", workspace_params, "application/octet-stream,application/json"),
        (f"{ALTERYX_BASE_URL}/api/v1/workflows/{workflow_id}/package", workspace_params, "application/octet-stream,application/json"),
    ]

    last_error = ""
    for url, params, accept in candidates:
        try:
            print(f"  Trying workflow artifact endpoint: {url}")
            resp = requests.get(
                url,
                headers=_workflow_artifact_headers(token, accept),
                params=params,
                timeout=30,
            )
            if resp.status_code == 401 and session.refresh_token:
                new_access, new_refresh = ensure_fresh_token(session), session.refresh_token
                token = new_access
                if new_refresh:
                    session.refresh_token = new_refresh
                resp = requests.get(
                    url,
                    headers=_workflow_artifact_headers(token, accept),
                    params=params,
                    timeout=30,
                )
            if resp.status_code >= 400:
                last_error = f"{resp.status_code} {resp.text[:200]}"
                print(f"  Failed artifact endpoint: {last_error}")
                continue

            content_type = resp.headers.get("content-type", "").lower()
            content = resp.content or b""
            text = resp.text if "json" in content_type or content[:1] in (b"{", b"[") else ""

            if content[:2] == b"PK":
                return "cloud_workflow.yxzp", content, url
            if content.lstrip().startswith(b"<?xml") or b"<AlteryxDocument" in content[:5000]:
                return "cloud_workflow.yxmd", content, url
            if text:
                data = resp.json()
                embedded = _artifact_from_json_payload(data)
                if embedded:
                    artifact_name, artifact_bytes = embedded
                    return artifact_name, artifact_bytes, url
                if _json_contains_workflow_graph(data):
                    return "cloud_workflow.json", json.dumps(data, ensure_ascii=False).encode("utf-8"), url

                if isinstance(data, dict):
                    print(
                        "  Artifact pointer summary | "
                        f"vfsYxzpPath={_safe_value_summary(data.get('vfsYxzpPath'))} | "
                        f"primaryWorkflowPath={_safe_value_summary(data.get('primaryWorkflowPath'))} | "
                        f"content={_safe_value_summary(data.get('content'))}"
                    )
                vfs_paths = _collect_artifact_paths(data)
                if vfs_paths:
                    print(f"  Collected {len(vfs_paths)} possible artifact path(s): {[path[:120] for path in vfs_paths[:6]]}")

                for vfs_path in vfs_paths:
                    print(f"  Following workflow VFS path: {vfs_path}")
                    for vfs_url, vfs_params, vfs_accept in _vfs_path_candidates(vfs_path):
                        try:
                            vfs_resp = requests.get(
                                vfs_url,
                                headers=_workflow_artifact_headers(token, vfs_accept),
                                params=vfs_params,
                                timeout=45,
                            )
                            if vfs_resp.status_code >= 400:
                                last_error = f"{vfs_resp.status_code} {vfs_resp.text[:200]}"
                                print(f"  Failed VFS endpoint: {vfs_url} | {last_error}")
                                continue

                            vfs_content = vfs_resp.content or b""
                            vfs_type = vfs_resp.headers.get("content-type", "").lower()
                            if vfs_content[:2] == b"PK":
                                return "cloud_workflow.yxzp", vfs_content, vfs_url
                            if vfs_content.lstrip().startswith(b"<?xml") or b"<AlteryxDocument" in vfs_content[:5000]:
                                return "cloud_workflow.yxmd", vfs_content, vfs_url
                            if "json" in vfs_type or vfs_content[:1] in (b"{", b"["):
                                vfs_json = vfs_resp.json()
                                embedded = _artifact_from_json_payload(vfs_json)
                                if embedded:
                                    artifact_name, artifact_bytes = embedded
                                    return artifact_name, artifact_bytes, vfs_url
                                if _json_contains_workflow_graph(vfs_json):
                                    return "cloud_workflow.json", json.dumps(vfs_json, ensure_ascii=False).encode("utf-8"), vfs_url
                            last_error = f"VFS response was not zip/xml/workflow-json content-type={vfs_type}, bytes={len(vfs_content)}"
                            print(f"  {last_error}")
                        except Exception as vfs_exc:
                            last_error = str(vfs_exc)
                            print(f"  Exception while following VFS path: {last_error}")

                last_error = f"JSON response did not contain workflow XML or tool graph. Keys: {list(data.keys()) if isinstance(data, dict) else 'array'}"
                print(f"  {last_error}")
                continue

            last_error = f"Unsupported package response content-type={content_type}, bytes={len(content)}"
            print(f"  {last_error}")
        except Exception as exc:
            last_error = str(exc)
            print(f"  Exception while downloading package: {last_error}")

    raise ValueError(
        "Unable to download the full Alteryx workflow package/XML/JSON definition from Cloud. "
        f"Tried {len(candidates)} candidate endpoints. Last error: {last_error}"
    )


# ── Validate auth ─────────────────────────────────────────────────────────────

@router.post("/validate-auth", response_model=AlteryxAuthResponse)
def validate_alteryx_auth(config: AlteryxAuthRequest):
    """
    Validates Alteryx credentials and resolves workspace name → ID.

    Token priority:
      1. access_token from request body
      2. ALTERYX_ACCESS_TOKEN from .env
    Works with ACCESS_TOKEN + REFRESH_TOKEN (no CLIENT_SECRET needed)
    """
    workspace_name = config.workspace_name.strip() or os.getenv("ALTERYX_WORKSPACE_NAME", "")
    if not workspace_name:
        raise HTTPException(
            status_code=400, 
            detail="Workspace name is required."
        )

    access_token = (config.access_token or "").strip()
    refresh_token = (config.refresh_token or "").strip()
    print(
        "🔎 /api/alteryx/validate-auth payload tokens | "
        f"access={_mask_token(access_token)} | refresh={_mask_token(refresh_token)}"
    )
    if False and (not access_token or not refresh_token):
        raise HTTPException(
            status_code=400,
            detail=(
                "Access Token and Refresh Token are required on the Connect page. "
                "Generate a fresh OAuth API token pair in Alteryx One and paste both tokens. "
                "The app will persist rotated tokens after the first successful connection."
            ),
        )

    if access_token and refresh_token and looks_like_refresh_token(access_token) and looks_like_access_token(refresh_token):
        print("🔁 Detected swapped Alteryx tokens; using refresh field as access token and access field as refresh token.")
        access_token, refresh_token = refresh_token, access_token
    elif access_token and not looks_like_access_token(access_token):
        raise HTTPException(
            status_code=400,
            detail="The Access Token field does not look like an Alteryx access token. Please paste the access token, not the refresh token.",
        )
    elif refresh_token and not looks_like_refresh_token(refresh_token):
        raise HTTPException(
            status_code=400,
            detail="The Refresh Token field does not look like an Alteryx refresh token. Please paste the refresh token, not the access token.",
        )

    try:
        print(f"\n🔵 Validating Alteryx auth for workspace: {workspace_name}")
        if access_token and is_token_expired(access_token) and not refresh_token:
            raise ValueError(
                f"The provided Alteryx access token is expired ({token_expiry_summary(access_token)}) "
                "and no refresh token was provided."
            )
        session = create_alteryx_session(
            access_token=access_token,
            refresh_token=refresh_token,
            workspace_name=workspace_name,
            username=config.username,
        )
        print(f"✅ Auth validation successful!")
    except ValueError as e:
        error_msg = str(e)
        print(f"❌ Validation error: {error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)
    except requests.HTTPError as e:
        error_msg = str(e)
        status_code = e.response.status_code if e.response is not None else 401
        print(f"❌ HTTP {status_code}: {error_msg}")
        raise HTTPException(status_code=status_code, detail=error_msg)
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        print(f"❌ {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)

    return AlteryxAuthResponse(
        status="authenticated",
        workspace_name=session.workspace_name,
        workspace_id=str(session.workspace_id),
        access_token=session.access_token,
        refresh_token=session.refresh_token,
    )


@router.post("/bulk-upload", response_model=AlteryxBulkUploadResponse)
async def bulk_upload_alteryx_workflows(
    files: list[UploadFile] = File(...),
):
    """
    Bulk ingest Alteryx workflow artifacts.

    Supports:
    - .yxmd workflow files
    - .yxmc macro files
    - .yxwz analytic app files
    - .yxzp packaged workflows
    - .zip archives containing any of the above
    """
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one Alteryx workflow/package file.")

    uploaded: list[tuple[str, bytes]] = []
    total_bytes = 0

    for upload in files:
        content = await upload.read()
        total_bytes += len(content)
        if total_bytes > MAX_BULK_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Upload is too large. Split migration packages into batches under 250 MB.",
            )
        uploaded.append((upload.filename or "uploaded-file", content))

    result = ingest_uploaded_files(uploaded)
    return AlteryxBulkUploadResponse(
        batch_id=result["batch_id"],
        summary=result["summary"],
        workflows=_workflows_for_client(result["workflows"]),
        rejected=result["rejected"],
        workspace_name=(os.getenv("ALTERYX_WORKSPACE_NAME", "") or "").strip() or None,
    )


@router.post("/workflows/materialize")
def materialize_cloud_workflow(config: CloudWorkflowMaterializeRequest):
    """
    Download a Cloud workflow artifact, ingest it as a normal Alteryx batch,
    and return the parsed workflow so the existing Summary/Scripts/Publish flow can be reused.
    """
    workflow_id = (config.workflow_id or "").strip()
    if not workflow_id:
        raise HTTPException(status_code=400, detail="workflow_id is required.")

    workspace_name = (config.workspace_name or os.getenv("ALTERYX_WORKSPACE_NAME", "")).strip()
    access_token = os.getenv("ALTERYX_ACCESS_TOKEN", "")
    refresh_token = os.getenv("ALTERYX_REFRESH_TOKEN")
    session = AlteryxSession(
        access_token=access_token,
        refresh_token=refresh_token,
        workspace_name=workspace_name or None,
        workspace_id=config.workspace_id or os.getenv("ALTERYX_WORKSPACE_ID", None),
    )

    try:
        artifact_name, artifact_bytes, source_endpoint = _download_cloud_workflow_artifact(
            session=session,
            workflow_id=workflow_id,
            workspace_id=config.workspace_id or session.workspace_id,
        )
        artifact_name = _safe_filename(config.workflow_name or artifact_name, os.path.splitext(artifact_name)[1] or ".yxzp")
        result = ingest_uploaded_files([(artifact_name, artifact_bytes)])
        workflows = result.get("workflows", [])
        if not workflows:
            rejected = result.get("rejected", [])
            reason = rejected[0].get("reason") if rejected else "Downloaded artifact did not contain a parseable workflow."
            raise ValueError(reason)

        persist_alteryx_tokens(session.access_token, session.refresh_token)
        return {
            "success": True,
            "source": "cloud_download",
            "source_endpoint": source_endpoint,
            "artifact_name": artifact_name,
            "batch_id": result["batch_id"],
            "workflow": _workflow_for_client(workflows[0]),
            "workflows": _workflows_for_client(workflows),
            "summary": result.get("summary", {}),
            "rejected": result.get("rejected", []),
        }
    except Exception as exc:
        print(f"❌ Cloud workflow materialization failed: {exc}")
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/workflows/materialize-json")
def materialize_workflow_json(config: WorkflowJsonMaterializeRequest):
    """
    Ingest a full Alteryx workflow JSON definition as a normal migration batch.

    This lets Cloud/API JSON reuse the existing Bulk Upload pipeline:
    Summary -> M Query -> BRD -> Power BI Publish -> Validation.
    """
    workflow_json = config.workflow_json or {}
    if not workflow_json:
        raise HTTPException(status_code=400, detail="workflow_json is required.")

    artifact_name = _safe_filename(config.workflow_name or "alteryx_workflow", ".json")
    try:
        artifact_bytes = json.dumps(workflow_json, ensure_ascii=False).encode("utf-8")
        result = ingest_uploaded_files([(artifact_name, artifact_bytes)])
        workflows = result.get("workflows", [])
        if not workflows:
            rejected = result.get("rejected", [])
            reason = rejected[0].get("reason") if rejected else "Workflow JSON did not contain a parseable workflow."
            raise ValueError(reason)

        parsed_workflow = workflows[0]
        if not parsed_workflow.get("workflowNodes"):
            raise ValueError(
                "The JSON payload was received, but it contains only workflow metadata. "
                "A full workflow JSON definition with tool nodes/connections is required for M Query conversion."
            )

        return {
            "success": True,
            "source": config.source or "cloud_json",
            "artifact_name": artifact_name,
            "batch_id": result["batch_id"],
            "workflow": _workflow_for_client(parsed_workflow),
            "workflows": _workflows_for_client(workflows),
            "summary": result.get("summary", {}),
            "rejected": result.get("rejected", []),
        }
    except Exception as exc:
        print(f"❌ Workflow JSON materialization failed: {exc}")
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/batches/{batch_id}")
def get_alteryx_upload_batch(batch_id: str):
    try:
        return load_batch(batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/batches/{batch_id}/workflows")
def get_alteryx_upload_batch_workflows(batch_id: str):
    try:
        batch = load_batch(batch_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "success": True,
        "batch_id": batch_id,
        "summary": batch.get("summary", {}),
        "total": len(batch.get("workflows", [])),
        "workflows": _workflows_for_client(batch.get("workflows", [])),
    }


@router.get("/batches/{batch_id}/workflows/{workflow_id}")
def get_alteryx_upload_batch_workflow(batch_id: str, workflow_id: str):
    return {
        "success": True,
        "batch_id": batch_id,
        "workflow": _workflow_for_client(_find_batch_workflow(batch_id, workflow_id)),
    }


@router.get("/batches/{batch_id}/workflows/{workflow_id}/analysis")
def get_alteryx_workflow_analysis(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    m_query = generate_m_query(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    return {
        "success": True,
        "workflow": workflow,
        "summary": generate_executive_summary(workflow),
        "diagram": generate_workflow_diagram(workflow),
        "mquery": m_query,
        "validation": validate_migration(workflow),
    }


@router.get("/batches/{batch_id}/workflows/{workflow_id}/mquery")
def get_alteryx_workflow_mquery(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return {"success": True, **generate_m_query(workflow, sharepoint_url=sharepoint_url, file_name=file_name)}


@router.get("/batches/{batch_id}/workflows/{workflow_id}/transform-plan")
def get_alteryx_workflow_transform_plan(batch_id: str, workflow_id: str):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return build_transform_plan(workflow)


@router.get("/batches/{batch_id}/workflows/{workflow_id}/dbt")
def get_alteryx_workflow_dbt_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)


@router.get("/batches/{batch_id}/workflows/{workflow_id}/dbt.zip")
def download_alteryx_workflow_dbt_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    project = generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        root = project.get("project_name") or "alteryx_dbt_project"
        for path, content in (project.get("files") or {}).items():
            archive.writestr(f"{root}/{path}", str(content))
    buffer.seek(0)
    safe_project_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project.get("project_name") or "alteryx_dbt_project"))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_project_name}_dbt_project.zip"'},
    )


def _zip_project(project: dict[str, Any], default_root: str, filename_suffix: str) -> Response:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        root = project.get("project_name") or default_root
        for path, content in (project.get("files") or {}).items():
            archive.writestr(f"{root}/{path}", str(content))
    buffer.seek(0)
    safe_project_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project.get("project_name") or default_root))
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_project_name}_{filename_suffix}.zip"'},
    )


def _allow_partial_transform_publish() -> bool:
    return str(os.getenv("ALLOW_PARTIAL_TRANSFORM_PUBLISH", "")).strip().lower() in {"1", "true", "yes", "y"}


def _enforce_transform_parity_gate() -> bool:
    return str(os.getenv("ENFORCE_TRANSFORM_PARITY_GATE", "")).strip().lower() in {"1", "true", "yes", "y"}


def _assert_transform_publishable(project: dict[str, Any], target: str) -> None:
    if _allow_partial_transform_publish() or not _enforce_transform_parity_gate():
        return
    plan = project.get("transform_plan") or {}
    detail = transform_publish_blocker_detail(plan, target)
    if detail:
        raise HTTPException(status_code=400, detail=detail)


def _transform_publish_warning(project: dict[str, Any], target: str) -> dict[str, Any] | None:
    return transform_publish_blocker_detail(project.get("transform_plan") or {}, target)


@router.get("/batches/{batch_id}/workflows/{workflow_id}/dataform")
def get_alteryx_workflow_dataform_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return generate_dataform_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)


@router.get("/batches/{batch_id}/workflows/{workflow_id}/dataform.zip")
def download_alteryx_workflow_dataform_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    project = generate_dataform_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    return _zip_project(project, "alteryx_dataform_project", "dataform_project")


@router.post("/batches/{batch_id}/workflows/{workflow_id}/dataform/publish-bigquery")
def publish_alteryx_workflow_dataform_to_bigquery(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    if workflow.get("isMacroDefinition"):
        raise HTTPException(
            status_code=400,
            detail="Select the parent .yxmd workflow for Publish Dataform to BigQuery.",
        )
    if _workflow_has_python_tools(workflow):
        raise HTTPException(
            status_code=400,
            detail=(
                "This workflow contains Alteryx Python tools. Use Python to BigQuery instead of Dataform to BigQuery; "
                "Dataform expects SQL-convertible logic and pre-landed source tables."
            ),
        )
    project = generate_dataform_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    _assert_transform_publishable(project, "Dataform to BigQuery")
    try:
        result = publish_dataform_project_to_bigquery(project)
        result["transformation_coverage"] = project.get("transformation_coverage") or {}
        result["transform_plan"] = project.get("transform_plan") or {}
        result["transform_publish_warning"] = _transform_publish_warning(project, "Dataform to BigQuery")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to publish generated Dataform project to BigQuery")
        raise HTTPException(status_code=500, detail=f"Failed to publish Dataform project to BigQuery: {exc}") from exc


@router.post("/batches/{batch_id}/workflows/{workflow_id}/dataform/publish-repository")
def publish_alteryx_workflow_dataform_to_repository(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    if workflow.get("isMacroDefinition"):
        raise HTTPException(
            status_code=400,
            detail="Select the parent .yxmd workflow for Publish Dataform to GCP Repo.",
        )
    if _workflow_has_python_tools(workflow):
        raise HTTPException(
            status_code=400,
            detail=(
                "This workflow contains Alteryx Python tools. Use Python to BigQuery instead of Dataform repository publish; "
                "Dataform expects SQL-convertible logic and cannot execute Python tool logic."
            ),
        )
    project = generate_dataform_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    _assert_transform_publishable(project, "Dataform repository")
    try:
        result = publish_dataform_project_to_repository(project)
        result["transformation_coverage"] = project.get("transformation_coverage") or {}
        result["transform_plan"] = project.get("transform_plan") or {}
        result["transform_publish_warning"] = _transform_publish_warning(project, "Dataform repository")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to publish generated Dataform project to GCP Dataform repository")
        raise HTTPException(status_code=500, detail=f"Failed to publish Dataform project to repository: {exc}") from exc


@router.get("/batches/{batch_id}/workflows/{workflow_id}/python")
def get_alteryx_workflow_python_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return generate_python_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)


@router.get("/batches/{batch_id}/workflows/{workflow_id}/python.zip")
def download_alteryx_workflow_python_project(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    project = generate_python_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    return _zip_project(project, "alteryx_python_project", "python_project")


@router.post("/batches/{batch_id}/workflows/{workflow_id}/python/publish-bigquery")
def publish_alteryx_workflow_python_to_bigquery(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
    async_publish: bool = Query(default=False, alias="async"),
):
    def _run_publish() -> dict[str, Any]:
        workflow = _find_batch_workflow(batch_id, workflow_id)
        if workflow.get("isMacroDefinition"):
            raise HTTPException(
                status_code=400,
                detail="Select the parent .yxmd workflow for Publish Python to BigQuery.",
            )
        analysis_started = time.time()
        project = generate_python_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
        analysis_duration = round(time.time() - analysis_started, 2)
        _assert_transform_publishable(project, "Python to BigQuery")
        try:
            result = publish_python_project_to_bigquery(project)
            result["transformation_coverage"] = project.get("transformation_coverage") or {}
            result["transform_plan"] = project.get("transform_plan") or {}
            result["transform_publish_warning"] = _transform_publish_warning(project, "Python to BigQuery")
            result["analysis_duration_seconds"] = analysis_duration
            timings = result.setdefault("timings", {})
            timings["analysis_seconds"] = analysis_duration
            return result
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to publish generated Python pipeline to BigQuery")
            raise HTTPException(status_code=500, detail=f"Failed to publish Python pipeline to BigQuery: {exc}") from exc

    if async_publish:
        job = _start_publish_job("python_bigquery", _run_publish)
        return {**job, "success": True, "status": "accepted"}
    return _run_publish()


@router.post("/batches/{batch_id}/workflows/{workflow_id}/dbt/publish-bigquery")
def publish_alteryx_workflow_dbt_to_bigquery(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    if workflow.get("isMacroDefinition"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Select the parent .yxmd workflow for Publish dbt to BigQuery. "
                "A .yxmc macro definition is an internal reusable component and does not contain the external source table mapping needed for dbt publish."
            ),
        )
    if _workflow_has_python_tools(workflow):
        raise HTTPException(
            status_code=400,
            detail=(
                "This workflow contains Alteryx Python tools. Use Python to BigQuery instead of dbt to BigQuery; "
                "dbt expects pre-landed source tables and cannot execute Python tool logic."
            ),
        )
    project = generate_dbt_project(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    _assert_transform_publishable(project, "dbt to BigQuery")
    try:
        result = publish_dbt_project_to_bigquery(project)
        result["transformation_coverage"] = project.get("transformation_coverage") or {}
        result["transform_plan"] = project.get("transform_plan") or {}
        result["transform_publish_warning"] = _transform_publish_warning(project, "dbt to BigQuery")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to publish generated dbt project to BigQuery")
        raise HTTPException(status_code=500, detail=f"Failed to publish dbt project to BigQuery: {exc}") from exc
    return result


@router.get("/batches/{batch_id}/workflows/{workflow_id}/diagram")
def get_alteryx_workflow_diagram(batch_id: str, workflow_id: str):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return {"success": True, **generate_workflow_diagram(workflow)}


@router.get("/batches/{batch_id}/workflows/{workflow_id}/brd", response_class=HTMLResponse)
def get_alteryx_workflow_brd(
    batch_id: str,
    workflow_id: str,
    sharepoint_url: str = Query(default=""),
    file_name: str = Query(default=""),
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    m_query = generate_m_query(workflow, sharepoint_url=sharepoint_url, file_name=file_name)
    return generate_brd_html(workflow, m_query.get("combined_mquery", ""))


@router.post("/batches/{batch_id}/workflows/{workflow_id}/validation")
def post_alteryx_workflow_validation(batch_id: str, workflow_id: str, publish_result: dict[str, Any] | None = None):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    return validate_migration(workflow, publish_result=publish_result or {})


@router.post("/batches/{batch_id}/workflows/{workflow_id}/record-count-validation")
async def post_alteryx_record_count_validation(
    batch_id: str,
    workflow_id: str,
    request: AlteryxRecordCountValidationRequest,
):
    workflow = _find_batch_workflow(batch_id, workflow_id)
    alteryx_count = _resolve_alteryx_output_row_count(
        workflow,
        final_table_name=request.table_name,
        fallback=request.expected_row_count,
    )
    expected = alteryx_count.get("row_count")
    source_profile = alteryx_count.get("profile") or {}
    validation_numeric_columns = request.numeric_columns or (source_profile.get("numeric_columns") or [])[:20]

    powerbi_validation = None
    bigquery_validation = None
    if request.dataset_id and request.table_name:
        from app.api.v1.endpoints.migration import (
            PowerBiValidationRequest,
            validate_powerbi_dataset_endpoint,
        )
        import requests as _requests

        # ── Wait for the Power BI dataset refresh to complete ────────────────
        # The publish endpoint triggers a refresh but does not wait for data load.
        # Running executeQueries before the refresh finishes returns 0 rows.
        # Poll the refresh history endpoint (max 90s) before querying.
        workspace_id_for_poll = request.workspace_id or ""
        _refresh_url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id_for_poll}"
            f"/datasets/{request.dataset_id}/refreshes?$top=1"
        )
        try:
            from app.services.powerbi_publisher import _acquire_sp_token
            _sp_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
            _auth_headers = {
                "Authorization": f"Bearer {_sp_token}",
                "Content-Type": "application/json",
            }
            _refresh_poll_start = asyncio.get_event_loop().time()
            _refresh_timeout = 90  # seconds
            _refresh_interval = 6  # seconds between polls
            _refresh_done = False
            while True:
                _elapsed = asyncio.get_event_loop().time() - _refresh_poll_start
                if _elapsed > _refresh_timeout:
                    logger.warning(
                        "[record-count-validation] Refresh did not complete within %ds — proceeding anyway.",
                        _refresh_timeout,
                    )
                    break
                _rr = _requests.get(_refresh_url, headers=_auth_headers, timeout=20)
                if _rr.ok:
                    _latest = (_rr.json().get("value") or [{}])[0]
                    _status = str(_latest.get("status") or "Unknown")
                    logger.info(
                        "[record-count-validation] Refresh poll: status=%s elapsed=%.0fs",
                        _status, _elapsed,
                    )
                    if _status == "Completed":
                        logger.info(
                            "[record-count-validation] Refresh completed after %.0fs — querying row count.",
                            _elapsed,
                        )
                        _refresh_done = True
                        break
                    elif _status in ("Failed", "Disabled"):
                        logger.warning(
                            "[record-count-validation] Refresh status=%s — proceeding without waiting.", _status
                        )
                        break
                # Still running/unknown — wait before next poll (moved OUTSIDE if block)
                await asyncio.sleep(_refresh_interval)
        except Exception as _poll_exc:
            logger.warning("[record-count-validation] Refresh poll failed: %s — proceeding.", _poll_exc)

        # ── Now run executeQueries for the actual Power BI row count ─────────
        powerbi_validation = await validate_powerbi_dataset_endpoint(
            PowerBiValidationRequest(
                dataset_id=request.dataset_id,
                table_name=request.table_name,
                workspace_id=workspace_id_for_poll,
                numeric_columns=validation_numeric_columns,
                expected_row_count=expected,
            )
        )
    elif request.table_name or request.target_tables:
        target_tables = request.target_tables or [request.table_name]
        bigquery_validation = aggregate_bigquery_validation_payload(target_tables)

    target_validation = powerbi_validation or bigquery_validation
    return build_validation_response(
        workflow_id=workflow_id,
        workflow_name=workflow.get("name"),
        requested_table_name=request.table_name,
        alteryx_count=alteryx_count,
        target_validation=target_validation,
        powerbi_validation=powerbi_validation,
        bigquery_validation=bigquery_validation,
        numeric_columns=validation_numeric_columns,
    )


# ── Fetch workflows ───────────────────────────────────────────────────────────

@router.get("/workflows")
def get_alteryx_workflows(
    workspace_id: Optional[str] = None,
    workspace_name: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    x_alteryx_refresh_token: Optional[str] = Header(None, alias="X-Alteryx-Refresh-Token"),
    x_alteryx_username: Optional[str] = Header(None, alias="X-Alteryx-Username"),
    response: Response = None,
):
    """
    Fetch all workflows for a workspace.

    Token priority:
      1. Authorization: Bearer <token> header
      2. ALTERYX_ACCESS_TOKEN from .env
    Works with ACCESS_TOKEN + REFRESH_TOKEN (no CLIENT_SECRET needed)
    
    Endpoint discovery:
      - Tries custom workspace domain first (/api/v1/workflows on custom URL)
      - Falls back to main domain endpoints
      - Iterates through Designer Cloud API patterns
    """
    # Resolve workspace_id (from param or .env)
    workspace_id = workspace_id or os.getenv("ALTERYX_WORKSPACE_ID", "")
    workspace_name = workspace_name or os.getenv("ALTERYX_WORKSPACE_NAME", "")
    
    if not workspace_id and not workspace_name:
        raise HTTPException(status_code=400, detail="Missing workspace_id or workspace_name parameter.")

    # Resolve access token
    if authorization and authorization.startswith("Bearer "):
        access_token = authorization.split(" ", 1)[1]
    else:
        access_token = os.getenv("ALTERYX_ACCESS_TOKEN", "")

    # Resolve refresh token
    refresh_token = x_alteryx_refresh_token or os.getenv("ALTERYX_REFRESH_TOKEN")

    if not access_token and not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="No access token available. Set ALTERYX_ACCESS_TOKEN in .env or pass Authorization header.",
        )

    # Create session
    session = AlteryxSession(
        access_token=access_token,
        refresh_token=refresh_token,
    )

    # If workspace_name provided, resolve it to get custom_url
    custom_url = None
    if workspace_name:
        try:
            from app.utils.alteryx_workspace_utils import get_workspace_id_by_name
            get_workspace_id_by_name(session, workspace_name)
            custom_url = session.custom_url
            workspace_id = session.workspace_id
            print(f"✅ Resolved workspace '{workspace_name}' → ID {workspace_id}, custom_url: {custom_url}")
        except Exception as e:
            print(f"⚠️  Could not resolve workspace name: {e}")

    # Build endpoint candidates - prefer platform APIs over custom workspace domains.
    endpoint_candidates = []
    workspace_params = {"workspaceId": workspace_id} if workspace_id else {}

    # Sam accelerator working endpoint for Designer Cloud workflows.
    endpoint_candidates.extend([
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", {}),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", {"limit": 100}),
    ])
    if workspace_id:
        endpoint_candidates.append(
            (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", {"workspaceId": workspace_id})
        )

    # Known-good API for this tenant. If workflow APIs are unavailable, this
    # still returns the accessible Alteryx workspace list for the selection page.
    endpoint_candidates.append((f"{ALTERYX_BASE_URL}/v4/workspaces", {}))

    # First priority: platform flow/asset APIs tied to the workspace.
    if workspace_id:
        endpoint_candidates.extend([
            (f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/flows", {}),
            (f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/assets", {}),
            (f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/packages", {}),
        ])
        endpoint_candidates.extend([
            (f"{ALTERYX_BASE_URL}/v4/flows", workspace_params),
            (f"{ALTERYX_BASE_URL}/v4/assets", workspace_params),
            (f"{ALTERYX_BASE_URL}/v4/packages", workspace_params),
        ])

    # Second priority: Main domain Designer Cloud API
    endpoint_candidates.extend([
        (f"{ALTERYX_BASE_URL}/api/v1/workflows", workspace_params),
        (f"{ALTERYX_BASE_URL}/api/v1/workflows/list", workspace_params),
        (f"{ALTERYX_BASE_URL}/designer/api/v1/workflows", workspace_params),
        (f"{ALTERYX_BASE_URL}/designer/v1/workflows", workspace_params),
        (f"{ALTERYX_BASE_URL}/api/designer/workflows", workspace_params),
    ])

    # Third priority: workspace-specific and generic workflow endpoints.
    if workspace_id:
        endpoint_candidates.extend([
            (f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/workflows", {}),
            (f"{ALTERYX_BASE_URL}/api/v1/workspaces/{workspace_id}/workflows", {}),
            (f"{ALTERYX_BASE_URL}/api/workspaces/{workspace_id}/workflows", {}),
        ])

    endpoint_candidates.extend([
        (f"{ALTERYX_BASE_URL}/api/v2/workflows", workspace_params),
        (f"{ALTERYX_BASE_URL}/v1/workflows", workspace_params),
        (f"{ALTERYX_BASE_URL}/workflows", workspace_params),
    ])

    data = None
    last_error = None

    print(f"\n🔵 Fetching workflows | workspace_id: {workspace_id} | custom_url: {custom_url}")
    for endpoint, params in endpoint_candidates:
        print(f"  Trying: {endpoint}")
        try:
            data = _get_with_refresh(endpoint, session, params=params)
            print(f"  ✅ Success! Workflows endpoint found: {endpoint}")
            break
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 500
            last_error = (code, str(e))
            if code in {401, 403, 404}:
                print(f"  ⚠️  {code} - endpoint not available, trying next...")
                continue
            else:
                print(f"  ⚠️  {code} - {e}")
                continue
        except ValueError as e:
            last_error = (500, str(e))
            print(f"  ⚠️  Invalid response format, trying next...")
            continue

    if data is None:
        code, detail = last_error or (500, "Unable to fetch workflows.")
        error_msg = (
            f"❌ Failed to fetch workflows. No compatible endpoint found. "
            f"Tried {len(endpoint_candidates)} endpoints."
        )
        print(f"\n{error_msg}")
        raise HTTPException(
            status_code=code or 500,
            detail=error_msg,
        )

    # Normalise response shape (list OR {"data": [...]} OR {"workflows": [...]})
    raw_workflows = extract_workflow_items(data)

    print(f"  📊 Found {len(raw_workflows)} workflow(s)")

    workflows = []
    for wf in raw_workflows:
        workflow_id = (
            wf.get("id")
            or wf.get("workflowId")
            or wf.get("assetId")
            or wf.get("packageId")
            or wf.get("flowId")
        )
        if not workflow_id:
            continue

        # Handle multiple field name variations
        workflow = AlteryxWorkflow(
            id=str(workflow_id),
            name=(
                wf.get("name")
                or wf.get("workspaceName")
                or wf.get("packageName")
                or wf.get("workflowName")
                or wf.get("assetName")
                or wf.get("flowName")
                or wf.get("fileName")
                or wf.get("title")
                or "Unnamed Workflow"
            ),
            lastModifiedDate=(
                wf.get("dateModified") or 
                wf.get("lastModifiedDate") or 
                wf.get("updated") or
                wf.get("modifiedAt") or
                wf.get("updatedAt") or
                wf.get("dateCreated")
            ),
            runCount=wf.get("runCount") or wf.get("runs"),
            credentialType=wf.get("credentialType") or wf.get("type"),
            workerTag=wf.get("workerTag") or wf.get("tag"),
        )
        workflows.append(workflow)

    # Send refreshed tokens back to frontend if they changed during the request
    if response is not None and session.access_token and session.access_token != access_token:
        response.headers["X-Alteryx-Access-Token"] = session.access_token
    if response is not None and session.refresh_token and session.refresh_token != refresh_token:
        response.headers["X-Alteryx-Refresh-Token"] = session.refresh_token
    persist_alteryx_tokens(session.access_token, session.refresh_token)

    return {
        "workspace_id": workspace_id,
        "total": len(workflows),
        "workflows": [wf.dict() for wf in workflows],
    }


# ── Debug: Test workflow endpoints ─────────────────────────────────────────────

@router.get("/debug/test-endpoints")
def debug_test_endpoints(
    workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """
    Debug endpoint to test different workflow API endpoints.
    Helps identify which endpoint works for your Alteryx Cloud instance.
    """
    workspace_id = workspace_id or os.getenv("ALTERYX_WORKSPACE_ID", "")
    if not workspace_id:
        return {"error": "Missing workspace_id parameter"}

    if authorization and authorization.startswith("Bearer "):
        access_token = authorization.split(" ", 1)[1]
    else:
        access_token = os.getenv("ALTERYX_ACCESS_TOKEN", "")

    if not access_token:
        return {"error": "No access token available"}

    session = AlteryxSession(
        access_token=access_token,
        refresh_token=os.getenv("ALTERYX_REFRESH_TOKEN"),
    )

    test_endpoints = [
        f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/workflows",
        f"{ALTERYX_BASE_URL}/v3/workspaces/{workspace_id}/workflows",
        f"{ALTERYX_BASE_URL}/api/v4/workspaces/{workspace_id}/workflows",
        f"{ALTERYX_BASE_URL}/api/workspaces/{workspace_id}/workflows",
        f"{ALTERYX_BASE_URL}/v4/workflows",
        f"{ALTERYX_BASE_URL}/api/workflows",
        f"{ALTERYX_BASE_URL}/designer/api/workflows/list",
        f"{ALTERYX_BASE_URL}/designer/api/v1/workflows",
        f"{ALTERYX_BASE_URL}/api/v1/workflows",
        f"{ALTERYX_BASE_URL}/workflows",
    ]

    results = []
    for endpoint in test_endpoints:
        try:
            resp = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                params={"limit": 5} if "/workflows" in endpoint else {"workspaceId": workspace_id},
                timeout=10,
            )
            
            result = {
                "endpoint": endpoint,
                "status": resp.status_code,
                "success": resp.status_code == 200,
            }
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    result["response_type"] = type(data).__name__
                    result["keys"] = list(data.keys()) if isinstance(data, dict) else "array"
                    result["count"] = len(data) if isinstance(data, list) else len(data.get("data", data.get("workflows", [])))
                except:
                    result["response"] = "Non-JSON response"
            else:
                result["error"] = resp.text[:200]
            
            results.append(result)
        except Exception as e:
            results.append({
                "endpoint": endpoint,
                "status": "error",
                "error": str(e),
            })

    successful = [r for r in results if r.get("success")]
    return {
        "workspace_id": workspace_id,
        "total_tested": len(test_endpoints),
        "successful": len(successful),
        "endpoints": results,
    }
