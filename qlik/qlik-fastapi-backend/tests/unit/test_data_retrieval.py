#!/usr/bin/env python3
"""
Test script to diagnose why data retrieval is failing
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

print("\n" + "="*80)
print("QLIK DATA RETRIEVAL DIAGNOSTIC")
print("="*80)

# Test 1: Check environment variables
print("\n✅ TEST 1: Environment Variables")
print("-" * 80)

qlik_api_key = os.getenv('QLIK_API_KEY')
qlik_tenant_url = os.getenv('QLIK_TENANT_URL')
qlik_api_base_url = os.getenv('QLIK_API_BASE_URL')

print(f"QLIK_API_KEY: {'SET' if qlik_api_key else 'MISSING'} ({len(qlik_api_key) if qlik_api_key else 0} chars)")
print(f"QLIK_TENANT_URL: {qlik_tenant_url or 'MISSING'}")
print(f"QLIK_API_BASE_URL: {qlik_api_base_url or 'MISSING'}")

if not qlik_api_key or not qlik_tenant_url:
    print("\n❌ FAILED: Missing required credentials")
    sys.exit(1)

# Test 2: Test REST API connection
print("\n✅ TEST 2: REST API Connection")
print("-" * 80)

import requests

try:
    headers = {
        'Authorization': f'Bearer {qlik_api_key}',
        'Content-Type': 'application/json'
    }
    
    url = f"{qlik_api_base_url}/users/me"
    print(f"Testing: {url}")
    
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        print("✓ REST API connection successful")
        user_data = response.json()
        print(f"User: {user_data.get('name', 'Unknown')}")
    else:
        print(f"✗ REST API failed: {response.status_code}")
        print(f"Response: {response.text[:500]}")
except Exception as e:
    print(f"✗ Connection error: {e}")
    sys.exit(1)

# Test 3: List available apps
print("\n✅ TEST 3: List Available Apps")
print("-" * 80)

try:
    url = f"{qlik_api_base_url}/apps"
    response = requests.get(url, headers=headers, timeout=10)
    
    if response.status_code == 200:
        apps = response.json()
        if isinstance(apps, dict) and 'data' in apps:
            apps = apps['data']
        
        print(f"Found {len(apps)} app(s)")
        for i, app in enumerate(apps[:3]):
            app_id = app.get('id') or app.get('attributes', {}).get('id')
            app_name = app.get('name') or app.get('attributes', {}).get('name')
            print(f"  {i+1}. {app_name} (ID: {app_id})")
        
        if apps:
            test_app_id = apps[0].get('id') or apps[0].get('attributes', {}).get('id')
    else:
        print(f"✗ Failed to list apps: {response.status_code}")
        print(f"Response: {response.text[:500]}")
        sys.exit(1)
        
except Exception as e:
    print(f"✗ Error: {e}")
    sys.exit(1)

# Test 4: Test WebSocket connection
print(f"\n✅ TEST 4: WebSocket Connection (using first app: {test_app_id})")
print("-" * 80)

try:
    from qlik_websocket_client import QlikWebSocketClient
    
    ws_client = QlikWebSocketClient()
    
    if ws_client.connect_to_app(test_app_id):
        print("✓ WebSocket connected successfully")
        
        # Test 5: Get table data
        print(f"\n✅ TEST 5: Retrieve Table Data")
        print("-" * 80)
        
        # First get list of tables
        tables_info = ws_client._get_tables_from_data_model()
        tables = tables_info.get("tables", [])
        
        print(f"Found {len(tables)} table(s)")
        
        if tables:
            test_table = tables[0]
            table_name = test_table.get('name')
            print(f"\nTesting data retrieval from table: {table_name}")
            print(f"Table has {test_table.get('no_of_rows', '?')} rows")
            
            # Try to get data
            data_result = ws_client.get_table_data(test_app_id, table_name, limit=5)
            
            print(f"\nData Retrieval Result:")
            print(f"Success: {data_result.get('success')}")
            print(f"Rows: {len(data_result.get('rows', []))}")
            print(f"Columns: {data_result.get('columns', [])}")
            
            if data_result.get('rows'):
                print(f"\nFirst row preview:")
                first_row = data_result['rows'][0]
                for k, v in list(first_row.items())[:3]:
                    print(f"  {k}: {v}")
            
            if "not accessible" in str(data_result.get('rows', [])):
                print("\n⚠️ Data marked as 'not accessible' - all retrieval methods failed")
                print("This suggests WebSocket connection is established but data extraction is failing")
        else:
            print("No tables found in app")
        
        ws_client.close()
    else:
        print("✗ WebSocket connection failed")
        
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*80)
print("DIAGNOSTIC COMPLETE")
print("="*80 + "\n")
