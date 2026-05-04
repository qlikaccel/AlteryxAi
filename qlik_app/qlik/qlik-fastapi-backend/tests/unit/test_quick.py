#!/usr/bin/env python3
"""
Quick Test: Compare OLD vs NEW LoadScript Fetch
Shows the difference between metadata reconstruction and full Engine API fetch
"""

import requests
import json
import sys

def print_header(text):
    print(f"\n{'='*80}")
    print(f"{text}")
    print(f"{'='*80}\n")

def test_api(app_id):
    """Test the current loadscript fetch API"""
    
    print_header("🚀 TESTING FULL LOADSCRIPT FETCH - ENGINE API FIX")
    
    if not app_id:
        print("❌ Error: App ID is required")
        print("Usage: python test_quick.py <APP_ID>")
        return False
    
    print(f"📋 Target App ID: {app_id}")
    print(f"🌐 API Endpoint: http://127.0.0.1:8000/api/migration/fetch-loadscript\n")
    
    try:
        # url = f"http://127.0.0.1:8000/api/migration/fetch-loadscript?app_id={app_id}"
        url = f"https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/fetch-loadscript?app_id={app_id}"
        
        print("Making request...")
        response = requests.post(url, timeout=60)
        
        print(f"Status Code: {response.status_code}\n")
        
        if response.status_code == 200:
            data = response.json()
            
            # Parse response
            status = data.get('status')
            method = data.get('method')
            script_length = data.get('script_length', 0)
            script = data.get('loadscript', '')
            message = data.get('message', '')
            
            print_header("📊 RESPONSE DETAILS")
            
            print(f"Status: {status}")
            print(f"Method: {method}")
            print(f"Script Length: {script_length} characters")
            print(f"Message: {message}\n")
            
            # Determine if using new or old method
            if method == "websocket_engine_api" and script_length > 1000:
                print("✅ FULL LOADSCRIPT VIA ENGINE API (NEW FIX WORKING!)")
                success = True
            elif method == "metadata_reconstruction":
                print("⚠️  METADATA RECONSTRUCTION (FALLBACK - WEBSOCKET MAY HAVE ISSUES)")
                success = False
            else:
                print(f"ℹ️  Using method: {method}")
                success = True
            
            # Show script preview
            print_header("📄 LOADSCRIPT PREVIEW")
            preview_lines = script.split('\n')[:10]
            for i, line in enumerate(preview_lines, 1):
                print(f"{i:2}. {line}")
            if len(script.split('\n')) > 10:
                print(f"    ... ({len(script.split(chr(10))) - 10} more lines)")
            
            # Check for key indicators
            print_header("✅ VALIDATION CHECKS")
            
            checks = {
                "Has loadscript content": len(script) > 100,
                "Uses WebSocket Engine API": method == "websocket_engine_api",
                "Script length > 1000 chars": script_length > 1000,
                "Contains LOAD statements": "LOAD" in script.upper(),
                "Status is success": status == "success"
            }
            
            for check, result in checks.items():
                symbol = "✅" if result else "❌"
                print(f"{symbol} {check}")
            
            # Final verdict
            print_header("🎯 VERDICT")
            
            if all([
                status == "success",
                method == "websocket_engine_api",
                script_length > 1000,
                "LOAD" in script.upper()
            ]):
                print("🎉 FIX IS WORKING! Full loadscript fetched via Engine API!\n")
                print("You can now:")
                print("1. Parse the loadscript")
                print("2. Convert to M Query")
                print("3. Download for Power BI\n")
                return True
            else:
                print("⚠️  Partial success or using fallback method")
                print("The API is working but using fallback (metadata reconstruction)")
                print("Check WebSocket connection if you need full script.\n")
                return False
            
        else:
            print(f"❌ Error: Status code {response.status_code}")
            print(response.text)
            return False
            
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to backend!")
        print("Make sure the backend is running: python main.py")
        return False
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    app_id = sys.argv[1] if len(sys.argv) > 1 else None
    
    if not app_id:
        # Use default app ID from your Postman test
        app_id = "764185f-b9cc-4dab-8f72-35e1ba8d1547"
        print(f"Using default app ID: {app_id}\n")
    
    success = test_api(app_id)
    sys.exit(0 if success else 1)
