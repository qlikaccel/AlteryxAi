


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json
import os
import logging
from urllib.parse import urlparse
from typing import Optional
import sys

# Import the Alteryx session manager
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.services.alteryx_session_manager import get_alteryx_session_manager

router = APIRouter()
logger = logging.getLogger(__name__)

# Load users.json
# Go up from app/schemas to backend root
BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
USERS_FILE = os.path.join(BACKEND_ROOT, "users.json")

with open(USERS_FILE, "r") as f:
    USERS = json.load(f)


class LoginPayload(BaseModel):
    tenant_url: str
    connect_as_user: bool
    username: str
    password: str


class AlteryxLoginPayload(BaseModel):
    base_url: str


def normalize_tenant_url(url: str) -> str:
    """Normalize a tenant URL to scheme://host (ignore path, query, and trailing slash)."""
    value = (url or "").strip()
    if not value:
        return ""

    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"

    parsed = urlparse(value)
    scheme = parsed.scheme or "https"
    host = (parsed.netloc or parsed.path or "").lower()

    if host.startswith("www."):
        host = host[4:]

    return f"{scheme}://{host}".rstrip("/")

@router.post("/validate-login")
def validate_login(payload: LoginPayload):

    # 1️⃣ Checkbox
    if not payload.connect_as_user:
        raise HTTPException(
            status_code=400,
            detail="Please enable 'Connect as User'"
        )

    # 2️⃣ Username exists?
    if payload.username not in USERS:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    user = USERS[payload.username]

    # 3️⃣ Password check
    if payload.password != user["password"]:
        raise HTTPException(
            status_code=401,
            detail="Invalid username or password"
        )

    # 4️⃣ Tenant URL match (normalized)
    input_url = normalize_tenant_url(payload.tenant_url)
    expected_url = normalize_tenant_url(user["tenant"])
    
    if input_url != expected_url:
        raise HTTPException(
            status_code=400,
            detail=f"Tenant URL mismatch. Expected: {expected_url}"
        )

    return {
        "success": True,
        "message": "Login successful"
    }


@router.post("/alteryx-login")
def alteryx_login(payload: AlteryxLoginPayload):
    """
    Authenticate with Alteryx Cloud using fixed credentials.
    
    This endpoint:
    1. Takes an Alteryx Cloud base URL
    2. Authenticates with fixed hardcoded credentials
    3. Stores the session cookies for future API calls
    4. Returns success if login works
    
    Args:
        payload: Contains base_url (e.g., https://us1.alteryxcloud.com)
        
    Returns:
        success: True if authentication succeeded
        message: Status message
        base_url: The Alteryx Cloud base URL
        username: The username used for authentication
    """
    try:
        # Get base URL from payload
        base_url = payload.base_url.strip()
        
        if not base_url:
            raise HTTPException(
                status_code=400,
                detail="Alteryx Cloud base URL is required"
            )
        
        # Validate URL format
        if not (base_url.startswith("https://") or base_url.startswith("http://")):
            raise HTTPException(
                status_code=400,
                detail="URL must start with https:// or http://"
            )
        
        # Get fixed credentials from users.json
        if "accelerators@sorim.ai" not in USERS:
            logger.error("Alteryx credentials not found in users.json")
            raise HTTPException(
                status_code=500,
                detail="Service configuration error: Alteryx credentials not configured"
            )
        
        user_config = USERS["accelerators@sorim.ai"]
        username = "accelerators@sorim.ai"
        password = user_config["password"]
        api_token = user_config.get("alteryx_api_token")  # Get API token if available
        
        logger.info(f"Authenticating with fixed credentials: {username}")
        logger.info(f"API token available: {bool(api_token)}")
        
        # Get Alteryx session manager and authenticate
        session_manager = get_alteryx_session_manager()
        
        result = session_manager.authenticate(
            base_url=base_url,
            username=username,
            password=password,
            api_token=api_token  # Pass API token
        )
        
        logger.info(f"Authentication result: {result}")
        logger.info(f"Successfully authenticated to Alteryx Cloud: {base_url}")
        
        return {
            "success": True,
            "message": f"Successfully authenticated to Alteryx Cloud at {base_url}",
            "base_url": base_url,
            "username": username
        }
        
    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(
            status_code=400,
            detail=str(e)
        )
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Alteryx authentication failed: {error_msg}")
        raise HTTPException(
            status_code=401,
            detail=f"Alteryx authentication failed: {error_msg}"
        )
