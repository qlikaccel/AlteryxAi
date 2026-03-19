#!/usr/bin/env python3
"""
Qlik Connection Diagnostics
This script will help identify why the WebSocket connection is failing
"""

import os
import sys
import json
import jwt
import requests
from dotenv import load_dotenv

load_dotenv()

def print_header(title):
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80)

def test_environment():
    """Test environment variables"""
    print_header("1. Checking Environment Variables")
    
    api_key = os.getenv('QLIK_API_KEY')
    tenant_url = os.getenv('QLIK_TENANT_URL')
    
    if not api_key:
        print("❌ QLIK_API_KEY is not set!")
        return False
    else:
        print(f"✅ QLIK_API_KEY is set (length: {len(api_key)} characters)")
        
        # Decode JWT to check validity
        try:
            decoded = jwt.decode(api_key, options={"verify_signature": False})
            print(f"✅ API Key is valid JWT")
            print(f"   - Subject (user ID): {decoded.get('sub', 'N/A')}")
            print(f"   - Tenant ID: {decoded.get('tenantId', 'N/A')}")
            print(f"   - Issuer: {decoded.get('iss', 'N/A')}")
            
            # Check expiration
            if 'exp' in decoded:
                import datetime
                exp_time = datetime.datetime.fromtimestamp(decoded['exp'])
                now = datetime.datetime.now()
                if exp_time < now:
                    print(f"❌ API Key has EXPIRED (expired at {exp_time})")
                    return False
                else:
                    print(f"✅ API Key is valid until {exp_time}")
        except Exception as e:
            print(f"⚠️  Could not decode JWT: {e}")
    
    if not tenant_url:
        print("❌ QLIK_TENANT_URL is not set!")
        return False
    else:
        print(f"✅ QLIK_TENANT_URL is set: {tenant_url}")
        
        # Clean URL
        if tenant_url.endswith('/'):
            print("⚠️  Warning: URL has trailing slash, will be removed")
            tenant_url = tenant_url.rstrip('/')
        
        if not tenant_url.startswith('https://'):
            print("❌ URL must start with https://")
            return False
    
    return True

def test_rest_api():
    """Test REST API connection"""
    print_header("2. Testing REST API Connection")
    
    api_key = os.getenv('QLIK_API_KEY')
    tenant_url = os.getenv('QLIK_TENANT_URL').rstrip('/')
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    # Test /users/me endpoint
    print("\nTesting: GET /api/v1/users/me")
    try:
        response = requests.get(
            f'{tenant_url}/api/v1/users/me',
            headers=headers,
            timeout=10
        )
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ REST API authentication successful!")
            data = response.json()
            print(f"   - User ID: {data.get('id', 'N/A')}")
            print(f"   - User Name: {data.get('name', 'N/A')}")
            print(f"   - Email: {data.get('email', 'N/A')}")
            return True
        elif response.status_code == 401:
            print("❌ Authentication failed - API key is invalid or expired")
            print(f"   Response: {response.text}")
            return False
        elif response.status_code == 403:
            print("❌ Forbidden - API key doesn't have required permissions")
            print(f"   Response: {response.text}")
            return False
        else:
            print(f"❌ Unexpected status code: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Request timed out - check your internet connection")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"❌ Connection error: {e}")
        print("   Check if the tenant URL is correct")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def test_apps_list():
    """Test getting apps list"""
    print_header("3. Testing Apps List")
    
    api_key = os.getenv('QLIK_API_KEY')
    tenant_url = os.getenv('QLIK_TENANT_URL').rstrip('/')
    
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    
    print("\nTesting: GET /api/v1/apps")
    try:
        response = requests.get(
            f'{tenant_url}/api/v1/apps',
            headers=headers,
            timeout=10
        )
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # Handle different response formats
            apps = []
            if isinstance(data, list):
                apps = data
            elif isinstance(data, dict) and 'data' in data:
                apps = data['data']
            elif isinstance(data, dict) and 'items' in data:
                apps = data['items']
            
            print(f"✅ Found {len(apps)} apps")
            
            if len(apps) > 0:
                print("\nApps available:")
                for i, app in enumerate(apps[:5], 1):
                    if isinstance(app, dict):
                        attributes = app.get('attributes', {})
                        app_id = attributes.get('id', 'N/A')
                        app_name = attributes.get('name', 'N/A')
                        last_reload = attributes.get('lastReloadTime', 'Never')
                        print(f"   {i}. {app_name}")
                        print(f"      ID: {app_id}")
                        print(f"      Last Reload: {last_reload}")
                
                if len(apps) > 5:
                    print(f"   ... and {len(apps) - 5} more apps")
                
                # Return first app with data
                for app in apps:
                    if isinstance(app, dict):
                        attributes = app.get('attributes', {})
                        if attributes.get('lastReloadTime'):
                            return attributes.get('id')
                
                # Return first app if no apps have data
                if isinstance(apps[0], dict):
                    return apps[0].get('attributes', {}).get('id')
            else:
                print("⚠️  No apps found. You need to create or have access to apps.")
                return None
        else:
            print(f"❌ Failed to get apps: {response.status_code}")
            print(f"   Response: {response.text}")
            return None
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def test_websocket_connection(app_id):
    """Test WebSocket connection"""
    print_header("4. Testing WebSocket Connection")
    
    if not app_id:
        print("❌ No app ID provided, skipping WebSocket test")
        return False
    
    print(f"\nTesting WebSocket connection to app: {app_id}")
    
    try:
        import websocket
        import ssl
        
        api_key = os.getenv('QLIK_API_KEY')
        tenant_url = os.getenv('QLIK_TENANT_URL').rstrip('/')
        
        # Decode JWT for user info
        try:
            decoded = jwt.decode(api_key, options={"verify_signature": False})
            user_id = decoded.get('sub', '')
            user_directory = "QLIK"
        except:
            user_id = ""
            user_directory = "QLIK"
        
        # Clean tenant URL for WebSocket
        tenant_host = tenant_url.replace('https://', '').replace('http://', '')
        ws_url = f"wss://{tenant_host}/app/{app_id}"
        
        print(f"WebSocket URL: {ws_url}")
        print(f"User ID: {user_id}")
        print(f"User Directory: {user_directory}")
        
        # Create WebSocket
        print("\n➤ Creating WebSocket connection...")
        ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
        
        # Prepare headers
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        
        if user_id:
            headers["X-Qlik-User"] = f"UserDirectory={user_directory};UserId={user_id}"
        
        print("➤ Connecting...")
        try:
            ws.connect(ws_url, header=headers, timeout=10)
            print("✅ WebSocket connected successfully!")
        except websocket.WebSocketTimeoutException:
            print("❌ WebSocket connection timed out")
            print("   Possible causes:")
            print("   - Firewall blocking WebSocket connections")
            print("   - App ID is invalid")
            print("   - Network/proxy issues")
            return False
        except websocket.WebSocketBadStatusException as e:
            print(f"❌ WebSocket connection rejected: {e}")
            print(f"   Status code: {e.status_code}")
            if e.status_code == 401:
                print("   → Authentication failed")
            elif e.status_code == 403:
                print("   → Access forbidden - check app permissions")
            elif e.status_code == 404:
                print("   → App not found - check app ID")
            return False
        except Exception as e:
            print(f"❌ WebSocket connection failed: {e}")
            return False
        
        # Try to open the app
        print("\n➤ Opening app...")
        open_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "OpenDoc",
            "handle": -1,
            "params": {
                "qDocName": app_id,
                "qNoData": False
            }
        }
        
        try:
            ws.send(json.dumps(open_msg))
            print("   Sent OpenDoc request")
            
            response = ws.recv()
            response_data = json.loads(response)
            
            print(f"   Received response:")
            print(f"   {json.dumps(response_data, indent=2)}")
            
            if 'result' in response_data:
                if 'qReturn' in response_data['result']:
                    app_handle = response_data['result']['qReturn']['qHandle']
                    print(f"\n✅ App opened successfully!")
                    print(f"   App handle: {app_handle}")
                    
                    ws.close()
                    return True
                else:
                    print("\n❌ Unexpected response structure")
                    print("   Expected 'qReturn' in result")
                    ws.close()
                    return False
            elif 'error' in response_data:
                error = response_data['error']
                print(f"\n❌ Error opening app:")
                print(f"   Code: {error.get('code', 'N/A')}")
                print(f"   Message: {error.get('message', 'N/A')}")
                ws.close()
                return False
            else:
                print("\n❌ Unexpected response format")
                ws.close()
                return False
                
        except Exception as e:
            print(f"❌ Error during OpenDoc: {e}")
            ws.close()
            return False
            
    except ImportError:
        print("❌ websocket-client library not installed")
        print("   Run: pip install websocket-client")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all diagnostic tests"""
    print("\n" + "="*80)
    print(" " * 25 + "Qlik Connection Diagnostics")
    print("="*80)
    
    # Test 1: Environment
    if not test_environment():
        print("\n❌ Environment check failed. Please fix environment variables.")
        return
    
    # Test 2: REST API
    if not test_rest_api():
        print("\n❌ REST API connection failed. Check your API key and tenant URL.")
        return
    
    # Test 3: Apps list
    app_id = test_apps_list()
    
    # Test 4: WebSocket
    if app_id:
        test_websocket_connection(app_id)
    
    print_header("Diagnostic Complete")
    print("\nIf all tests passed, your connection should work.")
    print("If any tests failed, check the error messages above.")
    print("\nCommon issues:")
    print("  1. Expired API key → Generate a new one in Qlik Cloud")
    print("  2. Wrong tenant URL → Check it matches your Qlik Cloud URL")
    print("  3. App has no data → App must be reloaded at least once")
    print("  4. No app access → Check permissions in Qlik Cloud")
    print("  5. Firewall blocking WebSocket → Check network/proxy settings")

if __name__ == "__main__":
    main()