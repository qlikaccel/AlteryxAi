#!/usr/bin/env python3
"""
List all available Power BI workspaces
"""

import requests
from app.services.powerbi_auth import get_auth_manager

auth = get_auth_manager()

print("=" * 60)
print("Your Power BI Workspaces")
print("=" * 60)

url = "https://api.powerbi.com/v1.0/myorg/groups"

try:
    r = requests.get(url, headers=auth.get_headers(), timeout=10)
    
    if r.status_code == 200:
        workspaces = r.json().get("value", [])
        
        if not workspaces:
            print("\n⚠️  No workspaces found")
        else:
            print(f"\n📊 Found {len(workspaces)} workspace(s):\n")
            for ws in workspaces:
                print(f"Name: {ws.get('name')}")
                print(f"ID: {ws.get('id')}")
                print(f"Type: {ws.get('type')}")
                print(f"---")
                
                # Print the ID in a copyable format
                import pyperclip
    else:
        print(f"\n❌ Error: HTTP {r.status_code}")
        print(f"Response: {r.text[:500]}")
        
except Exception as e:
    print(f"\n❌ Exception: {e}")
    import traceback
    traceback.print_exc()

print("=" * 60)
