#!/usr/bin/env python
"""Quick test to verify QLIK_API_KEY works"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv('QLIK_API_KEY')
tenant_url = os.getenv('QLIK_TENANT_URL')
api_base_url = os.getenv('QLIK_API_BASE_URL')

print("=" * 60)
print("🔑 Testing QLIK_API_KEY authentication")
print("=" * 60)

if not api_key:
    print("❌ QLIK_API_KEY not set in .env")
    exit(1)

print(f"✅ API Key found")
print(f"   Tenant: {tenant_url}")
print(f"   Base URL: {api_base_url}")

headers = {
    'Authorization': f'Bearer {api_key}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# Test 1: Get current user
print(f"\n1️⃣  Testing: GET /users/me")
url = f'{api_base_url}/users/me'
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        user = response.json()
        print(f"   ✅ SUCCESS! User: {user.get('name', 'N/A')}")
    else:
        print(f"   ❌ Response: {response.text[:200]}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 2: List applications
print(f"\n2️⃣  Testing: GET /apps")
url = f'{api_base_url}/apps'
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        apps = response.json()
        if isinstance(apps, list):
            print(f"   ✅ SUCCESS! Found {len(apps)} apps")
            for app in apps[:3]:
                print(f"      - {app.get('name', 'N/A')}")
        else:
            print(f"   Response type: {type(apps)}")
            print(f"   Response: {str(apps)[:200]}")
    else:
        print(f"   ❌ Response: {response.text[:200]}")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Test 3: List with limit
print(f"\n3️⃣  Testing: GET /apps?limit=10")
url = f'{api_base_url}/apps?limit=10'
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        apps = response.json()
        print(f"   ✅ SUCCESS! Response: {str(apps)[:100]}...")
    else:
        print(f"   ❌ Response status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Error: {e}")

print("\n" + "=" * 60)
