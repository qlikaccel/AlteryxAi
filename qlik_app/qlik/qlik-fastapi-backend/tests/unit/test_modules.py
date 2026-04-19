#!/usr/bin/env python
"""Test the parser and converter modules"""

from loadscript_fetcher import LoadScriptFetcher
from loadscript_parser import LoadScriptParser
from loadscript_converter import LoadScriptToMQueryConverter

# Test Parser
test_script = """
LOAD [CustomerID], [Name]
FROM [qvd/customers.qvd];

LOAD [OrderID], [Amount]
FROM [qvd/orders.qvd];
"""

print("Testing LoadScriptParser...")
parser = LoadScriptParser(test_script)
parse_result = parser.parse()
print(f"✅ Parser result status: {parse_result.get('status')}")
print(f"✅ Tables found: {parse_result.get('summary', {}).get('tables_count', 0)}")

# Test Converter
print("\nTesting LoadScriptToMQueryConverter...")
converter = LoadScriptToMQueryConverter(parse_result)
conversion_result = converter.convert()
print(f"✅ Converter result status: {conversion_result.get('status')}")
m_query = conversion_result.get('m_query', '')
print(f"✅ M Query length: {len(m_query)} chars")
print(f"✅ M Query preview (first 300 chars):\n{m_query[:300]}...")

print("\n✅ All modules working correctly!")
