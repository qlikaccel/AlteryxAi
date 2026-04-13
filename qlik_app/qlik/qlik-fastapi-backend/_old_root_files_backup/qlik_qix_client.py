import requests
import json
import os
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

class QlikQixClient:
    def __init__(self):
        self.api_key = os.getenv('QLIK_API_KEY')
        self.tenant_url = os.getenv('QLIK_TENANT_URL')
        self.base_url = self.tenant_url  # QIX endpoints are at tenant root
        
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def get_app_data_model(self, app_id: str) -> Dict[str, Any]:
        """Get the complete data model of the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/data/model"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "data_model": data,
                    "model_summary": self._summarize_data_model(data)
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def _summarize_data_model(self, data_model: Dict) -> Dict[str, Any]:
        """Summarize the data model"""
        summary = {
            "tables": [],
            "total_tables": 0,
            "total_fields": 0,
            "fields_by_table": {}
        }
        
        # The structure might vary, try different patterns
        if isinstance(data_model, dict):
            # Try to find tables
            if 'qAppObjectList' in data_model:
                # This might be the app object list
                pass
            elif 'qFieldDescriptions' in data_model:
                # This might be field descriptions
                fields = data_model.get('qFieldDescriptions', [])
                summary["total_fields"] = len(fields)
                summary["fields"] = [field.get('qName', '') for field in fields[:20]]  # First 20
            
            # Look for tables in various structures
            for key, value in data_model.items():
                if isinstance(value, list) and key.lower() in ['tables', 'qtreepages', 'qappobjectlist']:
                    summary["tables"] = [item.get('qName', str(item)) for item in value[:10]]
                    summary["total_tables"] = len(value)
        
        return summary
    
    def get_app_fields(self, app_id: str) -> Dict[str, Any]:
        """Get all fields from the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/fields"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                fields = []
                
                if isinstance(data, dict) and 'qFieldDescriptions' in data:
                    field_descriptions = data['qFieldDescriptions']
                    for field in field_descriptions:
                        fields.append({
                            "name": field.get('qName', ''),
                            "type": field.get('qType', ''),
                            "is_system": field.get('qIsSystem', False),
                            "is_hidden": field.get('qIsHidden', False),
                            "original_fields": field.get('qOriginalFields', []),
                            "semantic_type": field.get('qSemanticType', ''),
                            "cardinal": field.get('qCardinal', 0)
                        })
                
                return {
                    "success": True,
                    "fields": fields,
                    "total_fields": len(fields),
                    "field_names": [f["name"] for f in fields],
                    "raw_data": data
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_app_measures(self, app_id: str) -> Dict[str, Any]:
        """Get measures from the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/measures"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "measures": data,
                    "total_measures": len(data) if isinstance(data, list) else 0
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_app_dimensions(self, app_id: str) -> Dict[str, Any]:
        """Get dimensions from the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/dimensions"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "dimensions": data,
                    "total_dimensions": len(data) if isinstance(data, list) else 0
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_app_objects(self, app_id: str) -> Dict[str, Any]:
        """Get objects from the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/objects"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "objects": data,
                    "total_objects": len(data) if isinstance(data, list) else 0
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_app_sheets(self, app_id: str) -> Dict[str, Any]:
        """Get sheets from the app"""
        url = f"{self.base_url}/qix/apps/{app_id}/sheets"
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "sheets": data,
                    "total_sheets": len(data) if isinstance(data, list) else 0
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}",
                    "response_text": response.text[:500]
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_field_data(self, app_id: str, field_name: str, limit: int = 100) -> Dict[str, Any]:
        """Get sample data for a specific field"""
        # This would typically require a WebSocket connection
        # For now, we'll return the structure
        return {
            "success": True,
            "note": "Field data requires WebSocket/Engine API connection",
            "field_name": field_name,
            "websocket_url": f"wss://{self.tenant_url.replace('https://', '')}/app/{app_id}",
            "method": "Use WebSocket to call GetFieldData method",
            "example_request": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "GetFieldData",
                "handle": -1,
                "params": {
                    "qFieldName": field_name,
                    "qLimit": limit
                }
            }
        }
    
    def get_all_app_data(self, app_id: str) -> Dict[str, Any]:
        """Get all available data from the app using QIX endpoints"""
        print(f"\n{'='*80}")
        print(f"Fetching all QIX data for app: {app_id}")
        print(f"{'='*80}")
        
        results = {}
        
        # Get data model first
        print("\n1. Getting data model...")
        model_result = self.get_app_data_model(app_id)
        results["data_model"] = model_result
        
        # Get fields
        print("2. Getting fields...")
        fields_result = self.get_app_fields(app_id)
        results["fields"] = fields_result
        
        # Get other components
        endpoints = [
            ("measures", self.get_app_measures),
            ("dimensions", self.get_app_dimensions),
            ("objects", self.get_app_objects),
            ("sheets", self.get_app_sheets),
        ]
        
        for name, method in endpoints:
            print(f"3. Getting {name}...")
            try:
                result = method(app_id)
                results[name] = result
            except Exception as e:
                results[name] = {"success": False, "error": str(e)}
        
        # Create summary
        summary = {
            "app_id": app_id,
            "has_data_model": model_result.get("success", False),
            "has_fields": fields_result.get("success", False) and fields_result.get("total_fields", 0) > 0,
            "field_count": fields_result.get("total_fields", 0) if fields_result.get("success") else 0,
            "field_names": fields_result.get("field_names", [])[:20] if fields_result.get("success") else [],  # First 20
            "available_endpoints": [name for name, result in results.items() if result.get("success")],
            "direct_app_url": f"{self.tenant_url}/hub/{app_id}"
        }
        
        return {
            "success": True,
            "app_id": app_id,
            "summary": summary,
            "detailed_data": results
        }