# powerbi_dataset_publisher.py - Cloud-Optimized REST Push + M Queries
#
# STRICT CONVERTER (Future-Proof Architecture FIX)
# ──────────────────────────────────────────────
# ✅ FIX 1: No silent fallback to CSV
#    - Always use MQueryConverter
#    - Raise exception if conversion fails (no data loss)
#    - Caller must handle errors explicitly
#
# ✅ FIX 2: Universal schema detection
#    - Supports LOAD *, RESIDENT, CONCATENATE
#    - Dynamic schema from CSV preview
#    - Inherited schema from parent tables
#    - Union schema for concatenation
#
# ✅ FIX 3: Full transformation support
#    - GROUP BY aggregations
#    - JOIN/KEEP operations
#    - IF conditions
#    - APPLYMAP lookups
#    - All Qlik complex logic preserved
#
import os
import json
from typing import Dict, Any, List
from msal import ConfidentialClientApplication
import requests
from pydantic import BaseModel

class PublisherConfig(BaseModel):
    tenant_id: str
    client_id: str
    client_secret: str
    workspace_id: str
    dataset_name: str

def get_powerbi_token(config: PublisherConfig) -> str:
    app = ConfidentialClientApplication(
        config.client_id, authority=f"https://login.microsoftonline.com/{config.tenant_id}",
        client_credential=config.client_secret
    )
    result = app.acquire_token_for_client(scopes=["https://analysis.windows.net/powerbi/api/.default"])
    if "access_token" not in result: raise ValueError(f"Auth failed: {result.get('error_description')}")
    return result["access_token"]  # [web:6]

def inject_schema_if_missing(m_expr: str, source_type: str = "") -> str:
    """
    🔥 BULLETPROOF Schema Injection (FIX 3)
    
    Handles all variations of "in\n" with different whitespace:
    - in\n    Headers
    - in\n        Headers (tabs/spaces)
    - in\r\n    Headers (Windows newlines)
    
    PROBLEM with old string.replace():
    ❌ m_expr.replace("in\n", ...)
    - Fails on "in  \n  Headers" (extra spaces)
    - Fails on "in\t\n\tHeaders" (tabs)
    
    SOLUTION: Use regex to handle ANY whitespace pattern
    """
    import logging
    import re
    
    logger = logging.getLogger(__name__)
    
    # If already has schema → skip (no double injection)
    if "TransformColumnTypes" in m_expr:
        logger.debug(f"[inject_schema] ℹ️ Schema already present - skipping")
        return m_expr
    
    # Only inject for file-based sources
    file_based_types = ("csv", "qvd", "excel", "json", "xml", "parquet", "file", "unknown", "")
    if source_type and source_type.lower() not in file_based_types:
        logger.debug(f"[inject_schema] ⓘ Source type '{source_type}' - checking for schema...")
    
    logger.info(f"[inject_schema] 🔥 Injecting bulletproof dynamic schema...")
    
    # 🔥 FIX 3: Bulletproof regex that handles ALL whitespace variations
    # Pattern matches: "in" keyword + any whitespace + newline + any whitespace + step name
    # Group 1 captures the final step name before "in"
    pattern = r"in\s*\n\s*([A-Za-z0-9_#\"\']+)"
    
    def replacer(match):
        step = match.group(1).strip().strip('#"\'')  # Extract step name, strip quotes/hashes
        return f""",
    Columns = Table.ColumnNames({step}),
    TypedTable = Table.TransformColumnTypes(
        {step},
        List.Transform(Columns, each {{_, type text}})
    )
in
    TypedTable"""
    
    # Try to apply the replacement (only once — count=1)
    result = re.sub(pattern, replacer, m_expr, count=1, flags=re.MULTILINE | re.IGNORECASE)
    
    if result != m_expr:
        logger.info(f"[inject_schema] ✅ Bulletproof schema injection SUCCESSFUL")
        return result
    else:
        logger.warning(f"[inject_schema] ⚠️  Could not inject - M query format unexpected")
        return m_expr



def validate_m_query_columns(
    table: Dict[str, Any],
    m_expression: str,
    converter: "MQueryConverter",
) -> List[str]:
    """
    RC-6: Pre-publish column validation.

    Checks that every column in the BIM schema (resolve_output_columns) is
    actually produced by the M expression (present in AddColumn / TypedTable
    / TransformColumnTypes steps).

    Returns a list of column names that are MISSING from the M expression.
    An empty list means the M query is consistent with the schema.
    """
    import re
    import logging
    logger = logging.getLogger(__name__)
    
    expected_cols = {
        col["name"]
        for col in converter.resolve_output_columns(table)
        if col.get("name")
    }
    if not expected_cols:
        return []  # Nothing to validate

    # Extract column names mentioned in the M expression steps
    present_cols: set = set()

    # AddColumn steps: Table.AddColumn(prev, "colname", …)
    for m in re.finditer(
        r'Table\.AddColumn\s*\([^,]+,\s*"([^"]+)"', m_expression
    ):
        present_cols.add(m.group(1))

    # TransformColumnTypes / SelectColumns: {"colname", type …}
    for m in re.finditer(r'\{"([^"]+)"(?:,\s*type|\s*})', m_expression):
        present_cols.add(m.group(1))

    # ExpandTableColumn columns list: {"col1", "col2"}
    for m in re.finditer(r'Table\.ExpandTableColumn\s*\([^{]+\{([^}]+)\}', m_expression):
        for col_match in re.finditer(r'"([^"]+)"', m.group(1)):
            present_cols.add(col_match.group(1))

    missing = sorted(expected_cols - present_cols)

    if missing:
        logger.warning(
            "[validate_m_query_columns] Table '%s': %d column(s) expected by BIM "
            "but NOT found in M expression: %s",
            table.get("name", "?"),
            len(missing),
            missing,
        )
    else:
        logger.info(
            "[validate_m_query_columns] Table '%s': all %d expected column(s) present",
            table.get("name", "?"),
            len(expected_cols),
        )

    return missing


def generate_cloud_m(table: Dict[str, Any], qlik_fields_map: Dict[str, Any] = None) -> str:
    """
    ✅ STRICT CONVERTER (Future-Proof FIX)
    
    Use MQueryConverter for ALL transformations.
    NEVER silent fallback to CSV — raises exception if conversion fails.
    This ensures no data loss from transformations (CONCATENATE, RESIDENT, GROUP BY, JOIN).

    qlik_fields_map: full cross-table map {TableName: [col1, col2, ...]} from the
    fetcher/parser (GetTablesAndKeys). When supplied, RESIDENT and CONCATENATE tables
    can resolve columns from their parent/sibling tables — not just their own columns.
    Falls back to building a single-table map from options['qlik_columns'] if not provided.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from mquery_converter import MQueryConverter
        converter = MQueryConverter()
        
        table_name = table.get('name', 'Unknown')

        # 🔥 FIX: Use the full cross-table map when provided by the caller.
        # Previously this built a single-table map from options['qlik_columns'] only,
        # which meant RESIDENT/CONCATENATE tables couldn't resolve columns from other
        # tables they depend on. Now we accept the full map from tables_to_push_schema()
        # which receives it from parse_result (populated by the fetcher via GetTablesAndKeys).
        if not qlik_fields_map:
            # Fallback: build single-table map from this table's own parsed columns.
            # This is used when caller does not have a full map (e.g. direct calls).
            qlik_cols = table.get('options', {}).get('qlik_columns', [])
            qlik_fields_map = {}
            if qlik_cols:
                qlik_fields_map[table_name] = qlik_cols
                logger.info(
                    f"[generate_cloud_m] ⚠️  No cross-table map supplied — "
                    f"falling back to single-table map: {table_name} → {len(qlik_cols)} columns"
                )
        else:
            logger.info(
                f"[generate_cloud_m] 🔥 Using full cross-table qlik_fields_map "
                f"({len(qlik_fields_map)} tables) for '{table_name}'"
            )

        # Build all_table_names from the full map so converter knows every table in scope.
        all_table_names = set(qlik_fields_map.keys()) | {table_name}

        # Convert the table using full converter logic
        # This will handle: RESIDENT, CONCATENATE, GROUP BY, JOIN, IF, etc.
        m_expr = converter.convert_one(
            table,
            base_path="[DataSourcePath]",
            connection_string=None,
            all_table_names=all_table_names,
            qlik_fields_map=qlik_fields_map
        )
        
        if m_expr and m_expr.strip():
            # 🔥 FINAL FIX: Inject schema for ALL tables without TransformColumnTypes
            source_type = table.get('source_type', '').lower()
            m_expr_before = m_expr
            
            # 🔥 ALWAYS inject for file-based sources
            if source_type in ("csv", "qvd", "excel", "json", "xml", "parquet", "file", "unknown", ""):
                m_expr = inject_schema_if_missing(m_expr, source_type)
            
            # Check if schema was injected
            if m_expr != m_expr_before:
                logger.info(f"[generate_cloud_m] ✅ Schema injected for {table_name}")
            else:
                logger.info(f"[generate_cloud_m] ℹ️ No schema injection needed for {table_name}")
            
            # RC-6: Validate derived columns before returning
            missing_cols = validate_m_query_columns(table, m_expr, converter)
            if missing_cols:
                raise RuntimeError(
                    f"[validate] Table '{table_name}' M query is missing "
                    f"{len(missing_cols)} column(s) that the BIM schema expects: "
                    f"{missing_cols}\n"
                    f"Fix: check _auto_detect_transformations() detected these "
                    f"fields and _detect_and_apply_derived_columns() emitted "
                    f"Table.AddColumn steps for them."
                )
            
            logger.info(f"[generate_cloud_m] Successfully converted {table_name}")
            return m_expr
        else:
            # Converter returned empty - this is an error condition
            raise ValueError(f"MQueryConverter returned empty M expression for table {table_name}")
    
    except Exception as e:
        # STRICT FIX: Don't silent fallback - raise exception
        # This forces the caller to handle the error gracefully instead of publishing broken data
        table_name = table.get('name', 'Unknown')
        error_msg = (
            f"[MQuery] CONVERSION FAILED for table '{table_name}': {str(e)}\n"
            f"❌ NO FALLBACK — This table will NOT be published.\n"
            f"📋 Action required:\n"
            f"   1. Check mquery_converter is properly configured\n"
            f"   2. Verify table fields are parsed correctly\n"
            f"   3. Ensure source paths are valid\n"
            f"   4. Check for complex transformations (RESIDENT, CONCATENATE, GROUP BY)"
        )
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e

def tables_to_push_schema(parse_result: Dict[str, Any]) -> Dict[str, Any]:
    # FIX: Handle both parser formats (details.tables and direct tables)
    tables = parse_result.get("details", {}).get("tables", []) or parse_result.get("tables", [])

    # 🔥 FIX: Extract the full cross-table map from parse_result.
    # The fetcher (LoadScriptFetcher.fetch_and_parse) stores it under 'qlik_fields_map'.
    # The parser also returns it in the parse result dict.
    # We prefer the top-level key (from fetcher) then the nested parse result key.
    qlik_fields_map: Dict[str, Any] = (
        parse_result.get("qlik_fields_map")
        or parse_result.get("details", {}).get("qlik_fields_map")
        or {}
    )
    if qlik_fields_map:
        import logging
        logging.getLogger(__name__).info(
            "[tables_to_push_schema] 🔥 Cross-table qlik_fields_map found: %d tables — "
            "will be passed to generate_cloud_m() for RESIDENT/CONCATENATE resolution.",
            len(qlik_fields_map)
        )
    
    dataset = {
        "name": parse_result.get("dataset_name", "QlikConvertedDataset"),
        "defaultMode": "Push",
        "parameters": [{"name": "DataSourcePath", "type": "string", "mode": "Required"}],
        "tables": []
    }
    
    for table in tables:
        # FIX: Handle both "name" and "table_name" keys
        table_name = table.get("name") or table.get("table_name", "UnknownTable")
        cols = [{"name": f["name"], "dataType": "string"} for f in table.get("fields", [])]
        dataset["tables"].append({"name": table_name, "columns": cols})
    
    # 🔥 FIX: Pass the full cross-table map into generate_cloud_m for every table.
    # Previously generate_cloud_m was called with no map so each table could only
    # see its own columns — RESIDENT and CONCATENATE tables resolved incorrectly.
    dataset["m_queries"] = {
        (t.get("name") or t.get("table_name", "UnknownTable")): generate_cloud_m(t, qlik_fields_map)
        for t in tables
    }
    return dataset






def resolve_ambiguous_relationships(relationships: List[Dict]) -> List[Dict]:
    """
    Detects ambiguous paths (two active routes between same table pair)
    and sets isActive: false on the redundant direct relationship.
    
    Rule: If TableA -> TableC exists directly AND via TableA -> TableB -> TableC,
    mark the direct TableA -> TableC as inactive.
    """
    direct_map = {}
    for i, rel in enumerate(relationships):
        key = (rel["fromTable"], rel["toTable"])
        direct_map[key] = i

    to_deactivate = set()

    for (from_table, to_table), rel_idx in direct_map.items():
        intermediaries = [t for (f, t) in direct_map if f == from_table and t != to_table]
        for mid in intermediaries:
            if (mid, to_table) in direct_map:
                to_deactivate.add(rel_idx)
                print(f"[RelationshipFix] Deactivating ambiguous: "
                      f"{from_table}.{relationships[rel_idx]['fromColumn']} -> "
                      f"{to_table}.{relationships[rel_idx]['toColumn']} "
                      f"(indirect path via {mid} exists)")

    result = []
    for i, rel in enumerate(relationships):
        r = dict(rel)
        if i in to_deactivate:
            r["isActive"] = False
        result.append(r)

    return result


def build_bim_relationships(inferred_relationships: List[Dict]) -> List[Dict]:
    """
    Converts inferred relationships into BIM-format and resolves ambiguity.
    Call this before building the final BIM payload.
    """
    bim_rels = []
    for rel in inferred_relationships:
        bim_rels.append({
            "name": f"{rel['fromTable']}_{rel['fromColumn']}_{rel['toTable']}",
            "fromTable": rel["fromTable"],
            "fromColumn": rel["fromColumn"],
            "toTable": rel["toTable"],
            "toColumn": rel["toColumn"],
            "isActive": True
        })

    return resolve_ambiguous_relationships(bim_rels)




# def publish_from_parse_result(parse_result: Dict[str, Any], config: PublisherConfig) -> Dict[str, Any]:
#     token = get_powerbi_token(config)
#     headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
#     schema = tables_to_push_schema(parse_result)


def publish_from_parse_result(parse_result: Dict[str, Any], config: PublisherConfig) -> Dict[str, Any]:
    token = get_powerbi_token(config)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 🔥 FIX: qlik_fields_map is already inside parse_result (set by fetcher/parser).
    # tables_to_push_schema() now reads and threads it through to generate_cloud_m().
    # No separate extraction needed here — just pass parse_result as-is.
    schema = tables_to_push_schema(parse_result)

    # ✅ Build relationships with ambiguity resolved BEFORE BIM payload
    raw_relationships = parse_result.get("relationships", [])
    resolved_relationships = build_bim_relationships(raw_relationships)

    # Inject into BIM payload
    schema["relationships"] = resolved_relationships

    # ... rest of your publish logic