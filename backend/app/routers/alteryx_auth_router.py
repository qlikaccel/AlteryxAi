# app/routers/alteryx_auth_router.py
#
# CHANGES vs original dev10:
#   - validate_alteryx_auth() now resolves tokens via
#     TokenManager.get_tokens_from_storage_or_env() instead of reading
#     os.getenv() directly.  This ensures that after the first successful
#     connection (and token rotation), subsequent /validate-auth calls use
#     the fresh rotated tokens from token_storage.json — not the stale
#     originals in .env.
#   - All other logic (error handling, response model) is UNCHANGED.

import os
import requests
from fastapi import APIRouter, HTTPException

from app.utils.token_manager import TokenManager
from utils.alteryx_workspace_utils import create_alteryx_session
from schemas.alteryx_schemas import AlteryxAuthRequest, AlteryxAuthResponse

router = APIRouter(prefix="/api/alteryx", tags=["Alteryx Auth"])


@router.post("/validate-auth", response_model=AlteryxAuthResponse)
def validate_alteryx_auth(config: AlteryxAuthRequest):
    """
    Validates Alteryx Cloud credentials and confirms the workspace name.

    Token resolution order (most-recent first):
      1. token_storage.json  — contains the latest rotated pair after any refresh
      2. .env environment variables  — original tokens set at startup
      3. Raise HTTP 500 if neither has a refresh token

    The frontend should store the returned access_token and refresh_token
    because refresh tokens rotate on every use.
    """
    print(f"\n📋 [validate_alteryx_auth] Starting authentication validation...")
    print(f"   Workspace name: {config.workspace_name}")

    # ── Resolve tokens from storage → env (storage wins so rotated tokens are used) ──
    access_token, refresh_token = TokenManager.get_tokens_from_storage_or_env()

    print(f"\n🔑 Checking tokens:")
    print(f"   ✓ ACCESS_TOKEN  : {'present' if access_token else 'MISSING'}")
    print(f"   ✓ REFRESH_TOKEN : {'present' if refresh_token else 'MISSING'}")

    if not refresh_token:
        print(f"\n❌ REFRESH_TOKEN NOT FOUND in storage or environment!")
        raise HTTPException(
            status_code=500,
            detail=(
                "ALTERYX_REFRESH_TOKEN is not configured. "
                "Generate tokens from Alteryx One → User Preferences → OAuth 2.0 API Tokens "
                "and set them in .env as ALTERYX_ACCESS_TOKEN and ALTERYX_REFRESH_TOKEN."
            ),
        )

    if not access_token:
        print(f"\n❌ ACCESS_TOKEN NOT FOUND in storage or environment!")
        raise HTTPException(
            status_code=500,
            detail=(
                "ALTERYX_ACCESS_TOKEN is not configured. "
                "Generate tokens from Alteryx One → User Preferences → OAuth 2.0 API Tokens."
            ),
        )

    print(f"\n✅ Both tokens resolved. Proceeding with session creation...")

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
    print(f"   Tokens persisted to token_storage.json for auto-refresh")

    return response
