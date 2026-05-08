#!/usr/bin/env python
"""
Verify Qlik API Key and provide next steps
"""
import os
import requests
import jwt
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

print("\n" + "=" * 70)
print("🔐 QLIK API KEY VERIFICATION - NO OAUTH")
print("=" * 70)

api_key = os.getenv('QLIK_API_KEY')
tenant_url = os.getenv('QLIK_TENANT_URL')
api_base_url = os.getenv('QLIK_API_BASE_URL')

# Check if using OAuth (should NOT be)
client_id = os.getenv('QLIK_CLIENT_ID')
client_secret = os.getenv('QLIK_CLIENT_SECRET')

if client_id or client_secret:
    print("⚠️  WARNING: QLIK_CLIENT_ID or QLIK_CLIENT_SECRET are set")
    print("   These have been DISABLED - only API Key authentication is used now")

print(f"\n✅ Configuration:")
print(f"   API Base URL: {api_base_url}")
print(f"   Tenant URL:  {tenant_url}")

if not api_key:
    print("\n❌ CRITICAL: QLIK_API_KEY not found in .env")
    print("   Please generate a new API key from Qlik Cloud console")
    exit(1)

# Decode and validate JWT
print(f"\n📋 JWT Analysis:")
try:
    decoded = jwt.decode(api_key, options={"verify_signature": False})
    
    print(f"   ✅ JWT decoded successfully")
    print(f"   Subject:      {decoded.get('sub', 'N/A')}")
    print(f"   Tenant ID:    {decoded.get('tenantId', 'N/A')}")
    print(f"   Audience:     {decoded.get('aud', 'N/A')}")
    print(f"   Issuer:       {decoded.get('iss', 'N/A')}")
    print(f"   Token Type:   {decoded.get('subType', 'N/A')}")
    
    # Check expiration
    if 'exp' in decoded:
        exp_time = datetime.fromtimestamp(decoded['exp'])
        now = datetime.now()
        if now > exp_time:
            print(f"\n   ⏰ EXPIRED: {exp_time}")
            print(f"   ❌ This API key is EXPIRED and must be regenerated!")
        else:
            time_left = exp_time - now
            days = time_left.days
            hours = time_left.seconds // 3600
            print(f"   ⏰ Expires: {exp_time} ({days}d {hours}h remaining)")
    
except Exception as e:
    print(f"   ❌ JWT decode failed: {e}")
    exit(1)

# Test API connection
print(f"\n🧪 Testing API Connection:")

headers = {
    'Authorization': f'Bearer {api_key}',
    'Content-Type': 'application/json',
    'Accept': 'application/json'
}

# Test 1: Get user info
url = f'{api_base_url}/users/me'
print(f"\n   1️⃣  GET {url}")
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"      Status: {response.status_code}")
    
    if response.status_code == 200:
        user = response.json()
        print(f"      ✅ SUCCESS!")
        print(f"      User: {user.get('name', 'N/A')}")
        print(f"      ID:   {user.get('id', 'N/A')}")
    elif response.status_code == 401:
        print(f"      ❌ 401 - Unauthorized")
        print(f"      API Key is INVALID or EXPIRED")
    else:
        print(f"      Response: {response.text[:150]}")
        
except Exception as e:
    print(f"      ❌ Connection error: {e}")

# Test 2: List applications
url = f'{api_base_url}/apps'
print(f"\n   2️⃣  GET {url}")
try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"      Status: {response.status_code}")
    
    if response.status_code == 200:
        apps = response.json()
        if isinstance(apps, list):
            print(f"      ✅ SUCCESS! Found {len(apps)} apps")
            for app in apps[:3]:
                print(f"         • {app.get('name', 'N/A')}")
        else:
            print(f"      Response: {str(apps)[:100]}...")
    elif response.status_code == 401:
        print(f"      ❌ 401 - Unauthorized (API Key invalid/expired)")
    else:
        print(f"      Status: {response.status_code}")
        print(f"      Response: {response.text[:150]}")
        
except Exception as e:
    print(f"      ❌ Connection error: {e}")

# Next steps
print("\n" + "=" * 70)
print("📝 NEXT STEPS:")
print("=" * 70)
print("""
If you see 401 errors:

1. ✅ Open Qlik Cloud Console:
   https://c8vlzp3sx6akvnh.in.qlikcloud.com/console

2. ✅ Navigate to API Keys:
   • Admin → API Keys
   • Or Settings (gear icon) → API Keys

3. ✅ Delete the OLD key (if it's expired)

4. ✅ Create NEW API Key:
   • Click "Create new API key" button
   • Name: "Migration_Tool"
   • Expiration: 12 months (or higher)
   • Scopes (CHECK ALL):
     ✓ apps:read
     ✓ data:read
     ✓ spaces:read

5. ✅ Copy the ENTIRE JWT token
   • Should look like: eyJhbGciOiJFUzM4NCIs...
   • ~600+ characters long
   • NO line breaks or extra spaces

6. ✅ Update .env:
   Replace QLIK_API_KEY=<paste entire token here>

7. ✅ Restart application:
   • Kill uvicorn (Ctrl+C)
   • Run: uvicorn main:app --reload

8. ✅ Test again:
   python verify_api_key.py
""")

print("=" * 70)
