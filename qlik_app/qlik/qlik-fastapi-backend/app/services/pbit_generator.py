"""
pbit_generator.py
Generates Power BI Template / publish-friendly table definitions from combined M Query.
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

MODEL_SCHEMA_TEMPLATE = {
    "name": "{dataset_name}",
    "compatibilityLevel": 1550,
    "model": {
        "tables": [],
        "relationships": [],
    },
}

PACKAGE_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="metadata"/>'
    '</Relationships>'
)

METADATA_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<metadata xmlns="http://schemas.openxmlformats.org/package/2006/metadata"/>'
)

PACKAGE_CONTENTS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="json" ContentType="application/json"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '</Types>'
)


def parse_combined_mquery(combined_m: str) -> List[Dict[str, Any]]:
    """Parse a combined M Query string into one or more table definitions."""
    if not combined_m or not combined_m.strip():
        return []

    normalized = combined_m.strip()
    # If the script contains Table headers, parse them.
    header_re = re.compile(r"//\s*Table:\s*(.+?)\s*\[(\w+)\]", re.IGNORECASE)
    headers = list(header_re.finditer(normalized))

    if headers:
        tables: List[Dict[str, Any]] = []
        for i, match in enumerate(headers):
            name = match.group(1).strip() or f"Table{i+1}"
            source_type = match.group(2).strip().lower() or "powerquery"
            end = headers[i + 1].start() if i + 1 < len(headers) else len(normalized)
            chunk = normalized[match.start():end].strip()
            let_match = re.search(r"(let\b.*?\bin\s+\S+)", chunk, re.DOTALL | re.IGNORECASE)
            m_expr = let_match.group(1).strip() if let_match else chunk
            tables.append({
                "name": name,
                "source_type": source_type,
                "m_expression": m_expr,
                "fields": [],
                "options": {},
                "source_path": "",
            })
        return tables

    # Fallback: treat the whole expression as a single table.
    final_name = "FinalTable"
    in_match = re.search(r"\bin\s+([A-Za-z0-9_]+)\b", normalized, re.IGNORECASE)
    if in_match:
        final_name = in_match.group(1).strip()

    return [
        {
            "name": final_name,
            "source_type": "powerquery",
            "m_expression": normalized,
            "fields": [],
            "options": {},
            "source_path": "",
        }
    ]


def build_pbit(
    tables_m: List[Dict[str, Any]],
    dataset_name: str,
    relationships: Optional[List[Dict[str, Any]]] = None,
    data_source_path_default: str = "",
) -> bytes:
    """Create a minimal .pbit package from table definitions."""
    model_tables = []
    for t in tables_m:
        table_name = t.get("name", "Table")
        m_expression = t.get("m_expression", "")
        table_def: Dict[str, Any] = {
            "name": table_name,
            "partitions": [
                {
                    "name": f"{table_name}-Partition",
                    "mode": "import",
                    "source": {"type": "m", "expression": m_expression},
                }
            ],
        }
        cols = []
        for field in t.get("fields", []):
            name = field.get("name") or field.get("alias") or ""
            if not name:
                continue
            data_type = str(field.get("type", "string")).lower()
            data_type = PBI_TYPE_MAP.get(data_type, "string")
            cols.append({
                "name": name,
                "dataType": data_type,
                "sourceColumn": name,
                "summarizeBy": "none",
            })
        if cols:
            table_def["columns"] = cols
        model_tables.append(table_def)

    model_relationships = []
    for rel in (relationships or []):
        model_relationships.append({
            "name": rel.get("name") or f"{rel.get('from_table', '')}_to_{rel.get('to_table', '')}",
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
            "tables": model_tables,
            "relationships": model_relationships,
        },
    }

    model_json = json.dumps(schema, indent=2)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("DataModelSchema", model_json)
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", PACKAGE_RELS)
        zf.writestr("metadata.xml", METADATA_XML)

    return buffer.getvalue()
