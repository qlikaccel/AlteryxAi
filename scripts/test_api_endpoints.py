#!/usr/bin/env python3
"""
Test different Alteryx API endpoints to find the correct one
"""
import requests
import json
import sys
sys.path.insert(0, r"D:\Alteryx\QlikAi\qlik_app\qlik\qlik-fastapi-backend")

from app.services.alteryx_session_manager import AlteryxSessionManager

# Initialize authenticated session
session_manager = AlteryxSessionManager()
session_manager.authenticate("https://us1.alteryxcloud.com", "accelerators@sorim.ai", "@1tr3yx123")
session = session_manager.get_session()
base_url = "https://us1.alteryxcloud.com"

# Common Alteryx API endpoint patterns to try
endpoints_to_try = [
    "/api/v1/workflows",
    "/api/workflows",
    "/designer/workflows",
    "/api/v1/designer/workflows",
    "/designer/api/v1/workflows",
    "/api/designer/workflows",
    "/rest/designer/workflows",
    "/api/rest/workflows",
    "/api/v2/workflows",
]

for endpoint in endpoints_to_try:
    url = f"{base_url}{endpoint}"
    print(f"\n{'='*60}")
    print(f"Testing: {url}")
    print(f"{'='*60}")
    
    try:
        response = session.get(url, timeout=5)
        print(f"Status: {response.status_code}")
        
        if response.status_code == 200:
            try:
                data = response.json()
                print(f"✓ JSON Response (type: {type(data).__name__})")
                if isinstance(data, dict):
                    print(f"  Keys: {list(data.keys())[:5]}")
                elif isinstance(data, list):
                    print(f"  List length: {len(data)}")
                    if data:
                        print(f"  First item keys: {list(data[0].keys())[:5] if isinstance(data[0], dict) else 'not a dict'}")
                print(f"  Preview: {str(data)[:200]}")
            except:
                content_type = response.headers.get('content-type', 'unknown')
                print(f"✗ Not JSON (content-type: {content_type})")
                print(f"  Response preview: {response.text[:100]}")
        else:
            print(f"✗ HTTP {response.status_code}")
            
    except requests.Timeout:
        print(f"✗ Timeout")
    except Exception as e:
        print(f"✗ Error: {e}")

print("\n" + "="*60)
print("Testing complete!")
