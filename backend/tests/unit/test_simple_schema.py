#!/usr/bin/env python3
"""
Test schema injection fix for LOAD * tables
"""
import logging
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')

from mquery_converter import MQueryConverter

# Initialize converter
converter = MQueryConverter()

# Sample LOAD * table (no explicit fields, just wildcard)
test_table = {
    "name": "Departments",
    "source_type": "csv",
    "source_path": "departments.csv",
    "fields": [{"name": "*", "type": "text"}],  # LOAD * = just wildcard
    "options": {}
}

print("="*80)
print("Testing LOAD * table schema injection")
print("="*80)
print(f"\nInput table: {test_table['name']}")
print(f"Fields: {test_table['fields']}")

# Generate M query
m_query, desc = converter._m_csv(test_table, "C:/data", converter)

print(f"\nGenerated M query (first 800 chars):")
print(m_query[:800])
print("\n...")

# Check for TypedTable
if "TypedTable" in m_query:
    print("\n✅ SUCCESS: TypedTable found in M query")
    import re
    pattern = r'TypedTable\s*=\s*Table\.TransformColumnTypes\s*\([^,]+,\s*\{([\s\S]*?)\}\s*\)'
    match = re.search(pattern, m_query)
    if match:
        print("✅ TypedTable schema successfully parsed")
    else:
        print("⚠️  TypedTable found but couldn't extract schema")
else:
    print("\n❌ FAILURE: TypedTable NOT found in M query")
    print("This means columns won't be extracted by Power BI BIM builder")

print("\n" + "="*80)
