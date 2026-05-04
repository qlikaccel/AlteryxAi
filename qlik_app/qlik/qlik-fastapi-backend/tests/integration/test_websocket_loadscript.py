#!/usr/bin/env python3
"""
Test WebSocket Engine API Connection for Full LoadScript Fetch
This script tests the direct WebSocket connection to Qlik Cloud
"""

import os
import sys
import json
import logging
from dotenv import load_dotenv
from qlik_websocket_client import QlikWebSocketClient
from loadscript_fetcher import LoadScriptFetcher

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_websocket_direct(app_id):
    """Test WebSocket connection directly"""
    print("\n" + "="*80)
    print("TEST 1: Direct WebSocket Connection via QlikWebSocketClient")
    print("="*80)
    
    try:
        ws_client = QlikWebSocketClient()
        print("✅ WebSocket client initialized")
        
        print("\nTesting WebSocket connection to app...")
        result = ws_client._get_app_script_websocket(app_id)
        
        print(f"\n📊 Result Status: {result.get('success')}")
        print(f"📊 Error (if any): {result.get('error')}")
        print(f"📊 Script Length: {len(result.get('script', ''))} characters")
        print(f"📊 Tables Found: {len(result.get('tables', []))}")
        
        if result.get('success'):
            print("\n✅ SUCCESS! Full loadscript retrieved via WebSocket")
            script_preview = result.get('script', '')[:500]
            print(f"\nScript Preview:\n{script_preview}...")
            return True
        else:
            print("\n❌ WebSocket failed. Error:", result.get('error'))
            return False
            
    except Exception as e:
        print(f"❌ Error in WebSocket test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_fetcher_api(app_id):
    """Test via LoadScriptFetcher"""
    print("\n" + "="*80)
    print("TEST 2: LoadScript Fetcher (Full Pipeline)")
    print("="*80)
    
    try:
        fetcher = LoadScriptFetcher()
        print("✅ Fetcher initialized")
        
        # Test connection
        print("\nTesting Qlik Cloud connection...")
        conn_test = fetcher.test_connection()
        if conn_test["status"] != "success":
            print(f"❌ Connection test failed: {conn_test}")
            return False
        print("✅ Connection test passed")
        
        # Fetch loadscript
        print(f"\nFetching loadscript for app: {app_id}")
        script_result = fetcher.fetch_loadscript(app_id)
        
        print(f"\n📊 Fetch Status: {script_result.get('status')}")
        print(f"📊 Method Used: {script_result.get('method')}")
        print(f"📊 Script Length: {script_result.get('script_length')} characters")
        print(f"📊 Message: {script_result.get('message')}")
        
        script_preview = script_result.get('loadscript', '')[:500]
        print(f"\nScript Preview:\n{script_preview}...")
        
        if script_result.get('status') == 'success':
            print("\n✅ Full loadscript retrieved successfully!")
            return True
        else:
            print("\n⚠️  Partial success or fallback method used")
            return False
            
    except Exception as e:
        print(f"❌ Error in fetcher test: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def show_env_info():
    """Show environment variables"""
    print("\n" + "="*80)
    print("ENVIRONMENT CONFIGURATION")
    print("="*80)
    
    api_key = os.getenv('QLIK_API_KEY')
    tenant_url = os.getenv('QLIK_TENANT_URL')
    api_base_url = os.getenv('QLIK_API_BASE_URL')
    
    print(f"✓ QLIK_API_KEY: {api_key[:20]}..." if api_key else "✗ QLIK_API_KEY: NOT SET")
    print(f"✓ QLIK_TENANT_URL: {tenant_url}" if tenant_url else "✗ QLIK_TENANT_URL: NOT SET")
    print(f"✓ QLIK_API_BASE_URL: {api_base_url}" if api_base_url else "✗ QLIK_API_BASE_URL: NOT SET")
    
    if not (api_key and tenant_url):
        print("\n❌ Required environment variables not set!")
        return False
    return True


def main():
    """Main test function"""
    load_dotenv()
    
    print("\n" + "="*80)
    print("QLIK WEBSOCKET LOADSCRIPT FETCH - DIAGNOSTIC TEST")
    print("="*80)
    
    # Check environment
    if not show_env_info():
        print("\n❌ Cannot proceed without environment variables")
        sys.exit(1)
    
    # Get app ID
    app_id = sys.argv[1] if len(sys.argv) > 1 else input("\nEnter Qlik App ID: ").strip()
    
    if not app_id:
        print("❌ App ID is required")
        sys.exit(1)
    
    print(f"\n📋 Testing with App ID: {app_id}")
    
    # Run tests
    ws_result = test_websocket_direct(app_id)
    fetcher_result = test_fetcher_api(app_id)
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    print(f"Direct WebSocket: {'✅ PASS' if ws_result else '❌ FAIL'}")
    print(f"Fetcher API: {'✅ PASS' if fetcher_result else '❌ FAIL'}")
    
    if ws_result and fetcher_result:
        print("\n✅ All tests passed! Full loadscript fetch is working.")
        sys.exit(0)
    else:
        print("\n⚠️  Some tests failed. Check the logs above for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
