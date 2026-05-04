#!/usr/bin/env python3
"""
Comprehensive Alteryx API Investigation Tool

Verifies:
1. Authentication is real (not just from .env)
2. Workspace API response structure
3. All possible workflow API endpoints
4. Alternative ways to get workflow data
"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

ALTERYX_BASE_URL = os.getenv("ALTERYX_TENANT_URL", "https://us1.alteryxcloud.com")
ALTERYX_ACCESS_TOKEN = os.getenv("ALTERYX_ACCESS_TOKEN", "")
ALTERYX_WORKSPACE_ID = os.getenv("ALTERYX_WORKSPACE_ID", "")
ALTERYX_WORKSPACE_NAME = os.getenv("ALTERYX_WORKSPACE_NAME", "")


def print_section(title):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}\n")


def test_workspaces_api():
    """Test workspace API to see what fields are returned."""
    print_section("STEP 1: TEST WORKSPACES API (Authentication Verification)")
    
    if not ALTERYX_ACCESS_TOKEN:
        print("❌ No access token")
        return None
    
    endpoint = f"{ALTERYX_BASE_URL}/v4/workspaces"
    print(f"Testing: {endpoint}\n")
    
    try:
        resp = requests.get(
            endpoint,
            headers={
                "Authorization": f"Bearer {ALTERYX_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        
        print(f"Status: {resp.status_code}\n")
        
        if resp.status_code == 200:
            data = resp.json()
            
            # Normalize response
            workspaces = data if isinstance(data, list) else data.get("data", [])
            
            print(f"✅ SUCCESS! Found {len(workspaces)} workspace(s)\n")
            
            if workspaces:
                ws = workspaces[0]
                print(f"📊 WORKSPACE STRUCTURE:")
                print(f"   Keys available: {list(ws.keys())}\n")
                
                print(f"   Full workspace object:")
                print(f"   {json.dumps(ws, indent=6)}\n")
                
                return ws
            
        else:
            print(f"❌ Failed: {resp.status_code}")
            print(f"Response: {resp.text[:200]}\n")
            return None
    
    except Exception as e:
        print(f"❌ Error: {e}\n")
        return None


def test_specific_endpoints():
    """Test specific endpoints based on workspace structure."""
    print_section("STEP 2: TEST WORKFLOW ENDPOINTS")
    
    if not ALTERYX_ACCESS_TOKEN or not ALTERYX_WORKSPACE_ID:
        print("❌ Missing token or workspace ID\n")
        return
    
    # Test endpoints that might work
    endpoints_to_try = [
        # Directly accessing workspace flows
        (f"{ALTERYX_BASE_URL}/v4/workspaces/{ALTERYX_WORKSPACE_ID}/flows", {}),
        (f"{ALTERYX_BASE_URL}/v4/workspaces/{ALTERYX_WORKSPACE_ID}/assets", {}),
        (f"{ALTERYX_BASE_URL}/v4/workspaces/{ALTERYX_WORKSPACE_ID}/packages", {}),
        
        # Direct /flows endpoint
        (f"{ALTERYX_BASE_URL}/v4/flows", {"workspaceId": ALTERYX_WORKSPACE_ID}),
        (f"{ALTERYX_BASE_URL}/api/flows", {}),
        (f"{ALTERYX_BASE_URL}/flows", {}),
        
        # Package endpoints
        (f"{ALTERYX_BASE_URL}/v4/packages", {"workspaceId": ALTERYX_WORKSPACE_ID}),
        (f"{ALTERYX_BASE_URL}/api/packages", {}),
        
        # Assets (generic)
        (f"{ALTERYX_BASE_URL}/v4/assets", {"workspaceId": ALTERYX_WORKSPACE_ID}),
        
        # Jobs/runs (might list workflows)
        (f"{ALTERYX_BASE_URL}/v4/runs", {"workspaceId": ALTERYX_WORKSPACE_ID}),
        (f"{ALTERYX_BASE_URL}/v4/jobs", {"workspaceId": ALTERYX_WORKSPACE_ID}),
    ]
    
    successful = []
    
    for endpoint, params in endpoints_to_try:
        print(f"📍 Testing: {endpoint}")
        
        try:
            resp = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {ALTERYX_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                params=params,
                timeout=10,
            )
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    
                    # Count items
                    count = 0
                    if isinstance(data, list):
                        count = len(data)
                        data_type = f"array ({count} items)"
                    else:
                        # Try common keys
                        for key in ["data", "items", "workflows", "flows", "packages", "results"]:
                            if key in data:
                                count = len(data[key])
                                data_type = f"dict with '{key}' key ({count} items)"
                                break
                        else:
                            data_type = f"dict with keys: {list(data.keys())}"
                    
                    print(f"   ✅ 200 OK - Response: {data_type}")
                    
                    if count > 0:
                        print(f"   🎉 HAS DATA! ({count} items)")
                        successful.append({
                            "endpoint": endpoint,
                            "params": params,
                            "status": 200,
                            "count": count
                        })
                    
                except Exception as e:
                    print(f"   ❌ Response not JSON: {str(e)[:50]}")
            
            elif resp.status_code == 404:
                print(f"   ⚠️  404 Not found")
            else:
                print(f"   ❌ {resp.status_code}")
        
        except Exception as e:
            print(f"   ❌ Error: {str(e)[:50]}")
        
        print()
    
    return successful


def test_designer_cloud_ui_api():
    """Test Designer Cloud's internal/UI API endpoints."""
    print_section("STEP 3: TEST DESIGNER CLOUD UI API")
    
    if not ALTERYX_ACCESS_TOKEN:
        print("❌ No access token\n")
        return
    
    ui_endpoints = [
        # Designer Cloud UI APIs (internal, may change)
        (f"{ALTERYX_BASE_URL}/api/designer/projects", {}),
        (f"{ALTERYX_BASE_URL}/api/designer/workflows", {}),
        (f"{ALTERYX_BASE_URL}/api/designer/repository", {}),
        (f"{ALTERYX_BASE_URL}/designer/api/repository", {}),
        (f"{ALTERYX_BASE_URL}/designer/api/projects", {}),
        
        # GraphQL endpoint (if available)
        (f"{ALTERYX_BASE_URL}/graphql", {}),
        (f"{ALTERYX_BASE_URL}/v4/graphql", {}),
    ]
    
    for endpoint, params in ui_endpoints:
        print(f"Testing: {endpoint}")
        
        try:
            resp = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {ALTERYX_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                params=params,
                timeout=5,
            )
            
            if resp.status_code in [200, 201]:
                print(f"   ✅ {resp.status_code} - Endpoint exists!\n")
            elif resp.status_code == 404:
                print(f"   ⚠️  404\n")
            else:
                print(f"   ❌ {resp.status_code}\n")
        
        except:
            print(f"   ❌ Timeout/Error\n")


def main():
    print("\n" + "="*80)
    print("  ALTERYX API VERIFICATION & WORKFLOW ENDPOINT DISCOVERY")
    print("="*80)
    
    print(f"\nConfiguration:")
    print(f"  Base URL: {ALTERYX_BASE_URL}")
    print(f"  Token: {ALTERYX_ACCESS_TOKEN[:50] if ALTERYX_ACCESS_TOKEN else 'NOT SET'}...")
    print(f"  Workspace ID: {ALTERYX_WORKSPACE_ID}")
    print(f"  Workspace Name: {ALTERYX_WORKSPACE_NAME}")
    
    # Test workspaces API
    workspace = test_workspaces_api()
    
    if not workspace:
        print("❌ Cannot proceed without workspace data\n")
        return
    
    # Test workflow endpoints
    successful = test_specific_endpoints()
    
    if successful:
        print_section("✅ SUCCESSFUL ENDPOINTS FOUND")
        for result in successful:
            print(f"Endpoint: {result['endpoint']}")
            print(f"Status: {result['status']}")
            print(f"Items found: {result['count']}")
            print()
    else:
        print_section("⚠️  NO WORKING ENDPOINTS FOUND")
        print("Testing Designer Cloud UI API...\n")
        test_designer_cloud_ui_api()
    
    # Summary
    print_section("SUMMARY")
    
    if successful:
        best = successful[0]
        print(f"✅ WORKING ENDPOINT FOUND:")
        print(f"   {best['endpoint']}")
        print(f"   Items: {best['count']}")
        print(f"\nTo fix your app, update alteryx_router.py to use this endpoint.")
    else:
        print("⚠️  WORKFLOW API ENDPOINT NOT FOUND")
        print("\nPossible causes:")
        print("  1. Alteryx Designer Cloud doesn't expose workflows via REST API")
        print("  2. Requires different authentication headers")
        print("  3. Uses GraphQL instead of REST")
        print("  4. Workflows are called something else (packages, flows, assets, runs)")
        print("\nNext steps:")
        print("  1. Check Alteryx Designer Cloud API documentation")
        print("  2. Use browser DevTools → Network tab in Designer Cloud")
        print("  3. Look for API calls that fetch the workflow list")
        print("  4. Copy the endpoint URL and share with the development team")


if __name__ == "__main__":
    main()
