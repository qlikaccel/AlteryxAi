from pydantic import BaseModel
from typing import Optional


class AlteryxAuthRequest(BaseModel):
    workspace_name: str  # User types this in the UI to confirm correct workspace


class AlteryxAuthResponse(BaseModel):
    status: str
    workspace_name: str
    workspace_id: str   # Keep as str — consistent across all files
    access_token: str
    refresh_token: str
