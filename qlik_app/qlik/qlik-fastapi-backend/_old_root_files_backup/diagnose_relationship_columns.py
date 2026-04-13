#!/usr/bin/env python3
"""
Diagnostic script to identify missing relationship columns
════════════════════════════════════════════════════════════

Compares the columns exported by each table's M Query against
the columns referenced in relationships. Identifies which 
relationships will fail due to missing columns.
"""

import json
import logging
from typing import Dict, List, Set, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def analyze_bim_relationships(bim_json_str: str) -> Dict[str, Any]:
    """
    Parse a BIM JSON and validate all relationships against table columns.
    
    Returns:
        {
            "valid_relationships": [...],
            "invalid_relationships": [...],
            "missing_columns_by_table": {...},
            "summary": {...}
        }
    """
    try:
        bim = json.loads(bim_json_str)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return {"error": str(e)}
    
    model = bim.get("model", {})
    tables = model.get("tables", [])
    relationships = model.get("relationships", [])
    
    # Build column sets for each table
    table_columns: Dict[str, Set[str]] = {}
    for table in tables:
        name = table.get("name", "")
        cols = {c.get("name", "") for c in table.get("columns", []) if c.get("name")}
        table_columns[name] = cols
        logger.info(f"Table '{name}': {len(cols)} columns")
    
    # Validate relationships
    valid_rels = []
    invalid_rels = []
    missing_cols_by_table: Dict[str, Set[str]] = {}
    
    for rel in relationships:
        ft = rel.get("fromTable", "")
        fc = rel.get("fromColumn", "")
        tt = rel.get("toTable", "")
        tc = rel.get("toColumn", "")
        rel_name = rel.get("name", f"{ft}_{fc}_{tt}_{tc}")
        
        from_cols = table_columns.get(ft, set())
        to_cols = table_columns.get(tt, set())
        
        from_exists = fc in from_cols
        to_exists = tc in to_cols
        
        if from_exists and to_exists:
            logger.info(f"✅ Valid: {rel_name}")
            valid_rels.append(rel)
        else:
            logger.warning(f"❌ Invalid: {rel_name}")
            if not from_exists:
                logger.warning(f"   - Column '{fc}' missing from '{ft}'")
                logger.warning(f"     Available: {sorted(from_cols)}")
                if ft not in missing_cols_by_table:
                    missing_cols_by_table[ft] = set()
                missing_cols_by_table[ft].add(fc)
            
            if not to_exists:
                logger.warning(f"   - Column '{tc}' missing from '{tt}'")
                logger.warning(f"     Available: {sorted(to_cols)}")
                if tt not in missing_cols_by_table:
                    missing_cols_by_table[tt] = set()
                missing_cols_by_table[tt].add(tc)
            
            invalid_rels.append(rel)
    
    return {
        "valid_relationships": valid_rels,
        "invalid_relationships": invalid_rels,
        "missing_columns_by_table": {
            table: sorted(cols) 
            for table, cols in missing_cols_by_table.items()
        },
        "summary": {
            "total_relationships": len(relationships),
            "valid": len(valid_rels),
            "invalid": len(invalid_rels),
            "tables_with_missing_cols": len(missing_cols_by_table),
        }
    }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python diagnose_relationship_columns.py <bim_file.bim>")
        sys.exit(1)
    
    bim_file = sys.argv[1]
    try:
        with open(bim_file, 'r', encoding='utf-8') as f:
            bim_content = f.read()
    except Exception as e:
        logger.error(f"Failed to read file: {e}")
        sys.exit(1)
    
    result = analyze_bim_relationships(bim_content)
    
    if "error" not in result:
        print("\n" + "="*70)
        print("RELATIONSHIP VALIDATION SUMMARY")
        print("="*70)
        summary = result.get("summary", {})
        print(f"Total Relationships: {summary.get('total_relationships', 0)}")
        print(f"Valid: {summary.get('valid', 0)}")
        print(f"Invalid: {summary.get('invalid', 0)}")
        print(f"Tables with Missing Columns: {summary.get('tables_with_missing_cols', 0)}")
        
        if result.get("missing_columns_by_table"):
            print("\n" + "-"*70)
            print("MISSING COLUMNS BY TABLE")
            print("-"*70)
            for table, cols in result.get("missing_columns_by_table", {}).items():
                print(f"\n{table}:")
                for col in cols:
                    print(f"  - {col}")
        
        if result.get("invalid_relationships"):
            print("\n" + "-"*70)
            print("INVALID RELATIONSHIPS")
            print("-"*70)
            for rel in result.get("invalid_relationships", []):
                print(f"  {rel.get('name', 'unknown')}")
        
        print("\n")
    else:
        print(f"Error: {result['error']}")
        sys.exit(1)
