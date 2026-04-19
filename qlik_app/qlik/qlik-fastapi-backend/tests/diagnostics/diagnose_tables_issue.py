#!/usr/bin/env python3
"""
Diagnostic script to test the tables endpoint issue
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qlik_websocket_client import QlikWebSocketClient
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def test_get_app_tables(app_id: str):
    """Test getting tables from a specific app"""
    print(f"\n{'='*80}")
    print(f"Testing get_app_tables_simple for app: {app_id}")
    print(f"{'='*80}\n")
    
    try:
        client = QlikWebSocketClient()
        result = client.get_app_tables_simple(app_id)
        
        print(f"\nResult:")
        print(f"Success: {result.get('success')}")
        if not result.get('success'):
            print(f"Error: {result.get('error')}")
        else:
            print(f"Tables found: {len(result.get('tables', []))}")
            for table in result.get('tables', [])[:3]:
                print(f"  - {table.get('name')}")
        
        return result
        
    except Exception as e:
        print(f"\n✗ EXCEPTION CAUGHT:")
        print(f"  Type: {type(e).__name__}")
        print(f"  Message: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    # Test a few apps from your log
    test_apps = [
        "0955ffe1-7e3b-4ba4-9bbf-936d7ad5f1de",  
        "1958638f-6d27-4f35-be33-477ed71c3c49",
    ]
    
    for app_id in test_apps:
        test_get_app_tables(app_id)
