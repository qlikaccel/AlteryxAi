# qlikAI-backend/schemas/alteryx_schemas.py
# Add these to your existing schemas file or import from here

from pydantic import BaseModel, Field
from typing import Optional


class AlteryxAuthRequest(BaseModel):
    """Input from the user on the connection config screen."""
    access_token: str = Field(..., description="OAuth2 Access Token from Alteryx One → OAuth 2.0 API Tokens")
    refresh_token: Optional[str] = Field(None, description="Optional — enables auto-renewal when access token expires")
    workspace_name: str = Field(..., description="Workspace name, e.g. sorim-alteryx-trial-2hcg")


class AlteryxAuthResponse(BaseModel):
    """Returned to frontend after successful validation."""
    status: str                          # "authenticated"
    workspace_name: str
    workspace_id: str
    access_token: str                    # may be a refreshed token — frontend should update its stored copy
    refresh_token: Optional[str] = None  # echo back so frontend can persist it
