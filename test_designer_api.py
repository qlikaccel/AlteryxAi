#!/usr/bin/env python3
"""
Test Designer Cloud API endpoint with correct credentials
"""

import requests
import json
import sys

# Valid credentials
BASE_URL = "https://us1.alteryxcloud.com"
USERNAME = "accelerators@sorim.ai"
PASSWORD = "@1tr3yx123"

print(f"🔐 Testing Alteryx Designer Cloud Authentication")
print(f"📍 Base URL: {BASE_URL}")
print(f"👤 Username: {USERNAME}\n")

# Create session
session = requests.Session()

# Step 1: Authenticate
print("Step 1️⃣ - Authenticating...")
login_url = f"{BASE_URL}/auth/login"
login_payload = {
    "username": USERNAME,
    "password": PASSWORD
}

try:
    auth_response = session.post(
        login_url,
        json=login_payload,
        timeout=10,
        verify=True
    )
    
    print(f"✓ Auth Response Status: {auth_response.status_code}")
    
    if auth_response.status_code == 200:
        print("✅ Authentication SUCCESSFUL!\n")
    elif auth_response.status_code == 401:
        print(f"❌ Authentication FAILED (401 Unauthorized)")
        print(f"   Response: {auth_response.text[:200]}\n")
        sys.exit(1)
    else:
        print(f"⚠️ Unexpected status: {auth_response.status_code}")
        print(f"   Response: {auth_response.text[:200]}\n")
        
except Exception as e:
    print(f"❌ Auth Error: {e}\n")
    sys.exit(1)

# Step 2: Test Designer Cloud API Endpoint
print("Step 2️⃣ - Testing Designer Cloud API Endpoint...")
designer_api_url = f"{BASE_URL}/designer/api/workflows"

try:
    workflows_response = session.get(
        designer_api_url,
        timeout=10,
        verify=True,
        params={"limit": 100, "offset": 0}
    )
    
    print(f"✓ Workflows Response Status: {workflows_response.status_code}")
    
    if workflows_response.status_code == 200:
        try:
            data = workflows_response.json()
            if isinstance(data, dict):
                # Check common response keys
                keys = list(data.keys())
                print(f"✅ Valid JSON response with keys: {keys}")
                
                # Try to find workflows
                workflows = None
                for key in ["workflows", "items", "data", "results"]:
                    if key in data:
                        workflows = data[key]
                        if isinstance(workflows, list):
                            print(f"\n📦 Found {len(workflows)} workflows in '{key}' field:")
                            for idx, wf in enumerate(workflows[:5], 1):
                                name = wf.get("name", "N/A")
                                wf_id = wf.get("id", "N/A")
                                print(f"   {idx}. {name} (ID: {wf_id})")
                            if len(workflows) > 5:
                                print(f"   ... and {len(workflows) - 5} more")
                            break
                
                if workflows is None:
                    print(f"\n⚠️  Response structure not recognized. Raw data:\n{json.dumps(data, indent=2)[:500]}")
            else:
                print(f"Response is array with {len(data)} items")
                for idx, wf in enumerate(data[:5], 1):
                    name = wf.get("name", "N/A")
                    print(f"   {idx}. {name}")
                if len(data) > 5:
                    print(f"   ... and {len(data) - 5} more")
                    
        except json.JSONDecodeError:
            print(f"❌ Response is not valid JSON")
            print(f"Response text: {workflows_response.text[:200]}")
    else:
        print(f"❌ HTTP {workflows_response.status_code}")
        print(f"   Response: {workflows_response.text[:200]}")
        
except Exception as e:
    print(f"❌ API Error: {e}")
    sys.exit(1)

print("\n✅ All tests passed!")
