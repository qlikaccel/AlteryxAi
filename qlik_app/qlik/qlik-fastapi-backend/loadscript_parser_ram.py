# """
# Qlik LoadScript Parser Module  (PATCHED v2)

# Parses the loadscript and extracts meaningful components with detailed logging.

# Patches applied over v1:
#   ✅ Fix 1: lib:// comment stripping bug — // inside lib:// was stripped as a comment
#   ✅ Fix 2: String-aware semicolon split — naive split broke on delimiter is ','
#              and Date(x, 'YYYY-MM-DD') format strings
#   ✅ Fix 3: INLINE block detection and parsing
#   ✅ Fix 4: QVD load source type detection (was falling through as generic file)
#   ✅ Fix 5: RESIDENT load detection
#   ✅ Fix 6: Field type inference (date, number, boolean, string) from expressions
#   ✅ Fix 7: Duplicate load_statement suppression (3 overlapping patterns caused 3x dupes)
#   ✅ Fix 8: Table source_type and source_path now populated per table
#   ✅ Fix 9: raw_script always present in parse() return dict (required by simple_mquery_generator)
#   ✅ Fix 10: DISTINCT/WHERE/GROUP BY now scoped per-table, not globally duplicated
# """

# import logging
# import re
# from typing import Dict, List, Any, Optional, Tuple
# from datetime import datetime

# # Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
#     datefmt='%Y-%m-%d %H:%M:%S'
# )
# logger = logging.getLogger(__name__)


# # ---------------------------------------------------------------------------
# # Internal helpers (module-level, not part of the public class interface)
# # ---------------------------------------------------------------------------

# def _strip_comments_safe(script: str) -> str:
#     """
#     Strip // and /* */ comments WITHOUT mangling lib:// or file:// URLs.

#     Rules:
#       - // is only a comment if NOT preceded by ':'
#       - /* ... */ block comments are always stripped
#     """
#     # Pass 1: block comments
#     script = re.sub(r'/\*.*?\*/', '', script, flags=re.DOTALL)

#     # Pass 2: line comments — walk line by line, skip // preceded by ':'
#     result_lines = []
#     for line in script.split('\n'):
#         out = []
#         in_single = False
#         in_double = False
#         i = 0
#         while i < len(line):
#             ch = line[i]
#             if ch == "'" and not in_double:
#                 in_single = not in_single
#             elif ch == '"' and not in_single:
#                 in_double = not in_double
#             elif (ch == '/' and i + 1 < len(line) and line[i + 1] == '/'
#                   and not in_single and not in_double):
#                 # Only treat as comment if NOT preceded by ':'
#                 if i > 0 and line[i - 1] == ':':
#                     pass  # it's :// — keep it
#                 else:
#                     break  # real comment — truncate line
#             out.append(ch)
#             i += 1
#         result_lines.append(''.join(out))
#     return '\n'.join(result_lines)


# def _split_statements(script: str) -> List[str]:
#     """
#     Split a Qlik script on semicolons, respecting:
#       • single-quoted strings  'abc'
#       • double-quoted strings  "abc"
#       • square brackets        [abc]
#     Returns list of non-empty stripped statement strings.
#     """
#     parts: List[str] = []
#     current: List[str] = []
#     in_single = False
#     in_double = False
#     bracket_depth = 0

#     for ch in script:
#         if ch == "'" and not in_double and bracket_depth == 0:
#             in_single = not in_single
#         elif ch == '"' and not in_single and bracket_depth == 0:
#             in_double = not in_double
#         elif ch == '[' and not in_single and not in_double:
#             bracket_depth += 1
#         elif ch == ']' and not in_single and not in_double:
#             bracket_depth = max(0, bracket_depth - 1)
#         elif ch == ';' and not in_single and not in_double and bracket_depth == 0:
#             stmt = ''.join(current).strip()
#             if stmt:
#                 parts.append(stmt)
#             current = []
#             continue
#         current.append(ch)

#     tail = ''.join(current).strip()
#     if tail:
#         parts.append(tail)
#     return parts


# def _file_source_type(path: str) -> str:
#     """Classify a file path into a source type string."""
#     path_lower = path.lower()
#     if path_lower.endswith('.qvd'):
#         return 'qvd'
#     if path_lower.endswith(('.csv', '.txt', '.tsv', '.tab')):
#         return 'csv'
#     if path_lower.endswith(('.xlsx', '.xls', '.xlsm')):
#         return 'excel'
#     if path_lower.endswith('.json'):
#         return 'json'
#     if path_lower.endswith('.xml'):
#         return 'xml'
#     if path_lower.endswith('.parquet'):
#         return 'parquet'
#     return 'file'


# def _infer_field_type(expression: str) -> str:
#     """
#     Heuristic type inference from a Qlik field expression or bare field name.

#     Priority order:
#       1. Qlik function calls in the expression  (DATE(), NUM(), etc.)
#       2. Field-name suffixes/keywords           (Price, Cost, Date, Year, ID, etc.)
#       3. Arithmetic operators
#       4. Default: string
#     """
#     e = expression.upper()

#     # ── Expression-level inference (function calls) ──────────────────────────
#     if re.search(r'\b(DATE|MAKEDATE|YEAR|MONTH|DAY|TIMESTAMP|TODAY|NOW)\s*\(', e):
#         return 'date'
#     if re.search(r'\b(NUM|INT|FLOOR|CEIL|ROUND|SUM|COUNT|AVG|MIN|MAX|FRAC|MOD)\s*\(', e):
#         return 'number'
#     if re.search(r'\b(TEXT|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|CAT|REPLACE|LEN)\s*\(', e):
#         return 'string'
#     if re.search(r'\b(IF|PICK|MATCH|ALT|NULL)\s*\(', e):
#         return 'mixed'
#     if re.search(r'\bTRUE\b|\bFALSE\b', e):
#         return 'boolean'
#     if re.search(r'[\+\-\*\/]', e) and not re.search(r'[A-Z].*[A-Z]', e):
#         return 'number'

#     # ── Name-based inference (bare field names after aliasing) ───────────────
#     # Strip table qualifier prefix (e.g. "Service_History.ServiceCost" -> "SERVICECOST")
#     name = e.split('.')[-1] if '.' in e else e
#     # Strip brackets
#     name = name.strip('[]#"\'')

#     # Date/time suffixes
#     if re.search(r'(DATE|TIME|TIMESTAMP|CREATED|UPDATED|MODIFIED|DOB|BIRTH)$', name):
#         return 'date'
#     if re.search(r'^(DATE|TIME|TIMESTAMP)', name):
#         return 'date'

#     # Integer — year, count, age, rank, sequence
#     if re.search(r'(YEAR|MONTH|DAY|AGE|COUNT|QTY|QUANTITY|RANK|SEQ|NUM|NBR|NR|NO)$', name):
#         return 'integer'

#     # Decimal — price, cost, amount, revenue, tax, rate, weight, discount
#     if re.search(r'(PRICE|COST|AMOUNT|REVENUE|SALARY|RATE|TOTAL|TAX|DISCOUNT|FEE|WEIGHT|PERCENT|PCT|BUDGET|BALANCE)$', name):
#         return 'number'

#     return 'string'


# def _extract_from_path(statement: str) -> Tuple[str, str]:
#     """
#     Pull the file/table path from a FROM clause.
#     Returns (raw_path, normalized_path_without_lib_prefix).
#     """
#     # Handles [lib://x], 'lib://x', bare lib://x, quoted 'path/file.csv'
#     m = re.search(
#         r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|(\S+))""",
#         statement, re.IGNORECASE
#     )
#     if not m:
#         return '', ''
#     raw = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
#     # Strip Qlik lib:// prefix:  lib://DataFiles/orders.qvd → orders.qvd
#     normalized = re.sub(r'^lib://[^/]+/', '', raw)
#     normalized = re.sub(r'^lib://', '', normalized)
#     return raw, normalized


# def _parse_field_list(field_text: str) -> List[Dict[str, Any]]:
#     """
#     Parse a comma-separated field expression list (the part between LOAD and FROM/INLINE/RESIDENT).
#     Returns list of field dicts with name, expression, alias, type.
#     """
#     if not field_text.strip():
#         return []
#     if field_text.strip() == '*':
#         return [{'name': '*', 'expression': '*', 'alias': None, 'type': 'wildcard'}]

#     fields = []
#     # Smart split on commas (respecting brackets/parens)
#     parts: List[str] = []
#     depth_p = 0
#     depth_b = 0
#     current: List[str] = []
#     for ch in field_text:
#         if ch == '(':
#             depth_p += 1
#         elif ch == ')':
#             depth_p -= 1
#         elif ch == '[':
#             depth_b += 1
#         elif ch == ']':
#             depth_b -= 1
#         elif ch == ',' and depth_p == 0 and depth_b == 0:
#             parts.append(''.join(current).strip())
#             current = []
#             continue
#         current.append(ch)
#     if current:
#         parts.append(''.join(current).strip())

#     for part in parts:
#         part = part.strip()
#         if not part:
#             continue

#         # Detect AS alias
#         alias = None
#         as_m = re.search(
#             r'\bAS\b\s+(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_ ]*))\s*$',
#             part, re.IGNORECASE
#         )
#         if as_m:
#             alias = (as_m.group(1) or as_m.group(2)).strip()
#             expr = part[:as_m.start()].strip()
#         else:
#             expr = part

#         # Bare field name (strip brackets)
#         if expr.startswith('[') and expr.endswith(']'):
#             bare = expr[1:-1]
#         elif re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', expr):
#             bare = expr
#         else:
#             bare = alias or expr  # expression like Qty*Price

#         name = alias or bare
#         data_type = _infer_field_type(expr)

#         fields.append({
#             'name':       name,
#             'expression': expr,
#             'alias':      alias,
#             'type':       data_type,
#             'extracted_from': 'load_statement'
#         })

#     return fields


# def _parse_inline_block(inline_text: str) -> Tuple[List[Dict], Dict]:
#     """Parse INLINE [ header\\nrows… ] content. Returns (fields, options)."""
#     lines = [l.strip() for l in inline_text.splitlines() if l.strip()]
#     if not lines:
#         return [], {}

#     headers = [h.strip() for h in lines[0].split(',')]
#     fields = [{'name': h, 'expression': h, 'alias': None, 'type': 'string',
#                 'extracted_from': 'inline'} for h in headers if h]

#     sample_rows = []
#     for line in lines[1:]:
#         vals = [v.strip() for v in line.split(',')]
#         if vals:
#             sample_rows.append(dict(zip(headers, vals)))

#     options = {
#         'inline_headers':   headers,
#         'inline_sample':    sample_rows[:5],   # preview only
#         'inline_rows_all':  sample_rows,       # ALL rows — used for M Query embedding
#         'inline_row_count': len(lines) - 1,
#     }
#     return fields, options


# def _extract_table_label(statement: str) -> str:
#     """Return the TableName: label that precedes LOAD or SELECT, or ''."""
#     m = re.match(
#         r'^\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_\. ]*))\s*:',
#         statement
#     )
#     if m:
#         candidate = (m.group(1) or m.group(2) or '').strip()
#         reserved = {
#             'noconcatenate', 'concatenate', 'join', 'keep',
#             'left', 'right', 'inner', 'outer', 'where', 'load', 'select'
#         }
#         if candidate.lower() not in reserved:
#             return candidate
#     return ''


# def _parse_single_statement(stmt: str) -> Optional[Dict[str, Any]]:
#     """
#     Parse one semicolon-terminated Qlik statement into a table dict.
#     Returns None if the statement is not a data-load statement.
#     """
#     has_load   = bool(re.search(r'\bLOAD\b',   stmt, re.IGNORECASE))
#     has_select = bool(re.search(r'\bSELECT\b', stmt, re.IGNORECASE))
#     if not (has_load or has_select):
#         return None
#     # ✅ Fix: Skip MAPPING LOAD — temporary Qlik tables, not needed in Power BI
#     if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
#         return None
#     table_name = _extract_table_label(stmt)

#     # --- modifier ---
#     modifier = ''
#     if re.search(r'\bNoConcatenate\b', stmt, re.IGNORECASE):
#         modifier = 'NoConcatenate'
#     elif mc := re.search(r'\bConcatenate\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
#         modifier = f"Concatenate({mc.group(1) or ''})"
#     elif mj := re.search(r'\b(Left|Right|Inner|Outer)?\s*Join\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
#         modifier = f"{(mj.group(1) or '').strip()}Join({mj.group(2) or ''})".strip()
#     elif mk := re.search(r'\b(Left|Right|Inner)?\s*Keep\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
#         modifier = f"{(mk.group(1) or '').strip()}Keep({mk.group(2) or ''})".strip()

#     fields: List[Dict] = []
#     source_type = 'unknown'
#     source_path = ''
#     options: Dict[str, Any] = {}

#     # ── INLINE ──────────────────────────────────────────────────────────────
#     inline_m = re.search(r'\bINLINE\b\s*[\[(](.*?)[\])]', stmt, re.IGNORECASE | re.DOTALL)
#     if inline_m:
#         source_type = 'inline'
#         fields, options = _parse_inline_block(inline_m.group(1))

#     # ── RESIDENT ─────────────────────────────────────────────────────────────
#     elif res_m := re.search(r'\bRESIDENT\b\s+([A-Za-z_][A-Za-z0-9_]*)', stmt, re.IGNORECASE):
#         source_type = 'resident'
#         source_path = res_m.group(1).strip()
#         # field list is between LOAD and RESIDENT
#         load_m = re.search(r'\bLOAD\b', stmt, re.IGNORECASE)
#         res_pos = res_m.start()
#         if load_m and load_m.end() < res_pos:
#             fields = _parse_field_list(stmt[load_m.end():res_pos])

#     # ── FROM (file / SQL) ────────────────────────────────────────────────────
#     elif re.search(r'\bFROM\b', stmt, re.IGNORECASE):
#         raw_path, norm_path = _extract_from_path(stmt)
#         source_path = norm_path or raw_path
#         source_type = _file_source_type(source_path) if source_path else 'sql'

#         if has_load:
#             load_m = re.search(r'\bLOAD\b', stmt, re.IGNORECASE)
#             from_m = re.search(
#                 r"""\bFROM\b\s+(?:\[[^\]]+\]|'[^']+'|"[^"]+"|[^\s;,(]+)""",
#                 stmt, re.IGNORECASE
#             )
#             if load_m and from_m and load_m.end() < from_m.start():
#                 fields = _parse_field_list(stmt[load_m.end():from_m.start()])
#         elif has_select:
#             sel_m   = re.search(r'\bSELECT\b', stmt, re.IGNORECASE)
#             from_m  = re.search(r'\bFROM\b', stmt, re.IGNORECASE)
#             if sel_m and from_m and sel_m.end() < from_m.start():
#                 fields = _parse_field_list(stmt[sel_m.end():from_m.start()])

#         # File options
#         delim_m = re.search(r"\bDELIMITER\b\s+IS\s+(?:'([^']+)'|(\S+))", stmt, re.IGNORECASE)
#         if delim_m:
#             options['delimiter'] = (delim_m.group(1) or delim_m.group(2) or ',').strip()
#         elif source_type == 'csv':
#             options['delimiter'] = ','

#         sheet_m = re.search(r'\bSHEET\b\s+(?:IS\s+)?[\'"]?([^\s\'"]+)[\'"]?', stmt, re.IGNORECASE)
#         if sheet_m:
#             options['sheet'] = sheet_m.group(1).strip()

#         enc_m = re.search(r'\b(UTF-?8|UTF-?16|CP\d{4}|LATIN\d)\b', stmt, re.IGNORECASE)
#         if enc_m:
#             options['encoding'] = enc_m.group(1)

#         if source_type == 'sql':
#             source_path = _extract_sql_table(stmt)

#     # ── WHERE clause ────────────────────────────────────────────────────────
#     where_m = re.search(r'\bWHERE\b\s+(.+?)(?=\bGROUP\b|\bORDER\b|$)', stmt, re.IGNORECASE | re.DOTALL)
#     if where_m:
#         options['where'] = where_m.group(1).strip()[:200]

#     # ── GROUP BY ────────────────────────────────────────────────────────────
#     grp_m = re.search(r'\bGROUP\s+BY\b\s+([^;]+)', stmt, re.IGNORECASE)
#     if grp_m:
#         options['group_by'] = grp_m.group(1).strip()[:200]

#     if not fields and source_type not in ('inline',):
#         return None  # skip variable/set statements

#     # Derive table name from source path if still unnamed
#     if not table_name and source_path:
#         base = source_path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
#         base = re.sub(r'\.[^.]+$', '', base)
#         base = re.sub(r'[^A-Za-z0-9_]', '_', base)
#         table_name = base or 'UnnamedTable'

#     return {
#         'name':        table_name,
#         'source_type': source_type,
#         'source_path': source_path,
#         'fields':      fields,
#         'options':     options,
#         'modifier':    modifier,
#         'raw_statement': stmt,
#     }


# def _extract_sql_table(stmt: str) -> str:
#     """Pull table name from SELECT … FROM <table>."""
#     m = re.search(
#         r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|(\S+))""",
#         stmt, re.IGNORECASE
#     )
#     if m:
#         return (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
#     return ''


# # ---------------------------------------------------------------------------
# # Public class — same interface as v1
# # ---------------------------------------------------------------------------

# class LoadScriptParser:
#     """Parse Qlik loadscript and extract components (patched v2)."""

#     def __init__(self, loadscript: str):
#         logger.info("=" * 80)
#         logger.info("PHASE 5: PARSING LOADSCRIPT")
#         logger.info("=" * 80)

#         self.loadscript = loadscript
#         self.script_length = len(loadscript)

#         logger.info(f"📊 Input Script Length: {self.script_length} characters")
#         logger.info(f"⏰ Parse Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

#         # Component storage
#         self.tables: List[Dict]          = []
#         self.fields: List[Dict]          = []
#         self.data_connections: List[Dict] = []
#         self.transformations: List[Dict]  = []
#         self.joins: List[Dict]            = []
#         self.variables: List[Dict]        = []
#         self.functions: List[Dict]        = []
#         self.comments: List[Dict]         = []
#         self.load_statements: List[Dict]  = []

#         logger.info("✅ Parser initialized and ready to parse")

#     # ------------------------------------------------------------------
#     # Public entry point
#     # ------------------------------------------------------------------

#     def parse(self) -> Dict[str, Any]:
#         """
#         Main parsing function.
#         Returns dict with raw_script, tables, fields, and all component lists.
#         """
#         logger.info("📍 Starting comprehensive script analysis...")

#         try:
#             # Step 5.1 — Comments
#             logger.info("📍 Step 5.1: Extracting comments...")
#             self._extract_comments()
#             logger.info(f"✅ Found {len(self.comments)} comment block(s)")

#             # Step 5.2 — LOAD statements (de-duplicated)
#             logger.info("📍 Step 5.2: Extracting LOAD statements...")
#             self._extract_load_statements()
#             logger.info(f"✅ Found {len(self.load_statements)} LOAD statement(s)")

#             # Step 5.3 — Tables (now with source_type + source_path per table)
#             logger.info("📍 Step 5.3: Extracting table names...")
#             self._extract_tables()
#             logger.info(f"✅ Found {len(self.tables)} table(s)")
#             for table in self.tables:
#                 logger.info(
#                     f"   ✓ Table: {table.get('name', 'Unknown')}"
#                     f"  [{table.get('source_type', '?')}]"
#                     f"  {table.get('source_path', '')}"
#                 )

#             # Step 5.4 — Fields with type inference
#             logger.info("📍 Step 5.4: Extracting field definitions...")
#             self._extract_fields()
#             logger.info(f"✅ Found {len(self.fields)} field(s)")
#             for field in self.fields[:5]:
#                 logger.info(
#                     f"   ✓ Field: {field.get('name', 'Unknown')}"
#                     f" ({field.get('type', 'Unknown')})"
#                 )
#             if len(self.fields) > 5:
#                 logger.info(f"   ... and {len(self.fields) - 5} more field(s)")

#             # Step 5.5 — Data connections
#             logger.info("📍 Step 5.5: Extracting data connections...")
#             self._extract_data_connections()
#             logger.info(f"✅ Found {len(self.data_connections)} data connection(s)")
#             for conn in self.data_connections:
#                 logger.info(f"   ✓ Connection: {conn.get('type', 'Unknown')} - {conn.get('source', 'Unknown')}")

#             # Step 5.6 — Transformations
#             logger.info("📍 Step 5.6: Extracting transformations...")
#             self._extract_transformations()
#             logger.info(f"✅ Found {len(self.transformations)} transformation(s)")
#             for trans in self.transformations[:3]:
#                 logger.info(f"   ✓ {trans.get('type', 'Unknown')}: {trans.get('description', 'Unknown')}")
#             if len(self.transformations) > 3:
#                 logger.info(f"   ... and {len(self.transformations) - 3} more transformation(s)")

#             # Step 5.7 — JOINs
#             logger.info("📍 Step 5.7: Detecting JOIN operations...")
#             self._extract_joins()
#             logger.info(f"✅ Found {len(self.joins)} JOIN operation(s)")
#             for join in self.joins:
#                 logger.info(f"   ✓ {join.get('type', 'Unknown')}: {join.get('description', 'Unknown')}")

#             # Step 5.8 — Variables
#             logger.info("📍 Step 5.8: Extracting variable definitions...")
#             self._extract_variables()
#             logger.info(f"✅ Found {len(self.variables)} variable(s)")
#             for var in self.variables[:3]:
#                 logger.info(f"   ✓ Variable: {var.get('name', 'Unknown')}")
#             if len(self.variables) > 3:
#                 logger.info(f"   ... and {len(self.variables) - 3} more variable(s)")

#             logger.info("=" * 80)
#             logger.info("✅ PARSING COMPLETED SUCCESSFULLY")
#             logger.info("=" * 80)

#             return {
#                 # ✅ FIX 9: raw_script always present (required by simple_mquery_generator)
#                 "raw_script": self.loadscript,
#                 "status": "success",
#                 "parse_timestamp": datetime.now().isoformat(),
#                 "script_length": self.script_length,
#                 "summary": {
#                     "tables_count":          len(self.tables),
#                     "fields_count":          len(self.fields),
#                     "connections_count":     len(self.data_connections),
#                     "transformations_count": len(self.transformations),
#                     "joins_count":           len(self.joins),
#                     "variables_count":       len(self.variables),
#                     "comments_count":        len(self.comments),
#                 },
#                 "details": {
#                     "tables":           self.tables,
#                     "fields":           self.fields,
#                     "data_connections": self.data_connections,
#                     "transformations":  self.transformations,
#                     "joins":            self.joins,
#                     "variables":        self.variables,
#                     "comments":         self.comments,
#                 },
#             }

#         except Exception as e:
#             logger.error(f"❌ Error during parsing: {str(e)}")
#             import traceback
#             logger.debug(traceback.format_exc())
#             return {
#                 # ✅ FIX 9: raw_script in error path too
#                 "raw_script": self.loadscript,
#                 "status": "error",
#                 "message": str(e),
#                 "parse_timestamp": datetime.now().isoformat(),
#             }

#     # ------------------------------------------------------------------
#     # Step 5.1 — Comments
#     # ------------------------------------------------------------------

#     def _extract_comments(self):
#         """Extract inline (//) and block (/* */) comments."""
#         logger.debug("Extracting inline and block comments...")

#         # ✅ FIX 1: use the safe stripper so lib:// is not flagged
#         # We scan the original script, not the stripped version
#         inline_comments = re.findall(r'(?<![:/])//[^\n]*', self.loadscript)
#         self.comments.extend([{"type": "inline", "text": c.strip()} for c in inline_comments])

#         block_comments = re.findall(r'/\*.*?\*/', self.loadscript, re.DOTALL)
#         self.comments.extend([{"type": "block", "text": c.strip()} for c in block_comments])

#     # ------------------------------------------------------------------
#     # Step 5.2 — LOAD statements (de-duplicated)
#     # ------------------------------------------------------------------

#     def _extract_load_statements(self):
#         """
#         Extract unique LOAD statements using the string-aware splitter.
#         ✅ FIX 7: single-pass split eliminates the 3x duplication from v1.
#         """
#         logger.debug("Searching for LOAD statements...")

#         cleaned = _strip_comments_safe(self.loadscript)
#         stmts = _split_statements(cleaned)

#         seen: set = set()
#         for stmt in stmts:
#             if re.search(r'\bLOAD\b', stmt, re.IGNORECASE):
#                 key = stmt[:100]
#                 if key not in seen:
#                     seen.add(key)
#                     self.load_statements.append({
#                         "statement":   stmt[:200],
#                         "full_length": len(stmt),
#                     })

#     # ------------------------------------------------------------------
#     # Step 5.3 — Tables
#     # ------------------------------------------------------------------

#     def _extract_tables(self):
#         """
#         Extract table definitions using the string-aware statement parser.
#         ✅ FIX 3: Detects INLINE, RESIDENT, QVD, CSV, Excel, SQL
#         ✅ FIX 8: source_type and source_path populated per table
#         ✅ FIX 11: MAPPING LOAD tables filtered out
#         ✅ FIX 12: Dropped/staging tables filtered out
#         """
#         logger.debug("Extracting table definitions...")

#         cleaned = _strip_comments_safe(self.loadscript)
#         stmts = _split_statements(cleaned)

#         seen_names: set = set()

#         # ✅ FIX 12: Collect all DROP TABLE names first
#         dropped_tables: set = set()
#         for stmt in stmts:
#             drop_m = re.search(
#                 r'\bDROP\s+TABLE\b\s+([A-Za-z_][A-Za-z0-9_]*)',
#                 stmt, re.IGNORECASE
#             )
#             if drop_m:
#                 dropped_tables.add(drop_m.group(1).strip())

#         for stmt in stmts:
#             # ✅ FIX 11: Skip MAPPING LOAD tables
#             if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
#                 continue
#             # ✅ FIX 12: Skip DROP TABLE statements
#             if re.search(r'\bDROP\s+TABLE\b', stmt, re.IGNORECASE):
#                 continue

#             td = _parse_single_statement(stmt)
#             if td is None:
#                 continue

#             name = td['name']
#             if not name:
#                 continue

#             # ✅ FIX 12: Skip staging/intermediate tables that are dropped later
#             if name in dropped_tables:
#                 continue

#             # Deduplicate by name
#             if name in seen_names:
#                 continue
#             seen_names.add(name)

#             self.tables.append({
#                 "name":        name,
#                 "type":        "load_statement",
#                 "source_type": td['source_type'],
#                 "source_path": td['source_path'],
#                 "modifier":    td.get('modifier', ''),
#                 "options":     td.get('options', {}),
#                 "field_count": len(td['fields']),
#                 "fields":      td['fields'],
#             })
#         # """
#         # Extract table definitions using the string-aware statement parser.
#         # ✅ FIX 3: Detects INLINE, RESIDENT, QVD, CSV, Excel, SQL
#         # ✅ FIX 8: source_type and source_path populated per table
#         # """
#         # logger.debug("Extracting table definitions...")

#         # cleaned = _strip_comments_safe(self.loadscript)
#         # stmts = _split_statements(cleaned)

#         # seen_names: set = set()

#         # for stmt in stmts:
#         #     # ✅ Fix: Skip MAPPING LOAD and DROP TABLE statements
#         #     if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
#         #         continue
#         #     if re.search(r'\bDROP\s+TABLE\b', stmt, re.IGNORECASE):
#         #         continue
#         #     td = _parse_single_statement(stmt)
#         #     if td is None:
#         #         continue

#         #     name = td['name']
#         #     if not name:
#         #         continue

#         #     # Deduplicate by name
#         #     if name in seen_names:
#         #         continue
#         #     seen_names.add(name)

#         #     self.tables.append({
#         #         "name":        name,
#         #         "type":        "load_statement",
#         #         "source_type": td['source_type'],
#         #         "source_path": td['source_path'],
#         #         "modifier":    td.get('modifier', ''),
#         #         "options":     td.get('options', {}),
#         #         "field_count": len(td['fields']),
#         #         "fields":      td['fields'],   # per-table fields available here too
#         #     })

#     # ------------------------------------------------------------------
#     # Step 5.4 — Fields (with type inference)
#     # ------------------------------------------------------------------

#     def _extract_fields(self):
#         """
#         Extract all fields across all parsed tables.
#         ✅ FIX 6: field type inferred from expression, not always 'column'
#         """
#         logger.debug("Extracting field definitions...")

#         seen: set = set()
#         for table in self.tables:
#             for f in table.get('fields', []):
#                 name = f.get('name', '')
#                 if not name or name == '*':
#                     continue
#                 if name in seen:
#                     continue
#                 seen.add(name)
#                 self.fields.append(f)

#     # ------------------------------------------------------------------
#     # Step 5.5 — Data connections
#     # ------------------------------------------------------------------

#     def _extract_data_connections(self):
#         """
#         Extract data connection references from the script.
#         Derives from already-parsed table source paths + scans for
#         explicit ODBC/SQL/database references.
#         """
#         logger.debug("Extracting data connections...")

#         seen: set = set()

#         # From parsed tables
#         for table in self.tables:
#             src = table.get('source_path', '')
#             stype = table.get('source_type', '')
#             if not src or src in seen:
#                 continue
#             seen.add(src)

#             raw_path = src
#             # Reconstruct full lib:// path for display
#             orig_m = re.search(
#                 r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|(\S+))""",
#                 table.get('fields', [{}])[0].get('extracted_from', '') if table.get('fields') else '',
#                 re.IGNORECASE
#             )
#             self.data_connections.append({
#                 "type":       "library" if "lib://" in table.get('options', {}).get('raw_from', src) else stype,
#                 "source":     src,
#                 "path":       src,
#                 "table_name": table['name'],
#                 "source_type": stype,
#             })

#         # Additional sweep for any lib:// or file:// refs not caught above
#         for m in re.finditer(r'lib://([^\s;\'")\]]+)', self.loadscript):
#             path = f"lib://{m.group(1)}"
#             norm = re.sub(r'^lib://[^/]+/', '', path)
#             if norm not in seen:
#                 seen.add(norm)
#                 self.data_connections.append({
#                     "type":   "library",
#                     "source": path,
#                     "path":   norm,
#                 })

#         for m in re.finditer(r'file://([^\s;\'")\]]+)', self.loadscript):
#             path = m.group(1)
#             if path not in seen:
#                 seen.add(path)
#                 self.data_connections.append({
#                     "type":   "file",
#                     "source": f"file://{path}",
#                     "path":   path,
#                 })

#         # Database connections
#         for m in re.finditer(
#             r'\b(ODBC|SQL|ORACLE|MYSQL|POSTGRESQL)\b\s+([^;]+)',
#             self.loadscript, re.IGNORECASE
#         ):
#             db_type = m.group(1).upper()
#             detail  = m.group(2).strip()[:100]
#             self.data_connections.append({
#                 "type":   "database",
#                 "source": db_type,
#                 "detail": detail,
#             })

#     # ------------------------------------------------------------------
#     # Step 5.6 — Transformations
#     # ------------------------------------------------------------------

#     def _extract_transformations(self):
#         """
#         Extract WHERE, GROUP BY, DISTINCT, ORDER BY — scoped per table.
#         ✅ FIX 10: per-table scoping avoids global duplication
#         """
#         logger.debug("Extracting transformations...")

#         seen: set = set()

#         for table in self.tables:
#             opts = table.get('options', {})
#             name = table['name']

#             if 'where' in opts:
#                 key = f"WHERE:{name}"
#                 if key not in seen:
#                     seen.add(key)
#                     self.transformations.append({
#                         "type":        "filter",
#                         "table":       name,
#                         "description": f"WHERE {opts['where'][:80]}",
#                     })

#             if 'group_by' in opts:
#                 key = f"GROUP:{name}"
#                 if key not in seen:
#                     seen.add(key)
#                     self.transformations.append({
#                         "type":        "aggregation",
#                         "table":       name,
#                         "description": f"GROUP BY {opts['group_by'][:80]}",
#                     })

#         # DISTINCT (global scan, deduped)
#         if re.search(r'\bDISTINCT\b', self.loadscript, re.IGNORECASE):
#             self.transformations.append({
#                 "type":        "deduplication",
#                 "table":       "global",
#                 "description": "DISTINCT",
#             })

#         # ORDER BY (global scan)
#         for m in re.finditer(r'\bORDER\s+BY\b\s+([^;]+)', self.loadscript, re.IGNORECASE):
#             clause = m.group(1).strip()[:80]
#             key = f"ORDER:{clause}"
#             if key not in seen:
#                 seen.add(key)
#                 self.transformations.append({
#                     "type":        "sorting",
#                     "table":       "global",
#                     "description": f"ORDER BY {clause}",
#                 })

#     # ------------------------------------------------------------------
#     # Step 5.7 — JOINs
#     # ------------------------------------------------------------------

#     def _extract_joins(self):
#         """
#         Extract JOIN operations — both SQL-style and Qlik-style.
#         SQL:  INNER JOIN table ON condition
#         Qlik: Join (TableName) / Left Join (TableName)
#         """
#         logger.debug("Extracting JOIN operations...")

#         # SQL-style joins
#         for jtype in ['INNER JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN']:
#             pattern = rf'{jtype}\s+(\S+)\s+(?:ON|WHERE)\s+([^;]*)'
#             for m in re.finditer(pattern, self.loadscript, re.IGNORECASE):
#                 table     = m.group(1).strip()
#                 condition = m.group(2).strip()[:80]
#                 self.joins.append({
#                     "type":        jtype,
#                     "table":       table,
#                     "description": f"{jtype} {table} ON {condition}",
#                 })

#         # Qlik-style modifiers: Join (Table), Left Join (Table), etc.
#         for m in re.finditer(
#             r'\b(Left|Right|Inner|Outer)?\s*(Join|Keep)\b\s*(?:\(([^)]+)\))?',
#             self.loadscript, re.IGNORECASE
#         ):
#             prefix     = (m.group(1) or '').strip()
#             join_kw    = m.group(2).strip()
#             join_table = (m.group(3) or '').strip()
#             label      = f"{prefix} {join_kw}".strip()
#             self.joins.append({
#                 "type":        label,
#                 "table":       join_table,
#                 "description": f"{label} ({join_table})" if join_table else label,
#             })

#     # ------------------------------------------------------------------
#     # Step 5.8 — Variables
#     # ------------------------------------------------------------------

#     def _extract_variables(self):
#         """Extract LET / SET variable definitions."""
#         logger.debug("Extracting variable definitions...")

#         for m in re.finditer(
#             r'\b(LET|SET)\b\s+(\w+)\s*=\s*([^;]*)',
#             self.loadscript, re.IGNORECASE
#         ):
#             self.variables.append({
#                 "name":    m.group(2),
#                 "value":   m.group(3).strip()[:80],
#                 "keyword": m.group(1).upper(),
#                 "type":    "let_set",
#             })


# # ---------------------------------------------------------------------------
# # Standalone testing
# # ---------------------------------------------------------------------------

# if __name__ == "__main__":
#     sample_script = """
#     // Sample Load Script — patched parser test

#     SET vStartDate = '2024-01-01';
#     LET vMaxRows = 1000;

#     /* ============================================================
#        Customer Table — CSV source
#     ============================================================ */
#     [Customers]:
#     LOAD
#         CustomerID,
#         CustomerName,
#         Country,
#         Date(JoinDate, 'YYYY-MM-DD') AS JoinDate,
#         Num(Revenue) AS Revenue
#     FROM [lib://DataFiles/customers.csv]
#     (txt, utf8, embedded labels, delimiter is ',');

#     // Orders from QVD
#     [Orders]:
#     LOAD
#         OrderID,
#         CustomerID,
#         OrderDate,
#         Amount
#     FROM [lib://DataFiles/orders.qvd] (qvd)
#     WHERE Amount > 0;

#     // Excel source with sheet
#     [Employees]:
#     LOAD
#         EmpID,
#         Name,
#         Department
#     FROM [lib://DataFiles/employees.xlsx]
#     (ooxml, embedded labels, table is Sheet1);

#     // Inline lookup table
#     Calendar:
#     LOAD * INLINE [
#         Month, Quarter, Year
#         January, Q1, 2024
#         February, Q1, 2024
#         March, Q1, 2024
#     ];

#     // RESIDENT table (derived from Orders)
#     SalesSummary:
#     LOAD
#         CustomerID,
#         Sum(Amount) AS TotalAmount
#     RESIDENT Orders
#     WHERE Amount > 0
#     GROUP BY CustomerID;

#     // SQL via ODBC
#     Products:
#     SELECT ProductID, ProductName, Category, Price
#     FROM dbo.Products;
#     """

#     parser = LoadScriptParser(sample_script)
#     result = parser.parse()

#     print("\n" + "=" * 60)
#     print("PARSE SUMMARY")
#     print("=" * 60)
#     print(f"Status:        {result['status']}")
#     print(f"Tables:        {result['summary']['tables_count']}")
#     print(f"Fields:        {result['summary']['fields_count']}")
#     print(f"Connections:   {result['summary']['connections_count']}")
#     print(f"Transforms:    {result['summary']['transformations_count']}")
#     print(f"Variables:     {result['summary']['variables_count']}")
#     print(f"raw_script OK: {'raw_script' in result}")

#     print("\n--- Tables ---")
#     for t in result['details']['tables']:
#         print(f"  {t['name']:20} [{t['source_type']:8}]  {t['source_path']}")
#         for f in t['fields'][:3]:
#             print(f"    • {f['name']:20} ({f['type']})")
#         if len(t['fields']) > 3:
#             print(f"    ... +{len(t['fields'])-3} more")





"""
Qlik LoadScript Parser Module  (PATCHED v3)

Parses the loadscript and extracts meaningful components with detailed logging.

Patches applied over v2:
  ✅ Fix 1:  lib:// comment stripping bug — // inside lib:// was stripped as a comment
  ✅ Fix 2:  String-aware semicolon split — naive split broke on delimiter is ','
             and Date(x, 'YYYY-MM-DD') format strings
  ✅ Fix 3:  INLINE block detection and parsing
  ✅ Fix 4:  QVD load source type detection (was falling through as generic file)
  ✅ Fix 5:  RESIDENT load detection
  ✅ Fix 6:  Field type inference (date, number, boolean, string) from expressions
  ✅ Fix 7:  Duplicate load_statement suppression (3 overlapping patterns caused 3x dupes)
  ✅ Fix 8:  Table source_type and source_path now populated per table
  ✅ Fix 9:  raw_script always present in parse() return dict (required by simple_mquery_generator)
  ✅ Fix 10: DISTINCT/WHERE/GROUP BY now scoped per-table, not globally duplicated
  ✅ Fix 11: MAPPING LOAD tables filtered out
  ✅ Fix 12: Dropped/staging tables filtered out (DROP TABLE)
  ✅ Fix 13: dropped_table_paths collected and injected into resident table options
             so mquery_converter can inline the CSV source instead of generating
             the broken  SalesRaw = SalesRaw  self-reference pattern.
"""

import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers (module-level, not part of the public class interface)
# ---------------------------------------------------------------------------

def _strip_comments_safe(script: str) -> str:
    """
    Strip // and /* */ comments WITHOUT mangling lib:// or file:// URLs.

    Rules:
      - // is only a comment if NOT preceded by ':'
      - /* ... */ block comments are always stripped
    """
    # Pass 1: block comments
    script = re.sub(r'/\*.*?\*/', '', script, flags=re.DOTALL)

    # Pass 2: line comments — walk line by line, skip // preceded by ':'
    result_lines = []
    for line in script.split('\n'):
        out = []
        in_single = False
        in_double = False
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif (ch == '/' and i + 1 < len(line) and line[i + 1] == '/'
                  and not in_single and not in_double):
                # Only treat as comment if NOT preceded by ':'
                if i > 0 and line[i - 1] == ':':
                    pass  # it's :// — keep it
                else:
                    break  # real comment — truncate line
            out.append(ch)
            i += 1
        result_lines.append(''.join(out))
    return '\n'.join(result_lines)


def _split_statements(script: str) -> List[str]:
    """
    Split a Qlik script on semicolons, respecting:
      • single-quoted strings  'abc'
      • double-quoted strings  "abc"
      • square brackets        [abc]
    Returns list of non-empty stripped statement strings.
    """
    parts: List[str] = []
    current: List[str] = []
    in_single = False
    in_double = False
    bracket_depth = 0

    for ch in script:
        if ch == "'" and not in_double and bracket_depth == 0:
            in_single = not in_single
        elif ch == '"' and not in_single and bracket_depth == 0:
            in_double = not in_double
        elif ch == '[' and not in_single and not in_double:
            bracket_depth += 1
        elif ch == ']' and not in_single and not in_double:
            bracket_depth = max(0, bracket_depth - 1)
        elif ch == ';' and not in_single and not in_double and bracket_depth == 0:
            stmt = ''.join(current).strip()
            if stmt:
                parts.append(stmt)
            current = []
            continue
        current.append(ch)

    tail = ''.join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _file_source_type(path: str) -> str:
    """Classify a file path into a source type string."""
    path_lower = path.lower()
    if path_lower.endswith('.qvd'):
        return 'qvd'
    if path_lower.endswith(('.csv', '.txt', '.tsv', '.tab')):
        return 'csv'
    if path_lower.endswith(('.xlsx', '.xls', '.xlsm')):
        return 'excel'
    if path_lower.endswith('.json'):
        return 'json'
    if path_lower.endswith('.xml'):
        return 'xml'
    if path_lower.endswith('.parquet'):
        return 'parquet'
    return 'file'


def _infer_field_type(expression: str) -> str:
    """
    Heuristic type inference from a Qlik field expression or bare field name.

    Priority order:
      1. Qlik function calls in the expression  (DATE(), NUM(), etc.)
      2. Field-name suffixes/keywords           (Price, Cost, Date, Year, ID, etc.)
      3. Arithmetic operators
      4. Default: string
    """
    e = expression.upper()

    # ── Expression-level inference (function calls) ──────────────────────────
    if re.search(r'\b(DATE|MAKEDATE|YEAR|MONTH|DAY|TIMESTAMP|TODAY|NOW)\s*\(', e):
        return 'date'
    if re.search(r'\b(NUM|INT|FLOOR|CEIL|ROUND|SUM|COUNT|AVG|MIN|MAX|FRAC|MOD)\s*\(', e):
        return 'number'
    if re.search(r'\b(TEXT|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|CAT|REPLACE|LEN)\s*\(', e):
        return 'string'
    if re.search(r'\b(IF|PICK|MATCH|ALT|NULL)\s*\(', e):
        return 'mixed'
    if re.search(r'\bTRUE\b|\bFALSE\b', e):
        return 'boolean'
    if re.search(r'[\+\-\*\/]', e):
        return 'number'

    # ── Name-based inference (bare field names after aliasing) ───────────────
    # Strip table qualifier prefix (e.g. "Service_History.ServiceCost" -> "SERVICECOST")
    name = e.split('.')[-1] if '.' in e else e
    # Strip brackets
    name = name.strip('[]#"\'')

    # Date/time suffixes
    if re.search(r'(DATE|TIME|TIMESTAMP|CREATED|UPDATED|MODIFIED|DOB|BIRTH)$', name):
        return 'date'
    if re.search(r'^(DATE|TIME|TIMESTAMP)', name):
        return 'date'

    # Integer — year, count, age, rank, sequence
    if re.search(r'(YEAR|MONTH|DAY|AGE|COUNT|QTY|QUANTITY|RANK|SEQ|NUM|NBR|NR|NO)$', name):
        return 'integer'

    # Decimal — price, cost, amount, revenue, tax, rate, weight, discount
    if re.search(r'(PRICE|COST|AMOUNT|REVENUE|SALARY|RATE|TOTAL|TAX|DISCOUNT|FEE|WEIGHT|PERCENT|PCT|BUDGET|BALANCE)$', name):
        return 'number'

    return 'string'


def _extract_from_path(statement: str) -> Tuple[str, str]:
    """
    Pull the file/table path from a FROM clause.
    Returns (raw_path, normalized_path_without_lib_prefix).
    """
    # Handles [lib://x], 'lib://x', bare lib://x, quoted 'path/file.csv'
    m = re.search(
        r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|(\S+))""",
        statement, re.IGNORECASE
    )
    if not m:
        return '', ''
    raw = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
    # Strip Qlik lib:// prefix:  lib://DataFiles/orders.qvd → orders.qvd
    normalized = re.sub(r'^lib://[^/]+/', '', raw)
    normalized = re.sub(r'^lib://', '', normalized)
    return raw, normalized


def _parse_field_list(field_text: str) -> List[Dict[str, Any]]:
    """
    Parse a comma-separated field expression list (the part between LOAD and FROM/INLINE/RESIDENT).
    Returns list of field dicts with name, expression, alias, type.
    """
    if not field_text.strip():
        return []
    if field_text.strip() == '*':
        return [{'name': '*', 'expression': '*', 'alias': None, 'type': 'wildcard'}]

    fields = []
    # Smart split on commas (respecting brackets/parens)
    parts: List[str] = []
    depth_p = 0
    depth_b = 0
    current: List[str] = []
    for ch in field_text:
        if ch == '(':
            depth_p += 1
        elif ch == ')':
            depth_p -= 1
        elif ch == '[':
            depth_b += 1
        elif ch == ']':
            depth_b -= 1
        elif ch == ',' and depth_p == 0 and depth_b == 0:
            parts.append(''.join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append(''.join(current).strip())

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Detect AS alias
        alias = None
        as_m = re.search(
            r'\bAS\b\s+(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_ ]*))\s*$',
            part, re.IGNORECASE
        )
        if as_m:
            alias = (as_m.group(1) or as_m.group(2)).strip()
            expr = part[:as_m.start()].strip()
        else:
            expr = part

        # Bare field name (strip brackets)
        if expr.startswith('[') and expr.endswith(']'):
            bare = expr[1:-1]
        elif re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', expr):
            bare = expr
        else:
            bare = alias or expr  # expression like Qty*Price

        name = alias or bare
        data_type = _infer_field_type(expr)

        fields.append({
            'name':       name,
            'expression': expr,
            'alias':      alias,
            'type':       data_type,
            'extracted_from': 'load_statement'
        })

    return fields


def _parse_inline_block(inline_text: str) -> Tuple[List[Dict], Dict]:
    """Parse INLINE [ header\\nrows… ] content. Returns (fields, options)."""
    lines = [l.strip() for l in inline_text.splitlines() if l.strip()]
    if not lines:
        return [], {}

    headers = [h.strip() for h in lines[0].split(',')]
    fields = [{'name': h, 'expression': h, 'alias': None, 'type': 'string',
                'extracted_from': 'inline'} for h in headers if h]

    sample_rows = []
    for line in lines[1:]:
        vals = [v.strip() for v in line.split(',')]
        if vals:
            sample_rows.append(dict(zip(headers, vals)))

    options = {
        'inline_headers':   headers,
        'inline_sample':    sample_rows[:5],   # preview only
        'inline_rows_all':  sample_rows,       # ALL rows — used for M Query embedding
        'inline_row_count': len(lines) - 1,
    }
    return fields, options


def _extract_table_label(statement: str) -> str:
    """Return the TableName: label that precedes LOAD or SELECT, or ''."""
    m = re.match(
        r'^\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_\. ]*))\\s*:',
        statement
    )
    if not m:
        # Try without the escaped backslash (standard regex)
        m = re.match(
            r'^\s*(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_\. ]*))\s*:',
            statement
        )
    if m:
        candidate = (m.group(1) or m.group(2) or '').strip()
        reserved = {
            'noconcatenate', 'concatenate', 'join', 'keep',
            'left', 'right', 'inner', 'outer', 'where', 'load', 'select'
        }
        if candidate.lower() not in reserved:
            return candidate
    return ''


def _parse_single_statement(stmt: str) -> Optional[Dict[str, Any]]:
    """
    Parse one semicolon-terminated Qlik statement into a table dict.
    Returns None if the statement is not a data-load statement.
    """
    has_load   = bool(re.search(r'\bLOAD\b',   stmt, re.IGNORECASE))
    has_select = bool(re.search(r'\bSELECT\b', stmt, re.IGNORECASE))
    if not (has_load or has_select):
        return None
    # ✅ Fix: Skip MAPPING LOAD — temporary Qlik tables, not needed in Power BI
    if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
        return None
    table_name = _extract_table_label(stmt)

    # --- modifier ---
    modifier = ''
    if re.search(r'\bNoConcatenate\b', stmt, re.IGNORECASE):
        modifier = 'NoConcatenate'
    elif mc := re.search(r'\bConcatenate\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
        modifier = f"Concatenate({mc.group(1) or ''})"
    elif mj := re.search(r'\b(Left|Right|Inner|Outer)?\s*Join\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
        modifier = f"{(mj.group(1) or '').strip()}Join({mj.group(2) or ''})".strip()
    elif mk := re.search(r'\b(Left|Right|Inner)?\s*Keep\s*(?:\(([^)]+)\))?', stmt, re.IGNORECASE):
        modifier = f"{(mk.group(1) or '').strip()}Keep({mk.group(2) or ''})".strip()

    fields: List[Dict] = []
    source_type = 'unknown'
    source_path = ''
    options: Dict[str, Any] = {}

    # ── INLINE ──────────────────────────────────────────────────────────────
    inline_m = re.search(r'\bINLINE\b\s*[\[(](.*?)[\])]', stmt, re.IGNORECASE | re.DOTALL)
    if inline_m:
        source_type = 'inline'
        fields, options = _parse_inline_block(inline_m.group(1))

    # ── RESIDENT ─────────────────────────────────────────────────────────────
    elif res_m := re.search(r'\bRESIDENT\b\s+([A-Za-z_][A-Za-z0-9_]*)', stmt, re.IGNORECASE):
        source_type = 'resident'
        source_path = res_m.group(1).strip()
        # ✅ Fix 13: Store resident source name so extract_tables can inject
        # the raw CSV path if the source table was dropped (intermediate table).
        options['resident_source'] = source_path
        # field list is between LOAD and RESIDENT
        load_m = re.search(r'\bLOAD\b', stmt, re.IGNORECASE)
        res_pos = res_m.start()
        if load_m and load_m.end() < res_pos:
            fields = _parse_field_list(stmt[load_m.end():res_pos])

    # ── FROM (file / SQL) ────────────────────────────────────────────────────
    elif re.search(r'\bFROM\b', stmt, re.IGNORECASE):
        raw_path, norm_path = _extract_from_path(stmt)
        source_path = norm_path or raw_path
        source_type = _file_source_type(source_path) if source_path else 'sql'

        if has_load:
            load_m = re.search(r'\bLOAD\b', stmt, re.IGNORECASE)
            from_m = re.search(
                r"""\bFROM\b\s+(?:\[[^\]]+\]|'[^']+'|"[^"]+"|[^\s;,(]+)""",
                stmt, re.IGNORECASE
            )
            if load_m and from_m and load_m.end() < from_m.start():
                fields = _parse_field_list(stmt[load_m.end():from_m.start()])
        elif has_select:
            sel_m   = re.search(r'\bSELECT\b', stmt, re.IGNORECASE)
            from_m  = re.search(r'\bFROM\b', stmt, re.IGNORECASE)
            if sel_m and from_m and sel_m.end() < from_m.start():
                fields = _parse_field_list(stmt[sel_m.end():from_m.start()])

        # File options
        delim_m = re.search(r"\bDELIMITER\b\s+IS\s+(?:'([^']+)'|(\S+))", stmt, re.IGNORECASE)
        if delim_m:
            options['delimiter'] = (delim_m.group(1) or delim_m.group(2) or ',').strip()
        elif source_type == 'csv':
            options['delimiter'] = ','

        sheet_m = re.search(r'\bSHEET\b\s+(?:IS\s+)?[\'"]?([^\s\'"]+)[\'"]?', stmt, re.IGNORECASE)
        if sheet_m:
            options['sheet'] = sheet_m.group(1).strip()

        enc_m = re.search(r'\b(UTF-?8|UTF-?16|CP\d{4}|LATIN\d)\b', stmt, re.IGNORECASE)
        if enc_m:
            options['encoding'] = enc_m.group(1)

        if source_type == 'sql':
            source_path = _extract_sql_table(stmt)

    # ── WHERE clause ────────────────────────────────────────────────────────
    where_m = re.search(r'\bWHERE\b\s+(.+?)(?=\bGROUP\b|\bORDER\b|$)', stmt, re.IGNORECASE | re.DOTALL)
    if where_m:
        options['where'] = where_m.group(1).strip()[:200]

    # ── GROUP BY ────────────────────────────────────────────────────────────
    grp_m = re.search(r'\bGROUP\s+BY\b\s+([^;]+)', stmt, re.IGNORECASE)
    if grp_m:
        options['group_by'] = grp_m.group(1).strip()[:200]

    if not fields and source_type not in ('inline',):
        return None  # skip variable/set statements

    # Derive table name from source path if still unnamed
    if not table_name and source_path:
        base = source_path.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
        base = re.sub(r'\.[^.]+$', '', base)
        base = re.sub(r'[^A-Za-z0-9_]', '_', base)
        table_name = base or 'UnnamedTable'

    return {
        'name':          table_name,
        'source_type':   source_type,
        'source_path':   source_path,
        'fields':        fields,
        'options':       options,
        'modifier':      modifier,
        'raw_statement': stmt,
    }


def _extract_sql_table(stmt: str) -> str:
    """Pull table name from SELECT … FROM <table>."""
    m = re.search(
        r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|(\S+))""",
        stmt, re.IGNORECASE
    )
    if m:
        return (m.group(1) or m.group(2) or m.group(3) or m.group(4) or '').strip()
    return ''


# ---------------------------------------------------------------------------
# Public class — same interface as v1/v2
# ---------------------------------------------------------------------------

class LoadScriptParser:
    """Parse Qlik loadscript and extract components (patched v3)."""

    def __init__(self, loadscript: str):
        logger.info("=" * 80)
        logger.info("PHASE 5: PARSING LOADSCRIPT")
        logger.info("=" * 80)

        self.loadscript = loadscript
        self.script_length = len(loadscript)

        logger.info(f"📊 Input Script Length: {self.script_length} characters")
        logger.info(f"⏰ Parse Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Component storage
        self.tables: List[Dict]          = []
        self.fields: List[Dict]          = []
        self.data_connections: List[Dict] = []
        self.transformations: List[Dict]  = []
        self.joins: List[Dict]            = []
        self.variables: List[Dict]        = []
        self.functions: List[Dict]        = []
        self.comments: List[Dict]         = []
        self.load_statements: List[Dict]  = []

        logger.info("✅ Parser initialized and ready to parse")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self) -> Dict[str, Any]:
        """
        Main parsing function.
        Returns dict with raw_script, tables, fields, and all component lists.
        """
        logger.info("📍 Starting comprehensive script analysis...")

        try:
            # Step 5.1 — Comments
            logger.info("📍 Step 5.1: Extracting comments...")
            self._extract_comments()
            logger.info(f"✅ Found {len(self.comments)} comment block(s)")

            # Step 5.2 — LOAD statements (de-duplicated)
            logger.info("📍 Step 5.2: Extracting LOAD statements...")
            self._extract_load_statements()
            logger.info(f"✅ Found {len(self.load_statements)} LOAD statement(s)")

            # Step 5.3 — Tables (now with source_type + source_path per table)
            logger.info("📍 Step 5.3: Extracting table names...")
            self._extract_tables()
            logger.info(f"✅ Found {len(self.tables)} table(s)")
            for table in self.tables:
                logger.info(
                    f"   ✓ Table: {table.get('name', 'Unknown')}"
                    f"  [{table.get('source_type', '?')}]"
                    f"  {table.get('source_path', '')}"
                )

            # Step 5.4 — Fields with type inference
            logger.info("📍 Step 5.4: Extracting field definitions...")
            self._extract_fields()
            logger.info(f"✅ Found {len(self.fields)} field(s)")
            for field in self.fields[:5]:
                logger.info(
                    f"   ✓ Field: {field.get('name', 'Unknown')}"
                    f" ({field.get('type', 'Unknown')})"
                )
            if len(self.fields) > 5:
                logger.info(f"   ... and {len(self.fields) - 5} more field(s)")

            # Step 5.5 — Data connections
            logger.info("📍 Step 5.5: Extracting data connections...")
            self._extract_data_connections()
            logger.info(f"✅ Found {len(self.data_connections)} data connection(s)")
            for conn in self.data_connections:
                logger.info(f"   ✓ Connection: {conn.get('type', 'Unknown')} - {conn.get('source', 'Unknown')}")

            # Step 5.6 — Transformations
            logger.info("📍 Step 5.6: Extracting transformations...")
            self._extract_transformations()
            logger.info(f"✅ Found {len(self.transformations)} transformation(s)")
            for trans in self.transformations[:3]:
                logger.info(f"   ✓ {trans.get('type', 'Unknown')}: {trans.get('description', 'Unknown')}")
            if len(self.transformations) > 3:
                logger.info(f"   ... and {len(self.transformations) - 3} more transformation(s)")

            # Step 5.7 — JOINs
            logger.info("📍 Step 5.7: Detecting JOIN operations...")
            self._extract_joins()
            logger.info(f"✅ Found {len(self.joins)} JOIN operation(s)")
            for join in self.joins:
                logger.info(f"   ✓ {join.get('type', 'Unknown')}: {join.get('description', 'Unknown')}")

            # Step 5.8 — Variables
            logger.info("📍 Step 5.8: Extracting variable definitions...")
            self._extract_variables()
            logger.info(f"✅ Found {len(self.variables)} variable(s)")
            for var in self.variables[:3]:
                logger.info(f"   ✓ Variable: {var.get('name', 'Unknown')}")
            if len(self.variables) > 3:
                logger.info(f"   ... and {len(self.variables) - 3} more variable(s)")

            logger.info("=" * 80)
            logger.info("✅ PARSING COMPLETED SUCCESSFULLY")
            logger.info("=" * 80)

            return {
                # ✅ FIX 9: raw_script always present (required by simple_mquery_generator)
                "raw_script": self.loadscript,
                "status": "success",
                "parse_timestamp": datetime.now().isoformat(),
                "script_length": self.script_length,
                "summary": {
                    "tables_count":          len(self.tables),
                    "fields_count":          len(self.fields),
                    "connections_count":     len(self.data_connections),
                    "transformations_count": len(self.transformations),
                    "joins_count":           len(self.joins),
                    "variables_count":       len(self.variables),
                    "comments_count":        len(self.comments),
                },
                "details": {
                    "tables":           self.tables,
                    "fields":           self.fields,
                    "data_connections": self.data_connections,
                    "transformations":  self.transformations,
                    "joins":            self.joins,
                    "variables":        self.variables,
                    "comments":         self.comments,
                },
            }

        except Exception as e:
            logger.error(f"❌ Error during parsing: {str(e)}")
            import traceback
            logger.debug(traceback.format_exc())
            return {
                # ✅ FIX 9: raw_script in error path too
                "raw_script": self.loadscript,
                "status": "error",
                "message": str(e),
                "parse_timestamp": datetime.now().isoformat(),
            }

    # ------------------------------------------------------------------
    # Step 5.1 — Comments
    # ------------------------------------------------------------------

    def _extract_comments(self):
        """Extract inline (//) and block (/* */) comments."""
        logger.debug("Extracting inline and block comments...")

        # ✅ FIX 1: use the safe stripper so lib:// is not flagged
        # We scan the original script, not the stripped version
        inline_comments = re.findall(r'(?<![:/])//[^\n]*', self.loadscript)
        self.comments.extend([{"type": "inline", "text": c.strip()} for c in inline_comments])

        block_comments = re.findall(r'/\*.*?\*/', self.loadscript, re.DOTALL)
        self.comments.extend([{"type": "block", "text": c.strip()} for c in block_comments])

    # ------------------------------------------------------------------
    # Step 5.2 — LOAD statements (de-duplicated)
    # ------------------------------------------------------------------

    def _extract_load_statements(self):
        """
        Extract unique LOAD statements using the string-aware splitter.
        ✅ FIX 7: single-pass split eliminates the 3x duplication from v1.
        """
        logger.debug("Searching for LOAD statements...")

        cleaned = _strip_comments_safe(self.loadscript)
        stmts = _split_statements(cleaned)

        seen: set = set()
        for stmt in stmts:
            if re.search(r'\bLOAD\b', stmt, re.IGNORECASE):
                key = stmt[:100]
                if key not in seen:
                    seen.add(key)
                    self.load_statements.append({
                        "statement":   stmt[:200],
                        "full_length": len(stmt),
                    })

    # ------------------------------------------------------------------
    # Step 5.3 — Tables
    # ------------------------------------------------------------------

    def _extract_tables(self):
        """
        Extract table definitions using the string-aware statement parser.

        ✅ FIX 3:  Detects INLINE, RESIDENT, QVD, CSV, Excel, SQL
        ✅ FIX 8:  source_type and source_path populated per table
        ✅ FIX 11: MAPPING LOAD tables filtered out
        ✅ FIX 12: Dropped/staging tables filtered out (DROP TABLE)
        ✅ FIX 13: dropped_table_paths collected so that resident tables whose
                   source was dropped receive the original CSV path in options
                   (raw_source_path + is_dropped_resident = True).
                   mquery_converter._m_resident uses these flags to inline the
                   CSV load directly instead of generating the broken
                   SalesRaw = SalesRaw self-reference.
        """
        logger.debug("Extracting table definitions...")

        cleaned = _strip_comments_safe(self.loadscript)
        stmts = _split_statements(cleaned)

        seen_names: set = set()

        # ── Pass 1: collect all DROP TABLE target names ───────────────────────
        dropped_tables: set = set()
        for stmt in stmts:
            drop_m = re.search(
                r'\bDROP\s+TABLE\b\s+([A-Za-z_][A-Za-z0-9_]*)',
                stmt, re.IGNORECASE
            )
            if drop_m:
                dropped_tables.add(drop_m.group(1).strip())

        # ── Pass 2: collect source paths of tables that will be dropped ───────
        # We need to know WHERE Sales_Raw was loaded from (e.g. fact_sales_1M.csv)
        # so that the resident table (Sales) can inline the same CSV source.
        dropped_table_paths: Dict[str, dict] = {}   # table_name → {source_path, delimiter, encoding, sheet}
        for stmt in stmts:
            if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
                continue
            if re.search(r'\bDROP\s+TABLE\b', stmt, re.IGNORECASE):
                continue
            td_temp = _parse_single_statement(stmt)
            if td_temp and td_temp.get('name') in dropped_tables:
                dropped_table_paths[td_temp['name']] = {
                    'source_path': td_temp.get('source_path', ''),
                    'delimiter':   td_temp.get('options', {}).get('delimiter', ','),
                    'encoding':    td_temp.get('options', {}).get('encoding', ''),
                    'sheet':       td_temp.get('options', {}).get('sheet', ''),
                }

        logger.debug(f"Dropped tables: {dropped_tables}")
        logger.debug(f"Dropped table paths: {dropped_table_paths}")

        # ── Pass 3: build final table list ───────────────────────────────────
        for stmt in stmts:
            # ✅ FIX 11: Skip MAPPING LOAD tables
            if re.search(r'\bMAPPING\s+LOAD\b', stmt, re.IGNORECASE):
                continue
            # ✅ FIX 12: Skip DROP TABLE statements themselves
            if re.search(r'\bDROP\s+TABLE\b', stmt, re.IGNORECASE):
                continue

            td = _parse_single_statement(stmt)
            if td is None:
                continue

            name = td['name']
            if not name:
                continue

            # ✅ FIX 12: Skip staging/intermediate tables that are dropped later
            if name in dropped_tables:
                continue

            # ✅ FIX 13: For resident tables whose source was dropped, inject
            # the original file path so mquery_converter can inline the CSV.
            if td['source_type'] == 'resident':
                resident_src = td['options'].get('resident_source', td['source_path'])
                if resident_src in dropped_tables:
                    src_info = dropped_table_paths.get(resident_src, {})
                    td['options']['is_dropped_resident'] = True
                    td['options']['raw_source_path'] = src_info.get('source_path', '') if isinstance(src_info, dict) else src_info
                    if isinstance(src_info, dict):
                        if src_info.get('delimiter'):
                            td['options']['delimiter'] = src_info['delimiter']
                        if src_info.get('encoding'):
                            td['options']['encoding'] = src_info['encoding']
                        if src_info.get('sheet'):
                            td['options']['sheet'] = src_info['sheet']
                    logger.info(
                        f"   ℹ️  Table '{name}' is RESIDENT of dropped table '{resident_src}'. "
                        f"raw_source_path='{td['options']['raw_source_path']}'"
                    )

            # Deduplicate by name
            if name in seen_names:
                continue
            seen_names.add(name)

            self.tables.append({
                "name":        name,
                "type":        "load_statement",
                "source_type": td['source_type'],
                "source_path": td['source_path'],
                "modifier":    td.get('modifier', ''),
                "options":     td.get('options', {}),
                "field_count": len(td['fields']),
                "fields":      td['fields'],
            })

    # ------------------------------------------------------------------
    # Step 5.4 — Fields (with type inference)
    # ------------------------------------------------------------------

    def _extract_fields(self):
        """
        Extract all fields across all parsed tables.
        ✅ FIX 6: field type inferred from expression, not always 'column'
        """
        logger.debug("Extracting field definitions...")

        seen: set = set()
        for table in self.tables:
            for f in table.get('fields', []):
                name = f.get('name', '')
                if not name or name == '*':
                    continue
                if name in seen:
                    continue
                seen.add(name)
                self.fields.append(f)

    # ------------------------------------------------------------------
    # Step 5.5 — Data connections
    # ------------------------------------------------------------------

    def _extract_data_connections(self):
        """
        Extract data connection references from the script.
        Derives from already-parsed table source paths + scans for
        explicit ODBC/SQL/database references.
        """
        logger.debug("Extracting data connections...")

        seen: set = set()

        # From parsed tables
        for table in self.tables:
            src = table.get('source_path', '')
            stype = table.get('source_type', '')
            if not src or src in seen:
                continue
            seen.add(src)

            self.data_connections.append({
                "type":        "library" if "lib://" in table.get('options', {}).get('raw_from', src) else stype,
                "source":      src,
                "path":        src,
                "table_name":  table['name'],
                "source_type": stype,
            })

        # Additional sweep for any lib:// or file:// refs not caught above
        for m in re.finditer(r'lib://([^\s;\'")\]]+)', self.loadscript):
            path = f"lib://{m.group(1)}"
            norm = re.sub(r'^lib://[^/]+/', '', path)
            if norm not in seen:
                seen.add(norm)
                self.data_connections.append({
                    "type":   "library",
                    "source": path,
                    "path":   norm,
                })

        for m in re.finditer(r'file://([^\s;\'")\]]+)', self.loadscript):
            path = m.group(1)
            if path not in seen:
                seen.add(path)
                self.data_connections.append({
                    "type":   "file",
                    "source": f"file://{path}",
                    "path":   path,
                })

        # Database connections
        for m in re.finditer(
            r'\b(ODBC|SQL|ORACLE|MYSQL|POSTGRESQL)\b\s+([^;]+)',
            self.loadscript, re.IGNORECASE
        ):
            db_type = m.group(1).upper()
            detail  = m.group(2).strip()[:100]
            self.data_connections.append({
                "type":   "database",
                "source": db_type,
                "detail": detail,
            })

    # ------------------------------------------------------------------
    # Step 5.6 — Transformations
    # ------------------------------------------------------------------

    def _extract_transformations(self):
        """
        Extract WHERE, GROUP BY, DISTINCT, ORDER BY — scoped per table.
        ✅ FIX 10: per-table scoping avoids global duplication
        """
        logger.debug("Extracting transformations...")

        seen: set = set()

        for table in self.tables:
            opts = table.get('options', {})
            name = table['name']

            if 'where' in opts:
                key = f"WHERE:{name}"
                if key not in seen:
                    seen.add(key)
                    self.transformations.append({
                        "type":        "filter",
                        "table":       name,
                        "description": f"WHERE {opts['where'][:80]}",
                    })

            if 'group_by' in opts:
                key = f"GROUP:{name}"
                if key not in seen:
                    seen.add(key)
                    self.transformations.append({
                        "type":        "aggregation",
                        "table":       name,
                        "description": f"GROUP BY {opts['group_by'][:80]}",
                    })

        # DISTINCT (global scan, deduped)
        if re.search(r'\bDISTINCT\b', self.loadscript, re.IGNORECASE):
            self.transformations.append({
                "type":        "deduplication",
                "table":       "global",
                "description": "DISTINCT",
            })

        # ORDER BY (global scan)
        for m in re.finditer(r'\bORDER\s+BY\b\s+([^;]+)', self.loadscript, re.IGNORECASE):
            clause = m.group(1).strip()[:80]
            key = f"ORDER:{clause}"
            if key not in seen:
                seen.add(key)
                self.transformations.append({
                    "type":        "sorting",
                    "table":       "global",
                    "description": f"ORDER BY {clause}",
                })

    # ------------------------------------------------------------------
    # Step 5.7 — JOINs
    # ------------------------------------------------------------------

    def _extract_joins(self):
        """
        Extract JOIN operations — both SQL-style and Qlik-style.
        SQL:  INNER JOIN table ON condition
        Qlik: Join (TableName) / Left Join (TableName)
        """
        logger.debug("Extracting JOIN operations...")

        # SQL-style joins
        for jtype in ['INNER JOIN', 'LEFT JOIN', 'RIGHT JOIN', 'FULL JOIN', 'CROSS JOIN']:
            pattern = rf'{jtype}\s+(\S+)\s+(?:ON|WHERE)\s+([^;]*)'
            for m in re.finditer(pattern, self.loadscript, re.IGNORECASE):
                table     = m.group(1).strip()
                condition = m.group(2).strip()[:80]
                self.joins.append({
                    "type":        jtype,
                    "table":       table,
                    "description": f"{jtype} {table} ON {condition}",
                })

        # Qlik-style modifiers: Join (Table), Left Join (Table), etc.
        for m in re.finditer(
            r'\b(Left|Right|Inner|Outer)?\s*(Join|Keep)\b\s*(?:\(([^)]+)\))?',
            self.loadscript, re.IGNORECASE
        ):
            prefix     = (m.group(1) or '').strip()
            join_kw    = m.group(2).strip()
            join_table = (m.group(3) or '').strip()
            label      = f"{prefix} {join_kw}".strip()
            self.joins.append({
                "type":        label,
                "table":       join_table,
                "description": f"{label} ({join_table})" if join_table else label,
            })

    # ------------------------------------------------------------------
    # Step 5.8 — Variables
    # ------------------------------------------------------------------

    def _extract_variables(self):
        """Extract LET / SET variable definitions."""
        logger.debug("Extracting variable definitions...")

        for m in re.finditer(
            r'\b(LET|SET)\b\s+(\w+)\s*=\s*([^;]*)',
            self.loadscript, re.IGNORECASE
        ):
            self.variables.append({
                "name":    m.group(2),
                "value":   m.group(3).strip()[:80],
                "keyword": m.group(1).upper(),
                "type":    "let_set",
            })


# ---------------------------------------------------------------------------
# Standalone testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_script = """
    SET ThousandSep=',';

    RegionMap:
    MAPPING LOAD city, region
    FROM [lib://DataFiles/dim_regions.csv]
    (txt, utf8, embedded labels, delimiter is ',');

    Customers:
    LOAD
        customer_id,
        customer_name,
        city,
        segment
    FROM [lib://DataFiles/dim_customers_20K.csv]
    (txt, utf8, embedded labels, delimiter is ',');

    Products:
    LOAD
        product_id,
        product_name,
        category,
        price
    FROM [lib://DataFiles/dim_products_5K.csv]
    (txt, utf8, embedded labels, delimiter is ',');

    Dates:
    LOAD
        date_id,
        year,
        month,
        day
    FROM [lib://DataFiles/dim_dates.csv]
    (txt, utf8, embedded labels, delimiter is ',');

    Sales_Raw:
    LOAD
        order_id,
        customer_id,
        product_id,
        date_id,
        quantity,
        amount
    FROM [lib://DataFiles/fact_sales_1M.csv]
    (txt, utf8, embedded labels, delimiter is ',');

    Sales:
    LOAD
        order_id,
        customer_id,
        product_id,
        date_id,
        quantity,
        amount,
        quantity * amount as total_value
    RESIDENT Sales_Raw;

    DROP TABLE Sales_Raw;
    """

    parser = LoadScriptParser(sample_script)
    result = parser.parse()

    print("\n" + "=" * 60)
    print("PARSE SUMMARY")
    print("=" * 60)
    print(f"Status:        {result['status']}")
    print(f"Tables:        {result['summary']['tables_count']}")
    print(f"Fields:        {result['summary']['fields_count']}")

    print("\n--- Tables ---")
    for t in result['details']['tables']:
        print(f"  {t['name']:20} [{t['source_type']:8}]  {t['source_path']}")
        opts = t.get('options', {})
        if opts.get('is_dropped_resident'):
            print(f"    ↳ is_dropped_resident=True  raw_source_path='{opts.get('raw_source_path')}'")
        for f in t['fields'][:3]:
            print(f"    • {f['name']:20} ({f['type']})")
        if len(t['fields']) > 3:
            print(f"    ... +{len(t['fields'])-3} more")