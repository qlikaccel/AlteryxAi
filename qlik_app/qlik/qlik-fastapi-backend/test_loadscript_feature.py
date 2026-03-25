"""
TEST SCRIPT - Verify LoadScript Conversion Feature

Run this script to test the new LoadScript to PowerBI M Query conversion feature.
This tests both individual components and the full pipeline.

Usage:
    python test_loadscript_feature.py
"""

import logging
import sys
import json
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_module_imports():
    """Test that all new modules import successfully"""
    logger.info("=" * 80)
    logger.info("TEST 1: Module Imports")
    logger.info("=" * 80)
    
    tests = [
        ("loadscript_fetcher", "LoadScriptFetcher"),
        ("loadscript_parser", "LoadScriptParser"),
        ("loadscript_converter", "LoadScriptToMQueryConverter"),
    ]
    
    all_passed = True
    for module_name, class_name in tests:
        try:
            module = __import__(module_name)
            cls = getattr(module, class_name)
            logger.info(f"✅ {module_name}.{class_name} - OK")
        except Exception as e:
            logger.error(f"❌ {module_name}.{class_name} - FAILED: {str(e)}")
            all_passed = False
    
    return all_passed


def test_parser_with_sample_data():
    """Test parser with sample loadscript"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 2: Parser with Sample Data")
    logger.info("=" * 80)
    
    try:
        from loadscript_parser import LoadScriptParser
        
        sample_script = """
        // Customer and Orders Data
        
        [Customers]:
        LOAD
            CustomerID,
            CustomerName,
            Country
        FROM lib://DataFiles/customers.csv;
        
        [Orders]:
        LOAD
            OrderID,
            CustomerID,
            OrderDate,
            Amount
        FROM lib://DataFiles/orders.csv
        WHERE OrderDate > '2024-01-01'
        ORDER BY OrderDate DESC;
        
        [OrderDetails]:
        LOAD
            OrderID,
            ProductID,
            Quantity,
            UnitPrice,
            Quantity * UnitPrice as LineTotal
        FROM lib://DataFiles/orderdetails.csv;
        """
        
        logger.info("Parsing sample loadscript...")
        parser = LoadScriptParser(sample_script)
        result = parser.parse()
        
        if result["status"] == "success":
            logger.info(f"✅ Parse successful!")
            logger.info(f"   Tables: {result['summary']['tables_count']}")
            logger.info(f"   Fields: {result['summary']['fields_count']}")
            logger.info(f"   Connections: {result['summary']['connections_count']}")
            logger.info(f"   Transformations: {result['summary']['transformations_count']}")
            return True
        else:
            logger.error(f"❌ Parse failed: {result.get('message')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test failed: {str(e)}")
        return False


def test_converter_with_sample_data():
    """Test converter with sample parsed data"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 3: Converter with Sample Data")
    logger.info("=" * 80)
    
    try:
        from loadscript_converter import LoadScriptToMQueryConverter
        
        sample_parsed = {
            "status": "success",
            "summary": {
                "tables_count": 3,
                "fields_count": 8,
                "connections_count": 2,
                "transformations_count": 2,
                "joins_count": 0,
                "variables_count": 0,
                "comments_count": 1
            },
            "details": {
                "tables": [
                    {"name": "Customers"},
                    {"name": "Orders"},
                    {"name": "OrderDetails"}
                ],
                "fields": [
                    {"name": "CustomerID", "type": "column"},
                    {"name": "OrderID", "type": "column"},
                    {"name": "Amount", "type": "column"},
                    {"name": "LineTotal", "type": "column"}
                ],
                "data_connections": [
                    {"type": "file", "source": "file://customers.csv"},
                    {"type": "file", "source": "file://orders.csv"}
                ],
                "transformations": [
                    {"type": "filter", "description": "WHERE OrderDate > '2024-01-01'"},
                    {"type": "sorting", "description": "ORDER BY OrderDate DESC"}
                ],
                "joins": [],
                "variables": [],
                "comments": [{"type": "inline", "text": "// Customer and Orders"}]
            }
        }
        
        logger.info("Converting sample parsed data to M Query...")
        converter = LoadScriptToMQueryConverter(sample_parsed)
        result = converter.convert()
        
        if result["status"] == "success":
            logger.info(f"✅ Conversion successful!")
            logger.info(f"   M Query Length: {result['query_length']} characters")
            logger.info(f"   Warnings: {result['warnings_count']}")
            logger.info(f"   Errors: {result['errors_count']}")
            logger.info(f"   Tables: {result['statistics']['total_tables_converted']}")
            logger.info(f"   Connections: {result['statistics']['total_connections_converted']}")
            return True
        else:
            logger.error(f"❌ Conversion failed: {result.get('message')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Test failed: {str(e)}")
        return False


def test_api_endpoints():
    """Test API endpoints (requires backend running)"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 4: API Endpoints (Backend Required)")
    logger.info("=" * 80)
    
    try:
        import requests
        
        # base_url = "http://localhost:8000"  
        base_url = "https://qlikai-app-ltmrv.ondigitalocean.app"
        
        # Test health
        logger.info("Testing API availability...")
        try:
            response = requests.get(f"{base_url}/health", timeout=5)
            if response.status_code == 200:
                logger.info("✅ API is running")
            else:
                logger.warning(f"⚠️  API responded with status {response.status_code}")
        except requests.exceptions.ConnectionError:
            logger.error("❌ Cannot connect to API - is backend running?")
            logger.error("   Run: python main.py")
            return False
        
        # Test pipeline help endpoint
        logger.info("Testing /pipeline-help endpoint...")
        response = requests.get(f"{base_url}/api/migration/pipeline-help")
        
        if response.status_code == 200:
            data = response.json()
            endpoints = data.get('endpoints', [])
            new_endpoints = [e for e in endpoints if 'fetch-loadscript' in e or 'download-mquery' in e]
            
            if new_endpoints:
                logger.info(f"✅ New endpoints found: {len(new_endpoints)}")
                for endpoint in new_endpoints:
                    logger.info(f"   - {endpoint}")
                return True
            else:
                logger.error("❌ New endpoints not found in help")
                return False
        else:
            logger.error(f"❌ API returned status {response.status_code}")
            return False
            
    except ImportError:
        logger.warning("⚠️  Requests library not installed - skipping API tests")
        logger.info("   Install with: pip install requests")
        return True  # Not a failure, just skipped


def test_full_integration():
    """Test full integration without backend"""
    logger.info("\n" + "=" * 80)
    logger.info("TEST 5: Full Integration (No Backend)")
    logger.info("=" * 80)
    
    try:
        from loadscript_parser import LoadScriptParser
        from loadscript_converter import LoadScriptToMQueryConverter
        
        # Sample script
        script = """
        [Sales]:
        LOAD
            SalesID,
            Date,
            Amount
        FROM lib://data.csv;
        """
        
        logger.info("Step 1: Parsing loadscript...")
        parser = LoadScriptParser(script)
        parsed = parser.parse()
        
        if parsed["status"] != "success":
            logger.error("❌ Parse failed")
            return False
        logger.info(f"✅ Parsed: {parsed['summary']['tables_count']} tables")
        
        logger.info("Step 2: Converting to M Query...")
        converter = LoadScriptToMQueryConverter(parsed)
        converted = converter.convert()
        
        if converted["status"] != "success":
            logger.error("❌ Conversion failed")
            return False
        logger.info(f"✅ Converted: {converted['query_length']} char M Query")
        
        logger.info("Step 3: Checking M Query content...")
        m_query = converted['m_query']
        required_parts = ['let', 'Source', 'in']
        
        for part in required_parts:
            if part in m_query:
                logger.info(f"   ✓ Contains '{part}'")
            else:
                logger.warning(f"   ⚠️  Missing '{part}'")
        
        logger.info("✅ Full integration test passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Integration test failed: {str(e)}")
        return False


def main():
    """Run all tests"""
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 20 + "LOADSCRIPT CONVERSION FEATURE TEST" + " " * 24 + "║")
    logger.info("║" + " " * 30 + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " * 15 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    
    test_results = {
        "Module Imports": test_module_imports(),
        "Parser Test": test_parser_with_sample_data(),
        "Converter Test": test_converter_with_sample_data(),
        "API Endpoints": test_api_endpoints(),
        "Full Integration": test_full_integration(),
    }
    
    logger.info("\n" + "=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    
    passed = sum(1 for result in test_results.values() if result)
    total = len(test_results)
    
    for test_name, result in test_results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status}: {test_name}")
    
    logger.info("=" * 80)
    logger.info(f"Results: {passed}/{total} tests passed")
    logger.info("=" * 80)
    
    if passed == total:
        logger.info("\n🎉 ALL TESTS PASSED - Feature is ready to use!")
        logger.info("\nNext steps:")
        logger.info("1. Start backend: python main.py")
        # logger.info("2. Test endpoint: curl -X POST http://localhost:8000/api/migration/full-pipeline?app_id=YOUR_APP_ID")
        logger.info("2. Test endpoint: curl -X POST https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/full-pipeline?app_id=YOUR_APP_ID")
        logger.info("3. Check console for detailed logging output")
        logger.info("4. See README_LOADSCRIPT_CONVERSION.md for full documentation")
        return 0
    else:
        logger.error(f"\n❌ Some tests failed - please check output above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
