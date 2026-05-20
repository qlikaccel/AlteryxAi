#!/usr/bin/env python3
"""
Test to verify that schema injection is working correctly for all table types.
This will show us the actual M query output with TypedTable steps.
"""

from mquery_converter import MQueryConverter

# Sample tables to test
test_tables = {
    "Departments": {
        "name": "Departments",
        "source_type": "csv",
        "source_path": "departments.csv",
        "base_path": "C:/data",
        "fields": [{"name": "*", "type": "text"}],
        "options": {}
    },
    "Locations": {
        "name": "Locations",
        "source_type": "csv",
        "source_path": "locations.csv",
        "base_path": "C:/data",
        "fields": [{"name": "*", "type": "text"}],
        "options": {}
    },
    "Projects": {
        "name": "Projects",
        "source_type": "csv",
        "source_path": "projects.csv",
        "base_path": "C:/data",
        "fields": [{"name": "*", "type": "text"}],
        "options": {}
    },
}

# Initialize converter
converter = MQueryConverter(all_tables_list=test_tables)

# Test each table
for table_name, table_def in test_tables.items():
    print(f"\n{'='*80}")
    print(f"Testing: {table_name}")
    print('='*80)
    
    # Generate M query
    try:
        m_query, description = converter.to_m_expression(table_def, "C:/data", converter)
        
        # Check if TypedTable is present
        if "TypedTable" in m_query:
            print(f"✅ TypedTable found in M query")
        else:
            print(f"❌ TypedTable NOT found in M query - ISSUE!")
        
        # Extract columns from TypedTable
        import re
        pattern = r'TypedTable\s*=\s*Table\.TransformColumnTypes\s*\([^,]+,\s*\{([\s\S]*?)\}\s*\)'
        match = re.search(pattern, m_query)
        
        if match:
            block = match.group(1)
            columns = []
            for col_match in re.finditer(r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)', block):
                col_name = col_match.group(1).strip()
                if col_name and col_name != "*":
                    columns.append(col_name)
            print(f"Extracted columns: {columns}")
        else:
            print(f"⚠️  Could not parse TypedTable from M query")
        
        # Show first 500 chars of M query
        print(f"\nM Query (first 800 chars):")
        print(m_query[:800])
        if len(m_query) > 800:
            print(f"... [truncated, total length: {len(m_query)}]")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*80}")
print("Test Complete")
print('='*80)
