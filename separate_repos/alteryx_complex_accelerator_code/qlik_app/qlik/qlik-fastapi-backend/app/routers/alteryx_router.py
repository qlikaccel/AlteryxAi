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
    generate_executive_summary,
    generate_dbt_project,
    generate_m_query,
    generate_workflow_diagram,
    validate_migration,
)

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx"])
logger = logging.getLogger(__name__)

MAX_BULK_UPLOAD_BYTES = 250 * 1024 * 1024


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
    workspace_id: Optional[str] = ""
    expected_row_count: Optional[int] = None
    numeric_columns: list[str] = []


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
    for node in reversed(_terminal_output_nodes(workflow)):
        node_blob = _node_blob(node)
        for path in _candidate_output_paths(node):
            count = _count_delimited_file(path)
            if count is None:
                continue
            score = max(
                _name_match_score(path.stem, final_table_name),
                _name_match_score(path.name, final_table_name),
                _name_match_score(node_blob, final_table_name),
            )
            columns = _delimited_file_columns(path)
            counted_outputs.append({
                "row_count": count,
                "column_count": len(columns) or None,
                "columns": columns,
                "method": "matched_output_file" if score >= 80 else "output_file",
                "source": str(path),
                "confidence": "high" if score >= 80 or not final_table_name else "medium",
                "match_score": score,
            })

    if counted_outputs:
        counted_outputs.sort(key=lambda item: (item.get("match_score", 0), item.get("confidence") == "high"), reverse=True)
        best = counted_outputs[0]
        if not final_table_name or best.get("match_score", 0) >= 80 or len(counted_outputs) == 1:
            return best

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
        workflows=result["workflows"],
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
            "workflow": workflows[0],
            "workflows": workflows,
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
            "workflow": parsed_workflow,
            "workflows": workflows,
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
        "workflows": batch.get("workflows", []),
    }


@router.get("/batches/{batch_id}/workflows/{workflow_id}")
def get_alteryx_upload_batch_workflow(batch_id: str, workflow_id: str):
    return {
        "success": True,
        "batch_id": batch_id,
        "workflow": _find_batch_workflow(batch_id, workflow_id),
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

    powerbi_validation = None
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
                numeric_columns=request.numeric_columns or [],
                expected_row_count=expected,
            )
        )

    powerbi_actual = None
    if powerbi_validation:
        powerbi_row_check = next(
            (check for check in powerbi_validation.get("checks", []) if check.get("name") == "Row count"),
            {},
        )
        powerbi_actual = powerbi_row_check.get("actual")
        if not isinstance(powerbi_actual, int):
            actual_payload = powerbi_validation.get("actual") or {}
            actual_value = actual_payload.get("RowCount")
            if isinstance(actual_value, (int, float)):
                powerbi_actual = int(actual_value)

    if expected is None and isinstance(powerbi_actual, int) and powerbi_actual > 0:
        alteryx_count = {
            **alteryx_count,
            "row_count": powerbi_actual,
            "method": "final_table_mquery_count",
            "source": "Power BI executeQueries count of the published final M query table.",
            "confidence": "medium",
        }
        expected = powerbi_actual

    row_check = next(
        (check for check in (powerbi_validation or {}).get("checks", []) if check.get("name") == "Row count"),
        {},
    )
    actual = row_check.get("actual") if row_check else None
    variance = (
        actual - expected
        if isinstance(actual, int) and isinstance(expected, int)
        else row_check.get("variance")
    )
    status = (
        "PASS" if isinstance(variance, int) and variance == 0 else "WARNING"
        if isinstance(variance, int) else row_check.get("status") or "INFO"
    )

    return {
        "success": True,
        "workflow_id": workflow_id,
        "workflow_name": workflow.get("name"),
        "table_name": (powerbi_validation or {}).get("table_name") or request.table_name,
        "available_columns": (powerbi_validation or {}).get("available_columns", []),
        "alteryx": alteryx_count,
        "powerbi": powerbi_validation,
        "checks": [
            {
                "name": "Row count",
                "expected": expected,
                "actual": actual,
                "variance": variance,
                "status": status,
                "alteryx_method": alteryx_count.get("method"),
                "alteryx_confidence": alteryx_count.get("confidence"),
            }
        ],
    }


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
