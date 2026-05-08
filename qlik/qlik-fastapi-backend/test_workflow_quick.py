#!/usr/bin/env python3
"""
Quick test for Alteryx Designer Cloud workflow API
Tests the most likely endpoints based on Alteryx Cloud v1 API
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

ALTERYX_BASE_URL = os.getenv("ALTERYX_TENANT_URL", "https://us1.alteryxcloud.com")
ALTERYX_ACCESS_TOKEN = os.getenv("ALTERYX_ACCESS_TOKEN", "")
ALTERYX_WORKSPACE_ID = os.getenv("ALTERYX_WORKSPACE_ID", "")


def test_flow_endpoint():
    """Test the most likely Designer Cloud v1 workflow endpoint."""
    
    if not ALTERYX_ACCESS_TOKEN:
        print("❌ ALTERYX_ACCESS_TOKEN not set")
        return
    
    print("\n" + "="*80)
    print("  QUICK WORKFLOW ENDPOINT TEST")
    print("="*80 + "\n")
    
    # Most likely endpoint based on Alteryx Designer Cloud API
    endpoint = f"{ALTERYX_BASE_URL}/api/v1/workflows"
    
    print(f"Testing Designer Cloud v1 API:")
    print(f"  Endpoint: {endpoint}")
    print(f"  Token: {ALTERYX_ACCESS_TOKEN[:50]}...")
    print()
    
    try:
        resp = requests.get(
            endpoint,
            headers={
                "Authorization": f"Bearer {ALTERYX_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        
        print(f"Response Status: {resp.status_code}\n")
        
        if resp.status_code == 200:
            print("✅ SUCCESS!\n")
            data = resp.json()
            
            if isinstance(data, list):
                print(f"Got {len(data)} workflows (array response)")
                if data:
                    print(f"First workflow: {data[0]}\n")
            elif isinstance(data, dict):
                print(f"Got dict response with keys: {list(data.keys())}")
                # Try to find workflows in common locations
                if "workflows" in data:
                    print(f"Found {len(data['workflows'])} in 'workflows' key")
                    if data['workflows']:
                        print(f"First: {data['workflows'][0]}")
                elif "data" in data:
                    print(f"Found {len(data['data'])} in 'data' key")
                    if data['data']:
                        print(f"First: {data['data'][0]}")
                elif "items" in data:
                    print(f"Found {len(data['items'])} in 'items' key")
                print()
            
            return True
        else:
            print(f"❌ {resp.status_code}\n")
            try:
                error = resp.json()
                print(f"Error: {error}\n")
            except:
                print(f"Response: {resp.text[:300]}\n")
            return False
    
    except requests.Timeout:
        print("❌ Request timed out\n")
        return False
    except Exception as e:
        print(f"❌ Error: {e}\n")
        return False


if __name__ == "__main__":
    success = test_flow_endpoint()
    
    if not success:
        print("="*80)
        print("Endpoint not working. Trying alternative..."\n)
        
        # Try with workspace ID in query
        endpoint = f"{ALTERYX_BASE_URL}/api/v1/workflows"
        print(f"Trying with workspace ID in query parameter...")
        
        try:
            resp = requests.get(
                endpoint,
                headers={
                    "Authorization": f"Bearer {ALTERYX_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                params={"workspaceId": ALTERYX_WORKSPACE_ID} if ALTERYX_WORKSPACE_ID else {},
                timeout=10,
            )
            
            print(f"Status: {resp.status_code}\n")
            if resp.status_code == 200:
                print("✅ This endpoint works with workspaceId parameter!")
            else:
                print(f"Still failed: {resp.status_code}")
                
        except Exception as e:
            print(f"Error: {e}")
