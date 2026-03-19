"""
pbit_generator.py
Generates a Power BI Template (.pbit) file from:
  - A combined M Query string (multiple tables separated by comment headers), OR
  - A list of per-table dicts
  
A .pbit is a ZIP file containing DataModelSchema (JSON). When opened in
Power BI Desktop it loads all tables/queries and prompts for the
DataSourcePath parameter value.
"""
import io
import json
import re
import zipfile
from typing import Any, Dict, List, Optional

PBI_TYPE_MAP = {
    "string": "string",    "text": "string",
    "number": "double",    "double": "double",
    "decimal": "decimal",  "integer": "int64",
    "int": "int64",        "int64": "int64",
    "boolean": "boolean",  "bool": "boolean",
    "date": "dateTime",    "datetime": "dateTime",
    "timestamp": "dateTime",
}

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="json" ContentType="application/json"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    "</Types>"
)


def parse_combined_mquery(combined_m: str) -> List[Dict[str, str]]:
    """
    Split a combined M Query string (with // Table: Name [type] headers)
    into per-table dicts: {name, source_type, m_expression}
    """
    tables: List[Dict[str, str]] = []
    header_re = re.compile(r"// Table:\s+(.+?)\s+\[(\w+)\]", re.IGNORECASE)
    headers = list(header_re.finditer(combined_m))

    for i, match in enumerate(headers):
        name = match.group(1).strip()
        source_type = match.group(2).strip()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(combined_m)
        chunk = combined_m[match.start():end]
        # Extract the let...in block from the chunk.
        # Matches any valid final step: TypedTable, PromotedHeaders, Source, etc.
        let_match = re.search(r"(let\b.*\bin\s+\w+\s*$)", chunk, re.DOTALL | re.IGNORECASE | re.MULTILINE)
        if let_match:
            m_expr = let_match.group(1).strip()
        else:
            lines = [l for l in chunk.split("\n") if not l.strip().startswith("//")]
            m_expr = "\n".join(lines).strip()
        if m_expr:
            tables.append({"name": name, "source_type": source_type, "m_expression": m_expr})
    return tables


def build_pbit(
    tables_m: List[Dict[str, Any]],
    dataset_name: str,
    relationships: Optional[List[Dict[str, str]]] = None,
    data_source_path_default: str = "",
) -> bytes:
    """
    Build and return a .pbit file as bytes.

    tables_m items:
        name         : str  — table name
        m_expression : str  — full M let...in block
        columns      : list — optional [{name, dataType}] for column schema
        source_type  : str  — informational only
    """
    model_tables = []
    for t in tables_m:
        cols_raw = t.get("columns") or []
        columns = []
        for col in cols_raw:
            cname = col.get("name", "")
            ctype = PBI_TYPE_MAP.get(
                str(col.get("dataType", "string")).lower().replace(" ", ""), "string"
            )
            if cname:
                columns.append({
                    "name": cname,
                    "dataType": ctype,
                    "sourceColumn": cname,
                    "summarizeBy": "none" if ctype in ("string", "dateTime") else "sum",
                })

        tdef: Dict[str, Any] = {
            "name": t["name"],
            "partitions": [{
                "name": f"{t['name']}-Partition",
                "mode": "import",
                "source": {"type": "m", "expression": t["m_expression"]},
            }],
        }
        if columns:
            tdef["columns"] = columns
        model_tables.append(tdef)

    model_rels = []
    for rel in (relationships or []):
        model_rels.append({
            "name": f"{rel.get('from_table')}_to_{rel.get('to_table')}",
            "fromTable": rel.get("from_table", ""),
            "fromColumn": rel.get("from_column", ""),
            "toTable": rel.get("to_table", ""),
            "toColumn": rel.get("to_column", ""),
            "crossFilteringBehavior": "oneDirection",
        })

    schema = {
        "name": dataset_name,
        "compatibilityLevel": 1550,
        "model": {
            "culture": "en-US",
            "dataAccessOptions": {
                "legacyRedirects": True,
                "returnErrorValuesAsNull": True,
            },
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "tables": model_tables,
            "relationships": model_rels,
            # DataSourcePath is exposed as a proper Power BI Query Parameter.
            # The IsParameterQuery annotation is REQUIRED — without it, Fabric/
            # Power BI Service treats [DataSourcePath] references in M as a
            # dynamic/unknown source and blocks dataset refresh.
            "expressions": [{
                "name": "DataSourcePath",
                "description": (
                    "Base folder path for CSV/QVD file sources. "
                    "Set this to your data folder (e.g. C:/Data or /mnt/data)."
                ),
                "kind": "m",
                "expression": json.dumps(data_source_path_default),
                "annotations": [
                    {"name": "IsParameterQuery",         "value": "True"},
                    {"name": "IsParameterQueryRequired", "value": "False"},
                    {"name": "PBI_QueryOrder",           "value": "0"},
                ],
            }],
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("Version", "2.0")
        zf.writestr("DataModelSchema", json.dumps(schema, ensure_ascii=False, indent=2))
        zf.writestr("DiagramLayout", json.dumps({"version": 0, "diagrams": []}))
        zf.writestr("SecurityBindings", "")
        zf.writestr("Settings", json.dumps({}))
    return buf.getvalue()
