"""
Power BI Service - Delegated Authentication Version
Uses user delegation (not service principal)
"""

import os
import json
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

# Load env from this directory
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=ENV_PATH)

PBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
PBI_WORKSPACE_ID = os.getenv("POWERBI_WORKSPACE_ID", "7219790d-ee43-4137-b293-e3c477a754f0")


class PowerBIAuthError(Exception):
    pass


class PowerBIService:
    """Power BI Service using delegated authentication (user token)"""
    
    def __init__(self, access_token: str, workspace_id: Optional[str] = None) -> None:
        """
        Initialize with user access token
        Args:
            access_token: Valid Power BI API token
            workspace_id: Optional workspace ID (defaults to env var)
        """
        self.access_token = access_token
        self.workspace_id = workspace_id or PBI_WORKSPACE_ID
        self.use_personal_workspace = not self.workspace_id
        
        if not self.access_token:
            raise PowerBIAuthError("Access token is required")

    def _headers(self) -> Dict[str, str]:
        """Get request headers with authorization"""
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def find_dataset_by_name(self, dataset_name: str) -> Optional[Dict[str, Any]]:
        """Find a dataset by name in the workspace"""
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

    def create_push_dataset(self, dataset_name: str, table_name: str, columns_schema: List[Dict[str, str]]) -> Dict[str, Any]:
        """Create a new push dataset with specified schema"""
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

    def delete_dataset(self, dataset_id: str) -> bool:
        """Delete a dataset by ID"""
        try:
            if self.use_personal_workspace:
                url = f"{PBI_API_ROOT}/datasets/{dataset_id}"
            else:
                url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets/{dataset_id}"
            
            r = requests.delete(url, headers=self._headers(), timeout=30)
            return r.status_code in (200, 204)
        except Exception as e:
            print(f"Warning: Failed to delete dataset {dataset_id}: {e}")
            return False

    def get_dataset_columns(self, dataset_id: str, table_name: str) -> List[str]:
        """Get column names from existing dataset table"""
        try:
            if self.use_personal_workspace:
                url = f"{PBI_API_ROOT}/datasets/{dataset_id}/tables/{table_name}"
            else:
                url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets/{dataset_id}/tables/{table_name}"
            
            r = requests.get(url, headers=self._headers(), timeout=30)
            r.raise_for_status()
            columns = r.json().get("columns", [])
            return [col.get("name") for col in columns]
        except Exception as e:
            print(f"Warning: Failed to get dataset columns: {e}")
            return []

    def schemas_match(self, new_schema: List[Dict[str, str]], existing_columns: List[str]) -> bool:
        """Check if new schema matches existing columns"""
        new_column_names = {col["name"] for col in new_schema}
        existing_column_names = set(existing_columns)
        return new_column_names == existing_column_names

    def get_or_create_push_dataset(self, dataset_name: str, table_name: str, columns_schema: List[Dict[str, str]]) -> Tuple[str, bool]:
        """Get existing dataset or create new one - recreate if schema doesn't match"""
        existing = self.find_dataset_by_name(dataset_name)
        
        if existing:
            existing_id = existing.get("id")
            existing_columns = self.get_dataset_columns(existing_id, table_name)
            
            # Check if schema matches
            if self.schemas_match(columns_schema, existing_columns):
                print(f"✓ Using existing dataset with matching schema")
                return existing_id, False
            else:
                # Schema mismatch - delete old and create new
                print(f"⚠ Schema mismatch detected!")
                print(f"  Old columns: {existing_columns}")
                new_column_names = [col["name"] for col in columns_schema]
                print(f"  New columns: {new_column_names}")
                print(f"  Deleting old dataset and creating new one...")
                
                self.delete_dataset(existing_id)
                created = self.create_push_dataset(dataset_name, table_name, columns_schema)
                return created.get("id"), True
        
        # No existing dataset - create new one
        created = self.create_push_dataset(dataset_name, table_name, columns_schema)
        return created.get("id"), True

    def push_rows(self, dataset_id: str, table_name: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Push data rows to a dataset table (chunked to respect Power BI per-request limits)."""
        if not rows:
            return {"status_code": 200, "chunks": 0, "rows": 0}

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
            print(f"✓ Pushed chunk {chunks}: rows {start + 1}-{end} to dataset {dataset_id}")

        return {"status_code": 200, "chunks": chunks, "rows": total_rows}

    def trigger_refresh(self, dataset_id: str) -> Dict[str, Any]:
        """Trigger dataset refresh"""
        if self.use_personal_workspace:
            url = f"{PBI_API_ROOT}/datasets/{dataset_id}/refreshes"
        else:
            url = f"{PBI_API_ROOT}/groups/{self.workspace_id}/datasets/{dataset_id}/refreshes"
        
        r = requests.post(url, headers=self._headers(), timeout=60)
        if r.status_code not in (200, 202):
            raise Exception(f"Failed to trigger refresh: {r.status_code} {r.text}")
        return {"status_code": r.status_code}


def infer_powerbi_type(value: Any) -> str:
    """Infer Power BI data type from Python value"""
    if value is None:
        return "string"
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Int64"
    if isinstance(value, float):
        return "Double"
    if isinstance(value, str) and ("T" in value and ":" in value and "-" in value):
        return "DateTime"
    return "string"


def infer_schema_from_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Infer Power BI schema from data rows"""
    if not rows:
        return []
    
    first = rows[0]
    schema: List[Dict[str, str]] = []
    
    for col in first.keys():
        col_type = "string"
        for r in rows[:50]:
            t = infer_powerbi_type(r.get(col))
            if t in ("Double", "DateTime"):
                col_type = t
                break
            if t == "Int64" and col_type == "string":
                col_type = "Int64"
            if t == "Boolean" and col_type == "string":
                col_type = "Boolean"
        
        schema.append({"name": str(col), "dataType": col_type})
    
    return schema
