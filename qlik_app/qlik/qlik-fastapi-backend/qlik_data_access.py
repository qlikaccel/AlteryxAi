import requests
import json
import jwt
import os
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

class QlikDataAccess:
    def __init__(self, api_key: Optional[str] = None, tenant_url: Optional[str] = None):
        self.api_key = api_key or os.getenv('QLIK_API_KEY')
        self.tenant_url = (tenant_url or os.getenv('QLIK_TENANT_URL') or "").rstrip("/")
        self.api_base_url = f"{self.tenant_url}/api/v1"
        
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # Decode JWT to get user info for Engine API
        try:
            decoded = jwt.decode(self.api_key, options={"verify_signature": False})
            self.user_id = decoded.get('sub', '')
            self.user_directory = "QLIK"
        except:
            self.user_id = ""
            self.user_directory = "QLIK"
    
    def get_app_direct_data(self, app_id: str) -> Dict[str, Any]:
        """Try to get data directly using various methods"""
        
        results = {
            "app_id": app_id,
            "methods_tried": [],
            "successful_methods": [],
            "data_found": False
        }
        
        # Method 1: Try to get data via Engine API endpoints
        print("\n=== Method 1: Trying Engine API endpoints ===")
        engine_endpoints = [
            f"/qix/apps/{app_id}/data/model",
            f"/qix/apps/{app_id}/fields",
            f"/qix/apps/{app_id}/measures",
            f"/qix/apps/{app_id}/dimensions",
        ]
        
        for endpoint in engine_endpoints:
            try:
                response = requests.get(
                    f"{self.tenant_url}{endpoint}",
                    headers=self.headers,
                    timeout=30
                )
                results["methods_tried"].append(f"GET {endpoint}")
                
                if response.status_code == 200:
                    print(f"✓ {endpoint}: Success")
                    results["successful_methods"].append(endpoint)
                    results[f"data_{endpoint.replace('/', '_')}"] = response.json()
                else:
                    print(f"✗ {endpoint}: {response.status_code}")
            except Exception as e:
                print(f"✗ {endpoint}: {str(e)}")
        
        # Method 2: Try to export app data
        print("\n=== Method 2: Trying export endpoints ===")
        export_endpoints = [
            f"/apps/{app_id}/export/data",
            f"/apps/{app_id}/export/app",
            f"/apps/{app_id}/data/export",
        ]
        
        for endpoint in export_endpoints:
            try:
                response = requests.post(
                    f"{self.api_base_url}{endpoint}",
                    headers=self.headers,
                    json={},
                    timeout=30
                )
                results["methods_tried"].append(f"POST {endpoint}")
                
                if response.status_code in [200, 201, 202]:
                    print(f"✓ {endpoint}: Success")
                    results["successful_methods"].append(endpoint)
                    results[f"export_{endpoint.replace('/', '_')}"] = response.json()
                else:
                    print(f"✗ {endpoint}: {response.status_code}")
            except Exception as e:
                print(f"✗ {endpoint}: {str(e)}")
        
        # Method 3: Try to get app script and analyze it
        print("\n=== Method 3: Analyzing app script ===")
        try:
            script_response = requests.get(
                f"{self.api_base_url}/apps/{app_id}/script",
                headers=self.headers,
                timeout=30
            )
            
            if script_response.status_code == 200:
                script_data = script_response.json()
                script = script_data.get('qScript', '')
                
                results["script"] = {
                    "has_script": bool(script.strip()),
                    "script_length": len(script),
                    "script_preview": script[:500] + "..." if len(script) > 500 else script,
                    "tables_loaded": self._extract_tables_from_script(script)
                }
                print(f"✓ Script: Found {len(script)} characters")
                results["successful_methods"].append("script")
            else:
                print(f"✗ Script: {script_response.status_code}")
                results["script"] = {"error": f"Status {script_response.status_code}"}
        except Exception as e:
            print(f"✗ Script: {str(e)}")
            results["script"] = {"error": str(e)}
        
        # Method 4: Try to access via WebSocket (Engine API)
        print("\n=== Method 4: WebSocket connection (simplified) ===")
        ws_info = self._get_websocket_info(app_id)
        results["websocket_info"] = ws_info
        
        # Determine if data was found
        if len(results["successful_methods"]) > 0:
            results["data_found"] = True
        
        return results
    
    def _extract_tables_from_script(self, script: str) -> List[str]:
        """Extract table names from Qlik script"""
        tables = []
        lines = script.split('\n')
        
        for line in lines:
            line = line.strip()
            # Look for LOAD statements
            if 'LOAD' in line.upper() and ';' not in line:
                # Try to extract table name before colon
                if ':' in line:
                    table_part = line.split(':')[0].strip()
                    if table_part and not table_part.startswith('//'):
                        tables.append(table_part)
        
        return tables
    
    def _get_websocket_info(self, app_id: str) -> Dict[str, Any]:
        """Get WebSocket connection info for Engine API"""
        ws_url = f"wss://{self.tenant_url.replace('https://', '')}/app/{app_id}"
        
        return {
            "websocket_url": ws_url,
            "headers_required": {
                "Authorization": f"Bearer {self.api_key}",
                "X-Qlik-User": f"UserDirectory={self.user_directory};UserId={self.user_id}"
            },
            "note": "Use websocket-client library to connect to this URL",
            "example_connection": f"""
import websocket
import json

ws = websocket.WebSocket()
ws.connect("{ws_url}", 
           header={{"Authorization": "Bearer {self.api_key}",
                    "X-Qlik-User": "UserDirectory={self.user_directory};UserId={self.user_id}"}})

# Open app
open_msg = {{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "OpenDoc",
    "handle": -1,
    "params": {{"qDocName": "{app_id}"}}
}}
ws.send(json.dumps(open_msg))
            """
        }
    
    def get_app_available_endpoints(self, app_id: str) -> Dict[str, Any]:
        """Discover available endpoints for this app"""
        endpoints_to_test = [
            # REST API endpoints
            f"/apps/{app_id}",
            f"/apps/{app_id}/sheets",
            f"/apps/{app_id}/objects", 
            f"/apps/{app_id}/fields",
            f"/apps/{app_id}/measures",
            f"/apps/{app_id}/variables",
            f"/apps/{app_id}/dimensions",
            f"/apps/{app_id}/script",
            f"/apps/{app_id}/reloads",
            f"/apps/{app_id}/bookmarks",
            f"/apps/{app_id}/layout",
            f"/apps/{app_id}/export/data",
            f"/apps/{app_id}/export/app",
            
            # QIX/Engine API endpoints
            f"/qix/apps/{app_id}",
            f"/qix/apps/{app_id}/fields",
            f"/qix/apps/{app_id}/measures",
            f"/qix/apps/{app_id}/dimensions",
            f"/qix/apps/{app_id}/data/model",
            f"/qix/data/apps/{app_id}/tables",
            
            # Data endpoints
            f"/data/apps/{app_id}/tables",
            f"/data/apps/{app_id}/fields",
            f"/data/apps/{app_id}/model",
        ]
        
        results = {}
        
        for endpoint in endpoints_to_test:
            try:
                # Try with both base URLs
                for base_url in [self.api_base_url, self.tenant_url]:
                    url = f"{base_url}{endpoint}"
                    
                    try:
                        if 'export' in endpoint:
                            response = requests.post(url, headers=self.headers, json={}, timeout=10)
                        else:
                            response = requests.get(url, headers=self.headers, timeout=10)
                        
                        results[endpoint] = {
                            "url": url,
                            "status_code": response.status_code,
                            "content_type": response.headers.get('content-type', ''),
                            "success": response.status_code in [200, 201, 202],
                            "response_preview": response.text[:200] if response.text else ""
                        }
                        
                        if response.status_code == 200:
                            break  # Stop trying other URLs if successful
                            
                    except:
                        continue
                        
            except Exception as e:
                results[endpoint] = {
                    "error": str(e),
                    "success": False
                }
        
        # Count successes
        successful = [e for e, r in results.items() if r.get('success')]
        
        return {
            "total_endpoints_tested": len(endpoints_to_test),
            "successful_endpoints": successful,
            "success_count": len(successful),
            "detailed_results": results
        }
    
    def try_direct_browser_access(self, app_id: str) -> Dict[str, Any]:
        """Get direct URLs for browser access"""
        return {
            "app_in_hub": f"{self.tenant_url}/hub/{app_id}",
            "app_editor": f"{self.tenant_url}/sense/app/{app_id}",
            "data_load_editor": f"{self.tenant_url}/sense/app/{app_id}/edit/script",
            "data_model_viewer": f"{self.tenant_url}/sense/app/{app_id}/edit/model",
            "sheet_view": f"{self.tenant_url}/sense/app/{app_id}/sheet",
            "note": "Open these URLs in browser while logged into Qlik Cloud"
        }