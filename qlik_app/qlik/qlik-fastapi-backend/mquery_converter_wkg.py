"""
mquery_converter.py
───────────────────
Converts parsed Qlik table definitions (from qlik_script_parser.py) into
Power Query M expressions suitable for embedding in a Power BI BIM partition.

Each table definition → an M expression string.

Source type handling:
  inline   → Table.FromRecords / #table with embedded data
  qvd      → placeholder with a note (QVD needs ETL step or gateway)
  csv      → Csv.Document / File.Contents
  excel    → Excel.Workbook / Excel.CurrentWorkbook
  json     → Json.Document / File.Contents
  xml      → Xml.Tables / File.Contents
  parquet  → Parquet.Document (requires ODBC or dataflow)
  resident → reference to an existing M query (Table.Buffer)
  sql      → Odbc.Query / Sql.Database
  unknown  → empty typed table (schema-only placeholder)

All generated M expressions:
  • are compatible with Power BI Import mode
  • include the correct column types via Table.TransformColumnTypes
  • gracefully handle missing/optional options
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Type mapping: Qlik / inferred → M type annotation
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

_DEFAULT_M_TYPE = "type text"


def _normalize_path(path: str) -> str:
    """
    Strip Qlik-specific prefixes (lib://, etc.) from file paths
    so they can be used in Power BI M expressions.
    """
    # Remove lib:// prefix (Qlik library connector syntax)
    path = re.sub(r"^lib://[^/]*/", "", path)  # lib://DataFiles/file.csv → file.csv
    path = re.sub(r"^lib://", "", path)          # lib://file.csv → file.csv
    return path


def _m_type(qlik_type: str) -> str:
    return _QLIK_TO_M_TYPE.get(str(qlik_type).lower().strip(), _DEFAULT_M_TYPE)


def _sanitize_col_name(name: str) -> str:
    """Wrap column names with special characters in #\" … \"."""
    if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        return name
    return f'#"{name}"'


def _m_col_list(fields: List[Dict]) -> str:
    """Build  { {"ColA", type text}, {"ColB", Int64.Type} }  for TransformColumnTypes."""
    parts = []
    for f in fields:
        name     = f.get("alias") or f.get("name", "")
        if not name or name == "*":
            continue
        m_type   = _m_type(f.get("type", "string"))
        parts.append(f'{{"{name}", {m_type}}}')
    return "{\n        " + ",\n        ".join(parts) + "\n    }"


# ─────────────────────────────────────────────────────────────────────────────
# Main converter
# ─────────────────────────────────────────────────────────────────────────────

class MQueryConverter:
    """
    Convert a list of parsed Qlik table dicts into M expressions.
    """

    def convert_all(
        self,
        tables: List[Dict[str, Any]],
        base_path: str = "[DataSourcePath]",
        connection_string: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Convert all tables.

        Args:
            tables:            Output of QlikScriptParser.parse().
            base_path:         Root folder for file sources (parameterisable in M).
            connection_string: ODBC/SQL connection string for SQL sources.

        Returns:
            List of dicts:
              { "name": str, "source_type": str, "m_expression": str,
                "fields": [...], "notes": str }
        """
        results = []
        all_table_names = {t["name"] for t in tables}

        for table in tables:
            m_expr, notes = self._dispatch(table, base_path, connection_string, all_table_names)
            results.append({
                "name":        table["name"],
                "source_type": table["source_type"],
                "m_expression": m_expr,
                "fields":      table["fields"],
                "notes":       notes,
                "source_path": table.get("source_path", ""),
                "options":     table.get("options", {}),
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
        m_expr, _ = self._dispatch(table, base_path, connection_string, all_table_names or set())
        return m_expr

    # ─────────────────────────────────────────────────────────────────────────
    # Dispatcher
    # ─────────────────────────────────────────────────────────────────────────

    def _dispatch(
        self,
        table: Dict[str, Any],
        base_path: str,
        connection_string: Optional[str],
        all_table_names: set,
    ) -> tuple[str, str]:
        src = table.get("source_type", "unknown")
        dispatch = {
            "inline":   self._m_inline,
            "qvd":      self._m_qvd,
            "csv":      self._m_csv,
            "excel":    self._m_excel,
            "json":     self._m_json,
            "xml":      self._m_xml,
            "parquet":  self._m_parquet,
            "resident": self._m_resident,
            "sql":      self._m_sql,
        }
        handler = dispatch.get(src, self._m_placeholder)

        try:
            return handler(table, base_path, connection_string)
        except Exception as exc:
            logger.warning("[MQuery] Error converting '%s': %s", table.get("name"), exc)
            return self._m_placeholder(table, base_path, connection_string), f"Conversion error: {exc}"

    # ─────────────────────────────────────────────────────────────────────────
    # INLINE
    # ─────────────────────────────────────────────────────────────────────────

    def _m_inline(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        opts    = table.get("options", {})
        headers = opts.get("inline_headers", [f["name"] for f in table["fields"]])
        rows    = opts.get("inline_sample", [])
        fields  = table["fields"]

        # Build column type definition for #table
        type_defs = ", ".join(
            f"{_sanitize_col_name(h)} = {_m_type(next((f['type'] for f in fields if f['name'] == h), 'string'))}"
            for h in headers
        )

        # Build data rows
        row_strs = []
        for row in rows:
            vals = []
            # for h in headers:
            #     v = row.get(h, "")
            #     vals.append(f'"{v}"')
            # row_strs.append("{" + ", ".join(vals) + "}")
            # for h in headers:
            #     v = str(row.get(h, ""))
            #     v = v.strip("'")          # strip Qlik single quotes
            #     v = v.replace('"', '""')  # escape any embedded double quotes
            #     vals.append(f'"{v}"')
            for h in headers:
                v = str(row.get(h, ""))
                v = v.strip("'")          # strip Qlik single quotes
                v = v.replace('"', '""')  # escape any embedded double quotes
                vals.append(f'"{v}"')
        rows_m = "{\n        " + ",\n        ".join(row_strs) + "\n    }" if row_strs else "{}"

        m = (
            f"let\n"
            f"    Source = #table(\n"
            f"        type table [{type_defs}],\n"
            f"        {rows_m}\n"
            f"    )\n"
            f"in\n"
            f"    Source"
        )
        notes = f"Inline table with {opts.get('inline_row_count', 0)} row(s). Data embedded directly."
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # QVD
    # ─────────────────────────────────────────────────────────────────────────

    def _m_qvd(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        fields    = table["fields"]
        path      = _normalize_path(table.get("source_path", ""))
        col_types = _m_col_list(fields)

        # QVD files cannot be read natively in Power BI.
        # Options: 1) Pre-export to CSV/Parquet in ETL, 2) Qlik Sense gateway, 3) ODBC.
        # We generate a CSV fallback pattern with a parameter pointing to a converted file.

        # Derive CSV sibling path (replace .qvd → .csv)
        csv_path  = re.sub(r"\.qvd$", ".csv", path, flags=re.IGNORECASE)
        file_name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]

        col_transforms = self._col_transform_steps(fields)

        m = (
            f"let\n"
            f"    // QVD source: {path}\n"
            f"    // QVD files are not natively supported in Power BI.\n"
            f"    // This query reads a pre-exported CSV equivalent.\n"
            f"    // Set the 'DataSourcePath' parameter to your export folder.\n"
            # f"    FilePath = {base_path} & \"/{csv_path}\",\n"
            f"    FilePath = {base_path} & \"\\\\{path}\",\n"
            f"    Source = Csv.Document(\n"
            f"        File.Contents(FilePath),\n"
            f"        [Delimiter=\",\", Columns={len([f for f in fields if f['name'] != '*'])},\n"
            f"         Encoding=65001, QuoteStyle=QuoteStyle.None]\n"
            f"    ),\n"
            f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]){col_transforms}\n"
            f"in\n"
            f"    PromotedHeaders"
        )
        notes = (
            f"QVD source ({file_name}). "
            "QVD files must be pre-converted to CSV/Parquet before Power BI can read them. "
            "Set the DataSourcePath parameter or update FilePath to point to the converted file."
        )
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # CSV / flat file
    # ─────────────────────────────────────────────────────────────────────────

    def _m_csv(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        fields    = table["fields"]
        path      = _normalize_path(table.get("source_path", ""))
        opts      = table.get("options", {})
        delimiter = opts.get("delimiter", ",")
        encoding  = 65001  # UTF-8 default

        # Map encoding string to code page
        enc_str   = opts.get("encoding", "")
        if enc_str:
            enc_map = {"UTF-8": 65001, "UTF8": 65001, "UTF-16": 1200, "UTF16": 1200}
            encoding = enc_map.get(enc_str.upper().replace("-", ""), encoding)

        header_mode = opts.get("header", "")
        promote_headers = "true" if "NO LABELS" not in header_mode.upper() else "false"

        col_transforms = self._col_transform_steps(fields)

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Csv.Document(\n"
            f"        File.Contents(FilePath),\n"
            f"        [Delimiter=\"{delimiter}\", Encoding={encoding},\n"
            f"         QuoteStyle=QuoteStyle.Csv]\n"
            f"    ),\n"
            f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars={promote_headers}]){col_transforms}\n"
            f"in\n"
            f"    PromotedHeaders"
        )
        notes = f"CSV source: {path}"
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # Excel
    # ─────────────────────────────────────────────────────────────────────────

    def _m_excel(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        fields  = table["fields"]
        path    = _normalize_path(table.get("source_path", ""))
        opts    = table.get("options", {})
        sheet   = opts.get("sheet", "Sheet1")

        col_transforms = self._col_transform_steps(fields)

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Excel.Workbook(File.Contents(FilePath), null, true),\n"
            f"    SheetData = Source{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
            f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true]){col_transforms}\n"
            f"in\n"
            f"    PromotedHeaders"
        )
        notes = f"Excel source: {path}, sheet: {sheet}"
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # JSON
    # ─────────────────────────────────────────────────────────────────────────

    def _m_json(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        fields = table["fields"]
        path   = _normalize_path(table.get("source_path", ""))

        col_transforms = self._col_transform_steps(fields)

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Json.Document(File.Contents(FilePath)),\n"
            f"    ToTable = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n"
            f"    Expanded = Table.ExpandRecordColumn(ToTable, \"Column1\",\n"
            f"        {{{', '.join([chr(34) + (f.get('alias') or f['name']) + chr(34) for f in fields if f['name'] != '*'])}}},\n"
            f"        {{{', '.join([chr(34) + (f.get('alias') or f['name']) + chr(34) for f in fields if f['name'] != '*'])}}}\n"
            f"    ){col_transforms}\n"
            f"in\n"
            f"    Expanded"
        )
        notes = f"JSON source: {path}. Assumes array of records at root level."
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # XML
    # ─────────────────────────────────────────────────────────────────────────

    def _m_xml(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        path = _normalize_path(table.get("source_path", ""))
        col_transforms = self._col_transform_steps(table["fields"])

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Xml.Tables(File.Contents(FilePath)){col_transforms}\n"
            f"in\n"
            f"    Source"
        )
        notes = f"XML source: {path}. Review nested table expansion manually."
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # Parquet
    # ─────────────────────────────────────────────────────────────────────────

    def _m_parquet(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        path = _normalize_path(table.get("source_path", ""))
        col_transforms = self._col_transform_steps(table["fields"])

        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Parquet.Document(File.Contents(FilePath)){col_transforms}\n"
            f"in\n"
            f"    Source"
        )
        notes = f"Parquet source: {path}. Requires Power BI Desktop June 2023+ or a Dataflow."
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # RESIDENT (reference to another M query)
    # ─────────────────────────────────────────────────────────────────────────

    def _m_resident(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        source_table = table.get("source_path", "UnknownTable")
        fields       = table["fields"]

        # Build SELECT column list (skip wildcard)
        selected = [
            f.get("alias") or f["name"]
            for f in fields if f["name"] != "*"
        ]
        col_transforms = self._col_transform_steps(fields)

        if selected:
            select_step = (
                f",\n    Selected = Table.SelectColumns({source_table},\n"
                f"        {{{', '.join([chr(34) + c + chr(34) for c in selected])}}}\n"
                f"    )"
            )
            last_step = "Selected"
        else:
            select_step = ""
            last_step = source_table

        m = (
            f"let\n"
            f"    // References another Power BI query: {source_table}\n"
            f"    {source_table} = {source_table}{select_step}{col_transforms}\n"
            f"in\n"
            f"    {last_step}"
        )
        notes = (
            f"RESIDENT load from '{source_table}'. "
            "Ensure the referenced query exists in this Power BI dataset."
        )
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # SQL (ODBC / Direct Query)
    # ─────────────────────────────────────────────────────────────────────────

    def _m_sql(self, table: Dict, base_path: str, connection_string: Optional[str]) -> tuple[str, str]:
        source_table  = table.get("source_path", "dbo.UnknownTable")
        fields        = table["fields"]
        conn          = connection_string or "[OdbcConnectionString]"

        selected = [
            f.get("alias") or f["name"]
            for f in fields if f["name"] != "*"
        ]
        col_list = ", ".join(f"[{c}]" for c in selected) if selected else "*"
        col_transforms = self._col_transform_steps(fields)

        m = (
            f"let\n"
            f"    ConnectionString = {conn},\n"
            f"    Source = Odbc.Query(\n"
            f"        ConnectionString,\n"
            f"        \"SELECT {col_list} FROM {source_table}\"\n"
            f"    ){col_transforms}\n"
            f"in\n"
            f"    Source"
        )
        notes = (
            f"SQL/ODBC source: {source_table}. "
            "Set the OdbcConnectionString parameter (e.g. DSN=MyDSN or a full ODBC string). "
            "For on-premises sources, an On-premises data gateway is required."
        )
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # Placeholder for unknown / unsupported
    # ─────────────────────────────────────────────────────────────────────────

    def _m_placeholder(self, table: Dict, base_path: str, _cs: Optional[str]) -> tuple[str, str]:
        fields    = table["fields"]
        type_defs = ", ".join(
            f"{_sanitize_col_name(f.get('alias') or f['name'])} = {_m_type(f.get('type', 'string'))}"
            for f in fields if f["name"] != "*"
        )
        m = (
            f"let\n"
            f"    // Placeholder — source type '{table.get('source_type', 'unknown')}' not auto-converted.\n"
            f"    // Original load: {table.get('source_path', 'N/A')}\n"
            f"    Source = #table(\n"
            f"        type table [{type_defs or 'Column1 = type text'}],\n"
            f"        {{}}\n"
            f"    )\n"
            f"in\n"
            f"    Source"
        )
        notes = f"Source type '{table.get('source_type')}' requires manual configuration."
        return m, notes

    # ─────────────────────────────────────────────────────────────────────────
    # Shared helper: column type transform step
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _col_transform_steps(fields: List[Dict]) -> str:
        """
        Generate:
          ,\n    TypedTable = Table.TransformColumnTypes(PreviousStep, { … })
        Returns empty string if no non-wildcard fields.
        """
        typed = [
            f for f in fields
            if f.get("name") not in ("*", "") and f.get("type", "string") != "wildcard"
        ]
        if not typed:
            return ""

        pairs = []
        for f in typed:
            name   = f.get("alias") or f["name"]
            m_type = _m_type(f.get("type", "string"))
            pairs.append(f'{{"{name}", {m_type}}}')

        transform_list = ",\n        ".join(pairs)
        return (
            f",\n"
            f"    TypedTable = Table.TransformColumnTypes(\n"
            f"        PromotedHeaders,\n"
            f"        {{\n        {transform_list}\n        }}\n"
            f"    )"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def convert_to_mquery(
    tables: List[Dict[str, Any]],
    base_path: str = "[DataSourcePath]",
    connection_string: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Convert parsed Qlik tables to M expressions."""
    return MQueryConverter().convert_all(tables, base_path, connection_string)
