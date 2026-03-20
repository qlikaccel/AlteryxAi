"""
mquery_converter.py
───────────────────
Converts parsed Qlik table definitions into Power Query M expressions
suitable for embedding in a Power BI BIM partition.
"""

from __future__ import annotations
import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# TYPE MAPPING
# ─────────────────────────────────────────────────────────────────────────────

_QLIK_TO_M_TYPE: Dict[str, str] = {
    "string":    "type text",
    "text":      "type text",
    "number":    "type number",
    "integer":   "Int64.Type",
    "int":       "Int64.Type",
    "double":    "type number",
    "decimal":   "type number",
    "date":      "type date",
    "time":      "type time",
    "datetime":  "type datetime",
    "timestamp": "type datetime",
    "boolean":   "type logical",
    "bool":      "type logical",
    "mixed":     "type any",
    "wildcard":  "type text",
    "unknown":   "type text",
}

# Types for use INSIDE #table() type signature — no "type" keyword prefix
_QLIK_TO_M_TYPE_FOR_TABLE: Dict[str, str] = {
    "string":    "text",
    "text":      "text",
    "number":    "number",
    "integer":   "Int64.Type",
    "int":       "Int64.Type",
    "double":    "number",
    "decimal":   "number",
    "date":      "date",
    "time":      "time",
    "datetime":  "datetime",
    "timestamp": "datetime",
    "boolean":   "logical",
    "bool":      "logical",
    "mixed":     "any",
    "wildcard":  "text",
    "unknown":   "text",
}

_DEFAULT_M_TYPE = "type text"
_DEFAULT_M_TYPE_FOR_TABLE = "text"


def _m_type(qlik_type: str) -> str:
    """M type with 'type' prefix — for TransformColumnTypes."""
    return _QLIK_TO_M_TYPE.get(str(qlik_type).lower().strip(), _DEFAULT_M_TYPE)


def _m_type_for_table(qlik_type: str) -> str:
    """M type WITHOUT 'type' prefix — for #table() column signatures."""
    return _QLIK_TO_M_TYPE_FOR_TABLE.get(str(qlik_type).lower().strip(), _DEFAULT_M_TYPE_FOR_TABLE)


def _normalize_path(path: str) -> str:
    path = re.sub(r"^lib://[^/]*/", "", path)
    path = re.sub(r"^lib://", "", path)
    return path


def _sanitize_col_name(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        return name
    return f'#"{name}"'


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONVERTER
# ─────────────────────────────────────────────────────────────────────────────

class MQueryConverter:

    # ============================================================
    # PUBLIC
    # ============================================================

    def convert_all(
        self,
        tables: List[Dict[str, Any]],
        base_path: str = "[DataSourcePath]",
        connection_string: Optional[str] = None,
    ) -> List[Dict[str, Any]]:

        results = []

        for table in tables:
            m_expr, notes = self._dispatch(table, base_path, connection_string)
            results.append({
                "name":         table["name"],
                "source_type":  table["source_type"],
                "m_expression": m_expr,
                "fields":       table["fields"],
                "notes":        notes,
                "source_path":  table.get("source_path", ""),
                "options":      table.get("options", {}),
            })

        logger.info("[MQueryConverter] Converted %d table(s)", len(results))
        return results

    def convert_one(
        self,
        table: Dict[str, Any],
        base_path: str = "[DataSourcePath]",
        connection_string: Optional[str] = None,
        all_table_names: Optional[set] = None,
    ) -> str:
        m_expr, _ = self._dispatch(table, base_path, connection_string)
        return m_expr

    # ============================================================
    # DISPATCH
    # ============================================================

    def _dispatch(self, table, base_path, connection_string):
        dispatch = {
            "inline":   self._m_inline,
            "csv":      self._m_csv,
            "excel":    self._m_excel,
            "json":     self._m_json,
            "xml":      self._m_xml,
            "parquet":  self._m_parquet,
            "qvd":      self._m_qvd,
            "resident": self._m_resident,
            "sql":      self._m_sql,
        }
        handler = dispatch.get(table.get("source_type"), self._m_placeholder)
        try:
            return handler(table, base_path, connection_string)
        except Exception as exc:
            logger.warning("[MQuery] Error converting '%s': %s", table.get("name"), exc)
            return self._m_placeholder(table, base_path, connection_string), f"Conversion error: {exc}"

    # ============================================================
    # SHARED TYPE TRANSFORM STEP
    # ============================================================

    def _apply_types(self, fields: List[Dict], previous_step: str):
        """
        Returns (transform_step_str, final_step_name).
        Uses 'type' prefix form — correct for TransformColumnTypes.
        Returns ("", previous_step) if no typed fields.
        """
        typed = [f for f in fields if f.get("name") not in ("*", "")]
        if not typed:
            return "", previous_step

        pairs = []
        for f in typed:
            name   = f.get("alias") or f["name"]
            m_type = _m_type(f.get("type", "string"))
            pairs.append(f'{{"{name}", {m_type}}}')

        pairs_str = ",\n        ".join(pairs)
        transform = (
            f",\n"
            f"    TypedTable = Table.TransformColumnTypes(\n"
            f"        {previous_step},\n"
            f"        {{\n        {pairs_str}\n        }}\n"
            f"    )"
        )
        return transform, "TypedTable"

    # ============================================================
    # INLINE
    # ============================================================

    def _m_inline(self, table, base_path, _cs):
        opts   = table.get("options", {})
        fields = table["fields"]

        headers = opts.get(
            "inline_headers",
            [f["name"] for f in fields if f["name"] != "*"]
        )
        rows = opts.get("inline_sample", [])

        # Column type defs — NO "type" prefix inside #table() signature
        type_defs = ", ".join(
            f"{_sanitize_col_name(h)} = "
            f"{_m_type_for_table(next((f['type'] for f in fields if f['name'] == h), 'string'))}"
            for h in headers
        )

        # Build data rows — strip Qlik single-quotes, escape double-quotes
        row_strs = []
        for row in rows:
            vals = []
            for h in headers:
                v = str(row.get(h, ""))
                v = v.strip("'")            # strip Qlik single quotes
                v = v.replace('"', '""')    # escape embedded double quotes
                v = v.replace("\n", " ")    # collapse embedded newlines
                v = v.replace("\r", "")     # remove carriage returns
                v = " ".join(v.split())     # collapse multiple spaces
                vals.append(f'"{v}"')
            # Entire row MUST be on one line
            row_strs.append("{" + ", ".join(vals) + "}")
        rows_m = (
            "{\n        " + ",\n        ".join(row_strs) + "\n    }"
            if row_strs else "{}"
        )
    # Safety: ensure no row spans multiple lines
        import re
        rows_m_safe = re.sub(r',\s*\n\s*"', ', "', rows_m)
        rows_m = rows_m_safe
        m = (
            f"let\n"
            f"    Source = #table(\n"
            f"        type table [{type_defs}],\n"
            f"        {rows_m}\n"
            f"    )\n"
            f"in\n"
            f"    Source"
        )
        return m, f"Inline table with {len(rows)} row(s). Data embedded directly."

    # ============================================================
    # CSV
    # ============================================================

    def _m_csv(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]
        opts   = table.get("options", {})

        delimiter = opts.get("delimiter", ",")
        encoding  = 65001
        enc_str   = opts.get("encoding", "")
        if enc_str:
            enc_map = {"UTF-8": 65001, "UTF8": 65001, "UTF-16": 1200, "UTF16": 1200}
            encoding = enc_map.get(enc_str.upper().replace("-", ""), encoding)

        transform, final = self._apply_types(fields, "PromotedHeaders")

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Csv.Document(\n"
            f"        File.Contents(FilePath),\n"
            f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
            f"    ),\n"
            f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"CSV source: {path}"

    # ============================================================
    # EXCEL
    # ============================================================

    def _m_excel(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        sheet  = table.get("options", {}).get("sheet", "Sheet1")
        fields = table["fields"]

        transform, final = self._apply_types(fields, "PromotedHeaders")

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Excel.Workbook(File.Contents(FilePath), null, true),\n"
            f"    SheetData = Source{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
            f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"Excel source: {path}, sheet: {sheet}"

    # ============================================================
    # JSON
    # ============================================================

    def _m_json(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]

        expand_cols = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
        col_list    = ", ".join(f'"{c}"' for c in expand_cols)

        transform, final = self._apply_types(fields, "Expanded")

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Json.Document(File.Contents(FilePath)),\n"
            f"    ToTable = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n"
            f"    Expanded = Table.ExpandRecordColumn(ToTable, \"Column1\",\n"
            f"        {{{col_list}}},\n"
            f"        {{{col_list}}}\n"
            f"    )"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"JSON source: {path}. Assumes array of records at root level."

    # ============================================================
    # XML
    # ============================================================

    def _m_xml(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]

        transform, final = self._apply_types(fields, "Source")

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Xml.Tables(File.Contents(FilePath))"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"XML source: {path}. Review nested table expansion manually."

    # ============================================================
    # PARQUET
    # ============================================================

    def _m_parquet(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]

        transform, final = self._apply_types(fields, "Source")

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Parquet.Document(File.Contents(FilePath))"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"Parquet source: {path}."

    # ============================================================
    # QVD (CSV fallback)
    # ============================================================

    def _m_qvd(self, table, base_path, _cs):
        path     = _normalize_path(table.get("source_path", ""))
        csv_path = re.sub(r"\.qvd$", ".csv", path, flags=re.IGNORECASE)
        fields   = table["fields"]

        transform, final = self._apply_types(fields, "PromotedHeaders")

        m = (
            f"let\n"
            f"    // QVD not supported natively — expects pre-converted CSV\n"
            f"    FilePath = {base_path} & \"/{csv_path}\",\n"
            f"    Source = Csv.Document(\n"
            f"        File.Contents(FilePath),\n"
            f"        [Delimiter=\",\", Encoding=65001]\n"
            f"    ),\n"
            f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"QVD fallback (pre-convert to CSV): {path}"

    # ============================================================
    # RESIDENT
    # ============================================================

    def _m_resident(self, table, base_path, _cs):
        source_table = table.get("source_path", "UnknownTable")
        fields       = table["fields"]

        selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]

        if selected:
            select_step = (
                f",\n    Selected = Table.SelectColumns({source_table},\n"
                f"        {{{', '.join(chr(34) + c + chr(34) for c in selected)}}}\n"
                f"    )"
            )
            intermediate = "Selected"
        else:
            select_step = ""
            intermediate = source_table

        transform, final = self._apply_types(fields, intermediate)

        m = (
            f"let\n"
            f"    // References another Power BI query: {source_table}\n"
            f"    {source_table} = {source_table}"
            f"{select_step}"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"RESIDENT load from '{source_table}'."

    # ============================================================
    # SQL
    # ============================================================

    def _m_sql(self, table, base_path, connection_string):
        source_table = table.get("source_path", "dbo.UnknownTable")
        fields       = table["fields"]
        conn         = connection_string or "[OdbcConnectionString]"

        selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
        col_list = ", ".join(f"[{c}]" for c in selected) if selected else "*"

        transform, final = self._apply_types(fields, "Source")

        m = (
            f"let\n"
            f"    ConnectionString = {conn},\n"
            f"    Source = Odbc.Query(\n"
            f"        ConnectionString,\n"
            f"        \"SELECT {col_list} FROM {source_table}\"\n"
            f"    )"
            f"{transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"SQL/ODBC source: {source_table}."

    # ============================================================
    # PLACEHOLDER
    # ============================================================

    def _m_placeholder(self, table, base_path, _cs):
        fields = table["fields"]

        type_defs = ", ".join(
            f"{_sanitize_col_name(f.get('alias') or f['name'])} = {_m_type(f.get('type', 'string'))}"
            for f in fields if f["name"] != "*"
        ) or "Column1 = type text"

        m = (
            f"let\n"
            f"    // Placeholder — source type '{table.get('source_type', 'unknown')}' not auto-converted.\n"
            f"    // Original load: {table.get('source_path', 'N/A')}\n"
            f"    Source = #table(\n"
            f"        type table [{type_defs}],\n"
            f"        {{}}\n"
            f"    )\n"
            f"in\n"
            f"    Source"
        )
        return m, f"Source type '{table.get('source_type')}' requires manual configuration."


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE
# ─────────────────────────────────────────────────────────────────────────────

def convert_to_mquery(
    tables: List[Dict[str, Any]],
    base_path: str = "[DataSourcePath]",
    connection_string: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convert parsed Qlik tables to M expressions."""
    return MQueryConverter().convert_all(tables, base_path, connection_string)