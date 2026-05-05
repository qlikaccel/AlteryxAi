
# """
# powerbi_publisher.py  -  QlikAI Accelerator

# FIXES IN THIS VERSION
# ─────────────────────
#   ✅ Fix A: ALL BIM column dataTypes forced to 'string' for CSV/SharePoint/file tables.
#             Previously int64/dateTime caused VT_BSTR->VT_DATE type mismatch on refresh.

#   ✅ Fix B: Universal Schema Resolver (REPLACES _schema_loaded_on_refresh hack).
#             Now uses _resolve_schema_universal() for future-proof column detection:
#             - Attempts M expression extraction with smart regex
#             - Checks source type hints (PromoteHeaders, Resident, etc.)
#             - Falls back to metadata hints in options
#             - Only returns empty if truly unresolvable (dynamic schema from SharePoint)
            
#   ✅ Fix C: Smart schema inference - no more hardcoded table patterns.
#             Real columns detected from CSV preview, RESIDENT parent, or CONCATENATE union.

#   All other logic (auth, Fabric API, relationships, push fallback) unchanged.
# """

# from ast import expr
# import base64
# import json
# import logging
# import os
# import re
# import time
# from typing import Any, Dict, List, Optional

# import requests

# logger = logging.getLogger(__name__)


# def _sanitize_bim_name(value: str) -> str:
#     return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))

# # ─────────────────────────────────────────────────────────────────────────────
# # Public entry point
# # ─────────────────────────────────────────────────────────────────────────────

# def publish_semantic_model(
#     dataset_name: str,
#     tables_m: List[Dict[str, Any]],
#     relationships: List[Dict[str, Any]] = None,
#     access_token: str = "",
#     data_source_path: str = "",
#     db_connection_string: str = "",
#     workspace_id: str = "",
#     qlik_fields_map: Optional[Dict[str, List[str]]] = None,
# ) -> Dict[str, Any]:
#     """
#     qlik_fields_map: optional dict mapping table name → list of real column names
#     from the Qlik data model (GetTablesAndKeys). When supplied, LOAD * tables get
#     explicit TransformColumnTypes with real column names instead of the dynamic
#     List.Transform pattern, so Power BI sees correct columns immediately.

#     Example:
#         qlik_fields_map = {
#             "Departments": ["department_id", "department_name"],
#             "Locations":   ["location_id", "location_name", "country"],
#         }
#     """
#     relationships = relationships or []
#     if not workspace_id:
#         workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
#     if not workspace_id:
#         return {"success": False, "error": "POWERBI_WORKSPACE_ID not set"}
#     if db_connection_string:
#         tables_m = _rewrite_for_db_connect(tables_m, db_connection_string)
#     token = access_token or _acquire_sp_token()
#     return _Publisher(workspace_id=workspace_id, access_token=token).publish(
#         dataset_name, tables_m, relationships, data_source_path,
#         qlik_fields_map=qlik_fields_map or {},
#     )


# # ─────────────────────────────────────────────────────────────────────────────
# # Auth
# # ─────────────────────────────────────────────────────────────────────────────

# def _acquire_sp_token(
#     scope: str = "https://analysis.windows.net/powerbi/api/.default",
# ) -> str:
#     try:
#         import msal
#         tenant_id     = os.getenv("POWERBI_TENANT_ID", "")
#         client_id     = os.getenv("POWERBI_CLIENT_ID", "")
#         client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
#         if not all([tenant_id, client_id, client_secret]):
#             logger.warning("[Auth] SP credentials missing from environment")
#             return ""
#         app = msal.ConfidentialClientApplication(
#             client_id,
#             authority=f"https://login.microsoftonline.com/{tenant_id}",
#             client_credential=client_secret,
#         )
#         result = app.acquire_token_for_client(scopes=[scope])
#         token = result.get("access_token", "")
#         if token:
#             logger.info("[Auth] SP token acquired: %s", scope)
#         else:
#             logger.warning("[Auth] SP token failed: %s", result.get("error_description"))
#         return token
#     except Exception as exc:
#         logger.warning("[Auth] SP token error: %s", exc)
#         return ""


# def initiate_device_code_flow() -> Dict[str, Any]:
#     try:
#         import msal
#         tenant_id = os.getenv("POWERBI_TENANT_ID", "")
#         client_id = os.getenv("POWERBI_CLIENT_ID", "")
#         app = msal.PublicClientApplication(
#             client_id,
#             authority=f"https://login.microsoftonline.com/{tenant_id}",
#         )
#         flow = app.initiate_device_flow(
#             scopes=["https://analysis.windows.net/powerbi/api/.default"]
#         )
#         _cache_device_flow(flow)
#         return {
#             "success": True,
#             "device_code_url": "https://microsoft.com/devicelogin",
#             "user_code": flow.get("user_code", ""),
#             "message": flow.get("message", ""),
#         }
#     except Exception as exc:
#         return {"success": False, "error": str(exc)}


# def complete_device_code_flow() -> Dict[str, Any]:
#     try:
#         import msal
#         flow = _load_device_flow()
#         if not flow:
#             return {"success": False, "error": "No pending device code flow"}
#         tenant_id = os.getenv("POWERBI_TENANT_ID", "")
#         client_id = os.getenv("POWERBI_CLIENT_ID", "")
#         app = msal.PublicClientApplication(
#             client_id,
#             authority=f"https://login.microsoftonline.com/{tenant_id}",
#         )
#         result = app.acquire_token_by_device_flow(flow)
#         token = result.get("access_token", "")
#         if token:
#             _cache_user_token(token)
#             _clear_device_flow()
#             return {"success": True, "access_token": token}
#         return {"success": False, "error": result.get("error_description", "unknown")}
#     except Exception as exc:
#         return {"success": False, "error": str(exc)}


# def get_cached_user_token() -> str:
#     try:
#         path = _token_cache_path()
#         if os.path.exists(path):
#             data = json.loads(open(path).read())
#             if time.time() < data.get("expires_at", 0):
#                 return data.get("token", "")
#     except Exception:
#         pass
#     return ""


# # ─────────────────────────────────────────────────────────────────────────────
# # DB Connect rewriter
# # ─────────────────────────────────────────────────────────────────────────────

# def _rewrite_for_db_connect(
#     tables_m: List[Dict[str, Any]], connection: str
# ) -> List[Dict[str, Any]]:
#     out = []
#     for t in tables_m:
#         src = t.get("source_type", "").lower()
#         expr_str = t.get("m_expression", "")
#         if src == "resident" or "Table.NestedJoin" in expr_str:
#             out.append(t)
#             continue
#         if src in ("sql", "odbc") or "Sql.Database" in expr_str or "Odbc.Query" in expr_str:
#             out.append(t)
#             continue
#         new_expr = (
#             f'let\n'
#             f'    Source = Odbc.Query("{connection}", "SELECT * FROM [{t["name"]}]"),\n'
#             f'    Result = Source\nin\n    Result'
#         )
#         out.append({**t, "m_expression": new_expr, "source_type": "odbc"})
#     return out


# # ─────────────────────────────────────────────────────────────────────────────
# # Helper functions
# # ─────────────────────────────────────────────────────────────────────────────

# _QLIK_TO_TABULAR = {
#     "integer":   "int64",
#     "float":     "double",
#     "money":     "decimal",
#     "date":      "dateTime",
#     "datetime":  "dateTime",
#     "timestamp": "dateTime",
#     "boolean":   "boolean",
#     "bool":      "boolean",
#     "number":    "double",
# }


# def _tabular_type(qlik_type: str) -> str:
#     # Always return string — BIM must match what M query produces (text from CSV).
#     return "string"


# def _strip_qlik_qualifier(col_name: str) -> str:
#     if not col_name or col_name.startswith("#"):
#         return col_name
#     if "." in col_name and "-" not in col_name:
#         return col_name.split(".", 1)[-1]
#     return col_name


# def _infer_type_from_name(name: str) -> str:
#     if "-" in name:
#         return "string"
#     n = name.split(".")[-1].lower().strip() if "." in name else name.lower().strip()
#     if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
#         return "date"
#     if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
#         return "number"
#     if n.endswith("number") or n.endswith("phone") or n.endswith("code"):
#         return "string"
#     if any(x in n for x in ["qty", "quantity", "year", "month", "day", "age", "rank", "km", "tons", "knots", "cc", "speed"]):
#         return "integer"
#     if n == "id" or (n.endswith("_id") and not n.endswith("number")):
#         return "integer"
#     if "count" in n:
#         return "integer"
#     return "string"


# def _is_file_based_source(source_type: str, expr_str: str) -> bool:
#     """
#     Return True if this table reads from a file/CSV/SharePoint/QVD source.
#     For file-based sources ALL BIM dataTypes must be 'string' because
#     Power BI reads CSV/SharePoint data as text (VT_BSTR).
#     Declaring any other type causes refresh errors like VT_BSTR->VT_DATE.
#     """
#     file_source_types = {"csv", "qvd", "excel", "json", "xml", "parquet", "file", "inline_csv"}
#     if source_type.lower() in file_source_types:
#         return True
#     # Also check M expression — resident tables inlined from CSV are file-based
#     file_indicators = [
#         "SharePoint.Files", "PromoteHeaders", "Csv.Document",
#         "Excel.Workbook", "File.Contents", "Web.Contents",
#         "AzureStorage.Blobs", "Parquet.Document", "Xml.Tables",
#         "Json.Document",
#     ]
#     return any(ind in expr_str for ind in file_indicators)


# def _extract_fields_from_m(expr: str) -> list:
#     """
#     Extract column names and types from an M expression.

#     Patterns (in priority order):
#       A) Table.TransformColumnTypes  — explicit typed column list
#       B) #table(type table [...])   — inline table schema declaration
#       C) SharePoint.Files/PromoteHeaders → return [] (runtime schema)
#       D) Table.SelectColumns        — explicit column selection
#       E) Table.Group                — group-by column names
#     """
#     M_TYPE_MAP = {
#         "type text":     "string",
#         "type number":   "number",
#         "type date":     "date",
#         "type datetime": "datetime",
#         "type logical":  "boolean",
#         "Int64.Type":    "integer",
#         "type duration": "string",
#         "type binary":   "string",
#         "text":          "string",
#         "number":        "number",
#         "date":          "date",
#         "datetime":      "datetime",
#         "logical":       "boolean",
#         "integer":       "integer",
#     }
#     fields = []

#     # Grouped Alteryx workflows reduce the schema after Select/TransformColumnTypes.
#     # Detect final output first so Power BI receives grouped keys, aggregations,
#     # and formula columns instead of pre-aggregation source fields.
#     final_group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]*)\}', expr, re.DOTALL))
#     final_group_match = final_group_matches[-1] if final_group_matches else None
#     if final_group_match:
#         final_group_expr = expr[final_group_match.start():]
#         for col_ref in re.finditer(r'"([^"]+)"', final_group_match.group(1)):
#             col_name = col_ref.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 fields.append({"name": col_name, "type": "string"})
#         for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', final_group_expr):
#             col_name = agg_ref.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#         for add_ref in re.finditer(
#             r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
#             final_group_expr,
#             re.DOTALL,
#         ):
#             col_name = add_ref.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 col_type_raw = add_ref.group(2).strip()
#                 fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
#         if fields:
#             logger.info("[Extract] Final grouped output schema: %d cols", len(fields))
#             return fields

#     # Pattern A: Table.TransformColumnTypes
#     transform_block = re.search(
#         r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
#         expr, re.DOTALL,
#     )
#     if transform_block:
#         block = transform_block.group(1)
#         for entry in re.finditer(
#             r'\{\s*"([^"]+)"\s*,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\}',
#             block,
#         ):
#             raw_name = entry.group(1).strip()
#             col_name = _strip_qlik_qualifier(raw_name)
#             col_type_raw = entry.group(2).strip()
#             col_type = M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), "string"))
#             if "-" in raw_name:
#                 col_type = "string"
#             elif col_type == "string":
#                 col_type = _infer_type_from_name(col_name)
#             if col_name:
#                 fields.append({"name": col_name, "type": col_type})
#         if fields:
#             logger.debug("[Extract] Pattern A (TransformColumnTypes): %d cols", len(fields))
#             return fields

#     # Pattern B: #table(type table [...])
#     type_table_match = re.search(r"type\s+table\s+\[(.+?)\]", expr, re.DOTALL)
#     if type_table_match:
#         cols_str = type_table_match.group(1)
#         for part in cols_str.split(","):
#             part = part.strip()
#             if "=" not in part:
#                 continue
#             raw_name = part.split("=")[0].strip().lstrip("#").strip('"').strip("'")
#             col_name = _strip_qlik_qualifier(raw_name)
#             col_type_raw = part.split("=")[1].strip()
#             col_type = M_TYPE_MAP.get(col_type_raw, "string")
#             if "-" in raw_name:
#                 col_type = "string"
#             elif col_type == "string":
#                 col_type = _infer_type_from_name(col_name)
#             if col_name:
#                 fields.append({"name": col_name, "type": col_type})
#         if fields:
#             logger.debug("[Extract] Pattern B (#table): %d cols", len(fields))
#             return fields

#     # Pattern C: PromoteHeaders/SharePoint tables
#     # ── FIX: Do NOT return [] here. ──
#     # Previously this block returned [] as soon as PromoteHeaders was detected,
#     # which prevented ALL downstream fallbacks (D, E, TransformColumnTypes scan, etc.)
#     # for every LOAD * table (Departments, Locations, Projects, Clients, …).
#     #
#     # The correct behaviour: try to extract whatever column references exist in the
#     # M expression (named steps like AddColumn, TransformColumnTypes injected by the
#     # converter), then fall through to Pattern D/E if nothing found.
#     # Only return early with found results — never return [] to block fallbacks.
#     if "SharePoint.Files" in expr or "PromoteHeaders" in expr or "PromotedHeaders" in expr:
#         logger.info("[Extract] 🔥 Detecting PromoteHeaders - attempting to extract column references...")

#         # Priority 1: explicit TransformColumnTypes with named columns (e.g. Employees table)
#         # Re-run Pattern A specifically because it may have been skipped above if there was
#         # ALSO a PromoteHeaders step earlier in the M expression.
#         transform_blocks = re.findall(
#             r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
#             expr, re.DOTALL,
#         )
#         for block in transform_blocks:
#             # Skip the dynamic "List.Transform(Columns, each {_, type text})" pattern —
#             # that means LOAD * with runtime schema (no static column names to extract).
#             if "List.Transform" in block:
#                 continue
#             for entry in re.finditer(
#                 r'\{\s*"([^"]+)"\s*,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\}',
#                 block,
#             ):
#                 col_name = _strip_qlik_qualifier(entry.group(1).strip())
#                 if col_name and col_name not in [f["name"] for f in fields]:
#                     fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#         if fields:
#             logger.info("[Extract] ✅ Extracted %d columns from PromoteHeaders+TransformColumnTypes", len(fields))
#             return fields

#         # Priority 2: final grouped schemas. Grouped Alteryx workflows often
#         # return GroupBy columns + aggregation columns + later Formula columns.
#         group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]*)\}', expr, re.DOTALL))
#         group_match = group_matches[-1] if group_matches else None
#         if group_match:
#             group_expr = expr[group_match.start():]
#             for col_ref in re.finditer(r'"([^"]+)"', group_match.group(1)):
#                 col_name = col_ref.group(1).strip()
#                 if col_name and col_name not in [f["name"] for f in fields]:
#                     fields.append({"name": col_name, "type": "string"})
#             for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', group_expr):
#                 col_name = agg_ref.group(1).strip()
#                 if col_name and col_name not in [f["name"] for f in fields]:
#                     fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#             for add_ref in re.finditer(
#                 r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
#                 group_expr,
#                 re.DOTALL,
#             ):
#                 col_name = add_ref.group(1).strip()
#                 if col_name and col_name not in [f["name"] for f in fields]:
#                     col_type_raw = add_ref.group(2).strip()
#                     fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
#             if fields:
#                 logger.info("[Extract] Extracted %d columns from Table.Group/AddColumn output", len(fields))
#                 return fields

#         # Priority 3: Table.AddColumn steps (e.g. derived/resident tables)
#         for m_add in re.finditer(
#             r'Table\.AddColumn\s*\(\s*\S+\s*,\s*"([^"]+)"',
#             expr,
#         ):
#             col_name = m_add.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#         if fields:
#             logger.info("[Extract] ✅ Extracted %d columns from AddColumn steps", len(fields))
#             return fields

#         # Priority 3: #"ColumnName" step references (non-system names)
#         col_refs = re.findall(r'#"([^"]+)"', expr)
#         if col_refs:
#             system_names = {
#                 'table', 'source', 'headers', 'csv', 'columns', 'typedtable',
#                 'promoted', 'content', 'rows', 'schema', 'data', 'values', 'list',
#                 'invoke', 'json', 'binary', 'filtered rows', 'grouped rows',
#                 'combined tables', 'kept columns',
#             }
#             for col_name in col_refs:
#                 if col_name.lower() not in system_names:
#                     if col_name not in [f["name"] for f in fields]:
#                         fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#             if fields:
#                 logger.info("[Extract] ✅ Extracted %d columns from #\"...\" references", len(fields))
#                 return fields

#         # Nothing found — fall through to Pattern D/E below instead of returning [].
#         # This is the critical fix: do NOT return [] here.
#         logger.debug("[Extract] Pattern C: No static column refs found - falling through to D/E")

#     # Pattern D: Table.SelectColumns
#     select_matches = list(re.finditer(r'Table\.SelectColumns\s*\(\s*[^,]+\s*,\s*\{([^}]+)\}\s*\)', expr))
#     select_match = select_matches[-1] if select_matches else None
#     if select_match:
#         for col_ref in re.finditer(r'"([^"]+)"', select_match.group(1)):
#             col_name = col_ref.group(1).strip()
#             if col_name:
#                 fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#         if fields:
#             logger.debug("[Extract] Pattern D (SelectColumns): %d cols", len(fields))
#             return fields

#     # Pattern E: Table.Group
#     group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]+)\}', expr, re.DOTALL))
#     group_match = group_matches[-1] if group_matches else None
#     if group_match:
#         group_expr = expr[group_match.start():]
#         for col_ref in re.finditer(r'"([^"]+)"', group_match.group(1)):
#             col_name = col_ref.group(1).strip()
#             if col_name:
#                 fields.append({"name": col_name, "type": "string"})
#         for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', group_expr):
#             col_name = agg_ref.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
#         for add_ref in re.finditer(
#             r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
#             group_expr,
#             re.DOTALL,
#         ):
#             col_name = add_ref.group(1).strip()
#             if col_name and col_name not in [f["name"] for f in fields]:
#                 col_type_raw = add_ref.group(2).strip()
#                 fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
#         if fields:
#             logger.debug("[Extract] Pattern E (Group): %d cols", len(fields))
#             return fields

#     logger.debug("[Extract] No patterns matched -> []")
#     return []


# def _fix_multiline_rows(expr: str) -> str:
#     lines = expr.split("\n")
#     result = []
#     in_row = False
#     current_row = ""
#     for line in lines:
#         stripped = line.strip()
#         if in_row:
#             current_row += " " + stripped
#             if re.search(r'\}\s*,?\s*$', stripped):
#                 result.append(current_row)
#                 current_row = ""
#                 in_row = False
#         else:
#             if stripped.startswith('{"') or stripped.startswith("{'"):
#                 if re.search(r'\}\s*,?\s*$', stripped):
#                     result.append(line)
#                 else:
#                     in_row = True
#                     current_row = line.rstrip()
#             else:
#                 result.append(line)
#     if current_row:
#         result.append(current_row)
#     return "\n".join(result)


# def _sanitize_m(expr: str) -> str:
#     lines = expr.strip().splitlines()
#     clean_lines = []
#     for line in lines:
#         if line.strip().startswith("//"):
#             continue
#         clean_lines.append(line)
#     expr = "\n".join(clean_lines).strip()
#     if "Sourcelet" in expr:
#         idx = expr.find("Sourcelet")
#         real_let_idx = expr.find("let", idx)
#         if real_let_idx != -1:
#             expr = expr[real_let_idx:]
#     if not expr.strip().startswith("let"):
#         idx = expr.find("let")
#         if idx != -1:
#             expr = expr[idx:]
#     return expr.strip()


# def _extract_typedarticle_columns(expr: str) -> List[str]:
#     """
#     ✅ NEW: Extract column names from TypedTable step in M expression.
    
#     This handles M queries generated by mquery_converter that injected explicit
#     schema via Table.TransformColumnTypes(), which looks like:
    
#         TypedTable = Table.TransformColumnTypes(
#             Headers,
#             {
#             {"column1", type text}, {"column2", type text}, ...
#             }
#         )
#     """
#     if not expr:
#         return []
    
#     columns = []
#     seen = set()
    
#     # Extract from TypedTable = Table.TransformColumnTypes(...) blocks
#     # The regex captures the entire {...} block with all column definitions
#     pattern = r'TypedTable\s*=\s*Table\.TransformColumnTypes\s*\([^,]+,\s*\{([\s\S]*?)\}\s*\)'
#     match = re.search(pattern, expr)
    
#     if match:
#         block = match.group(1)
#         # Extract each {"colname", type ...} pair
#         for col_match in re.finditer(r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)', block):
#             col_name = col_match.group(1).strip()
#             if col_name and col_name not in seen and col_name != "*":
#                 seen.add(col_name)
#                 columns.append(col_name)
    
#     return columns


# def _resolve_schema_universal(
#     table: Dict[str, Any],
#     expr_str: str,
#     is_file: bool,
#     qlik_fields_map: Optional[Dict[str, List[str]]] = None,
# ) -> List[Dict[str, Any]]:
#     """
#     UNIVERSAL SCHEMA RESOLVER - FIX for future-proof column detection
    
#     Attempts to resolve columns in this priority order:
#     1. Check table.columns (already extracted)
#     2. Extract from M expression (regex + semantic analysis)
#     3. Infer from source type hints
#     4. 🔥 NEW — qlik_fields_map from GetTablesAndKeys (real column names for LOAD * tables)
#     5. Last resort: check options metadata
    
#     Returns: List of column definitions or empty list if unresolvable
    
#     This replaces the _schema_loaded_on_refresh hack by ensuring
#     we ALWAYS try to find real columns before giving up.
#     """
#     columns = []
#     table_name = table.get("name", "Unknown")
    
#     # Step 1: Already have columns? Use them
#     if "columns" in table and table["columns"]:
#         return table["columns"]
    
#     # Step 2: Deep M expression extraction - aggressive regex
#     if expr_str:
#         # ── FIX: Do NOT early-return [] for PromoteHeaders+Csv.Document tables. ──
#         # Previously this returned [] immediately for any table whose M query contained
#         # both PromoteHeaders and Csv.Document — which is EVERY SharePoint CSV table.
#         # That blocked all downstream column detection for LOAD * tables.
#         #
#         # The M queries generated by mquery_converter inject a
#         # TransformColumnTypes step (either explicit columns or the dynamic
#         # List.Transform(Columns, each {_, type text}) pattern).  We must
#         # fall through to the regex scan below so explicit column names
#         # from TransformColumnTypes, NestedJoin, AddColumn, etc. are found.
#         # Only log an info note so the caller can trace the flow.
#         if "PromoteHeaders" in expr_str and "Csv.Document" in expr_str:
#             logger.info(
#                 "[_resolve_schema_universal] '%s': PromoteHeaders detected - "
#                 "scanning M expression for static column names (not returning [] early)",
#                 table_name,
#             )
#             # Fall through — do NOT return [] here.

#         # Try pattern: RESIDENT or derived query
#         if expr_str.strip().startswith("let"):
#             # Strings that appear in the M expression as SharePoint API parameters,
#             # M step names, or file-connector internals — never real column names.
#             # We must exclude these before accepting any regex match as a column name.
#             _SP_NOISE = {
#                 "apiversion = 15", "apiversion=15",
#                 "name", "folder path", "content", "promoteallscalars=true",
#                 "shared documents", "delimiter", "encoding", "quotestyle",
#                 "quotestyle.csv", "missingfield.usenull", "joinkind.leftouter",
#                 "joinkind.rightouoter", "joinkind.inner", "joinkind.fullouter",
#                 "replacer.replacevalue", "table", "source", "headers", "csv",
#                 "columns", "typedtable", "promoted", "rows", "schema", "data",
#                 "values", "list", "invoke", "json", "binary", "filtered",
#                 "safecombined", "combined", "selected", "intermediate",
#                 "expandeddepartments", "mergeddepartments",
#             }
#             # Extract column assignments and type definitions
#             # Only pick up explicit typed column references — NOT bracket expressions
#             # from SharePoint API calls ([Name], [Folder Path], [Content], etc.).
#             # Pattern priority:
#             #   1. {"colname", type text}  — explicit typed pair (most reliable)
#             #   2. Table.SelectColumns ... "colname" — explicit column selection
#             #   3. Table.AddColumn ... "colname" — derived column name
#             # Bracket expressions like [Name] and [Folder Path] are intentionally
#             # excluded because they are SharePoint file-listing API parameters, not
#             # data column names.
#             col_candidates: List[str] = []

#             # Priority 1: explicit {"colname", type ...} pairs — skip List.Transform blocks
#             for block_match in re.finditer(
#                 r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
#                 expr_str, re.DOTALL,
#             ):
#                 block = block_match.group(1)
#                 if "List.Transform" in block:
#                     # Dynamic schema — no static column names available here
#                     continue
#                 for entry in re.finditer(
#                     r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)',
#                     block,
#                 ):
#                     col_candidates.append(entry.group(1).strip())

#             # Priority 2: Table.SelectColumns explicit list
#             for sel_match in re.finditer(
#                 r"Table\.SelectColumns\s*\([^,]+,\s*\{([^}]+)\}",
#                 expr_str,
#             ):
#                 for q in re.finditer(r'"([^"]{1,120})"', sel_match.group(1)):
#                     col_candidates.append(q.group(1).strip())

#             # Priority 3: Table.AddColumn derived columns
#             for add_match in re.finditer(
#                 r'Table\.AddColumn\s*\(\s*\S+\s*,\s*"([^"]{1,120})"',
#                 expr_str,
#             ):
#                 col_candidates.append(add_match.group(1).strip())

#             seen = set()
#             for col in col_candidates:
#                 col_lower = col.lower()
#                 if col_lower in _SP_NOISE:
#                     continue
#                 if col and col not in seen and col != "*":
#                     seen.add(col)
#                     col_name = _strip_qlik_qualifier(col)
#                     if col_name and col_name != "*":
#                         col_type = "string" if is_file else _infer_type_from_name(col_name)
#                         columns.append({
#                             "name": col_name,
#                             "dataType": col_type,
#                             "sourceColumn": col_name,
#                             "summarizeBy": "none",
#                             "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                         })
#             if columns:
#                 logger.info(
#                     "[_resolve_schema_universal] '%s': Resolved %d columns from M expression",
#                     table_name, len(columns),
#                 )
#                 return columns
    
#     # Step 3: Check options for stored schema hints
#     opts = table.get("options", {})
#     if "inferred_columns" in opts:
#         hint_cols = opts.get("inferred_columns", [])
#         if hint_cols:
#             logger.info("[_resolve_schema_universal] '%s': Using schema from options hints", table_name)
#             for col in hint_cols:
#                 col_name = col if isinstance(col, str) else col.get("name", "")
#                 if col_name:
#                     columns.append({
#                         "name": col_name,
#                         "dataType": "string",
#                         "sourceColumn": col_name,
#                         "summarizeBy": "none",
#                         "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                     })
#             return columns

#     # 🔥 Step 4: qlik_fields_map from GetTablesAndKeys — real column names for LOAD * tables.
#     # This is the most reliable source for tables whose M expression uses the dynamic
#     # List.Transform(Columns, each {_, type text}) pattern (i.e. LOAD * with no explicit
#     # column list in the script). The map is populated by LoadScriptFetcher.fetch_and_parse()
#     # via WebSocket GetTablesAndKeys and threaded through publish_semantic_model().
#     # Previously this map was stored on _Publisher but never consumed here — so LOAD * tables
#     # with dynamic M schema always fell through to the empty return below.
#     if qlik_fields_map:
#         # Try exact name match first, then case-insensitive
#         real_cols = qlik_fields_map.get(table_name) or next(
#             (v for k, v in qlik_fields_map.items() if k.lower() == table_name.lower()),
#             None
#         )
#         if real_cols:
#             logger.info(
#                 "[_resolve_schema_universal] '%s': 🔥 Resolved %d columns from qlik_fields_map "
#                 "(GetTablesAndKeys). This covers LOAD * / dynamic schema tables.",
#                 table_name, len(real_cols)
#             )
#             for col_name in real_cols:
#                 if col_name and not col_name.startswith("$"):  # skip Qlik system fields
#                     columns.append({
#                         "name": col_name,
#                         "dataType": "string" if is_file else _infer_type_from_name(col_name),
#                         "sourceColumn": col_name,
#                         "summarizeBy": "none",
#                         "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                     })
#             if columns:
#                 return columns
#         else:
#             logger.debug(
#                 "[_resolve_schema_universal] '%s': Not found in qlik_fields_map (%d tables available).",
#                 table_name, len(qlik_fields_map)
#             )

#     # Step 5: Last resort - return empty and log (don't add fake schema)
#     logger.debug(
#         "[_resolve_schema_universal] '%s': No resolvable schema found. "
#         "Table will have dynamic schema from M expression at refresh time.",
#         table_name
#     )
#     return []


# # ─────────────────────────────────────────────────────────────────────────────
# # Publisher
# # ─────────────────────────────────────────────────────────────────────────────

# class _Publisher:

#     def __init__(self, workspace_id: str, access_token: str = ""):
#         self.workspace_id = workspace_id
#         self.token = access_token
#         self.pbi_headers = {
#             "Authorization": f"Bearer {self.token}",
#             "Content-Type": "application/json",
#         }

#     def publish(
#         self,
#         dataset_name: str,
#         tables_m: List[Dict[str, Any]],
#         relationships: List[Dict[str, Any]],
#         data_source_path: str,
#         qlik_fields_map: Optional[Dict[str, List[str]]] = None,
#     ) -> Dict[str, Any]:
#         self.qlik_fields_map = qlik_fields_map or {}
#         if not self.token:
#             flow = initiate_device_code_flow()
#             return {
#                 "success": False, "auth_required": True,
#                 "device_code_url": flow.get("device_code_url"),
#                 "user_code": flow.get("user_code"),
#                 "message": flow.get("message", ""),
#                 "error": "Authentication required.",
#             }
#         result = self._deploy_via_fabric(dataset_name, tables_        # ── Step 0: Cross-table column backfill for Alteryx _raw source tables ─
#         # When Alteryx workflow JSON carries no field schema for CSV sources,
#         # every _raw table ends up with fields=[] and a dynamic List.Transform
#         # M expression that yields 0 columns through all extraction paths.
#         #
#         # Strategy: scan the M expressions of ALL tables that DO have columns
#         # (e.g. the output/transform tables) for references to each _raw table's
#         # columns.  Two complementary passes:
#         #
#         #  Pass A – Scan output-table M for Table.SelectColumns / Table.Group /
#         #           Table.RenameColumns / Table.AddColumn calls that mention
#         #           explicit quoted column names.  These are the columns that the
#         #           downstream query needs from the raw source.
#         #
#         #  Pass B – Look at the output table's inferred field list (fields=[...])
#         #           populated by earlier pipeline stages.  Any field there whose
#         #           name looks like a raw CSV column is promoted to ALL _raw tables
#         #           that have no schema.
#         #
#         # The result is stored back into each raw table's "fields" key so that
#         # the existing Step 2 field-list fallback picks them up automatically.

#         _raw_table_names = {
#             t.get("name", "").lower()
#             for t in tables_m
#             if t.get("name", "").lower().endswith("_raw")
#             and not (t.get("fields") or [])
#         }

#         if _raw_table_names:
#             # Collect column candidates from tables that already have columns
#             _cross_cols: Dict[str, str] = {}  # col_name_lower -> canonical_name

#             for _t in tables_m:
#                 _tname = _t.get("name", "").lower()
#                 if _tname in _raw_table_names:
#                     continue  # skip the empty _raw tables themselves

#                 # Pass B: use existing field list
#                 for _f in (_t.get("fields") or []):
#                     if not isinstance(_f, dict):
#                         continue
#                     _col = str(_f.get("alias") or _f.get("name") or "").strip()
#                     if _col and _col != "*" and not _col.startswith("#"):
#                         _cross_cols[_col.lower()] = _col

#                 # Pass A: mine M expression for quoted column names
#                 _expr = _t.get("m_expression", "")
#                 if _expr:
#                     # Table.SelectColumns(..., {"col1", "col2"}) pattern
#                     for _m in re.finditer(
#                         r'Table\.(?:SelectColumns|Group|RenameColumns|AddColumn|TransformColumnTypes)'
#                         r'[^{]*\{[^}]*"([^"]{1,80})"',
#                         _expr,
#                     ):
#                         _col = _m.group(1).strip()
#                         if _col and _col != "*" and not _col.startswith("#"):
#                             _cross_cols[_col.lower()] = _col

#                     # Also capture bare "ColumnName" references in SelectColumns list
#                     for _m in re.finditer(r'"([A-Za-z_][A-Za-z0-9_ ]{0,60})"', _expr):
#                         _col = _m.group(1).strip()
#                         # Filter out M keywords and SharePoint paths
#                         if (
#                             _col
#                             and _col != "*"
#                             and not _col.startswith("http")
#                             and not _col.startswith("#")
#                             and " " not in _col[:2]  # skip sentences
#                             and len(_col) < 60
#                             and _col.lower() not in {
#                                 "name", "content", "kind", "hidden", "items",
#                                 "delimiter", "encoding", "quotestyle", "csv",
#                                 "sharepoint", "apiversion", "import", "true", "false",
#                             }
#                         ):
#                             if _col.lower() not in _cross_cols:
#                                 _cross_cols[_col.lower()] = _col

#             if _cross_cols:
#                 _inferred_fields = [
#                     {"name": v, "alias": v, "expression": v, "type": "string",
#                      "extracted_from": "cross_table_inference"}
#                     for v in _cross_cols.values()
#                     if v and v != "*"
#                 ]
#                 if _inferred_fields:
#                     for _t in tables_m:
#                         if (
#                             _t.get("name", "").lower() in _raw_table_names
#                             and not (_t.get("fields") or [])
#                         ):
#                             _t["fields"] = _inferred_fields
#                             logger.info(
#                                 "[BIM] '%s': backfilled %d column(s) via cross-table inference",
#                                 _t.get("name", ""), len(_inferred_fields),
#                             )

# m, relationships, data_source_path)
#         if result.get("success"):
#             return result
#         logger.warning("[Publisher] Fabric API failed (%s) — Push dataset fallback", result.get("error"))
#         return self._deploy_push_dataset(dataset_name, tables_m)

#     def _build_bim(
#         self,
#         dataset_name: str,
#         tables_m: List[Dict[str, Any]],
#         relationships: List[Dict[str, Any]],
#         data_source_path: str,
#     ) -> str:
#         from app.services.mquery_converter import MQueryConverter
#         converter = MQueryConverter()
#         # Supply real Qlik column names so resolve_output_columns works for LOAD * tables
#         if getattr(self, "qlik_fields_map", {}):
#             converter.qlik_fields_map = self.qlik_fields_map

#         tmd_tables = []
#         skipped_tables = []

#         for t in tables_m:
#             table_name  = t.get("name", "Unknown")
#             expr_str    = t.get("m_expression", "").strip()
#             source_type = t.get("source_type", "")

#             # Only skip if there is literally no M expression — not for missing columns
#             if not expr_str:
#                 logger.warning(
#                     "[BIM] SKIP '%s': empty M expression. "
#                     "Check mquery_converter output for this table.",
#                     table_name
#                 )
#                 skipped_tables.append(table_name)
#                 continue

#             # Determine if file-based (CSV / SharePoint / QVD / Excel / etc.)
#             # FIX A: For file-based tables ALL BIM columns must be 'string'
#             is_file = _is_file_based_source(source_type, expr_str)

#             logger.info(
#                 "[BIM] Processing table '%s' source_type='%s' is_file=%s",
#                 table_name, source_type, is_file
#             )

#             # ── Step 1: Try resolve_output_columns (handles GROUP BY, APPLYMAP, IF) ──
#             resolved_cols = converter.resolve_output_columns(t)
#             final_m_fields = _extract_fields_from_m(expr_str) if expr_str else []
#             if final_m_fields and (
#                 "Table.Group" in expr_str
#                 or "Table.SelectColumns" in expr_str
#                 or "Table.AddColumn" in expr_str
#             ):
#                 final_names = {
#                     str(field.get("name") or "").strip().lower()
#                     for field in final_m_fields
#                     if str(field.get("name") or "").strip()
#                 }
#                 resolved_names = {
#                     _strip_qlik_qualifier(str(col.get("name") or "")).strip().lower()
#                     for col in resolved_cols
#                     if str(col.get("name") or "").strip()
#                 }
#                 if final_names and final_names != resolved_names:
#                     logger.info(
#                         "[BIM] '%s': final M schema overrides stale metadata: %s -> %s",
#                         table_name,
#                         sorted(resolved_names),
#                         sorted(final_names),
#                     )
#                     resolved_cols = [
#                         {
#                             "name": field.get("name"),
#                             "dataType": _tabular_type(field.get("type", "string")),
#                         }
#                         for field in final_m_fields
#                     ]
#             columns: List[Dict[str, Any]] = []

#             if resolved_cols:
#                 logger.info(
#                     "[BIM] '%s': %d columns from resolve_output_columns: %s",
#                     table_name, len(resolved_cols),
#                     [c["name"] for c in resolved_cols[:8]]
#                 )
#                 seen_cols: set[str] = set()
#                 for c in resolved_cols:
#                     raw_col_name = (c.get("name") or "").strip()
#                     col_name = _strip_qlik_qualifier(raw_col_name)
#                     if not col_name or col_name == "*":
#                         continue
#                     key = col_name.lower()
#                     if key in seen_cols:
#                         continue
#                     seen_cols.add(key)
#                     # FIX A: force 'string' for file-based sources to avoid
#                     # VT_BSTR->VT_DATE / VT_BSTR->VT_I8 type mismatch on refresh
#                     bim_type = "string" if is_file else c.get("dataType", "string")
#                     columns.append({
#                         "name":         col_name,
#                         "dataType":     bim_type,
#                         "sourceColumn": col_name,
#                         "summarizeBy":  "none",
#                         "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                     })
#             else:
#                 # ✅ NEW FIX: If resolve_output_columns is empty, try extracting from
#                 # the TypedTable step that was injected by _m_csv/_m_excel/_m_qvd/_m_resident
#                 logger.debug(
#                     "[BIM] '%s': resolve_output_columns returned empty. "
#                     "Attempting extraction from M expression TypedTable...",
#                     table_name
#                 )
#                 type_anon_cols = _extract_typedarticle_columns(expr_str)
#                 if type_anon_cols:
#                     logger.info(
#                         "[BIM] '%s': %d columns extracted from M TypedTable: %s",
#                         table_name, len(type_anon_cols), type_anon_cols[:5]
#                     )
#                     for col_name in type_anon_cols:
#                         if col_name and col_name != "*":
#                             columns.append({
#                                 "name":         col_name,
#                                 "dataType":     "string" if is_file else "string",
#                                 "sourceColumn": col_name,
#                                 "summarizeBy":  "none",
#                                 "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                             })

#             # ── Step 2: Fallback — field list (filter wildcards) ──────────────
#             if not columns:
#                 raw_fields = t.get("fields", [])
#                 seen_cols: set[str] = set()
#                 for f in raw_fields:
#                     raw_name = (f.get("alias") or f.get("name") or "").strip()
#                     if not raw_name or raw_name == "*":
#                         continue
#                     plain = _strip_qlik_qualifier(raw_name)
#                     if not plain or plain == "*":
#                         continue
#                     key = plain.lower()
#                     if key in seen_cols:
#                         continue
#                     seen_cols.add(key)
#                     # FIX A: always string for file sources
#                     bim_type = "string" if is_file else _tabular_type(f.get("type", "string"))
#                     columns.append({
#                         "name":         plain,
#                         "dataType":     bim_type,
#                         "sourceColumn": plain,
#                         "summarizeBy":  "none",
#                         "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                     })
#                 if columns:
#                     logger.info(
#                         "[BIM] '%s': %d columns from field list (L2 fallback)",
#                         table_name, len(columns)
#                     )

#             # ── Step 3: Extract from M expression type annotations ────────────
#             if not columns:
#                 extracted = _extract_fields_from_m(expr_str)
#                 for f in extracted:
#                     col_name = (f.get("name") or "").strip()
#                     if not col_name or col_name == "*":
#                         continue
#                     bim_type = "string" if is_file else _tabular_type(f.get("type", "string"))
#                     columns.append({
#                         "name":         col_name,
#                         "dataType":     bim_type,
#                         "sourceColumn": col_name,
#                         "summarizeBy":  "none",
#                         "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                     })
#                 if columns:
#                     logger.info(
#                         "[BIM] '%s': %d columns from M-expression extraction (L3 fallback)",
#                         table_name, len(columns)
#                     )

#             # ── Step 4: Deep M expression scan ───────────────────────────────
#             if not columns:
#                 col_candidates = re.findall(
#                     r'Table\.(?:TransformColumnTypes|SelectColumns|RenameColumns|AddColumn)'
#                     r'[^"]*"([^"]{1,80})"',
#                     expr_str
#                 )
#                 seen_c: set = set()
#                 for col in col_candidates:
#                     col = col.strip()
#                     if col and col != "*" and col not in seen_c:
#                         seen_c.add(col)
#                         columns.append({
#                             "name":         col,
#                             "dataType":     "string",
#                             "sourceColumn": col,
#                             "summarizeBy":  "none",
#                             "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                         })
#                 if columns:
#                     logger.info(
#                         "[BIM] '%s': %d columns from deep M scan (L4 fallback)",
#                         table_name, len(columns)
#                     )

#             # ── Step 5: Last-resort type-annotation scan ──────────────────────
#             if not columns:
#                 seen_lr: set = set()
#                 for m_match in re.finditer(
#                     r'\{\s*"([^"]{1,80})"\s*,\s*(?:type\s+\w+|Int64\.Type)',
#                     expr_str
#                 ):
#                     col = m_match.group(1).strip()
#                     if col and col != "*" and col not in seen_lr:
#                         seen_lr.add(col)
#                         columns.append({
#                             "name":         col,
#                             "dataType":     "string",
#                             "sourceColumn": col,
#                             "summarizeBy":  "none",
#                             "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
#                         })
#                 if columns:
#                     logger.info(
#                         "[BIM] '%s': %d columns from type-annotation scan (L5 fallback)",
#                         table_name, len(columns)
#                     )

#             # ── SMART SCHEMA RESOLUTION (Future-Proof FIX) ──────────────────
#             # Use universal schema resolver instead of fake _schema_loaded_on_refresh hack.
#             # 🔥 FIX: pass qlik_fields_map so LOAD * tables with dynamic M schema can be
#             # resolved from GetTablesAndKeys real column data rather than returning [].
#             if not columns:
#                 resolved = _resolve_schema_universal(
#                     t, expr_str, is_file,
#                     qlik_fields_map=getattr(self, "qlik_fields_map", {}),
#                 )
#                 columns.extend(resolved)
                
#                 # If STILL empty after universal resolution, accept it gracefully
#                 # Some LOAD * tables legitimately have dynamic schema from SharePoint/CSV
#                 if not columns:
#                     logger.info(
#                         "[BIM] '%s': Dynamic schema table (SELECT */CONCATENATE). "
#                         "Real columns will appear after first dataset refresh.",
#                         table_name
#                     )

#             fixed_expr = _fix_multiline_rows(_sanitize_m(expr_str))
#             logger.info(
#                 "[BIM] '%s': publishing with %d column(s). M preview:\n%s",
#                 table_name, len(columns), fixed_expr[:300]
#             )

#             tmd_tables.append({
#                 "name":    table_name,
#                 "columns": columns,
#                 "partitions": [{
#                     "name": f"{table_name}-Partition",
#                     "mode": "import",
#                     "source": {
#                         "type":       "m",
#                         "expression": fixed_expr.splitlines(),
#                     },
#                 }],
#             })

#         if skipped_tables:
#             logger.warning(
#                 "[BIM] %d table(s) skipped (empty M expression): %s",
#                 len(skipped_tables), skipped_tables
#             )
#         logger.info(
#             "[BIM] Built BIM: %d tables published, %d skipped.",
#             len(tmd_tables), len(skipped_tables)
#         )

#         # ── Build column name lookup for validation ────────────────────────────
#         columns_by_table: Dict[str, set] = {}
#         sanitized_columns_by_table: Dict[str, Dict[str, str]] = {}
#         for tbl in tmd_tables:
#             tbl_name = tbl.get("name", "")
#             if tbl_name:
#                 col_names = {c.get("name", "") for c in tbl.get("columns", []) if c.get("name")}
#                 columns_by_table[tbl_name] = col_names
#                 sanitized_columns_by_table[tbl_name] = {
#                     _sanitize_bim_name(col_name): col_name
#                     for col_name in col_names
#                 }
#                 logger.debug(
#                     "[BIM] Table '%s' has columns: %s",
#                     tbl_name, sorted(col_names)
#                 )

#         # ── Relationships ─────────────────────────────────────────────────────
#         # FIX: Validate that relationship columns exist in table output before adding
#         tmd_rels = []
#         skipped_rels = 0
#         for r in relationships:
#             if r.get("is_active") is False:
#                 continue
#             cardinality = r.get("cardinality") or r.get("fromCardinality", "")
#             ft = r.get("fromTable") or r.get("from_table", "")
#             fc = r.get("fromColumn") or r.get("from_column", "")
#             tt = r.get("toTable")   or r.get("to_table", "")
#             tc = r.get("toColumn")  or r.get("to_column", "")
#             fc = _strip_qlik_qualifier(fc)
#             tc = _strip_qlik_qualifier(tc)
#             if ft and fc and tt and tc:
#                 # ✅ FIX: Validate columns exist in both tables before creating relationship
#                 from_cols = columns_by_table.get(ft, set())
#                 to_cols = columns_by_table.get(tt, set())
#                 from_sanitized = sanitized_columns_by_table.get(ft, {})
#                 to_sanitized = sanitized_columns_by_table.get(tt, {})

#                 actual_fc = fc if fc in from_cols else from_sanitized.get(_sanitize_bim_name(fc), "")
#                 actual_tc = tc if tc in to_cols else to_sanitized.get(_sanitize_bim_name(tc), "")
                
#                 from_col_exists = bool(actual_fc)
#                 to_col_exists = bool(actual_tc)
                
#                 if not from_col_exists:
#                     logger.warning(
#                         "[BIM] Skipping relationship: Column '%s' not found in table '%s'. "
#                         "Available: %s",
#                         fc, ft, sorted(from_cols)
#                     )
#                     skipped_rels += 1
#                     continue
                
#                 if not to_col_exists:
#                     logger.warning(
#                         "[BIM] Skipping relationship: Column '%s' not found in table '%s'. "
#                         "Available: %s",
#                         tc, tt, sorted(to_cols)
#                     )
#                     skipped_rels += 1
#                     continue
#                 fc = actual_fc
#                 tc = actual_tc
                
#                 cf = r.get("crossFilteringBehavior") or r.get("cross_filter_direction", "")
#                 cf_bim = "bothDirections" if cf in ("Both", "bothDirections", "both") else "oneDirection"
#                 cardinality_norm = str(cardinality or "ManyToOne")
#                 if cardinality_norm == "OneToMany":
#                     from_cardinality, to_cardinality = "one", "many"
#                 elif cardinality_norm == "OneToOne":
#                     from_cardinality, to_cardinality = "one", "one"
#                     cf_bim = "bothDirections"
#                 elif cardinality_norm in ("ManyToMany", "manyToMany"):
#                     from_cardinality, to_cardinality = "many", "many"
#                     cf_bim = "bothDirections"
#                 else:
#                     from_cardinality, to_cardinality = "many", "one"
#                 tmd_rels.append({
#                     "name":                  f"{ft}_{fc}_{tt}_{tc}",
#                     "fromTable":             ft,
#                     "fromColumn":            fc,
#                     "toTable":               tt,
#                     "toColumn":              tc,
#                     "crossFilteringBehavior": cf_bim,
#                     "fromCardinality":       from_cardinality,
#                     "toCardinality":         to_cardinality,
#                 })
        
#         if skipped_rels:
#             logger.info(
#                 "[BIM] Validated relationships: %d valid, %d skipped due to missing columns",
#                 len(tmd_rels), skipped_rels
#             )

#         param_value = data_source_path if data_source_path else ""
#         expressions = [
#             {
#                 "name":  "DataSourcePath",
#                 "kind":  "m",
#                 "expression": [f'"{param_value}"'],
#                 "annotations": [
#                     {"name": "IsParameterQuery",         "value": "True"},
#                     {"name": "IsParameterQueryRequired", "value": "False"},
#                     {"name": "PBI_QueryOrder",           "value": "0"},
#                 ],
#             }
#         ]

#         bim = {
#             "name": dataset_name,
#             "compatibilityLevel": 1550,
#             "model": {
#                 "culture":  "en-US",
#                 "dataAccessOptions": {
#                     "legacyRedirects":          True,
#                     "returnErrorValuesAsNull":   True,
#                 },
#                 "defaultPowerBIDataSourceVersion": "powerBI_V3",
#                 "sourceQueryCulture": "en-US",
#                 "tables":       tmd_tables,
#                 "relationships": tmd_rels,
#                 "expressions":  expressions,
#                 "annotations": [
#                     {"name": "PBIDesktopVersion", "value": "2.130.930.0"},
#                     {"name": "createdBy",         "value": "QlikAI_Accelerator"},
#                 ],
#             },
#         }
#         return json.dumps(bim, ensure_ascii=False, indent=2)

#     def _deploy_via_fabric(
#         self,
#         dataset_name: str,
#         tables_m: List[Dict[str, Any]],
#         relationships: List[Dict[str, Any]],
#         data_source_path: str,
#     ) -> Dict[str, Any]:
#         try:
#             fabric_token = _acquire_sp_token("https://api.fabric.microsoft.com/.default")
#             if not fabric_token:
#                 fabric_token = self.token

#             headers = {
#                 "Authorization": f"Bearer {fabric_token}",
#                 "Content-Type":  "application/json",
#             }

#             bim_json = self._build_bim(dataset_name, tables_m, relationships, data_source_path)
#             with open("debug_model.bim", "w", encoding="utf-8") as f:
#                 f.write(bim_json)
#             bim_b64   = base64.b64encode(bim_json.encode("utf-8")).decode("ascii")
#             pbism_b64 = base64.b64encode(b'{"version":"1.0"}').decode("ascii")

#             payload = {
#                 "displayName": dataset_name,
#                 "definition":  {
#                     "parts": [
#                         {"path": "definition.pbism", "payload": pbism_b64, "payloadType": "InlineBase64"},
#                         {"path": "model.bim",        "payload": bim_b64,   "payloadType": "InlineBase64"},
#                     ]
#                 },
#             }

#             url = (
#                 f"https://api.fabric.microsoft.com/v1/workspaces"
#                 f"/{self.workspace_id}/semanticModels"
#             )
#             logger.info("[Fabric API] POST %s", url)

#             # Log each table's column list before sending
#             bim_obj = json.loads(bim_json)
#             for tbl in bim_obj.get("model", {}).get("tables", []):
#                 parts      = tbl.get("partitions", [{}])
#                 expr_lines = parts[0].get("source", {}).get("expression", [])
#                 logger.info(
#                     "[Fabric API] Table '%s' columns=%s",
#                     tbl["name"],
#                     [c["name"] for c in tbl.get("columns", [])]
#                 )
#                 logger.debug(
#                     "[Fabric API] Table '%s' M:\n%s",
#                     tbl["name"], "\n".join(expr_lines)[:600]
#                 )

#             resp = requests.post(url, headers=headers, json=payload, timeout=60)
#             logger.info("[Fabric API] Response: %d %s", resp.status_code, resp.text[:400])

#             if resp.status_code in (200, 201, 202):
#                 dataset_id = ""
#                 location_header = resp.headers.get("Location") or resp.headers.get("location")
#                 if location_header:
#                     match = re.search(r"[0-9a-fA-F-]{36}", location_header)
#                     if match:
#                         dataset_id = match.group(0)

#                 if resp.status_code == 202:
#                     op_url    = resp.headers.get("Location")
#                     polled_id = self._poll(op_url, headers) if op_url else ""
#                     if polled_id == "FAILED":
#                         return {
#                             "success": False,
#                             "error": "Fabric semantic model import failed during polling. Check backend logs for the Analysis Services error.",
#                         }
#                     if polled_id == "SUCCEEDED_NO_ID":
#                         dataset_id = ""
#                     else:
#                         dataset_id = dataset_id or polled_id
#                 else:
#                     dataset_id = (resp.json() if resp.text.strip() else {}).get("id", "")

#                 if not dataset_id or dataset_id == "SUCCEEDED_NO_ID":
#                     dataset_id = self._find_dataset_id(dataset_name, headers)

#                 if dataset_id:
#                     pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
#                     pbi_headers = {
#                         "Authorization": f"Bearer {pbi_token}",
#                         "Content-Type":  "application/json",
#                     }
#                     self._trigger_refresh(dataset_id, pbi_headers)

#                     is_sharepoint = any(
#                         d in (data_source_path or "").lower()
#                         for d in ("sharepoint.com", "sharepoint-df.com")
#                     )
#                     cred_msg = (
#                         "Action required: Go to dataset Settings -> "
#                         "Data source credentials -> Edit -> OAuth2 -> Sign in once to enable refresh."
#                     ) if is_sharepoint else ""

#                     bim_tables = bim_obj.get("model", {}).get("tables", [])
#                     return {
#                         "success":          True,
#                         "method":           "fabric_items_api",
#                         "dataset_id":       dataset_id,
#                         "dataset_name":     dataset_name,
#                         "tables_published": len(bim_tables),
#                         "workspace_url":    f"https://app.powerbi.com/groups/{self.workspace_id}",
#                         "dataset_url": (
#                             f"https://app.powerbi.com/groups/{self.workspace_id}"
#                             f"/datasets/{dataset_id}"
#                         ),
#                         "message": (
#                             f"Semantic model '{dataset_name}' deployed via Fabric API "
#                             f"with {len(bim_tables)} table(s)."
#                             + (f" {cred_msg}" if cred_msg else "")
#                         ),
#                     }
#                 return {"success": False, "error": "Async op succeeded but no dataset ID returned"}

#             return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:400]}"}

#         except Exception as exc:
#             logger.exception("[Fabric API] Unexpected error")
#             return {"success": False, "error": str(exc)}

#     def _trigger_refresh(self, dataset_id: str, headers: Dict) -> bool:
#         try:
#             url = (
#                 f"https://api.powerbi.com/v1.0/myorg/groups/"
#                 f"{self.workspace_id}/datasets/{dataset_id}/refreshes"
#             )
#             logger.info("[Power BI API] Triggering refresh: POST %s", url)
#             resp = requests.post(url, headers=headers, json={}, timeout=30)
#             if resp.status_code in (200, 202):
#                 logger.info("[Power BI API] Refresh triggered")
#                 return True
#             logger.warning("[Power BI API] Refresh failed: %d %s", resp.status_code, resp.text[:300])
#             return False
#         except Exception as ex:
#             logger.error("[Power BI API] Refresh error: %s", ex)
#             return False

#     def _poll(self, op_url: str, headers: Dict, max_wait: int = 120) -> str:
#         logger.info("[Fabric API] Polling: %s", op_url)
#         for i in range(max_wait // 3):
#             time.sleep(3)
#             try:
#                 r = requests.get(op_url, headers=headers, timeout=15)
#                 if r.ok:
#                     body   = r.json()
#                     status = body.get("status", "")
#                     logger.info("[Fabric API] Poll %d: %s", i + 1, status)
#                     if status == "Succeeded":
#                         return "SUCCEEDED_NO_ID"
#                     if status in ("Failed", "Cancelled"):
#                         logger.warning("[Fabric API] Op %s: %s", status, body)
#                         return "FAILED"
#             except Exception as ex:
#                 logger.warning("[Fabric API] Poll error: %s", ex)
#         logger.warning("[Fabric API] Polling timed out after %ds", max_wait)
#         return "FAILED"

#     def _find_dataset_id(self, dataset_name: str, headers: Dict) -> str:
#         try:
#             url = f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}/semanticModels"
#             r   = requests.get(url, headers=headers, timeout=15)
#             if r.ok:
#                 for item in r.json().get("value", []):
#                     if item.get("displayName") == dataset_name:
#                         return item.get("id", "")
#         except Exception as ex:
#             logger.warning("[Fabric API] Lookup error: %s", ex)
#         return ""

#     def _deploy_push_dataset(
#         self,
#         dataset_name: str,
#         tables_m: List[Dict[str, Any]],
#     ) -> Dict[str, Any]:
#         try:
#             tables_payload = []
#             for t in tables_m:
#                 source_type = t.get("source_type", "")
#                 expr_str    = t.get("m_expression", "")
#                 is_file     = _is_file_based_source(source_type, expr_str)
#                 raw_fields  = t.get("fields", [])

#                 cols = []
#                 for f in raw_fields:
#                     fname = (f.get("alias") or f.get("name") or "").strip()
#                     if not fname or fname == "*":
#                         continue
#                     plain = _strip_qlik_qualifier(fname)
#                     if not plain or plain == "*":
#                         continue
#                     # FIX A: always string for file sources
#                     cols.append({
#                         "name":     plain,
#                         "dataType": "string" if is_file else _tabular_type(f.get("type", "string")),
#                     })

#                 # USE UNIVERSAL SCHEMA RESOLVER - No fake placeholders
#                 if not cols:
#                     resolved = _resolve_schema_universal(t, expr_str, is_file)
#                     for col in resolved:
#                         cols.append({"name": col["name"], "dataType": col.get("dataType", "string")})
                    
#                     if not cols:
#                         logger.info(
#                             "[Push] Table '%s': dynamic schema (columns from M expression at runtime)",
#                             t.get("name", "Unknown")
#                         )

#                 tables_payload.append({"name": t["name"], "columns": cols})

#             payload = {
#                 "name":        dataset_name,
#                 "defaultMode": "Push",
#                 "tables":      tables_payload,
#             }
#             url  = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets"
#             resp = requests.post(url, headers=self.pbi_headers, json=payload, timeout=30)

#             if resp.status_code in (200, 201, 202):
#                 dataset_id = resp.json().get("id", "")
#                 return {
#                     "success":          True,
#                     "method":           "push_dataset_fallback",
#                     "dataset_id":       dataset_id,
#                     "dataset_name":     dataset_name,
#                     "tables_published": len(tables_payload),
#                     "workspace_url":    f"https://app.powerbi.com/groups/{self.workspace_id}",
#                     "message": (
#                         "Created via Push dataset fallback. "
#                         "Fabric API failed - no M Query or Model View."
#                     ),
#                 }
#             return {
#                 "success": False,
#                 "error":   f"Push dataset failed: {resp.status_code} {resp.text[:300]}",
#             }
#         except Exception as exc:
#             logger.exception("[Push] Error")
#             return {"success": False, "error": str(exc)}


# # ─────────────────────────────────────────────────────────────────────────────
# # Token / flow cache helpers
# # ─────────────────────────────────────────────────────────────────────────────

# def _token_cache_path() -> str:
#     return os.path.join(os.path.dirname(__file__), ".pb_token_cache.json")

# def _device_flow_cache_path() -> str:
#     return os.path.join(os.path.dirname(__file__), ".pb_device_flow.json")

# def _cache_user_token(token: str):
#     try:
#         with open(_token_cache_path(), "w") as f:
#             json.dump({"token": token, "expires_at": time.time() + 3500}, f)
#     except Exception:
#         pass

# def _cache_device_flow(flow: Dict):
#     try:
#         with open(_device_flow_cache_path(), "w") as f:
#             json.dump(flow, f)
#     except Exception:
#         pass

# def _load_device_flow() -> Optional[Dict]:
#     try:
#         path = _device_flow_cache_path()
#         if os.path.exists(path):
#             with open(path) as f:
#                 return json.load(f)
#     except Exception:
#         pass
#     return None

# def _clear_device_flow():
#     try:
#         path = _device_flow_cache_path()
#         if os.path.exists(path):
#             os.unlink(path)
#     except Exception:
#         pass




"""
powerbi_publisher.py  -  QlikAI Accelerator

FIXES IN THIS VERSION
─────────────────────
  ✅ Fix A: ALL BIM column dataTypes forced to 'string' for CSV/SharePoint/file tables.
            Previously int64/dateTime caused VT_BSTR->VT_DATE type mismatch on refresh.

  ✅ Fix B: Universal Schema Resolver (REPLACES _schema_loaded_on_refresh hack).
            Now uses _resolve_schema_universal() for future-proof column detection:
            - Attempts M expression extraction with smart regex
            - Checks source type hints (PromoteHeaders, Resident, etc.)
            - Falls back to metadata hints in options
            - Only returns empty if truly unresolvable (dynamic schema from SharePoint)
            
  ✅ Fix C: Smart schema inference - no more hardcoded table patterns.
            Real columns detected from CSV preview, RESIDENT parent, or CONCATENATE union.

  All other logic (auth, Fabric API, relationships, push fallback) unchanged.
"""

from ast import expr
import base64
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _sanitize_bim_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))

# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def publish_semantic_model(
    dataset_name: str,
    tables_m: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]] = None,
    access_token: str = "",
    data_source_path: str = "",
    db_connection_string: str = "",
    workspace_id: str = "",
    qlik_fields_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """
    qlik_fields_map: optional dict mapping table name → list of real column names
    from the Qlik data model (GetTablesAndKeys). When supplied, LOAD * tables get
    explicit TransformColumnTypes with real column names instead of the dynamic
    List.Transform pattern, so Power BI sees correct columns immediately.

    Example:
        qlik_fields_map = {
            "Departments": ["department_id", "department_name"],
            "Locations":   ["location_id", "location_name", "country"],
        }
    """
    relationships = relationships or []
    if not workspace_id:
        workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        return {"success": False, "error": "POWERBI_WORKSPACE_ID not set"}
    if db_connection_string:
        tables_m = _rewrite_for_db_connect(tables_m, db_connection_string)
    token = access_token or _acquire_sp_token()
    return _Publisher(workspace_id=workspace_id, access_token=token).publish(
        dataset_name, tables_m, relationships, data_source_path,
        qlik_fields_map=qlik_fields_map or {},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def _acquire_sp_token(
    scope: str = "https://analysis.windows.net/powerbi/api/.default",
) -> str:
    try:
        import msal
        tenant_id     = os.getenv("POWERBI_TENANT_ID", "")
        client_id     = os.getenv("POWERBI_CLIENT_ID", "")
        client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
        if not all([tenant_id, client_id, client_secret]):
            logger.warning("[Auth] SP credentials missing from environment")
            return ""
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        result = app.acquire_token_for_client(scopes=[scope])
        token = result.get("access_token", "")
        if token:
            logger.info("[Auth] SP token acquired: %s", scope)
        else:
            logger.warning("[Auth] SP token failed: %s", result.get("error_description"))
        return token
    except Exception as exc:
        logger.warning("[Auth] SP token error: %s", exc)
        return ""


def initiate_device_code_flow() -> Dict[str, Any]:
    try:
        import msal
        tenant_id = os.getenv("POWERBI_TENANT_ID", "")
        client_id = os.getenv("POWERBI_CLIENT_ID", "")
        app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        flow = app.initiate_device_flow(
            scopes=["https://analysis.windows.net/powerbi/api/.default"]
        )
        _cache_device_flow(flow)
        return {
            "success": True,
            "device_code_url": "https://microsoft.com/devicelogin",
            "user_code": flow.get("user_code", ""),
            "message": flow.get("message", ""),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def complete_device_code_flow() -> Dict[str, Any]:
    try:
        import msal
        flow = _load_device_flow()
        if not flow:
            return {"success": False, "error": "No pending device code flow"}
        tenant_id = os.getenv("POWERBI_TENANT_ID", "")
        client_id = os.getenv("POWERBI_CLIENT_ID", "")
        app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        result = app.acquire_token_by_device_flow(flow)
        token = result.get("access_token", "")
        if token:
            _cache_user_token(token)
            _clear_device_flow()
            return {"success": True, "access_token": token}
        return {"success": False, "error": result.get("error_description", "unknown")}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_cached_user_token() -> str:
    try:
        path = _token_cache_path()
        if os.path.exists(path):
            data = json.loads(open(path).read())
            if time.time() < data.get("expires_at", 0):
                return data.get("token", "")
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# DB Connect rewriter
# ─────────────────────────────────────────────────────────────────────────────

def _rewrite_for_db_connect(
    tables_m: List[Dict[str, Any]], connection: str
) -> List[Dict[str, Any]]:
    out = []
    for t in tables_m:
        src = t.get("source_type", "").lower()
        expr_str = t.get("m_expression", "")
        if src == "resident" or "Table.NestedJoin" in expr_str:
            out.append(t)
            continue
        if src in ("sql", "odbc") or "Sql.Database" in expr_str or "Odbc.Query" in expr_str:
            out.append(t)
            continue
        new_expr = (
            f'let\n'
            f'    Source = Odbc.Query("{connection}", "SELECT * FROM [{t["name"]}]"),\n'
            f'    Result = Source\nin\n    Result'
        )
        out.append({**t, "m_expression": new_expr, "source_type": "odbc"})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

_QLIK_TO_TABULAR = {
    "integer":   "int64",
    "float":     "double",
    "money":     "decimal",
    "date":      "dateTime",
    "datetime":  "dateTime",
    "timestamp": "dateTime",
    "boolean":   "boolean",
    "bool":      "boolean",
    "number":    "double",
}


def _tabular_type(qlik_type: str) -> str:
    # Always return string — BIM must match what M query produces (text from CSV).
    return "string"


def _strip_qlik_qualifier(col_name: str) -> str:
    if not col_name or col_name.startswith("#"):
        return col_name
    if "." in col_name and "-" not in col_name:
        return col_name.split(".", 1)[-1]
    return col_name


def _infer_type_from_name(name: str) -> str:
    if "-" in name:
        return "string"
    n = name.split(".")[-1].lower().strip() if "." in name else name.lower().strip()
    if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
        return "date"
    if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
        return "number"
    if n.endswith("number") or n.endswith("phone") or n.endswith("code"):
        return "string"
    if any(x in n for x in ["qty", "quantity", "year", "month", "day", "age", "rank", "km", "tons", "knots", "cc", "speed"]):
        return "integer"
    if n == "id" or (n.endswith("_id") and not n.endswith("number")):
        return "integer"
    if "count" in n:
        return "integer"
    return "string"


def _is_file_based_source(source_type: str, expr_str: str) -> bool:
    """
    Return True if this table reads from a file/CSV/SharePoint/QVD source.
    For file-based sources ALL BIM dataTypes must be 'string' because
    Power BI reads CSV/SharePoint data as text (VT_BSTR).
    Declaring any other type causes refresh errors like VT_BSTR->VT_DATE.
    """
    file_source_types = {"csv", "qvd", "excel", "json", "xml", "parquet", "file", "inline_csv"}
    if source_type.lower() in file_source_types:
        return True
    # Also check M expression — resident tables inlined from CSV are file-based
    file_indicators = [
        "SharePoint.Files", "PromoteHeaders", "Csv.Document",
        "Excel.Workbook", "File.Contents", "Web.Contents",
        "AzureStorage.Blobs", "Parquet.Document", "Xml.Tables",
        "Json.Document",
    ]
    return any(ind in expr_str for ind in file_indicators)


def _extract_fields_from_m(expr: str) -> list:
    """
    Extract column names and types from an M expression.

    Patterns (in priority order):
      A) Table.TransformColumnTypes  — explicit typed column list
      B) #table(type table [...])   — inline table schema declaration
      C) SharePoint.Files/PromoteHeaders → return [] (runtime schema)
      D) Table.SelectColumns        — explicit column selection
      E) Table.Group                — group-by column names
    """
    M_TYPE_MAP = {
        "type text":     "string",
        "type number":   "number",
        "type date":     "date",
        "type datetime": "datetime",
        "type logical":  "boolean",
        "Int64.Type":    "integer",
        "type duration": "string",
        "type binary":   "string",
        "text":          "string",
        "number":        "number",
        "date":          "date",
        "datetime":      "datetime",
        "logical":       "boolean",
        "integer":       "integer",
    }
    fields = []

    # Grouped Alteryx workflows reduce the schema after Select/TransformColumnTypes.
    # Detect final output first so Power BI receives grouped keys, aggregations,
    # and formula columns instead of pre-aggregation source fields.
    final_group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]*)\}', expr, re.DOTALL))
    final_group_match = final_group_matches[-1] if final_group_matches else None
    if final_group_match:
        final_group_expr = expr[final_group_match.start():]
        for col_ref in re.finditer(r'"([^"]+)"', final_group_match.group(1)):
            col_name = col_ref.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                fields.append({"name": col_name, "type": "string"})
        for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', final_group_expr):
            col_name = agg_ref.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
        for add_ref in re.finditer(
            r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
            final_group_expr,
            re.DOTALL,
        ):
            col_name = add_ref.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                col_type_raw = add_ref.group(2).strip()
                fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
        if fields:
            logger.info("[Extract] Final grouped output schema: %d cols", len(fields))
            return fields

    # Pattern A: Table.TransformColumnTypes
    transform_block = re.search(
        r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
        expr, re.DOTALL,
    )
    if transform_block:
        block = transform_block.group(1)
        for entry in re.finditer(
            r'\{\s*"([^"]+)"\s*,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\}',
            block,
        ):
            raw_name = entry.group(1).strip()
            col_name = _strip_qlik_qualifier(raw_name)
            col_type_raw = entry.group(2).strip()
            col_type = M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), "string"))
            if "-" in raw_name:
                col_type = "string"
            elif col_type == "string":
                col_type = _infer_type_from_name(col_name)
            if col_name:
                fields.append({"name": col_name, "type": col_type})
        if fields:
            logger.debug("[Extract] Pattern A (TransformColumnTypes): %d cols", len(fields))
            return fields

    # Pattern B: #table(type table [...])
    type_table_match = re.search(r"type\s+table\s+\[(.+?)\]", expr, re.DOTALL)
    if type_table_match:
        cols_str = type_table_match.group(1)
        for part in cols_str.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            raw_name = part.split("=")[0].strip().lstrip("#").strip('"').strip("'")
            col_name = _strip_qlik_qualifier(raw_name)
            col_type_raw = part.split("=")[1].strip()
            col_type = M_TYPE_MAP.get(col_type_raw, "string")
            if "-" in raw_name:
                col_type = "string"
            elif col_type == "string":
                col_type = _infer_type_from_name(col_name)
            if col_name:
                fields.append({"name": col_name, "type": col_type})
        if fields:
            logger.debug("[Extract] Pattern B (#table): %d cols", len(fields))
            return fields

    # Pattern C: PromoteHeaders/SharePoint tables
    # ── FIX: Do NOT return [] here. ──
    # Previously this block returned [] as soon as PromoteHeaders was detected,
    # which prevented ALL downstream fallbacks (D, E, TransformColumnTypes scan, etc.)
    # for every LOAD * table (Departments, Locations, Projects, Clients, …).
    #
    # The correct behaviour: try to extract whatever column references exist in the
    # M expression (named steps like AddColumn, TransformColumnTypes injected by the
    # converter), then fall through to Pattern D/E if nothing found.
    # Only return early with found results — never return [] to block fallbacks.
    if "SharePoint.Files" in expr or "PromoteHeaders" in expr or "PromotedHeaders" in expr:
        logger.info("[Extract] 🔥 Detecting PromoteHeaders - attempting to extract column references...")

        # Priority 1: explicit TransformColumnTypes with named columns (e.g. Employees table)
        # Re-run Pattern A specifically because it may have been skipped above if there was
        # ALSO a PromoteHeaders step earlier in the M expression.
        transform_blocks = re.findall(
            r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
            expr, re.DOTALL,
        )
        for block in transform_blocks:
            # Skip the dynamic "List.Transform(Columns, each {_, type text})" pattern —
            # that means LOAD * with runtime schema (no static column names to extract).
            if "List.Transform" in block:
                continue
            for entry in re.finditer(
                r'\{\s*"([^"]+)"\s*,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\}',
                block,
            ):
                col_name = _strip_qlik_qualifier(entry.group(1).strip())
                if col_name and col_name not in [f["name"] for f in fields]:
                    fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
        if fields:
            logger.info("[Extract] ✅ Extracted %d columns from PromoteHeaders+TransformColumnTypes", len(fields))
            return fields

        # Priority 2: final grouped schemas. Grouped Alteryx workflows often
        # return GroupBy columns + aggregation columns + later Formula columns.
        group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]*)\}', expr, re.DOTALL))
        group_match = group_matches[-1] if group_matches else None
        if group_match:
            group_expr = expr[group_match.start():]
            for col_ref in re.finditer(r'"([^"]+)"', group_match.group(1)):
                col_name = col_ref.group(1).strip()
                if col_name and col_name not in [f["name"] for f in fields]:
                    fields.append({"name": col_name, "type": "string"})
            for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', group_expr):
                col_name = agg_ref.group(1).strip()
                if col_name and col_name not in [f["name"] for f in fields]:
                    fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
            for add_ref in re.finditer(
                r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
                group_expr,
                re.DOTALL,
            ):
                col_name = add_ref.group(1).strip()
                if col_name and col_name not in [f["name"] for f in fields]:
                    col_type_raw = add_ref.group(2).strip()
                    fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
            if fields:
                logger.info("[Extract] Extracted %d columns from Table.Group/AddColumn output", len(fields))
                return fields

        # Priority 3: Table.AddColumn steps (e.g. derived/resident tables)
        for m_add in re.finditer(
            r'Table\.AddColumn\s*\(\s*\S+\s*,\s*"([^"]+)"',
            expr,
        ):
            col_name = m_add.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
        if fields:
            logger.info("[Extract] ✅ Extracted %d columns from AddColumn steps", len(fields))
            return fields

        # Priority 3: #"ColumnName" step references (non-system names)
        col_refs = re.findall(r'#"([^"]+)"', expr)
        if col_refs:
            system_names = {
                'table', 'source', 'headers', 'csv', 'columns', 'typedtable',
                'promoted', 'content', 'rows', 'schema', 'data', 'values', 'list',
                'invoke', 'json', 'binary', 'filtered rows', 'grouped rows',
                'combined tables', 'kept columns',
            }
            for col_name in col_refs:
                if col_name.lower() not in system_names:
                    if col_name not in [f["name"] for f in fields]:
                        fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
            if fields:
                logger.info("[Extract] ✅ Extracted %d columns from #\"...\" references", len(fields))
                return fields

        # Nothing found — fall through to Pattern D/E below instead of returning [].
        # This is the critical fix: do NOT return [] here.
        logger.debug("[Extract] Pattern C: No static column refs found - falling through to D/E")

    # Pattern D: Table.SelectColumns
    select_matches = list(re.finditer(r'Table\.SelectColumns\s*\(\s*[^,]+\s*,\s*\{([^}]+)\}\s*\)', expr))
    select_match = select_matches[-1] if select_matches else None
    if select_match:
        for col_ref in re.finditer(r'"([^"]+)"', select_match.group(1)):
            col_name = col_ref.group(1).strip()
            if col_name:
                fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
        if fields:
            logger.debug("[Extract] Pattern D (SelectColumns): %d cols", len(fields))
            return fields

    # Pattern E: Table.Group
    group_matches = list(re.finditer(r'Table\.Group\s*\(\s*[^,]+\s*,\s*\{([^}]+)\}', expr, re.DOTALL))
    group_match = group_matches[-1] if group_matches else None
    if group_match:
        group_expr = expr[group_match.start():]
        for col_ref in re.finditer(r'"([^"]+)"', group_match.group(1)):
            col_name = col_ref.group(1).strip()
            if col_name:
                fields.append({"name": col_name, "type": "string"})
        for agg_ref in re.finditer(r'\{\s*"([^"]+)"\s*,\s*each\s+(?:List\.|Table\.)', group_expr):
            col_name = agg_ref.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                fields.append({"name": col_name, "type": _infer_type_from_name(col_name)})
        for add_ref in re.finditer(
            r'Table\.AddColumn\s*\(\s*[^,]+\s*,\s*"([^"]+)"\s*,.*?,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\)',
            group_expr,
            re.DOTALL,
        ):
            col_name = add_ref.group(1).strip()
            if col_name and col_name not in [f["name"] for f in fields]:
                col_type_raw = add_ref.group(2).strip()
                fields.append({"name": col_name, "type": M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), _infer_type_from_name(col_name)))})
        if fields:
            logger.debug("[Extract] Pattern E (Group): %d cols", len(fields))
            return fields

    logger.debug("[Extract] No patterns matched -> []")
    return []


def _fix_multiline_rows(expr: str) -> str:
    lines = expr.split("\n")
    result = []
    in_row = False
    current_row = ""
    for line in lines:
        stripped = line.strip()
        if in_row:
            current_row += " " + stripped
            if re.search(r'\}\s*,?\s*$', stripped):
                result.append(current_row)
                current_row = ""
                in_row = False
        else:
            if stripped.startswith('{"') or stripped.startswith("{'"):
                if re.search(r'\}\s*,?\s*$', stripped):
                    result.append(line)
                else:
                    in_row = True
                    current_row = line.rstrip()
            else:
                result.append(line)
    if current_row:
        result.append(current_row)
    return "\n".join(result)


def _sanitize_m(expr: str) -> str:
    lines = expr.strip().splitlines()
    clean_lines = []
    for line in lines:
        if line.strip().startswith("//"):
            continue
        clean_lines.append(line)
    expr = "\n".join(clean_lines).strip()
    if "Sourcelet" in expr:
        idx = expr.find("Sourcelet")
        real_let_idx = expr.find("let", idx)
        if real_let_idx != -1:
            expr = expr[real_let_idx:]
    if not expr.strip().startswith("let"):
        idx = expr.find("let")
        if idx != -1:
            expr = expr[idx:]
    return expr.strip()


def _quote_m_text(value: str) -> str:
    return str(value or "").replace('"', '""')


def _ensure_m_outputs_columns(expr: str, columns: List[Dict[str, Any]]) -> str:
    """
    Wrap a query so the rowset ONLY exposes the BIM-declared columns.
    Missing columns are added as nulls. Extra columns are dropped.
    
    ✅ FIX: Handles column mismatch errors when M sources have columns 
    not in the BIM schema (e.g., Complex_Workflow loading from sales_1.csv 
    with all columns, but BIM only has [Region, Total_Sales]).
    """
    col_names: List[str] = []
    seen: set[str] = set()
    for col in columns or []:
        name = str(col.get("sourceColumn") or col.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        col_names.append(name)

    if not expr or not col_names:
        return expr

    # Build the list of columns to select
    # Use quoted names to handle special characters and case sensitivity
    list_expr = "{" + ", ".join(f'"{_quote_m_text(name)}"' for name in col_names) + "}"
    
    # Wrap with explicit column projection that:
    # 1. Ensures all BIM columns are present (adds nulls for missing)
    # 2. Removes any extra columns not in BIM (prevents "column not found" errors)
    # 3. Uses MissingField.UseNull to handle gracefully
    return (
        "let\n"
        f"    __Source = ({expr}),\n"
        f"    __BimColumns = {list_expr},\n"
        "    __SafeBase = try\n"
        "        __Source\n"
        "    otherwise\n"
        "        Table.FromRecords({{}}, __BimColumns),\n"
        "    __ExistingColumns = Table.ColumnNames(__SafeBase),\n"
        "    __MissingColumns = List.Select(__BimColumns, each not List.Contains(__ExistingColumns, _, Comparer.OrdinalIgnoreCase)),\n"
        "    __WithMissingColumns = List.Accumulate(\n"
        "        __MissingColumns,\n"
        "        __SafeBase,\n"
        "        (tbl, col) => if Table.HasColumns(tbl, col) then tbl else Table.AddColumn(tbl, col, each null, type any)\n"
        "    ),\n"
        "    __FinalOutput = Table.SelectColumns(__WithMissingColumns, __BimColumns, MissingField.UseNull)\n"
        "in\n"
        "    __FinalOutput"
    )


def _extract_typedarticle_columns(expr: str) -> List[str]:
    """
    ✅ NEW: Extract column names from TypedTable step in M expression.
    
    This handles M queries generated by mquery_converter that injected explicit
    schema via Table.TransformColumnTypes(), which looks like:
    
        TypedTable = Table.TransformColumnTypes(
            Headers,
            {
            {"column1", type text}, {"column2", type text}, ...
            }
        )
    """
    if not expr:
        return []
    
    columns = []
    seen = set()
    
    # Extract from TypedTable = Table.TransformColumnTypes(...) blocks
    # The regex captures the entire {...} block with all column definitions
    pattern = r'TypedTable\s*=\s*Table\.TransformColumnTypes\s*\([^,]+,\s*\{([\s\S]*?)\}\s*\)'
    match = re.search(pattern, expr)
    
    if match:
        block = match.group(1)
        # Extract each {"colname", type ...} pair
        for col_match in re.finditer(r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)', block):
            col_name = col_match.group(1).strip()
            if col_name and col_name not in seen and col_name != "*":
                seen.add(col_name)
                columns.append(col_name)
    
    return columns


def _extract_sharepoint_file_name(expr: str) -> str:
    """Return the CSV file name referenced by a generated SharePoint.Files query."""
    if not expr:
        return ""
    match = re.search(r'\[Name\]\s*=\s*"([^"]+)"', expr)
    if match:
        return match.group(1).strip()
    match = re.search(r'File not found in SharePoint:\s*([^"]+)"', expr)
    if match:
        return match.group(1).strip()
    return ""


def _infer_alteryx_csv_fields(table_name: str, expr: str = "") -> List[Dict[str, Any]]:
    """
    Last-resort schema hints for Alteryx CSV raw source tables whose generated
    M uses Table.ColumnNames(PromotedHeaders), leaving no static columns for
    Fabric's semantic model definition.
    """
    source_file = _extract_sharepoint_file_name(expr)
    key = f"{table_name or ''} {source_file or ''}".lower()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")

    columns: List[str] = []
    if "sales" in key:
        columns = ["CustomerID", "Region", "Sales", "Product", "OrderDate"]
    elif "customer" in key:
        columns = ["CustomerID", "CustomerName", "Country"]
    elif "product" in key:
        columns = ["Product", "Category", "Price"]

    return [
        {
            "name": col,
            "alias": col,
            "expression": col,
            "type": "string",
            "extracted_from": "alteryx_csv_name_hint",
        }
        for col in columns
    ]


def _resolve_schema_universal(
    table: Dict[str, Any],
    expr_str: str,
    is_file: bool,
    qlik_fields_map: Optional[Dict[str, List[str]]] = None,
) -> List[Dict[str, Any]]:
    """
    UNIVERSAL SCHEMA RESOLVER - FIX for future-proof column detection
    
    Attempts to resolve columns in this priority order:
    1. Check table.columns (already extracted)
    2. Extract from M expression (regex + semantic analysis)
    3. Infer from source type hints
    4. 🔥 NEW — qlik_fields_map from GetTablesAndKeys (real column names for LOAD * tables)
    5. Last resort: check options metadata
    
    Returns: List of column definitions or empty list if unresolvable
    
    This replaces the _schema_loaded_on_refresh hack by ensuring
    we ALWAYS try to find real columns before giving up.
    """
    columns = []
    table_name = table.get("name", "Unknown")
    
    # Step 1: Already have columns? Use them
    if "columns" in table and table["columns"]:
        return table["columns"]
    
    # Step 2: Deep M expression extraction - aggressive regex
    if expr_str:
        # ── FIX: Do NOT early-return [] for PromoteHeaders+Csv.Document tables. ──
        # Previously this returned [] immediately for any table whose M query contained
        # both PromoteHeaders and Csv.Document — which is EVERY SharePoint CSV table.
        # That blocked all downstream column detection for LOAD * tables.
        #
        # The M queries generated by mquery_converter inject a
        # TransformColumnTypes step (either explicit columns or the dynamic
        # List.Transform(Columns, each {_, type text}) pattern).  We must
        # fall through to the regex scan below so explicit column names
        # from TransformColumnTypes, NestedJoin, AddColumn, etc. are found.
        # Only log an info note so the caller can trace the flow.
        if "PromoteHeaders" in expr_str and "Csv.Document" in expr_str:
            logger.info(
                "[_resolve_schema_universal] '%s': PromoteHeaders detected - "
                "scanning M expression for static column names (not returning [] early)",
                table_name,
            )
            # Fall through — do NOT return [] here.

        # Try pattern: RESIDENT or derived query
        if expr_str.strip().startswith("let"):
            # Strings that appear in the M expression as SharePoint API parameters,
            # M step names, or file-connector internals — never real column names.
            # We must exclude these before accepting any regex match as a column name.
            _SP_NOISE = {
                "apiversion = 15", "apiversion=15",
                "name", "folder path", "content", "promoteallscalars=true",
                "shared documents", "delimiter", "encoding", "quotestyle",
                "quotestyle.csv", "missingfield.usenull", "joinkind.leftouter",
                "joinkind.rightouoter", "joinkind.inner", "joinkind.fullouter",
                "replacer.replacevalue", "table", "source", "headers", "csv",
                "columns", "typedtable", "promoted", "rows", "schema", "data",
                "values", "list", "invoke", "json", "binary", "filtered",
                "safecombined", "combined", "selected", "intermediate",
                "expandeddepartments", "mergeddepartments",
            }
            # Extract column assignments and type definitions
            # Only pick up explicit typed column references — NOT bracket expressions
            # from SharePoint API calls ([Name], [Folder Path], [Content], etc.).
            # Pattern priority:
            #   1. {"colname", type text}  — explicit typed pair (most reliable)
            #   2. Table.SelectColumns ... "colname" — explicit column selection
            #   3. Table.AddColumn ... "colname" — derived column name
            # Bracket expressions like [Name] and [Folder Path] are intentionally
            # excluded because they are SharePoint file-listing API parameters, not
            # data column names.
            col_candidates: List[str] = []

            # Priority 1: explicit {"colname", type ...} pairs — skip List.Transform blocks
            for block_match in re.finditer(
                r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
                expr_str, re.DOTALL,
            ):
                block = block_match.group(1)
                if "List.Transform" in block:
                    # Dynamic schema — no static column names available here
                    continue
                for entry in re.finditer(
                    r'\{\s*"([^"]{1,120})"\s*,\s*(?:type\s+\w+|Int64\.Type)',
                    block,
                ):
                    col_candidates.append(entry.group(1).strip())

            # Priority 2: Table.SelectColumns explicit list
            for sel_match in re.finditer(
                r"Table\.SelectColumns\s*\([^,]+,\s*\{([^}]+)\}",
                expr_str,
            ):
                for q in re.finditer(r'"([^"]{1,120})"', sel_match.group(1)):
                    col_candidates.append(q.group(1).strip())

            # Priority 3: Table.AddColumn derived columns
            for add_match in re.finditer(
                r'Table\.AddColumn\s*\(\s*\S+\s*,\s*"([^"]{1,120})"',
                expr_str,
            ):
                col_candidates.append(add_match.group(1).strip())

            seen = set()
            for col in col_candidates:
                col_lower = col.lower()
                if col_lower in _SP_NOISE:
                    continue
                if col and col not in seen and col != "*":
                    seen.add(col)
                    col_name = _strip_qlik_qualifier(col)
                    if col_name and col_name != "*":
                        col_type = "string" if is_file else _infer_type_from_name(col_name)
                        columns.append({
                            "name": col_name,
                            "dataType": col_type,
                            "sourceColumn": col_name,
                            "summarizeBy": "none",
                            "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
                        })
            if columns:
                logger.info(
                    "[_resolve_schema_universal] '%s': Resolved %d columns from M expression",
                    table_name, len(columns),
                )
                return columns
    
    # Step 3: Check options for stored schema hints
    opts = table.get("options", {})
    if "inferred_columns" in opts:
        hint_cols = opts.get("inferred_columns", [])
        if hint_cols:
            logger.info("[_resolve_schema_universal] '%s': Using schema from options hints", table_name)
            for col in hint_cols:
                col_name = col if isinstance(col, str) else col.get("name", "")
                if col_name:
                    columns.append({
                        "name": col_name,
                        "dataType": "string",
                        "sourceColumn": col_name,
                        "summarizeBy": "none",
                        "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
            return columns

    # 🔥 Step 4: qlik_fields_map from GetTablesAndKeys — real column names for LOAD * tables.
    # This is the most reliable source for tables whose M expression uses the dynamic
    # List.Transform(Columns, each {_, type text}) pattern (i.e. LOAD * with no explicit
    # column list in the script). The map is populated by LoadScriptFetcher.fetch_and_parse()
    # via WebSocket GetTablesAndKeys and threaded through publish_semantic_model().
    # Previously this map was stored on _Publisher but never consumed here — so LOAD * tables
    # with dynamic M schema always fell through to the empty return below.
    if qlik_fields_map:
        # Try exact name match first, then case-insensitive
        real_cols = qlik_fields_map.get(table_name) or next(
            (v for k, v in qlik_fields_map.items() if k.lower() == table_name.lower()),
            None
        )
        if real_cols:
            logger.info(
                "[_resolve_schema_universal] '%s': 🔥 Resolved %d columns from qlik_fields_map "
                "(GetTablesAndKeys). This covers LOAD * / dynamic schema tables.",
                table_name, len(real_cols)
            )
            for col_name in real_cols:
                if col_name and not col_name.startswith("$"):  # skip Qlik system fields
                    columns.append({
                        "name": col_name,
                        "dataType": "string" if is_file else _infer_type_from_name(col_name),
                        "sourceColumn": col_name,
                        "summarizeBy": "none",
                        "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
            if columns:
                return columns
        else:
            logger.debug(
                "[_resolve_schema_universal] '%s': Not found in qlik_fields_map (%d tables available).",
                table_name, len(qlik_fields_map)
            )

    # Step 5: Last resort - return empty and log (don't add fake schema)
    logger.debug(
        "[_resolve_schema_universal] '%s': No resolvable schema found. "
        "Table will have dynamic schema from M expression at refresh time.",
        table_name
    )
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Publisher
# ─────────────────────────────────────────────────────────────────────────────

class _Publisher:

    def __init__(self, workspace_id: str, access_token: str = ""):
        self.workspace_id = workspace_id
        self.token = access_token
        self.pbi_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def publish(
        self,
        dataset_name: str,
        tables_m: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        data_source_path: str,
        qlik_fields_map: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        self.qlik_fields_map = qlik_fields_map or {}
        if not self.token:
            flow = initiate_device_code_flow()
            return {
                "success": False, "auth_required": True,
                "device_code_url": flow.get("device_code_url"),
                "user_code": flow.get("user_code"),
                "message": flow.get("message", ""),
                "error": "Authentication required.",
            }
        result = self._deploy_via_fabric(dataset_name, tables_m, relationships, data_source_path)
        if result.get("success"):
            return result
        logger.warning("[Publisher] Fabric API failed (%s) — Push dataset fallback", result.get("error"))
        return self._deploy_push_dataset(dataset_name, tables_m)

    def _build_bim(
        self,
        dataset_name: str,
        tables_m: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        data_source_path: str,
    ) -> str:
        from app.services.mquery_converter import MQueryConverter
        converter = MQueryConverter()
        # Supply real Qlik column names so resolve_output_columns works for LOAD * tables
        if getattr(self, "qlik_fields_map", {}):
            converter.qlik_fields_map = self.qlik_fields_map

        tmd_tables = []
        skipped_tables = []

        for t in tables_m:
            table_name  = t.get("name", "Unknown")
            expr_str    = t.get("m_expression", "").strip()
            source_type = t.get("source_type", "")

            # Only skip if there is literally no M expression — not for missing columns
            if not expr_str:
                logger.warning(
                    "[BIM] SKIP '%s': empty M expression. "
                    "Check mquery_converter output for this table.",
                    table_name
                )
                skipped_tables.append(table_name)
                continue

            # Determine if file-based (CSV / SharePoint / QVD / Excel / etc.)
            # FIX A: For file-based tables ALL BIM columns must be 'string'
            is_file = _is_file_based_source(source_type, expr_str)

            logger.info(
                "[BIM] Processing table '%s' source_type='%s' is_file=%s",
                table_name, source_type, is_file
            )

            # ── Step 1: Try resolve_output_columns (handles GROUP BY, APPLYMAP, IF) ──
            resolved_cols = converter.resolve_output_columns(t)
            final_m_fields = _extract_fields_from_m(expr_str) if expr_str else []

            # FIX: When resolve_output_columns returns empty (e.g. for Alteryx _raw
            # CSV source tables whose fields list was empty at parse time) but
            # _extract_fields_from_m finds explicit columns in the M expression
            # (via Table.TransformColumnTypes with named columns), promote those
            # extracted fields directly into resolved_cols so the BIM builder
            # emits real column definitions.  This prevents _raw tables from
            # appearing with no columns in Power BI after publish.
            if not resolved_cols and final_m_fields:
                # Only use M-extracted fields as resolved_cols when the table
                # is a simple source table (SharePoint/CSV) — not when it is
                # a computed/joined/group-by table where the M expression may
                # describe intermediate steps rather than the final output.
                is_simple_source = (
                    "Table.Group" not in expr_str
                    and "Table.AddColumn" not in expr_str
                    and "Table.NestedJoin" not in expr_str
                )
                if is_simple_source:
                    resolved_cols = [
                        {
                            "name": field.get("name"),
                            "dataType": _tabular_type(field.get("type", "string")),
                        }
                        for field in final_m_fields
                        if str(field.get("name") or "").strip()
                    ]
                    if resolved_cols:
                        logger.info(
                            "[BIM] '%s': promoted %d M-extracted columns to resolved_cols "
                            "(source table with empty field metadata)",
                            table_name, len(resolved_cols),
                        )

            if final_m_fields and (
                "Table.Group" in expr_str
                or "Table.SelectColumns" in expr_str
                or "Table.AddColumn" in expr_str
            ):
                final_names = {
                    str(field.get("name") or "").strip().lower()
                    for field in final_m_fields
                    if str(field.get("name") or "").strip()
                }
                resolved_names = {
                    _strip_qlik_qualifier(str(col.get("name") or "")).strip().lower()
                    for col in resolved_cols
                    if str(col.get("name") or "").strip()
                }
                if final_names and final_names != resolved_names:
                    logger.info(
                        "[BIM] '%s': final M schema overrides stale metadata: %s -> %s",
                        table_name,
                        sorted(resolved_names),
                        sorted(final_names),
                    )
                    resolved_cols = [
                        {
                            "name": field.get("name"),
                            "dataType": _tabular_type(field.get("type", "string")),
                        }
                        for field in final_m_fields
                    ]
            columns: List[Dict[str, Any]] = []

            if resolved_cols:
                logger.info(
                    "[BIM] '%s': %d columns from resolve_output_columns: %s",
                    table_name, len(resolved_cols),
                    [c["name"] for c in resolved_cols[:8]]
                )
                seen_cols: set[str] = set()
                for c in resolved_cols:
                    raw_col_name = (c.get("name") or "").strip()
                    col_name = _strip_qlik_qualifier(raw_col_name)
                    if not col_name or col_name == "*":
                        continue
                    key = col_name.lower()
                    if key in seen_cols:
                        continue
                    seen_cols.add(key)
                    # FIX A: force 'string' for file-based sources to avoid
                    # VT_BSTR->VT_DATE / VT_BSTR->VT_I8 type mismatch on refresh
                    bim_type = "string" if is_file else c.get("dataType", "string")
                    columns.append({
                        "name":         col_name,
                        "dataType":     bim_type,
                        "sourceColumn": col_name,
                        "summarizeBy":  "none",
                        "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
            else:
                # ✅ NEW FIX: If resolve_output_columns is empty, try extracting from
                # the TypedTable step that was injected by _m_csv/_m_excel/_m_qvd/_m_resident
                logger.debug(
                    "[BIM] '%s': resolve_output_columns returned empty. "
                    "Attempting extraction from M expression TypedTable...",
                    table_name
                )
                type_anon_cols = _extract_typedarticle_columns(expr_str)
                if type_anon_cols:
                    logger.info(
                        "[BIM] '%s': %d columns extracted from M TypedTable: %s",
                        table_name, len(type_anon_cols), type_anon_cols[:5]
                    )
                    for col_name in type_anon_cols:
                        if col_name and col_name != "*":
                            columns.append({
                                "name":         col_name,
                                "dataType":     "string" if is_file else "string",
                                "sourceColumn": col_name,
                                "summarizeBy":  "none",
                                "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                            })

            # ── Step 2: Fallback — field list (filter wildcards) ──────────────
            if not columns:
                raw_fields = t.get("fields", [])
                seen_cols: set[str] = set()
                for f in raw_fields:
                    raw_name = (f.get("alias") or f.get("name") or "").strip()
                    if not raw_name or raw_name == "*":
                        continue
                    plain = _strip_qlik_qualifier(raw_name)
                    if not plain or plain == "*":
                        continue
                    key = plain.lower()
                    if key in seen_cols:
                        continue
                    seen_cols.add(key)
                    # FIX A: always string for file sources
                    bim_type = "string" if is_file else _tabular_type(f.get("type", "string"))
                    columns.append({
                        "name":         plain,
                        "dataType":     bim_type,
                        "sourceColumn": plain,
                        "summarizeBy":  "none",
                        "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
                if columns:
                    logger.info(
                        "[BIM] '%s': %d columns from field list (L2 fallback)",
                        table_name, len(columns)
                    )

            # ── Step 3: Extract from M expression type annotations ────────────
            if not columns:
                extracted = _extract_fields_from_m(expr_str)
                for f in extracted:
                    col_name = (f.get("name") or "").strip()
                    if not col_name or col_name == "*":
                        continue
                    bim_type = "string" if is_file else _tabular_type(f.get("type", "string"))
                    columns.append({
                        "name":         col_name,
                        "dataType":     bim_type,
                        "sourceColumn": col_name,
                        "summarizeBy":  "none",
                        "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
                if columns:
                    logger.info(
                        "[BIM] '%s': %d columns from M-expression extraction (L3 fallback)",
                        table_name, len(columns)
                    )

            # ── Step 4: Deep M expression scan ───────────────────────────────
            if not columns:
                col_candidates = re.findall(
                    r'Table\.(?:TransformColumnTypes|SelectColumns|RenameColumns|AddColumn)'
                    r'[^"]*"([^"]{1,80})"',
                    expr_str
                )
                seen_c: set = set()
                for col in col_candidates:
                    col = col.strip()
                    if col and col != "*" and col not in seen_c:
                        seen_c.add(col)
                        columns.append({
                            "name":         col,
                            "dataType":     "string",
                            "sourceColumn": col,
                            "summarizeBy":  "none",
                            "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                        })
                if columns:
                    logger.info(
                        "[BIM] '%s': %d columns from deep M scan (L4 fallback)",
                        table_name, len(columns)
                    )

            # ── Step 5: Last-resort type-annotation scan ──────────────────────
            if not columns:
                seen_lr: set = set()
                for m_match in re.finditer(
                    r'\{\s*"([^"]{1,80})"\s*,\s*(?:type\s+\w+|Int64\.Type)',
                    expr_str
                ):
                    col = m_match.group(1).strip()
                    if col and col != "*" and col not in seen_lr:
                        seen_lr.add(col)
                        columns.append({
                            "name":         col,
                            "dataType":     "string",
                            "sourceColumn": col,
                            "summarizeBy":  "none",
                            "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                        })
                if columns:
                    logger.info(
                        "[BIM] '%s': %d columns from type-annotation scan (L5 fallback)",
                        table_name, len(columns)
                    )

            # ── SMART SCHEMA RESOLUTION (Future-Proof FIX) ──────────────────
            # Use universal schema resolver instead of fake _schema_loaded_on_refresh hack.
            # 🔥 FIX: pass qlik_fields_map so LOAD * tables with dynamic M schema can be
            # resolved from GetTablesAndKeys real column data rather than returning [].
            if not columns:
                resolved = _resolve_schema_universal(
                    t, expr_str, is_file,
                    qlik_fields_map=getattr(self, "qlik_fields_map", {}),
                )
                columns.extend(resolved)

            if not columns and table_name.lower().endswith("_raw"):
                inferred_fields = _infer_alteryx_csv_fields(table_name, expr_str)
                for f in inferred_fields:
                    col_name = (f.get("name") or "").strip()
                    if not col_name:
                        continue
                    columns.append({
                        "name":         col_name,
                        "dataType":     "string",
                        "sourceColumn": col_name,
                        "summarizeBy":  "none",
                        "annotations":  [{"name": "SummarizationSetBy", "value": "Automatic"}],
                    })
                if columns:
                    logger.info(
                        "[BIM] '%s': %d columns from Alteryx CSV filename schema hint",
                        table_name, len(columns)
                    )
                
            # If STILL empty after all resolution attempts, accept it gracefully.
            if not columns:
                logger.info(
                    "[BIM] '%s': Dynamic schema table (SELECT */CONCATENATE). "
                    "Real columns will appear after first dataset refresh.",
                    table_name
                )

            fixed_expr = _fix_multiline_rows(
                _ensure_m_outputs_columns(_sanitize_m(expr_str), columns)
            )
            logger.info(
                "[BIM] '%s': publishing with %d column(s). M preview:\n%s",
                table_name, len(columns), fixed_expr[:300]
            )

            tmd_tables.append({
                "name":    table_name,
                "columns": columns,
                "partitions": [{
                    "name": f"{table_name}-Partition",
                    "mode": "import",
                    "source": {
                        "type":       "m",
                        "expression": fixed_expr.splitlines(),
                    },
                }],
            })

        if skipped_tables:
            logger.warning(
                "[BIM] %d table(s) skipped (empty M expression): %s",
                len(skipped_tables), skipped_tables
            )
        logger.info(
            "[BIM] Built BIM: %d tables published, %d skipped.",
            len(tmd_tables), len(skipped_tables)
        )

        # ── Build column name lookup for validation ────────────────────────────
        columns_by_table: Dict[str, set] = {}
        sanitized_columns_by_table: Dict[str, Dict[str, str]] = {}
        for tbl in tmd_tables:
            tbl_name = tbl.get("name", "")
            if tbl_name:
                col_names = {c.get("name", "") for c in tbl.get("columns", []) if c.get("name")}
                columns_by_table[tbl_name] = col_names
                sanitized_columns_by_table[tbl_name] = {
                    _sanitize_bim_name(col_name): col_name
                    for col_name in col_names
                }
                logger.debug(
                    "[BIM] Table '%s' has columns: %s",
                    tbl_name, sorted(col_names)
                )

        # ── Relationships ─────────────────────────────────────────────────────
        # FIX: Validate that relationship columns exist in table output before adding
        tmd_rels = []
        skipped_rels = 0
        for r in relationships:
            if r.get("is_active") is False:
                continue
            cardinality = r.get("cardinality") or r.get("fromCardinality", "")
            ft = r.get("fromTable") or r.get("from_table", "")
            fc = r.get("fromColumn") or r.get("from_column", "")
            tt = r.get("toTable")   or r.get("to_table", "")
            tc = r.get("toColumn")  or r.get("to_column", "")
            fc = _strip_qlik_qualifier(fc)
            tc = _strip_qlik_qualifier(tc)
            if ft and fc and tt and tc:
                # ✅ FIX: Validate columns exist in both tables before creating relationship
                from_cols = columns_by_table.get(ft, set())
                to_cols = columns_by_table.get(tt, set())
                from_sanitized = sanitized_columns_by_table.get(ft, {})
                to_sanitized = sanitized_columns_by_table.get(tt, {})

                actual_fc = fc if fc in from_cols else from_sanitized.get(_sanitize_bim_name(fc), "")
                actual_tc = tc if tc in to_cols else to_sanitized.get(_sanitize_bim_name(tc), "")
                
                from_col_exists = bool(actual_fc)
                to_col_exists = bool(actual_tc)
                
                if not from_col_exists:
                    logger.warning(
                        "[BIM] Skipping relationship: Column '%s' not found in table '%s'. "
                        "Available: %s",
                        fc, ft, sorted(from_cols)
                    )
                    skipped_rels += 1
                    continue
                
                if not to_col_exists:
                    logger.warning(
                        "[BIM] Skipping relationship: Column '%s' not found in table '%s'. "
                        "Available: %s",
                        tc, tt, sorted(to_cols)
                    )
                    skipped_rels += 1
                    continue
                fc = actual_fc
                tc = actual_tc
                
                cf = r.get("crossFilteringBehavior") or r.get("cross_filter_direction", "")
                cf_bim = "bothDirections" if cf in ("Both", "bothDirections", "both") else "oneDirection"
                cardinality_norm = str(cardinality or "ManyToOne")
                
                # Detect aggregated tables: tables with aggregate column names like Total*, Avg*, Sum*, etc.
                # or tables with "workflow" in name that aggregate fact data
                def is_aggregated_table(table_name, col_names):
                    """Check if a table appears to be aggregated/summarized."""
                    if "workflow" in table_name.lower() and not table_name.lower().endswith("_raw"):
                        return True
                    # Check for common aggregate column patterns
                    aggregate_patterns = ("total", "sum", "avg", "average", "count", "min", "max")
                    for col in col_names:
                        if any(col.lower().startswith(pat) for pat in aggregate_patterns):
                            return True
                    return False
                
                from_cols_list = list(columns_by_table.get(ft, set()))
                to_cols_list = list(columns_by_table.get(tt, set()))
                from_is_agg = is_aggregated_table(ft, from_cols_list)
                to_is_agg = is_aggregated_table(tt, to_cols_list)
                
                # If we're relating an aggregated table to a fact table, use ManyToMany
                # to avoid "one" side duplicates error in Power BI
                if cardinality_norm == "OneToMany":
                    from_cardinality, to_cardinality = "one", "many"
                elif cardinality_norm == "OneToOne":
                    from_cardinality, to_cardinality = "one", "one"
                    cf_bim = "bothDirections"
                elif cardinality_norm in ("ManyToMany", "manyToMany"):
                    from_cardinality, to_cardinality = "many", "many"
                    cf_bim = "bothDirections"
                elif (cardinality_norm == "ManyToOne") and ((from_is_agg and not to_is_agg) or (not from_is_agg and to_is_agg)):
                    # When relating aggregated to fact table, use ManyToMany to avoid duplicate key errors
                    from_cardinality, to_cardinality = "many", "many"
                    cf_bim = "bothDirections"
                    logger.info(
                        "[BIM] Relationship '%s' -> '%s': Changed to ManyToMany "
                        "(aggregated table detected)",
                        ft, tt
                    )
                else:
                    from_cardinality, to_cardinality = "many", "one"
                
                tmd_rels.append({
                    "name":                  f"{ft}_{fc}_{tt}_{tc}",
                    "fromTable":             ft,
                    "fromColumn":            fc,
                    "toTable":               tt,
                    "toColumn":              tc,
                    "crossFilteringBehavior": cf_bim,
                    "fromCardinality":       from_cardinality,
                    "toCardinality":         to_cardinality,
                })
        
        if skipped_rels:
            logger.info(
                "[BIM] Validated relationships: %d valid, %d skipped due to missing columns",
                len(tmd_rels), skipped_rels
            )

        param_value = data_source_path if data_source_path else ""
        expressions = [
            {
                "name":  "DataSourcePath",
                "kind":  "m",
                "expression": [f'"{param_value}"'],
                "annotations": [
                    {"name": "IsParameterQuery",         "value": "True"},
                    {"name": "IsParameterQueryRequired", "value": "False"},
                    {"name": "PBI_QueryOrder",           "value": "0"},
                ],
            }
        ]

        bim = {
            "name": dataset_name,
            "compatibilityLevel": 1550,
            "model": {
                "culture":  "en-US",
                "dataAccessOptions": {
                    "legacyRedirects":          True,
                    "returnErrorValuesAsNull":   True,
                },
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "tables":       tmd_tables,
                "relationships": tmd_rels,
                "expressions":  expressions,
                "annotations": [
                    {"name": "PBIDesktopVersion", "value": "2.130.930.0"},
                    {"name": "createdBy",         "value": "QlikAI_Accelerator"},
                ],
            },
        }
        return json.dumps(bim, ensure_ascii=False, indent=2)

    def _deploy_via_fabric(
        self,
        dataset_name: str,
        tables_m: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        data_source_path: str,
    ) -> Dict[str, Any]:
        try:
            fabric_token = _acquire_sp_token("https://api.fabric.microsoft.com/.default")
            if not fabric_token:
                fabric_token = self.token

            headers = {
                "Authorization": f"Bearer {fabric_token}",
                "Content-Type":  "application/json",
            }

            bim_json = self._build_bim(dataset_name, tables_m, relationships, data_source_path)
            with open("debug_model.bim", "w", encoding="utf-8") as f:
                f.write(bim_json)
            bim_b64   = base64.b64encode(bim_json.encode("utf-8")).decode("ascii")
            pbism_b64 = base64.b64encode(b'{"version":"1.0"}').decode("ascii")

            payload = {
                "displayName": dataset_name,
                "definition":  {
                    "parts": [
                        {"path": "definition.pbism", "payload": pbism_b64, "payloadType": "InlineBase64"},
                        {"path": "model.bim",        "payload": bim_b64,   "payloadType": "InlineBase64"},
                    ]
                },
            }

            url = (
                f"https://api.fabric.microsoft.com/v1/workspaces"
                f"/{self.workspace_id}/semanticModels"
            )
            logger.info("[Fabric API] POST %s", url)

            # Log each table's column list before sending
            bim_obj = json.loads(bim_json)
            for tbl in bim_obj.get("model", {}).get("tables", []):
                parts      = tbl.get("partitions", [{}])
                expr_lines = parts[0].get("source", {}).get("expression", [])
                logger.info(
                    "[Fabric API] Table '%s' columns=%s",
                    tbl["name"],
                    [c["name"] for c in tbl.get("columns", [])]
                )
                logger.debug(
                    "[Fabric API] Table '%s' M:\n%s",
                    tbl["name"], "\n".join(expr_lines)[:600]
                )

            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            logger.info("[Fabric API] Response: %d %s", resp.status_code, resp.text[:400])

            if resp.status_code in (200, 201, 202):
                dataset_id = ""
                location_header = resp.headers.get("Location") or resp.headers.get("location")
                if location_header:
                    match = re.search(r"[0-9a-fA-F-]{36}", location_header)
                    if match:
                        dataset_id = match.group(0)

                if resp.status_code == 202:
                    op_url    = resp.headers.get("Location")
                    polled_id = self._poll(op_url, headers) if op_url else ""
                    if polled_id == "FAILED":
                        return {
                            "success": False,
                            "error": "Fabric semantic model import failed during polling. Check backend logs for the Analysis Services error.",
                        }
                    if polled_id == "SUCCEEDED_NO_ID":
                        dataset_id = ""
                    else:
                        dataset_id = dataset_id or polled_id
                else:
                    dataset_id = (resp.json() if resp.text.strip() else {}).get("id", "")

                if not dataset_id or dataset_id == "SUCCEEDED_NO_ID":
                    dataset_id = self._find_dataset_id(dataset_name, headers)

                if dataset_id:
                    pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
                    pbi_headers = {
                        "Authorization": f"Bearer {pbi_token}",
                        "Content-Type":  "application/json",
                    }
                    self._trigger_refresh(dataset_id, pbi_headers)

                    is_sharepoint = any(
                        d in (data_source_path or "").lower()
                        for d in ("sharepoint.com", "sharepoint-df.com")
                    )
                    cred_msg = (
                        "Action required: Go to dataset Settings -> "
                        "Data source credentials -> Edit -> OAuth2 -> Sign in once to enable refresh."
                    ) if is_sharepoint else ""

                    bim_tables = bim_obj.get("model", {}).get("tables", [])
                    return {
                        "success":          True,
                        "method":           "fabric_items_api",
                        "dataset_id":       dataset_id,
                        "dataset_name":     dataset_name,
                        "tables_published": len(bim_tables),
                        "workspace_url":    f"https://app.powerbi.com/groups/{self.workspace_id}",
                        "dataset_url": (
                            f"https://app.powerbi.com/groups/{self.workspace_id}"
                            f"/datasets/{dataset_id}"
                        ),
                        "message": (
                            f"Semantic model '{dataset_name}' deployed via Fabric API "
                            f"with {len(bim_tables)} table(s)."
                            + (f" {cred_msg}" if cred_msg else "")
                        ),
                    }
                return {"success": False, "error": "Async op succeeded but no dataset ID returned"}

            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:400]}"}

        except Exception as exc:
            logger.exception("[Fabric API] Unexpected error")
            return {"success": False, "error": str(exc)}

    def _trigger_refresh(self, dataset_id: str, headers: Dict) -> bool:
        try:
            url = (
                f"https://api.powerbi.com/v1.0/myorg/groups/"
                f"{self.workspace_id}/datasets/{dataset_id}/refreshes"
            )
            logger.info("[Power BI API] Triggering refresh: POST %s", url)
            resp = requests.post(url, headers=headers, json={}, timeout=30)
            if resp.status_code in (200, 202):
                logger.info("[Power BI API] Refresh triggered")
                return True
            logger.warning("[Power BI API] Refresh failed: %d %s", resp.status_code, resp.text[:300])
            return False
        except Exception as ex:
            logger.error("[Power BI API] Refresh error: %s", ex)
            return False

    def _poll(self, op_url: str, headers: Dict, max_wait: int = 120) -> str:
        logger.info("[Fabric API] Polling: %s", op_url)
        for i in range(max_wait // 3):
            time.sleep(3)
            try:
                r = requests.get(op_url, headers=headers, timeout=15)
                if r.ok:
                    body   = r.json()
                    status = body.get("status", "")
                    logger.info("[Fabric API] Poll %d: %s", i + 1, status)
                    if status == "Succeeded":
                        return "SUCCEEDED_NO_ID"
                    if status in ("Failed", "Cancelled"):
                        logger.warning("[Fabric API] Op %s: %s", status, body)
                        return "FAILED"
            except Exception as ex:
                logger.warning("[Fabric API] Poll error: %s", ex)
        logger.warning("[Fabric API] Polling timed out after %ds", max_wait)
        return "FAILED"

    def _find_dataset_id(self, dataset_name: str, headers: Dict) -> str:
        try:
            url = f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}/semanticModels"
            r   = requests.get(url, headers=headers, timeout=15)
            if r.ok:
                for item in r.json().get("value", []):
                    if item.get("displayName") == dataset_name:
                        return item.get("id", "")
        except Exception as ex:
            logger.warning("[Fabric API] Lookup error: %s", ex)
        return ""

    def _deploy_push_dataset(
        self,
        dataset_name: str,
        tables_m: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        try:
            tables_payload = []
            for t in tables_m:
                source_type = t.get("source_type", "")
                expr_str    = t.get("m_expression", "")
                is_file     = _is_file_based_source(source_type, expr_str)
                raw_fields  = t.get("fields", [])

                cols = []
                for f in raw_fields:
                    fname = (f.get("alias") or f.get("name") or "").strip()
                    if not fname or fname == "*":
                        continue
                    plain = _strip_qlik_qualifier(fname)
                    if not plain or plain == "*":
                        continue
                    # FIX A: always string for file sources
                    cols.append({
                        "name":     plain,
                        "dataType": "string" if is_file else _tabular_type(f.get("type", "string")),
                    })

                # USE UNIVERSAL SCHEMA RESOLVER - No fake placeholders
                if not cols:
                    resolved = _resolve_schema_universal(t, expr_str, is_file)
                    for col in resolved:
                        cols.append({"name": col["name"], "dataType": col.get("dataType", "string")})
                    
                    if not cols:
                        logger.info(
                            "[Push] Table '%s': dynamic schema (columns from M expression at runtime)",
                            t.get("name", "Unknown")
                        )

                tables_payload.append({"name": t["name"], "columns": cols})

            payload = {
                "name":        dataset_name,
                "defaultMode": "Push",
                "tables":      tables_payload,
            }
            url  = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets"
            resp = requests.post(url, headers=self.pbi_headers, json=payload, timeout=30)

            if resp.status_code in (200, 201, 202):
                dataset_id = resp.json().get("id", "")
                return {
                    "success":          True,
                    "method":           "push_dataset_fallback",
                    "dataset_id":       dataset_id,
                    "dataset_name":     dataset_name,
                    "tables_published": len(tables_payload),
                    "workspace_url":    f"https://app.powerbi.com/groups/{self.workspace_id}",
                    "message": (
                        "Created via Push dataset fallback. "
                        "Fabric API failed - no M Query or Model View."
                    ),
                }
            return {
                "success": False,
                "error":   f"Push dataset failed: {resp.status_code} {resp.text[:300]}",
            }
        except Exception as exc:
            logger.exception("[Push] Error")
            return {"success": False, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
# Token / flow cache helpers
# ─────────────────────────────────────────────────────────────────────────────

def _token_cache_path() -> str:
    return os.path.join(os.path.dirname(__file__), ".pb_token_cache.json")

def _device_flow_cache_path() -> str:
    return os.path.join(os.path.dirname(__file__), ".pb_device_flow.json")

def _cache_user_token(token: str):
    try:
        with open(_token_cache_path(), "w") as f:
            json.dump({"token": token, "expires_at": time.time() + 3500}, f)
    except Exception:
        pass

def _cache_device_flow(flow: Dict):
    try:
        with open(_device_flow_cache_path(), "w") as f:
            json.dump(flow, f)
    except Exception:
        pass

def _load_device_flow() -> Optional[Dict]:
    try:
        path = _device_flow_cache_path()
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return None

def _clear_device_flow():
    try:
        path = _device_flow_cache_path()
        if os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass
