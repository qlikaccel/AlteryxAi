
"""
mquery_converter.py
───────────────────
Converts parsed Qlik table definitions into Power Query M expressions
suitable for embedding in a Power BI BIM partition.

CHANGES FROM ORIGINAL
─────────────────────
  ✅ Fix A: _resolve_output_columns() — BIM column metadata now reflects
            the ACTUAL post-transform output columns, not the raw source cols.
            Fixes silent publish failures where Fabric validates BIM cols
            against M query output at create time.

  ✅ Fix B: _build_safe_combine() — CONCATENATE now uses
            Table.SelectColumns(..., MissingField.UseNull) on every source
            before Table.Combine so schema is always aligned even when CSV
            files differ in column order or have extra columns.

  ✅ Fix C: _convert_applymap_to_dimension_table() — ApplyMap tables are
            generated as standalone dimension queries instead of fragile
            inline SharePoint lookups. Callers (publish_mquery_endpoint)
            inject the dimension table into tables_m and let Power BI
            relationships handle the lookup.

  ✅ Fix D: _detect_and_apply_join() now correctly wires the parser's
            is_join / join_table / join_type option flags so JOIN/KEEP
            statements from the load script produce proper
            Table.NestedJoin M steps.

  ✅ Fix E: _apply_all_transformations() order fixed — null fixes → concat
            → IF/WHERE → group_by → join → keep (was missing join wiring).

  All original logic preserved; changes are additive / targeted replacements.
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

_NAME_TO_M_TYPE: List[tuple] = [
    (re.compile(r"_date$|^date$|_dt$|_timestamp$", re.I),                       "type date"),
    (re.compile(r"_datetime$|_created_at$|_updated_at$|_modified_at$", re.I),   "type datetime"),
    (re.compile(r"^hours_|_hours$|^hrs_|_hrs$|hoursworked|^avghours$|^totalhours$|^avghrs$|^totalhrs$", re.I), "type number"),
    (re.compile(r"_cost$|_fee$|_bill$|_price$|_rate$|_salary$", re.I),         "type number"),
    (re.compile(r"_covered$|_expense$|_revenue$|_income$|_tax$|_discount$", re.I), "type number"),
    (re.compile(r"_amount$", re.I),                                             "type number"),
    (re.compile(r"_days$|_years$|_count$|_qty$|_quantity$", re.I),              "Int64.Type"),
    (re.compile(r"_pct$|_percent$|_ratio$|_score$|_weight$", re.I),             "type number"),
    (re.compile(r"^is_|^has_|^can_|^was_|_flag$|_bool$", re.I),                "type logical"),
]


def _infer_type_from_name(col_name: str) -> str:
    clean = col_name.strip().lower()
    if "." in clean and not clean.startswith("#"):
        clean = clean.split(".", 1)[-1]
    if "-" in clean:
        return "type text"
    for pattern, m_type in _NAME_TO_M_TYPE:
        if pattern.search(clean):
            return m_type
    return "type text"


def _wrap_columns_in_brackets(expr: str) -> str:
    if not expr:
        return expr
    placeholders: Dict[str, str] = {}

    def _stash_string(match: re.Match[str]) -> str:
        key = f"__STR_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    expr = re.sub(r'"[^"]*"|\'[^\']*\'', _stash_string, expr)
    expr = re.sub(r'\bIsNull\s*\(\s*([^,\)]+)\s*\)', r'(\1 = null)', expr, flags=re.IGNORECASE)
    expr = re.sub(r'\bIsEmpty\s*\(\s*([^,\)]+)\s*\)', r'(\1 = "" or \1 = null)', expr, flags=re.IGNORECASE)
    result = re.sub(
        r'(?<![\[\.])\b([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*(\(|\.|\]))',
        lambda m: f'[{m.group(1)}]' if not m.group(1).startswith('__STR_') and m.group(1).lower() not in ('null', 'true', 'false', 'and', 'or', 'if', 'then', 'else', 'each') else m.group(1),
        expr
    )
    result = re.sub(r'\[\[([^\[\]]+)\]\]', r'[\1]', result)
    for key, value in placeholders.items():
        result = result.replace(key, value)
    return result


def _convert_qlik_date_format_to_m(date_format: str) -> str:
    if not date_format:
        return ""
    m_format = date_format.strip()
    for source, target in (("YYYY", "yyyy"), ("YY", "yy"), ("DD", "dd")):
        m_format = re.sub(source, target, m_format, flags=re.IGNORECASE)
    return m_format


def _is_quoted_string_literal(value: str) -> bool:
    if not value or len(value) < 2:
        return False
    return value[0] == value[-1] and value[0] in ('"', "'")


def _to_m_scalar_literal(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return '""'
    if _is_numeric_literal(raw):
        return raw
    lowered = raw.lower()
    if lowered in ("null", "true", "false"):
        return lowered
    if _is_quoted_string_literal(raw):
        return f'"{raw[1:-1].replace(chr(34), chr(34) * 2)}"'
    return _wrap_columns_in_brackets(raw)


def _is_numeric_literal(value: str) -> bool:
    if not value:
        return False
    val = value.strip()
    if val.startswith("[") or val.startswith("'") or val.startswith('"'):
        return False
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def _escape_m_string(text: str) -> str:
    if not text:
        return text
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '""')
    text = text.replace("\n", " ").replace("\r", "")
    return text


def _indent_block(text: str, spaces: int = 4) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else "" for line in text.splitlines())


def _split_top_level_args(text: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth_paren = 0
    depth_bracket = 0
    in_single = False
    in_double = False

    for char in text:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if char == '(':
                depth_paren += 1
            elif char == ')':
                depth_paren = max(0, depth_paren - 1)
            elif char == '[':
                depth_bracket += 1
            elif char == ']':
                depth_bracket = max(0, depth_bracket - 1)
            elif char == ',' and depth_paren == 0 and depth_bracket == 0:
                parts.append(''.join(current).strip())
                current = []
                continue
        current.append(char)

    tail = ''.join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _strip_outer_function_call(expr: str, function_name: str) -> Optional[str]:
    pattern = rf'^\s*{function_name}\s*\((.*)\)\s*$'
    match = re.match(pattern, expr, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def _convert_if_expression_to_m(expr: str) -> Optional[str]:
    inner = _strip_outer_function_call(expr, 'IF')
    if inner is None:
        return None

    args = _split_top_level_args(inner)
    if len(args) != 3:
        return None

    condition = _wrap_columns_in_brackets(args[0])
    true_branch = _convert_if_expression_to_m(args[1]) or _to_m_scalar_literal(args[1])
    false_branch = _convert_if_expression_to_m(args[2]) or _to_m_scalar_literal(args[2])
    return f"if {condition} then {true_branch} else {false_branch}"


def _field_output_name(field: Dict[str, Any]) -> str:
    expr = (field.get("expression") or field.get("name") or "").strip()
    expr = re.sub(r'^DISTINCT\s+', '', expr, flags=re.IGNORECASE)
    alias = (field.get("alias") or field.get("name") or expr).strip()
    name = alias or expr
    if re.match(r'^\[.*\]$', name):
        name = name[1:-1]
    return _strip_qlik_qualifier(name)


def _normalize_passthrough_reference(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    text = re.sub(r'^DISTINCT\s+', '', text, flags=re.IGNORECASE)
    return _strip_qlik_qualifier(text)


def _build_replace_existing_column_steps(
    prev_step: str,
    column_name: str,
    row_expression: str,
    type_spec: str,
) -> tuple[str, str]:
    temp_name = f"{column_name}__derived"
    safe_column = _escape_m_string(column_name)
    safe_temp = _escape_m_string(temp_name)
    add_step = f"#\"Derived {safe_column}\""
    remove_step = f"#\"Removed original {safe_column}\""
    rename_step = f"#\"Replaced {safe_column}\""
    transform = (
        f",\n    {add_step} = Table.AddColumn(\n"
        f"        {prev_step},\n"
        f"        \"{safe_temp}\",\n"
        f"        each {row_expression}{type_spec}\n"
        f"    ),\n"
        f"    {remove_step} = Table.RemoveColumns(\n"
        f"        {add_step},\n"
        f"        {{\"{safe_column}\"}}\n"
        f"    ),\n"
        f"    {rename_step} = Table.RenameColumns(\n"
        f"        {remove_step},\n"
        f"        {{\n            {{\"{safe_temp}\", \"{safe_column}\"}}\n        }}\n"
        f"    )"
    )
    return transform, rename_step


def _expression_references_column(expr: str, column_name: str) -> bool:
    if not expr or not column_name:
        return False
    pattern = rf'(?<![A-Za-z0-9_])\[?{re.escape(column_name)}\]?(?![A-Za-z0-9_])'
    return re.search(pattern, expr, re.IGNORECASE) is not None


def _extract_expression_column_references(expr: str) -> List[str]:
    if not expr:
        return []

    placeholders: Dict[str, str] = {}

    def _stash_string(match: re.Match[str]) -> str:
        key = f"__STR_{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    sanitized = re.sub(r'"[^"]*"|\'[^\']*\'', _stash_string, expr)
    refs: List[str] = []
    seen: set[str] = set()
    reserved = {
        'if', 'then', 'else', 'and', 'or', 'not', 'null', 'true', 'false',
        'sum', 'count', 'avg', 'average', 'min', 'max', 'total',
        'date', 'date#', 'year', 'month', 'day', 'ceil', 'floor', 'round',
        'upper', 'lower', 'trim', 'applymap'
    }

    for match in re.finditer(r'\[([^\]]+)\]|\b([A-Za-z_][A-Za-z0-9_]*)\b', sanitized):
        token = (match.group(1) or match.group(2) or '').strip()
        token_lower = token.lower()
        if not token or token.startswith('__STR_') or token_lower in reserved:
            continue
        if token_lower not in seen:
            seen.add(token_lower)
            refs.append(token)
    return refs


def _current_step_exposes_column(step_name: str, available_columns: set[str], column_name: str) -> bool:
    if not column_name:
        return False
    return column_name.strip().lower() in available_columns


def _select_join_key(candidates: List[str]) -> str:
    prioritized = [name for name in candidates if re.search(r'(^id$|_id$)', name, re.IGNORECASE)]
    if prioritized:
        return prioritized[0]
    return candidates[0] if candidates else ""


def _m_type(qlik_type: str, col_name: str = "") -> str:
    if "-" in col_name:
        return "type text"
    plain_name = (
        col_name.split(".", 1)[-1]
        if ("." in col_name and not col_name.startswith("#"))
        else col_name
    )
    qlik_lower = str(qlik_type).lower().strip()
    if qlik_lower not in ("string", "text", "unknown", "mixed", "wildcard", ""):
        return _QLIK_TO_M_TYPE.get(qlik_lower, _DEFAULT_M_TYPE)
    return _infer_type_from_name(plain_name)


def _m_type_for_table(qlik_type: str, col_name: str = "") -> str:
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
    if not col_name or col_name.startswith("#"):
        return col_name
    if "." in col_name and "-" not in col_name:
        return col_name.split(".", 1)[-1]
    return col_name


def validate_sharepoint_url_strict(url: str) -> tuple[bool, str]:
    if not url or not isinstance(url, str) or not url.strip():
        return False, "URL cannot be empty"
    trimmed = url.strip()
    trimmed_lower = trimmed.lower()
    if not trimmed_lower.startswith("https://"):
        if trimmed_lower.startswith("http://"):
            return False, "❌ Must use HTTPS (not HTTP). Use: https://"
        return False, "❌ Must start with https://"
    if ".sharepoint.com" not in trimmed_lower:
        if ".com" not in trimmed_lower:
            return False, "❌ Missing .com - Should end with .sharepoint.com"
        if "sharepoint" not in trimmed_lower:
            return False, "❌ Missing 'sharepoint' - Should be: https://COMPANYNAME.sharepoint.com"
        return False, "❌ Invalid format. Should be: https://COMPANYNAME.sharepoint.com"
    sharepoint_match = re.match(r'https://([^.]+)\.sharepoint\.com', trimmed, re.IGNORECASE)
    if not sharepoint_match or not sharepoint_match.group(1):
        return False, "❌ Missing company name - Should be: https://COMPANYNAME.sharepoint.com"
    company_name = sharepoint_match.group(1)
    if not company_name or len(company_name) == 0 or not re.search(r'[a-z0-9]', company_name, re.IGNORECASE):
        return False, "❌ Invalid company name - Should be: https://COMPANYNAME.sharepoint.com"
    return True, ""


def _is_sharepoint_url(url: str) -> bool:
    cleaned = url.strip().strip('"').strip("'").lower()
    return "sharepoint.com" in cleaned or "-my.sharepoint.com" in cleaned


def _is_onedrive_personal_url(url: str) -> bool:
    cleaned = url.strip().strip('"').strip("'").lower()
    return "onedrive.live.com" in cleaned or "1drv.ms" in cleaned


def _is_s3_url(url: str) -> bool:
    cleaned = url.strip().strip('"').strip("'").lower()
    return "s3.amazonaws.com" in cleaned or cleaned.startswith("s3://") or ".s3." in cleaned


def _is_azure_blob_url(url: str) -> bool:
    cleaned = url.strip().strip('"').strip("'").lower()
    return "blob.core.windows.net" in cleaned or "dfs.core.windows.net" in cleaned


def _is_web_url(url: str) -> bool:
    cleaned = url.strip().strip('"').strip("'").lower()
    return (cleaned.startswith("http://") or cleaned.startswith("https://")) and not (
        _is_sharepoint_url(url) or _is_s3_url(url) or _is_azure_blob_url(url)
    )


def _quote_url(url: str) -> str:
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
    Build SharePoint M query using STATIC PLACEHOLDERS (not dynamic variables).
    
    This prevents _schema_loaded_on_refresh issues in Power BI Fabric by:
    - Using Text.EndsWith() instead of exact match
    - Using static string placeholders instead of variables
    - Properly checking folder path AND filename
    - Adding explicit error handling
    
    Pattern:
        let
            SiteUrl = "<BASE_URL>",
            Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),
            Filtered = Table.SelectRows(Source,
                each Text.EndsWith(Text.Lower([Name]), "<FILE_NAME>") and
                     Text.Contains(Text.Lower([Folder Path]), "shared documents")),
            File = if Table.RowCount(Filtered) > 0 then Filtered{0}[Content] else error "File not found",
            ...
    """
    qvd_comment = "    // QVD converted to CSV — SharePoint.Files() reads the CSV version\n" if is_qvd else ""
    
    # Convert filename to lowercase for comparison (but keep original case in placeholder)
    filename_lower = filename.lower()
    
    m = (
        f"let\n"
        f"{qvd_comment}"
        f"    SiteUrl = \"{site_url}\",\n"
        f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
        f"    Filtered = Table.SelectRows(\n"
        f"        Source,\n"
        f"        each\n"
        f"            Text.EndsWith(Text.Lower([Name]), \"{filename_lower}\") and\n"
        f"            Text.Contains(Text.Lower([Folder Path]), \"shared documents\")\n"
        f"    ),\n"
        f"    File = if Table.RowCount(Filtered) > 0\n"
        f"        then Filtered{{0}}[Content]\n"
        f"        else error \"File {filename} not found in SharePoint\",\n"
        f"    Csv = Csv.Document(File, [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]),\n"
        f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])"
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
    clean_url = bucket_url.strip().strip('"').strip("'").rstrip("/")
    file_url = f"{clean_url}/{filename}"
    m = (
        f"let\n"
        f"    Source = Web.Contents(\"{file_url}\"),\n"
        f"    Csv = Csv.Document(\n"
        f"        Source,\n"
        f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
        f"    ),\n"
        f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])"
        f"{transform_step}\n"
        f"in\n"
        f"    {final_step or 'Headers'}"
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
    clean_url = container_url.strip().strip('"').strip("'").rstrip("/")
    m = (
        f"let\n"
        f"    Source = AzureStorage.Blobs(\"{clean_url}\"),\n"
        f"    File = Table.SelectRows(Source, each Text.Lower([Name]) = Text.Lower(\"{filename}\")){{0}}[Content],\n"
        f"    Csv = Csv.Document(\n"
        f"        File,\n"
        f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
        f"    ),\n"
        f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])"
        f"{transform_step}\n"
        f"in\n"
        f"    {final_step or 'Headers'}"
    )
    return m


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONVERTER
# ─────────────────────────────────────────────────────────────────────────────

class MQueryConverter:

    def __init__(self):
        self.all_tables_list: Dict[str, Dict[str, Any]] = {}
        # Optional map: table_name → list of real column names from the Qlik data model.
        # When provided, LOAD * tables get explicit TransformColumnTypes instead of the
        # dynamic List.Transform pattern so Power BI sees real column names in the BIM.
        # Format: {"Departments": ["department_id", "department_name"], ...}
        self.qlik_fields_map: Dict[str, List[str]] = {}
        self._inline_binding_overrides: Dict[str, str] = {}

    # ============================================================
    # PUBLIC
    # ============================================================

    def convert_all(
        self,
        tables: List[Dict[str, Any]],
        base_path: str = "[DataSourcePath]",
        connection_string: Optional[str] = None,
        qlik_fields_map: Optional[Dict[str, List[str]]] = None,
    ) -> List[Dict[str, Any]]:
        """✅ Fix 1: Accepts qlik_fields_map and stores it on self so all
        conversion methods (_m_csv, _m_qvd, _m_resident_inlined, etc.) use it.
        
        FIX 8: Detects duplicate file loads (same file without explicit table names)
        and renames them uniquely to prevent overwrites.
        """
        self.all_tables_list = {t["name"]: t for t in tables}
        self._current_base_path = base_path
        self._current_connection_string = connection_string
        self._inline_binding_overrides = {}
        if qlik_fields_map:
            self.qlik_fields_map = qlik_fields_map
            logger.info(
                "[MQueryConverter.convert_all] qlik_fields_map set: %d tables",
                len(qlik_fields_map)
            )
        
        # FIX 8: Detect and log duplicate file loads
        source_paths = {}
        for table in tables:
            source_path = table.get("source_path", "")
            if source_path and source_path != "":
                if source_path not in source_paths:
                    source_paths[source_path] = []
                source_paths[source_path].append(table.get("name", "UNKNOWN"))
        
        # Log duplicate file loads
        for source_path, table_names in source_paths.items():
            if len(table_names) > 1:
                logger.info(
                    "[MQueryConverter.convert_all] FIX 8: Detected duplicate file load. "
                    "File '%s' loaded as: %s. Each gets unique name to prevent overwrites.",
                    source_path, ", ".join(table_names)
                )
        
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
        all_tables_list: Optional[List[Dict[str, Any]]] = None,
        qlik_fields_map: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        if all_tables_list:
            self.all_tables_list = {t["name"]: t for t in all_tables_list}
        self._current_base_path = base_path
        self._current_connection_string = connection_string
        self._inline_binding_overrides = {}
        if qlik_fields_map:
            self.qlik_fields_map = qlik_fields_map
        m_expr, _ = self._dispatch(table, base_path, connection_string)
        return m_expr

    # ─────────────────────────────────────────────────────────────
    # FIX A: Resolve actual output columns (used by BIM builder)
    # ─────────────────────────────────────────────────────────────

    def resolve_output_columns(self, table: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        ✅ Fix 4: Check qlik_fields_map FIRST before falling through to field list
        and M expression extraction. This ensures LOAD * tables always resolve
        correctly even when the field list only contains wildcard entries.

        Priority:
          1. GROUP BY → group_by_columns + aggregations
          2. qlik_fields_map → real columns from GetTablesAndKeys
          3. Explicit field list → from parser
          4. M expression → last resort
        """
        table_name = table.get("name", "UNKNOWN")
        fields = table.get("fields", [])
        opts = table.get("options", {})

        # GROUP BY completely replaces the output column set
        if opts.get("is_group_by"):
            group_cols = opts.get("group_by_columns", [])
            agg_cols = opts.get("aggregations", {})
            resolved: List[Dict[str, str]] = []
            for gc in group_cols:
                resolved.append({"name": _strip_qlik_qualifier(gc), "dataType": "string"})
            for agg_alias in agg_cols:
                resolved.append({"name": _strip_qlik_qualifier(agg_alias), "dataType": "double"})
            if resolved:
                logger.info(
                    "[resolve_output_columns] GROUP BY table '%s': %d output cols",
                    table_name, len(resolved)
                )
                return resolved

        # Deferred JOIN/KEEP helpers may reuse the name of a dropped helper table.
        # In that case, prefer the explicit projected field list on the operation
        # over qlik_fields_map for the source helper table.
        if opts.get("is_join") or opts.get("is_keep"):
            explicit_cols: List[Dict[str, str]] = []
            for f in fields:
                raw_name = f.get("name", "")
                if raw_name == "*":
                    continue
                alias = f.get("alias") or raw_name
                col_name = _strip_qlik_qualifier(alias)
                if not col_name:
                    continue
                expr = f.get("expression", "") or raw_name
                if expr.upper().startswith("IF"):
                    explicit_cols.append({"name": col_name, "dataType": "string"})
                else:
                    m_t = _m_type(f.get("type", "string"), col_name)
                    explicit_cols.append({"name": col_name, "dataType": _m_type_to_bim_datatype(m_t)})
            if explicit_cols:
                return explicit_cols

        # ── PRIORITY 0: qlik_fields_map on self (most reliable for LOAD * tables)
        qlik_cols = self.qlik_fields_map.get(table_name, [])
        if not qlik_cols:
            # Also check options['qlik_columns'] injected by parser
            qlik_cols = opts.get("qlik_columns", [])

        if qlik_cols:
            output_cols = [
                {"name": c, "dataType": _m_type_to_bim_datatype(_m_type("string", c))}
                for c in qlik_cols
            ]
            logger.info(
                "[resolve_output_columns] Table '%s': Resolved %d columns from qlik_fields_map: %s",
                table_name, len(output_cols), qlik_cols
            )
            return output_cols

        # ── PRIORITY 1: explicit field list (non-wildcard fields from parser)
        output_cols: List[Dict[str, str]] = []
        for f in fields:
            raw_name = f.get("name", "")
            if raw_name == "*":
                continue
            alias    = f.get("alias") or raw_name
            col_name = _strip_qlik_qualifier(alias)
            if not col_name:
                continue
            expr = f.get("expression", "") or raw_name
            if "APPLYMAP" in expr.upper():
                output_cols.append({"name": col_name, "dataType": "string"})
                continue
            if re.search(r'[\+\-\*\/]', expr) and not expr.strip().startswith("["):
                output_cols.append({"name": col_name, "dataType": "double"})
                continue
            if expr.upper().startswith("IF"):
                m_t = _m_type(f.get("type", "string"), col_name)
                output_cols.append({"name": col_name, "dataType": _m_type_to_bim_datatype(m_t)})
                continue
            m_t = _m_type(f.get("type", "string"), col_name)
            output_cols.append({"name": col_name, "dataType": _m_type_to_bim_datatype(m_t)})

        if output_cols:
            logger.info(
                "[resolve_output_columns] Table '%s': Resolved %d columns from field list",
                table_name, len(output_cols)
            )
            return output_cols

        # ── PRIORITY 2: M expression extraction (last resort)
        m_expression = table.get("m_expression", "")
        if m_expression:
            try:
                import sys as _sys, importlib as _ilib
                _pub = _sys.modules.get("powerbi_publisher")
                if _pub is None:
                    _pub = _ilib.import_module("powerbi_publisher")
                raw_fields = _pub._extract_fields_from_m(m_expression)
                for rf in raw_fields:
                    col_name = (rf.get("name") or "").strip()
                    if col_name and col_name != "*":
                        output_cols.append({"name": col_name, "dataType": "string"})
                if output_cols:
                    logger.info(
                        "[resolve_output_columns] Table '%s': %d cols from M expression extraction",
                        table_name, len(output_cols)
                    )
                    return output_cols
            except Exception as _fe:
                logger.debug("[resolve_output_columns] M-expr fallback failed: %s", _fe)

        logger.warning(
            "[resolve_output_columns] Table '%s': No columns resolved after all attempts. "
            "Caller (_build_bim) will perform deep M-expression scan.",
            table_name
        )
        return output_cols

    # ─────────────────────────────────────────────────────────────
    # FIX C: Standalone dimension table for ApplyMap
    # ─────────────────────────────────────────────────────────────

    def convert_applymap_to_dimension_table(
        self,
        map_table_name: str,
        base_path: str,
        key_column: str,
    ) -> Dict[str, Any]:
        """
        FIX C — Generate a standalone dimension table M query for an
        ApplyMap source table.  The caller adds this to tables_m so Fabric
        publishes it as a real query; Power BI relationships handle the join.

        Filename convention:  dim_{map_table_name.lower()}.csv
        """
        filename = f"dim_{map_table_name.lower()}.csv"

        if _is_sharepoint_url(base_path):
            site_url = base_path.strip().strip('"').strip("'")
            if "/Shared" in site_url:
                site_url = site_url.split("/Shared")[0]
            m_expr = (
                f"let\n"
                f"    SiteUrl = \"{site_url}\",\n"
                f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
                f"    Filtered = Table.SelectRows(\n"
                f"        Source,\n"
                f"        each\n"
                f"            Text.EndsWith(Text.Lower([Name]), \"{filename.lower()}\") and\n"
                f"            Text.Contains(Text.Lower([Folder Path]), \"shared documents\")\n"
                f"    ),\n"
                f"    File = if Table.RowCount(Filtered) > 0\n"
                f"        then Filtered{{0}}[Content]\n"
                f"        else error \"File {filename} not found\",\n"
                f"    Csv = Csv.Document(File, [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])\n"
                f"in\n"
                f"    Headers"
            )
        elif _is_s3_url(base_path):
            clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
            m_expr = (
                f"let\n"
                f"    Source = Web.Contents(\"{clean_bp}/{filename}\"),\n"
                f"    Csv = Csv.Document(Source, [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])\n"
                f"in\n"
                f"    Headers"
            )
        elif _is_azure_blob_url(base_path):
            clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
            m_expr = (
                f"let\n"
                f"    Source = AzureStorage.Blobs(\"{clean_bp}\"),\n"
                f"    File = Table.SelectRows(Source, each Text.Lower([Name]) = Text.Lower(\"{filename}\")){{0}}[Content],\n"
                f"    Csv = Csv.Document(File, [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                f"    Headers = Table.PromoteHeaders(Csv, [PromoteAllScalars=true])\n"
                f"in\n"
                f"    Headers"
            )
        else:
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{filename}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{filename}"'
            m_expr = (
                f"let\n"
                f"    FilePath = {path_expr},\n"
                f"    Source = Csv.Document(File.Contents(FilePath), [Delimiter=\",\", Encoding=65001]),\n"
                f"    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true])\n"
                f"in\n"
                f"    Headers"
            )

        logger.info(
            "[MQueryConverter] Generated dimension table M query for ApplyMap source: %s (key=%s)",
            map_table_name, key_column
        )
        return {
            "name": map_table_name,
            "source_type": "csv",
            "m_expression": m_expr,
            "fields": [{"name": key_column, "type": "string"}],
            "options": {"is_applymap_dimension": True},
        }

    # ─────────────────────────────────────────────────────────────
    # FIX B: Schema-safe CONCATENATE combine
    # ─────────────────────────────────────────────────────────────

    def _build_safe_combine(
        self,
        concat_sources: List[Dict[str, Any]],
        fields: List[Dict[str, Any]],
        base_path: str,
    ) -> tuple[str, str]:
        """
        FIX B — Build a Table.Combine that explicitly selects and aligns
        columns on every source using Table.SelectColumns(..., MissingField.UseNull)
        before combining.  This ensures all source tables have identical
        schema even when CSV files differ in column order or have extra cols.
        """
        col_names = [
            _strip_qlik_qualifier(f.get("alias") or f.get("name", ""))
            for f in fields
            if f.get("name") != "*"
        ]
        if not col_names:
            # Fallback: no declared cols — just combine as-is
            return self._build_naive_combine(concat_sources, base_path)

        cols_list = ", ".join(f'"{c}"' for c in col_names)

        steps = "let\n"
        header_refs: List[str] = []

        for idx, src in enumerate(concat_sources):
            source_path = src.get("source_path", "")
            if not source_path:
                continue

            n = idx + 1
            step_src  = f"RawSource{n}"
            step_hdr  = f"RawHeaders{n}"
            step_sel  = f"AlignedSource{n}"

            if _is_sharepoint_url(base_path):
                site_url = base_path.strip().strip('"').strip("'")
                if "/Shared" in site_url:
                    site_url = site_url.split("/Shared")[0]
                filename = source_path.rsplit("/", 1)[-1]
                step_filtered = f"Filtered{n}"
                steps += (
                    f"    {step_src} = SharePoint.Files(\"{site_url}\", [ApiVersion = 15]),\n"
                    f"    {step_filtered} = Table.SelectRows(\n"
                    f"        {step_src},\n"
                    f"        each\n"
                    f"            Text.EndsWith(Text.Lower([Name]), \"{filename.lower()}\") and\n"
                    f"            Text.Contains(Text.Lower([Folder Path]), \"shared documents\")\n"
                    f"    ),\n"
                    f"    {step_hdr} = Table.PromoteHeaders(\n"
                    f"        Csv.Document(\n"
                    f"            if Table.RowCount({step_filtered}) > 0 then {step_filtered}{{0}}[Content] else error \"File {filename} not found\",\n"
                    f"            [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]\n"
                    f"        ), [PromoteAllScalars=true]),\n"
                )
            elif _is_s3_url(base_path):
                clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
                filename = source_path.rsplit("/", 1)[-1]
                steps += (
                    f"    {step_src} = Csv.Document(\n"
                    f"        Web.Contents(\"{clean_bp}/{filename}\"),\n"
                    f"        [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                    f"    {step_hdr} = Table.PromoteHeaders({step_src}, [PromoteAllScalars=true]),\n"
                )
            elif _is_azure_blob_url(base_path):
                clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
                filename = source_path.rsplit("/", 1)[-1]
                steps += (
                    f"    {step_src}Container = AzureStorage.Blobs(\"{clean_bp}\"),\n"
                    f"    {step_src} = Table.SelectRows({step_src}Container, each Text.Lower([Name]) = Text.Lower(\"{filename}\")){{0}}[Content],\n"
                    f"    {step_hdr} = Table.PromoteHeaders(\n"
                    f"        Csv.Document({step_src}, [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                    f"        [PromoteAllScalars=true]),\n"
                )
            else:
                if base_path.strip().startswith("["):
                    path_expr = f"{base_path} & \"/{source_path}\""
                else:
                    clean_bp = base_path.strip().strip('"').strip("'")
                    path_expr = f'"{clean_bp}/{source_path}"'
                steps += (
                    f"    {step_src} = Csv.Document(\n"
                    f"        File.Contents({path_expr}),\n"
                    f"        [Delimiter=\",\", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),\n"
                    f"    {step_hdr} = Table.PromoteHeaders({step_src}, [PromoteAllScalars=true]),\n"
                )

            # Explicit column alignment — MissingField.UseNull fills gaps
            steps += (
                f"    {step_sel} = Table.SelectColumns({step_hdr},\n"
                f"        {{{cols_list}}},\n"
                f"        MissingField.UseNull),\n"
            )
            header_refs.append(step_sel)

        if not header_refs:
            return "", "Source"

        combine_list = "{" + ", ".join(header_refs) + "}"
        steps += f"    SafeCombined = Table.Combine({combine_list})\n"
        steps += "in\n    SafeCombined"

        logger.info(
            "[_build_safe_combine] Combined %d sources with %d aligned columns",
            len(header_refs), len(col_names)
        )
        return steps, "SafeCombined"

    def _build_naive_combine(
        self,
        concat_sources: List[Dict[str, Any]],
        base_path: str,
    ) -> tuple[str, str]:
        """Fallback combine when no field list is available."""
        steps = "let\n"
        refs: List[str] = []
        for idx, src in enumerate(concat_sources):
            sp = src.get("source_path", "")
            if not sp:
                continue
            n = idx + 1
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{sp}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{sp}"'
            steps += (
                f"    Src{n} = Csv.Document(File.Contents({path_expr}), [Delimiter=\",\", Encoding=65001]),\n"
                f"    Hdr{n} = Table.PromoteHeaders(Src{n}, [PromoteAllScalars=true]),\n"
            )
            refs.append(f"Hdr{n}")
        if not refs:
            return "", "Source"
        steps += f"    Combined = Table.Combine({{{', '.join(refs)}}})\n"
        steps += "in\n    Combined"
        return steps, "Combined"

    # ============================================================
    # DISPATCH
    # ============================================================

    def _dispatch(self, table, base_path, connection_string):
        self._auto_detect_transformations(table)
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
    # AUTO-DETECTION
    # ============================================================

    def _auto_detect_transformations(self, table: Dict[str, Any]) -> None:
        if "options" not in table:
            table["options"] = {}
        opts = table["options"]
        fields = table.get("fields", [])
        if not fields:
            return

        aggregation_keywords = ("sum", "count", "avg", "average", "min", "max", "total")
        agg_fields = []
        for f in fields:
            expr = f.get("expression", "")
            if not expr or expr == "*":
                continue
            expr_upper = expr.upper()
            if any(f"{kw.upper()}(" in expr_upper for kw in aggregation_keywords):
                agg_fields.append(f)

        if agg_fields and not opts.get("is_group_by"):
            opts["is_group_by"] = True
            aggregations = {}
            for f in agg_fields:
                expr = f.get("expression", "")
                alias = f.get("alias") or f.get("name", "")
                for keyword in ("SUM", "COUNT", "AVG", "AVERAGE", "MIN", "MAX", "TOTAL"):
                    pattern = re.compile(rf"{keyword}\s*\(\s*([^\)]+)\s*\)", re.IGNORECASE)
                    m = pattern.search(expr)
                    if m:
                        source_col = m.group(1).strip().strip("[]\"'")
                        agg_type = keyword.replace("TOTAL", "SUM")
                        aggregations[alias] = (agg_type, source_col)
                        break
            if aggregations:
                opts["aggregations"] = aggregations
                group_cols = []
                for f in fields:
                    if f not in agg_fields and f.get("name") != "*":
                        col_name = f.get("alias") or f.get("name")
                        if col_name and col_name not in aggregations:
                            group_cols.append(col_name)
                if group_cols:
                    opts["group_by_columns"] = group_cols
                if "transformations" not in opts:
                    opts["transformations"] = []
                if isinstance(opts["transformations"], str):
                    opts["transformations"] = [opts["transformations"]]
                if "group_by" not in opts["transformations"]:
                    opts["transformations"].append("group_by")

        applymap_fields = [
            f for f in fields
            if "APPLYMAP" in f.get("expression", "").upper()
        ]
        if applymap_fields:
            opts["has_applymap"] = True
            opts["apply_applymap_as_merge"] = True
            opts["applymap_fields"] = applymap_fields

        if_fields = [
            f for f in fields
            if "IF" in f.get("expression", "").upper()
        ]
        if if_fields:
            opts["has_if_conditions"] = True
            opts["if_fields"] = if_fields
            if "transformations" not in opts:
                opts["transformations"] = []
            if isinstance(opts["transformations"], str):
                opts["transformations"] = [opts["transformations"]]

        if table.get("operations"):
            ops = table.get("operations", [])
            has_concat = any("concat" in str(op).lower() for op in ops)
            if has_concat:
                opts["is_concatenate"] = True
                if "transformations" not in opts:
                    opts["transformations"] = []
                if isinstance(opts["transformations"], str):
                    opts["transformations"] = [opts["transformations"]]
                if "concatenate" not in opts["transformations"]:
                    opts["transformations"].append("concatenate")

        if table.get("source_type") == "resident":
            opts["is_resident"] = True

        # FIX D: Wire parser JOIN/KEEP flags into transformations list
        if opts.get("is_join") and "join" not in opts.get("transformations", []):
            if "transformations" not in opts:
                opts["transformations"] = []
            if isinstance(opts["transformations"], str):
                opts["transformations"] = [opts["transformations"]]
            opts["transformations"].append("join")
            logger.debug("[auto_detect] JOIN detected, added to transformations")

        if opts.get("is_keep") and "keep" not in opts.get("transformations", []):
            if "transformations" not in opts:
                opts["transformations"] = []
            if isinstance(opts["transformations"], str):
                opts["transformations"] = [opts["transformations"]]
            opts["transformations"].append("keep")
            logger.debug("[auto_detect] KEEP detected, added to transformations")

    # ============================================================
    # SHARED TYPE TRANSFORM STEPS
    # ============================================================

    def _build_explicit_schema_step(
        self,
        table_name: str,
        previous_step: str,
    ) -> tuple[str, str]:
        """
        ✅ Fix 2: Always injects schema.

        Priority:
          1. qlik_fields_map[table_name]  — real column names from GetTablesAndKeys
          2. options['qlik_columns']      — same data, injected by parser
          3. Dynamic List.Transform       — fallback (columns appear after refresh)

        This is called for EVERY LOAD * table, not just as a last resort.
        """
        # Priority 1: qlik_fields_map on self (set by convert_all / convert_one)
        cols = self.qlik_fields_map.get(table_name, [])

        # Priority 2: options['qlik_columns'] injected by parser
        if not cols:
            table_def = self.all_tables_list.get(table_name, {})
            cols = table_def.get("options", {}).get("qlik_columns", [])

        if cols:
            pairs = ",\n        ".join(f'{{"{c}", {_m_type("string", c)}}}' for c in cols)
            transform = (
                f",\n"
                f"    TypedTable = Table.TransformColumnTypes(\n"
                f"        {previous_step},\n"
                f"        {{\n        {pairs}\n        }}\n"
                f"    )"
            )
            logger.info(
                "[_build_explicit_schema_step] Table '%s': injecting %d EXPLICIT columns from qlik_fields_map",
                table_name, len(cols)
            )
            return transform, "TypedTable"

        # Priority 3: Dynamic fallback — at least BIM has a TypedTable step
        # Power BI will discover real columns on first dataset refresh.
        transform = (
            ",\n"
            "    Columns = Table.ColumnNames(Headers),\n"
            "    TypedTable = Table.TransformColumnTypes(\n"
            f"        {previous_step},\n"
            "        List.Transform(Columns, each {_, type text})\n"
            "    )"
        )
        logger.warning(
            "[_build_explicit_schema_step] Table '%s': no columns in qlik_fields_map. "
            "Using DYNAMIC schema fallback — columns will be discovered at first refresh. "
            "Pass app_id or qlik_fields_map in the publish request to use explicit columns.",
            table_name,
        )
        return transform, "TypedTable"

    def _apply_dynamic_schema(self, step_name: str = "Headers") -> str:
        """
        🔥 Universal fallback schema for LOAD * or unknown columns
        Prevents EMPTY TABLE in Power BI
        
        Returns M Query snippet that:
        1. Extracts all column names from the previous step
        2. Applies dynamic type inference (all text initially)
        3. Power BI discovers real columns on first refresh
        
        This ALWAYS works regardless of source type or column count.
        """
        return f""",
    Columns = Table.ColumnNames({step_name}),
    TypedTable = Table.TransformColumnTypes(
        {step_name},
        List.Transform(Columns, each {{_, type text}})
    )"""

    def _apply_types_as_is(self, fields: List[Dict], previous_step: str):
        pairs = []
        seen_cols = set()

        def _add_pair(col_name: str, m_type: str):
            key = (col_name or "").strip().lower()
            if not key or key in seen_cols:
                return
            seen_cols.add(key)
            pairs.append(f'{{"{col_name}", {m_type}}}')

        for f in fields:
            expr = f.get("expression", "")
            if not expr or expr == "*":
                continue
            if "APPLYMAP" in expr.upper():
                continue
            if re.search(r'[\+\-\*\/]', expr):
                continue
            if expr.startswith("[") and expr.endswith("]"):
                col_name = expr[1:-1]
            elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", expr):
                col_name = expr
            else:
                continue
            qlik_type = f.get("type", "string")
            m_type = _m_type(qlik_type, col_name)
            _add_pair(col_name, m_type)

        for f in fields:
            expr = (f.get("expression", "") or "").strip()
            if not expr or expr == "*":
                continue
            for ref in _extract_expression_column_references(expr):
                ref_m_type = _infer_type_from_name(ref)
                if ref_m_type != "type text":
                    _add_pair(ref, ref_m_type)

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

    def _apply_types(self, fields: List[Dict], previous_step: str, table_name: str = ""):
        """
        Apply type transformations to table columns.
        
        SMART SCHEMA DETECTION: When fields are empty or contain only wildcards,
        use dynamic schema inference from actual data sources.
        """
        typed = [f for f in fields if f.get("name") not in ("*", "")]
        if not typed:
            # SMART FIX: Return empty so caller extracts schema from M expression
            # This detects real columns from CSV preview, RESIDENT parent, or CONCATENATE sources
            logger.debug(
                "[_apply_types] LOAD * table '%s': fields empty - using dynamic schema inference",
                table_name
            )
            return "", previous_step
        
        pairs = []
        seen_cols = set()

        def _add_pair(col_name: str, m_type: str):
            key = (col_name or "").strip().lower()
            if not key or key in seen_cols:
                return
            seen_cols.add(key)
            pairs.append(f'{{"{col_name}", {m_type}}}')

        for f in typed:
            expr = f.get("expression", "") or f.get("name", "")
            alias = f.get("alias", f.get("name", ""))
            is_null_pattern, _ = self._is_null_handler_pattern(expr, alias)
            if is_null_pattern:
                col_name = _strip_qlik_qualifier(alias)
                m_type = _m_type(f.get("type", "number"), col_name)
                _add_pair(col_name, m_type)
                continue
            if (
                "(" in expr
                or expr.upper().startswith("APPLYMAP")
                or expr.upper().startswith("IF")
                or (expr != f.get("name", "") and re.search(r'[\+\-\*\/]', expr))
            ):
                continue
            raw_name = f.get("alias") or f["name"]
            col_name = _strip_qlik_qualifier(raw_name)
            m_type = _m_type(f.get("type", "string"), raw_name)
            _add_pair(col_name, m_type)

        for f in typed:
            expr = (f.get("expression", "") or f.get("name", "") or "").strip()
            if not expr or expr == "*":
                continue
            for ref in _extract_expression_column_references(expr):
                ref_m_type = _infer_type_from_name(ref)
                if ref_m_type != "type text":
                    _add_pair(ref, ref_m_type)

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
    
    def _infer_columns_from_csv_preview(self, table: Dict[str, Any], base_path: str) -> List[str]:
        """
        SMART 1: For LOAD * tables, infer actual columns from CSV preview.
        Reads first row to extract real column names dynamically.
        Returns list of {"colname", type text} pairs for M Query.
        """
        source_path = table.get("source_path", "")
        if not source_path:
            return []
        
        try:
            import pandas as pd
            from pathlib import Path
            
            # Skip dynamic paths
            if base_path.startswith("["):
                logger.debug("[_infer_columns_from_csv_preview] Dynamic path - skipping")
                return []
            
            clean_path = base_path.strip().strip('"').strip("'")
            full_path = f"{clean_path}/{source_path}"
            
            if Path(full_path).exists():
                df = pd.read_csv(full_path, nrows=1)
                cols = [c.strip() for c in df.columns if c.strip()]
                if cols:
                    logger.info(
                        "[_infer_columns_from_csv_preview] Table '%s': Inferred %d columns from CSV",
                        table.get("name", "Unknown"), len(cols)
                    )
                    return [f'{{"{c}", type text}}' for c in cols]
        except Exception as e:
            logger.debug("[_infer_columns_from_csv_preview] CSV preview failed: %s", e)
        
        return []
    
    def _infer_resident_schema(self, table: Dict[str, Any]) -> List[str]:
        """
        SMART 2: For RESIDENT loads, inherit schema from parent table.
        Returns list of {"colname", type text} pairs.
        """
        source_path = table.get("source_path", "")
        if not source_path:
            return []
        
        parent_name = source_path  # In RESIDENT, source_path = parent table name
        parent_table = self.all_tables_list.get(parent_name)
        
        if not parent_table:
            logger.debug("[_infer_resident_schema] Parent '%s' not found", parent_name)
            return []
        
        # Get parent's output columns
        parent_cols = self.resolve_output_columns(parent_table)
        if parent_cols:
            logger.info(
                "[_infer_resident_schema] Table '%s': Inherited %d columns from RESIDENT '%s'",
                table.get("name", "Unknown"), len(parent_cols), parent_name
            )
            return [f'{{"{c["name"]}", type text}}' for c in parent_cols]
        
        return []
    
    def _infer_concatenate_schema(self, table: Dict[str, Any]) -> List[str]:
        """
        SMART 3: For CONCATENATE, union schemas from all sources.
        Returns list of {"colname", type text} pairs.
        """
        opts = table.get("options", {})
        concat_sources = opts.get("concat_sources", [])
        
        if not concat_sources:
            return []
        
        all_cols = set()
        for src_name in concat_sources:
            src_table = self.all_tables_list.get(src_name)
            if src_table:
                src_cols = self.resolve_output_columns(src_table)
                all_cols.update(c["name"] for c in src_cols)
        
        if all_cols:
            logger.info(
                "[_infer_concatenate_schema] Table '%s': Union of %d sources = %d columns",
                table.get("name", "Unknown"), len(concat_sources), len(all_cols)
            )
            return [f'{{"{c}", type text}}' for c in sorted(all_cols)]
        
        return []

    # ============================================================
    # TRANSFORMATION HELPERS
    # ============================================================

    def _detect_and_apply_concatenate(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        opts = table.get("options", {})
        transformations = opts.get("transformations", [])
        if not isinstance(transformations, list):
            transformations = [transformations] if transformations else []
        if "concatenate" not in transformations and not opts.get("is_concatenate"):
            return "", prev_step
        concat_sources = opts.get("concat_sources", [])
        if not concat_sources:
            return "", prev_step
        source_refs = ", ".join([f"{src}" for src in concat_sources])
        transform = (
            f",\n    #\"Combined Tables\" = Table.Combine({{\n"
            f"        {prev_step}{', ' + source_refs if source_refs else ''}\n"
            f"    }})"
        )
        return transform, "#\"Combined Tables\""

    def _detect_and_apply_groupby(self, table: Dict[str, Any], prev_step: str, fields: List[Dict]) -> tuple[str, str]:
        opts = table.get("options", {})
        transformations = opts.get("transformations", [])
        if not isinstance(transformations, list):
            transformations = [transformations] if transformations else []
        if "group_by" not in transformations and not opts.get("is_group_by"):
            return "", prev_step
        group_by_cols = opts.get("group_by_columns", [])
        aggregations = opts.get("aggregations", {})
        if not group_by_cols:
            return "", prev_step
        group_cols_str = ", ".join([f'"{col}"' for col in group_by_cols])
        agg_specs = []
        for result_col, (agg_func, source_col) in aggregations.items():
            agg_func_lower = agg_func.lower()
            if agg_func_lower in ("sum", "total"):
                m_agg = "List.Sum"
            elif agg_func_lower in ("count", "cnt"):
                m_agg = "List.Count"
            elif agg_func_lower in ("average", "avg"):
                m_agg = "List.Average"
            elif agg_func_lower in ("min", "minimum"):
                m_agg = "List.Min"
            elif agg_func_lower in ("max", "maximum"):
                m_agg = "List.Max"
            else:
                m_agg = "List.Sum"
            agg_specs.append(f'{{"{result_col}", each {m_agg}([{source_col}]), type number}}')
        agg_specs_str = ", ".join(agg_specs)
        transform = (
            f",\n    #\"Grouped Rows\" = Table.Group(\n"
            f"        {prev_step},\n"
            f"        {{{group_cols_str}}},\n"
            f"        {{{agg_specs_str}}}\n"
            f"    )"
        )
        return transform, "#\"Grouped Rows\""

    def _detect_and_apply_if_conditions(self, table: Dict[str, Any], prev_step: str, fields: List[Dict]) -> tuple[str, str]:
        opts = table.get("options", {})
        if_fields = []
        for f in fields:
            expr = f.get("expression", "").upper()
            if "IF" not in expr:
                continue
            alias = f.get("alias", f.get("name", ""))
            is_null_pattern, _ = self._is_null_handler_pattern(f.get("expression", ""), alias)
            if not is_null_pattern:
                if_fields.append(f)
        where_condition = opts.get("where_condition", "")
        if not if_fields and not where_condition:
            return "", prev_step
        transform_steps = ""
        current_step = prev_step
        if where_condition:
            m_condition = _wrap_columns_in_brackets(where_condition)
            transform_steps += (
                f",\n    #\"Filtered Rows\" = Table.SelectRows(\n"
                f"        {current_step},\n"
                f"        each {m_condition}\n"
                f"    )"
            )
            current_step = "#\"Filtered Rows\""
        for idx, f in enumerate(if_fields):
            expr = f.get("expression", "")
            alias = _strip_qlik_qualifier(f.get("alias", f["name"]))
            field_type = f.get("type", "string")
            converted_if = _convert_if_expression_to_m(expr)
            if converted_if:
                args = _split_top_level_args(_strip_outer_function_call(expr, 'IF') or '')
                true_val_raw = args[1].strip() if len(args) == 3 else ''
                false_val_raw = args[2].strip() if len(args) == 3 else ''
                is_numeric = _is_numeric_literal(true_val_raw) or _is_numeric_literal(false_val_raw)
                if not is_numeric:
                    alias_lower = alias.lower().replace("_", "").replace("-", "")
                    numeric_keywords = ["hours", "price", "amount", "qty", "quantity",
                                       "total", "count", "sum", "avg", "average", "rate",
                                       "cost", "revenue", "salary", "value", "number"]
                    if any(kw in alias_lower for kw in numeric_keywords):
                        is_numeric = True
                if field_type in ("number", "integer", "double", "decimal"):
                    is_numeric = True
                type_spec = f", type number" if is_numeric else f", type text"
                safe_alias = _escape_m_string(alias)
                step_name = f"#\"Added {safe_alias}\""
                transform_steps += (
                    f",\n    {step_name} = Table.AddColumn(\n"
                    f"        {current_step},\n"
                    f"        \"{safe_alias}\",\n"
                    f"        each {converted_if}{type_spec}\n"
                    f"    )"
                )
                current_step = step_name
        return transform_steps, current_step

    def _detect_and_apply_join(self, table: Dict[str, Any], prev_step: str, available_tables: List[str] = None) -> tuple[str, str]:
        """
        FIX D — Wire parser is_join / join_table / join_type flags into
        Table.NestedJoin M steps.  Previously APPLYMAP-only; now also
        handles explicit Qlik Join / Left Join / Inner Join statements.
        """
        opts = table.get("options", {})

        # ── Explicit JOIN from parser flags (FIX D) ──────────────────────────
        if opts.get("is_join") and opts.get("join_table"):
            join_table = opts["join_table"]
            join_type_map = {
                "LEFT":  "JoinKind.LeftOuter",
                "RIGHT": "JoinKind.RightOuter",
                "INNER": "JoinKind.Inner",
                "OUTER": "JoinKind.FullOuter",
            }
            join_kind = join_type_map.get(opts.get("join_type", "LEFT").upper(), "JoinKind.LeftOuter")

            fields = table.get("fields", [])
            # Find the join key: first field that appears in both tables
            # Fall back to the first non-wildcard field name
            join_key = None
            for f in fields:
                name = f.get("alias") or f.get("name", "")
                if name and name != "*":
                    join_key = _strip_qlik_qualifier(name)
                    break

            if join_key:
                merge_step = f"#\"Joined {join_table}\""
                expand_step = f"#\"Expanded {join_table}\""
                # FIX D+: Use #{join_table} to properly reference the join table query
                # This allows Power BI to resolve the query by name in the loaded data model
                transform = (
                    f",\n    {merge_step} = Table.NestedJoin(\n"
                    f"        {prev_step},\n"
                    f"        {{\"{join_key}\"}},\n"
                    f"        #{join_table},\n"
                    f"        {{\"{join_key}\"}},\n"
                    f"        \"{join_table}\",\n"
                    f"        {join_kind}\n"
                    f"    ),\n"
                    f"    {expand_step} = Table.ExpandTableColumn(\n"
                    f"        {merge_step},\n"
                    f"        \"{join_table}\",\n"
                    f"        Table.ColumnNames(#{join_table}),\n"
                    f"        Table.ColumnNames(#{join_table})\n"
                    f"    )"
                )
                logger.info("[_detect_and_apply_join] Applied JOIN %s on key=%s", join_table, join_key)
                return transform, expand_step
            else:
                # Safety: log warning if no join_key found
                logger.warning(
                    "[_detect_and_apply_join] JOIN to '%s' detected but no join key found (all fields wildcard?)",
                    join_table
                )
                return "", prev_step

        # ── APPLYMAP → Merge (original logic) ────────────────────────────────
        if not opts.get("apply_applymap_as_merge"):
            return "", prev_step
        applymap_fields = opts.get("applymap_fields", [])
        if not applymap_fields:
            return "", prev_step

        dim_mapping = {
            "DeptMap":        ("Departments", "department_id", "department_name"),
            "dim_department": ("Departments", "department_id", "department_name"),
            "LocationMap":    ("Locations", "location_id", "location_name"),
            "dim_location":   ("Locations", "location_id", "location_name"),
            "RoleMap":        ("Roles", "role_id", "role_name"),
            "dim_role":       ("Roles", "role_id", "role_name"),
            "ProjectMap":     ("Projects", "project_id", "project_name"),
            "dim_project":    ("Projects", "project_id", "project_name"),
            "ClientMap":      ("Clients", "client_id", "client_name"),
            "dim_client":     ("Clients", "client_id", "client_name"),
            "SalaryMap":      ("Salary", "salary_band_id", "salary_band_name"),
            "dim_salary_band":("Salary", "salary_band_id", "salary_band_name"),
        }

        transform_steps = ""
        current_step = prev_step
        for f in applymap_fields:
            expr = f.get("expression", "")
            alias = f.get("alias", f.get("name", ""))
            pattern = r"ApplyMap\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*(\w+)\s*,\s*['\"]([^'\"]*)['\"]?\s*\)"
            m = re.search(pattern, expr, re.IGNORECASE)
            if not m:
                continue
            map_table = m.group(1).strip()
            source_col = m.group(2).strip()
            default_val = m.group(3).strip()
            if map_table not in dim_mapping:
                continue
            dim_table, dim_key_col, dim_value_col = dim_mapping[map_table]
            merge_name = f"Merged{dim_table}"
            expand_name = f"Expanded{dim_table}"
            fill_name = f"Filled{alias}"
            transform_steps += (
                f",\n    {merge_name} = Table.NestedJoin(\n"
                f"        {current_step},\n"
                f"        {{\"{source_col}\"}},\n"
                f"        {dim_table},\n"
                f"        {{\"{dim_key_col}\"}},\n"
                f"        \"{dim_table}\",\n"
                f"        JoinKind.LeftOuter\n"
                f"    ),\n"
                f"    {expand_name} = Table.ExpandTableColumn(\n"
                f"        {merge_name},\n"
                f"        \"{dim_table}\",\n"
                f"        {{\"{dim_value_col}\"}},\n"
                f"        {{\"{alias}\"}}\n"
                f"    ),\n"
                f"    {fill_name} = Table.ReplaceValue(\n"
                f"        {expand_name},\n"
                f"        null,\n"
                f"        \"{default_val}\",\n"
                f"        Replacer.ReplaceValue,\n"
                f"        {{\"{alias}\"}}\n"
                f"    )"
            )
            current_step = fill_name
        return transform_steps, current_step

    def _build_inline_helper_binding(self, helper_name: str, helper_table: Dict[str, Any], seen: Optional[set] = None) -> tuple[str, str]:
        seen = seen or set()
        binding_name = re.sub(r'[^A-Za-z0-9_]', '_', helper_name) or "HelperQuery"
        if binding_name in seen:
            return "", binding_name
        seen.add(binding_name)

        prelude = ""
        source_name = helper_table.get("source_path")
        source_table = self.all_tables_list.get(source_name)
        if (
            helper_table.get("source_type") == "resident"
            and source_table
            and source_table.get("options", {}).get("is_helper_table")
        ):
            dependency_binding, dependency_name = self._build_inline_helper_binding(
                f"HelperSource_{source_name}",
                source_table,
                seen,
            )
            prelude += dependency_binding
            self._inline_binding_overrides[source_name] = dependency_name

        helper_options = dict(helper_table.get("options", {}))
        helper_options.pop("is_join", None)
        helper_options.pop("join_table", None)
        helper_options.pop("join_type", None)
        helper_options.pop("is_keep", None)
        helper_options.pop("keep_table", None)
        helper_options.pop("post_join_loads", None)
        helper_options.pop("post_keep_loads", None)
        helper_table_for_binding = dict(helper_table)
        helper_table_for_binding["options"] = helper_options

        # Deferred resident JOIN/KEEP operations that read from a dropped helper table
        # should still use the JOIN/KEEP load's own projected fields and transforms,
        # but must not carry the bogus dropped-resident fallback that would force a
        # fake file lookup like "employee_summary".
        resident_source_name = helper_table.get("source_path")
        resident_source_table = self.all_tables_list.get(resident_source_name)
        if (
            helper_table.get("source_type") == "resident"
            and resident_source_table
            and resident_source_table.get("options", {}).get("is_helper_table")
        ):
            helper_table_for_binding["options"].pop("is_dropped_resident", None)
            helper_table_for_binding["options"].pop("raw_source_path", None)
            helper_table_for_binding["options"].pop("resident_source", None)

        helper_expr, _ = self._dispatch(
            helper_table_for_binding,
            getattr(self, "_current_base_path", "[DataSourcePath]"),
            getattr(self, "_current_connection_string", None),
        )
        binding = prelude + f",\n    {binding_name} =\n{_indent_block(helper_expr, 8)}"
        return binding, binding_name

    def _detect_and_apply_post_joins(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        post_join_loads = table.get("options", {}).get("post_join_loads", [])
        if not post_join_loads:
            return "", prev_step

        transform_steps = ""
        current_step = prev_step
        current_table_columns = {col["name"] for col in self.resolve_output_columns(table)}

        for idx, join_load in enumerate(post_join_loads, start=1):
            helper_binding, helper_name = self._build_inline_helper_binding(
                f"JoinSource_{table.get('name', 'Table')}_{idx}",
                join_load,
            )
            transform_steps += helper_binding

            join_fields = [
                _field_output_name(field)
                for field in join_load.get("fields", [])
                if _field_output_name(field) and _field_output_name(field) != "*"
            ]
            shared_candidates = [name for name in join_fields if name in current_table_columns]
            join_key = _select_join_key(shared_candidates)
            if not join_key and join_fields:
                join_key = _select_join_key(join_fields)
            if not join_key:
                continue

            expanded_columns = [col["name"] for col in self.resolve_output_columns(join_load) if col["name"] != join_key]
            merge_step = f"#\"Joined Helper {idx}\""
            transform_steps += (
                f",\n    {merge_step} = Table.NestedJoin(\n"
                f"        {current_step},\n"
                f"        {{\"{join_key}\"}},\n"
                f"        {helper_name},\n"
                f"        {{\"{join_key}\"}},\n"
                f"        \"{helper_name}\",\n"
                f"        JoinKind.LeftOuter\n"
                f"    )"
            )
            current_step = merge_step

            if expanded_columns:
                cols_literal = ", ".join(f'\"{col}\"' for col in expanded_columns)
                expand_step = f"#\"Expanded Helper {idx}\""
                transform_steps += (
                    f",\n    {expand_step} = Table.ExpandTableColumn(\n"
                    f"        {current_step},\n"
                    f"        \"{helper_name}\",\n"
                    f"        {{{cols_literal}}},\n"
                    f"        {{{cols_literal}}}\n"
                    f"    )"
                )
                current_step = expand_step

        return transform_steps, current_step

    def _detect_and_apply_post_keeps(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        post_keep_loads = table.get("options", {}).get("post_keep_loads", [])
        if not post_keep_loads:
            return "", prev_step

        transform_steps = ""
        current_step = prev_step
        current_table_columns = {col["name"] for col in self.resolve_output_columns(table)}

        for idx, keep_load in enumerate(post_keep_loads, start=1):
            helper_binding, helper_name = self._build_inline_helper_binding(
                f"KeepSource_{table.get('name', 'Table')}_{idx}",
                keep_load,
            )
            transform_steps += helper_binding

            keep_fields = [
                _field_output_name(field)
                for field in keep_load.get("fields", [])
                if _field_output_name(field) and _field_output_name(field) != "*"
            ]
            shared_candidates = [name for name in keep_fields if name in current_table_columns]
            keep_key = _select_join_key(shared_candidates)
            if not keep_key:
                continue

            filter_step = f"#\"Applied KEEP {idx}\""
            transform_steps += (
                f",\n    {filter_step} = Table.SelectRows(\n"
                f"        {current_step},\n"
                f"        each List.Contains(Table.Column({helper_name}, \"{keep_key}\"), [{keep_key}])\n"
                f"    )"
            )
            current_step = filter_step

        return transform_steps, current_step

    def _detect_and_apply_keep(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        """
        FIX 7 — Handle KEEP statements: INNER KEEP (TableName), LEFT KEEP, etc.
        
        KEEP semantics in Qlik:
        - INNER KEEP (TableName): Keep ONLY fields that exist in TableName  
        - LEFT KEEP (TableName): Keep fields from current table + intersection with TableName
        - RIGHT KEEP (TableName): Keep fields from TableName
        
        In Power Query M:
        - Extract column list from TableName
        - Use Table.SelectColumns() to retain only those columns
        """
        opts = table.get("options", {})
        transformations = opts.get("transformations", [])
        if not isinstance(transformations, list):
            transformations = [transformations] if transformations else []
        
        # Check for explicit KEEP table reference (from parser)
        keep_table = opts.get("keep_table")
        keep_cols = opts.get("keep_columns", [])
        
        # Priority 1: Parser-detected KEEP (Employees) syntax
        if opts.get("is_keep") and keep_table:
            keep_type = opts.get("keep_type", "inner").lower()
            
            # Extract columns from the KEEP table
            keep_table_def = self.all_tables_list.get(keep_table, {})
            if keep_table_def:
                # Extract field names from keep_table definition
                keep_table_fields = keep_table_def.get("fields", [])
                extracted_cols = []
                for f in keep_table_fields:
                    col_name = f.get("alias") or f.get("name", "")
                    if col_name and col_name != "*":
                        extracted_cols.append(_strip_qlik_qualifier(col_name))
                
                if extracted_cols:
                    keep_cols = extracted_cols
                    logger.debug(
                        "[_detect_and_apply_keep] %s KEEP (%s): extracted %d columns",
                        keep_type.upper(), keep_table, len(extracted_cols)
                    )
        
        # Priority 2: Explicit keep_columns from options
        if not keep_cols and ("keep" in transformations or opts.get("is_keep")):
            # Safety: skip if still no columns
            if not opts.get("is_keep"):
                return "", prev_step
            logger.warning(
                "[_detect_and_apply_keep] KEEP detected for '%s' but no keep_table or keep_columns found",
                table.get("name")
            )
            return "", prev_step
        
        if not keep_cols:
            return "", prev_step
        
        cols_list = ", ".join([f'"{col}"' for col in keep_cols])
        transform = (
            f",\n    #\"Kept Columns\" = Table.SelectColumns(\n"
            f"        {prev_step},\n"
            f"        {{{cols_list}}}\n"
            f"    )"
        )
        logger.debug(
            "[_detect_and_apply_keep] Keeping %d columns for table '%s'",
            len(keep_cols), table.get("name")
        )
        return transform, "#\"Kept Columns\""

    # ============================================================
    # TRANSFORMATION ORCHESTRATOR
    # ============================================================

    def _is_null_handler_pattern(self, expr: str, alias: str) -> tuple[bool, str]:
        if not expr or "ISNULL" not in expr.upper():
            return False, ""
        pattern = r"^IF\s*\(\s*IsNull\s*\(\s*(\w+)\s*\)\s*,\s*([^,]+)\s*,\s*\1\s*\)$"
        m = re.match(pattern, expr.strip(), re.IGNORECASE)
        if m:
            col_name = m.group(1).strip()
            replacement = m.group(2).strip().strip('"\'')
            if alias.lower() == col_name.lower():
                return True, replacement
        return False, ""

    def _detect_and_apply_null_fixes(self, fields: List[Dict], prev_step: str) -> tuple[str, str]:
        null_fixes = []
        for f in fields:
            expr = f.get("expression", "").strip()
            alias = f.get("alias", f.get("name", ""))
            is_pattern, replacement_val = self._is_null_handler_pattern(expr, alias)
            if is_pattern:
                null_fixes.append((alias, replacement_val))
        if not null_fixes:
            return "", prev_step
        transform_steps = ""
        current_step = prev_step
        for col_name, replacement in null_fixes:
            step_name = f"#\"Replaced {col_name} nulls\""
            transform_steps += (
                f",\n    {step_name} = Table.ReplaceValue(\n"
                f"        {current_step},\n"
                f"        null,\n"
                f"        {replacement},\n"
                f"        Replacer.ReplaceValue,\n"
                f"        {{\"{col_name}\"}}\n"
                f"    )"
            )
            current_step = step_name
        return transform_steps, current_step

    def _convert_qlik_expr_to_m(self, expr: str) -> str:
        """Convert a Qlik field expression to M Query syntax for Table.AddColumn."""
        if not expr:
            return ""
        result = expr.strip()

        converted_if = _convert_if_expression_to_m(result)
        if converted_if:
            return converted_if

        # Qlik single-quoted literals -> M double-quoted literals.
        result = re.sub(r"'([^']*)'", lambda m: f'"{m.group(1).replace(chr(34), chr(34) * 2)}"', result)

        def _replace_date_hash(match: re.Match[str]) -> str:
            field_name = match.group(1).strip()
            date_format = _convert_qlik_date_format_to_m(match.group(2).strip())
            if date_format:
                return f'Date.FromText([{field_name}], [Format = "{date_format}"])'
            return f'Date.FromText([{field_name}])'

        result = re.sub(
            r"Date#\s*\(\s*\[?([^\]\),]+)\]?\s*,\s*[\"']([^\"']*)[\"']\s*\)",
            _replace_date_hash,
            result,
            flags=re.IGNORECASE,
        )

        # Qlik Date() is usually a formatting/casting wrapper; keep the parsed date value.
        result = re.sub(r'(?<!\.)Date\s*\(', 'Date.From(', result, flags=re.IGNORECASE)

        # Convert date-part and rounding functions to M equivalents.
        result = re.sub(r'(?<!\.)Upper\s*\(', 'Text.Upper(', result, flags=re.IGNORECASE)
        result = re.sub(r'(?<!\.)Lower\s*\(', 'Text.Lower(', result, flags=re.IGNORECASE)
        result = re.sub(r'(?<!\.)Trim\s*\(', 'Text.Trim(', result, flags=re.IGNORECASE)
        result = re.sub(r'(?<!\.)Year\s*\(', 'Date.Year(', result, flags=re.IGNORECASE)
        result = re.sub(r'(?<!\.)Month\s*\(', 'Date.Month(', result, flags=re.IGNORECASE)
        result = re.sub(r'(?<!\.)Day\s*\(', 'Date.Day(', result, flags=re.IGNORECASE)
        # Ceil(x) → Number.RoundUp(x)
        result = re.sub(r'(?<!\.)Ceil\s*\(', "Number.RoundUp(", result, flags=re.IGNORECASE)
        # Floor(x) → Number.RoundDown(x)
        result = re.sub(r'(?<!\.)Floor\s*\(', "Number.RoundDown(", result, flags=re.IGNORECASE)

        # Simple Qlik text concatenation like 'Q' & Ceil(...) needs explicit text conversion in M.
        if "&" in result:
            concat_parts = [part.strip() for part in result.split("&", 1)]
            if len(concat_parts) == 2 and concat_parts[0].startswith('"') and not concat_parts[1].startswith("Text.From("):
                result = f"{concat_parts[0]} & Text.From({concat_parts[1]})"

        # Wrap remaining bare field names after function replacement.
        result = _wrap_columns_in_brackets(result)

        return result

    def _detect_and_apply_derived_columns(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        """
        Detect Qlik derived-column expressions (Date#, Year, Month, Ceil, &, etc.)
        that are NOT handled by IF/APPLYMAP/GROUP BY, and emit Table.AddColumn M steps.

        This is the critical step that makes columns like full_date, total_hours,
        emp_department_name actually appear in the M expression output so Power BI
        can find them in the rowset at refresh time.

        FIX: Only add columns whose alias is NOT already present in the source CSV
        (i.e., the alias differs from the raw field name AND is not a passthrough).
        """
        fields = table.get("fields", [])
        aggregation_kws = ("SUM(", "COUNT(", "AVG(", "AVERAGE(", "MIN(", "MAX(", "TOTAL(")

        transform_steps = ""
        current_step = prev_step
        seen_aliases: set = set()
        current_available_columns = {
            col["name"].strip().lower()
            for col in self.resolve_output_columns(table)
            if col.get("name")
        }

        for f in fields:
            name = f.get("name", "")
            alias = _strip_qlik_qualifier((f.get("alias") or name).strip())
            expr = f.get("expression", "").strip()
            normalized_name = _normalize_passthrough_reference(name)
            normalized_expr = _normalize_passthrough_reference(expr)

            # Skip: no expression, passthrough, wildcard
            if not expr or expr == name or expr == "*" or not alias:
                continue
            if normalized_expr and normalized_expr in {normalized_name, alias}:
                continue

            expr_upper = expr.upper()

            # Skip IF conditions (handled by _detect_and_apply_if_conditions)
            if expr_upper.startswith("IF(") or expr_upper.startswith("IF ("):
                continue
            # Skip APPLYMAP (handled by _detect_and_apply_join)
            if "APPLYMAP" in expr_upper:
                continue
            # Skip aggregations (handled by _detect_and_apply_groupby)
            if any(kw in expr_upper for kw in aggregation_kws):
                continue

            # Skip duplicates
            if alias.lower() in seen_aliases:
                continue
            seen_aliases.add(alias.lower())

            # Convert expression to M
            m_expr = self._convert_qlik_expr_to_m(expr)
            if not m_expr:
                logger.warning("[derived_cols] Could not convert expr for '%s': %s", alias, expr)
                continue

            # Infer return type
            expr_u = expr.upper()
            if "&" in expr:
                type_spec = ", type text"
            elif any(kw in expr_u for kw in ("YEAR(", "MONTH(", "DAY(", "CEIL(", "FLOOR(", "ROUND(")):
                type_spec = ", type number"
            elif any(kw in expr_u for kw in ("DATE#", "DATE(", "MAKEDATE", "TODAY(", "NOW(")):
                type_spec = ", type date"
            elif re.search(r'[\+\-\*\/]', expr) and not re.search(r'&', expr):
                type_spec = ", type number"
            else:
                type_spec = ", type text"

            safe_alias = _escape_m_string(alias)
            referenced_columns = {
                _strip_qlik_qualifier(ref).strip().lower()
                for ref in _extract_expression_column_references(expr)
                if ref.strip()
            }
            if (
                alias.lower() == normalized_name.lower()
                and normalized_name.lower() in referenced_columns
                and _current_step_exposes_column(current_step, current_available_columns, alias)
            ):
                replacement_steps, replacement_final = _build_replace_existing_column_steps(
                    current_step,
                    alias,
                    m_expr,
                    type_spec,
                )
                transform_steps += replacement_steps
                current_step = replacement_final
                logger.info(
                    "[derived_cols] Table '%s': Replaced existing column '%s' from expr: %s → %s",
                    table.get("name", "?"), alias, expr[:60], m_expr[:60]
                )
                continue

            step_name = f"#\"Added {safe_alias}\""
            transform_steps += (
                f",\n    {step_name} = Table.AddColumn(\n"
                f"        {current_step},\n"
                f"        \"{safe_alias}\",\n"
                f"        each {m_expr}{type_spec}\n"
                f"    )"
            )
            current_step = step_name
            logger.info(
                "[derived_cols] Table '%s': Added column '%s' from expr: %s → %s",
                table.get("name", "?"), alias, expr[:60], m_expr[:60]
            )
            current_available_columns.add(alias.lower())

        return transform_steps, current_step

    def _apply_all_transformations(self, table: Dict[str, Any], prev_step: str) -> tuple[str, str]:
        """
        FIX E — Master method applies all transformations in the correct order.
        Order: null fixes → concatenate → IF/WHERE → derived columns → group_by → join → keep

        Each step is wrapped in try/except for safe fallback.
        """
        fields = table.get("fields", [])
        current_step = prev_step
        all_transforms = ""

        try:
            transform, current_step = self._detect_and_apply_null_fixes(fields, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] null fixes failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_concatenate(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] concatenate failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_if_conditions(table, current_step, fields)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] IF conditions failed: %s", e)

        # 🔥 CRITICAL: Apply derived columns (Date#, Year, Month, APPLYMAP, Ceil, &)
        # BEFORE GROUP BY so these columns are available as group-by targets if needed.
        # Without this, columns like full_date, total_hours, emp_department_name are
        # declared in the BIM but never produced by the M expression → refresh crash.
        try:
            transform, current_step = self._detect_and_apply_derived_columns(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] derived columns failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_groupby(table, current_step, fields)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] GROUP BY failed: %s", e)

        try:
            # FIX D/E: join is now wired and called here
            transform, current_step = self._detect_and_apply_join(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] JOIN failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_post_joins(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] deferred JOIN failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_post_keeps(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] deferred KEEP failed: %s", e)

        try:
            transform, current_step = self._detect_and_apply_keep(table, current_step)
            all_transforms += transform
        except Exception as e:
            logger.warning("[_apply_all_transformations] KEEP failed: %s", e)

        return all_transforms, current_step

    # ============================================================
    # INLINE
    # ============================================================

    def _m_inline(self, table, base_path, _cs):
        opts   = table.get("options", {})
        fields = table["fields"]
        headers = opts.get("inline_headers", [f["name"] for f in fields if f["name"] != "*"])
        rows = opts.get("inline_rows_all") or opts.get("inline_sample", [])
        type_defs = ", ".join(
            f"{_sanitize_col_name(_strip_qlik_qualifier(h))} = "
            f"{_m_type_for_table(next((f['type'] for f in fields if f['name'] == h), 'string'), h)}"
            for h in headers
        )
        row_strs = []
        for row in rows:
            vals = []
            for h in headers:
                v = str(row.get(h, ""))
                v = v.strip("'").replace('"', '""').replace("\n", " ").replace("\r", "")
                v = " ".join(v.split())
                vals.append(f'"{v}"')
            row_strs.append("{" + ", ".join(vals) + "}")
        rows_m = (
            "{\n        " + ",\n        ".join(row_strs) + "\n    }"
            if row_strs else "{}"
        )
        rows_m = re.sub(r',\s*\n\s*"', ', "', rows_m)
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
        table_name = table.get("name", "")
        delimiter = opts.get("delimiter", ",")
        encoding  = 65001
        enc_str   = opts.get("encoding", "")
        if enc_str:
            enc_map = {"UTF-8": 65001, "UTF8": 65001, "UTF-16": 1200, "UTF16": 1200}
            encoding = enc_map.get(enc_str.upper().replace("-", ""), encoding)

        base_transform, transform_final = self._apply_types(fields, "Headers", table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Headers")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Headers"
        
        # Detect LOAD * table (no explicit fields)
        is_load_star = not fields or all(f.get("name") == "*" for f in fields)
        
        logger.debug(
            "[_m_csv] '%s': LOAD_STAR=%s, base_transform len=%d, extra_transform len=%d, combined has TypedTable=%s",
            table_name, is_load_star, len(base_transform), len(extra_transform), "TypedTable" in combined_transform
        )
        
        # ✅ FIX: For LOAD * tables, ALWAYS ensure explicit column metadata at end
        # This guarantees TypedTable step exists in M query for Power BI extraction
        if is_load_star and not "TransformColumnTypes" in combined_transform:
            # No schema in transforms — use _build_explicit_schema_step which has dynamic fallback
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_csv] '%s': LOAD * detected - injected schema. TypedTable now in query=%s",
                table_name, "TypedTable" in combined_transform
            )
        elif not base_transform.strip() or (extra_transform and not "TransformColumnTypes" in combined_transform):
            # Also inject for non-LOAD* tables if no schema generated
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_csv] '%s': injected schema (non-LOAD*). TypedTable in query=%s",
                table_name, "TypedTable" in combined_transform
            )
        filename_only = path.rsplit("/", 1)[-1] if "/" in path else path

        if _is_sharepoint_url(base_path):
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
            sp_transform, sp_final = self._apply_types_as_is(fields, "Headers")
            extra_sp_transform, extra_sp_final = self._apply_all_transformations(table, sp_final or "Headers")
            combined_sp_transform = sp_transform + extra_sp_transform
            final_sp = extra_sp_final or sp_final or "Headers"

            # ✅ Fix 3: Determine if this is a LOAD * table (no explicit field definitions)
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            # For LOAD * tables: always inject schema (explicit or dynamic fallback)
            # For explicit LOAD tables: only inject if no transform was generated
            if is_load_star or (not combined_sp_transform.strip() and final_sp == "Headers"):
                combined_sp_transform, final_sp = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_sharepoint_m(
                site_url=site_url, filename=filename, folder_path=folder_path,
                delimiter=delimiter, encoding=encoding,
                transform_step=combined_sp_transform, final_step=final_sp or "Headers",
            )
        elif _is_s3_url(base_path):
            sp_transform, sp_final = self._apply_types_as_is(fields, "Headers")
            extra_sp_transform, extra_sp_final = self._apply_all_transformations(table, sp_final or "Headers")
            combined_sp_transform = sp_transform + extra_sp_transform
            final_sp = extra_sp_final or sp_final or "Headers"

            # ✅ Fix 3: Same fix for S3 LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_sp_transform.strip() and final_sp == "Headers"):
                combined_sp_transform, final_sp = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_s3_m(bucket_url=base_path, filename=filename_only,
                            delimiter=delimiter, encoding=encoding,
                            transform_step=combined_sp_transform, final_step=final_sp)
        elif _is_azure_blob_url(base_path):
            sp_transform, sp_final = self._apply_types_as_is(fields, "Headers")
            extra_sp_transform, extra_sp_final = self._apply_all_transformations(table, sp_final or "Headers")
            combined_sp_transform = sp_transform + extra_sp_transform
            final_sp = extra_sp_final or sp_final or "Headers"

            # ✅ Fix 3: Same fix for Azure Blob LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_sp_transform.strip() and final_sp == "Headers"):
                combined_sp_transform, final_sp = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_azure_blob_m(container_url=base_path, filename=filename_only,
                                    delimiter=delimiter, encoding=encoding,
                                    transform_step=combined_sp_transform, final_step=final_sp)
        elif _is_web_url(base_path):
            clean_bp = base_path.strip().strip('"').strip("'").rstrip("/")
            file_url = f"{clean_bp}/{path}" if path else clean_bp
            
            # 🔥 CRITICAL FIX: Inject schema for LOAD * tables (Web URL path)
            if not combined_transform.strip() and (final or "Headers") == "Headers":
                combined_transform = (
                    ",\n"
                    "    Columns = Table.ColumnNames(Headers),\n"
                    "    TypedTable = Table.TransformColumnTypes(\n"
                    "        Headers,\n"
                    "        List.Transform(Columns, each {_, type text})\n"
                    "    )"
                )
                final = "TypedTable"
                logger.info("[_m_csv] '%s': injected DYNAMIC schema for Web URL (no explicit cols)", table_name)
            
            m = (
                f"let\n"
                f"    Source = Web.Contents(\"{file_url}\"),\n"
                f"    CsvData = Csv.Document(\n"
                f"        Source,\n"
                f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
                f"    ),\n"
                f"    Headers = Table.PromoteHeaders(CsvData, [PromoteAllScalars=true])"
                f"{combined_transform}\n"
                f"in\n"
                f"    {final or 'Headers'}"
            )
        else:
            # ✅ FIX 4: Ensure fallback for LOAD * tables in local file path too
            if not combined_transform.strip() and (final or "Headers") == "Headers":
                # No schema generated — inject dynamic fallback
                combined_transform = self._apply_dynamic_schema("Headers")
                final = "TypedTable"
                logger.info(
                    "[_m_csv] '%s': No transformations - injected dynamic fallback schema (local file)",
                    table_name
                )
            
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
                f"    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
                f"{combined_transform}\n"
                f"in\n"
                f"    {final or 'Headers'}"
            )
        return m, f"CSV source: {path}"

    # ============================================================
    # SHAREPOINT HELPERS
    # ============================================================

    def _get_sharepoint_parts(self, base_path: str, source_path: str, opts: dict):
        raw = base_path.strip().strip('"').strip("'").rstrip("/")
        if "/Shared Documents" in raw:
            site_url = raw.split("/Shared Documents")[0].rstrip("/")
            folder_path = raw + "/"
        elif "/sites/" in raw:
            parts = raw.split("/")
            site_url = "/".join(parts[:5]) if len(parts) >= 5 else raw
            folder_path = f"{site_url}/Shared Documents/"
        else:
            site_url = raw
            folder_path = f"{site_url}/Shared Documents/"
        sp_subfolder = opts.get("sp_subfolder", "")
        if sp_subfolder:
            folder_path = f"{site_url}/Shared Documents/{sp_subfolder.strip('/')}/"
        elif "/" in source_path:
            sub = source_path.rsplit("/", 1)[0]
            folder_path = f"{site_url}/Shared Documents/{sub}/"
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
        table_name = table.get("name", "")
        base_transform, transform_final = self._apply_types(fields, "Headers", table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Headers")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Headers"
        
        # Detect LOAD * table (no explicit fields)
        is_load_star = not fields or all(f.get("name") == "*" for f in fields)
        
        # ✅ FIX: For LOAD * tables, ALWAYS ensure explicit column metadata at end
        # This guarantees TypedTable step exists in M query for Power BI extraction
        if is_load_star and not "TransformColumnTypes" in combined_transform:
            # No schema in transforms — use _build_explicit_schema_step which has dynamic fallback
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_excel] '%s': LOAD * detected - injected schema",
                table_name
            )
        elif not base_transform.strip() or (extra_transform and not "TransformColumnTypes" in combined_transform):
            # Also inject for non-LOAD* tables if no schema generated
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_excel] '%s': injected schema (non-LOAD*)",
                table_name
            )
        if _is_sharepoint_url(base_path):
            opts = table.get("options", {})
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, path, opts)
            sp_transform, sp_final = self._apply_types_as_is(fields, "Headers")
            extra_sp_transform, extra_sp_final = self._apply_all_transformations(table, sp_final or "Headers")
            combined_sp_transform = sp_transform + extra_sp_transform
            final_sp = extra_sp_final or sp_final or "Headers"
            m = (
                f"let\n"
                f"    SiteUrl = \"{site_url}\",\n"
                f"    Source = SharePoint.Files(SiteUrl, [ApiVersion = 15]),\n"
                f"    Filtered = Table.SelectRows(\n"
                f"        Source,\n"
                f"        each\n"
                f"            Text.EndsWith(Text.Lower([Name]), \"{filename.lower()}\") and\n"
                f"            Text.Contains(Text.Lower([Folder Path]), \"shared documents\")\n"
                f"    ),\n"
                f"    FileBinary = if Table.RowCount(Filtered) > 0\n"
                f"        then Filtered{{0}}[Content]\n"
                f"        else error \"File {filename} not found in SharePoint\",\n"
                f"    ExcelData = Excel.Workbook(FileBinary, null, true),\n"
                f"    SheetData = ExcelData{{[Item=\"{sheet}\", Kind=\"Sheet\"]}}[Data],\n"
                f"    Headers = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
                f"{combined_sp_transform}\n"
                f"in\n"
                f"    {final_sp or 'Headers'}"
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
                f"    Headers = Table.PromoteHeaders(SheetData, [PromoteAllScalars=true])"
                f"{combined_transform}\n"
                f"in\n"
                f"    {final or 'Headers'}"
            )
        return m, f"Excel source: {path}, sheet: {sheet}"

    # ============================================================
    # JSON / XML / PARQUET / QVD / SQL / PLACEHOLDER
    # ============================================================

    def _m_json(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]
        expand_cols = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
        col_list = ", ".join(f'"{c}"' for c in expand_cols)
        base_transform, transform_final = self._apply_types(fields, "Expanded")
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Expanded")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Expanded"
        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Json.Document(File.Contents(FilePath)),\n"
            f"    ToTable = Table.FromList(Source, Splitter.SplitByNothing(), null, null, ExtraValues.Error),\n"
            f"    Expanded = Table.ExpandRecordColumn(ToTable, \"Column1\",\n"
            f"        {{{col_list}}},\n"
            f"        {{{col_list}}}\n"
            f"    )"
            f"{combined_transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"JSON source: {path}."

    def _m_xml(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]
        base_transform, transform_final = self._apply_types(fields, "Source")
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Source")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Source"
        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Xml.Tables(File.Contents(FilePath))"
            f"{combined_transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"XML source: {path}."

    def _m_parquet(self, table, base_path, _cs):
        path   = _normalize_path(table.get("source_path", ""))
        fields = table["fields"]
        base_transform, transform_final = self._apply_types(fields, "Source")
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Source")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Source"
        m = (
            f"let\n"
            f"    FilePath = {base_path} & \"/{path}\",\n"
            f"    Source = Parquet.Document(File.Contents(FilePath))"
            f"{combined_transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"Parquet source: {path}."

    def _m_qvd(self, table, base_path, _cs):
        path     = _normalize_path(table.get("source_path", ""))
        csv_path = re.sub(r"\.qvd$", ".csv", path, flags=re.IGNORECASE)
        fields   = table["fields"]
        table_name = table.get("name", "")
        base_transform, transform_final = self._apply_types(fields, "Headers", table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Headers")
        combined_transform = base_transform + extra_transform
        final = extra_final or transform_final or "Headers"
        
        # Detect LOAD * table (no explicit fields)
        is_load_star = not fields or all(f.get("name") == "*" for f in fields)
        
        # ✅ FIX: For LOAD * tables, ALWAYS ensure explicit column metadata at end
        # This guarantees TypedTable step exists in M query for Power BI extraction
        if is_load_star and not "TransformColumnTypes" in combined_transform:
            # No schema in transforms — use _build_explicit_schema_step which has dynamic fallback
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_qvd] '%s': LOAD * detected - injected schema",
                table_name
            )
        elif not base_transform.strip() or (extra_transform and not "TransformColumnTypes" in combined_transform):
            # Also inject for non-LOAD* tables if no schema generated
            schema_step, final = self._build_explicit_schema_step(table_name, final or "Headers")
            combined_transform += schema_step
            logger.info(
                "[_m_qvd] '%s': injected schema (non-LOAD*)",
                table_name
            )
        if _is_sharepoint_url(base_path):
            opts = table.get("options", {})
            opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, csv_path, opts)
            sp_transform, sp_final = self._apply_types_as_is(fields, "Headers")
            extra_sp_transform, extra_sp_final = self._apply_all_transformations(table, sp_final or "Headers")
            combined_sp_transform = sp_transform + extra_sp_transform
            final_sp = extra_sp_final or sp_final or "Headers"

            # ✅ Fix 3: Same fix for QVD LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_sp_transform.strip() and final_sp == "Headers"):
                combined_sp_transform, final_sp = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_sharepoint_m(
                site_url=site_url, filename=filename, folder_path=folder_path,
                delimiter=",", encoding=65001,
                transform_step=combined_sp_transform, final_step=final_sp or "Headers",
                is_qvd=True,
            )
        else:
            # ✅ FIX 4: Ensure fallback for LOAD * tables in local QVD file path too
            if not combined_transform.strip() and (final or "Headers") == "Headers":
                # No schema generated — inject dynamic fallback
                combined_transform = self._apply_dynamic_schema("Headers")
                final = "TypedTable"
                logger.info(
                    "[_m_qvd] '%s': No transformations - injected dynamic fallback schema (local file)",
                    table_name
                )
            
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{csv_path}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{csv_path}"'
            m = (
                f"let\n"
                f"    FilePath = {path_expr},\n"
                f"    Source = Csv.Document(\n"
                f"        File.Contents(FilePath),\n"
                f"        [Delimiter=\",\", Encoding=65001]\n"
                f"    ),\n"
                f"    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
                f"{combined_transform}\n"
                f"in\n"
                f"    {final or 'Headers'}"
            )
        return m, f"QVD fallback (pre-convert to CSV): {path}"

    # ============================================================
    # RESIDENT
    # ============================================================

    def _m_resident(self, table, base_path, _cs):
        opts         = table.get("options", {})
        source_table = table.get("source_path", "UnknownTable")
        fields       = table["fields"]

        source_table_def = self.all_tables_list.get(source_table)
        if source_table_def and source_table_def.get("options", {}).get("concatenate_sources"):
            return self._m_resident_with_concatenation(table, base_path, fields, source_table_def)

        if opts.get("is_dropped_resident"):
            return self._m_resident_inlined(table, base_path, opts, fields, source_table)

        return self._m_resident_reference(table, fields, source_table)

    def _m_resident_inlined(self, table, base_path, opts, fields, source_table):
        raw_source_path = opts.get("raw_source_path", "")
        delimiter = opts.get("delimiter", ",")
        encoding  = 65001
        table_name = table.get("name", "")
        base_transform, transform_final = self._apply_types(fields, "Headers", table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or "Headers")
        combined_transform = base_transform + extra_transform
        last_step = extra_final or transform_final or "Headers"

        if _is_sharepoint_url(base_path):
            inline_opts = dict(opts)
            inline_opts["table_name"] = table.get("name", "Table")
            site_url, folder_path, filename = self._get_sharepoint_parts(base_path, raw_source_path, inline_opts)

            # ✅ Fix 3: Same fix for RESIDENT inlined LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_transform.strip() and last_step == "Headers"):
                combined_transform, last_step = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_sharepoint_m(
                site_url=site_url, filename=filename, folder_path=folder_path,
                delimiter=delimiter, encoding=encoding,
                transform_step=combined_transform, final_step=last_step,
            )
        elif _is_s3_url(base_path):
            filename_only = raw_source_path.rsplit("/", 1)[-1] if "/" in raw_source_path else raw_source_path

            # ✅ Fix 3: Same fix for S3 LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_transform.strip() and last_step == "Headers"):
                combined_transform, last_step = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_s3_m(bucket_url=base_path, filename=filename_only,
                            delimiter=delimiter, encoding=encoding,
                            transform_step=combined_transform, final_step=last_step)
        elif _is_azure_blob_url(base_path):
            filename_only = raw_source_path.rsplit("/", 1)[-1] if "/" in raw_source_path else raw_source_path

            # ✅ Fix 3: Same fix for Azure Blob LOAD * tables
            is_load_star = (
                not fields or
                all(f.get("name") == "*" for f in fields) or
                all(f.get("extracted_from") == "qlik_fields_map" for f in fields if f.get("name") != "*")
            )
            if is_load_star or (not combined_transform.strip() and last_step == "Headers"):
                combined_transform, last_step = self._build_explicit_schema_step(
                    table.get("name", ""), "Headers"
                )

            m = _build_azure_blob_m(container_url=base_path, filename=filename_only,
                                    delimiter=delimiter, encoding=encoding,
                                    transform_step=combined_transform, final_step=last_step)
        else:
            if base_path.strip().startswith("["):
                path_expr = f"{base_path} & \"/{raw_source_path}\""
            else:
                clean_bp = base_path.strip().strip('"').strip("'")
                path_expr = f'"{clean_bp}/{raw_source_path}"'
            m = (
                f"let\n"
                f"    FilePath = {path_expr},\n"
                f"    Source = Csv.Document(\n"
                f"        File.Contents(FilePath),\n"
                f"        [Delimiter=\"{delimiter}\", Encoding={encoding}, QuoteStyle=QuoteStyle.Csv]\n"
                f"    ),\n"
                f"    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"
                f"{combined_transform}\n"
                f"in\n"
                f"    {last_step}"
            )
        return m, (
            f"RESIDENT '{source_table}' was a dropped intermediate table. "
            f"CSV inlined from '{raw_source_path}'."
        )

    def _m_resident_reference(self, table, fields, source_table):
        table_name = table.get("name", "")
        source_reference = self._inline_binding_overrides.get(source_table, source_table)
        has_wildcard = any(f.get("name") == "*" for f in fields)
        selected = []
        if not has_wildcard:
            seen_selected = set()
            for f in fields:
                name = f.get("name", "")
                if name == "*":
                    continue
                alias = f.get("alias") or name
                expr = (f.get("expression", "") or name).strip()
                if alias == name and expr == name:
                    key = alias.lower()
                    if key not in seen_selected:
                        seen_selected.add(key)
                        selected.append(alias)
                for ref in _extract_expression_column_references(expr):
                    key = ref.lower()
                    if key not in seen_selected:
                        seen_selected.add(key)
                        selected.append(ref)
        if selected:
            select_step = (
                f",\n    Selected = Table.SelectColumns({source_reference},\n"
                f"        {{{', '.join(chr(34) + c + chr(34) for c in selected)}}}\n"
                f"    )"
            )
            intermediate = "Selected"
        else:
            select_step = ""
            intermediate = source_reference
        base_transform, transform_final = self._apply_types(fields, intermediate, table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or intermediate)
        combined_transform = select_step + base_transform + extra_transform
        final = extra_final or transform_final or intermediate
        
        # ✅ FIX: ALWAYS ensure explicit column metadata at end for Power BI schema extraction
        # Even if transformations produced no TransformColumnTypes, add one now
        if not base_transform.strip() or (extra_transform and not "TransformColumnTypes" in combined_transform):
            # No explicit columns in transforms — resolve from qlik_fields_map or parser metadata
            resolved_cols = self.resolve_output_columns(table)
            if resolved_cols:
                col_list = ", ".join([
                    f'{{"{c["name"]}", {_bim_datatype_to_m_type(c.get("dataType", "string"), c["name"])}}}'
                    for c in resolved_cols
                ])
                schema_step = (
                    f",\n    TypedTable = Table.TransformColumnTypes(\n"
                    f"        {final},\n"
                    f"        {{\n        {col_list}\n        }}\n"
                    f"    )"
                )
                combined_transform += schema_step
                final = "TypedTable"
                logger.info(
                    "[_m_resident_reference] '%s': injected explicit schema with %d columns",
                    table_name, len(resolved_cols)
                )
        
        m = (
            f"let\n"
            f"    {source_table} = {source_reference}"
            f"{combined_transform}\n"
            f"in\n"
            f"    {final}"
        )
        return m, f"RESIDENT load from '{source_table}'."

    def _m_resident_with_concatenation(self, table, base_path, fields, source_table_def):
        """
        FIX B applied to RESIDENT-of-concatenation:
        Uses _build_safe_combine instead of naive combine.
        """
        source_table_name = source_table_def.get("name", "UnknownTable")
        table_name = table.get("name", "")
        concat_sources = source_table_def.get("options", {}).get("concatenate_sources", [])

        if not concat_sources:
            return self._m_resident_reference(table, fields, source_table_name)

        # FIX B: use schema-aligned combine
        combined_m, combined_step = self._build_safe_combine(concat_sources, fields, base_path)

        if not combined_m:
            return self._m_resident_reference(table, fields, source_table_name)

        selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
        if selected:
            select_step = (
                f",\n    Selected = Table.SelectColumns({combined_step},\n"
                f"        {{{', '.join(chr(34) + c + chr(34) for c in selected)}}}\n"
                f"    )"
            )
            intermediate = "Selected"
        else:
            select_step = ""
            intermediate = combined_step

        base_transform, transform_final = self._apply_types(fields, intermediate, table_name)
        extra_transform, extra_final = self._apply_all_transformations(table, transform_final or intermediate)
        combined_transform = base_transform + select_step + extra_transform
        final = extra_final or transform_final or intermediate
        
        # ✅ FIX: ALWAYS ensure explicit column metadata at end for Power BI schema extraction
        # Even if transformations produced no TransformColumnTypes, add one now
        if not base_transform.strip() or (extra_transform and not "TransformColumnTypes" in combined_transform):
            # No explicit columns in transforms — resolve from qlik_fields_map or parser metadata
            resolved_cols = self.resolve_output_columns(table)
            if resolved_cols:
                col_list = ", ".join([
                    f'{{"{c["name"]}", {_bim_datatype_to_m_type(c.get("dataType", "string"), c["name"])}}}'
                    for c in resolved_cols
                ])
                schema_step = (
                    f",\n    TypedTable = Table.TransformColumnTypes(\n"
                    f"        {final},\n"
                    f"        {{\n        {col_list}\n        }}\n"
                    f"    )"
                )
                combined_transform += schema_step
                final = "TypedTable"
                logger.info(
                    "[_m_resident_with_concatenation] '%s': injected explicit schema with %d columns",
                    table_name, len(resolved_cols)
                )

        # Inject the combine block into a let..in expression
        # combined_m already ends with "in\n    SafeCombined"
        # We need to insert additional steps before the final "in"
        if combined_transform:
            combined_m_body = combined_m.rsplit("in\n", 1)[0]
            m = combined_m_body + combined_transform + f"\nin\n    {final}"
        else:
            m = combined_m

        return m, f"RESIDENT of concatenated table '{source_table_name}' with {len(concat_sources)} sources (schema-aligned)."

    # ============================================================
    # SQL
    # ============================================================

    def _m_sql(self, table, base_path, connection_string):
        source_table = table.get("source_path", "dbo.UnknownTable")
        fields       = table["fields"]
        conn         = connection_string or "[OdbcConnectionString]"
        selected = [f.get("alias") or f["name"] for f in fields if f["name"] != "*"]
        col_list = ", ".join(f"[{c}]" for c in selected) if selected else "*"
        transform, final = self._apply_types(fields, "Source", table.get("name", ""))
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
            f"    Source = #table(\n"
            f"        type table [{type_defs}],\n"
            f"        {{}}\n"
            f"    )\n"
            f"in\n"
            f"    Source"
        )
        return m, f"Source type '{table.get('source_type')}' requires manual configuration."


# ─────────────────────────────────────────────────────────────────────────────
# HELPER: M type string → BIM dataType string
# ─────────────────────────────────────────────────────────────────────────────

def _m_type_to_bim_datatype(m_type: str) -> str:
    """Convert an M type annotation string to a BIM-compatible dataType string."""
    mapping = {
        "type text":     "string",
        "type number":   "double",
        "Int64.Type":    "int64",
        "type date":     "dateTime",
        "type datetime": "dateTime",
        "type time":     "dateTime",
        "type logical":  "boolean",
        "type any":      "string",
    }
    return mapping.get(m_type, "string")


def _bim_datatype_to_m_type(data_type: str, col_name: str = "") -> str:
    mapping = {
        "string": "type text",
        "double": "type number",
        "int64": "Int64.Type",
        "dateTime": "type datetime",
        "boolean": "type logical",
    }
    if data_type in mapping:
        return mapping[data_type]
    return _infer_type_from_name(col_name)


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