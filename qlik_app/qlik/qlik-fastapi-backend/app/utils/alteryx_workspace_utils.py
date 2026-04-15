# qlikAI-backend/utils/alteryx_workspace_utils.py

import requests
from typing import Optional
from dataclasses import dataclass, field

ALTERYX_BASE_URL = "https://us1.alteryxcloud.com"
ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"

# Client ID shown on the OAuth2.0 API Tokens page — used only for refresh grant
ALTERYX_CLIENT_ID = "af1b5321-afe0-48c2-966a-c77d74e98085"


# ---------------------------------------------------------------------------
# Token container — holds current access token + optional refresh token
# ---------------------------------------------------------------------------

@dataclass
class AlteryxSession:
    access_token: str
    refresh_token: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None


import os
from dotenv import load_dotenv
load_dotenv()

def get_session_from_env() -> AlteryxSession:
    """Build a session from environment variables."""
    access_token = os.getenv("ALTERYX_ACCESS_TOKEN")
    if not access_token:
        raise ValueError("ALTERYX_ACCESS_TOKEN not set in .env")
    
    return AlteryxSession(
        access_token=access_token,
        refresh_token=os.getenv("ALTERYX_REFRESH_TOKEN"),
        workspace_name=os.getenv("ALTERYX_WORKSPACE_NAME"),
        workspace_id=os.getenv("ALTERYX_WORKSPACE_ID"),
    )

# ---------------------------------------------------------------------------
# Token refresh
# ---------------------------------------------------------------------------

def refresh_access_token(refresh_token: str) -> tuple[str, Optional[str]]:
    """
    Use the refresh token to obtain a new access token from Ping Identity.
    Returns a tuple of (new_access_token, new_refresh_token).
    Raises requests.HTTPError on failure.
    """
    resp = requests.post(
        ALTERYX_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": ALTERYX_CLIENT_ID,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], data.get("refresh_token")


# ---------------------------------------------------------------------------
# Workspace API calls — with automatic one-shot refresh on 401
# ---------------------------------------------------------------------------

def _get_with_refresh(
    url: str,
    session: AlteryxSession,
    params: Optional[dict] = None,
) -> dict:
    """
    GET wrapper that:
      1. Tries with the current access token.
      2. On 401, attempts a refresh (if refresh_token is available).
      3. Retries once with the new token.
      4. Raises on any other error or if refresh also fails.
    """
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

    resp = _do_get(session.access_token)

    # Token expired — try refresh
    if resp.status_code == 401 and session.refresh_token:
        try:
            new_token, new_refresh_token = refresh_access_token(session.refresh_token)
            session.access_token = new_token          # mutate session in-place
            if new_refresh_token:
                session.refresh_token = new_refresh_token
            resp = _do_get(new_token)                 # retry once
        except requests.HTTPError:
            raise requests.HTTPError(
                "Access token expired and refresh token is also invalid. "
                "Please generate a new token from Alteryx One.",
                response=resp,
            )

    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as e:
        raise ValueError(
            f"Invalid JSON response from Alteryx endpoint {url} (status {resp.status_code}). Response body: {resp.text[:200]}"
        ) from e


# ---------------------------------------------------------------------------
# Workspace lookup
# ---------------------------------------------------------------------------

# def list_alteryx_workspaces(session: AlteryxSession) -> list[dict]:
#     """Fetch all workspaces accessible to the session token."""
#     data = _get_with_refresh(
#         f"{ALTERYX_BASE_URL}/v4/workspaces",
#         session,
#     )
#     return data.get("data", [])




def list_alteryx_workspaces(session: AlteryxSession) -> list[dict]:
    """Fetch all workspaces accessible to the session token."""
    data = _get_with_refresh(
        f"{ALTERYX_BASE_URL}/v4/workspaces",
        session,
    )
    # The API may return a plain list OR {"data": [...]}
    if isinstance(data, list):
        return data
    return data.get("data", [])

def get_workspace_id_by_name(session: AlteryxSession, workspace_name: str) -> str:
    """
    Resolve workspace name → ID.
    Tries exact match first (case-insensitive), then partial match.
    Raises ValueError on ambiguity or not found.
    Updates session.workspace_id and session.workspace_name in-place on success.
    """
    workspaces = list_alteryx_workspaces(session)

    # 1. Exact match (case-insensitive)
    for ws in workspaces:
        if ws.get("name", "").lower() == workspace_name.lower():
            session.workspace_id = str(ws["id"])
            session.workspace_name = ws["name"]
            return ws["id"]

    # 2. Partial match fallback
    matches = [
        ws for ws in workspaces
        if workspace_name.lower() in ws.get("name", "").lower()
    ]

    if len(matches) == 1:
        session.workspace_id = str(matches[0]["id"])
        session.workspace_name = matches[0]["name"]
        return matches[0]["id"]
    elif len(matches) > 1:
        names = [ws["name"] for ws in matches]
        raise ValueError(
            f"Ambiguous workspace name '{workspace_name}'. "
            f"Multiple matches: {names}. Please use the full exact name."
        )
    else:
        raise ValueError(
            f"No Alteryx workspace found matching '{workspace_name}'. "
            f"Available: {[ws.get('name') for ws in workspaces]}"
        )


# ---------------------------------------------------------------------------
# Convenience: build a validated session in one call
# ---------------------------------------------------------------------------

def create_alteryx_session(
    access_token: str,
    workspace_name: str,
    refresh_token: Optional[str] = None,
) -> AlteryxSession:
    """
    Entry point for the FastAPI endpoint.
    Builds an AlteryxSession, validates the token by resolving the workspace,
    and returns the fully populated session object.

    Raises:
        ValueError         — workspace not found or ambiguous
        requests.HTTPError — token invalid / API unreachable
    """
    session = AlteryxSession(
        access_token=access_token,
        refresh_token=refresh_token,
    )
    get_workspace_id_by_name(session, workspace_name)  # populates session fields
    return session
