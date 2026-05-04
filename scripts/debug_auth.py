#!/usr/bin/env python3
"""
Debug script to test with actual hardcoded credentials
"""
import requests
import json
import sys
sys.path.insert(0, r"D:\Alteryx\QlikAi\qlik_app\qlik\qlik-fastapi-backend")

from app.services.alteryx_session_manager import AlteryxSessionManager

# Initialize session manager
session_manager = AlteryxSessionManager()

# Use hardcoded credentials from users.json
base_url = "https://us1.alteryxcloud.com"
username = "accelerators@sorim.ai"
password = "@1tr3yx123"

print(f"Testing authentication with hardcoded credentials:")
print(f"  Base URL: {base_url}")
print(f"  Username: {username}")
print(f"  Password: {'*' * len(password)}")
print()

try:
    result = session_manager.authenticate(base_url, username, password)
    print(f"Auth result: {json.dumps(result, indent=2)}")
    print()
except Exception as e:
    print(f"Auth error: {e}")
    print()

# Now test API endpoints
session = session_manager.get_session()
print(f"Session cookies: {dict(session.cookies)}")
print()

endpoints = [
    "/api/v1/workflows",
    "/api/v2/workflows",
    "/api/workflows",
    "/rest/workflows",
]

for endpoint in endpoints:
    url = f"{base_url}{endpoint}?limit=100&offset=0"
    print(f"Testing: {url}")
    
    try:
        response = session.get(url, timeout=5, verify=True)
        print(f"  Status: {response.status_code}")
        print(f"  Content-Type: {response.headers.get('content-type', 'unknown')}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"  ✓ Got JSON response")
                print(f"  Type: {type(data).__name__}")
                if isinstance(data, dict):
                    print(f"  Keys: {list(data.keys())[:5]}")
                elif isinstance(data, list):
                    print(f"  Items: {len(data)}")
            except Exception as e:
                print(f"  ✗ JSON parse error: {e}")
                print(f"  Response preview: {response.text[:200]}")
        elif response.status_code == 401:
            print(f"  ✗ 401 Unauthorized")
        else:
            print(f"  ✗ Error: {response.text[:100]}")
            
    except Exception as e:
        print(f"  ✗ Exception: {e}")
    
    print()
