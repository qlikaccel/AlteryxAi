#!/usr/bin/env python3
"""
Debug script to test Alteryx API directly
"""
import requests
import json
import sys
sys.path.insert(0, r"D:\Alteryx\QlikAi\qlik_app\qlik\qlik-fastapi-backend")

from app.services.alteryx_session_manager import AlteryxSessionManager

# Initialize session manager
session_manager = AlteryxSessionManager()

# Try to authenticate
base_url = "https://us1.alteryxcloud.com"
username = "accelerators@sorim.ai"
password = "@1tr3yx123"

print(f"Attempting to authenticate to {base_url}...")
try:
    result = session_manager.authenticate(base_url, username, password)
    print(f"Auth result: {json.dumps(result, indent=2)}")
except Exception as e:
    print(f"Auth error: {e}")
    sys.exit(1)

# Now try to fetch workflows
print(f"\nFetching workflows from {base_url}/designer/api/workflows...")
session = session_manager.get_session()
workflows_url = f"{base_url}/designer/api/workflows?limit=100&offset=0"

try:
    response = session.get(workflows_url, timeout=10, verify=True)
    print(f"Response status: {response.status_code}")
    print(f"Response headers: {dict(response.headers)}")
    print(f"Response text (first 2000 chars):\n{response.text[:2000]}")
    
    # Try to parse as JSON
    if response.status_code == 200:
        data = response.json()
        print(f"\nParsed JSON structure:")
        print(f"Type: {type(data)}")
        if isinstance(data, dict):
            print(f"Keys: {list(data.keys())}")
            # Pretty print
            print(f"\nFull JSON:\n{json.dumps(data, indent=2)}")
        elif isinstance(data, list):
            print(f"List length: {len(data)}")
            if len(data) > 0:
                print(f"First item:\n{json.dumps(data[0], indent=2)}")
except Exception as e:
    print(f"Error: {e}")
