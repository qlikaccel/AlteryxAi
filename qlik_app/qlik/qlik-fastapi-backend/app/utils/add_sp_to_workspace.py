#!/usr/bin/env python3
"""
Add Service Principal (PowerBI-Migration app) to Power BI Workspace
"""

import requests
import sys
from app.services.powerbi_auth import get_auth_manager, CLIENT_ID, WORKSPACE_ID

print("=" * 60)
print("Adding Service Principal to Power BI Workspace")
print("=" * 60)
print(f"\n📱 Service Principal Details:")
print(f"   Display Name: PowerBI-Migration")
print(f"   Client ID: {CLIENT_ID}")
print(f"   Workspace ID: {WORKSPACE_ID}")

try:
    # Get authentication manager and token
    auth = get_auth_manager()
    
    # Check if token is valid
    if not auth.is_token_valid():
        print("\n🔐 Acquiring new token...")
        result = auth.acquire_token_by_device_code()
        if not result.get("success"):
            print(f"❌ Failed to acquire token: {result.get('error')}")
            sys.exit(1)
    
    print("✅ Token acquired")
    
    # Add service principal to workspace
    print("\n🔗 Adding service principal to workspace...")
    url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/users"
    
    # Use Object ID: 016ef36f-bffd-4c50-9a57-77554712c866 from your Azure app
    OBJECT_ID = "016ef36f-bffd-4c50-9a57-77554712c866"
    
    payload = {
        "principalId": OBJECT_ID,
        "principalType": "App",
        "accessRight": "Member"
    }
    
    r = requests.post(
        url, 
        json=payload, 
        headers=auth.get_headers(), 
        timeout=10
    )
    
    print(f"   Response Status: {r.status_code}")
    
    if r.status_code == 200:
        print("\n✅ SUCCESS! Service principal added to workspace with Admin role")
        print("\n🧪 Testing connection...")
        test_result = auth.test_connection()
        if test_result.get("success"):
            print(f"✅ {test_result.get('message')}")
        else:
            print(f"⚠️  Connection test: {test_result.get('error')}")
    
    elif r.status_code == 409:
        print("⚠️  Service principal already added to workspace")
        print("\n🧪 Testing connection...")
        test_result = auth.test_connection()
        if test_result.get("success"):
            print(f"✅ {test_result.get('message')}")
        else:
            print(f"⚠️  Connection test: {test_result.get('error')}")
    
    elif r.status_code == 401:
        print("❌ 401 Unauthorized - Check your credentials and token")
        print(f"Response: {r.text}")
    
    elif r.status_code == 403:
        print("❌ 403 Forbidden - You may not have admin access to this workspace")
        print(f"Response: {r.text[:200]}")
    
    else:
        print(f"❌ Error: HTTP {r.status_code}")
        print(f"Response: {r.text[:300]}")
        sys.exit(1)
    
    print("\n" + "=" * 60)

except Exception as e:
    print(f"\n❌ Exception: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
