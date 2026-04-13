


from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import json
import os
from urllib.parse import urlparse

router = APIRouter()

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
