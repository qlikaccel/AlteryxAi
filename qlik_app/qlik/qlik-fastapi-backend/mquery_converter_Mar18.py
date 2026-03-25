


# """
# mquery_converter.py
# ───────────────────
# Converts parsed Qlik table definitions into Power Query M expressions
# suitable for embedding in a Power BI BIM partition.
# """

# from __future__ import annotations
# import re
# import logging
# from typing import Any, Dict, List, Optional

# logger = logging.getLogger(__name__)

# # ─────────────────────────────────────────────────────────────────────────────
# # TYPE MAPPING
# # ─────────────────────────────────────────────────────────────────────────────

# _QLIK_TO_M_TYPE: Dict[str, str] = {
#     "string":    "type text",
#     "text":      "type text",
#     "number":    "type number",
#     "integer":   "Int64.Type",
#     "int":       "Int64.Type",
#     "double":    "type number",
#     "decimal":   "type number",
#     "date":      "type date",
#     "time":      "type time",
#     "datetime":  "type datetime",
#     "timestamp": "type datetime",
#     "boolean":   "type logical",
#     "bool":      "type logical",
#     "mixed":     "type any",
#     "wildcard":  "type text",
#     "unknown":   "type text",
# }

# # Types for use INSIDE #table() type signature — no "type" keyword prefix
# _QLIK_TO_M_TYPE_FOR_TABLE: Dict[str, str] = {
#     "string":    "text",
#     "text":      "text",
#     "number":    "number",
#     "integer":   "Int64.Type",
#     "int":       "Int64.Type",
#     "double":    "number",
#     "decimal":   "number",
#     "date":      "date",
#     "time":      "time",
#     "datetime":  "datetime",
#     "timestamp": "datetime",
#     "boolean":   "logical",
#     "bool":      "logical",
#     "mixed":     "any",
#     "wildcard":  "text",
#     "unknown":   "text",
# }

# _DEFAULT_M_TYPE = "type text"
# _DEFAULT_M_TYPE_FOR_TABLE = "text"

# # ─────────────────────────────────────────────────────────────────────────────
# # SMART COLUMN TYPE INFERENCE
# # ─────────────────────────────────────────────────────────────────────────────
# # When Qlik type metadata is missing/unknown, infer Power BI compatible types
# # from column name patterns.
# #
# # CRITICAL RULE FOR ID / KEY / NO COLUMNS:
# #   Column name alone is NOT sufficient to assign Int64.Type for ID-like
# #   columns (patient_id, doctor_id, ward_no, etc.).
# #
# #   Reason: ID columns frequently contain mixed alphanumeric values such as
# #   "P001", "DOC-123", "D_456", "W-2A" — assigning Int64.Type to these causes
# #   a type-conversion error at Power BI refresh time ("We couldn't convert
# #   'P001' to Number").
# #
# #   Safe rule:
# #     • Name ends in _id/_key/_no/_num → type text   (safe default)
# #     • Qlik explicitly reports type = "integer"/"int" → Int64.Type   (trust it)
# #
# #   Power BI relationship rule:
# #     Both sides of a relationship must share the SAME datatype.
# #     By consistently typing all ID columns as text (unless explicitly integer),
# #     we guarantee the same type on both sides → relationships never break.
# #
# # Rule priority (implemented in _m_type):
# #   1. Composite key (contains '-')            → type text  (always)
# #   2. Explicit Qlik type = integer/int        → Int64.Type
# #   3. Explicit Qlik type = date/datetime/etc  → respective type
# #   4. Explicit Qlik type = number/double/etc  → type number
# #   5. Name-based inference for safe patterns  → date/number/logical only
# #   6. Fallback                                → type text
# #
# # NOTE: _NAME_TO_M_TYPE intentionally does NOT include ID/key/no → Int64.
# # Those are handled exclusively via explicit Qlik type metadata (rule 2).
# # ─────────────────────────────────────────────────────────────────────────────

# # Column name suffixes/patterns → M type
# # IMPORTANT: No ID/key/no/num patterns here — see CRITICAL RULE above.
# _NAME_TO_M_TYPE: List[tuple] = [
#     # Date/time columns — safe to infer from name (no ambiguity)
#     (re.compile(r"_date$|^date$|_dt$|_timestamp$", re.I),                       "type date"),
#     (re.compile(r"_datetime$|_created_at$|_updated_at$|_modified_at$", re.I),   "type datetime"),
#     # Pure numeric measure columns — safe to infer from name
#     # (these are never alphanumeric in practice)
#     (re.compile(r"_cost$|_fee$|_bill$|_price$|_rate$|_salary$", re.I),         "type number"),
#     (re.compile(r"_covered$|_expense$|_revenue$|_income$|_tax$|_discount$", re.I), "type number"),
#     (re.compile(r"_amount$", re.I),                                             "type number"),
#     (re.compile(r"_days$|_years$|_count$|_qty$|_quantity$", re.I),              "Int64.Type"),
#     (re.compile(r"_pct$|_percent$|_ratio$|_score$|_weight$", re.I),             "type number"),
#     # Boolean-like columns
#     (re.compile(r"^is_|^has_|^can_|^was_|_flag$|_bool$", re.I),                "type logical"),
# ]


# def _infer_type_from_name(col_name: str) -> str:
#     """
#     Infer Power BI M type from column name patterns.

#     DOES NOT infer Int64 for _id/_key/_no — those default to type text
#     because they commonly contain mixed alphanumeric values (P001, DOC-123).
#     Only explicit Qlik type = 'integer'/'int' triggers Int64.Type for such cols.

#     Returns M type string WITH 'type' prefix (e.g. 'type date', 'type number').
#     Falls back to 'type text' for unrecognised / ambiguous names.
#     """
#     clean = col_name.strip().lower()
#     # Strip Qlik table-qualified prefix for matching
#     if "." in clean and not clean.startswith("#"):
#         clean = clean.split(".", 1)[-1]
#     # Composite keys always text
#     if "-" in clean:
#         return "type text"
#     for pattern, m_type in _NAME_TO_M_TYPE:
#         if pattern.search(clean):
#             return m_type
#     return "type text"


# def _m_type(qlik_type: str, col_name: str = "") -> str:
#     """
#     Resolve the correct Power BI M type for a column.

#     Priority order:
#       1. Composite key (col_name contains '-')   → type text
#       2. Explicit Qlik integer/int               → Int64.Type
#       3. Any other explicit non-text Qlik type   → mapped M type
#       4. Name-based safe inference               → date/number/logical only
#       5. Fallback                                → type text

#     ID / key / no columns:
#       When qlik_type is 'string'/'unknown', these default to type text even
#       if the name ends in _id/_key/_no, because their values are often
#       alphanumeric (P001, DOC-123).  Assign Int64 only when Qlik explicitly
#       reports integer — that means the data is guaranteed to be pure numeric.
#     """
#     if "-" in col_name:
#         return "type text"

#     # Resolve plain name (strip Qlik table-qualified prefix)
#     plain_name = (
#         col_name.split(".", 1)[-1]
#         if ("." in col_name and not col_name.startswith("#"))
#         else col_name
#     )

#     qlik_lower = str(qlik_type).lower().strip()

#     # Explicit Qlik type — trust it completely
#     if qlik_lower not in ("string", "text", "unknown", "mixed", "wildcard", ""):
#         return _QLIK_TO_M_TYPE.get(qlik_lower, _DEFAULT_M_TYPE)

#     # No explicit type info → safe name-based inference only
#     return _infer_type_from_name(plain_name)


# def _m_type_for_table(qlik_type: str, col_name: str = "") -> str:
#     """
#     M type WITHOUT 'type' prefix — for #table() column signatures.

#     Uses 'text' for all columns to avoid VT_BSTR → VT_I8 failures on
#     empty values in inline tables. Inline tables are small reference
#     tables — type safety matters less than load reliability here.
#     """
#     return "text"

# def _normalize_path(path: str) -> str:
#     path = re.sub(r"^lib://[^/]*/", "", path)
#     path = re.sub(r"^lib://", "", path)
#     return path


# def _sanitize_col_name(name: str) -> str:
#     if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
#         return name
#     return f'#"{name}"'


# def _strip_qlik_qualifier(col_name: str) -> str:
#     """
#     Strip Qlik table-qualified prefix from column name.

#     'Dealer_Master.City_GeoInfo'  →  'City_GeoInfo'
#     'Model_Master.ModelID'        →  'ModelID'
#     'DealerID-ServiceID'          →  'DealerID-ServiceID'  (composite key, keep as-is)
#     '#"Something"'                →  '#"Something"'        (already escaped)

#     Critical: the actual CSV column is just 'City_GeoInfo', not the
#     Qlik-qualified 'Dealer_Master.City_GeoInfo'. Using the qualified name
#     as a BIM column causes column-not-found errors at query time.
#     """
#     if not col_name or col_name.startswith("#"):
#         return col_name
#     # Only strip if dot present AND no hyphen (composite keys keep their name)
#     if "." in col_name and "-" not in col_name:
#         return col_name.split(".", 1)[-1]
#     return col_name


# def _is_sharepoint_url(url: str) -> bool:
#     """Check if a URL is a SharePoint URL (includes OneDrive for Business)."""
#     cleaned = url.strip().strip('"').strip("'").lower()
#     return "sharepoint.com" in cleaned or "-my.sharepoint.com" in cleaned


# def _is_onedrive_personal_url(url: str) -> bool:
#     """Check if a URL is a personal OneDrive URL."""
#     cleaned = url.strip().strip('"').strip("'").lower()
#     return "onedrive.live.com" in cleaned or "1drv.ms" in cleaned


# def _is_s3_url(url: str) -> bool:
#     """Check if a URL is an AWS S3 URL."""
#     cleaned = url.strip().strip('"').strip("'").lower()
#     return "s3.amazonaws.com" in cleaned or cleaned.startswith("s3://") or ".s3." in cleaned


# def _is_azure_blob_url(url: str) -> bool:
#     """Check if a URL is an Azure Blob Storage URL."""
#     cleaned = url.strip().strip('"').strip("'").lower()
#     return "blob.core.windows.net" in cleaned or "dfs.core.windows.net" in cleaned


# def _is_web_url(url: str) -> bool:
#     """Check if a URL is a generic web URL (non-cloud-storage)."""
#     cleaned = url.strip().strip('"').strip("'").lower()
#     return (cleaned.startswith("http://") or cleaned.startswith("https://")) and not (
#         _is_sharepoint_url(url) or _is_s3_url(url) or _is_azure_blob_url(url)
#     )


# def _quote_url(url: str) -> str:
#     """Ensure a URL is properly quoted for M expressions."""
#     cleaned = url.strip().strip('"').strip("'")
#     return f'"{cleaned}"'


# def _build_sharepoint_m(
#     site_url: str,
#     filename: str,
#     folder_path: str,
#     delimiter: str,
#     encoding: int,
#     transform_step: str,
#     final_step: str,
#     is_qvd: bool = False,
# ) -> str:
#     """
#     Build a fully DYNAMIC SharePoint.Files() M expression.

#     HOW IT WORKS:
#     - site_url  : the SharePoint root passed by the user at runtime
#                   (e.g. "https://sorimtechnologies.sharepoint.com")
#     - filename  : CSV file name extracted from the Qlik load script
#     - folder_path: expected folder URL — used as a hint but NOT hardcoded
#                   in the M query; the M query resolves it dynamically.

#     WHY SharePoint.Files() NOT SharePoint.Contents():
#     - SharePoint.Contents() → Power BI asks for "Web" credential  (WRONG ❌)
#     - SharePoint.Files()    → Power BI asks for "SharePoint" credential (CORRECT ✅)

#     DYNAMIC SEARCH STRATEGY (no hardcoded paths):
#     1. SharePoint.Files(site_url) returns ALL files under the site.
#     2. First try: match [Name] exactly (case-insensitive).
#     3. The M expression is fully parameterised — only site_url and filename
#        are embedded; the folder structure is discovered at runtime.
#     4. If 0 rows → clear error message shows what was searched.
#     """
#     qvd_comment = "    // QVD converted to CSV — SharePoint.Files() reads the CSV version\n" if is_qvd else ""

#     m = (
#         f"let\n"
#         f"{qvd_comment}"
#         f"    // ── Dynamic SharePoint source ─────────────────────────────────────\n"
#         f"    // site_url comes from the base_path the user configured.\n"
#         f"    // SharePoint.Files() scans ALL files under the site automatically.\n"
#         f"    // No folder paths are hardcoded — works regardless of subfolder structure.\n"
#         f"    SiteUrl = \"{site_url}\",\n"
#         f"    FileName = \"{filename}\",\n"
#         f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
#         f"    // Step 1: find rows where filename matches (case-insensitive)\n"
#         f"    FilteredByName = Table.SelectRows(\n"
#         f"        Source,\n"
#         f"        each Text.Lower([Name]) = Text.Lower(FileName)\n"
#         f"    ),\n"
#         f"    // Step 2: if multiple files with same name exist, pick the one\n"
#         f"    // whose [Folder Path] contains \"Shared Documents\" (standard library)\n"
#         f"    FilteredByLib = if Table.RowCount(FilteredByName) > 1\n"
#         f"        then Table.SelectRows(FilteredByName, each Text.Contains([Folder Path], \"Shared Documents\"))\n"
#         f"        else FilteredByName,\n"
#         f"    // Step 3: pick first match\n"
#         f"    FileBinary = if Table.RowCount(FilteredByLib) = 0\n"
#         f"        then error \"File \" & FileName & \" not found under \" & SiteUrl &\n"
#         f"             \". Ensure the file exists in SharePoint and credentials are set.\"\n"
#         f"        else FilteredByLib{{0}}[Content],\n"
#         f"    CsvData = Csv.Document(\n"
#         f"        FileBinary,\n"
#         f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#         f"    ),\n"
#         f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
#         f"{transform_step}\n"
#         f"in\n"
#         f"    {final_step}"
#     )
#     return m


# def _build_s3_m(
#     bucket_url: str,
#     filename: str,
#     delimiter: str,
#     encoding: int,
#     transform_step: str,
#     final_step: str,
# ) -> str:
#     """Build M expression for AWS S3 via Web.Contents() (public/pre-signed URL)."""
#     clean_url = bucket_url.strip().strip('"').strip("'").rstrip("/")
#     file_url = f"{clean_url}/{filename}"
#     m = (
#         f"let\n"
#         f"    // AWS S3: Web.Contents() — use pre-signed URL or make bucket public\n"
#         f"    // For private S3, use On-Premises Data Gateway with ODBC/JDBC connector\n"
#         f"    Source = Web.Contents(\"{file_url}\"),\n"
#         f"    CsvData = Csv.Document(\n"
#         f"        Source,\n"
#         f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#         f"    ),\n"
#         f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
#         f"{transform_step}\n"
#         f"in\n"
#         f"    {final_step}"
#     )
#     return m


# def _build_azure_blob_m(
#     container_url: str,
#     filename: str,
#     delimiter: str,
#     encoding: int,
#     transform_step: str,
#     final_step: str,
# ) -> str:
#     """Build M expression for Azure Blob Storage via AzureStorage.Blobs()."""
#     clean_url = container_url.strip().strip('"').strip("'").rstrip("/")
#     m = (
#         f"let\n"
#         f"    // Azure Blob Storage — authenticate with Account Key or SAS token\n"
#         f"    Source = AzureStorage.Blobs(\"{clean_url}\"),\n"
#         f"    FileRow = Table.SelectRows(Source, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#         f"    FileBinary = if Table.RowCount(FileRow) = 0 then error \"{filename} not found in Azure Blob\" else FileRow{{0}}[Content],\n"
#         f"    CsvData = Csv.Document(\n"
#         f"        FileBinary,\n"
#         f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#         f"    ),\n"
#         f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
#         f"{transform_step}\n"
#         f"in\n"
#         f"    {final_step}"
#     )
#     return m


# # ─────────────────────────────────────────────────────────────────────────────
# # MAIN CONVERTER
# # ─────────────────────────────────────────────────────────────────────────────

# class MQueryConverter:

#     # ============================================================
#     # PUBLIC
#     # ============================================================

#     def convert_all(
#         self,
#         tables: List[Dict[str, Any]],
#         base_path: str = "[DataSourcePath]",
#         connection_string: Optional[str] = None,
#     ) -> List[Dict[str, Any]]:

#         results = []

#         for table in tables:
#             m_expr, notes = self._dispatch(table, base_path, connection_string)
#             results.append({
#                 "name":         table["name"],
#                 "source_type":  table["source_type"],
#                 "m_expression": m_expr,
#                 "fields":       table["fields"],
#                 "notes":        notes,
#                 "source_path":  table.get("source_path", ""),
#                 "options":      table.get("options", {}),
#             })

#         logger.info("[MQueryConverter] Converted %d table(s)", len(results))
#         return results

#     def convert_one(
#         self,
#         table: Dict[str, Any],
#         base_path: str = "[DataSourcePath]",
#         connection_string: Optional[str] = None,
#         all_table_names: Optional[set] = None,
#     ) -> str:
#         m_expr, _ = self._dispatch(table, base_path, connection_string)
#         return m_expr

#     # ============================================================
#     # DISPATCH
#     # ============================================================

#     def _dispatch(self, table, base_path, connection_string):
#         source_type = table.get("source_type", "")
#         dispatch = {
#             "inline":   self._m_inline,
#             "csv":      self._m_csv,
#             "excel":    self._m_excel,
#             "json":     self._m_json,
#             "xml":      self._m_xml,
#             "parquet":  self._m_parquet,
#             "qvd":      self._m_qvd,
#             "resident": self._m_resident,
#             "sql":      self._m_sql,
#         }
#         handler = dispatch.get(source_type, self._m_placeholder)
#         try:
#             return handler(table, base_path, connection_string)
#         except Exception as exc:
#             logger.warning("[MQuery] Error converting '%s': %s", table.get("name"), exc)
#             return self._m_placeholder(table, base_path, connection_string), f"Conversion error: {exc}"

#     # ============================================================
#     # SHARED TYPE TRANSFORM STEP
#     # ============================================================

#     def _apply_types_as_is(self, fields: List[Dict], previous_step: str):
#         """
#         Like _apply_types but uses the SOURCE column name (expression, not alias).
#         Skips computed fields (APPLYMAP, IF, expressions with parentheses).
#         Used for SharePoint sources so columns match what's actually in the file.

#         KEY FIX: Uses smart type inference (_m_type) instead of hardcoding
#         everything as 'type text'. This ensures relationship key columns
#         (e.g. patient_id, doctor_id, disease_id) get the correct Power BI
#         datatype so cloud semantic model relationships (openSemanticModel)
#         don't break due to type mismatches between tables.

#         Safe strategy:
#           - ID/key/date/numeric columns → inferred correct type
#           - All other string columns → type text (safe for nulls/empty values)
#         """
#         pairs = []
#         for f in fields:
#             expr = f.get("expression", "")
#             # Skip computed expressions — anything with ( or known functions
#             if not expr or expr == "*":
#                 continue
#             if "(" in expr or expr.upper().startswith("APPLYMAP") or expr.upper().startswith("IF"):
#                 continue
#             # Extract plain column name from expression (strip brackets)
#             if expr.startswith("[") and expr.endswith("]"):
#                 col_name = expr[1:-1]
#             elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
#                 col_name = expr
#             else:
#                 continue  # skip anything else that's not a plain column name

#             # Use smart type inference: explicit Qlik type first, then name-based
#             qlik_type = f.get("type", "string")
#             m_type = _m_type(qlik_type, col_name)
#             pairs.append(f'{{"{col_name}", {m_type}}}')

#         if not pairs:
#             return "", previous_step

#         pairs_str = ",\n        ".join(pairs)
#         transform = (
#             f",\n"
#             f"    TypedTable = Table.TransformColumnTypes(\n"
#             f"        {previous_step},\n"
#             f"        {{\n        {pairs_str}\n        }}\n"
#             f"    )"
#         )
#         return transform, "TypedTable"

#     def _apply_types(self, fields: List[Dict], previous_step: str):
#         """
#         Returns (transform_step_str, final_step_name).
#         Uses 'type' prefix form — correct for TransformColumnTypes.
#         Returns ("", previous_step) if no typed fields.

#         KEY FIX: Uses smart type inference (_m_type) which combines:
#           1. Explicit Qlik type metadata (integer, date, etc.)
#           2. Column name pattern matching (_infer_type_from_name) for
#              columns that Qlik reports as 'string'/'unknown'
#           3. Composite key fields (containing '-') → always text
#           4. Qlik-qualified names (Table.Column) → strip prefix then infer

#         This fixes Power BI cloud relationship breakage caused by type
#         mismatches — e.g. patient_id must be Int64.Type in BOTH patients
#         and admissions tables for the relationship to work in the
#         openSemanticModel (cloud semantic model).
#         """
#         typed = [f for f in fields if f.get("name") not in ("*", "")]
#         if not typed:
#             return "", previous_step

#         pairs = []
#         for f in typed:
#             raw_name = f.get("alias") or f["name"]
#             # Strip Qlik table-qualified prefix → use the plain CSV column name
#             col_name = _strip_qlik_qualifier(raw_name)
#             # Get M type: explicit Qlik type takes priority, then name inference
#             m_type = _m_type(f.get("type", "string"), raw_name)
#             pairs.append(f'{{"{col_name}", {m_type}}}')

#         pairs_str = ",\n        ".join(pairs)
#         transform = (
#             f",\n"
#             f"    TypedTable = Table.TransformColumnTypes(\n"
#             f"        {previous_step},\n"
#             f"        {{\n        {pairs_str}\n        }}\n"
#             f"    )"
#         )
#         return transform, "TypedTable"

#     # ============================================================
#     # INLINE
#     # ============================================================

#     def _m_inline(self, table, base_path, _cs):
#         opts   = table.get("options", {})
#         fields = table["fields"]

#         headers = opts.get(
#             "inline_headers",
#             [f["name"] for f in fields if f["name"] != "*"]
#         )
#         rows = opts.get("inline_rows_all") or opts.get("inline_sample", [])

#         # Column type defs — NO "type" prefix inside #table() signature
#         type_defs = ", ".join(
#             f"{_sanitize_col_name(_strip_qlik_qualifier(h))} = "
#             f"{_m_type_for_table(next((f['type'] for f in fields if f['name'] == h), 'string'), h)}"
#             for h in headers
#         )

#         # Build data rows — strip Qlik single-quotes, escape double-quotes
#         row_strs = []
#         for row in rows:
#             vals = []
#             for h in headers:
#                 v = str(row.get(h, ""))
#                 v = v.strip("'")            # strip Qlik single quotes
#                 v = v.replace('"', '""')    # escape embedded double quotes
#                 v = v.replace("\n", " ")    # collapse embedded newlines
#                 v = v.replace("\r", "")     # remove carriage returns
#                 v = " ".join(v.split())     # collapse multiple spaces
#                 vals.append(f'"{v}"')
#             # Entire row MUST be on one line
#             row_strs.append("{" + ", ".join(vals) + "}")
#         rows_m = (
#             "{\n        " + ",\n        ".join(row_strs) + "\n    }"
#             if row_strs else "{}"
#         )
#         # Safety: ensure no row spans multiple lines
#         import re
#         rows_m_safe = re.sub(r',\s*\n\s*"', ', "', rows_m)
#         rows_m = rows_m_safe
#         m = (
#             f"let\n"
#             f"    Source = #table(\n"
#             f"        type table [{type_defs}],\n"
#             f"        {rows_m}\n"
#             f"    )\n"
#             f"in\n"
#             f"    Source"
#         )
#         return m, f"Inline table with {len(rows)} row(s). Data embedded directly."

#     # ============================================================
#     # CSV
#     # ============================================================

#     def _m_csv(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]
#         opts   = table.get("options", {})

#         delimiter = opts.get("delimiter", ",")
#         encoding  = 65001
#         enc_str   = opts.get("encoding", "")
#         if enc_str:
#             enc_map = {"UTF-8": 65001, "UTF8": 65001, "UTF-16": 1200, "UTF16": 1200}
#             encoding = enc_map.get(enc_str.upper().replace("-", ""), encoding)

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         # ── Universal source routing ──────────────────────────────────────────
#         # Supports: SharePoint/OneDrive-for-Business, AWS S3, Azure Blob,
#         #           generic Web URL, and local/on-prem File.Contents()
#         filename_only = path.rsplit("/", 1)[-1] if "/" in path else path

#         if _is_sharepoint_url(base_path):
#             # SharePoint / OneDrive for Business
#             # FIX: SharePoint.Files() triggers SharePoint credential (not Web credential)
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_sharepoint_m(
#                 site_url=site_url,
#                 filename=filename,
#                 folder_path=folder_path,
#                 delimiter=delimiter,
#                 encoding=encoding,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#             )
#         elif _is_s3_url(base_path):
#             # AWS S3
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_s3_m(
#                 bucket_url=base_path,
#                 filename=filename_only,
#                 delimiter=delimiter,
#                 encoding=encoding,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#             )
#         elif _is_azure_blob_url(base_path):
#             # Azure Blob Storage
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_azure_blob_m(
#                 container_url=base_path,
#                 filename=filename_only,
#                 delimiter=delimiter,
#                 encoding=encoding,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#             )
#         elif _is_web_url(base_path):
#             # Generic HTTPS web source (public URL)
#             clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
#             file_url = f"{clean_bp}/{path}" if path else clean_bp
#             m = (
#                 f"let\n"
#                 f"    Source = Web.Contents(\"{file_url}\"),\n"
#                 f"    CsvData = Csv.Document(\n"
#                 f"        Source,\n"
#                 f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#                 f"    ),\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         else:
#             # Local/on-prem: use File.Contents()
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{path}"'
#             m = (
#                 f"let\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Csv.Document(\n"
#                 f"        File.Contents(FilePath),\n"
#                 f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#                 f"    ),\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"CSV source: {path}"

#     # ============================================================
#     # SHAREPOINT HELPERS
#     # ============================================================

#     def _get_sharepoint_parts(self, base_path: str, source_path: str, opts: dict):
#         """
#         Extract (site_url, folder_path, filename) from the user-supplied base_path.

#         DESIGN: The M query generated by _build_sharepoint_m is FULLY DYNAMIC —
#         it calls SharePoint.Files(site_url) and searches by filename at runtime.
#         No folder path is hardcoded in the M expression.

#         This function only needs to:
#           1. Extract a clean site_url (root of the SharePoint site) from base_path
#           2. Extract the filename from source_path
#           3. Return folder_path (kept for Excel compatibility but not used in CSV filter)

#         Supported base_path formats:
#           A) "https://sorimtechnologies.sharepoint.com"
#              → site_url = "https://sorimtechnologies.sharepoint.com"
#           B) "https://sorimtechnologies.sharepoint.com/Shared Documents"
#              → site_url = "https://sorimtechnologies.sharepoint.com"
#           C) "https://sorimtechnologies.sharepoint.com/Shared Documents/SubFolder"
#              → site_url = "https://sorimtechnologies.sharepoint.com"
#           D) "https://company.sharepoint.com/sites/TeamSite"
#              → site_url = "https://company.sharepoint.com/sites/TeamSite"
#           E) "https://company.sharepoint.com/sites/TeamSite/Shared Documents/Folder"
#              → site_url = "https://company.sharepoint.com/sites/TeamSite"
#         """
#         raw = base_path.strip().strip('"').strip("'").rstrip("/")

#         # ── Extract clean site_url ─────────────────────────────────────────────
#         if "/Shared Documents" in raw:
#             # Strip everything from /Shared Documents onward
#             site_url = raw.split("/Shared Documents")[0].rstrip("/")
#             folder_path = raw + "/"
#         elif "/sites/" in raw:
#             # Keep up to and including /sites/SiteName
#             parts = raw.split("/")
#             # https://host/sites/SiteName → parts[0]=https:, [1]='', [2]=host, [3]=sites, [4]=SiteName
#             site_url = "/".join(parts[:5]) if len(parts) >= 5 else raw
#             folder_path = f"{site_url}/Shared Documents/"
#         else:
#             # Bare root: "https://sorimtechnologies.sharepoint.com"
#             site_url = raw
#             folder_path = f"{site_url}/Shared Documents/"

#         # Override folder_path if sp_subfolder explicitly set
#         sp_subfolder = opts.get("sp_subfolder", "")
#         if sp_subfolder:
#             folder_path = f"{site_url}/Shared Documents/{sp_subfolder.strip('/')}/"
#         elif "/" in source_path:
#             sub = source_path.rsplit("/", 1)[0]
#             folder_path = f"{site_url}/Shared Documents/{sub}/"

#         # ── Extract filename ───────────────────────────────────────────────────
#         filename = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
#         if not filename:
#             table_name = opts.get("table_name", "Table")
#             filename = f"{table_name}.csv"

#         return site_url, folder_path, filename

#     # ============================================================
#     # EXCEL
#     # ============================================================

#     def _m_excel(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         sheet  = table.get("options", {}).get("sheet", "Sheet1")
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         if _is_sharepoint_url(base_path):
#             opts = table.get("options", {})
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
#             excel_subfolder = folder_path.rstrip("/")
#             if "/Shared Documents/" in excel_subfolder:
#                 excel_subfolder = excel_subfolder.split("/Shared Documents/", 1)[1].strip("/")
#             else:
#                 excel_subfolder = ""

#             if excel_subfolder:
#                 excel_nav = (
#                     f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#                     f"    Folder = Documents{{[Name=\"{excel_subfolder}\"]}}[Content],\n"
#                     f"    FileRow = Table.SelectRows(Folder, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#                 )
#             else:
#                 excel_nav = (
#                     f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#                     f"    FileRow = Table.SelectRows(Documents, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#                 )

#             # DYNAMIC Excel from SharePoint — same search strategy as CSV
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = (
#                 f"let\n"
#                 f"    // Dynamic SharePoint Excel — no hardcoded folder paths\n"
#                 f"    SiteUrl = \"{site_url}\",\n"
#                 f"    FileName = \"{filename}\",\n"
#                 f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
#                 f"    FilteredByName = Table.SelectRows(\n"
#                 f"        Source,\n"
#                 f"        each Text.Lower([Name]) = Text.Lower(FileName)\n"
#                 f"    ),\n"
#                 f"    FilteredByLib = if Table.RowCount(FilteredByName) > 1\n"
#                 f"        then Table.SelectRows(FilteredByName, each Text.Contains([Folder Path], \"Shared Documents\"))\n"
#                 f"        else FilteredByName,\n"
#                 f"    FileBinary = if Table.RowCount(FilteredByLib) = 0\n"
#                 f"        then error \"File \" & FileName & \" not found under \" & SiteUrl\n"
#                 f"        else FilteredByLib{{0}}[Content],\n"
#                 f"    ExcelData = Excel.Workbook(FileBinary, null, true),\n"
#                 f"    SheetData = ExcelData{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
#                 f"{sp_transform}\n"
#                 f"in\n"
#                 f"    {sp_final}"
#             )
#         else:
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{path}"'
#             m = (
#                 f"let\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Excel.Workbook(File.Contents(FilePath), null, true),\n"
#                 f"    SheetData = Source{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"Excel source: {path}, sheet: {sheet}"

#     # ============================================================
#     # JSON
#     # ============================================================

#     def _m_json(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         expand_cols = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
#         col_list    = ", ".join(f'"{c}"' for c in expand_cols)

#         transform, final = self._apply_types(fields, "Expanded")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Json.Document(File.Contents(FilePath)),\n"
#             f"    ToTable = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n"
#             f"    Expanded = Table.ExpandRecordColumn(ToTable, \"Column1\",\n"
#             f"        {{{col_list}}},\n"
#             f"        {{{col_list}}}\n"
#             f"    )"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"JSON source: {path}. Assumes array of records at root level."

#     # ============================================================
#     # XML
#     # ============================================================

#     def _m_xml(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Xml.Tables(File.Contents(FilePath))"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"XML source: {path}. Review nested table expansion manually."

#     # ============================================================
#     # PARQUET
#     # ============================================================

#     def _m_parquet(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Parquet.Document(File.Contents(FilePath))"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"Parquet source: {path}."

#     # ============================================================
#     # QVD (CSV fallback)
#     # ============================================================

#     def _m_qvd(self, table, base_path, _cs):
#         path     = _normalize_path(table.get("source_path", ""))
#         csv_path = re.sub(r"\.qvd$", ".csv", path, flags=re.IGNORECASE)
#         fields   = table["fields"]

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         if _is_sharepoint_url(base_path):
#             opts = table.get("options", {})
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, csv_path, opts)
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_sharepoint_m(
#                 site_url=site_url,
#                 filename=filename,
#                 folder_path=folder_path,
#                 delimiter=",",
#                 encoding=65001,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#                 is_qvd=True,
#             )
#         else:
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{csv_path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{csv_path}"'
#             m = (
#                 f"let\n"
#                 f"    // QVD not supported natively — expects pre-converted CSV\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Csv.Document(\n"
#                 f"        File.Contents(FilePath),\n"
#                 f"        [Delimiter=\",\", Encoding=65001]\n"
#                 f"    ),\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"QVD fallback (pre-convert to CSV): {path}"


#     # ============================================================
#     # RESIDENT
#     # ============================================================

#     def _m_resident(self, table, base_path, _cs):
#         source_table = table.get("source_path", "UnknownTable")
#         fields       = table["fields"]

#         selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]

#         if selected:
#             select_step = (
#                 f",\n    Selected = Table.SelectColumns({source_table},\n"
#                 f"        {{{', '.join(chr(34) + c + chr(34) for c in selected)}}}\n"
#                 f"    )"
#             )
#             intermediate = "Selected"
#         else:
#             select_step = ""
#             intermediate = source_table

#         transform, final = self._apply_types(fields, intermediate)

#         m = (
#             f"let\n"
#             f"    // References another Power BI query: {source_table}\n"
#             f"    {source_table} = {source_table}"
#             f"{select_step}"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"RESIDENT load from '{source_table}'."

#     # ============================================================
#     # SQL
#     # ============================================================

#     def _m_sql(self, table, base_path, connection_string):
#         source_table = table.get("source_path", "dbo.UnknownTable")
#         fields       = table["fields"]
#         conn         = connection_string or "[OdbcConnectionString]"

#         selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
#         col_list = ", ".join(f"[{c}]" for c in selected) if selected else "*"

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    ConnectionString = {conn},\n"
#             f"    Source = Odbc.Query(\n"
#             f"        ConnectionString,\n"
#             f"        \"SELECT {col_list} FROM {source_table}\"\n"
#             f"    )"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"SQL/ODBC source: {source_table}."

#     # ============================================================
#     # PLACEHOLDER
#     # ============================================================

#     def _m_placeholder(self, table, base_path, _cs):
#         fields = table["fields"]

#         type_defs = ", ".join(
#             f"{_sanitize_col_name(f.get('alias') or f['name'])} = {_m_type(f.get('type', 'string'))}"
#             for f in fields if f["name"] != "*"
#         ) or "Column1 = type text"

#         m = (
#             f"let\n"
#             f"    // Placeholder — source type '{table.get('source_type', 'unknown')}' not auto-converted.\n"
#             f"    // Original load: {table.get('source_path', 'N/A')}\n"
#             f"    Source = #table(\n"
#             f"        type table [{type_defs}],\n"
#             f"        {{}}\n"
#             f"    )\n"
#             f"in\n"
#             f"    Source"
#         )
#         return m, f"Source type '{table.get('source_type')}' requires manual configuration."


# # ─────────────────────────────────────────────────────────────────────────────
# # CONVENIENCE
# # ─────────────────────────────────────────────────────────────────────────────

# def convert_to_mquery(
#     tables: List[Dict[str, Any]],
#     base_path: str = "[DataSourcePath]",
#     connection_string: Optional[str] = None,
# ) -> List[Dict[str, Any]]:
#     """Convert parsed Qlik tables to M expressions."""
#     return MQueryConverter().convert_all(tables, base_path, connection_string)







# """
# mquery_converter.py
# ───────────────────
# Converts parsed Qlik table definitions into Power Query M expressions
# suitable for embedding in a Power BI BIM partition.
# """

# from __future__ import annotations
# import re
# import logging
# from typing import Any, Dict, List, Optional

# logger = logging.getLogger(__name__)

# # ─────────────────────────────────────────────────────────────────────────────
# # TYPE MAPPING
# # ─────────────────────────────────────────────────────────────────────────────

# _QLIK_TO_M_TYPE: Dict[str, str] = {
#     "string":    "type text",
#     "text":      "type text",
#     "number":    "type number",
#     "integer":   "Int64.Type",
#     "int":       "Int64.Type",
#     "double":    "type number",
#     "decimal":   "type number",
#     "date":      "type date",
#     "time":      "type time",
#     "datetime":  "type datetime",
#     "timestamp": "type datetime",
#     "boolean":   "type logical",
#     "bool":      "type logical",
#     "mixed":     "type any",
#     "wildcard":  "type text",
#     "unknown":   "type text",
# }

# # Types for use INSIDE #table() type signature — no "type" keyword prefix
# _QLIK_TO_M_TYPE_FOR_TABLE: Dict[str, str] = {
#     "string":    "text",
#     "text":      "text",
#     "number":    "number",
#     "integer":   "Int64.Type",
#     "int":       "Int64.Type",
#     "double":    "number",
#     "decimal":   "number",
#     "date":      "date",
#     "time":      "time",
#     "datetime":  "datetime",
#     "timestamp": "datetime",
#     "boolean":   "logical",
#     "bool":      "logical",
#     "mixed":     "any",
#     "wildcard":  "text",
#     "unknown":   "text",
# }

# _DEFAULT_M_TYPE = "type text"
# _DEFAULT_M_TYPE_FOR_TABLE = "text"


# def _m_type(qlik_type: str, col_name: str = "") -> str:
#     """M type with 'type' prefix — for TransformColumnTypes.

#     Composite keys (containing '-') and Qlik-qualified names (Table.Column)
#     are always 'type text' regardless of inferred type.
#     """
#     if "-" in col_name or ("." in col_name and not col_name.startswith("#")):
#         return "type text"
#     return _QLIK_TO_M_TYPE.get(str(qlik_type).lower().strip(), _DEFAULT_M_TYPE)


# # def _m_type_for_table(qlik_type: str, col_name: str = "") -> str:
# #     """M type WITHOUT 'type' prefix — for #table() column signatures."""
# #     if "-" in col_name or ("." in col_name and not col_name.startswith("#")):
# #         return "text"
# #     return _QLIK_TO_M_TYPE_FOR_TABLE.get(str(qlik_type).lower().strip(), _DEFAULT_M_TYPE_FOR_TABLE)

# def _m_type_for_table(qlik_type: str, col_name: str = "") -> str:
#     """M type WITHOUT 'type' prefix — for #table() column signatures.
#     Always text to avoid VT_BSTR → VT_I8 failures on empty values."""
#     return "text"

# def _normalize_path(path: str) -> str:
#     path = re.sub(r"^lib://[^/]*/", "", path)
#     path = re.sub(r"^lib://", "", path)
#     return path


# def _sanitize_col_name(name: str) -> str:
#     if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
#         return name
#     return f'#"{name}"'


# def _strip_qlik_qualifier(col_name: str) -> str:
#     """
#     Strip Qlik table-qualified prefix from column name.

#     'Dealer_Master.City_GeoInfo'  →  'City_GeoInfo'
#     'Model_Master.ModelID'        →  'ModelID'
#     'DealerID-ServiceID'          →  'DealerID-ServiceID'  (composite key, keep as-is)
#     '#"Something"'                →  '#"Something"'        (already escaped)

#     Critical: the actual CSV column is just 'City_GeoInfo', not the
#     Qlik-qualified 'Dealer_Master.City_GeoInfo'. Using the qualified name
#     as a BIM column causes column-not-found errors at query time.
#     """
#     if not col_name or col_name.startswith("#"):
#         return col_name
#     # Only strip if dot present AND no hyphen (composite keys keep their name)
#     if "." in col_name and "-" not in col_name:
#         return col_name.split(".", 1)[-1]
#     return col_name


# def _is_sharepoint_url(url: str) -> bool:
#     """Check if a URL is a SharePoint URL."""
#     cleaned = url.strip().strip('"').strip("'")
#     return "sharepoint.com" in cleaned.lower()


# def _quote_url(url: str) -> str:
#     """Ensure a URL is properly quoted for M expressions."""
#     cleaned = url.strip().strip('"').strip("'")
#     return f'"{cleaned}"'


# def _build_sharepoint_m(
#     site_url: str,
#     filename: str,
#     folder_path: str,
#     delimiter: str,
#     encoding: int,
#     transform_step: str,
#     final_step: str,
#     is_qvd: bool = False,
# ) -> str:
#     """Build a SharePoint.Contents() M expression for CSV files — navigates directly to the library."""
#     qvd_comment = "    // QVD converted to CSV — SharePoint.Contents() reads the CSV version\n" if is_qvd else ""

#     # Extract subfolder name from folder_path if files are in a subfolder of Shared Documents.
#     # e.g. "https://site.sharepoint.com/Shared Documents/MyFolder/" → subfolder = "MyFolder"
#     # If files are directly in Shared Documents root, subfolder = ""
#     folder_filter = folder_path.rstrip("/")
#     if "/Shared Documents/" in folder_filter:
#         subfolder = folder_filter.split("/Shared Documents/", 1)[1].strip("/")
#     else:
#         subfolder = ""

#     if subfolder:
#         nav_steps = (
#             f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#             f"    Folder = Documents{{[Name=\"{subfolder}\"]}}[Content],\n"
#             f"    FileRow = Table.SelectRows(Folder, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#         )
#     else:
#         nav_steps = (
#             f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#             f"    FileRow = Table.SelectRows(Documents, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#         )

#     m = (
#         f"let\n"
#         f"{qvd_comment}"
#         f"    Source = SharePoint.Contents(\n"
#         f"        \"{site_url}\",\n"
#         f"        [ApiVersion = 15]\n"
#         f"    ),\n"
#         f"{nav_steps}"
#         f"    FileBinary = if Table.RowCount(FileRow) = 0 then error \"{filename} not found in SharePoint\" else FileRow{{0}}[Content],\n"
#         f"    CsvData = Csv.Document(\n"
#         f"        FileBinary,\n"
#         f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#         f"    ),\n"
#         f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
#         f"{transform_step}\n"
#         f"in\n"
#         f"    {final_step}"
#     )
#     return m


# # ─────────────────────────────────────────────────────────────────────────────
# # MAIN CONVERTER
# # ─────────────────────────────────────────────────────────────────────────────

# class MQueryConverter:

#     # ============================================================
#     # PUBLIC
#     # ============================================================

#     def convert_all(
#         self,
#         tables: List[Dict[str, Any]],
#         base_path: str = "[DataSourcePath]",
#         connection_string: Optional[str] = None,
#     ) -> List[Dict[str, Any]]:

#         results = []

#         for table in tables:
#             m_expr, notes = self._dispatch(table, base_path, connection_string)
#             results.append({
#                 "name":         table["name"],
#                 "source_type":  table["source_type"],
#                 "m_expression": m_expr,
#                 "fields":       table["fields"],
#                 "notes":        notes,
#                 "source_path":  table.get("source_path", ""),
#                 "options":      table.get("options", {}),
#             })

#         logger.info("[MQueryConverter] Converted %d table(s)", len(results))
#         return results

#     def convert_one(
#         self,
#         table: Dict[str, Any],
#         base_path: str = "[DataSourcePath]",
#         connection_string: Optional[str] = None,
#         all_table_names: Optional[set] = None,
#     ) -> str:
#         m_expr, _ = self._dispatch(table, base_path, connection_string)
#         return m_expr

#     # ============================================================
#     # DISPATCH
#     # ============================================================

#     def _dispatch(self, table, base_path, connection_string):
#         source_type = table.get("source_type", "")
#         dispatch = {
#             "inline":   self._m_inline,
#             "csv":      self._m_csv,
#             "excel":    self._m_excel,
#             "json":     self._m_json,
#             "xml":      self._m_xml,
#             "parquet":  self._m_parquet,
#             "qvd":      self._m_qvd,
#             "resident": self._m_resident,
#             "sql":      self._m_sql,
#         }
#         handler = dispatch.get(source_type, self._m_placeholder)
#         try:
#             return handler(table, base_path, connection_string)
#         except Exception as exc:
#             logger.warning("[MQuery] Error converting '%s': %s", table.get("name"), exc)
#             return self._m_placeholder(table, base_path, connection_string), f"Conversion error: {exc}"

#     # ============================================================
#     # SHARED TYPE TRANSFORM STEP
#     # ============================================================

#     def _apply_types_as_is(self, fields: List[Dict], previous_step: str):
#         """
#         Like _apply_types but uses the SOURCE column name (expression, not alias).
#         Skips computed fields (APPLYMAP, IF, expressions with parentheses).
#         Used for SharePoint sources so columns match what's actually in the file.
#         """
#         pairs = []
#         for f in fields:
#             expr = f.get("expression", "")
#             # Skip computed expressions — anything with ( or known functions
#             if not expr or expr == "*":
#                 continue
#             if "(" in expr or expr.upper().startswith("APPLYMAP") or expr.upper().startswith("IF"):
#                 continue
#             # Extract plain column name from expression (strip brackets)
#             if expr.startswith("[") and expr.endswith("]"):
#                 col_name = expr[1:-1]
#             elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
#                 col_name = expr
#             else:
#                 continue  # skip anything else that's not a plain column name
#             # Always use type text for SharePoint sources — avoids type conversion
#             # failures on empty/null values (e.g. VT_BSTR → VT_I8 mismatch).
#             # Power BI can cast to specific types in measures/calculated columns.
#             pairs.append(f'{{"{col_name}", type text}}')

#         if not pairs:
#             return "", previous_step

#         pairs_str = ",\n        ".join(pairs)
#         transform = (
#             f",\n"
#             f"    TypedTable = Table.TransformColumnTypes(\n"
#             f"        {previous_step},\n"
#             f"        {{\n        {pairs_str}\n        }}\n"
#             f"    )"
#         )
#         return transform, "TypedTable"

#     def _apply_types(self, fields: List[Dict], previous_step: str):
#         """
#         Returns (transform_step_str, final_step_name).
#         Uses 'type' prefix form — correct for TransformColumnTypes.
#         Returns ("", previous_step) if no typed fields.

#         KEY FIX: Qlik-qualified field names like 'Table_Name.FieldName' are stripped
#         to just 'FieldName' because the CSV column has only the plain name.
#         Composite key fields (containing '-') are always typed as text.
#         """
#         typed = [f for f in fields if f.get("name") not in ("*", "")]
#         if not typed:
#             return "", previous_step

#         pairs = []
#         for f in typed:
#             raw_name = f.get("alias") or f["name"]
#             # Strip Qlik table-qualified prefix → use the plain CSV column name
#             col_name = _strip_qlik_qualifier(raw_name)
#             # Get M type (composite keys and qualified names forced to text)
#             m_type = _m_type(f.get("type", "string"), raw_name)
#             pairs.append(f'{{"{col_name}", {m_type}}}')

#         pairs_str = ",\n        ".join(pairs)
#         transform = (
#             f",\n"
#             f"    TypedTable = Table.TransformColumnTypes(\n"
#             f"        {previous_step},\n"
#             f"        {{\n        {pairs_str}\n        }}\n"
#             f"    )"
#         )
#         return transform, "TypedTable"

#     # ============================================================
#     # INLINE
#     # ============================================================

#     def _m_inline(self, table, base_path, _cs):
#         opts   = table.get("options", {})
#         fields = table["fields"]

#         headers = opts.get(
#             "inline_headers",
#             [f["name"] for f in fields if f["name"] != "*"]
#         )
#         rows = opts.get("inline_rows_all") or opts.get("inline_sample", [])

#         # Column type defs — NO "type" prefix inside #table() signature
#         type_defs = ", ".join(
#             f"{_sanitize_col_name(_strip_qlik_qualifier(h))} = "
#             f"{_m_type_for_table(next((f['type'] for f in fields if f['name'] == h), 'string'), h)}"
#             for h in headers
#         )

#         # Build data rows — strip Qlik single-quotes, escape double-quotes
#         row_strs = []
#         for row in rows:
#             vals = []
#             for h in headers:
#                 v = str(row.get(h, ""))
#                 v = v.strip("'")            # strip Qlik single quotes
#                 v = v.replace('"', '""')    # escape embedded double quotes
#                 v = v.replace("\n", " ")    # collapse embedded newlines
#                 v = v.replace("\r", "")     # remove carriage returns
#                 v = " ".join(v.split())     # collapse multiple spaces
#                 vals.append(f'"{v}"')
#             # Entire row MUST be on one line
#             row_strs.append("{" + ", ".join(vals) + "}")
#         rows_m = (
#             "{\n        " + ",\n        ".join(row_strs) + "\n    }"
#             if row_strs else "{}"
#         )
#         # Safety: ensure no row spans multiple lines
#         import re
#         rows_m_safe = re.sub(r',\s*\n\s*"', ', "', rows_m)
#         rows_m = rows_m_safe
#         m = (
#             f"let\n"
#             f"    Source = #table(\n"
#             f"        type table [{type_defs}],\n"
#             f"        {rows_m}\n"
#             f"    )\n"
#             f"in\n"
#             f"    Source"
#         )
#         return m, f"Inline table with {len(rows)} row(s). Data embedded directly."

#     # ============================================================
#     # CSV
#     # ============================================================

#     def _m_csv(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]
#         opts   = table.get("options", {})

#         delimiter = opts.get("delimiter", ",")
#         encoding  = 65001
#         enc_str   = opts.get("encoding", "")
#         if enc_str:
#             enc_map = {"UTF-8": 65001, "UTF8": 65001, "UTF-16": 1200, "UTF16": 1200}
#             encoding = enc_map.get(enc_str.upper().replace("-", ""), encoding)

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         # ── SharePoint: use SharePoint.Contents() — columns taken as-is from file ──
#         if _is_sharepoint_url(base_path):
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_sharepoint_m(
#                 site_url=site_url,
#                 filename=filename,
#                 folder_path=folder_path,
#                 delimiter=delimiter,
#                 encoding=encoding,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#             )
#         else:
#             # Local/on-prem: use File.Contents()
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{path}"'
#             m = (
#                 f"let\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Csv.Document(\n"
#                 f"        File.Contents(FilePath),\n"
#                 f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
#                 f"    ),\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"CSV source: {path}"

#     # ============================================================
#     # SHAREPOINT HELPERS
#     # ============================================================

#     def _get_sharepoint_parts(self, base_path: str, source_path: str, opts: dict):
#         """
#         Extract SharePoint site URL, folder path, and filename from inputs.
#         Returns (site_url, folder_path, filename) all as plain strings.

#         Handles two input formats:
#         1. Full path:  "https://site.sharepoint.com/Shared Documents/MyFolder/"
#            → site_url  = "https://site.sharepoint.com"
#            → folder_path = "https://site.sharepoint.com/Shared Documents/MyFolder/"
#         2. Site root:  "https://site.sharepoint.com"
#            → site_url  = "https://site.sharepoint.com"
#            → folder_path = "https://site.sharepoint.com/Shared Documents/"
#         """
#         raw = base_path.strip().strip('"').strip("'").rstrip("/")

#         # Detect if user provided a full path (contains /Shared Documents or /sites/)
#         if "/Shared Documents" in raw or ("/sites/" in raw and "/" in raw.split("/sites/", 1)[-1]):
#             # Extract site_url as everything up to /Shared Documents or /sites/SiteName
#             if "/Shared Documents" in raw:
#                 site_url = raw.split("/Shared Documents")[0].rstrip("/")
#                 folder_path = raw + "/"
#             else:
#                 # /sites/SiteName/LibraryName/...
#                 parts = raw.split("/")
#                 # site_url = scheme + host + /sites/SiteName
#                 site_url = "/".join(parts[:5])
#                 folder_path = raw + "/"
#         else:
#             # Just a site root — build folder path from source_path
#             site_url = raw
#             sp_subfolder = opts.get("sp_subfolder", "")
#             if sp_subfolder:
#                 folder_path = f"{site_url}/{sp_subfolder.strip('/')}/"
#             elif "/" in source_path:
#                 folder_part = source_path.rsplit("/", 1)[0]
#                 folder_path = f"{site_url}/Shared Documents/{folder_part}/"
#             else:
#                 folder_path = f"{site_url}/Shared Documents/"

#         filename = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
#         if not filename:
#             table_name = opts.get("table_name", "Table")
#             filename = f"{table_name}.csv"

#         return site_url, folder_path, filename

#     # ============================================================
#     # EXCEL
#     # ============================================================

#     def _m_excel(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         sheet  = table.get("options", {}).get("sheet", "Sheet1")
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         if _is_sharepoint_url(base_path):
#             opts = table.get("options", {})
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
#             excel_subfolder = folder_path.rstrip("/")
#             if "/Shared Documents/" in excel_subfolder:
#                 excel_subfolder = excel_subfolder.split("/Shared Documents/", 1)[1].strip("/")
#             else:
#                 excel_subfolder = ""

#             if excel_subfolder:
#                 excel_nav = (
#                     f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#                     f"    Folder = Documents{{[Name=\"{excel_subfolder}\"]}}[Content],\n"
#                     f"    FileRow = Table.SelectRows(Folder, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#                 )
#             else:
#                 excel_nav = (
#                     f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
#                     f"    FileRow = Table.SelectRows(Documents, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
#                 )

#             m = (
#                 f"let\n"
#                 f"    Source = SharePoint.Contents(\n"
#                 f"        \"{site_url}\",\n"
#                 f"        [ApiVersion = 15]\n"
#                 f"    ),\n"
#                 f"{excel_nav}"
#                 f"    FileBinary = if Table.RowCount(FileRow) = 0 then error \"{filename} not found in SharePoint\" else FileRow{{0}}[Content],\n"
#                 f"    ExcelData = Excel.Workbook(FileBinary, null, true),\n"
#                 f"    SheetData = ExcelData{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
#                 f"{self._apply_types_as_is(fields, 'PromotedHeaders')[0]}\n"
#                 f"in\n"
#                 f"    {self._apply_types_as_is(fields, 'PromotedHeaders')[1]}"
#             )
#         else:
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{path}"'
#             m = (
#                 f"let\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Excel.Workbook(File.Contents(FilePath), null, true),\n"
#                 f"    SheetData = Source{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"Excel source: {path}, sheet: {sheet}"

#     # ============================================================
#     # JSON
#     # ============================================================

#     def _m_json(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         expand_cols = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
#         col_list    = ", ".join(f'"{c}"' for c in expand_cols)

#         transform, final = self._apply_types(fields, "Expanded")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Json.Document(File.Contents(FilePath)),\n"
#             f"    ToTable = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n"
#             f"    Expanded = Table.ExpandRecordColumn(ToTable, \"Column1\",\n"
#             f"        {{{col_list}}},\n"
#             f"        {{{col_list}}}\n"
#             f"    )"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"JSON source: {path}. Assumes array of records at root level."

#     # ============================================================
#     # XML
#     # ============================================================

#     def _m_xml(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Xml.Tables(File.Contents(FilePath))"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"XML source: {path}. Review nested table expansion manually."

#     # ============================================================
#     # PARQUET
#     # ============================================================

#     def _m_parquet(self, table, base_path, _cs):
#         path   = _normalize_path(table.get("source_path", ""))
#         fields = table["fields"]

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    FilePath = {base_path} & \"/{path}\",\n"
#             f"    Source = Parquet.Document(File.Contents(FilePath))"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"Parquet source: {path}."

#     # ============================================================
#     # QVD (CSV fallback)
#     # ============================================================

#     def _m_qvd(self, table, base_path, _cs):
#         path     = _normalize_path(table.get("source_path", ""))
#         csv_path = re.sub(r"\.qvd$", ".csv", path, flags=re.IGNORECASE)
#         fields   = table["fields"]

#         transform, final = self._apply_types(fields, "PromotedHeaders")

#         if _is_sharepoint_url(base_path):
#             opts = table.get("options", {})
#             opts["table_name"] = table.get("name", "Table")
#             site_url, folder_path, filename = self._get_sharepoint_parts(base_path, csv_path, opts)
#             sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
#             m = _build_sharepoint_m(
#                 site_url=site_url,
#                 filename=filename,
#                 folder_path=folder_path,
#                 delimiter=",",
#                 encoding=65001,
#                 transform_step=sp_transform,
#                 final_step=sp_final,
#                 is_qvd=True,
#             )
#         else:
#             if base_path.strip().startswith("["):
#                 path_expr = f"{base_path} & \"/{csv_path}\""
#             else:
#                 clean_bp = base_path.strip().strip('"').strip("'")
#                 path_expr = f'"{clean_bp}/{csv_path}"'
#             m = (
#                 f"let\n"
#                 f"    // QVD not supported natively — expects pre-converted CSV\n"
#                 f"    FilePath = {path_expr},\n"
#                 f"    Source = Csv.Document(\n"
#                 f"        File.Contents(FilePath),\n"
#                 f"        [Delimiter=\",\", Encoding=65001]\n"
#                 f"    ),\n"
#                 f"    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
#                 f"{transform}\n"
#                 f"in\n"
#                 f"    {final}"
#             )
#         return m, f"QVD fallback (pre-convert to CSV): {path}"


#     # ============================================================
#     # RESIDENT
#     # ============================================================

#     def _m_resident(self, table, base_path, _cs):
#         source_table = table.get("source_path", "UnknownTable")
#         fields       = table["fields"]

#         selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]

#         if selected:
#             select_step = (
#                 f",\n    Selected = Table.SelectColumns({source_table},\n"
#                 f"        {{{', '.join(chr(34) + c + chr(34) for c in selected)}}}\n"
#                 f"    )"
#             )
#             intermediate = "Selected"
#         else:
#             select_step = ""
#             intermediate = source_table

#         transform, final = self._apply_types(fields, intermediate)

#         m = (
#             f"let\n"
#             f"    // References another Power BI query: {source_table}\n"
#             f"    {source_table} = {source_table}"
#             f"{select_step}"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"RESIDENT load from '{source_table}'."

#     # ============================================================
#     # SQL
#     # ============================================================

#     def _m_sql(self, table, base_path, connection_string):
#         source_table = table.get("source_path", "dbo.UnknownTable")
#         fields       = table["fields"]
#         conn         = connection_string or "[OdbcConnectionString]"

#         selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
#         col_list = ", ".join(f"[{c}]" for c in selected) if selected else "*"

#         transform, final = self._apply_types(fields, "Source")

#         m = (
#             f"let\n"
#             f"    ConnectionString = {conn},\n"
#             f"    Source = Odbc.Query(\n"
#             f"        ConnectionString,\n"
#             f"        \"SELECT {col_list} FROM {source_table}\"\n"
#             f"    )"
#             f"{transform}\n"
#             f"in\n"
#             f"    {final}"
#         )
#         return m, f"SQL/ODBC source: {source_table}."

#     # ============================================================
#     # PLACEHOLDER
#     # ============================================================

#     def _m_placeholder(self, table, base_path, _cs):
#         fields = table["fields"]

#         type_defs = ", ".join(
#             f"{_sanitize_col_name(f.get('alias') or f['name'])} = {_m_type(f.get('type', 'string'))}"
#             for f in fields if f["name"] != "*"
#         ) or "Column1 = type text"

#         m = (
#             f"let\n"
#             f"    // Placeholder — source type '{table.get('source_type', 'unknown')}' not auto-converted.\n"
#             f"    // Original load: {table.get('source_path', 'N/A')}\n"
#             f"    Source = #table(\n"
#             f"        type table [{type_defs}],\n"
#             f"        {{}}\n"
#             f"    )\n"
#             f"in\n"
#             f"    Source"
#         )
#         return m, f"Source type '{table.get('source_type')}' requires manual configuration."


# # ─────────────────────────────────────────────────────────────────────────────
# # CONVENIENCE
# # ─────────────────────────────────────────────────────────────────────────────

# def convert_to_mquery(
#     tables: List[Dict[str, Any]],
#     base_path: str = "[DataSourcePath]",
#     connection_string: Optional[str] = None,
# ) -> List[Dict[str, Any]]:
#     """Convert parsed Qlik tables to M expressions."""
#     return MQueryConverter().convert_all(tables, base_path, connection_string)






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

# ─────────────────────────────────────────────────────────────────────────────
# SMART COLUMN TYPE INFERENCE
# ─────────────────────────────────────────────────────────────────────────────
# When Qlik type metadata is missing/unknown, infer Power BI compatible types
# from column name patterns.
#
# CRITICAL RULE FOR ID / KEY / NO COLUMNS:
#   Column name alone is NOT sufficient to assign Int64.Type for ID-like
#   columns (patient_id, doctor_id, ward_no, etc.).
#
#   Reason: ID columns frequently contain mixed alphanumeric values such as
#   "P001", "DOC-123", "D_456", "W-2A" — assigning Int64.Type to these causes
#   a type-conversion error at Power BI refresh time ("We couldn't convert
#   'P001' to Number").
#
#   Safe rule:
#     • Name ends in _id/_key/_no/_num → type text   (safe default)
#     • Qlik explicitly reports type = "integer"/"int" → Int64.Type   (trust it)
#
#   Power BI relationship rule:
#     Both sides of a relationship must share the SAME datatype.
#     By consistently typing all ID columns as text (unless explicitly integer),
#     we guarantee the same type on both sides → relationships never break.
#
# Rule priority (implemented in _m_type):
#   1. Composite key (contains '-')            → type text  (always)
#   2. Explicit Qlik type = integer/int        → Int64.Type
#   3. Explicit Qlik type = date/datetime/etc  → respective type
#   4. Explicit Qlik type = number/double/etc  → type number
#   5. Name-based inference for safe patterns  → date/number/logical only
#   6. Fallback                                → type text
#
# NOTE: _NAME_TO_M_TYPE intentionally does NOT include ID/key/no → Int64.
# Those are handled exclusively via explicit Qlik type metadata (rule 2).
# ─────────────────────────────────────────────────────────────────────────────

# Column name suffixes/patterns → M type
# IMPORTANT: No ID/key/no/num patterns here — see CRITICAL RULE above.
_NAME_TO_M_TYPE: List[tuple] = [
    # Date/time columns — safe to infer from name (no ambiguity)
    (re.compile(r"_date$|^date$|_dt$|_timestamp$", re.I),                       "type date"),
    (re.compile(r"_datetime$|_created_at$|_updated_at$|_modified_at$", re.I),   "type datetime"),
    # Pure numeric measure columns — safe to infer from name
    # (these are never alphanumeric in practice)
    (re.compile(r"_cost$|_fee$|_bill$|_price$|_rate$|_salary$", re.I),         "type number"),
    (re.compile(r"_covered$|_expense$|_revenue$|_income$|_tax$|_discount$", re.I), "type number"),
    (re.compile(r"_amount$", re.I),                                             "type number"),
    (re.compile(r"_days$|_years$|_count$|_qty$|_quantity$", re.I),              "Int64.Type"),
    (re.compile(r"_pct$|_percent$|_ratio$|_score$|_weight$", re.I),             "type number"),
    # Boolean-like columns
    (re.compile(r"^is_|^has_|^can_|^was_|_flag$|_bool$", re.I),                "type logical"),
]


def _infer_type_from_name(col_name: str) -> str:
    """
    Infer Power BI M type from column name patterns.

    DOES NOT infer Int64 for _id/_key/_no — those default to type text
    because they commonly contain mixed alphanumeric values (P001, DOC-123).
    Only explicit Qlik type = 'integer'/'int' triggers Int64.Type for such cols.

    Returns M type string WITH 'type' prefix (e.g. 'type date', 'type number').
    Falls back to 'type text' for unrecognised / ambiguous names.
    """
    clean = col_name.strip().lower()
    # Strip Qlik table-qualified prefix for matching
    if "." in clean and not clean.startswith("#"):
        clean = clean.split(".", 1)[-1]
    # Composite keys always text
    if "-" in clean:
        return "type text"
    for pattern, m_type in _NAME_TO_M_TYPE:
        if pattern.search(clean):
            return m_type
    return "type text"


def _m_type(qlik_type: str, col_name: str = "") -> str:
    """
    Resolve the correct Power BI M type for a column.

    Priority order:
      1. Composite key (col_name contains '-')   → type text
      2. Explicit Qlik integer/int               → Int64.Type
      3. Any other explicit non-text Qlik type   → mapped M type
      4. Name-based safe inference               → date/number/logical only
      5. Fallback                                → type text

    ID / key / no columns:
      When qlik_type is 'string'/'unknown', these default to type text even
      if the name ends in _id/_key/_no, because their values are often
      alphanumeric (P001, DOC-123).  Assign Int64 only when Qlik explicitly
      reports integer — that means the data is guaranteed to be pure numeric.
    """
    if "-" in col_name:
        return "type text"

    # Resolve plain name (strip Qlik table-qualified prefix)
    plain_name = (
        col_name.split(".", 1)[-1]
        if ("." in col_name and not col_name.startswith("#"))
        else col_name
    )

    qlik_lower = str(qlik_type).lower().strip()

    # Explicit Qlik type — trust it completely
    if qlik_lower not in ("string", "text", "unknown", "mixed", "wildcard", ""):
        return _QLIK_TO_M_TYPE.get(qlik_lower, _DEFAULT_M_TYPE)

    # No explicit type info → safe name-based inference only
    return _infer_type_from_name(plain_name)


def _m_type_for_table(qlik_type: str, col_name: str = "") -> str:
    """
    M type WITHOUT 'type' prefix — for #table() column signatures.

    Uses 'text' for all columns to avoid VT_BSTR → VT_I8 failures on
    empty values in inline tables. Inline tables are small reference
    tables — type safety matters less than load reliability here.
    """
    return "text"

def _normalize_path(path: str) -> str:
    path = re.sub(r"^lib://[^/]*/", "", path)
    path = re.sub(r"^lib://", "", path)
    return path


def _sanitize_col_name(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        return name
    return f'#"{name}"'


def _strip_qlik_qualifier(col_name: str) -> str:
    """
    Strip Qlik table-qualified prefix from column name.

    'Dealer_Master.City_GeoInfo'  →  'City_GeoInfo'
    'Model_Master.ModelID'        →  'ModelID'
    'DealerID-ServiceID'          →  'DealerID-ServiceID'  (composite key, keep as-is)
    '#"Something"'                →  '#"Something"'        (already escaped)

    Critical: the actual CSV column is just 'City_GeoInfo', not the
    Qlik-qualified 'Dealer_Master.City_GeoInfo'. Using the qualified name
    as a BIM column causes column-not-found errors at query time.
    """
    if not col_name or col_name.startswith("#"):
        return col_name
    # Only strip if dot present AND no hyphen (composite keys keep their name)
    if "." in col_name and "-" not in col_name:
        return col_name.split(".", 1)[-1]
    return col_name


def validate_sharepoint_url_strict(url: str) -> tuple[bool, str]:
    """
    Strict SharePoint URL validation
    Returns: (is_valid: bool, error_message: str)
    
    Validates:
    - Must start with https://
    - Must contain .sharepoint.com domain
    - Must have a non-empty company name
    - Rejects OneDrive and other URLs
    
    Error cases:
    - Missing https://
    - Missing .com
    - Missing company name
    - Missing .sharepoint component
    """
    if not url or not isinstance(url, str) or not url.strip():
        return False, "URL cannot be empty"
    
    trimmed = url.strip()
    trimmed_lower = trimmed.lower()
    
    # Error 1: Must start with https://
    if not trimmed_lower.startswith("https://"):
        if trimmed_lower.startswith("http://"):
            return False, "❌ Must use HTTPS (not HTTP). Use: https://"
        return False, "❌ Must start with https://"
    
    # Error 2: Must contain .sharepoint.com
    if ".sharepoint.com" not in trimmed_lower:
        # Check specifics for better error message
        if ".com" not in trimmed_lower:
            return False, "❌ Missing .com - Should end with .sharepoint.com"
        if "sharepoint" not in trimmed_lower:
            return False, "❌ Missing 'sharepoint' - Should be: https://COMPANYNAME.sharepoint.com"
        return False, "❌ Invalid format. Should be: https://COMPANYNAME.sharepoint.com"
    
    # Error 3: Extract company name and check it's not empty
    import re
    sharepoint_match = re.match(r'https://([^.]+)\.sharepoint\.com', trimmed, re.IGNORECASE)
    if not sharepoint_match or not sharepoint_match.group(1):
        return False, "❌ Missing company name - Should be: https://COMPANYNAME.sharepoint.com"
    
    company_name = sharepoint_match.group(1)
    
    # Error 4: Company name must contain valid characters
    if not company_name or len(company_name) == 0 or not re.search(r'[a-z0-9]', company_name, re.IGNORECASE):
        return False, "❌ Invalid company name - Should be: https://COMPANYNAME.sharepoint.com"
    
    # ✅ Valid
    return True, ""


def _is_sharepoint_url(url: str) -> bool:
    """Check if a URL is a SharePoint URL (includes OneDrive for Business)."""
    cleaned = url.strip().strip('"').strip("'").lower()
    return "sharepoint.com" in cleaned or "-my.sharepoint.com" in cleaned


def _is_onedrive_personal_url(url: str) -> bool:
    """Check if a URL is a personal OneDrive URL."""
    cleaned = url.strip().strip('"').strip("'").lower()
    return "onedrive.live.com" in cleaned or "1drv.ms" in cleaned


def _is_s3_url(url: str) -> bool:
    """Check if a URL is an AWS S3 URL."""
    cleaned = url.strip().strip('"').strip("'").lower()
    return "s3.amazonaws.com" in cleaned or cleaned.startswith("s3://") or ".s3." in cleaned


def _is_azure_blob_url(url: str) -> bool:
    """Check if a URL is an Azure Blob Storage URL."""
    cleaned = url.strip().strip('"').strip("'").lower()
    return "blob.core.windows.net" in cleaned or "dfs.core.windows.net" in cleaned


def _is_web_url(url: str) -> bool:
    """Check if a URL is a generic web URL (non-cloud-storage)."""
    cleaned = url.strip().strip('"').strip("'").lower()
    return (cleaned.startswith("http://") or cleaned.startswith("https://")) and not (
        _is_sharepoint_url(url) or _is_s3_url(url) or _is_azure_blob_url(url)
    )


def _quote_url(url: str) -> str:
    """Ensure a URL is properly quoted for M expressions."""
    cleaned = url.strip().strip('"').strip("'")
    return f'"{cleaned}"'


def _build_sharepoint_m(
    site_url: str,
    filename: str,
    folder_path: str,
    delimiter: str,
    encoding: int,
    transform_step: str,
    final_step: str,
    is_qvd: bool = False,
) -> str:
    """
    Build a fully DYNAMIC SharePoint.Files() M expression.

    HOW IT WORKS:
    - site_url  : the SharePoint root passed by the user at runtime
                  (e.g. "https://sorimtechnologies.sharepoint.com")
    - filename  : CSV file name extracted from the Qlik load script
    - folder_path: expected folder URL — used as a hint but NOT hardcoded
                  in the M query; the M query resolves it dynamically.

    WHY SharePoint.Files() NOT SharePoint.Contents():
    - SharePoint.Contents() → Power BI asks for "Web" credential  (WRONG ❌)
    - SharePoint.Files()    → Power BI asks for "SharePoint" credential (CORRECT ✅)

    DYNAMIC SEARCH STRATEGY (no hardcoded paths):
    1. SharePoint.Files(site_url) returns ALL files under the site.
    2. First try: match [Name] exactly (case-insensitive).
    3. The M expression is fully parameterised — only site_url and filename
       are embedded; the folder structure is discovered at runtime.
    4. If 0 rows → clear error message shows what was searched.
    """
    qvd_comment = "    // QVD converted to CSV — SharePoint.Files() reads the CSV version\n" if is_qvd else ""

    m = (
        f"let\n"
        f"{qvd_comment}"
        f"    // ── Dynamic SharePoint source ─────────────────────────────────────\n"
        f"    // site_url comes from the base_path the user configured.\n"
        f"    // SharePoint.Files() scans ALL files under the site automatically.\n"
        f"    // No folder paths are hardcoded — works regardless of subfolder structure.\n"
        f"    SiteUrl = \"{site_url}\",\n"
        f"    FileName = \"{filename}\",\n"
        f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
        f"    // Step 1: find rows where filename matches (case-insensitive)\n"
        f"    FilteredByName = Table.SelectRows(\n"
        f"        Source,\n"
        f"        each Text.Lower([Name]) = Text.Lower(FileName)\n"
        f"    ),\n"
        f"    // Step 2: if multiple files with same name exist, pick the one\n"
        f"    // whose [Folder Path] contains \"Shared Documents\" (standard library)\n"
        f"    FilteredByLib = if Table.RowCount(FilteredByName) > 1\n"
        f"        then Table.SelectRows(FilteredByName, each Text.Contains([Folder Path], \"Shared Documents\"))\n"
        f"        else FilteredByName,\n"
        f"    // Step 3: pick first match\n"
        f"    FileBinary = if Table.RowCount(FilteredByLib) = 0\n"
        f"        then error \"File \" & FileName & \" not found under \" & SiteUrl &\n"
        f"             \". Ensure the file exists in SharePoint and credentials are set.\"\n"
        f"        else FilteredByLib{{0}}[Content],\n"
        f"    CsvData = Csv.Document(\n"
        f"        FileBinary,\n"
        f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
        f"    ),\n"
        f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
        f"{transform_step}\n"
        f"in\n"
        f"    {final_step}"
    )
    return m


def _build_s3_m(
    bucket_url: str,
    filename: str,
    delimiter: str,
    encoding: int,
    transform_step: str,
    final_step: str,
) -> str:
    """Build M expression for AWS S3 via Web.Contents() (public/pre-signed URL)."""
    clean_url = bucket_url.strip().strip('"').strip("'").rstrip("/")
    file_url = f"{clean_url}/{filename}"
    m = (
        f"let\n"
        f"    // AWS S3: Web.Contents() — use pre-signed URL or make bucket public\n"
        f"    // For private S3, use On-Premises Data Gateway with ODBC/JDBC connector\n"
        f"    Source = Web.Contents(\"{file_url}\"),\n"
        f"    CsvData = Csv.Document(\n"
        f"        Source,\n"
        f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
        f"    ),\n"
        f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
        f"{transform_step}\n"
        f"in\n"
        f"    {final_step}"
    )
    return m


def _build_azure_blob_m(
    container_url: str,
    filename: str,
    delimiter: str,
    encoding: int,
    transform_step: str,
    final_step: str,
) -> str:
    """Build M expression for Azure Blob Storage via AzureStorage.Blobs()."""
    clean_url = container_url.strip().strip('"').strip("'").rstrip("/")
    m = (
        f"let\n"
        f"    // Azure Blob Storage — authenticate with Account Key or SAS token\n"
        f"    Source = AzureStorage.Blobs(\"{clean_url}\"),\n"
        f"    FileRow = Table.SelectRows(Source, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
        f"    FileBinary = if Table.RowCount(FileRow) = 0 then error \"{filename} not found in Azure Blob\" else FileRow{{0}}[Content],\n"
        f"    CsvData = Csv.Document(\n"
        f"        FileBinary,\n"
        f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
        f"    ),\n"
        f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
        f"{transform_step}\n"
        f"in\n"
        f"    {final_step}"
    )
    return m


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
        source_type = table.get("source_type", "")
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
        handler = dispatch.get(source_type, self._m_placeholder)
        try:
            return handler(table, base_path, connection_string)
        except Exception as exc:
            logger.warning("[MQuery] Error converting '%s': %s", table.get("name"), exc)
            return self._m_placeholder(table, base_path, connection_string), f"Conversion error: {exc}"

    # ============================================================
    # SHARED TYPE TRANSFORM STEP
    # ============================================================

    def _apply_types_as_is(self, fields: List[Dict], previous_step: str):
        """
        Like _apply_types but uses the SOURCE column name (expression, not alias).
        Skips computed fields (APPLYMAP, IF, expressions with parentheses).
        Used for SharePoint sources so columns match what's actually in the file.

        KEY FIX: Uses smart type inference (_m_type) instead of hardcoding
        everything as 'type text'. This ensures relationship key columns
        (e.g. patient_id, doctor_id, disease_id) get the correct Power BI
        datatype so cloud semantic model relationships (openSemanticModel)
        don't break due to type mismatches between tables.

        Safe strategy:
          - ID/key/date/numeric columns → inferred correct type
          - All other string columns → type text (safe for nulls/empty values)
        """
        pairs = []
        for f in fields:
            expr = f.get("expression", "")
            # Skip computed expressions — anything with ( or known functions
            if not expr or expr == "*":
                continue
            if "(" in expr or expr.upper().startswith("APPLYMAP") or expr.upper().startswith("IF"):
                continue
            # Extract plain column name from expression (strip brackets)
            if expr.startswith("[") and expr.endswith("]"):
                col_name = expr[1:-1]
            elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
                col_name = expr
            else:
                continue  # skip anything else that's not a plain column name

            # Use smart type inference: explicit Qlik type first, then name-based
            qlik_type = f.get("type", "string")
            m_type = _m_type(qlik_type, col_name)
            pairs.append(f'{{"{col_name}", {m_type}}}')

        if not pairs:
            return "", previous_step

        pairs_str = ",\n        ".join(pairs)
        transform = (
            f",\n"
            f"    TypedTable = Table.TransformColumnTypes(\n"
            f"        {previous_step},\n"
            f"        {{\n        {pairs_str}\n        }}\n"
            f"    )"
        )
        return transform, "TypedTable"

    def _apply_types(self, fields: List[Dict], previous_step: str):
        """
        Returns (transform_step_str, final_step_name).
        Uses 'type' prefix form — correct for TransformColumnTypes.
        Returns ("", previous_step) if no typed fields.

        KEY FIX: Uses smart type inference (_m_type) which combines:
          1. Explicit Qlik type metadata (integer, date, etc.)
          2. Column name pattern matching (_infer_type_from_name) for
             columns that Qlik reports as 'string'/'unknown'
          3. Composite key fields (containing '-') → always text
          4. Qlik-qualified names (Table.Column) → strip prefix then infer

        This fixes Power BI cloud relationship breakage caused by type
        mismatches — e.g. patient_id must be Int64.Type in BOTH patients
        and admissions tables for the relationship to work in the
        openSemanticModel (cloud semantic model).
        """
        typed = [f for f in fields if f.get("name") not in ("*", "")]
        if not typed:
            return "", previous_step

        pairs = []
        for f in typed:
            raw_name = f.get("alias") or f["name"]
            # Strip Qlik table-qualified prefix → use the plain CSV column name
            col_name = _strip_qlik_qualifier(raw_name)
            # Get M type: explicit Qlik type takes priority, then name inference
            m_type = _m_type(f.get("type", "string"), raw_name)
            pairs.append(f'{{"{col_name}", {m_type}}}')

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
        rows = opts.get("inline_rows_all") or opts.get("inline_sample", [])

        # Column type defs — NO "type" prefix inside #table() signature
        type_defs = ", ".join(
            f"{_sanitize_col_name(_strip_qlik_qualifier(h))} = "
            f"{_m_type_for_table(next((f['type'] for f in fields if f['name'] == h), 'string'), h)}"
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

        # ── Universal source routing ──────────────────────────────────────────
        # Supports: SharePoint/OneDrive-for-Business, AWS S3, Azure Blob,
        #           generic Web URL, and local/on-prem File.Contents()
        filename_only = path.rsplit("/", 1)[-1] if "/" in path else path

        if _is_sharepoint_url(base_path):
            # SharePoint / OneDrive for Business
            # FIX: SharePoint.Files() triggers SharePoint credential (not Web credential)
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
            sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
            m = _build_sharepoint_m(
                site_url=site_url,
                filename=filename,
                folder_path=folder_path,
                delimiter=delimiter,
                encoding=encoding,
                transform_step=sp_transform,
                final_step=sp_final,
            )
        elif _is_s3_url(base_path):
            # AWS S3
            sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
            m = _build_s3_m(
                bucket_url=base_path,
                filename=filename_only,
                delimiter=delimiter,
                encoding=encoding,
                transform_step=sp_transform,
                final_step=sp_final,
            )
        elif _is_azure_blob_url(base_path):
            # Azure Blob Storage
            sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
            m = _build_azure_blob_m(
                container_url=base_path,
                filename=filename_only,
                delimiter=delimiter,
                encoding=encoding,
                transform_step=sp_transform,
                final_step=sp_final,
            )
        elif _is_web_url(base_path):
            # Generic HTTPS web source (public URL)
            clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
            file_url = f"{clean_bp}/{path}" if path else clean_bp
            m = (
                f"let\n"
                f"    Source = Web.Contents(\"{file_url}\"),\n"
                f"    CsvData = Csv.Document(\n"
                f"        Source,\n"
                f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
                f"    ),\n"
                f"    PromotedHeaders = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
                f"{transform}\n"
                f"in\n"
                f"    {final}"
            )
        else:
            # Local/on-prem: use File.Contents()
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{path}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{path}"'
            m = (
                f"let\n"
                f"    FilePath = {path_expr},\n"
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
    # SHAREPOINT HELPERS
    # ============================================================

    def _get_sharepoint_parts(self, base_path: str, source_path: str, opts: dict):
        """
        Extract (site_url, folder_path, filename) from the user-supplied base_path.

        DESIGN: The M query generated by _build_sharepoint_m is FULLY DYNAMIC —
        it calls SharePoint.Files(site_url) and searches by filename at runtime.
        No folder path is hardcoded in the M expression.

        This function only needs to:
          1. Extract a clean site_url (root of the SharePoint site) from base_path
          2. Extract the filename from source_path
          3. Return folder_path (kept for Excel compatibility but not used in CSV filter)

        Supported base_path formats:
          A) "https://sorimtechnologies.sharepoint.com"
             → site_url = "https://sorimtechnologies.sharepoint.com"
          B) "https://sorimtechnologies.sharepoint.com/Shared Documents"
             → site_url = "https://sorimtechnologies.sharepoint.com"
          C) "https://sorimtechnologies.sharepoint.com/Shared Documents/SubFolder"
             → site_url = "https://sorimtechnologies.sharepoint.com"
          D) "https://company.sharepoint.com/sites/TeamSite"
             → site_url = "https://company.sharepoint.com/sites/TeamSite"
          E) "https://company.sharepoint.com/sites/TeamSite/Shared Documents/Folder"
             → site_url = "https://company.sharepoint.com/sites/TeamSite"
        """
        raw = base_path.strip().strip('"').strip("'").rstrip("/")

        # ── Extract clean site_url ─────────────────────────────────────────────
        if "/Shared Documents" in raw:
            # Strip everything from /Shared Documents onward
            site_url = raw.split("/Shared Documents")[0].rstrip("/")
            folder_path = raw + "/"
        elif "/sites/" in raw:
            # Keep up to and including /sites/SiteName
            parts = raw.split("/")
            # https://host/sites/SiteName → parts[0]=https:, [1]='', [2]=host, [3]=sites, [4]=SiteName
            site_url = "/".join(parts[:5]) if len(parts) >= 5 else raw
            folder_path = f"{site_url}/Shared Documents/"
        else:
            # Bare root: "https://sorimtechnologies.sharepoint.com"
            site_url = raw
            folder_path = f"{site_url}/Shared Documents/"

        # Override folder_path if sp_subfolder explicitly set
        sp_subfolder = opts.get("sp_subfolder", "")
        if sp_subfolder:
            folder_path = f"{site_url}/Shared Documents/{sp_subfolder.strip('/')}/"
        elif "/" in source_path:
            sub = source_path.rsplit("/", 1)[0]
            folder_path = f"{site_url}/Shared Documents/{sub}/"

        # ── Extract filename ───────────────────────────────────────────────────
        filename = source_path.rsplit("/", 1)[-1] if "/" in source_path else source_path
        if not filename:
            table_name = opts.get("table_name", "Table")
            filename = f"{table_name}.csv"

        return site_url, folder_path, filename

    # ============================================================
    # EXCEL
    # ============================================================

    def _m_excel(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        sheet  = table.get("options", {}).get("sheet", "Sheet1")
        fields = table["fields"]

        transform, final = self._apply_types(fields, "PromotedHeaders")

        if _is_sharepoint_url(base_path):
            opts = table.get("options", {})
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
            excel_subfolder = folder_path.rstrip("/")
            if "/Shared Documents/" in excel_subfolder:
                excel_subfolder = excel_subfolder.split("/Shared Documents/", 1)[1].strip("/")
            else:
                excel_subfolder = ""

            if excel_subfolder:
                excel_nav = (
                    f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
                    f"    Folder = Documents{{[Name=\"{excel_subfolder}\"]}}[Content],\n"
                    f"    FileRow = Table.SelectRows(Folder, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
                )
            else:
                excel_nav = (
                    f"    Documents = Source{{[Name=\"Shared Documents\"]}}[Content],\n"
                    f"    FileRow = Table.SelectRows(Documents, each Text.Lower([Name]) = Text.Lower(\"{filename}\")),\n"
                )

            # DYNAMIC Excel from SharePoint — same search strategy as CSV
            sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
            m = (
                f"let\n"
                f"    // Dynamic SharePoint Excel — no hardcoded folder paths\n"
                f"    SiteUrl = \"{site_url}\",\n"
                f"    FileName = \"{filename}\",\n"
                f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
                f"    FilteredByName = Table.SelectRows(\n"
                f"        Source,\n"
                f"        each Text.Lower([Name]) = Text.Lower(FileName)\n"
                f"    ),\n"
                f"    FilteredByLib = if Table.RowCount(FilteredByName) > 1\n"
                f"        then Table.SelectRows(FilteredByName, each Text.Contains([Folder Path], \"Shared Documents\"))\n"
                f"        else FilteredByName,\n"
                f"    FileBinary = if Table.RowCount(FilteredByLib) = 0\n"
                f"        then error \"File \" & FileName & \" not found under \" & SiteUrl\n"
                f"        else FilteredByLib{{0}}[Content],\n"
                f"    ExcelData = Excel.Workbook(FileBinary, null, true),\n"
                f"    SheetData = ExcelData{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
                f"    PromotedHeaders = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
                f"{sp_transform}\n"
                f"in\n"
                f"    {sp_final}"
            )
        else:
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{path}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{path}"'
            m = (
                f"let\n"
                f"    FilePath = {path_expr},\n"
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

        if _is_sharepoint_url(base_path):
            opts = table.get("options", {})
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, csv_path, opts)
            sp_transform, sp_final = self._apply_types_as_is(fields, "PromotedHeaders")
            m = _build_sharepoint_m(
                site_url=site_url,
                filename=filename,
                folder_path=folder_path,
                delimiter=",",
                encoding=65001,
                transform_step=sp_transform,
                final_step=sp_final,
                is_qvd=True,
            )
        else:
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{csv_path}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{csv_path}"'
            m = (
                f"let\n"
                f"    // QVD not supported natively — expects pre-converted CSV\n"
                f"    FilePath = {path_expr},\n"
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