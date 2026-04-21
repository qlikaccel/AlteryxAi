import requests
from fastapi import APIRouter, HTTPException

from utils.alteryx_workspace_utils import create_alteryx_session
from schemas.alteryx_schemas import AlteryxAuthRequest, AlteryxAuthResponse

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx Auth"])


@router.post("/validate-auth", response_model=AlteryxAuthResponse)
def validate_alteryx_auth(config: AlteryxAuthRequest):
    """
    Validates Alteryx Cloud credentials stored in .env and confirms
    the user-supplied workspace name matches the token's workspace.

    Flow:
    1. Reads access_token and refresh_token from environment variables.
    2. Refreshes the access token (Alteryx tokens expire every 5 min).
    3. Calls /iam/v1/workspaces/current to resolve workspace ID.
    4. Confirms the workspace name the user typed matches the token's workspace.

    The frontend should store:
    - workspace_id: required for all subsequent migration API calls
    - access_token: fresh token for immediate use
    - refresh_token: persist this — it replaces the old one (rotation)
    """
    import os

    print(f"\n📋 [validate_alteryx_auth] Starting authentication validation...")
    print(f"   Workspace name: {config.workspace_name}")
    
    # Read tokens from environment (set in .env)
    access_token = os.getenv("ALTERYX_ACCESS_TOKEN", "")
    refresh_token = os.getenv("ALTERYX_REFRESH_TOKEN", "")

    print(f"\n🔑 Checking tokens from environment:")
    print(f"   ✓ ACCESS_TOKEN loaded: {bool(access_token)}")
    print(f"   ✓ REFRESH_TOKEN loaded: {bool(refresh_token)}")

    if not refresh_token:
        print(f"\n❌ REFRESH_TOKEN NOT FOUND in environment!")
        raise HTTPException(
            status_code=500,
            detail=(
                "ALTERYX_REFRESH_TOKEN is not configured in environment variables. "
                "Generate tokens from Alteryx One → User Preferences → OAuth 2.0 API Tokens."
            ),
        )

    if not access_token:
        print(f"\n❌ ACCESS_TOKEN NOT FOUND in environment!")
        raise HTTPException(
            status_code=500,
            detail=(
                "ALTERYX_ACCESS_TOKEN is not configured in environment variables. "
                "Generate tokens from Alteryx One → User Preferences → OAuth 2.0 API Tokens."
            ),
        )
    
    print(f"\n✅ Both tokens found. Proceeding with session creation...")

    try:
        print(f"\n🚀 [validate_alteryx_auth] Calling create_alteryx_session()...")
        session = create_alteryx_session(
            access_token=access_token,
            workspace_name=config.workspace_name,
            refresh_token=refresh_token,
        )
        print(f"\n✅ [validate_alteryx_auth] Session created successfully!")
    except ValueError as e:
        print(f"\n❌ [validate_alteryx_auth] ValueError: {e}")
        raise HTTPException(status_code=404, detail=str(e))
    except requests.HTTPError as e:
        print(f"\n❌ [validate_alteryx_auth] HTTPError: {e}")
        status_code = e.response.status_code if e.response is not None else 401
        if status_code == 401:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Invalid or expired credentials. "
                    "Please generate a new token from Alteryx One → "
                    "User Preferences → OAuth 2.0 API Tokens, "
                    "then update ALTERYX_ACCESS_TOKEN and ALTERYX_REFRESH_TOKEN in your .env file."
                ),
            )
        raise HTTPException(
            status_code=status_code,
            detail=f"Alteryx API error: {e}",
        )
    except Exception as e:
        print(f"\n❌ [validate_alteryx_auth] Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")

    response = AlteryxAuthResponse(
        status="authenticated",
        workspace_name=session.workspace_name,
        workspace_id=session.workspace_id,
        access_token=session.access_token,    # freshly refreshed — store this
        refresh_token=session.refresh_token,  # rotated — store this, replaces old one
    )
    
    print(f"\n✅ [validate_alteryx_auth] Authentication successful!")
    print(f"   Workspace: {response.workspace_name} (ID: {response.workspace_id})")
    print(f"   Refresh token will be used for auto-refresh fallback (365 day lifetime)")
    
    return response
