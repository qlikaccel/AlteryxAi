# app/utils/alteryx_workspace_utils.py
#
# ROOT CAUSE FIXES applied in this version:
#
# BUG 1 — refresh_access_token() silently swallowed errors and returned the
#   OLD refresh_token string as the "new access_token" on any failure.
#   Every subsequent API call then 401'd because a refresh_token is not a
#   valid Bearer token. The retry loop could never self-heal.
#   FIX: raise on failure so callers get a real error, not a wrong token.
#
# BUG 2 — ALTERYX_CLIENT_ID was loaded from .env only, with no fallback.
#   When the .env doesn't set ALTERYX_CLIENT_ID, it defaults to "", and
#   Ping Identity returns 400 Bad Request for the refresh grant.
#   FIX: hard-code the public client_id ("af1b5321-...") as a fallback.
#   This is the public client_id shown on the OAuth 2.0 API Tokens page —
#   it is NOT a secret and is safe to embed.
#
# BUG 3 — create_alteryx_session() had the env fallback for access_token
#   commented out (resolved_access = access_token with fallback disabled).
#   When the UI sends an empty string, the session starts with no token and
#   immediately needs a refresh — which fails due to BUG 2.
#   FIX: restore the env fallback properly.
#
# BUG 4 — ensure_fresh_token() did not propagate the new refresh_token back
#   to the session when the server returns a rotated one.
#   FIX: always update session.refresh_token when a new one is returned.

import os
import re
import base64
import json
import requests
import time
from typing import Any, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

load_dotenv()

ALTERYX_BASE_URL  = "https://us1.alteryxcloud.com"
ALTERYX_TOKEN_URL = "https://pingauth.alteryxcloud.com/as/token"

# Public client_id shown on Alteryx One → OAuth 2.0 API Tokens page.
# Required for the refresh_token grant even with no client_secret.
_KNOWN_PUBLIC_CLIENT_ID = "af1b5321-afe0-48c2-966a-c77d74e98085"

ALTERYX_CLIENT_ID     = os.getenv("ALTERYX_CLIENT_ID", _KNOWN_PUBLIC_CLIENT_ID)
ALTERYX_CLIENT_SECRET = os.getenv("ALTERYX_CLIENT_SECRET", "")
ALTERYX_ENV_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", ".env")
)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode JWT payload without verifying the signature."""
    parts = (token or "").split(".")
    if len(parts) < 2:
        return {}

    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")))
    except Exception:
        return {}


def token_expiry_summary(token: str) -> str:
    payload = decode_jwt_payload(token)
    exp = payload.get("exp")
    if not exp:
        return "expiry unavailable"

    remaining = exp - time.time()
    minutes = abs(remaining) / 60
    direction = "expires in" if remaining >= 0 else "expired"
    return f"{direction} {minutes:.1f} minutes"


def looks_like_access_token(token: str) -> bool:
    payload = decode_jwt_payload(token)
    return bool(payload.get("client_id") and payload.get("aud"))


def looks_like_refresh_token(token: str) -> bool:
    payload = decode_jwt_payload(token)
    return bool(payload.get("sid") and payload.get("sub") and not payload.get("aud"))


# ── Token container ──────────────────────────────────────────────────────────

@dataclass
class AlteryxSession:
    access_token: str
    refresh_token: Optional[str] = None
    workspace_name: Optional[str] = None
    workspace_id: Optional[str] = None
    custom_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None


# ── Env-based session ────────────────────────────────────────────────────────

def get_session_from_env() -> AlteryxSession:
    """Build a session from environment variables."""
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


# ── Token refresh (BUG 1 + BUG 2 fixed) ─────────────────────────────────────

def refresh_access_token(refresh_token: str) -> tuple[str, Optional[str]]:
    """
    Exchange a refresh_token for a new access_token via Ping Identity.
    Returns (new_access_token, new_refresh_token_or_None).

    RAISES requests.HTTPError on failure.
    (Previously swallowed errors and returned the refresh_token string as
    the access_token — causing all subsequent API calls to 401.)
    """
    print(f"\n🔄 [refresh_access_token] Requesting new access_token from Ping Identity...")
    print(f"   client_id : {ALTERYX_CLIENT_ID}")

    payload: dict = {
        "grant_type":    "refresh_token",
        "refresh_token": refresh_token,
        "client_id":     ALTERYX_CLIENT_ID,   # BUG 2 FIX: always has a value now
    }
    if ALTERYX_CLIENT_SECRET:
        payload["client_secret"] = ALTERYX_CLIENT_SECRET

    resp = requests.post(
        ALTERYX_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=15,
    )

    if resp.status_code != 200:
        print(f"\n❌ [refresh_access_token] FAILED — HTTP {resp.status_code}")
        print(f"   Response: {resp.text[:400]}")
        if resp.status_code == 400 and "invalid_grant" in resp.text:
            raise ValueError(
                "The Alteryx refresh token is invalid, revoked, or already used. "
                "Alteryx refresh tokens rotate; after a successful refresh, the old refresh token no longer exists. "
                "Generate a completely new OAuth API token pair in Alteryx One and paste both tokens from the same generation."
            )
        # BUG 1 FIX: raise instead of silently returning wrong token
        resp.raise_for_status()

    body        = resp.json()
    new_access  = body.get("access_token", "")
    new_refresh = body.get("refresh_token")

    if not new_access:
        raise ValueError(
            f"Ping Identity returned 200 but no access_token in body: {body}"
        )

    print(f"✅ [refresh_access_token] New access_token received")
    if new_refresh:
        print(f"   Refresh token rotated — store the new one")
    return new_access, new_refresh


def persist_alteryx_tokens(access_token: str, refresh_token: Optional[str] = None) -> None:
    """Persist the current token pair so backend restarts do not reuse stale tokens."""
    try:
        if not os.path.exists(ALTERYX_ENV_FILE):
            print(f"⚠️  [persist_alteryx_tokens] .env not found: {ALTERYX_ENV_FILE}")
            return

        with open(ALTERYX_ENV_FILE, "r", encoding="utf-8") as env_file:
            content = env_file.read()

        def set_env_value(current: str, key: str, value: str) -> str:
            line = f"{key}={value}"
            if re.search(rf"^{re.escape(key)}=", current, flags=re.MULTILINE):
                return re.sub(rf"^{re.escape(key)}=.*$", line, current, flags=re.MULTILINE)
            return current.rstrip() + "\n" + line + "\n"

        if access_token:
            content = set_env_value(content, "ALTERYX_ACCESS_TOKEN", access_token)
            os.environ["ALTERYX_ACCESS_TOKEN"] = access_token
        if refresh_token:
            content = set_env_value(content, "ALTERYX_REFRESH_TOKEN", refresh_token)
            os.environ["ALTERYX_REFRESH_TOKEN"] = refresh_token

        with open(ALTERYX_ENV_FILE, "w", encoding="utf-8") as env_file:
            env_file.write(content)

        print("✅ [persist_alteryx_tokens] Stored latest Alteryx tokens in .env")
    except Exception as exc:
        print(f"⚠️  [persist_alteryx_tokens] Could not update .env: {exc}")


# ── Token expiry check ───────────────────────────────────────────────────────

def is_token_expired(token: str, buffer_seconds: int = 30) -> bool:
    """
    Returns True if the JWT is expired or expiring within buffer_seconds.
    Returns False (treat as valid) if PyJWT is unavailable.
    """
    if not token:
        return True

    if not HAS_JWT:
        return False   # Can't check — let the 401 fallback handle it

    try:
        decoded   = jwt.decode(token, options={"verify_signature": False})
        exp       = decoded.get("exp", 0)
        remaining = exp - time.time()
        if remaining <= buffer_seconds:
            print(f"⏰ Access token expiring in {remaining:.1f}s — will refresh proactively")
            return True
        print(f"✅ Access token valid for {remaining:.1f}s")
        return False
    except Exception as e:
        print(f"⚠️  Could not decode token ({e}) — treating as expired")
        return True


# ── Ensure fresh token ───────────────────────────────────────────────────────

def ensure_fresh_token(session: AlteryxSession) -> str:
    """
    Returns a valid access_token, refreshing via refresh_token if needed.
    Raises ValueError  — no refresh_token available.
    Raises HTTPError   — Ping Identity refresh call failed.
    """
    if not is_token_expired(session.access_token):
        return session.access_token

    print(f"\n🔄 [ensure_fresh_token] Token expired — refreshing...")

    last_error: Optional[Exception] = None

    if session.refresh_token:
        try:
            new_access, new_refresh = refresh_access_token(session.refresh_token)
            session.access_token = new_access
            if new_refresh:                  # BUG 4 FIX: always update rotated token
                session.refresh_token = new_refresh
            persist_alteryx_tokens(session.access_token, session.refresh_token)
            print(f"✅ [ensure_fresh_token] Token refreshed")
            return session.access_token
        except Exception as exc:
            last_error = exc
            print(f"⚠️  [ensure_fresh_token] Refresh token flow failed: {exc}")

    if last_error is not None:
        raise last_error

    raise ValueError(
        "Access token expired and no refresh_token or username/password is available. "
        "Provide Alteryx credentials to continue."
    )


# ── Authenticated GET ────────────────────────────────────────────────────────

def _get_with_refresh(
    url: str,
    session: AlteryxSession,
    params: Optional[dict] = None,
) -> dict:
    """
    Authenticated GET with proactive + reactive token refresh:
      1. Check expiry before call; refresh proactively if needed.
      2. Make the API call.
      3. On 401: attempt one final refresh and retry once.
      4. On other 4xx/5xx: raise.
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

    # Step 1: proactive refresh
    fresh_token = ensure_fresh_token(session)

    # Step 2: API call
    resp = _do_get(fresh_token)

    # Step 3: 401 reactive fallback
    if resp.status_code == 401 and session.refresh_token:
        print(f"\n⚠️  [_get_with_refresh] 401 after proactive refresh — final fallback attempt...")
        try:
            new_access, new_refresh = refresh_access_token(session.refresh_token)
            session.access_token = new_access
            if new_refresh:
                session.refresh_token = new_refresh
            resp = _do_get(new_access)
            print(f"   Retry status: {resp.status_code}")
        except Exception as e:
            raise requests.HTTPError(
                f"401 Unauthorized and fallback refresh also failed: {e}",
                response=resp,
            ) from e

    # Step 4: all other errors
    if resp.status_code >= 400:
        print(f"\n❌ API error {resp.status_code} | URL: {url}")
        print(f"   Response: {resp.text[:300]}")
        resp.raise_for_status()

    try:
        return resp.json()
    except ValueError as exc:
        raise ValueError(
            f"Non-JSON response from {url} (HTTP {resp.status_code}): {resp.text[:300]}"
        ) from exc


# ── Workspace listing ────────────────────────────────────────────────────────

def list_alteryx_workspaces(session: AlteryxSession) -> list[dict]:
    """Fetch all workspaces accessible to this token."""
    print(f"\n🔵 Fetching workspaces...")

    endpoints = [
        f"{ALTERYX_BASE_URL}/v4/workspaces",
        f"{ALTERYX_BASE_URL}/iam/v1/workspaces",
        f"{ALTERYX_BASE_URL}/api/v1/workspaces",
    ]

    last_error = None
    for endpoint in endpoints:
        try:
            print(f"  Trying: {endpoint}")
            data = _get_with_refresh(endpoint, session)
            workspaces = (
                data if isinstance(data, list)
                else data.get("data", data.get("workspaces", []))
            )
            if workspaces is not None:
                print(f"  ✅ {len(workspaces)} workspace(s)")
                return workspaces
        except Exception as e:
            print(f"  ⚠️  Failed: {e}")
            last_error = e
            continue

    raise ValueError(
        f"Unable to fetch workspaces. Last error: {last_error}. "
        f"Tokens may be expired — generate new ones from Alteryx One."
    )


# ── Workspace name → ID ──────────────────────────────────────────────────────

def get_workspace_id_by_name(session: AlteryxSession, workspace_name: str) -> str:
    """
    Resolve workspace name → ID. Mutates session on success.
    Raises ValueError if not found or ambiguous.
    """
    workspaces = list_alteryx_workspaces(session)

    # Exact match
    for ws in workspaces:
        if ws.get("name", "").lower() == workspace_name.lower():
            session.workspace_id   = str(ws["id"])
            session.workspace_name = ws["name"]
            session.custom_url     = ws.get("custom_url")
            return session.workspace_id

    # Partial match
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
            f"Matches: {[ws['name'] for ws in matches]}. Use the full exact name."
        )
    else:
        available = [ws.get("name") for ws in workspaces]
        raise ValueError(
            f"No workspace found matching '{workspace_name}'. "
            f"Available workspaces: {available}"
        )


# ── Entry point ──────────────────────────────────────────────────────────────

def list_alteryx_workflows(session: AlteryxSession, workspace_id: Optional[str] = None) -> list[dict]:
    """
    Fetch Alteryx Designer Cloud workflows using the working service endpoint
    from the Sam accelerator, with a workspace-scoped variant as fallback.
    """
    print(f"\nFetching Designer Cloud workflows...")

    endpoints: list[tuple[str, Optional[dict]]] = [
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", None),
        (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", {"limit": 100}),
    ]
    if workspace_id:
        endpoints.append(
            (f"{ALTERYX_BASE_URL}/svc-workflow/api/v1/workflows", {"workspaceId": workspace_id})
        )

    last_error: Optional[Exception] = None
    for endpoint, params in endpoints:
        try:
            print(f"  Trying: {endpoint}")
            data = _get_with_refresh(endpoint, session, params=params)
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
            print(f"  Successfully fetched {len(workflows)} workflow(s)")
            return [item for item in workflows if isinstance(item, dict)]
        except Exception as exc:
            print(f"  Failed: {type(exc).__name__}: {str(exc)[:140]}")
            last_error = exc

    raise ValueError(
        "Unable to fetch Designer Cloud workflows from svc-workflow. "
        f"Last error: {last_error}. Ensure the OAuth token has Designer Cloud access."
    )


def create_alteryx_session(
    access_token: str,
    workspace_name: str,
    refresh_token: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> AlteryxSession:
    """
    Build and validate an AlteryxSession.

    Token resolution order:
      access_token  → UI value → ALTERYX_ACCESS_TOKEN in .env → ""
      refresh_token → UI value → ALTERYX_REFRESH_TOKEN in .env → None
    """
    # BUG 3 FIX: env fallback was commented out in the original code
    resolved_access  = access_token  or os.getenv("ALTERYX_ACCESS_TOKEN", "")
    resolved_refresh = refresh_token or os.getenv("ALTERYX_REFRESH_TOKEN")

    if not resolved_access and not resolved_refresh:
        raise ValueError(
            "No Alteryx credentials provided. "
            "Provide an access token and refresh token from Alteryx One."
        )

    session = AlteryxSession(
        access_token=resolved_access,
        refresh_token=resolved_refresh,
    )

    get_workspace_id_by_name(session, workspace_name)
    persist_alteryx_tokens(session.access_token, session.refresh_token)
    return session
