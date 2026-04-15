# qlikAI-backend/routers/alteryx_router.py
#
# Mount in main.py:
#   from routers.alteryx_router import router as alteryx_router
#   app.include_router(alteryx_router)

import logging
import os
import requests
from fastapi import APIRouter, HTTPException, Header, Response
from typing import Optional
from pydantic import BaseModel

from utils.alteryx_workspace_utils import (
    AlteryxSession,
    create_alteryx_session,
    get_workspace_id_by_name,
    _get_with_refresh,
    ALTERYX_BASE_URL,
    refresh_access_token,
    ALTERYX_CLIENT_ID,
)

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx"])
logger = logging.getLogger(__name__)


# ── Schemas ──────────────────────────────────────────────────────────────────

class AlteryxAuthRequest(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    workspace_name: str


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


# ── Auth validation endpoint ──────────────────────────────────────────────────

# @router.post("/validate-auth", response_model=AlteryxAuthResponse)
# def validate_alteryx_auth(config: AlteryxAuthRequest):
#     """
#     Validates access token and resolves workspace name → ID.
#     Returns the workspace_id for downstream use.
#     """
#     session = AlteryxSession(
#         access_token=config.access_token,
#         refresh_token=config.refresh_token,
#     )

@router.post("/validate-auth", response_model=AlteryxAuthResponse)
def validate_alteryx_auth(config: AlteryxAuthRequest):
    # Use env values as fallback if not provided in request
    access_token = config.access_token or os.getenv("ALTERYX_ACCESS_TOKEN")
    refresh_token = config.refresh_token or os.getenv("ALTERYX_REFRESH_TOKEN")
    workspace_name = config.workspace_name or os.getenv("ALTERYX_WORKSPACE_NAME")

    if not workspace_name:
        raise HTTPException(
            status_code=400,
            detail="Workspace name is required. Set ALTERYX_WORKSPACE_NAME in env or provide it in the request.",
        )

    if not access_token and not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="No access token supplied and no refresh token available from env. Please provide a valid Alteryx access token or set ALTERYX_REFRESH_TOKEN.",
        )

    try:
        session = create_alteryx_session(
            access_token=access_token or "",
            refresh_token=refresh_token,
            workspace_name=workspace_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 401
        if status_code == 401:
            if refresh_token:
                raise HTTPException(
                    status_code=401,
                    detail="Access token expired. Tried refreshing with ALTERYX_REFRESH_TOKEN from env, but refresh failed. Please verify the refresh token or generate new tokens.",
                )
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired access token. Please generate a new one from Alteryx One → OAuth 2.0 API Tokens.",
            )
        raise HTTPException(status_code=status_code, detail=f"Alteryx API error: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    return AlteryxAuthResponse(
        status="authenticated",
        workspace_name=session.workspace_name,
        workspace_id=str(session.workspace_id),
        access_token=session.access_token,
        refresh_token=session.refresh_token,
    )


@router.get("/workflows")
def get_alteryx_workflows(
    workspace_id: Optional[str] = None,
    authorization: Optional[str] = Header(None),
    refresh_token: Optional[str] = Header(None, alias="X-Alteryx-Refresh-Token"),
    response: Response = None,
):
    # Fallback to env if not provided
    workspace_id = workspace_id or os.getenv("ALTERYX_WORKSPACE_ID")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="Missing workspace_id query parameter.")

    access_token = (
        authorization.split(" ", 1)[1]
        if authorization and authorization.startswith("Bearer ")
        else os.getenv("ALTERYX_ACCESS_TOKEN")
    )
    if not access_token:
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")

    refresh_token = refresh_token or os.getenv("ALTERYX_REFRESH_TOKEN")

    session = AlteryxSession(
        access_token=access_token,
        refresh_token=refresh_token,
    )

    endpoint_candidates = [
        (f"{ALTERYX_BASE_URL}/v4/workspaces/{workspace_id}/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/v3/workspaces/{workspace_id}/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/designer/api/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/designer/api/workflows", {"limit": 100, "workspaceId": workspace_id}),
        (f"{ALTERYX_BASE_URL}/designer/api/workflows", {"limit": 100, "workspace_id": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/v4/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/api/v4/workflows", {"limit": 100, "workspaceId": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/v4/workflows", {"limit": 100, "workspace_id": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/v3/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/api/v3/workflows", {"limit": 100, "workspaceId": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/v3/workflows", {"limit": 100, "workspace_id": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/workflows", {"limit": 100}),
        (f"{ALTERYX_BASE_URL}/api/workflows", {"limit": 100, "workspaceId": workspace_id}),
        (f"{ALTERYX_BASE_URL}/api/workflows", {"limit": 100, "workspace_id": workspace_id}),
    ]

    last_error = None
    data = None
    for endpoint, params in endpoint_candidates:
        logger.info(f"Trying Alteryx workflow endpoint: {endpoint} params={params}")
        try:
            data = _get_with_refresh(endpoint, session, params=params)
            logger.info(f"Alteryx workflow endpoint succeeded: {endpoint}")
            break
        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else 500
            last_error = (status_code, str(e))
            logger.warning(f"Alteryx workflow endpoint failed: {endpoint} status={status_code} error={e}")
            if status_code in {401, 403, 404}:
                continue
            raise HTTPException(status_code=status_code, detail=f"Alteryx API error: {e}")
        except ValueError as e:
            last_error = (500, str(e))
            logger.warning(f"Alteryx workflow endpoint invalid JSON: {endpoint} error={e}")
            continue

    if data is None:
        status_code, detail = last_error if last_error is not None else (500, "Unable to fetch workflows.")
        raise HTTPException(
            status_code=status_code or 500,
            detail=f"Failed to fetch workflows from Alteryx. Last error: {detail}",
        )

    raw_workflows = data.get("workflows", 
                    data.get("data", 
                    data if isinstance(data, list) else []))

    workflows = [
        AlteryxWorkflow(
            id=wf.get("id", ""),
            name=wf.get("name", "Unnamed Workflow"),
            lastModifiedDate=wf.get("dateModified") or wf.get("dateCreated"),
            runCount=wf.get("runCount"),
            credentialType=wf.get("credentialType"),
            workerTag=wf.get("workerTag"),
        )
        for wf in raw_workflows
        if wf.get("id")
    ]

    if response is not None and session.refresh_token and session.refresh_token != refresh_token:
        response.headers["X-Alteryx-Refresh-Token"] = session.refresh_token

    return {
        "workspace_id": workspace_id,
        "total": len(workflows),
        "workflows": [wf.dict() for wf in workflows],
    }
