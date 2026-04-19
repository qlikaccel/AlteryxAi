#!/usr/bin/env python3
"""
Diagnostic script to check Power BI workspace and API availability
"""

import requests
import json
from app.services.powerbi_auth import get_auth_manager, WORKSPACE_ID

auth = get_auth_manager()

print("=" * 60)
print("Power BI Workspace Diagnostic")
print("=" * 60)

# Test 1: Get workspace info
print("\n1️⃣  Getting workspace info...")
url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}"
try:
    r = requests.get(url, headers=auth.get_headers(), timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        workspace = r.json()
        print(f"   Workspace: {workspace.get('name', 'Unknown')}")
        print(f"   Type: {workspace.get('type', 'Unknown')}")
    else:
        print(f"   Error: {r.text[:300]}")
except Exception as e:
    print(f"   Exception: {e}")

# Test 2: Get current workspace users
print("\n2️⃣  Getting workspace users...")
url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/users"
try:
    r = requests.get(url, headers=auth.get_headers(), timeout=10)
    print(f"   Status: {r.status_code}")
    if r.status_code == 200:
        users = r.json().get("value", [])
        print(f"   Current users/apps: {len(users)}")
        for user in users[:5]:
            print(f"     - {user.get('displayName', 'Unknown')} ({user.get('principalType', 'Unknown')})")
    else:
        print(f"   Error: {r.text[:300]}")
except Exception as e:
    print(f"   Exception: {e}")

# Test 3: Try adding with different payload structures
print("\n3️⃣  Testing add service principal with different payloads...")

OBJECT_ID = "016ef36f-bffd-4c50-9a57-77554712c866"
url = f"https://api.powerbi.com/v1.0/myorg/groups/{WORKSPACE_ID}/users"

payloads = [
    {
        "principalId": OBJECT_ID,
        "principalType": "App",
        "accessRight": "Member"
    },
    {
        "principalId": OBJECT_ID,
        "principalType": "App"
    },
    {
        "principalId": OBJECT_ID
    }
]

for i, payload in enumerate(payloads, 1):
    print(f"\n   Payload {i}: {json.dumps(payload)}")
    try:
        r = requests.post(url, json=payload, headers=auth.get_headers(), timeout=10)
        print(f"      Status: {r.status_code}")
        if r.status_code not in [200, 201, 409]:
            print(f"      Error: {r.text[:200]}")
        else:
            print(f"      Success or already exists")
    except Exception as e:
        print(f"      Exception: {e}")

print("\n" + "=" * 60)
