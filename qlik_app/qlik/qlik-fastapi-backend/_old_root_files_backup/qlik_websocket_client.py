


# qlik_websocket_client.py - FIXED VERSION FOR YOUR SETUP
import websocket
import json
import ssl
import socket
import jwt
import os
import time
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv()

from urllib.parse import urlparse


class QlikWebSocketClient:
    def __init__(self):
        raw_api_key = os.getenv('QLIK_API_KEY', '')
        raw_tenant_url = os.getenv('QLIK_TENANT_URL', '') or os.getenv('QLIK_API_BASE_URL', '')

        self.api_key = self._normalize_api_key(raw_api_key)
        self.tenant_url = self._derive_tenant_url(raw_tenant_url)

        if not self.api_key:
            raise ValueError("QLIK_API_KEY must be set in environment variables")
        if not self.tenant_url:
            raise ValueError("QLIK_TENANT_URL or QLIK_API_BASE_URL must be set in environment variables")

        # Decode JWT to get user info
        try:
            decoded = jwt.decode(self.api_key, options={"verify_signature": False})
            self.user_id = decoded.get('sub', '')
            self.user_directory = "QLIK"
        except Exception as e:
            print(f"Warning: Could not decode JWT: {e}")
            self.user_id = ""
            self.user_directory = "QLIK"

        self.ws = None
        self.connected = False
        self.app_handle = None
        self.request_id = 1

    def _normalize_api_key(self, api_key: str) -> str:
        """Normalize API key for use in Authorization header."""
        key = api_key.strip().strip('"').strip("'")
        if key.lower().startswith("bearer "):
            key = key.split(" ", 1)[1].strip()
        return key

    def _derive_tenant_url(self, raw_url: str) -> str:
        """Derive tenant base URL from either QLIK_TENANT_URL or QLIK_API_BASE_URL."""
        if not raw_url:
            return ""

        url = raw_url.strip().rstrip('/')

        # If this looks like an API base URL, drop the /api/v1 portion
        if url.lower().endswith('/api/v1'):
            url = url[: -len('/api/v1')]
        elif url.lower().endswith('/api/v2'):
            url = url[: -len('/api/v2')]

        # Ensure scheme is present
        parsed = urlparse(url)
        if not parsed.scheme:
            url = 'https://' + url
        return url.rstrip('/')
        
    def _get_next_id(self) -> int:
        """Get next request ID"""
        current_id = self.request_id
        self.request_id += 1
        return current_id
    
    def _receive_response(self, expected_id: int = None, timeout: int = 30) -> Dict[str, Any]:
        """
        Receive response from WebSocket, handling OnConnected and other system messages
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = self.ws.recv()
                response_data = json.loads(response)
                
                # Check if this is a system message (OnConnected, etc.)
                if 'method' in response_data:
                    method = response_data['method']
                    print(f"   Received system message: {method}")
                    
                    if method == "OnConnected":
                        # This is normal, continue waiting for actual response
                        continue
                    else:
                        # Other system messages, log and continue
                        print(f"   System message data: {json.dumps(response_data, indent=2)}")
                        continue
                
                # Check if this is the response we're waiting for
                if expected_id is not None and response_data.get('id') == expected_id:
                    return response_data
                elif expected_id is None:
                    return response_data
                else:
                    # Not the response we're waiting for, continue
                    print(f"   Received response with different ID: {response_data.get('id')}")
                    continue
                    
            except websocket.WebSocketTimeoutException:
                continue
            except Exception as e:
                print(f"   Error receiving response: {e}")
                raise
        
        raise TimeoutError(f"Did not receive response within {timeout} seconds")
    
    def connect_to_app(self, app_id: str) -> bool:
        """Connect to a specific app via WebSocket"""
        last_error = None
        try:
            # Clean up tenant URL
            tenant_host = self.tenant_url.replace('https://', '').replace('http://', '')
            ws_url = f"wss://{tenant_host}/app/{app_id}"
            
            print(f"\n{'='*60}")
            print(f"Connecting to Qlik app via WebSocket")
            print(f"URL: {ws_url}")
            print(f"{'='*60}")
            
            # Create WebSocket connection with timeout
            self.ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
            self.ws.settimeout(10)  # Set timeout for receiving messages
            
            # Connect with headers
            headers = {
                "Authorization": f"Bearer {self.api_key}",
            }
            
            if self.user_id:
                headers["X-Qlik-User"] = f"UserDirectory={self.user_directory};UserId={self.user_id}"

            for attempt in range(1, 4):
                try:
                    self.ws = websocket.WebSocket(sslopt={"cert_reqs": ssl.CERT_NONE})
                    self.ws.settimeout(10)
                    self.ws.connect(ws_url, header=headers, timeout=10)
                    self.connected = True
                    print("✓ WebSocket connected successfully")
                    break
                except (TimeoutError, socket.timeout, websocket.WebSocketTimeoutException, OSError) as exc:
                    last_error = exc
                    self.connected = False
                    try:
                        if self.ws:
                            self.ws.close()
                    except Exception:
                        pass
                    print(f"⚠️  WebSocket connect attempt {attempt}/3 failed: {exc}")
                    if attempt == 3:
                        raise
                    time.sleep(attempt)
            
            # Open the app
            return self._open_app(app_id)
            
        except Exception as e:
            if last_error is not None:
                print(f"✗ Connection error after retries: {str(last_error)}")
            else:
                print(f"✗ Connection error: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    def _open_app(self, app_id: str) -> bool:
        """Open the app and get its handle"""
        try:
            # Open app message
            request_id = self._get_next_id()
            open_msg = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "OpenDoc",
                "handle": -1,
                "params": {
                    "qDocName": app_id,
                    "qNoData": False
                }
            }
            
            print(f"\nSending OpenDoc request (ID: {request_id})...")
            self.ws.send(json.dumps(open_msg))
            
            # Receive response, handling OnConnected messages
            response_data = self._receive_response(expected_id=request_id)
            
            print(f"OpenDoc response: {json.dumps(response_data, indent=2)}")
            
            if 'result' in response_data:
                if 'qReturn' in response_data['result']:
                    self.app_handle = response_data['result']['qReturn']['qHandle']
                    print(f"✓ App opened successfully with handle: {self.app_handle}")
                    return True
                elif 'qHandle' in response_data['result']:
                    # Some versions return qHandle directly
                    self.app_handle = response_data['result']['qHandle']
                    print(f"✓ App opened successfully with handle: {self.app_handle}")
                    return True
                else:
                    print(f"✗ Failed to get app handle from response")
                    return False
            elif 'error' in response_data:
                error = response_data['error']
                print(f"✗ Error opening app: {error.get('message', 'Unknown error')}")
                return False
            else:
                print(f"✗ Unexpected response format")
                return False
                
        except Exception as e:
            print(f"✗ Error opening app: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    def send_request(self, method: str, params: Dict = None, handle: int = None) -> Dict[str, Any]:
        """Send a request to Qlik Engine"""
        if not self.connected:
            raise ConnectionError("Not connected to Qlik")
        
        if handle is None:
            handle = self.app_handle
        
        request_id = self._get_next_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "handle": handle,
            "params": params or {}
        }
        
        print(f"\nSending request: {method} (ID: {request_id})")
        self.ws.send(json.dumps(request))
        
        # Receive response
        response_data = self._receive_response(expected_id=request_id)
        
        return response_data
    
    def get_app_tables_simple(self, app_id: str) -> Dict[str, Any]:
        """Get comprehensive table information from app"""
        try:
            # Connect to app
            if not self.connect_to_app(app_id):
                return {"success": False, "error": "Failed to connect to app"}
            
            # Method 1: Get data model metadata
            print("\n" + "="*60)
            print("Getting data model information...")
            print("="*60)
            
            tables_info = self._get_tables_from_data_model()
            
            # Method 2: Get all fields
            print("\nGetting field list...")
            fields_info = self._get_all_fields()
            
            # Method 3: Get app layout for metadata
            print("\nGetting app layout...")
            layout_info = self._get_app_layout()
            
            # Method 4: Get script
            print("\nGetting app script...")
            script_info = self._get_app_script()
            
            # Method 5: Get sheets
            print("\nGetting sheets...")
            sheets_info = self._get_sheets()
            
            # Close connection
            self.close()
            
            # Combine all information
            result = {
                "success": True,
                "app_id": app_id,
                "app_title": layout_info.get("title", "Unknown"),
                "tables": tables_info.get("tables", []),
                "all_fields": fields_info.get("fields", []),
                "field_count": len(fields_info.get("fields", [])),
                "script": script_info.get("script", ""),
                "script_tables": script_info.get("tables", []),
                "sheets": sheets_info.get("sheets", []),
                "data_model": tables_info.get("raw_data"),
                "summary": {
                    "table_count": len(tables_info.get("tables", [])),
                    "total_fields": len(fields_info.get("fields", [])),
                    "sheet_count": len(sheets_info.get("sheets", [])),
                    "has_script": len(script_info.get("script", "")) > 0,
                    "script_table_count": len(script_info.get("tables", []))
                }
            }
            
            return result
            
        except Exception as e:
            self.close()
            print(f"\n✗ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
        
      

    def get_script_table_details(self, app_id: str, table_name: str) -> Optional[dict]:
        """
        Fetch detailed metadata for a script-only table by name.
        Returns dict with keys: name, fields, field_count, no_of_rows, is_synthetic.
        """
        try:
            if not self.connected or not self.app_handle:
                if not self.connect_to_app(app_id):
                    return None

            # Call GetTablesAndKeys to get all tables
            tables_response = self._get_tables_from_data_model()
            if not tables_response.get("success"):
                return None

            for table in tables_response.get("tables", []):
                if table.get("name") == table_name:
                    return table

            return None  # Table not found

        except Exception as e:
            print(f"Error fetching script table details for {table_name}: {e}")
            return None

    # ... other methods below ...

    
    def _get_tables_from_data_model(self) -> Dict[str, Any]:
        """Get tables from data model"""
        try:
            # Get table list
            response = self.send_request("GetTablesAndKeys", {
                "qWindowSize": {
                    "qcx": 100,
                    "qcy": 100
                },
                "qNullSize": {
                    "qcx": 0,
                    "qcy": 0
                },
                "qCellHeight": 30,
                "qSyntheticMode": False,
                "qIncludeSysVars": True,
                "qIncludeProfiling": False
            })
            
            tables = []
            
            if 'result' in response:
                result = response['result']
                
                # Parse table data
                if 'qtr' in result:
                    for table_data in result['qtr']:
                        table_name = table_data.get('qName', 'Unknown')
                        
                        # Get fields for this table
                        fields = []
                        if 'qFields' in table_data:
                            for field in table_data['qFields']:
                                fields.append({
                                    "name": field.get('qName', ''),
                                    "is_key": field.get('qIsKey', False),
                                    "is_system": field.get('qIsSystem', False),
                                    "is_hidden": field.get('qIsHidden', False),
                                    "tags": field.get('qTags', [])
                                })
                        
                        tables.append({
                            "name": table_name,
                            "fields": fields,
                            "field_count": len(fields),
                            "is_synthetic": table_data.get('qIsSynthetic', False),
                            "no_of_rows": table_data.get('qNoOfRows', 0)
                        })
            
            print(f"✓ Found {len(tables)} tables")
            return {
                "success": True,
                "tables": tables,
                "raw_data": response
            }
            
        except Exception as e:
            print(f"Error getting tables: {str(e)}")
            return {"success": False, "error": str(e), "tables": []}
    
    def _get_all_fields(self) -> Dict[str, Any]:
        """Get all fields from the app"""
        try:
            # Create a proper FieldList session object
            field_list_response = self.send_request("CreateSessionObject", [{
                "qInfo": {
                    "qType": "FieldList"
                },
                "qFieldListDef": {
                    "qShowSystem": True,
                    "qShowHidden": True,
                    "qShowSemantic": True,
                    "qShowSrcTables": True
                }
            }])
            
            fields = []
            
            if 'result' in field_list_response and 'qReturn' in field_list_response['result']:
                field_obj_handle = field_list_response['result']['qReturn']['qHandle']
                
                # Get layout of field list
                layout_response = self.send_request("GetLayout", {}, handle=field_obj_handle)
                
                if 'result' in layout_response and 'qLayout' in layout_response['result']:
                    field_list = layout_response['result']['qLayout'].get('qFieldList', {}).get('qItems', [])
                    
                    for field in field_list:
                        fields.append({
                            "name": field.get('qName', ''),
                            "cardinal": field.get('qCardinal', 0),
                            "tags": field.get('qTags', []),
                            "is_system": field.get('qIsSystem', False),
                            "is_hidden": field.get('qIsHidden', False),
                            "is_semantic": field.get('qIsSemantic', False),
                            "src_tables": field.get('qSrcTables', [])
                        })
                
                # Destroy the session object
                self.send_request("DestroySessionObject", {"qId": str(field_obj_handle)}, handle=-1)
            
            print(f"✓ Found {len(fields)} fields")
            return {
                "success": True,
                "fields": fields
            }
            
        except Exception as e:
            print(f"Error getting fields: {str(e)}")
            return {"success": False, "error": str(e), "fields": []}
    
    def _get_app_layout(self) -> Dict[str, Any]:
        """Get app layout"""
        try:
            response = self.send_request("GetAppLayout", {})
            
            title = "Unknown"
            if 'result' in response and 'qLayout' in response['result']:
                title = response['result']['qLayout'].get('qTitle', 'Unknown')
            
            print(f"✓ App title: {title}")
            return {
                "success": True,
                "title": title,
                "raw_data": response
            }
            
        except Exception as e:
            print(f"Error getting app layout: {str(e)}")
            return {"success": False, "error": str(e), "title": "Unknown"}
    
    def _get_app_script(self) -> Dict[str, Any]:
        """Get app script"""
        try:
            response = self.send_request("GetScript", {})
            
            script = ""
            tables = []
            
            if 'result' in response and 'qScript' in response['result']:
                script = response['result']['qScript']
                
                # Parse script to extract table names
                tables = self._parse_tables_from_script(script)
            
            print(f"✓ Script length: {len(script)} characters")
            print(f"✓ Found {len(tables)} tables in script")
            
            return {
                "success": True,
                "script": script,
                "tables": tables
            }
            
        except Exception as e:
            print(f"Error getting script: {str(e)}")
            return {"success": False, "error": str(e), "script": "", "tables": []}
    
    def _parse_tables_from_script(self, script: str) -> List[str]:
        """Parse table names from Qlik script"""
        tables = []
        lines = script.split('\n')
        
        current_table = None
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            
            # Look for table name before colon
            if ':' in line and not line_stripped.startswith('//'):
                # Check if next few lines contain LOAD
                next_lines = '\n'.join(lines[i:i+5])
                if 'LOAD' in next_lines.upper():
                    parts = line.split(':')
                    if len(parts) >= 2:
                        table_name = parts[0].strip()
                        # Clean up table name
                        table_name = table_name.replace('[', '').replace(']', '')
                        if table_name and not table_name.startswith('//'):
                            tables.append(table_name)
        
        return list(set(tables))  # Remove duplicates
    
    def _get_sheets(self) -> Dict[str, Any]:
        """Get sheets from the app"""
        try:
            response = self.send_request("CreateSessionObject", [{
                "qInfo": {
                    "qType": "SheetList"
                },
                "qAppObjectListDef": {
                    "qType": "sheet",
                    "qData": {
                        "title": "/qMetaDef/title",
                        "description": "/qMetaDef/description",
                        "thumbnail": "/thumbnail",
                        "cells": "/cells",
                        "rank": "/rank",
                        "columns": "/columns",
                        "rows": "/rows"
                    }
                }
            }])
            
            sheets = []
            
            if 'result' in response and 'qReturn' in response['result']:
                sheet_obj_handle = response['result']['qReturn']['qHandle']
                
                # Get layout
                layout_response = self.send_request("GetLayout", {}, handle=sheet_obj_handle)
                
                if 'result' in layout_response and 'qLayout' in layout_response['result']:
                    app_obj_list = layout_response['result']['qLayout'].get('qAppObjectList', {})
                    items = app_obj_list.get('qItems', [])
                    
                    for item in items:
                        sheets.append({
                            "id": item.get('qInfo', {}).get('qId', ''),
                            "title": item.get('qData', {}).get('title', 'Untitled'),
                            "description": item.get('qData', {}).get('description', ''),
                            "rank": item.get('qData', {}).get('rank', 0)
                        })
                
                # Destroy the session object
                self.send_request("DestroySessionObject", {"qId": str(sheet_obj_handle)}, handle=-1)
            
            print(f"✓ Found {len(sheets)} sheets")
            return {
                "success": True,
                "sheets": sheets
            }
            
        except Exception as e:
            print(f"Error getting sheets: {str(e)}")
            return {"success": False, "error": str(e), "sheets": []}
    
    def get_field_values(self, app_id: str, field_name: str, limit: int = 100) -> Dict[str, Any]:
        """Get values for a specific field with actual data"""
        try:
            if not self.connected or not self.app_handle:
                if not self.connect_to_app(app_id):
                    return {"success": False, "error": "Failed to connect to app"}
            
            print(f"\n{'='*60}")
            print(f"Getting values for field: {field_name}")
            print(f"{'='*60}")
            
            # Create a list object for the field
            hypercube_def = {
                "qInfo": {
                    "qType": "ListObject"
                },
                "qListObjectDef": {
                    "qStateName": "$",
                    "qLibraryId": "",
                    "qDef": {
                        "qFieldDefs": [field_name],
                        "qFieldLabels": [field_name],
                        "qSortCriterias": [{
                            "qSortByState": 0,
                            "qSortByFrequency": 0,
                            "qSortByNumeric": 1,
                            "qSortByAscii": 1,
                            "qSortByLoadOrder": 1,
                            "qSortByExpression": 0,
                            "qExpression": {
                                "qv": ""
                            }
                        }]
                    },
                    "qAutoSort": True,
                    "qFrequencyMode": "V",
                    "qShowAlternatives": True,
                    "qInitialDataFetch": [{
                        "qTop": 0,
                        "qLeft": 0,
                        "qWidth": 1,
                        "qHeight": min(limit, 10000)
                    }]
                }
            }
            
            # Create session object
            create_response = self.send_request("CreateSessionObject", [hypercube_def])
            
            if 'result' in create_response and 'qReturn' in create_response['result']:
                object_handle = create_response['result']['qReturn']['qHandle']
                print(f"✓ Created list object with handle: {object_handle}")
                
                # Get layout which includes the data
                layout_response = self.send_request("GetLayout", {}, handle=object_handle)
                
                values = []
                if 'result' in layout_response and 'qLayout' in layout_response['result']:
                    list_object = layout_response['result']['qLayout'].get('qListObject', {})
                    data_pages = list_object.get('qDataPages', [])
                    
                    for page in data_pages:
                        matrix = page.get('qMatrix', [])
                        for row in matrix:
                            if row and len(row) > 0:
                                cell = row[0]
                                values.append({
                                    "text": cell.get('qText', ''),
                                    "number": cell.get('qNum', None),
                                    "element_number": cell.get('qElemNumber', -1),
                                    "state": cell.get('qState', ''),
                                    "frequency": cell.get('qFrequency', '')
                                })
                
                print(f"✓ Retrieved {len(values)} values")
                
                # Destroy the session object
                self.send_request("DestroySessionObject", {"qId": str(object_handle)}, handle=-1)
                
                self.close()
                
                return {
                    "success": True,
                    "field_name": field_name,
                    "values": values,
                    "value_count": len(values),
                    "sample_values": [v["text"] for v in values[:10]]
                }
            else:
                self.close()
                return {
                    "success": False,
                    "error": "Could not create list object",
                    "response": create_response
                }
                
        except Exception as e:
            self.close()
            print(f"✗ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def get_table_data(self, app_id: str, table_name: str, limit: int = 100, offset: int = 0) -> Dict[str, Any]:
        """Get actual data from a specific table with improved hypercube handling.
        Supports offset/limit paging so callers can request a specific window of rows."""
        try:
            if not self.connected or not self.app_handle:
                if not self.connect_to_app(app_id):
                    return {"success": False, "error": "Failed to connect to app"}
            
            print(f"\n{'='*60}")
            print(f"Getting data from table: {table_name} (offset={offset} limit={limit})")
            print(f"{'='*60}")
            
            # FIRST: Try script parsing (for CSV and INLINE data) - fastest approach
            print(f"📜 Attempting script-based extraction first...")
            try:
                script_result = self._get_app_script()
                if script_result.get("success") and script_result.get("script"):
                    script = script_result.get("script", "")
                    try:
                        from qlik_script_parser import QlikScriptParser
                        # request up to offset+limit so we can slice the returned rows
                        table_preview = QlikScriptParser.get_table_preview(script, table_name, offset + (limit if limit and limit>0 else 0))
                        
                        if table_preview.get("success"):
                            all_rows = table_preview.get("rows", [])
                            columns = table_preview.get("columns", [])
                            print(f"✅ Successfully extracted {len(all_rows)} rows from script")

                            # apply offset/limit slice
                            window = all_rows[offset: offset + limit] if limit and limit > 0 else all_rows[offset:]
                            self.close()
                            return {
                                "success": True,
                                "table_name": table_name,
                                "columns": columns,
                                "rows": window,
                                "row_count": len(all_rows),
                                "source": "script"
                            }
                    except Exception as e:
                        print(f"⚠️ Script parsing attempt failed: {str(e)}")
            except Exception as e:
                print(f"⚠️ Could not attempt script parsing: {str(e)}")
            
            # SECOND: Fall back to hypercube for data model tables
            print(f"🔄 Falling back to hypercube method...")
            
            # Get fields for this table (SAFE MATCHING VERSION)
            tables_info = self._get_tables_from_data_model()

            requested = table_name.strip().lower()
            matched_table = None

            print("Available tables from data model:")
            for t in tables_info.get("tables", []):
                print(" -", t.get("name"))

            for table in tables_info.get("tables", []):
                actual_name = (table.get("name") or "").strip()

                # Exact match
                if actual_name.lower() == requested:
                    matched_table = table
                    break

                # Partial match (handles -1, $Syn, etc.)
                if actual_name.lower().startswith(requested):
                    matched_table = table
                    break

            if not matched_table:
                return {"success": False, "error": f"Table '{table_name}' not found in data model"}

            table_fields = [
                f["name"]
                for f in matched_table.get("fields", [])
                if not f.get("is_system", False)
            ]

            if not table_fields:
                return {"success": False, "error": f"Table '{table_name}' has no usable fields"}

            print(f"Found {len(table_fields)} fields in table: {table_fields}")

            # Build a STRAIGHT-TABLE hypercube using ALL fields as dimensions.
            # Do NOT rely on qInitialDataFetch — we'll page explicitly using GetHyperCubeData
            hypercube_def = {
                "qInfo": {"qType": "HyperCube"},
                "qHyperCubeDef": {
                    "qMode": "S",
                    "qDimensions": [
                        {"qDef": {"qFieldDefs": [f], "qFieldLabels": [f]}, "qNullSuppression": False}
                        for f in table_fields
                    ],
                    "qMeasures": [],
                    "qInitialDataFetch": [],
                    "qSuppressZero": False,
                    "qSuppressMissing": False
                }
            }

            print(f"Creating straight-table hypercube with {len(table_fields)} dimensions (all fields) ...")

            # Create session object
            try:
                create_response = self.send_request("CreateSessionObject", [hypercube_def])

                print(f"CreateSessionObject response: {json.dumps(create_response, indent=2)[:800]}")

                if 'result' not in create_response:
                    print("⚠️ CreateSessionObject failed (no result), trying alternative...")
                    return self._get_table_data_alternative(table_name, table_fields, limit, matched_table)

                result = create_response.get('result', {})
                if 'error' in result:
                    print(f"❌ Hypercube creation error: {result['error']}")
                    return self._get_table_data_alternative(table_name, table_fields, limit, matched_table)

                if 'qReturn' not in result:
                    print(f"❌ No qReturn in response: {result}")
                    return self._get_table_data_alternative(table_name, table_fields, limit, matched_table)

                object_handle = result['qReturn']['qHandle']
                print(f"✓ Created hypercube with handle: {object_handle}")

                # Get layout to determine total size
                print(f"Getting layout for hypercube...")
                layout_response = self.send_request("GetLayout", {}, handle=object_handle)
                print(f"GetLayout response keys: {layout_response.keys() if isinstance(layout_response, dict) else 'N/A'}")

                rows: list = []
                column_names = table_fields

                total_rows = 0
                n_cols = len(column_names)
                if 'result' in layout_response and 'qLayout' in layout_response['result']:
                    hypercube = layout_response['result']['qLayout'].get('qHyperCube', {})
                    size = hypercube.get('qSize', {})
                    total_rows = size.get('qcy', 0) if isinstance(size, dict) else 0
                    n_cols = size.get('qcx', n_cols) if isinstance(size, dict) else n_cols
                    print(f"✓ HyperCube reported size: {total_rows} rows × {n_cols} cols")
                else:
                    total_rows = 0

                # Page through the hypercube starting at `offset` (supports windowed requests)
                page_size = 1000
                top = offset if offset and offset > 0 else 0

                # Determine the fetch target (stop when we reach this row index)
                if limit and limit > 0:
                    target = (offset if offset and offset > 0 else 0) + limit
                    # If engine reported total_rows, don't go beyond it
                    if total_rows and total_rows > 0:
                        target = min(target, total_rows)
                else:
                    # No explicit limit: fetch until engine-reported total or until pages run out
                    target = total_rows if total_rows and total_rows > 0 else None

                # If offset is beyond available rows, return empty result early
                if total_rows and offset and offset >= total_rows:
                    print(f"⚠️ Requested offset {offset} >= total_rows {total_rows}; returning empty window")
                    self.send_request("DestroySessionObject", {"qId": str(object_handle)}, handle=-1)
                    self.close()
                    return {
                        "success": True,
                        "table_name": table_name,
                        "columns": column_names,
                        "rows": [],
                        "row_count": total_rows,
                        "source": "hypercube"
                    }

                # Loop and page
                while True:
                    try:
                        # Stop conditions
                        if target is not None and top >= target:
                            break
                        if total_rows and top >= total_rows:
                            break

                        # Decide how many rows to request in this page
                        if target is not None:
                            rows_to_fetch = min(page_size, max(0, target - top))
                        else:
                            rows_to_fetch = page_size

                        if rows_to_fetch <= 0:
                            break

                        page_result = self.send_request(
                            "GetHyperCubeData",
                            [
                                "/qHyperCubeDef",
                                [
                                    {
                                        "qLeft": 0,
                                        "qTop": top,
                                        "qWidth": n_cols,
                                        "qHeight": rows_to_fetch,
                                    }
                                ],
                            ],
                            handle=object_handle,
                        )

                        qPages = (page_result.get('result') or {}).get('qDataPages', []) if isinstance(page_result, dict) else []
                        if not qPages:
                            # Some engines return qDataPages at top-level
                            qPages = page_result.get('qDataPages', []) if isinstance(page_result, dict) else []

                        if not qPages:
                            print(f"⚠️ No data pages returned for top={top} height={rows_to_fetch}")
                            break

                        for page in qPages:
                            matrix = page.get('qMatrix', [])
                            for row_data in matrix:
                                row_values = {}
                                for i, cell in enumerate(row_data):
                                    if i < len(column_names):
                                        text_value = cell.get('qText')
                                        num_value = cell.get('qNum')
                                        if text_value and text_value != "":
                                            row_values[column_names[i]] = text_value
                                        elif num_value is not None:
                                            row_values[column_names[i]] = num_value
                                        else:
                                            row_values[column_names[i]] = None
                                rows.append(row_values)

                        top += rows_to_fetch
                        print(f"  Fetched rows {top - rows_to_fetch}–{top} / {total_rows or 'unknown'}")

                        # Safety: stop if we've reached requested limit (when limit given)
                        if limit and limit > 0 and len(rows) >= limit:
                            break

                        # If engine reported total rows and we've reached it, stop
                        if total_rows and top >= total_rows:
                            break

                    except Exception as e:
                        print(f"⚠️ Error paging hypercube data: {e}")
                        break

                # If we somehow fetched more than requested (safety), trim to window
                if offset and offset > 0:
                    rows = rows[:limit] if limit and limit > 0 else rows

                print(f"✓ Retrieved {len(rows)} rows from hypercube (target={target})")


                print(f"✓ Retrieved {len(rows)} rows from hypercube (target={target})")
                if rows:
                    print(f"  First row sample: {list(rows[0].items())[:2]}")

                # Destroy session object
                self.send_request("DestroySessionObject", {"qId": str(object_handle)}, handle=-1)

                self.close()

                return {
                    "success": True,
                    "table_name": table_name,
                    "columns": column_names,
                    "rows": rows,
                    "row_count": len(rows),
                    "source": "hypercube"
                }
            except Exception as e:
                print(f"❌ Exception during hypercube operations: {str(e)}")
                import traceback
                traceback.print_exc()
                return self._get_table_data_alternative(table_name, table_fields, limit, matched_table)
                
        except Exception as e:
            self.close()
            print(f"✗ Error: {str(e)}")
            import traceback
            traceback.print_exc()
            return {"success": False, "error": str(e)}
    
    def _get_table_data_alternative(self, table_name: str, table_fields: list, limit: int, table_info: dict) -> Dict[str, Any]:
        """Alternative method when hypercube fails - tries field values extraction"""
        try:
            print(f"\n📊 Hypercube failed, trying field values extraction...")
            all_field_values = {}
            
            for field in table_fields[:min(5, len(table_fields))]:  # Limit to 5 fields
                try:
                    # Try to get values for each field
                    field_result = self.send_request("GetField", {
                        "qFieldName": field
                    })
                    
                    if 'result' in field_result and 'qReturn' in field_result['result']:
                        field_handle = field_result['result']['qReturn']['qHandle']
                        
                        # Get values
                        values_response = self.send_request("GetValues", {
                            "qHandle": field_handle,
                            "qMaxValues": min(limit, 100)
                        }, handle=-1)
                        
                        if 'result' in values_response:
                            values = []
                            for item in values_response.get('result', {}).get('qValues', []):
                                values.append(item.get('qText', ''))
                            all_field_values[field] = values[:limit]
                except Exception as e:
                    print(f"⚠️ Could not get values for field {field}: {str(e)}")
                    continue
            
            # If we got field values, build rows from them
            if all_field_values and any(all_field_values.values()):
                print(f"✅ Got field values, building rows...")
                max_len = max(len(v) for v in all_field_values.values()) if all_field_values else 0
                rows = []
                
                for i in range(min(limit, max_len)):
                    row = {}
                    for field, values in all_field_values.items():
                        row[field] = values[i] if i < len(values) else ""
                    rows.append(row)
                
                self.close()
                return {
                    "success": True,
                    "table_name": table_name,
                    "columns": list(all_field_values.keys()),
                    "rows": rows,
                    "row_count": len(rows),
                    "source": "field_values"
                }
            
            # LAST RESORT: Try one more time with simpler query
            print(f"⚠️ Attempt 1 failed, trying simpler hypercube approach...")
            try:
                # Try with just first field as dimension
                if table_fields:
                    simple_hc = {
                        "qInfo": {"qType": "HyperCube"},
                        "qHyperCubeDef": {
                            "qMode": "P",  # Try pivot mode
                            "qDimensions": [{"qDef": {"qFieldDefs": [table_fields[0]]}}],
                            "qMeasures": [{"qDef": {"qDef": f"Count()"}}],
                            "qInitialDataFetch": [{"qTop": 0, "qLeft": 0, "qWidth": len(table_fields), "qHeight": min(limit, 100)}],
                        }
                    }
                    
                    simple_create = self.send_request("CreateSessionObject", [simple_hc])
                    if 'result' in simple_create and 'qReturn' in simple_create['result']:
                        simple_handle = simple_create['result']['qReturn']['qHandle']
                        simple_layout = self.send_request("GetLayout", {}, handle=simple_handle)
                        
                        if 'result' in simple_layout:
                            hc = simple_layout['result']['qLayout'].get('qHyperCube', {})
                            data_pages = hc.get('qDataPages', [])
                            rows = []
                            
                            for page in data_pages:
                                matrix = page.get('qMatrix', [])
                                for row_data in matrix:
                                    row = {}
                                    for idx, cell in enumerate(row_data):
                                        if idx < len(table_fields):
                                            text_val = cell.get('qText', '')
                                            num_val = cell.get('qNum')
                                            row[table_fields[idx]] = text_val if text_val else (num_val if num_val is not None else '')
                                    rows.append(row)
                            
                            if rows:
                                print(f"✅ Got {len(rows)} rows from simple hypercube")
                                self.send_request("DestroySessionObject", {"qId": str(simple_handle)}, handle=-1)
                                self.close()
                                return {
                                    "success": True,
                                    "table_name": table_name,
                                    "columns": table_fields,
                                    "rows": rows,
                                    "row_count": len(rows),
                                    "source": "simple_hypercube"
                                }
                        
                        self.send_request("DestroySessionObject", {"qId": str(simple_handle)}, handle=-1)
            except Exception as e:
                print(f"⚠️ Simple hypercube also failed: {str(e)}")
            
            # EXTREME FALLBACK: Show structure with row count from metadata
            print(f"⚠️ All extraction methods failed, showing metadata structure...")
            rows = []
            row_count = table_info.get("no_of_rows", 0)
            
            if row_count > 0:
                print(f"Table has {row_count} rows according to metadata")
                for i in range(min(limit, row_count)):
                    row = {}
                    for field in table_fields[:10]:
                        row[field] = f"[Row {i+1} data not accessible - check QlikCloud load]"
                    rows.append(row)
            
            self.close()
            
            return {
                "success": True,
                "table_name": table_name,
                "columns": table_fields[:10],
                "rows": rows if rows else [],
                "row_count": row_count,
                "note": "Data structure from metadata - full data access limited"
            }
        except Exception as e:
            self.close()
            print(f"❌ Alternative retrieval also failed: {str(e)}")
            return {
                "success": False,
                "error": f"Could not retrieve table data: {str(e)}",
                "table_name": table_name,
                "columns": table_fields[:10]
            }
    
    def _get_app_script_websocket(self, app_id: str) -> Dict[str, Any]:
        """
        Fetch the actual app script via WebSocket Engine API
        Returns the real loadscript with all tables and definitions
        """
        try:
            print(f"\n📍 WebSocket: Connecting to app {app_id[:8]}...")
            
            # Connect to the app
            if not self.connect_to_app(app_id):
                return {
                    "success": False,
                    "error": "Could not connect to app",
                    "script": "",
                    "tables": []
                }
            
            print("📍 WebSocket: Requesting GetScript...")
            # Get the script using the existing method
            script_result = self._get_app_script()
            
            if script_result.get("success"):
                print(f"✅ WebSocket: Script retrieved ({len(script_result.get('script', ''))} chars)")
                return {
                    "success": True,
                    "app_name": app_id,
                    "script": script_result.get("script", ""),
                    "tables": script_result.get("tables", []),
                    "method": "websocket_engine_api"
                }
            else:
                return {
                    "success": False,
                    "error": script_result.get("error", "Unknown error"),
                    "script": "",
                    "tables": []
                }
        except Exception as e:
            print(f"❌ WebSocket script fetch error: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "script": "",
                "tables": []
            }
        finally:
            # Close connection
            try:
                self.close()
            except:
                pass
    
    def get_data_model(self, app_id: str) -> Dict[str, Any]:
        """
        Public method called by LoadScriptFetcher.get_data_model_fields().

        The fetcher expects:
            { 'success': True, 'tables': [{'name': str, 'fields': [{'name': str}, ...]}, ...] }

        Previously this method did not exist — fetcher caught AttributeError silently,
        returned empty qlik_fields_map {}, and every LOAD * table published with zero columns.

        This connects to the app, calls _get_tables_from_data_model() (GetTablesAndKeys),
        normalises the result into the expected shape, then closes the connection.
        """
        try:
            print(f"[get_data_model] Connecting to app {app_id[:8]}... for GetTablesAndKeys")

            if not self.connect_to_app(app_id):
                return {"success": False, "error": "Could not connect to app", "tables": []}

            raw = self._get_tables_from_data_model()

            if not raw.get("success"):
                return {"success": False, "error": raw.get("error", "GetTablesAndKeys failed"), "tables": []}

            # _get_tables_from_data_model() already builds:
            #   { 'name': str, 'fields': [{'name': str, 'is_key': bool, ...}] }
            # which is exactly what the fetcher reads — pass through unchanged.
            tables = raw.get("tables", [])
            print(f"[get_data_model] SUCCESS: {len(tables)} tables from GetTablesAndKeys")
            for t in tables:
                print(f"   {t['name']} -> {len(t.get('fields', []))} fields")

            return {"success": True, "tables": tables}

        except Exception as exc:
            print(f"[get_data_model] ERROR: {exc}")
            return {"success": False, "error": str(exc), "tables": []}
        finally:
            try:
                self.close()
            except Exception:
                pass

    def close(self):
        """Close WebSocket connection"""
        if self.ws:
            try:
                self.ws.close()
                print("\n✓ WebSocket connection closed")
            except:
                pass
            self.ws = None
        self.connected = False
        self.app_handle = None


