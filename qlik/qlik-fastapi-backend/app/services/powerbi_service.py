import os
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Load env from this directory
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

PBI_TENANT_ID = os.getenv("POWERBI_TENANT_ID")
PBI_CLIENT_ID = os.getenv("POWERBI_CLIENT_ID")
PBI_CLIENT_SECRET = os.getenv("POWERBI_CLIENT_SECRET")
PBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID")

AUTH_URL = f"https://login.microsoftonline.com/{PBI_TENANT_ID}/oauth2/v2.0/token"
PBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"

class PowerBIAuthError(Exception):
    pass

class PowerBIService:
    def __init__(self,
                 tenant_id: Optional[str] = None,
                 client_id: Optional[str] = None,
                 client_secret: Optional[str] = None,
                 workspace_id: Optional[str] = None) -> None:
        self.tenant_id = tenant_id or PBI_TENANT_ID
        self.client_id = client_id or PBI_CLIENT_ID
        self.client_secret = client_secret or PBI_CLIENT_SECRET
        # Allow empty workspace_id (means use "My Workspace")
        self.workspace_id = workspace_id if workspace_id is not None else PBI_WORKSPACE_ID
        self.use_personal_workspace = not self.workspace_id  # True if workspace_id is empty

        missing = [k for k, v in {
            "POWERBI_TENANT_ID": self.tenant_id,
            "POWERBI_CLIENT_ID": self.client_id,
            "POWERBI_CLIENT_SECRET": self.client_secret,
        }.items() if not v]
        if missing:
            raise PowerBIAuthError(f"Missing required env vars: {', '.join(missing)}")

        self.token_info: Optional[Dict[str, Any]] = None

    # Acquire token (client credentials)
    def get_access_token(self) -> str:
        if self.token_info and self.token_info.get("expires_at", 0) > time.time() + 60:
            return self.token_info["access_token"]

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://analysis.windows.net/powerbi/api/.default",
            "grant_type": "client_credentials",
        }
        resp = requests.post(AUTH_URL, data=data, timeout=30)
        if resp.status_code != 200:
            raise PowerBIAuthError(f"Token request failed: {resp.status_code} {resp.text}")
        tok = resp.json()
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        self.token_info = tok
        return tok["access_token"]

    def _headers(self) -> Dict[str, str]:
        token = self.get_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    # Lookup dataset by name
    def find_dataset_by_name(self, dataset_name: str) -> Optional[Dict[str, Any]]:
        if self.use_personal_workspace:
            url = f"{PBI_API_ROOT}/datasets"
        else:
            url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets"
        r = requests.get(url, headers=self._headers(), timeout=30)
        r.raise_for_status()
        items = r.json().get("value") or r.json().get("datasets") or []
        for ds in items:
            if ds.get("name") == dataset_name:
                return ds
        return None

    # Create a Push dataset from schema
    def create_push_dataset(self, dataset_name: str, table_name: str, columns_schema: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = {
            "name": dataset_name,
            "defaultMode": "Push",
            "tables": [
                {
                    "name": table_name,
                    "columns": columns_schema
                }
            ]
        }
        if self.use_personal_workspace:
            url = f"{PBI_API_ROOT}/datasets?defaultRetentionPolicy=None"
        else:
            url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets?defaultRetentionPolicy=None"
        r = requests.post(url, headers=self._headers(), data=json.dumps(payload), timeout=60)
        if r.status_code not in (200, 201):
            raise Exception(f"Failed to create dataset: {r.status_code} {r.text}")
        return r.json()

    def find_next_available_name(self, base_name: str) -> str:
        """Find next available dataset name by appending _2, _3, etc."""
        existing = self.find_dataset_by_name(base_name)
        if not existing:
            return base_name
        
        # Name exists, find next available
        counter = 2
        while counter <= 100:
            new_name = f"{base_name}_{counter}"
            existing = self.find_dataset_by_name(new_name)
            if not existing:
                return new_name
            counter += 1
        
        raise Exception(f"Could not find available dataset name for {base_name}")

    def get_or_create_push_dataset(self, dataset_name: str, table_name: str, columns_schema: List[Dict[str, str]]) -> Tuple[str, bool]:
        """Always create a NEW dataset with auto-incremented name if needed"""
        # Find next available name to avoid conflicts
        unique_name = self.find_next_available_name(dataset_name)
        created = self.create_push_dataset(unique_name, table_name, columns_schema)
        return created.get("id"), True

    # Push rows to a push dataset table (handles large payloads by chunking)
    def push_rows(self, dataset_id: str, table_name: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"status_code": 200, "chunks": 0, "rows": 0}

        # Power BI has a per-request maximum row count (≈10000). Send in chunks to avoid 413 errors.
        CHUNK_SIZE = 10000
        total_rows = len(rows)
        chunks = 0

        if self.use_personal_workspace:
            base_url = f"{PBI_API_ROOT}/datasets/{dataset_id}/tables/{table_name}/rows"
        else:
            base_url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets/{dataset_id}/tables/{table_name}/rows"

        for start in range(0, total_rows, CHUNK_SIZE):
            end = min(start + CHUNK_SIZE, total_rows)
            chunk_rows = rows[start:end]
            payload = {"rows": chunk_rows}
            r = requests.post(base_url, headers=self._headers(), data=json.dumps(payload), timeout=120)
            chunks += 1
            if r.status_code not in (200, 202):
                raise Exception(f"Failed to push rows (chunk {chunks}, rows {start}-{end}): {r.status_code} {r.text}")

        return {"status_code": 200, "chunks": chunks, "rows": total_rows}

    def trigger_refresh(self, dataset_id: str) -> Dict[str, Any]:
        if self.use_personal_workspace:
            url = f"{PBI_API_ROOT}/datasets/{dataset_id}/refreshes"
        else:
            url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets/{dataset_id}/refreshes"
        r = requests.post(url, headers=self._headers(), timeout=60)
        if r.status_code not in (200, 202):
            raise Exception(f"Failed to trigger refresh: {r.status_code} {r.text}")
        return {"status_code": r.status_code}

# Utility to map pandas/CSV to Power BI data types

def infer_powerbi_type(value: Any) -> str:
    if value is None:
        return "string"
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Int64"
    if isinstance(value, float):
        return "Double"
    # very naive ISO datetime check
    if isinstance(value, str) and ("T" in value and ":" in value and "-" in value):
        return "DateTime"
    return "string"


def infer_schema_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not rows:
        return []
    first = rows[0]
    schema: List[Dict[str, str]] = []
    for col in first.keys():
        # scan a few values to pick a reasonable type
        col_type = "string"
        for r in rows[:50]:
            t = infer_powerbi_type(r.get(col))
            # prefer wider types if encountered
            if t in ("Double", "DateTime"):
                col_type = t
                break
            if t == "Int64" and col_type == "string":
                col_type = "Int64"
            if t == "Boolean" and col_type == "string":
                col_type = "Boolean"
        schema.append({"name": str(col), "dataType": col_type})
    return schema
