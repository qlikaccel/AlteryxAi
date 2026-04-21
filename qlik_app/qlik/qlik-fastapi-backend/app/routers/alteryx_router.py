# Alteryx accelerator backend routes.

import logging
import json
import base64
import os
import requests
import re
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


class CloudWorkflowMaterializeRequest(BaseModel):
    workflow_id: str
    workflow_name: Optional[str] = None
    workspace_id: Optional[str] = None
    workspace_name: Optional[str] = None


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
