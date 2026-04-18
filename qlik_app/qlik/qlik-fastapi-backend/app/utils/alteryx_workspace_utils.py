# app/utils/alteryx_workspace_utils.py

import os
import requests
import time
import logging
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

load_dotenv()

logger = logging.getLogger(__name__)

# Import the new TokenManager
from app.utils.token_manager import TokenManager

ALTERYX_BASE_URL  = "https://us1.alteryxcloud.com"
ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"

_KNOWN_PUBLIC_CLIENT_ID = "af1b5321-afe0-48c2-966a-c77d74e98085"

ALTERYX_CLIENT_ID     = os.getenv("ALTERYX_CLIENT_ID", _KNOWN_PUBLIC_CLIENT_ID)
ALTERYX_CLIENT_SECRET = os.getenv("ALTERYX_CLIENT_SECRET", "")


# ── Token container ──────────────────────────────────────────────────────────

@dataclass
class AlteryxSession:
    access_token: str
    refresh_token: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    custom_url: Optional[str] = None


# ── Env-based session ────────────────────────────────────────────────────────

def get_session_from_env() -> AlteryxSession:
    access_token  = os.getenv("ALTERYX_ACCESS_TOKEN", "")
    refresh_token = os.getenv("ALTERYX_REFRESH_TOKEN", "")
    if not access_token and not refresh_token:
        raise ValueError(
            "No Alteryx credentials found. Set ALTERYX_ACCESS_TOKEN "
            "(and ALTERYX_REFRESH_TOKEN) in your .env file."
        )
    return AlteryxSession(
        access_token=access_token,
        refresh_token=refresh_token,
        workspace_name=os.getenv("ALTERYX_WORKSPACE_NAME"),
        workspace_id=os.getenv("ALTERYX_WORKSPACE_ID"),
    )


# ── Token refresh ────────────────────────────────────────────────────────────

def refresh_access_token(refresh_token: str) -> tuple[str, Optional[str]]:
    """
    Refresh access token using TokenManager (with persistence and retry logic).
    """
    logger.info(f"\n🔄 [refresh_access_token] Using TokenManager...")
    return TokenManager.refresh_token(refresh_token)


# ── Token expiry check ───────────────────────────────────────────────────────

def is_token_expired(token: str, buffer_seconds: int = 30) -> bool:
    if not token:
        logger.debug("⏰ No token provided — treating as expired")
        return True
    if not HAS_JWT:
        logger.debug("⚠️  JWT library not available — cannot check expiry, treating as valid")
        return False
    try:
        decoded   = jwt.decode(token, options={"verify_signature": False})
        exp       = decoded.get("exp", 0)
        remaining = exp - time.time()
        
        if remaining <= buffer_seconds:
            logger.info(f"⏰ Access token expiring in {remaining:.1f}s — will refresh")
            return True
        
        logger.debug(f"✅ Access token valid for {remaining:.1f}s")
        return False
        
    except Exception as e:
        logger.warning(f"⚠️  Could not decode token ({e}) — treating as expired")
        return True


# ── Ensure fresh token ───────────────────────────────────────────────────────

def ensure_fresh_token(session: AlteryxSession) -> str:
    """
    Ensure we have a valid access token.
    Always uses the latest refresh token from storage, not session.
    Uses TokenManager for persistence and retry logic.
    """
    # Try to load latest stored refresh token (in case it was rotated)
    stored = TokenManager._load_tokens_from_storage()
    refresh_to_use = stored.get("refresh_token") or session.refresh_token
    
    if not refresh_to_use:
        raise ValueError("No refresh token available for token refresh")
    
    fresh_access, fresh_refresh = TokenManager.get_fresh_access_token(
        session.access_token,
        refresh_to_use
    )
    
    # Update session with fresh tokens
    session.access_token = fresh_access
    if fresh_refresh:
        session.refresh_token = fresh_refresh
    
    logger.info(f"✅ [ensure_fresh_token] Token is ready to use")
    return fresh_access


# ── Authenticated GET ────────────────────────────────────────────────────────

def _get_with_refresh(
    url: str,
    session: AlteryxSession,
    params: Optional[dict] = None,
) -> dict:
    def _do_get(token: str) -> requests.Response:
        return requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            params=params,
            timeout=15,
        )

    logger.debug(f"   Making request to: {url}")
    fresh_token = ensure_fresh_token(session)
    resp = _do_get(fresh_token)

    if resp.status_code == 401 and session.refresh_token:
        logger.warning(f"⚠️  Got 401 — attempting fallback refresh...")
        try:
            new_access, new_refresh = refresh_access_token(session.refresh_token)
            session.access_token = new_access
            if new_refresh:
                session.refresh_token = new_refresh
            resp = _do_get(new_access)
            logger.info(f"   Fallback refresh succeeded")
        except Exception as e:
            logger.error(f"❌ 401 and fallback refresh also failed: {e}")
            raise requests.HTTPError(
                f"401 and fallback refresh also failed: {e}", response=resp
            ) from e

    if resp.status_code >= 400:
        logger.error(f"❌ API error {resp.status_code}")
        logger.error(f"   URL: {url}")
        logger.error(f"   Response: {resp.text[:500]}")
        resp.raise_for_status()

    try:
        return resp.json()
    except ValueError as exc:
        logger.error(f"❌ Non-JSON response from API: {resp.text[:300]}")
        raise ValueError(
            f"Non-JSON response from {url} (HTTP {resp.status_code}): {resp.text[:300]}"
        ) from exc


# ── Workspace listing ────────────────────────────────────────────────────────

def list_alteryx_workspaces(session: AlteryxSession) -> list[dict]:
    logger.info(f"\n🔵 Fetching workspaces...")
    endpoints = [
        f"{ALTERYX_BASE_URL}/v4/workspaces",
        f"{ALTERYX_BASE_URL}/iam/v1/workspaces",
        f"{ALTERYX_BASE_URL}/api/v1/workspaces",
    ]
    last_error = None
    for endpoint in endpoints:
        try:
            logger.info(f"   Trying: {endpoint}")
            data = _get_with_refresh(endpoint, session)
            workspaces = (
                data if isinstance(data, list)
                else data.get("data", data.get("workspaces", []))
            )
            if workspaces is not None:
                logger.info(f"   ✅ Successfully fetched {len(workspaces)} workspace(s)")
                return workspaces
        except Exception as e:
            logger.warning(f"   ⚠️  Endpoint failed: {type(e).__name__}: {str(e)[:100]}")
            last_error = e
            continue

    logger.error(f"❌ All workspace endpoints failed. Last error: {last_error}")
    raise ValueError(f"Unable to fetch workspaces. Last error: {last_error}.")


# ── Workspace name → ID ──────────────────────────────────────────────────────

def get_workspace_id_by_name(session: AlteryxSession, workspace_name: str) -> str:
    workspaces = list_alteryx_workspaces(session)

    for ws in workspaces:
        if ws.get("name", "").lower() == workspace_name.lower():
            session.workspace_id   = str(ws["id"])
            session.workspace_name = ws["name"]
            session.custom_url     = ws.get("custom_url")
            return session.workspace_id

    matches = [
        ws for ws in workspaces
        if workspace_name.lower() in ws.get("name", "").lower()
    ]
    if len(matches) == 1:
        session.workspace_id   = str(matches[0]["id"])
        session.workspace_name = matches[0]["name"]
        session.custom_url     = matches[0].get("custom_url")
        return session.workspace_id
    elif len(matches) > 1:
        raise ValueError(
            f"Ambiguous workspace name '{workspace_name}'. "
            f"Matches: {[ws['name'] for ws in matches]}"
        )
    else:
        available = [ws.get("name") for ws in workspaces]
        raise ValueError(
            f"No workspace found matching '{workspace_name}'. "
            f"Available: {available}"
        )


# ── Workflow listing ─────────────────────────────────────────────────────────
# ✅ Confirmed working endpoint from Alteryx engineer (community.alteryx.com):
#    https://us1.alteryxcloud.com/svc-workflow/api/v1/workflows
#
# The Designer Experience workflows are NOT accessible via:
#   - /api/v1/workflows       → 404
#   - /webapi/v3/workflows    → 404
#   - /v4/flows               → 403 (different product: Trifacta Classic)
#
# The correct internal service URL is /svc-workflow/api/v1/workflows

def list_alteryx_workflows(session: AlteryxSession, workspace_id: Optional[str] = None) -> list[dict]:
    logger.info(f"\n🔵 Fetching Designer Cloud workflows...")

    # ✅ The ONLY working endpoint for Designer Experience workflows
    endpoints = [
        f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows",
        f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows?limit=100",
    ]

    # Fallback: try workspace-scoped variant if we have an ID
    if workspace_id:
        endpoints.append(
            f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows?workspaceId={workspace_id}"
        )
        logger.debug(f"   Workspace ID provided: {workspace_id}")

    last_error = None
    for endpoint in endpoints:
        try:
            logger.info(f"   Trying: {endpoint}")
            data = _get_with_refresh(endpoint, session)

            # Response shape can be list, {"data": [...]}, or {"workflows": [...]}
            if isinstance(data, list):
                workflows = data
            else:
                workflows = (
                    data.get("data")
                    or data.get("workflows")
                    or data.get("items")
                    or data.get("results")
                    or []
                )

            logger.info(f"   ✅ Successfully fetched {len(workflows)} workflow(s)")
            return workflows

        except Exception as e:
            logger.warning(f"   ⚠️  Endpoint failed: {type(e).__name__}: {str(e)[:100]}")
            last_error = e
            continue

    logger.error(f"❌ All workflow endpoints failed")
    raise ValueError(
        f"Unable to fetch Designer Cloud workflows. "
        f"Last error: {last_error}. "
        f"Ensure your OAuth token has Designer Cloud access."
    )


# ── Entry point ──────────────────────────────────────────────────────────────

def create_alteryx_session(
    access_token: str,
    workspace_name: str,
    refresh_token: Optional[str] = None,
) -> AlteryxSession:
    resolved_access  = access_token  or os.getenv("ALTERYX_ACCESS_TOKEN", "")
    resolved_refresh = refresh_token or os.getenv("ALTERYX_REFRESH_TOKEN")

    if not resolved_access and not resolved_refresh:
        raise ValueError(
            "No access token provided and none found in environment."
        )

    session = AlteryxSession(
        access_token=resolved_access,
        refresh_token=resolved_refresh,
    )

    get_workspace_id_by_name(session, workspace_name)
    return session
