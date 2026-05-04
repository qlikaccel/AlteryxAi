#!/usr/bin/env python3
"""
Test: TypedTable Column Extraction from M Expressions
═════════════════════════════════════════════════════════════════

Tests that _extract_typedarticle_columns() properly extracts column names
from TypedTable steps that were injected by mquery_converter.
"""

import logging
import re
from typing import List

logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
logger = logging.getLogger(__name__)


def _extract_typedarticle_columns(expr: str) -> List[str]:
    """Extract column names from TypedTable step in M expression."""
    if not expr:
        return []
    
    columns = []
    seen = set()
    
    pattern = r'TypedTable\s*=\s*Table\.TransformColumnTypes\s*\([^,]+,\s*\{([\s\S]*?)\}\s*\)'
    match = re.search(pattern, expr)
    
    if match:
        block = match.group(1)
        for col_match in re.finditer(r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)', block):
            col_name = col_match.group(1).strip()
            if col_name and col_name not in seen and col_name != "*":
                seen.add(col_name)
                columns.append(col_name)
    
    return columns


def test_extract_typedarticle():
    """Test extraction from various M expressions"""
    
    logger.info("=" * 70)
    logger.info("TEST 1: Simple CSV with TypedTable")
    logger.info("=" * 70)
    
    m_expr1 = '''let
    FilePath = "C:/data/employees.csv",
    Source = Csv.Document(
        File.Contents(FilePath),
        [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
    TypedTable = Table.TransformColumnTypes(
        Headers,
        {
        {"employee_id", type text}, {"name", type text}, {"department_id", type text}
        }
    )
in
    TypedTable'''
    
    cols1 = _extract_typedarticle_columns(m_expr1)
    if cols1 == ["employee_id", "name", "department_id"]:
        logger.info(f"✅ PASS: Extracted {len(cols1)} columns: {cols1}")
    else:
        logger.error(f"❌ FAIL: Expected ['employee_id', 'name', 'department_id'], got {cols1}")
        return False
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: GROUP BY with TypedTable")
    logger.info("=" * 70)
    
    m_expr2 = '''let
    FilePath = "C:/data/employees.csv",
    Source = Csv.Document(...),
    Headers = Table.PromoteHeaders(Source, [...]),
    TypedTable = Table.TransformColumnTypes(
        Headers,
        {
        {"department_id", type text}
        }
    ),
    #"Grouped Rows" = Table.Group(
        TypedTable,
        {"department_id"},
        {{"total_salary", each List.Sum([salary]), type number}}
    )
in
    #"Grouped Rows"'''
    
    cols2 = _extract_typedarticle_columns(m_expr2)
    if cols2 == ["department_id"]:
        logger.info(f"✅ PASS: Extracted {len(cols2)} columns from GROUP BY: {cols2}")
    else:
        logger.error(f"❌ FAIL: Expected ['department_id'], got {cols2}")
        return False
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: RESIDENT with TypedTable")
    logger.info("=" * 70)
    
    m_expr3 = '''let
    Departments = Departments,
    TypedTable = Table.TransformColumnTypes(
        Departments,
        {
        {"location_id", type text}, {"location_name", type text}, {"country", type text}
        }
    )
in
    TypedTable'''
    
    cols3 = _extract_typedarticle_columns(m_expr3)
    if cols3 == ["location_id", "location_name", "country"]:
        logger.info(f"✅ PASS: Extracted {len(cols3)} columns from RESIDENT: {cols3}")
    else:
        logger.error(f"❌ FAIL: Expected ['location_id', 'location_name', 'country'], got {cols3}")
        return False
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST 4: No TypedTable (should return empty list)")
    logger.info("=" * 70)
    
    m_expr4 = '''let
    Source = Csv.Document(...),
    Headers = Table.PromoteHeaders(Source, [...])
in
    Headers'''
    
    cols4 = _extract_typedarticle_columns(m_expr4)
    if cols4 == []:
        logger.info(f"✅ PASS: Correctly returned empty list when no TypedTable found")
    else:
        logger.error(f"❌ FAIL: Expected [], got {cols4}")
        return False
    
    logger.info("\n" + "=" * 70)
    logger.info("ALL TESTS PASSED ✅")
    logger.info("=" * 70)
    return True


if __name__ == "__main__":
    import sys
    try:
        success = test_extract_typedarticle()
        sys.exit(0 if success else 1)
    except Exception as e:
        logger.exception("TEST FAILED WITH EXCEPTION")
        sys.exit(1)
