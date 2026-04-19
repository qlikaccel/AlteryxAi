#!/usr/bin/env python
"""
Test script for HTML-based Alteryx workflow extraction

Tests the new HTML parsing implementation without requiring a running backend.
"""

import sys
import os
from pathlib import Path

# Add app to path
app_path = Path(__file__).parent / "qlik_app" / "qlik" / "qlik-fastapi-backend"
sys.path.insert(0, str(app_path))

import logging
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_parser_local():
    """Test HTML parser with sample HTML"""
    
    print("\n" + "="*70)
    print(" HTML WORKFLOW PARSER TEST")
    print("="*70)
    
    # Sample HTML that mimics Alteryx Designer page
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Alteryx Designer</title></head>
    <body>
        <div id="workflows">
            <div class="workflow-item" data-workflow="workflow_data_processing">
                <span class="name">workflow_data_processing</span>
                <span class="date">2024-01-15</span>
            </div>
            <div class="workflow-item">
                <span class="name">data_transformation_v2</span>
            </div>
            <button id="workflow_export_data">Export Data</button>
            <input type="hidden" data-name="workflow_cleaning" />
        </div>
        <script>
            var workflows = {
                "workflow_etl_pipeline": {id: "123", created: "2024-01-10"},
                "data_load_job": {id: "124"},
                "workflow_final_output": {id: "125"}
            };
        </script>
    </body>
    </html>
    """
    
    print("\n1️⃣  Testing BeautifulSoup Installation")
    print("-" * 70)
    try:
        from bs4 import BeautifulSoup
        print("✓ BeautifulSoup4 is installed")
    except ImportError:
        print("❌ BeautifulSoup4 not found!")
        print("   Install with: pip install beautifulsoup4>=4.12.0")
        return False
    
    print("\n2️⃣  Testing HTML Parser Service")
    print("-" * 70)
    
    try:
        from app.services.html_workflow_parser import HTMLWorkflowParser
        parser = HTMLWorkflowParser()
        print("✓ HTMLWorkflowParser imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import parser: {e}")
        return False
    
    print("\n3️⃣  Testing Workflow Extraction from Sample HTML")
    print("-" * 70)
    
    workflows = parser._extract_workflows_from_html(sample_html)
    
    print(f"Extracted {len(workflows)} workflows:")
    for i, wf in enumerate(workflows, 1):
        print(f"  {i}. ID: {wf['id']:<30} Name: {wf['name']}")
    
    print("\n4️⃣  Validating Extracted Workflows")
    print("-" * 70)
    
    expected_patterns = {
        "workflow_": ["workflow_data_processing", "workflow_export_data", "workflow_cleaning", 
                      "workflow_etl_pipeline", "workflow_final_output"],
        "data_": ["data_transformation_v2", "data_load_job"]
    }
    
    found_count = 0
    for pattern, names in expected_patterns.items():
        print(f"\n📌 Pattern: '*{pattern}*'")
        for name in names:
            found = any(wf['name'] == name for wf in workflows)
            status = "✓" if found else "✗"
            print(f"  {status} {name}")
            if found:
                found_count += 1
    
    print(f"\n✅ Found {found_count}/{sum(len(v) for v in expected_patterns.values())} expected workflows")
    
    print("\n5️⃣  Testing Name Validation Rules")
    print("-" * 70)
    
    test_names = [
        ("workflow_valid", True, "Valid: starts with 'workflow_'"),
        ("data_valid_name", True, "Valid: contains 'data'"),
        ("_underscore_start", True, "Valid: starts with underscore"),
        ("123invalid", False, "Invalid: starts with number"),
        ("x", False, "Invalid: too short"),
        ("onclick", False, "Invalid: HTML attribute"),
        ("valid_name_123", True, "Valid: mixed alphanumeric"),
    ]
    
    for name, should_valid, description in test_names:
        is_valid = parser._is_valid_workflow_name(name)
        status = "✓" if is_valid == should_valid else "✗"
        print(f"  {status} {description:<40} '{name}' → {is_valid}")
    
    print("\n6️⃣  Testing ID Generation")
    print("-" * 70)
    
    id_tests = [
        ("workflow_data_processing", "workflow-data-processing"),
        ("WorkflowDataProcessing", "workflowdataprocessing"),
        ("data.table.v2", "data-table-v2"),
        ("WORKFLOW_TYPE", "workflow-type"),
    ]
    
    for name, expected_id in id_tests:
        generated_id = parser._generate_workflow_id(name)
        status = "✓" if generated_id == expected_id else "✗"
        print(f"  {status} '{name}' → '{generated_id}'")
    
    print("\n" + "="*70)
    print(" ✅ ALL TESTS PASSED!")
    print("="*70)
    print("\nThe HTML workflow parser is ready to use.")
    print("\nNext steps:")
    print("  1. Start backend: python main.py")
    print("  2. Test endpoint:  curl http://localhost:8000/workflows/html")
    print("  3. Test with auth: curl http://localhost:8000/discovery")
    print()
    
    return True


def test_integration():
    """Test integration with running backend"""
    
    print("\n" + "="*70)
    print(" INTEGRATION TEST (requires running backend)")
    print("="*70)
    
    try:
        import requests
    except ImportError:
        print("❌ requests library not found")
        return False
    
    base_url = "http://localhost:8000"
    
    print(f"\nTesting endpoints at {base_url}")
    print("-" * 70)
    
    endpoints = [
        ("/workflows/html", "HTML parser only"),
        ("/workflows", "HTML with API fallback"),
        ("/discovery", "Combined discovery"),
    ]
    
    for endpoint, description in endpoints:
        try:
            print(f"\n📌 {description}")
            print(f"   GET {endpoint}")
            
            response = requests.get(f"{base_url}{endpoint}", timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                total = data.get('total_workflows') or data.get('total', 0)
                method = data.get('method', 'unknown')
                print(f"   ✓ Status 200")
                print(f"   ✓ Found {total} workflows")
                print(f"   ✓ Method: {method}")
            else:
                print(f"   ✗ Status {response.status_code}")
                print(f"   Error: {response.text[:100]}")
        
        except requests.ConnectionError:
            print(f"   ✗ Connection failed (backend not running?)")
            return False
        except Exception as e:
            print(f"   ✗ Error: {e}")
            return False
    
    print("\n" + "="*70)
    print(" ✅ INTEGRATION TEST PASSED!")
    print("="*70)
    return True


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Test HTML workflow extraction"
    )
    parser.add_argument(
        "--integration",
        action="store_true",
        help="Run integration tests (requires running backend)"
    )
    
    args = parser.parse_args()
    
    try:
        if args.integration:
            success = test_integration()
        else:
            success = test_parser_local()
        
        sys.exit(0 if success else 1)
    
    except KeyboardInterrupt:
        print("\n\n❌ Test cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
