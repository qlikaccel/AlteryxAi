"""
Alteryx Workflows API Endpoint

Fetches workflows from Alteryx Cloud using stored session credentials.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import logging
import requests
from typing import Optional, Dict, Any, List

# Import the Alteryx session manager
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
from app.services.alteryx_session_manager import get_alteryx_session_manager

router = APIRouter()
logger = logging.getLogger(__name__)


class WorkflowItem(BaseModel):
    """Workflow data returned to frontend"""
    id: str
    name: str
    updatedAt: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = "available"
    toolCount: Optional[int] = None


def normalize_workflow(raw: Dict[str, Any]) -> Optional[WorkflowItem]:
    """
    Extract workflow fields from Alteryx API response.
    
    Alteryx API typically returns workflows with structure like:
    {
        "id": "workflow-id",
        "name": "Workflow Name",
        "updatedAt": "2024-01-15T10:30:00Z",
        "owner": "user@example.com",
        ...
    }
    """
    if not raw or not isinstance(raw, dict):
        return None
    
    # Extract required fields with fallbacks
    workflow_id = (
        raw.get("id") or
        raw.get("workflowId") or
        raw.get("uuid") or
        ""
    )
    
    if not workflow_id:
        return None
    
    name = (
        raw.get("name") or
        raw.get("workflowName") or
        raw.get("title") or
        "Unnamed Workflow"
    )
    
    updated_at = (
        raw.get("updatedAt") or
        raw.get("modifiedAt") or
        raw.get("lastModified") or
        None
    )
    
    owner = (
        raw.get("owner") or
        raw.get("ownerName") or
        raw.get("createdBy") or
        None
    )
    
    status = raw.get("status") or "available"
    tool_count = raw.get("toolCount")
    
    # Convert toolCount to int if possible
    if tool_count is not None and not isinstance(tool_count, int):
        try:
            tool_count = int(tool_count)
        except (ValueError, TypeError):
            tool_count = None
    
    return WorkflowItem(
        id=workflow_id,
        name=name,
        updatedAt=updated_at,
        owner=owner,
        status=status,
        toolCount=tool_count
    )


def extract_workflows(response_data: Any) -> List[Dict[str, Any]]:
    """
    Extract workflows array from various response formats.
    
    Alteryx may return workflows in different shapes:
    - { "workflows": [...] }
    - { "items": [...] }
    - { "data": [...] }
    - { "results": [...] }
    - { "payload": {...} } with nested structure
    - Direct array [...]
    
    Logs structure for debugging.
    """
    logger.debug(f"Extracting workflows from response type: {type(response_data)}")
    
    if isinstance(response_data, list):
        logger.debug(f"Response is direct array with {len(response_data)} items")
        return response_data
    
    if isinstance(response_data, dict):
        # Log all keys in the response
        logger.debug(f"Response is dict with keys: {list(response_data.keys())}")
        
        # Try common response wrappers in order of likelihood
        for key in ["workflows", "items", "data", "results", "payload"]:
            if key in response_data:
                value = response_data[key]
                
                # If it's a list, return it directly
                if isinstance(value, list):
                    logger.debug(f"Found workflows in '{key}' field: {len(value)} items")
                    return value
                
                # If it's a dict (like payload), try to extract workflows from it too
                if isinstance(value, dict):
                    logger.debug(f"Found dict in '{key}' field, checking for nested workflows...")
                    for nested_key in ["workflows", "items", "data"]:
                        if nested_key in value and isinstance(value[nested_key], list):
                            logger.debug(f"Found nested workflows in '{key}.{nested_key}': {len(value[nested_key])} items")
                            return value[nested_key]
        
        logger.warning(f"Could not find workflows in common keys. Available keys: {list(response_data.keys())}")
    
    logger.warning(f"Response is not a list or dict: {type(response_data)}")
    return []


@router.get("/workflows")
def get_workflows(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """
    Fetch workflows from Alteryx Cloud.
    
    This endpoint:
    1. Retrieves the stored Alteryx session from authentication
    2. Calls Alteryx API: GET /designer/api/workflows
    3. Extracts and normalizes workflow data
    4. Returns formatted workflow list
    
    Query Parameters:
    - limit: Maximum number of workflows to return (1-200, default 50)
    - offset: Starting position for pagination (default 0)
    
    Returns:
        {
            "success": true,
            "workflows": [...],
            "total": number,
            "limit": number,
            "offset": number
        }
    """
    try:
        # Get the Alteryx session manager
        session_manager = get_alteryx_session_manager()
        session = session_manager.get_session()
        base_url = session_manager.get_base_url()
        
        if not session or not session.cookies:
            logger.error("No active Alteryx session found")
            raise HTTPException(
                status_code=401,
                detail="Not authenticated with Alteryx Cloud. Please login on the Connect page."
            )
        
        if not base_url:
            logger.error("Alteryx Cloud base URL not found in session")
            raise HTTPException(
                status_code=500,
                detail="Alteryx Cloud base URL not configured. Please reconnect on the Connect page."
            )
        
        # Normalize base URL
        base_url = base_url.rstrip('/')
        
        # Get authorization headers (either Bearer token or Basic auth)
        auth_headers = session_manager.get_auth_headers()
        logger.info(f"Using auth mode: {'Bearer token' if 'Bearer' in str(auth_headers) else 'Basic auth' if auth_headers else 'No explicit auth'}")
        
        # Try different API endpoints (Alteryx Designer Cloud APIs)
        # The order is important - try most likely endpoints first
        possible_endpoints = [
            # Alteryx Designer Cloud primary endpoints
            f"{base_url}/api/v2/workflows",           # Most common for newer versions
            f"{base_url}/api/v1/workflows",           # Fallback to v1
            f"{base_url}/api/workflows",              # Generic API
            f"{base_url}/designer/api/workflows",     # Designer endpoint
            f"{base_url}/api/designer/workflows",
            
            # Additional fallbacks
            f"{base_url}/rest/workflows",
            f"{base_url}/workflows/list",
        ]
        
        workflows_url = None
        response = None
        last_error = None
        
        # Try each endpoint until one works
        for url in possible_endpoints:
            try:
                logger.info(f"Attempting to fetch workflows from: {url}")
                
                # Prepare headers with authorization
                headers = {
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
                headers.update(auth_headers)  # Add auth headers
                
                resp = session.get(
                    url,
                    timeout=10,
                    verify=True,
                    params={"limit": limit, "offset": offset},
                    headers=headers  # Include auth headers
                )
                
                logger.info(f"Response status from {url}: {resp.status_code}")
                
                # Check if response is JSON and successful
                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        logger.info(f"✓ Successfully retrieved JSON from {url}")
                        response = resp
                        workflows_url = url
                        break  # Found working endpoint
                    except Exception as json_err:
                        logger.warning(f"✗ Got {resp.status_code} but response is not JSON from {url}: {json_err}")
                        logger.debug(f"Response text: {resp.text[:200]}")
                        last_error = f"Not JSON: {json_err}"
                        continue
                elif resp.status_code == 401:
                    # Auth failed, try next endpoint
                    logger.warning(f"✗ 401 Unauthorized from {url}")
                    try:
                        error_detail = resp.json()
                        logger.debug(f"401 response: {error_detail}")
                    except:
                        logger.debug(f"401 response text: {resp.text[:200]}")
                    last_error = "Unauthorized"
                    continue
                elif resp.status_code == 403:
                    logger.warning(f"✗ 403 Forbidden from {url}")
                    last_error = "Forbidden"
                    continue
                else:
                    logger.warning(f"✗ HTTP {resp.status_code} from {url}")
                    logger.debug(f"Response: {resp.text[:200]}")
                    last_error = f"HTTP {resp.status_code}"
                    continue
                    
            except requests.Timeout:
                logger.warning(f"✗ Timeout from {url}")
                last_error = "Timeout"
                continue
            except Exception as e:
                logger.warning(f"✗ Error from {url}: {e}")
                last_error = str(e)
                continue
        
        # If no endpoint worked, handle error
        if response is None:
            logger.error(f"Failed to fetch workflows from any endpoint. Last error: {last_error}")
            logger.error(f"Attempted endpoints: {possible_endpoints}")
            
            # Provide detailed error messages based on auth status
            if last_error and ("unauthorized" in last_error.lower() or "forbidden" in last_error.lower()):
                raise HTTPException(
                    status_code=401,
                    detail=f"Authentication failed with Alteryx Cloud ({last_error}). Possible causes:\n"
                           f"1. Credentials in users.json don't match your Alteryx account\n"
                           f"2. Alteryx Cloud is not accessible from your network\n"
                           f"3. Invalid Alteryx base URL\n"
                           f"Please verify: username={session_manager._username}, base_url={base_url}"
                )
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to fetch workflows from Alteryx API(s). Last error: {last_error}\n"
                           f"Tried {len(possible_endpoints)} endpoints. Please check:\n"
                           f"1. Alteryx Cloud is reachable at {base_url}\n"
                           f"2. Your credentials are correct in users.json\n"
                           f"3. Your Alteryx account has proper permissions"
                )
        
        logger.info(f"Successfully using endpoint: {workflows_url}")
        
        # Parse response
        try:
            data = response.json()
        except Exception as json_err:
            logger.error(f"Failed to parse JSON response: {json_err}")
            logger.error(f"Response text: {response.text[:500]}")
            raise HTTPException(
                status_code=500,
                detail=f"Invalid JSON response from Alteryx: {str(json_err)}"
            )
        
        logger.info(f"Received response from Alteryx. Response type: {type(data)}")
        logger.debug(f"Response keys: {data.keys() if isinstance(data, dict) else 'not a dict'}")
        
        # Log full response for debugging (first 1000 chars)
        logger.debug(f"Full response preview: {str(data)[:1000]}")
        
        # Extract workflows from response
        raw_workflows = extract_workflows(data)
        logger.info(f"Found {len(raw_workflows)} workflows in response")
        
        if len(raw_workflows) == 0:
            logger.warning("No workflows found in Alteryx response. Response structure:")
            logger.warning(f"Response: {data}")
        
        # Normalize workflows
        workflows = []
        for idx, raw in enumerate(raw_workflows):
            logger.debug(f"Processing workflow {idx}: {raw}")
            normalized = normalize_workflow(raw)
            if normalized:
                workflows.append(normalized.dict())
            else:
                logger.warning(f"Failed to normalize workflow {idx}: {raw}")
        
        logger.info(f"Returning {len(workflows)} normalized workflows")
        
        return {
            "success": True,
            "workflows": workflows,
            "total": len(workflows),
            "limit": limit,
            "offset": offset
        }
        
    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error fetching workflows: {error_msg}", exc_info=True)
        
        # Log detailed debugging info
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Exception details: {error_msg}")
        
        # For development/testing, return mock data with error note
        # In production, this would be removed
        logger.warning("Returning mock workflows as fallback")
        mock_workflows = [
            {
                "id": "mock-wf-001",
                "name": "Sales Data Pipeline",
                "updatedAt": "2026-04-10T14:30:00Z",
                "owner": "admin@alteryx.com",
                "status": "available",
                "toolCount": 12
            },
            {
                "id": "mock-wf-002",
                "name": "Customer Analytics Workflow",
                "updatedAt": "2026-04-08T09:15:00Z",
                "owner": "analyst@alteryx.com",
                "status": "available",
                "toolCount": 8
            },
            {
                "id": "mock-wf-003",
                "name": "Data Quality Checker",
                "updatedAt": "2026-04-05T16:45:00Z",
                "owner": "data.team@alteryx.com",
                "status": "available",
                "toolCount": 5
            },
            {
                "id": "mock-wf-004",
                "name": "Inventory Management",
                "updatedAt": "2026-04-01T11:20:00Z",
                "owner": "ops@alteryx.com",
                "status": "available",
                "toolCount": 15
            },
        ]
        
        normalized_workflows = []
        for raw in mock_workflows:
            normalized = normalize_workflow(raw)
            if normalized:
                normalized_workflows.append(normalized.dict())
        
        return {
            "success": False,
            "workflows": normalized_workflows,
            "total": len(normalized_workflows),
            "limit": limit,
            "offset": offset,
            "error": error_msg,
            "note": f"Using mock workflows - Error: {error_msg}"
        }

