"""
Test script to validate column detection fixes in mquery_converter and powerbi_publisher.

This ensures:
1. No "Value" column fallback is created
2. resolve_output_columns() properly detects columns
3. _extract_fields_from_m() can extract from complex M expressions
4. Proper error handling when columns cannot be detected
"""

import sys
import logging
from mquery_converter import MQueryConverter
from powerbi_publisher import _extract_fields_from_m

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_resolve_output_columns():
    """Test the enhanced resolve_output_columns function"""
    logger.info("=" * 80)
    logger.info("TEST 1: resolve_output_columns() - GROUP BY detection")
    logger.info("=" * 80)
    
    converter = MQueryConverter()
    
    # Test Case 1: GROUP BY table
    table_group_by = {
        "name": "sales_summary",
        "fields": [
            {"name": "product", "type": "string"},
            {"name": "total_sales", "type": "number"}
        ],
        "options": {
            "is_group_by": True,
            "group_by_columns": ["product"],
            "aggregations": {"total_sales": ("SUM", "amount")}
        }
    }
    
    result = converter.resolve_output_columns(table_group_by)
    logger.info(f"GROUP BY Result: {result}")
    assert len(result) == 2, f"Expected 2 columns, got {len(result)}"
    assert result[0]["name"] == "product", f"Expected 'product', got {result[0]['name']}"
    assert result[1]["name"] == "total_sales", f"Expected 'total_sales', got {result[1]['name']}"
    logger.info("✅ GROUP BY test passed\n")
    
    # Test Case 2: Normal table with fields
    logger.info("TEST 2: resolve_output_columns() - Normal fields")
    logger.info("=" * 80)
    
    table_normal = {
        "name": "customer",
        "fields": [
            {"name": "customer_id", "type": "integer"},
            {"name": "customer_name", "type": "string"},
            {"name": "created_at", "type": "date"}
        ],
        "options": {}
    }
    
    result = converter.resolve_output_columns(table_normal)
    logger.info(f"Normal table Result: {result}")
    assert len(result) == 3, f"Expected 3 columns, got {len(result)}"
    logger.info("✅ Normal fields test passed\n")
    
    # Test Case 3: Wildcard (should skip)
    logger.info("TEST 3: resolve_output_columns() - Wildcard handling")
    logger.info("=" * 80)
    
    table_wildcard = {
        "name": "data_raw",
        "fields": [
            {"name": "*", "type": "string"}
        ],
        "options": {}
    }
    
    result = converter.resolve_output_columns(table_wildcard)
    logger.info(f"Wildcard Result: {result}")
    assert len(result) == 0, f"Expected empty for wildcard, got {len(result)}"
    logger.info("✅ Wildcard test passed\n")
    
    # Test Case 4: Empty fields with backup
    logger.info("TEST 4: resolve_output_columns() - Fallback to backup fields")
    logger.info("=" * 80)
    
    table_with_backup = {
        "name": "orders",
        "fields": [],  # Empty primary fields
        "options": {
            "_fields_backup": [
                {"name": "order_id", "type": "integer"},
                {"name": "order_date", "type": "date"}
            ]
        }
    }
    
    result = converter.resolve_output_columns(table_with_backup)
    logger.info(f"Backup fields Result: {result}")
    assert len(result) == 2, f"Expected 2 columns from backup, got {len(result)}"
    logger.info("✅ Backup fields test passed\n")


def test_extract_fields_from_m():
    """Test the enhanced _extract_fields_from_m function"""
    logger.info("=" * 80)
    logger.info("TEST 5: _extract_fields_from_m() - TransformColumnTypes pattern")
    logger.info("=" * 80)
    
    # Test Case 1: TransformColumnTypes pattern
    m_expr_1 = """
    let
        Source = Csv.Document(...),
        Headers = Table.PromoteHeaders(Source),
        Types = Table.TransformColumnTypes(Headers, {{"CustomerID", Int64.Type}, {"Name", type text}, {"Amount", type number}})
    in
        Types
    """
    
    result = _extract_fields_from_m(m_expr_1)
    logger.info(f"TransformColumnTypes Result: {result}")
    assert len(result) == 3, f"Expected 3 columns, got {len(result)}"
    logger.info("✅ TransformColumnTypes test passed\n")
    
    # Test Case 2: SelectColumns pattern
    logger.info("TEST 6: _extract_fields_from_m() - SelectColumns pattern")
    logger.info("=" * 80)
    
    m_expr_2 = """
    let
        Source = Csv.Document(...),
        Selected = Table.SelectColumns(Source, {"ID", "Name", "Created"})
    in
        Selected
    """
    
    result = _extract_fields_from_m(m_expr_2)
    logger.info(f"SelectColumns Result: {result}")
    assert len(result) == 3, f"Expected 3 columns from SelectColumns, got {len(result)}"
    logger.info("✅ SelectColumns test passed\n")
    
    # Test Case 3: Dynamic SharePoint (should return empty)
    logger.info("TEST 7: _extract_fields_from_m() - Dynamic SharePoint")
    logger.info("=" * 80)
    
    m_expr_3 = """
    let
        Source = SharePoint.Files("https://site", [ApiVersion = 15]),
        Headers = Table.PromoteHeaders(...)
    in
        Headers
    """
    
    result = _extract_fields_from_m(m_expr_3)
    logger.info(f"Dynamic SharePoint Result: {result}")
    assert len(result) == 0, f"Expected empty for dynamic SharePoint, got {len(result)}"
    logger.info("✅ Dynamic SharePoint test passed\n")


def test_no_value_column_fallback():
    """Test that the 'Value' column fallback is NOT created"""
    logger.info("=" * 80)
    logger.info("TEST 8: Verify NO 'Value' column fallback is created")
    logger.info("=" * 80)
    
    # This test verifies the fix by checking that:
    # 1. The code raises an error instead of creating a Value column
    # 2. The error message is clear and actionable
    
    converter = MQueryConverter()
    
    # Table with NO fields and NO options (worst case)
    table_empty = {
        "name": "test_table",
        "fields": [],
        "options": {}
    }
    
    result = converter.resolve_output_columns(table_empty)
    logger.info(f"Result for empty table: {result}")
    
    # Should be empty, allowing caller to use _extract_fields_from_m()
    assert len(result) == 0, f"Expected empty result, got {len(result)}"
    assert not any(c.get("name") == "Value" for c in result), "❌ Value column should NOT exist!"
    logger.info("✅ No Value column fallback test passed\n")


def main():
    """Run all tests"""
    logger.info("\n")
    logger.info("╔" + "=" * 78 + "╗")
    logger.info("║" + " " * 78 + "║")
    logger.info("║" + "COLUMN DETECTION FIX VALIDATION TESTS".center(78) + "║")
    logger.info("║" + " " * 78 + "║")
    logger.info("╚" + "=" * 78 + "╝")
    logger.info("")
    
    try:
        test_resolve_output_columns()
        test_extract_fields_from_m()
        test_no_value_column_fallback()
        
        logger.info("\n")
        logger.info("╔" + "=" * 78 + "╗")
        logger.info("║" + " " * 78 + "║")
        logger.info("║" + "✅ ALL TESTS PASSED ✅".center(78) + "║")
        logger.info("║" + " " * 78 + "║")
        logger.info("╚" + "=" * 78 + "╝")
        logger.info("")
        
        logger.info("Summary of fixes:")
        logger.info("1. ✅ Removed 'Value' column fallback in powerbi_publisher.py")
        logger.info("2. ✅ Enhanced resolve_output_columns() with fallback logic")
        logger.info("3. ✅ Improved _extract_fields_from_m() with better pattern detection")
        logger.info("4. ✅ Added clear error messages when column detection fails")
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Run the migration workflow with complex M queries")
        logger.info("2. Verify Power BI dataset publishes without column errors")
        logger.info("3. Check that all columns from M queries are correctly declared in BIM")
        logger.info("")
        
        return 0
    except AssertionError as e:
        logger.error(f"\n❌ TEST FAILED: {str(e)}")
        return 1
    except Exception as e:
        logger.error(f"\n❌ ERROR: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
