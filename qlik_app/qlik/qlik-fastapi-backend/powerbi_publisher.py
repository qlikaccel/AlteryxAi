# """
# powerbi_publisher.py  -  QlikAI Accelerator
# Publishes a semantic model to Microsoft Fabric / Power BI Premium workspace.

# Strategy:
#   1. Fabric Items API  (POST /v1/workspaces/{id}/semanticModels)
#      - Requires: definition.pbism (version 1.0) + model.bim (TMSL V3)
#      - model.bim MUST have compatibilityLevel=1550, defaultPowerBIDataSourceVersion="powerBI_V3"
#      - Tables MUST have explicit columns (Fabric does NOT infer from M on create)
#   2. Push Dataset API  (fallback - limited, no M Query)

# Auth: Service Principal client credentials (silent - no user interaction).
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
# ) -> Dict[str, Any]:
#     relationships = relationships or []
#     if not workspace_id:
#         workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
#     if not workspace_id:
#         return {"success": False, "error": "POWERBI_WORKSPACE_ID not set"}
#     if db_connection_string:
#         tables_m = _rewrite_for_db_connect(tables_m, db_connection_string)
#     token = access_token or _acquire_sp_token()
#     return _Publisher(workspace_id=workspace_id, access_token=token).publish(
#         dataset_name, tables_m, relationships, data_source_path
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
#         expr = t.get("m_expression", "")
#         if src == "resident" or "Table.NestedJoin" in expr:
#             out.append(t)
#             continue
#         if src in ("sql", "odbc") or "Sql.Database" in expr or "Odbc.Query" in expr:
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
#     # Always return string — BIM must match what M query produces (all text).
#     # Type-specific mapping caused VT_BSTR → VT_I8 failures on empty values.
#     return "string"


# def _strip_qlik_qualifier(col_name: str) -> str:
#     """
#     Strip Qlik table-qualified prefix from column name.

#     'Dealer_Master.City_GeoInfo'  →  'City_GeoInfo'
#     'Model_Master.ModelID'        →  'ModelID'
#     'DealerID-ServiceID'          →  'DealerID-ServiceID'  (composite key, keep as-is)
#     '#"Something"'                →  '#"Something"'        (already escaped)

#     Critical: the actual CSV column is just 'City_GeoInfo', not the
#     Qlik-qualified 'Dealer_Master.City_GeoInfo'. Using the qualified name
#     causes column-not-found errors at query/refresh time.
#     """
#     if not col_name or col_name.startswith("#"):
#         return col_name
#     if "." in col_name and "-" not in col_name:
#         return col_name.split(".", 1)[-1]
#     return col_name


# def _infer_type_from_name(name: str) -> str:
#     """Infer type from column name heuristics.

#     Rules:
#     - Fields containing '-' are composite keys (DealerID-ServiceID) → always string
#     - Qlik qualified names like Table.FieldName → strip prefix first
#     - Fields ending with 'Number' (EngineNumber, ChassisNumber) → string, not integer
#     """
#     if "-" in name:
#         return "string"
#     n = name.split(".")[-1].lower().strip() if "." in name else name.lower().strip()
#     if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
#         return "date"
#     if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
#         return "number"
#     # "number" suffix (e.g. EngineNumber, ChassisNumber) → string, not integer
#     if n.endswith("number") or n.endswith("phone") or n.endswith("code"):
#         return "string"
#     if any(x in n for x in ["qty", "quantity", "year", "month", "day", "age", "rank", "km", "tons", "knots", "cc", "speed"]):
#         return "integer"
#     if n == "id" or (n.endswith("_id") and not n.endswith("number")):
#         return "integer"
#     if "count" in n:
#         return "integer"
#     return "string"


# def _extract_fields_from_m(expr: str) -> list:
#     """Extract column names and types from M expression.

#     KEY FIX: Qlik-qualified field names like 'Dealer_Master.City_GeoInfo' are
#     stripped to just 'City_GeoInfo' — matching the actual CSV column name.
#     Composite key fields like 'DealerID-ServiceID' stay as-is and are always string.
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
#             # CRITICAL: strip Qlik-qualified prefix → use plain CSV column name
#             col_name = _strip_qlik_qualifier(raw_name)
#             col_type_raw = entry.group(2).strip()
#             col_type = M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), "string"))
#             # Composite key → always string
#             if "-" in raw_name:
#                 col_type = "string"
#             elif col_type == "string":
#                 col_type = _infer_type_from_name(col_name)
#             if col_name:
#                 fields.append({"name": col_name, "type": col_type})
#                 if col_name != raw_name:
#                     logger.info("[Extract] Stripped Qlik qualifier: '%s' → '%s'", raw_name, col_name)
#         if fields:
#             logger.info("[BIM] Pattern A: extracted %d fields: %s", len(fields), [f["name"] for f in fields])
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
#             logger.info("[BIM] Pattern B: extracted %d fields: %s", len(fields), [f["name"] for f in fields])
#             return fields

#     # Pattern C: SharePoint.Files or PromoteHeaders without TransformColumnTypes
#     if "SharePoint.Files" in expr or "PromoteHeaders" in expr or "PromotedHeaders" in expr:
#         logger.info("[Extract] Detected SharePoint/PromoteHeaders source - schema inferred at runtime")
#         return []

#     logger.warning("[BIM] Could not extract fields from M expression")
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
#     """
#     Clean M expression before sending to Fabric API.
#     1. Strip leading comment lines (// Table: ...)
#     2. Fix 'Sourcelet' corruption - when two let blocks accidentally merge
#     3. Ensure expression starts with 'let'
#     """
#     # Remove leading comment lines like // Table: Name [csv]
#     lines = expr.strip().splitlines()
#     clean_lines = []
#     for line in lines:
#         if line.strip().startswith("//"):
#             continue
#         clean_lines.append(line)
#     expr = "\n".join(clean_lines).strip()

#     # Fix "Sourcelet" corruption
#     if "Sourcelet" in expr:
#         idx = expr.find("Sourcelet")
#         real_let_idx = expr.find("let", idx)
#         if real_let_idx != -1:
#             expr = expr[real_let_idx:]
#             logger.info("[sanitize_m] Fixed Sourcelet corruption")

#     # Ensure it starts with let
#     if not expr.strip().startswith("let"):
#         idx = expr.find("let")
#         if idx != -1:
#             expr = expr[idx:]

#     return expr.strip()


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
#     ) -> Dict[str, Any]:
#         if not self.token:
#             flow = initiate_device_code_flow()
#             return {
#                 "success": False, "auth_required": True,
#                 "device_code_url": flow.get("device_code_url"),
#                 "user_code": flow.get("user_code"),
#                 "message": flow.get("message", ""),
#                 "error": "Authentication required.",
#             }
#         result = self._deploy_via_fabric(dataset_name, tables_m, relationships, data_source_path)
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
#         tmd_tables = []
#         for t in tables_m:
#             expr = t.get("m_expression", "").strip()
#             if not expr:
#                 continue
#             logger.info("==== RAW M FROM tables_m FOR TABLE %s ====", t["name"])
#             logger.info("\n%s\n", expr)

#             fields = t.get("fields", [])
#             logger.info("[BIM] Table '%s' raw fields from tables_m: %s", t["name"], fields)

#             if not fields:
#                 fields = _extract_fields_from_m(expr)
#                 logger.info("[BIM] Extracted fields for '%s': %s", t["name"], fields)
#             else:
#                 # CRITICAL: Strip Qlik-qualified names to match actual CSV column names.
#                 # 'Dealer_Master.City_GeoInfo' → 'City_GeoInfo'
#                 # Composite keys like 'DealerID-ServiceID' stay as-is and are always string.
#                 fixed_fields = []
#                 for f in fields:
#                     raw_name = f.get("name", "")
#                     plain_name = _strip_qlik_qualifier(raw_name)
#                     ftype = f.get("type", "string")
#                     if "-" in raw_name:
#                         ftype = "string"
#                     fixed_fields.append({"name": plain_name, "type": ftype})
#                     if plain_name != raw_name:
#                         logger.info("[BIM] Fixed column name: '%s' → '%s'", raw_name, plain_name)
#                 fields = fixed_fields
#                 logger.info("[BIM] Fixed fields for '%s': %s", t["name"], fields)

#             columns = []
#             for f in fields:
#                 columns.append({
#                     "name": f["name"],
#                     "dataType": _tabular_type(f.get("type", "string")),
#                     "sourceColumn": f["name"],
#                     "summarizeBy": "none",
#                     "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}]
#                 })

#             if not columns:
#                 columns = [{
#                     "name": "Value",
#                     "dataType": "string",
#                     "sourceColumn": "Value",
#                     "summarizeBy": "none",
#                     "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}]
#                 }]

#             fixed_expr = _fix_multiline_rows(_sanitize_m(expr))
#             logger.info("==== FINAL M SENT TO FABRIC FOR TABLE %s ====", t["name"])
#             logger.info("\n%s\n", fixed_expr)
#             tmd_tables.append({
#                 "name": t["name"],
#                 "columns": columns,
#                 "partitions": [{
#                     "name": f"{t['name']}-Partition",
#                     "mode": "import",
#                     "source": {
#                         "type": "m",
#                         "expression": fixed_expr.splitlines()
#                     }
#                 }]
#             })

#         tmd_rels = []
#         for r in relationships:
#             ft = r.get("fromTable") or r.get("from_table", "")
#             fc = r.get("fromColumn") or r.get("from_column", "")
#             tt = r.get("toTable")   or r.get("to_table", "")
#             tc = r.get("toColumn")  or r.get("to_column", "")
#             # CRITICAL: Strip Qlik-qualified names from relationship columns too
#             fc = _strip_qlik_qualifier(fc)
#             tc = _strip_qlik_qualifier(tc)
#             if ft and fc and tt and tc:
#                 cf = r.get("crossFilteringBehavior") or r.get("cross_filter_direction", "")
#                 cf_bim = "bothDirections" if cf in ("Both", "bothDirections", "both") else "oneDirection"
#                 tmd_rels.append({
#                     "name": f"{ft}_{fc}_{tt}_{tc}",
#                     "fromTable": ft, "fromColumn": fc,
#                     "toTable": tt,   "toColumn": tc,
#                     "crossFilteringBehavior": cf_bim,
#                 })


#         param_value = data_source_path if data_source_path else ""
#         expressions = [
#             {
#                 "name": "DataSourcePath",
#                 "kind": "m",
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
#                 "culture": "en-US",
#                 "dataAccessOptions": {
#                     "legacyRedirects": True,
#                     "returnErrorValuesAsNull": True
#                 },
#                 "defaultPowerBIDataSourceVersion": "powerBI_V3",
#                 "sourceQueryCulture": "en-US",
#                 "tables": tmd_tables,
#                 "relationships": tmd_rels,
#                 "expressions": expressions,
#                 "annotations": [
#                     {"name": "PBIDesktopVersion", "value": "2.130.930.0"},
#                     {"name": "createdBy", "value": "QlikAI_Accelerator"},
#                 ]
#             }
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
#                 "Content-Type": "application/json",
#             }

#             bim_json = self._build_bim(dataset_name, tables_m, relationships, data_source_path)
#             with open("debug_model.bim", "w", encoding="utf-8") as f:
#                 f.write(bim_json)
#             bim_b64   = base64.b64encode(bim_json.encode("utf-8")).decode("ascii")
#             pbism_b64 = base64.b64encode(b'{"version":"1.0"}').decode("ascii")

#             payload = {
#                 "displayName": dataset_name,
#                 "definition": {
#                     "parts": [
#                         {"path": "definition.pbism", "payload": pbism_b64, "payloadType": "InlineBase64"},
#                         {"path": "model.bim",        "payload": bim_b64,   "payloadType": "InlineBase64"},
#                     ]
#                 }
#             }

#             url = (
#                 f"https://api.fabric.microsoft.com/v1/workspaces"
#                 f"/{self.workspace_id}/semanticModels"
#             )
#             logger.info("[Fabric API] POST %s", url)

#             bim_obj = json.loads(bim_json)
#             for tbl in bim_obj.get("model", {}).get("tables", []):
#                 parts = tbl.get("partitions", [{}])
#                 expr_lines = parts[0].get("source", {}).get("expression", [])
#                 expr_preview = "\n".join(expr_lines)[:1000]
#                 logger.info("[Fabric API] Table '%s' M expression:\n%s", tbl["name"], expr_preview)

#             resp = requests.post(url, headers=headers, json=payload, timeout=60)
#             logger.info("[Fabric API] Response: %d %s", resp.status_code, resp.text[:400])

#             if resp.status_code in (200, 201, 202):
#                 dataset_id = ""

#                 location_header = resp.headers.get("Location") or resp.headers.get("location")
#                 if location_header:
#                     match = re.search(r"[0-9a-fA-F-]{36}", location_header)
#                     if match:
#                         dataset_id = match.group(0)
#                         logger.info("[Fabric API] Dataset ID from initial header: %s", dataset_id)

#                 if resp.status_code == 202:
#                     op_url = resp.headers.get("Location")
#                     polled_id = self._poll(op_url, headers) if op_url else ""
#                     if polled_id == "SUCCEEDED_NO_ID":
#                         dataset_id = ""
#                     else:
#                         dataset_id = dataset_id or polled_id
#                 else:
#                     dataset_id = (resp.json() if resp.text.strip() else {}).get("id", "")

#                 # After polling, look up the real semantic model ID by name.
#                 # The Location header contains an Operation ID (temporary), NOT the model ID.
#                 if not dataset_id or dataset_id == "SUCCEEDED_NO_ID":
#                     dataset_id = self._find_dataset_id(dataset_name, headers)
#                     logger.info("[Fabric API] Looked up semantic model ID: %s", dataset_id)

#                 if dataset_id:
#                     logger.info("[Fabric API] Created: %s", dataset_id)

#                     # Trigger refresh so M queries execute and data loads from SharePoint/files
#                     pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
#                     pbi_headers = {
#                         "Authorization": f"Bearer {pbi_token}",
#                         "Content-Type": "application/json",
#                     }
#                     self._trigger_refresh(dataset_id, pbi_headers)

#                     is_sharepoint = any(d in (data_source_path or "").lower()
#                                         for d in ("sharepoint.com", "sharepoint-df.com"))
#                     cred_msg = (
#                         "Action required: Go to dataset Settings → "
#                         "Data source credentials → Edit → OAuth2 → Sign in once to enable refresh."
#                     ) if is_sharepoint else ""

#                     return {
#                         "success": True,
#                         "method": "fabric_items_api",
#                         "dataset_id": dataset_id,
#                         "dataset_name": dataset_name,
#                         "workspace_url": f"https://app.powerbi.com/groups/{self.workspace_id}",
#                         "dataset_url": (
#                             f"https://app.powerbi.com/groups/{self.workspace_id}"
#                             f"/datasets/{dataset_id}"
#                         ),
#                         "message": (
#                             f"Semantic model '{dataset_name}' deployed via Fabric API "
#                             f"with {len(tables_m)} table(s) and full M Query support."
#                             + (f" {cred_msg}" if cred_msg else "")
#                         ),
#                     }
#                 return {"success": False, "error": "Async op succeeded but no dataset ID returned"}

#             return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:400]}"}

#         except Exception as exc:
#             logger.exception("[Fabric API] Unexpected error")
#             return {"success": False, "error": str(exc)}

#     def _trigger_refresh(self, dataset_id: str, headers: Dict) -> bool:
#         """Trigger a dataset refresh via Power BI REST API so M queries execute and data loads."""
#         try:
#             url = (
#                 f"https://api.powerbi.com/v1.0/myorg/groups/"
#                 f"{self.workspace_id}/datasets/{dataset_id}/refreshes"
#             )
#             logger.info("[Power BI API] Triggering refresh: POST %s", url)
#             resp = requests.post(url, headers=headers, json={}, timeout=30)
#             if resp.status_code in (200, 202):
#                 logger.info("[Power BI API] ✅ Refresh triggered — M query will execute and data will load")
#                 return True
#             elif resp.status_code == 404:
#                 logger.warning("[Power BI API] ⚠️  404: Dataset not found. Verify the semantic model ID is correct")
#                 return False
#             else:
#                 logger.warning("[Power BI API] Refresh failed: %d %s", resp.status_code, resp.text[:300])
#                 return False
#         except Exception as ex:
#             logger.error("[Power BI API] Refresh error: %s", ex)
#             return False

#     def _set_sharepoint_credentials(
#         self,
#         dataset_id: str,
#         sharepoint_url: str,
#         fabric_headers: Dict,
#     ) -> Dict[str, Any]:
#         """
#         Bind SharePoint credentials via Fabric API datasource listing
#         then Power BI REST API credential patching.
#         Falls back gracefully — never blocks the publish result.
#         """
#         try:
#             client_id     = os.getenv("POWERBI_CLIENT_ID", "")
#             client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
#             tenant_id     = os.getenv("POWERBI_TENANT_ID", "")

#             if not all([client_id, client_secret, tenant_id]):
#                 logger.warning("[Credentials] SP env vars missing — skipping credential bind")
#                 return {"success": False, "message": "Credentials not bound (SP env vars missing)."}

#             # Use Fabric token to list datasources
#             fabric_token = _acquire_sp_token("https://api.fabric.microsoft.com/.default")
#             if not fabric_token:
#                 logger.warning("[Credentials] Could not acquire Fabric token")
#                 return {"success": False, "message": "Credentials not bound (token error)."}

#             fabric_hdrs = {
#                 "Authorization": f"Bearer {fabric_token}",
#                 "Content-Type": "application/json",
#             }

#             # Step 1 — List datasources via Fabric API
#             ds_url = (
#                 f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}"
#                 f"/semanticModels/{dataset_id}/datasources"
#             )
#             logger.info("[Credentials] Fetching datasources: %s", ds_url)
#             ds_resp = requests.get(ds_url, headers=fabric_hdrs, timeout=30)

#             if not ds_resp.ok:
#                 logger.warning("[Credentials] Datasource fetch failed: %d %s",
#                                ds_resp.status_code, ds_resp.text[:300])
#                 return {
#                     "success": False,
#                     "message": "Credentials not bound (datasource list failed). Set manually in portal."
#                 }

#             datasources = ds_resp.json().get("value", [])
#             logger.info("[Credentials] Found %d datasource(s): %s", len(datasources), datasources)

#             # Step 2 — Use PBI token for credential patching
#             pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
#             pbi_hdrs = {
#                 "Authorization": f"Bearer {pbi_token}",
#                 "Content-Type": "application/json",
#             }

#             bound_count = 0
#             for ds in datasources:
#                 ds_type = ds.get("datasourceType", "").lower()
#                 logger.info("[Credentials] Datasource type: %s", ds_type)

#                 if "sharepoint" not in ds_type:
#                     continue

#                 gateway_id    = ds.get("gatewayId", "")
#                 datasource_id = ds.get("datasourceId", "")
#                 logger.info("[Credentials] gatewayId=%s datasourceId=%s", gateway_id, datasource_id)

#                 if not gateway_id or not datasource_id:
#                     logger.warning("[Credentials] Missing gatewayId or datasourceId — skipping")
#                     continue

#                 # Step 3 — Patch with ServicePrincipal credentials
#                 cred_data = json.dumps([
#                     {"name": "tenantId",     "value": tenant_id},
#                     {"name": "clientId",     "value": client_id},
#                     {"name": "clientSecret", "value": client_secret},
#                 ])
#                 patch_url = (
#                     f"https://api.powerbi.com/v1.0/myorg/gateways/{gateway_id}"
#                     f"/datasources/{datasource_id}"
#                 )
#                 patch_body = {
#                     "credentialDetails": {
#                         "credentialType":      "ServicePrincipal",
#                         "credentials":         cred_data,
#                         "encryptedConnection": "Encrypted",
#                         "encryptionAlgorithm": "None",
#                         "privacyLevel":        "Organizational",
#                     }
#                 }
#                 logger.info("[Credentials] Patching: %s", patch_url)
#                 patch_resp = requests.patch(
#                     patch_url, headers=pbi_hdrs, json=patch_body, timeout=15
#                 )
#                 logger.info("[Credentials] Patch response: %d %s",
#                             patch_resp.status_code, patch_resp.text[:300])

#                 if patch_resp.ok:
#                     bound_count += 1
#                     logger.info("[Credentials] Bound successfully for datasource %s", datasource_id)
#                 else:
#                     logger.warning("[Credentials] Bind failed: %d %s",
#                                    patch_resp.status_code, patch_resp.text[:300])

#             if bound_count > 0:
#                 return {
#                     "success": True,
#                     "message": f"SharePoint credentials auto-bound ({bound_count} datasource(s))."
#                 }

#             # Step 4 — Log full payload for debugging if nothing bound
#             logger.info("[Credentials] Full datasource payload: %s", json.dumps(datasources, indent=2))
#             return {
#                 "success": False,
#                 "message": (
#                     "SharePoint datasource found but no gateway binding possible. "
#                     "Please set credentials once manually: dataset settings → "
#                     "Data source credentials → Edit → OAuth2 → Sign in with org account."
#                 )
#             }

#         except Exception as exc:
#             logger.warning("[Credentials] Unexpected error: %s", exc)
#             return {"success": False, "message": f"Credential bind error: {exc}"}

#     def _poll(self, op_url: str, headers: Dict, max_wait: int = 120) -> str:
#         logger.info("[Fabric API] Polling: %s", op_url)
#         for i in range(max_wait // 3):
#             time.sleep(3)
#             try:
#                 r = requests.get(op_url, headers=headers, timeout=15)
#                 if r.ok:
#                     body = r.json()
#                     status = body.get("status", "")
#                     logger.info("[Fabric API] Poll %d: %s", i + 1, status)
#                     if status == "Succeeded":
#                         logger.info("[Fabric API] Full success body: %s", json.dumps(body))
#                         return "SUCCEEDED_NO_ID"
#                     if status in ("Failed", "Cancelled"):
#                         logger.warning("[Fabric API] Op %s: %s", status, body)
#                         return ""
#             except Exception as ex:
#                 logger.warning("[Fabric API] Poll error: %s", ex)
#         logger.warning("[Fabric API] Polling timed out after %ds", max_wait)
#         return ""

#     def _find_dataset_id(self, dataset_name: str, headers: Dict) -> str:
#         try:
#             url = f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}/semanticModels"
#             r = requests.get(url, headers=headers, timeout=15)
#             if r.ok:
#                 items = r.json().get("value", [])
#                 for item in items:
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
#                 fields = t.get("fields", [])
#                 if fields:
#                     cols = [
#                         {"name": f["name"], "dataType": _tabular_type(f.get("type", "string"))}
#                         for f in fields
#                     ]
#                 else:
#                     cols = [{"name": "Value", "dataType": "string"}]
#                 tables_payload.append({"name": t["name"], "columns": cols})

#             payload = {
#                 "name": dataset_name,
#                 "defaultMode": "Push",
#                 "tables": tables_payload,
#             }
#             url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets"
#             resp = requests.post(url, headers=self.pbi_headers, json=payload, timeout=30)

#             if resp.status_code in (200, 201, 202):
#                 dataset_id = resp.json().get("id", "")
#                 return {
#                     "success": True,
#                     "method": "push_dataset_fallback",
#                     "dataset_id": dataset_id,
#                     "dataset_name": dataset_name,
#                     "workspace_url": f"https://app.powerbi.com/groups/{self.workspace_id}",
#                     "message": (
#                         "Created via Push dataset fallback. "
#                         "Fabric API failed - no M Query or Model View."
#                     ),
#                 }
#             return {
#                 "success": False,
#                 "error": f"Push dataset failed: {resp.status_code} {resp.text[:300]}",
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
Publishes a semantic model to Microsoft Fabric / Power BI Premium workspace.

Strategy:
  1. Fabric Items API  (POST /v1/workspaces/{id}/semanticModels)
     - Requires: definition.pbism (version 1.0) + model.bim (TMSL V3)
     - model.bim MUST have compatibilityLevel=1550, defaultPowerBIDataSourceVersion="powerBI_V3"
     - Tables MUST have explicit columns (Fabric does NOT infer from M on create)
  2. Push Dataset API  (fallback - limited, no M Query)

Auth: Service Principal client credentials (silent - no user interaction).
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
) -> Dict[str, Any]:
    relationships = relationships or []
    if not workspace_id:
        workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        return {"success": False, "error": "POWERBI_WORKSPACE_ID not set"}
    if db_connection_string:
        tables_m = _rewrite_for_db_connect(tables_m, db_connection_string)
    token = access_token or _acquire_sp_token()
    return _Publisher(workspace_id=workspace_id, access_token=token).publish(
        dataset_name, tables_m, relationships, data_source_path
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
        expr = t.get("m_expression", "")
        if src == "resident" or "Table.NestedJoin" in expr:
            out.append(t)
            continue
        if src in ("sql", "odbc") or "Sql.Database" in expr or "Odbc.Query" in expr:
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
    # Always return string — BIM must match what M query produces (all text).
    # Type-specific mapping caused VT_BSTR → VT_I8 failures on empty values.
    return "string"


def _strip_qlik_qualifier(col_name: str) -> str:
    """
    Strip Qlik table-qualified prefix from column name.

    'Dealer_Master.City_GeoInfo'  →  'City_GeoInfo'
    'Model_Master.ModelID'        →  'ModelID'
    'DealerID-ServiceID'          →  'DealerID-ServiceID'  (composite key, keep as-is)
    '#"Something"'                →  '#"Something"'        (already escaped)

    Critical: the actual CSV column is just 'City_GeoInfo', not the
    Qlik-qualified 'Dealer_Master.City_GeoInfo'. Using the qualified name
    causes column-not-found errors at query/refresh time.
    """
    if not col_name or col_name.startswith("#"):
        return col_name
    if "." in col_name and "-" not in col_name:
        return col_name.split(".", 1)[-1]
    return col_name


def _infer_type_from_name(name: str) -> str:
    """Infer type from column name heuristics.

    Rules:
    - Fields containing '-' are composite keys (DealerID-ServiceID) → always string
    - Qlik qualified names like Table.FieldName → strip prefix first
    - Fields ending with 'Number' (EngineNumber, ChassisNumber) → string, not integer
    """
    if "-" in name:
        return "string"
    n = name.split(".")[-1].lower().strip() if "." in name else name.lower().strip()
    if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
        return "date"
    if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
        return "number"
    # "number" suffix (e.g. EngineNumber, ChassisNumber) → string, not integer
    if n.endswith("number") or n.endswith("phone") or n.endswith("code"):
        return "string"
    if any(x in n for x in ["qty", "quantity", "year", "month", "day", "age", "rank", "km", "tons", "knots", "cc", "speed"]):
        return "integer"
    if n == "id" or (n.endswith("_id") and not n.endswith("number")):
        return "integer"
    if "count" in n:
        return "integer"
    return "string"


def _extract_fields_from_m(expr: str) -> list:
    """Extract column names and types from M expression.

    KEY FIX: Qlik-qualified field names like 'Dealer_Master.City_GeoInfo' are
    stripped to just 'City_GeoInfo' — matching the actual CSV column name.
    Composite key fields like 'DealerID-ServiceID' stay as-is and are always string.
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
            # CRITICAL: strip Qlik-qualified prefix → use plain CSV column name
            col_name = _strip_qlik_qualifier(raw_name)
            col_type_raw = entry.group(2).strip()
            col_type = M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), "string"))
            # Composite key → always string
            if "-" in raw_name:
                col_type = "string"
            elif col_type == "string":
                col_type = _infer_type_from_name(col_name)
            if col_name:
                fields.append({"name": col_name, "type": col_type})
                if col_name != raw_name:
                    logger.info("[Extract] Stripped Qlik qualifier: '%s' → '%s'", raw_name, col_name)
        if fields:
            logger.info("[BIM] Pattern A: extracted %d fields: %s", len(fields), [f["name"] for f in fields])
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
            logger.info("[BIM] Pattern B: extracted %d fields: %s", len(fields), [f["name"] for f in fields])
            return fields

    # Pattern C: SharePoint.Files or PromoteHeaders without TransformColumnTypes
    if "SharePoint.Files" in expr or "PromoteHeaders" in expr or "PromotedHeaders" in expr:
        logger.info("[Extract] Detected SharePoint/PromoteHeaders source - schema inferred at runtime")
        return []

    logger.warning("[BIM] Could not extract fields from M expression")
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
    """
    Clean M expression before sending to Fabric API.
    1. Strip leading comment lines (// Table: ...)
    2. Fix 'Sourcelet' corruption - when two let blocks accidentally merge
    3. Ensure expression starts with 'let'
    """
    # Remove leading comment lines like // Table: Name [csv]
    lines = expr.strip().splitlines()
    clean_lines = []
    for line in lines:
        if line.strip().startswith("//"):
            continue
        clean_lines.append(line)
    expr = "\n".join(clean_lines).strip()

    # Fix "Sourcelet" corruption
    if "Sourcelet" in expr:
        idx = expr.find("Sourcelet")
        real_let_idx = expr.find("let", idx)
        if real_let_idx != -1:
            expr = expr[real_let_idx:]
            logger.info("[sanitize_m] Fixed Sourcelet corruption")

    # Ensure it starts with let
    if not expr.strip().startswith("let"):
        idx = expr.find("let")
        if idx != -1:
            expr = expr[idx:]

    return expr.strip()


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
    ) -> Dict[str, Any]:
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
        tmd_tables = []
        for t in tables_m:
            expr = t.get("m_expression", "").strip()
            if not expr:
                continue
            logger.info("==== RAW M FROM tables_m FOR TABLE %s ====", t["name"])
            logger.info("\n%s\n", expr)

            fields = t.get("fields", [])
            logger.info("[BIM] Table '%s' raw fields from tables_m: %s", t["name"], fields)

            if not fields:
                fields = _extract_fields_from_m(expr)
                logger.info("[BIM] Extracted fields for '%s': %s", t["name"], fields)
            else:
                # CRITICAL: Strip Qlik-qualified names to match actual CSV column names.
                # 'Dealer_Master.City_GeoInfo' → 'City_GeoInfo'
                # Composite keys like 'DealerID-ServiceID' stay as-is and are always string.
                fixed_fields = []
                for f in fields:
                    raw_name = f.get("name", "")
                    plain_name = _strip_qlik_qualifier(raw_name)
                    ftype = f.get("type", "string")
                    if "-" in raw_name:
                        ftype = "string"
                    fixed_fields.append({"name": plain_name, "type": ftype})
                    if plain_name != raw_name:
                        logger.info("[BIM] Fixed column name: '%s' → '%s'", raw_name, plain_name)
                fields = fixed_fields
                logger.info("[BIM] Fixed fields for '%s': %s", t["name"], fields)

            columns = []
            for f in fields:
                columns.append({
                    "name": f["name"],
                    "dataType": _tabular_type(f.get("type", "string")),
                    "sourceColumn": f["name"],
                    "summarizeBy": "none",
                    "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}]
                })

            if not columns:
                columns = [{
                    "name": "Value",
                    "dataType": "string",
                    "sourceColumn": "Value",
                    "summarizeBy": "none",
                    "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}]
                }]

            fixed_expr = _fix_multiline_rows(_sanitize_m(expr))
            logger.info("==== FINAL M SENT TO FABRIC FOR TABLE %s ====", t["name"])
            logger.info("\n%s\n", fixed_expr)
            tmd_tables.append({
                "name": t["name"],
                "columns": columns,
                "partitions": [{
                    "name": f"{t['name']}-Partition",
                    "mode": "import",
                    "source": {
                        "type": "m",
                        "expression": fixed_expr.splitlines()
                    }
                }]
            })

        tmd_rels = []
        for r in relationships:
            # Skip inactive relationships (ambiguous paths deactivated by extractor)
            if r.get("is_active") is False:
                continue
            # Skip manyToMany — Power BI Fabric TMSL does not support them
            # (they are flagged as descriptive/false-positive fields by the extractor)
            cardinality = r.get("cardinality") or r.get("fromCardinality", "")
            if cardinality == "manyToMany":
                continue
            ft = r.get("fromTable") or r.get("from_table", "")
            fc = r.get("fromColumn") or r.get("from_column", "")
            tt = r.get("toTable")   or r.get("to_table", "")
            tc = r.get("toColumn")  or r.get("to_column", "")
            # CRITICAL: Strip Qlik-qualified names from relationship columns too
            fc = _strip_qlik_qualifier(fc)
            tc = _strip_qlik_qualifier(tc)
            if ft and fc and tt and tc:
                cf = r.get("crossFilteringBehavior") or r.get("cross_filter_direction", "")
                cf_bim = "bothDirections" if cf in ("Both", "bothDirections", "both") else "oneDirection"
                tmd_rels.append({
                    "name": f"{ft}_{fc}_{tt}_{tc}",
                    "fromTable": ft, "fromColumn": fc,
                    "toTable": tt,   "toColumn": tc,
                    "crossFilteringBehavior": cf_bim,
                })


        param_value = data_source_path if data_source_path else ""
        expressions = [
            {
                "name": "DataSourcePath",
                "kind": "m",
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
                "culture": "en-US",
                "dataAccessOptions": {
                    "legacyRedirects": True,
                    "returnErrorValuesAsNull": True
                },
                "defaultPowerBIDataSourceVersion": "powerBI_V3",
                "sourceQueryCulture": "en-US",
                "tables": tmd_tables,
                "relationships": tmd_rels,
                "expressions": expressions,
                "annotations": [
                    {"name": "PBIDesktopVersion", "value": "2.130.930.0"},
                    {"name": "createdBy", "value": "QlikAI_Accelerator"},
                ]
            }
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
                "Content-Type": "application/json",
            }

            bim_json = self._build_bim(dataset_name, tables_m, relationships, data_source_path)
            with open("debug_model.bim", "w", encoding="utf-8") as f:
                f.write(bim_json)
            bim_b64   = base64.b64encode(bim_json.encode("utf-8")).decode("ascii")
            pbism_b64 = base64.b64encode(b'{"version":"1.0"}').decode("ascii")

            payload = {
                "displayName": dataset_name,
                "definition": {
                    "parts": [
                        {"path": "definition.pbism", "payload": pbism_b64, "payloadType": "InlineBase64"},
                        {"path": "model.bim",        "payload": bim_b64,   "payloadType": "InlineBase64"},
                    ]
                }
            }

            url = (
                f"https://api.fabric.microsoft.com/v1/workspaces"
                f"/{self.workspace_id}/semanticModels"
            )
            logger.info("[Fabric API] POST %s", url)

            bim_obj = json.loads(bim_json)
            for tbl in bim_obj.get("model", {}).get("tables", []):
                parts = tbl.get("partitions", [{}])
                expr_lines = parts[0].get("source", {}).get("expression", [])
                expr_preview = "\n".join(expr_lines)[:1000]
                logger.info("[Fabric API] Table '%s' M expression:\n%s", tbl["name"], expr_preview)

            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            logger.info("[Fabric API] Response: %d %s", resp.status_code, resp.text[:400])

            if resp.status_code in (200, 201, 202):
                dataset_id = ""

                location_header = resp.headers.get("Location") or resp.headers.get("location")
                if location_header:
                    match = re.search(r"[0-9a-fA-F-]{36}", location_header)
                    if match:
                        dataset_id = match.group(0)
                        logger.info("[Fabric API] Dataset ID from initial header: %s", dataset_id)

                if resp.status_code == 202:
                    op_url = resp.headers.get("Location")
                    polled_id = self._poll(op_url, headers) if op_url else ""
                    if polled_id == "SUCCEEDED_NO_ID":
                        dataset_id = ""
                    else:
                        dataset_id = dataset_id or polled_id
                else:
                    dataset_id = (resp.json() if resp.text.strip() else {}).get("id", "")

                # After polling, look up the real semantic model ID by name.
                # The Location header contains an Operation ID (temporary), NOT the model ID.
                if not dataset_id or dataset_id == "SUCCEEDED_NO_ID":
                    dataset_id = self._find_dataset_id(dataset_name, headers)
                    logger.info("[Fabric API] Looked up semantic model ID: %s", dataset_id)

                if dataset_id:
                    logger.info("[Fabric API] Created: %s", dataset_id)

                    # Trigger refresh so M queries execute and data loads from SharePoint/files
                    pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
                    pbi_headers = {
                        "Authorization": f"Bearer {pbi_token}",
                        "Content-Type": "application/json",
                    }
                    self._trigger_refresh(dataset_id, pbi_headers)

                    is_sharepoint = any(d in (data_source_path or "").lower()
                                        for d in ("sharepoint.com", "sharepoint-df.com"))
                    cred_msg = (
                        "Action required: Go to dataset Settings → "
                        "Data source credentials → Edit → OAuth2 → Sign in once to enable refresh."
                    ) if is_sharepoint else ""

                    return {
                        "success": True,
                        "method": "fabric_items_api",
                        "dataset_id": dataset_id,
                        "dataset_name": dataset_name,
                        "workspace_url": f"https://app.powerbi.com/groups/{self.workspace_id}",
                        "dataset_url": (
                            f"https://app.powerbi.com/groups/{self.workspace_id}"
                            f"/datasets/{dataset_id}"
                        ),
                        "message": (
                            f"Semantic model '{dataset_name}' deployed via Fabric API "
                            f"with {len(tables_m)} table(s) and full M Query support."
                            + (f" {cred_msg}" if cred_msg else "")
                        ),
                    }
                return {"success": False, "error": "Async op succeeded but no dataset ID returned"}

            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:400]}"}

        except Exception as exc:
            logger.exception("[Fabric API] Unexpected error")
            return {"success": False, "error": str(exc)}

    def _trigger_refresh(self, dataset_id: str, headers: Dict) -> bool:
        """Trigger a dataset refresh via Power BI REST API so M queries execute and data loads."""
        try:
            url = (
                f"https://api.powerbi.com/v1.0/myorg/groups/"
                f"{self.workspace_id}/datasets/{dataset_id}/refreshes"
            )
            logger.info("[Power BI API] Triggering refresh: POST %s", url)
            resp = requests.post(url, headers=headers, json={}, timeout=30)
            if resp.status_code in (200, 202):
                logger.info("[Power BI API] ✅ Refresh triggered — M query will execute and data will load")
                return True
            elif resp.status_code == 404:
                logger.warning("[Power BI API] ⚠️  404: Dataset not found. Verify the semantic model ID is correct")
                return False
            else:
                logger.warning("[Power BI API] Refresh failed: %d %s", resp.status_code, resp.text[:300])
                return False
        except Exception as ex:
            logger.error("[Power BI API] Refresh error: %s", ex)
            return False

    def _set_sharepoint_credentials(
        self,
        dataset_id: str,
        sharepoint_url: str,
        fabric_headers: Dict,
    ) -> Dict[str, Any]:
        """
        Bind SharePoint credentials via Fabric API datasource listing
        then Power BI REST API credential patching.
        Falls back gracefully — never blocks the publish result.
        """
        try:
            client_id     = os.getenv("POWERBI_CLIENT_ID", "")
            client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
            tenant_id     = os.getenv("POWERBI_TENANT_ID", "")

            if not all([client_id, client_secret, tenant_id]):
                logger.warning("[Credentials] SP env vars missing — skipping credential bind")
                return {"success": False, "message": "Credentials not bound (SP env vars missing)."}

            # Use Fabric token to list datasources
            fabric_token = _acquire_sp_token("https://api.fabric.microsoft.com/.default")
            if not fabric_token:
                logger.warning("[Credentials] Could not acquire Fabric token")
                return {"success": False, "message": "Credentials not bound (token error)."}

            fabric_hdrs = {
                "Authorization": f"Bearer {fabric_token}",
                "Content-Type": "application/json",
            }

            # Step 1 — List datasources via Fabric API
            ds_url = (
                f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}"
                f"/semanticModels/{dataset_id}/datasources"
            )
            logger.info("[Credentials] Fetching datasources: %s", ds_url)
            ds_resp = requests.get(ds_url, headers=fabric_hdrs, timeout=30)

            if not ds_resp.ok:
                logger.warning("[Credentials] Datasource fetch failed: %d %s",
                               ds_resp.status_code, ds_resp.text[:300])
                return {
                    "success": False,
                    "message": "Credentials not bound (datasource list failed). Set manually in portal."
                }

            datasources = ds_resp.json().get("value", [])
            logger.info("[Credentials] Found %d datasource(s): %s", len(datasources), datasources)

            # Step 2 — Use PBI token for credential patching
            pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
            pbi_hdrs = {
                "Authorization": f"Bearer {pbi_token}",
                "Content-Type": "application/json",
            }

            bound_count = 0
            for ds in datasources:
                ds_type = ds.get("datasourceType", "").lower()
                logger.info("[Credentials] Datasource type: %s", ds_type)

                if "sharepoint" not in ds_type:
                    continue

                gateway_id    = ds.get("gatewayId", "")
                datasource_id = ds.get("datasourceId", "")
                logger.info("[Credentials] gatewayId=%s datasourceId=%s", gateway_id, datasource_id)

                if not gateway_id or not datasource_id:
                    logger.warning("[Credentials] Missing gatewayId or datasourceId — skipping")
                    continue

                # Step 3 — Patch with ServicePrincipal credentials
                cred_data = json.dumps([
                    {"name": "tenantId",     "value": tenant_id},
                    {"name": "clientId",     "value": client_id},
                    {"name": "clientSecret", "value": client_secret},
                ])
                patch_url = (
                    f"https://api.powerbi.com/v1.0/myorg/gateways/{gateway_id}"
                    f"/datasources/{datasource_id}"
                )
                patch_body = {
                    "credentialDetails": {
                        "credentialType":      "ServicePrincipal",
                        "credentials":         cred_data,
                        "encryptedConnection": "Encrypted",
                        "encryptionAlgorithm": "None",
                        "privacyLevel":        "Organizational",
                    }
                }
                logger.info("[Credentials] Patching: %s", patch_url)
                patch_resp = requests.patch(
                    patch_url, headers=pbi_hdrs, json=patch_body, timeout=15
                )
                logger.info("[Credentials] Patch response: %d %s",
                            patch_resp.status_code, patch_resp.text[:300])

                if patch_resp.ok:
                    bound_count += 1
                    logger.info("[Credentials] Bound successfully for datasource %s", datasource_id)
                else:
                    logger.warning("[Credentials] Bind failed: %d %s",
                                   patch_resp.status_code, patch_resp.text[:300])

            if bound_count > 0:
                return {
                    "success": True,
                    "message": f"SharePoint credentials auto-bound ({bound_count} datasource(s))."
                }

            # Step 4 — Log full payload for debugging if nothing bound
            logger.info("[Credentials] Full datasource payload: %s", json.dumps(datasources, indent=2))
            return {
                "success": False,
                "message": (
                    "SharePoint datasource found but no gateway binding possible. "
                    "Please set credentials once manually: dataset settings → "
                    "Data source credentials → Edit → OAuth2 → Sign in with org account."
                )
            }

        except Exception as exc:
            logger.warning("[Credentials] Unexpected error: %s", exc)
            return {"success": False, "message": f"Credential bind error: {exc}"}

    def _poll(self, op_url: str, headers: Dict, max_wait: int = 120) -> str:
        logger.info("[Fabric API] Polling: %s", op_url)
        for i in range(max_wait // 3):
            time.sleep(3)
            try:
                r = requests.get(op_url, headers=headers, timeout=15)
                if r.ok:
                    body = r.json()
                    status = body.get("status", "")
                    logger.info("[Fabric API] Poll %d: %s", i + 1, status)
                    if status == "Succeeded":
                        logger.info("[Fabric API] Full success body: %s", json.dumps(body))
                        return "SUCCEEDED_NO_ID"
                    if status in ("Failed", "Cancelled"):
                        logger.warning("[Fabric API] Op %s: %s", status, body)
                        return ""
            except Exception as ex:
                logger.warning("[Fabric API] Poll error: %s", ex)
        logger.warning("[Fabric API] Polling timed out after %ds", max_wait)
        return ""

    def _find_dataset_id(self, dataset_name: str, headers: Dict) -> str:
        try:
            url = f"https://api.fabric.microsoft.com/v1/workspaces/{self.workspace_id}/semanticModels"
            r = requests.get(url, headers=headers, timeout=15)
            if r.ok:
                items = r.json().get("value", [])
                for item in items:
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
                fields = t.get("fields", [])
                if fields:
                    cols = [
                        {"name": f["name"], "dataType": _tabular_type(f.get("type", "string"))}
                        for f in fields
                    ]
                else:
                    cols = [{"name": "Value", "dataType": "string"}]
                tables_payload.append({"name": t["name"], "columns": cols})

            payload = {
                "name": dataset_name,
                "defaultMode": "Push",
                "tables": tables_payload,
            }
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets"
            resp = requests.post(url, headers=self.pbi_headers, json=payload, timeout=30)

            if resp.status_code in (200, 201, 202):
                dataset_id = resp.json().get("id", "")
                return {
                    "success": True,
                    "method": "push_dataset_fallback",
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "workspace_url": f"https://app.powerbi.com/groups/{self.workspace_id}",
                    "message": (
                        "Created via Push dataset fallback. "
                        "Fabric API failed - no M Query or Model View."
                    ),
                }
            return {
                "success": False,
                "error": f"Push dataset failed: {resp.status_code} {resp.text[:300]}",
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