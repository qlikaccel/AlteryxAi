#!/usr/bin/env python3
"""
Find the correct Alteryx Cloud workflow API endpoint
Helps diagnose which endpoint pattern works for your instance
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

ALTERYX_BASE_URL = os.getenv("ALTERYX_TENANT_URL", "https://us1.alteryxcloud.com")
ALTERYX_ACCESS_TOKEN = os.getenv("ALTERYX_ACCESS_TOKEN", "")
ALTERYX_REFRESH_TOKEN = os.getenv("ALTERYX_REFRESH_TOKEN", "")
ALTERYX_WORKSPACE_ID = os.getenv("ALTERYX_WORKSPACE_ID", "")


def test_endpoint(endpoint: str, params: dict = None, token: str = None) -> dict:
    """Test a single endpoint and return results."""
    if not token:
        token = ALTERYX_ACCESS_TOKEN
    
    if not token:
        return {"endpoint": endpoint, "status": "error", "message": "No token"}
    
    try:
        resp = requests.get(
            endpoint,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            params=params,
            timeout=10,
        )
        
        result = {
            "endpoint": endpoint,
            "status": resp.status_code,
            "success": resp.status_code == 200,
        }
        
        if resp.status_code == 200:
            try:
                data = resp.json()
                result["data_type"] = type(data).__name__
                
                # Count items
                if isinstance(data, list):
                    result["count"] = len(data)
                    result["sample_keys"] = list(data[0].keys()) if data else []
                elif isinstance(data, dict):
                    if "workflows" in data:
                        result["count"] = len(data["workflows"])
                        result["sample_keys"] = list(data["workflows"][0].keys()) if data["workflows"] else []
                    elif "data" in data:
                        result["count"] = len(data["data"])
                        result["sample_keys"] = list(data["data"][0].keys()) if data["data"] else []
                    else:
                        result["dict_keys"] = list(data.keys())
                        result["count"] = 0
                else:
                    result["response"] = str(data)[:100]
            except Exception as e:
                result["parse_error"] = str(e)
                result["response_sample"] = resp.text[:200]
        else:
            try:
                error_data = resp.json()
                result["error"] = error_data.get("message") or str(error_data)
            except:
                result["error"] = resp.text[:200]
        
        return result
    except requests.Timeout:
        return {"endpoint": endpoint, "status": "timeout", "message": "Request timed out"}
    except Exception as e:
        return {"endpoint": endpoint, "status": "error", "message": str(e)}


def main():
    print("\n" + "="*80)
    print("  ALTERYX CLOUD WORKFLOW API ENDPOINT DISCOVERY")
    print("="*80 + "\n")
    
    if not ALTERYX_WORKSPACE_ID:
        print("❌ ALTERYX_WORKSPACE_ID not set in .env")
        return
    
    if not ALTERYX_ACCESS_TOKEN:
        print("❌ ALTERYX_ACCESS_TOKEN not set in .env")
        return
    
    print(f"Testing with workspace ID: {ALTERYX_WORKSPACE_ID}\n")
    
    # Define endpoints to test with different variations
    test_cases = [
        # Designer Cloud v1 API (most likely)
        (f"{ALTERYX_BASE_URL}/api/v1/workflows/list", {}),
        (f"{ALTERYX_BASE_URL}/api/v1/workflows", {}),
        (f"{ALTERYX_BASE_URL}/designer/v1/workflows", {}),
        (f"{ALTERYX_BASE_URL}/designer/api/v1/workflows", {}),
        
        # Global workflows endpoint
        (f"{ALTERYX_BASE_URL}/api/workflows", {}),
        (f"{ALTERYX_BASE_URL}/workflows", {}),
        
        # v2 API
        (f"{ALTERYX_BASE_URL}/api/v2/workflows", {}),
        
        # v1 variants
        (f"{ALTERYX_BASE_URL}/v1/workflows", {}),
        
        # Workspace-specific v1
        (f"{ALTERYX_BASE_URL}/v1/workspaces/{ALTERYX_WORKSPACE_ID}/workflows", {}),
        (f"{ALTERYX_BASE_URL}/api/v1/workspaces/{ALTERYX_WORKSPACE_ID}/workflows", {}),
        
        # With workspace ID in header as backup
        (f"{ALTERYX_BASE_URL}/api/v4/workflows", {"workspaceId": ALTERYX_WORKSPACE_ID}),
        (f"{ALTERYX_BASE_URL}/v4/workflows", {"workspaceId": ALTERYX_WORKSPACE_ID}),
    ]
    
    results = []
    print("Testing endpoints...\n")
    
    for endpoint, params in test_cases:
        print(f"🧪 Testing: {endpoint}")
        result = test_endpoint(endpoint, params)
        results.append(result)
        
        if result.get("success"):
            print(f"   ✅ SUCCESS! Status {result['status']}")
            if result.get("count") is not None:
                print(f"      Found {result['count']} workflows")
            if result.get("sample_keys"):
                print(f"      Fields: {', '.join(result['sample_keys'][:5])}")
        else:
            status = result.get("status", "error")
            error = result.get("error", result.get("message", "Unknown error"))
            print(f"   ❌ {status}: {error}")
        print()
    
    # Summary
    successful = [r for r in results if r.get("success")]
    
    print("\n" + "="*80)
    print("  RESULTS SUMMARY")
    print("="*80 + "\n")
    
    if successful:
        print(f"✅ Found {len(successful)} working endpoint(s)!\n")
        for result in successful:
            print(f"   WORKING ENDPOINT:")
            print(f"   {result['endpoint']}")
            if result.get("count") is not None:
                print(f"   Workflows found: {result['count']}")
            print()
    else:
        print("❌ No working endpoints found.\n")
        print("This might mean:")
        print("  1. Your access token is invalid or expired")
        print("  2. The API endpoints have changed")
        print("  3. Your workspace has no workflows")
        print("\nAction items:")
        print("  1. Verify your ALTERYX_ACCESS_TOKEN is fresh")
        print("  2. Run: python setup_alteryx.py")
        print("  3. Check Alteryx Cloud API documentation for correct endpoints")


if __name__ == "__main__":
    main()
