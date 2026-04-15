"""
Alteryx Designer Workflows Endpoint

Fetches workflows from Alteryx Designer using HTML parsing approach.
Falls back to API if HTML parsing fails.

Supports both:
1. HTML parsing (primary - /designer/ page scraping)
2. API endpoints (fallback)
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import logging
from typing import Optional, Dict, Any, List

# Import session manager and HTML parser
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from app.services.alteryx_session_manager import get_alteryx_session_manager
from app.services.html_workflow_parser import get_html_workflow_parser

router = APIRouter()
logger = logging.getLogger(__name__)


class WorkflowItem(BaseModel):
    """Workflow data returned to frontend"""
    id: str
    name: str
    source: str = "html_parser"
    status: str = "available"


@router.get("/workflows")
def get_workflows_html(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    use_api: bool = Query(False, description="Force API mode instead of HTML parsing")
) -> Dict[str, Any]:
    """
    Fetch workflows from Alteryx Designer.
    
    Default method: HTML parsing of /designer/ page
    - Uses authenticated session cookies
    - Parses HTML with BeautifulSoup
    - Extracts workflow names matching patterns
    
    Fallback: API endpoints (if use_api=true or HTML fails)
    
    Query Parameters:
    - limit: Maximum number of workflows to return (1-200, default 50)
    - offset: Starting position for pagination (default 0)
    - use_api: Force API mode instead of HTML parsing (default false)
    
    Returns:
        {
            "success": true,
            "workflows": [...],
            "total": number,
            "limit": number,
            "offset": number,
            "method": "html_parser" or "api"
        }
    """
    try:
        # Get session manager
        session_manager = get_alteryx_session_manager()
        session = session_manager.get_session()
        base_url = session_manager.get_base_url()
        
        logger.info(f"Workflows request - API mode: {use_api}")
        
        if not base_url:
            logger.error("Alteryx Cloud base URL not found")
            raise HTTPException(
                status_code=500,
                detail="Alteryx Cloud base URL not configured. Please reconnect on the Connect page."
            )
        
        base_url = base_url.rstrip('/')
        workflows_list = []
        method_used = "unknown"
        
        # Try HTML parsing first (unless forced to use API)
        if not use_api:
            logger.info("Attempting HTML parsing method...")
            
            try:
                parser = get_html_workflow_parser()
                workflows_list = parser.parse_designer_page(
                    session=session,
                    base_url=base_url,
                    timeout=10
                )
                
                if workflows_list:
                    logger.info(f"✓ Successfully extracted {len(workflows_list)} workflows via HTML parsing")
                    method_used = "html_parser"
                    
                    # Apply pagination
                    total = len(workflows_list)
                    start_idx = offset
                    end_idx = offset + limit
                    paginated = workflows_list[start_idx:end_idx]
                    
                    return {
                        "success": True,
                        "workflows": paginated,
                        "total": total,
                        "limit": limit,
                        "offset": offset,
                        "method": method_used,
                        "message": f"Extracted {total} workflows from Alteryx Designer"
                    }
                else:
                    logger.warning("HTML parsing returned no workflows. Trying fallback methods...")
            
            except Exception as e:
                logger.warning(f"HTML parsing failed: {e}. Trying fallback methods...")
        
        # Fallback 1: Try API endpoints
        logger.info("Attempting API fallback method...")
        workflows_list = _try_api_endpoints(session, base_url)
        
        if workflows_list:
            method_used = "api"
            logger.info(f"✓ Successfully extracted {len(workflows_list)} workflows via API")
        else:
            # Fallback 2: Return authentication check
            logger.error("Both HTML and API methods failed")
            raise HTTPException(
                status_code=503,
                detail="Unable to fetch workflows from Alteryx Cloud. "
                       "Please ensure you are logged in and have access to workflows.",
                headers={"X-Method-Tried": "html_parser, api"}
            )
        
        # Apply pagination
        total = len(workflows_list)
        start_idx = offset
        end_idx = offset + limit
        paginated = workflows_list[start_idx:end_idx]
        
        return {
            "success": True,
            "workflows": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "method": method_used,
            "message": f"Extracted {total} workflows using {method_used}"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_workflows: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


def _try_api_endpoints(session, base_url: str) -> List[Dict[str, Any]]:
    """
    Try various API endpoints to fetch workflows.
    
    Args:
        session: Authenticated requests session
        base_url: Alteryx Cloud base URL
        
    Returns:
        List of workflow dictionaries or empty list
    """
    import requests
    
    possible_endpoints = [
        f"{base_url}/api/v4/workflows",
        f"{base_url}/api/v3/workflows",
        f"{base_url}/api/v2/workflows",
        f"{base_url}/api/v1/workflows",
        f"{base_url}/api/workflows",
        f"{base_url}/designer/api/workflows",
    ]
    
    for url in possible_endpoints:
        try:
            logger.debug(f"Trying API endpoint: {url}")
            
            response = session.get(
                url,
                timeout=10,
                verify=True,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code == 200:
                try:
                    data = response.json()
                    workflows = _extract_api_workflows(data)
                    if workflows:
                        logger.info(f"✓ Got workflows from {url}")
                        return workflows
                except Exception as e:
                    logger.debug(f"Could not parse JSON from {url}: {e}")
                    continue
        
        except (requests.Timeout, requests.ConnectionError):
            logger.debug(f"Connection failed to {url}")
            continue
        except Exception as e:
            logger.debug(f"Error with {url}: {e}")
            continue
    
    return []


def _extract_api_workflows(data: Any) -> List[Dict[str, Any]]:
    """
    Extract workflows from API response.
    
    Args:
        data: API response data
        
    Returns:
        List of workflow dictionaries
    """
    workflows = []
    
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                workflow = {
                    "id": item.get("id", item.get("workflowId", "")),
                    "name": item.get("name", item.get("workflowName", "")),
                    "source": "api"
                }
                if workflow.get("id") and workflow.get("name"):
                    workflows.append(workflow)
    
    elif isinstance(data, dict):
        # Try common response wrappers
        for key in ["workflows", "items", "data", "results", "payload"]:
            if key in data:
                value = data[key]
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            workflow = {
                                "id": item.get("id", item.get("workflowId", "")),
                                "name": item.get("name", item.get("workflowName", "")),
                                "source": "api"
                            }
                            if workflow.get("id") and workflow.get("name"):
                                workflows.append(workflow)
                    if workflows:
                        break
    
    return workflows


@router.get("/workflows/html")
def get_workflows_html_only(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """
    Fetch workflows using only HTML parsing (no API fallback).
    
    Useful for testing HTML parser independently.
    
    Returns:
        {
            "success": true,
            "workflows": [...],
            "method": "html_parser",
            "parse_details": {...}
        }
    """
    try:
        session_manager = get_alteryx_session_manager()
        session = session_manager.get_session()
        base_url = session_manager.get_base_url()
        
        if not base_url:
            raise HTTPException(
                status_code=500,
                detail="Alteryx Cloud base URL not configured"
            )
        
        parser = get_html_workflow_parser()
        workflows = parser.parse_designer_page(
            session=session,
            base_url=base_url.rstrip('/'),
            timeout=10
        )
        
        total = len(workflows)
        start_idx = offset
        end_idx = offset + limit
        paginated = workflows[start_idx:end_idx]
        
        return {
            "success": True,
            "workflows": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "method": "html_parser",
            "parse_details": {
                "source": "Alteryx Designer HTML page",
                "parser": "BeautifulSoup4",
                "extraction_method": "Multi-strategy (text, attributes, script content)"
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"HTML-only workflow fetch failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"HTML parsing failed: {str(e)}"
        )


@router.get("/discovery")
def get_discovery_page() -> Dict[str, Any]:
    """
    Get all discovered workflows for the Discovery page.
    
    Combines both HTML-parsed and API-fetched workflows.
    Removes duplicates by ID.
    
    Returns:
        {
            "success": true,
            "total_workflows": number,
            "workflows": [
                {
                    "id": "workflow-id",
                    "name": "Workflow Name",
                    "source": "html_parser" or "api",
                    "status": "available"
                },
                ...
            ],
            "summary": {
                "html_parser_count": number,
                "api_count": number
            }
        }
    """
    try:
        session_manager = get_alteryx_session_manager()
        session = session_manager.get_session()
        base_url = session_manager.get_base_url()
        
        if not base_url:
            raise HTTPException(
                status_code=500,
                detail="Not authenticated with Alteryx"
            )
        
        base_url = base_url.rstrip('/')
        all_workflows = {}  # Use dict to deduplicate by ID
        sources_found = {}
        
        # Try HTML parsing first
        try:
            logger.info("Discovery: Fetching workflows via HTML parsing...")
            parser = get_html_workflow_parser()
            html_workflows = parser.parse_designer_page(
                session=session,
                base_url=base_url,
                timeout=10
            )
            
            for wf in html_workflows:
                all_workflows[wf['id']] = wf
            
            sources_found['html_parser'] = len(html_workflows)
            logger.info(f"Discovery: Got {len(html_workflows)} workflows from HTML")
        
        except Exception as e:
            logger.warning(f"Discovery: HTML parsing failed: {e}")
            sources_found['html_parser'] = 0
        
        # Try API as supplementary source
        try:
            logger.info("Discovery: Fetching workflows via API...")
            api_workflows = _try_api_endpoints(session, base_url)
            
            for wf in api_workflows:
                if wf['id'] not in all_workflows:
                    all_workflows[wf['id']] = wf
            
            sources_found['api'] = len(api_workflows)
            logger.info(f"Discovery: Got {len(api_workflows)} workflows from API")
        
        except Exception as e:
            logger.warning(f"Discovery: API fetch failed: {e}")
            sources_found['api'] = 0
        
        # Convert to sorted list
        workflows_list = sorted(
            all_workflows.values(),
            key=lambda x: x.get('name', '').lower()
        )
        
        return {
            "success": True,
            "total_workflows": len(workflows_list),
            "workflows": workflows_list,
            "summary": sources_found,
            "message": f"Discovered {len(workflows_list)} total workflows"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Discovery page error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Discovery failed: {str(e)}"
        )
