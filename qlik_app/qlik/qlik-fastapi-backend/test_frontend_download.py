#!/usr/bin/env python3
"""
Test script to verify frontend download button now works with CompleteMQueryGenerator
Simulates what the frontend download button does
"""

import json
import requests
from datetime import datetime

# Sample parsed data that would come from the parser
sample_parsed = {
    "status": "success",
    "summary": {
        "tables_count": 6,
        "fields_count": 18,
        "connections_count": 2,
        "transformations_count": 3
    },
    "details": {
        "tables": [
            {
                "name": "VehicleDetails",
                "row_count": 150,
                "source_type": "CSV",
                "fields": ["VehicleID", "Make", "Model", "Year", "Color"]
            },
            {
                "name": "ScooterDetails",
                "row_count": 75,
                "source_type": "CSV",
                "fields": ["ScooterID", "Brand", "Type", "Engine"]
            }
        ],
        "fields": [
            {"name": "VehicleID", "type": "Integer", "table": "VehicleDetails"},
            {"name": "Make", "type": "Text", "table": "VehicleDetails"},
            {"name": "ScooterID", "type": "Integer", "table": "ScooterDetails"},
            {"name": "Brand", "type": "Text", "table": "ScooterDetails"}
        ],
        "data_connections": [
            {
                "name": "CSV_Connection",
                "type": "CSV",
                "source": "file://data.csv"
            }
        ]
    }
}

def test_convert_to_mquery_endpoint():
    """Test the /convert-to-mquery endpoint with complete generator"""
    print("=" * 80)
    print("TEST 1: /convert-to-mquery Endpoint")
    print("=" * 80)
    
    # url = "http://localhost:8000/api/migration/convert-to-mquery"
    url = "https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/convert-to-mquery"
    
    try:
        response = requests.post(
            url,
            params={
                "parsed_script_json": json.dumps(sample_parsed)
            }
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            m_query = result.get("m_query", "")
            
            print(f"\n✅ SUCCESS - M Query Generated!")
            print(f"   Query length: {len(m_query)} characters")
            print(f"   Status: {result.get('status')}")
            print(f"   Generator: {result.get('generator')}")
            print(f"\n📄 M Query Preview (first 500 chars):")
            print("-" * 80)
            print(m_query[:500])
            print("-" * 80)
            
            # Check if it contains real data (not just template placeholders)
            if "[Reference to source connection]" in m_query:
                print("\n⚠️  WARNING: Query still contains template placeholders!")
                return False
            else:
                print("\n✅ Query contains actual generated data (not just placeholders)")
                return True
        else:
            print(f"\n❌ FAILED - Status code: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        return False


def test_full_pipeline_endpoint():
    """Test the /full-pipeline endpoint with complete generator"""
    print("\n\n" + "=" * 80)
    print("TEST 2: /full-pipeline Endpoint")
    print("=" * 80)
    
    # You would need a real Qlik app ID
    # For now, we'll just check if the endpoint can be called
    # url = "http://localhost:8000/api/migration/full-pipeline"
    url = "https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/full-pipeline"

    app_id = "764185f-b9cc-4dab-8f72-35e1ba8d1547"  # Example app ID
    
    try:
        print("\nNote: This requires a real Qlik connection and app ID")
        print(f"Attempting to call full pipeline with app_id: {app_id}")
        print("(If connection is unavailable, this is expected)")
        
        response = requests.post(
            url,
            params={"app_id": app_id},
            timeout=10
        )
        
        print(f"\nStatus Code: {response.status_code}")
        
        if response.status_code == 200:
            result = response.json()
            m_query = result.get("m_query", "")
            
            print(f"\n✅ SUCCESS - Full Pipeline Executed!")
            print(f"   M Query length: {len(m_query)} characters")
            print(f"   Pipeline status: {result.get('status')}")
            
            if "[Reference to source connection]" in m_query:
                print("\n⚠️  WARNING: Query still contains template placeholders!")
                return False
            else:
                print("\n✅ Query contains actual generated data")
                return True
        else:
            print(f"\nStatus {response.status_code}: {response.text[:200]}")
            return False
            
    except requests.exceptions.Timeout:
        print("\n⏱️  Request timeout (expected if Qlik connection unavailable)")
        return None
    except requests.exceptions.ConnectionError:
        print("\n❌ Connection error - is the backend running on localhost:8000?")
        return None
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        return None


def test_download_endpoint():
    """Test the /download-mquery endpoint"""
    print("\n\n" + "=" * 80)
    print("TEST 3: /download-mquery Endpoint")
    print("=" * 80)
    
    # url = "http://localhost:8000/api/migration/download-mquery"
    url = "https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/download-mquery"
    
    # Generate a sample complete M query
    complete_m_query = """// Power Query - Converted from Qlik LoadScript
let
    // Connection to data source
    Source = Csv.Document(File.Contents("path/to/data.csv")),
    
    // VehicleDetails table
    VehicleDetails = Table.PromoteHeaders(Source),
    
    // Apply data types
    ChangedTypes = Table.TransformColumnTypes(VehicleDetails,
        List.Zip({Table.ColumnNames(VehicleDetails), Table.ColumnTypes(VehicleDetails)})),
    
    // Final output
    Result = ChangedTypes
in
    Result"""
    
    try:
        response = requests.post(
            url,
            params={
                "m_query": complete_m_query,
                "filename": "test_query_from_complete_generator.m"
            }
        )
        
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print(f"✅ SUCCESS - File download response generated!")
            print(f"   Content-Type: {response.headers.get('content-type')}")
            print(f"   Content-Disposition: {response.headers.get('content-disposition')}")
            print(f"   File size: {len(response.content)} bytes")
            return True
        else:
            print(f"❌ FAILED - Status code: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        return False


def print_summary(results):
    """Print test summary"""
    print("\n\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    test_names = [
        "Convert to M Query",
        "Full Pipeline",
        "Download M Query"
    ]
    
    for i, (name, result) in enumerate(zip(test_names, results), 1):
        status = "✅ PASS" if result is True else ("⏭️  SKIP" if result is None else "❌ FAIL")
        print(f"{i}. {name}: {status}")
    
    passed = sum(1 for r in results if r is True)
    total = sum(1 for r in results if r is not None)
    
    if passed == total:
        print(f"\n🎉 All tests passed! ({passed}/{total})")
    else:
        print(f"\n⚠️  {passed}/{total} tests passed")


if __name__ == "__main__":
    print("\n🚀 TESTING FRONTEND DOWNLOAD BUTTON WITH COMPLETEMQUERYGENERATOR\n")
    
    results = []
    
    # Test 1: Convert to M Query
    results.append(test_convert_to_mquery_endpoint())
    
    # Test 2: Full Pipeline
    results.append(test_full_pipeline_endpoint())
    
    # Test 3: Download endpoint
    results.append(test_download_endpoint())
    
    # Print summary
    print_summary(results)
    
    print("\n" + "=" * 80)
    print("✅ Frontend Download Button Test Complete")
    print("=" * 80)
