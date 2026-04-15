# qlikAI-backend/routers/alteryx_auth_router.py

import requests
from fastapi import APIRouter, HTTPException

from utils.alteryx_workspace_utils import create_alteryx_session
from schemas.alteryx_schemas import AlteryxAuthRequest, AlteryxAuthResponse

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx Auth"])


@router.post("/validate-auth", response_model=AlteryxAuthResponse)
def validate_alteryx_auth(config: AlteryxAuthRequest):
    """
    Validates the user-supplied Alteryx access token and resolves
    workspace name → ID. Returns the populated session details.

    The frontend should:
      1. Store workspace_id for all subsequent migration API calls.
      2. Store access_token (may differ from input if it was auto-refreshed).
      3. Persist refresh_token (if provided) for future auto-renewal.
    """
    try:
        session = create_alteryx_session(
            access_token=config.access_token,
            workspace_name=config.workspace_name,
            refresh_token=config.refresh_token,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except requests.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else 401
        if status_code == 401:
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
        workspace_id=session.workspace_id,
        access_token=session.access_token,   # may be refreshed
        refresh_token=config.refresh_token,  # echo back for frontend persistence
    )
