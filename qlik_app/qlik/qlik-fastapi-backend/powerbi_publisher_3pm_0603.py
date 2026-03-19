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
    """
    Publish tables as a Power BI semantic model.

    Each item in tables_m must have:
        name         - table name
        m_expression - full M Query (let ... in ...)
        source_type  - 'inline' | 'csv' | 'qvd' | 'sql' | 'resident'
        fields       - list of {name, type} dicts  <- used to build columns in BIM
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
        dataset_name, tables_m, relationships, data_source_path
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def _acquire_sp_token(
    scope: str = "https://analysis.windows.net/powerbi/api/.default",
) -> str:
    """Acquire token via Service Principal (client credentials)."""
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
    return _QLIK_TO_TABULAR.get((qlik_type or "").lower(), "string")


def _infer_type_from_name(name: str) -> str:
    """Infer type from column name heuristics."""
    n = name.lower().strip()
    if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
        return "date"
    if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
        return "number"
    if any(x in n for x in ["id", "count", "qty", "quantity", "year", "month", "day", "num", "age", "rank", "km", "tons", "knots", "cc", "speed"]):
        return "integer"
    return "string"


# def _extract_fields_from_m(expr: str) -> list:
#     """Extract column names and types from #table() type signature in M expression."""
#     match = re.search(r"type\s+table\s+\[(.+?)\]", expr, re.DOTALL)
#     if not match:
#         logger.warning("[BIM] Could not extract fields from M expression")
#         return []
#     cols_str = match.group(1)
#     type_map = {
#         "text":       "string",
#         "number":     "number",
#         "date":       "date",
#         "datetime":   "datetime",
#         "logical":    "boolean",
#         "Int64.Type": "integer",
#     }
#     fields = []
#     for part in cols_str.split(","):
#         part = part.strip()
#         if "=" not in part:
#             continue
#         col_name = part.split("=")[0].strip().strip('#').strip('"')
#         col_type_raw = part.split("=")[1].strip()
#         mapped_type = type_map.get(col_type_raw, "string")
#         if mapped_type == "string":
#             mapped_type = _infer_type_from_name(col_name)
#         fields.append({"name": col_name, "type": mapped_type})
#     logger.info("[BIM] Extracted %d fields: %s", len(fields), [f["name"] for f in fields])
#     return fields if fields else []
def _extract_fields_from_m(expr: str) -> list:
    """
    Extract column names and types from an M expression.

    Handles three patterns produced by mquery_converter:

    Pattern A — Table.TransformColumnTypes (most common from CSV pipelines):
        Table.TransformColumnTypes(
            PromotedHeaders,
            {
                {"ColName", type text},
                {"OtherCol", type number},
                ...
            }
        )

    Pattern B — #table() type-table signature:
        #table(
            type table [ColName = text, OtherCol = number, ...],
            { ... }
        )

    Pattern C — PromoteHeaders only (no explicit types — infer from names):
        Table.PromoteHeaders(Source, ...)
        — extracts column names from any quoted string pairs in the expression
          and falls back to name-based type inference.
    """
    import re

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

    # ── Pattern A: Table.TransformColumnTypes ─────────────────────────────
    # Matches every {"ColumnName", type xxx} or {"ColumnName", Int64.Type} pair
    # inside the TransformColumnTypes call.
    transform_block = re.search(
        r"Table\.TransformColumnTypes\s*\(.*?,\s*\{(.*?)\}\s*\)",
        expr,
        re.DOTALL,
    )
    if transform_block:
        block = transform_block.group(1)
        # Each entry looks like: {"ColName", type text}
        for entry in re.finditer(
            r'\{\s*"([^"]+)"\s*,\s*(type\s+\w+|Int64\.Type|\w+(?:\.\w+)*)\s*\}',
            block,
        ):
            col_name = entry.group(1).strip()
            col_type_raw = entry.group(2).strip()
            col_type = M_TYPE_MAP.get(col_type_raw, M_TYPE_MAP.get(col_type_raw.lower(), "string"))
            if col_name:
                fields.append({"name": col_name, "type": col_type})

        if fields:
            logger.info(
                "[BIM] Pattern A (TransformColumnTypes): extracted %d fields: %s",
                len(fields), [f["name"] for f in fields],
            )
            return fields

    # ── Pattern B: #table(type table [...]) ───────────────────────────────
    type_table_match = re.search(r"type\s+table\s+\[(.+?)\]", expr, re.DOTALL)
    if type_table_match:
        cols_str = type_table_match.group(1)
        for part in cols_str.split(","):
            part = part.strip()
            if "=" not in part:
                continue
            col_name = part.split("=")[0].strip().lstrip("#").strip('"').strip("'")
            col_type_raw = part.split("=")[1].strip()
            col_type = M_TYPE_MAP.get(col_type_raw, "string")
            if col_name:
                fields.append({"name": col_name, "type": col_type})

        if fields:
            logger.info(
                "[BIM] Pattern B (#table type table): extracted %d fields: %s",
                len(fields), [f["name"] for f in fields],
            )
            return fields

    # ── Pattern C: Fallback — grab all quoted strings from TransformColumnTypes
    #    block or any column-like quoted strings in the expression.
    # This handles edge cases where the type annotation is missing.
    quoted_in_transform = re.search(
        r"Table\.TransformColumnTypes\s*\(.*?\{(.*?)\}\s*\)",
        expr,
        re.DOTALL,
    )
    search_area = quoted_in_transform.group(1) if quoted_in_transform else expr
    for col_name in re.findall(r'"([^"]+)"', search_area):
        # Skip values that look like M keywords or type names
        if col_name.lower() in ("csv", "delimiter", "encoding", "quotestyle",
                                "promoteallscalars", "true", "false"):
            continue
        col_type = _infer_type_from_name(col_name)
        fields.append({"name": col_name, "type": col_type})

    if fields:
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for f in fields:
            if f["name"] not in seen:
                seen.add(f["name"])
                deduped.append(f)
        fields = deduped
        logger.info(
            "[BIM] Pattern C (fallback quoted strings): extracted %d fields: %s",
            len(fields), [f["name"] for f in fields],
        )
        return fields

    logger.warning("[BIM] Could not extract fields from M expression")
    return []

def _fix_multiline_rows(expr: str) -> str:
    """
    Ensure every data row in a #table() M expression is on a single line.
    Rows start with { and end with } or },
    Any continuation lines are joined onto the opening line.
    """
    lines = expr.split("\n")
    result = []
    in_row = False
    current_row = ""

    for line in lines:
        stripped = line.strip()

        if in_row:
            current_row += " " + stripped
            # Row complete when closing brace found (with or without trailing comma)
            if re.search(r'\}\s*,?\s*$', stripped):
                result.append(current_row)
                current_row = ""
                in_row = False
        else:
            if stripped.startswith('{"') or stripped.startswith("{'"):
                if re.search(r'\}\s*,?\s*$', stripped):
                    # Complete row on one line
                    result.append(line)
                else:
                    # Incomplete row - start buffering
                    in_row = True
                    current_row = line.rstrip()
            else:
                result.append(line)

    if current_row:
        result.append(current_row)

    return "\n".join(result)


def _sanitize_m(expr: str) -> str:
    """M expressions from mquery_converter are already valid - return as-is."""
    return expr


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

    # -- main entry -----------------------------------------------------------

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

    # -- BIM builder ----------------------------------------------------------

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
            logger.info("[BIM] Table '%s' fields: %s", t["name"], fields)
            if not fields:
                fields = _extract_fields_from_m(expr)
                logger.info("[BIM] Extracted fields for '%s': %s", t["name"], fields)

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
        seen_table_pairs = set()
        for r in relationships:
            ft = r.get("fromTable") or r.get("from_table", "")
            fc = r.get("fromColumn") or r.get("from_column", "")
            tt = r.get("toTable")   or r.get("to_table", "")
            tc = r.get("toColumn")  or r.get("to_column", "")
            if ft and fc and tt and tc:
                pair = tuple(sorted([ft, tt]))
                if pair in seen_table_pairs:
                    continue
                seen_table_pairs.add(pair)
                # Fix: crossFilteringBehavior BIM valid values are
                # "oneDirection" and "bothDirections" (NOT "Single"/"Both")
                cf = r.get("crossFilteringBehavior") or r.get("cross_filter_direction", "")
                cf_bim = "bothDirections" if cf in ("Both", "bothDirections", "both") else "oneDirection"
                tmd_rels.append({
                    "name": f"{ft}_{fc}_{tt}_{tc}",
                    "fromTable": ft, "fromColumn": fc,
                    "toTable": tt,   "toColumn": tc,
                    "crossFilteringBehavior": cf_bim,
                })

        # ── DataSourcePath parameter ─────────────────────────────────────────
        # ALWAYS declare DataSourcePath as a proper M parameter in the BIM so
        # Fabric/Power BI Service treats it as a static bound source (not a
        # dynamic/unknown source that blocks refresh).
        #
        # The critical annotations are:
        #   IsParameterQuery = "True"   → marks it as a Query Parameter
        #   IsParameterQueryRequired = "False" → not required (has a default)
        #
        # Without these, [DataSourcePath] in M expressions is treated as an
        # unresolvable dynamic reference and triggers the
        # "dynamic data source" refresh error.
        param_value = data_source_path if data_source_path else ""
        expressions = [
            {
                "name": "DataSourcePath",
                "kind": "m",
                # The expression must be a quoted string literal —
                # this becomes the default/current value of the parameter.
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

    # -- Strategy 1: Fabric Items API -----------------------------------------

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
                    dataset_id = dataset_id or polled_id
                else:
                    dataset_id = (resp.json() if resp.text.strip() else {}).get("id", "")

                if dataset_id == "SUCCEEDED_NO_ID":
                    dataset_id = self._find_dataset_id(dataset_name, headers)
                    logger.info("[Fabric API] Looked up dataset ID: %s", dataset_id)

                if dataset_id:
                    logger.info("[Fabric API] Created: %s", dataset_id)

                    # Auto-bind SharePoint credentials so refresh works
                    # immediately without any manual portal steps.
                    # cred_result = self._set_sharepoint_credentials(
                    #     dataset_id, data_source_path, headers
                    # )
                    # cred_msg = cred_result.get("message", "")
                    is_sharepoint = any(d in (data_source_path or "").lower() 
                                       for d in ("sharepoint.com", "sharepoint-df.com"))
                    if is_sharepoint:
                        import time
                        time.sleep(10)  # Wait for dataset to propagate to Power BI REST API
                        cred_result = self._set_sharepoint_credentials(
                            dataset_id, data_source_path, headers
                        )
                        cred_msg = cred_result.get("message", "")
                    else:
                        cred_result = {"success": True}
                        cred_msg = ""
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
                        "credentials_bound": cred_result.get("success", False),
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

    def _set_sharepoint_credentials(
        self,
        dataset_id: str,
        sharepoint_url: str,
        fabric_headers: Dict,
    ) -> Dict[str, Any]:
        """
        After publishing, automatically bind Service Principal credentials to
        the SharePoint datasource on the dataset so that scheduled/manual
        refresh works without any manual portal intervention.

        Uses the Power BI REST API (not Fabric API) to:
          1. List datasources on the dataset
          2. Find the SharePoint datasource
          3. PATCH its credentials using the SP's client_id + client_secret
             (ServicePrincipal credential type)

        Requirements:
          - The SP must have at least read access to the SharePoint site.
          - If the tenant has not granted Sites.ReadAll, the credential binding
            will succeed here but refresh will fail with a 403 from SharePoint.
            In that case, grant Sites.ReadAll in Azure AD (Step 1 in the docs).

        Falls back gracefully — a failure here never blocks the publish result.
        """
        try:
            client_id     = os.getenv("POWERBI_CLIENT_ID", "")
            client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
            tenant_id     = os.getenv("POWERBI_TENANT_ID", "")

            if not all([client_id, client_secret, tenant_id]):
                logger.warning("[Credentials] SP env vars missing — skipping credential bind")
                return {"success": False, "message": "Credentials not bound (SP env vars missing)."}

            # Power BI REST API uses a different token scope than Fabric API
            pbi_token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
            if not pbi_token:
                logger.warning("[Credentials] Could not acquire PBI token for credential bind")
                return {"success": False, "message": "Credentials not bound (token error)."}

            pbi_headers = {
                "Authorization": f"Bearer {pbi_token}",
                "Content-Type": "application/json",
            }

            # Step 1 — Get datasources for the dataset
            # Uses myorg endpoint (workspace-scoped datasets work via myorg too)
            ds_url = (
                f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}"
                f"/datasets/{dataset_id}/datasources"
            )
            logger.info("[Credentials] Fetching datasources: %s", ds_url)
            ds_resp = requests.get(ds_url, headers=pbi_headers, timeout=15)

            if not ds_resp.ok:
                logger.warning(
                    "[Credentials] Could not list datasources: %d %s",
                    ds_resp.status_code, ds_resp.text[:200]
                )
                return {
                    "success": False,
                    "message": "Credentials not bound (datasource list failed — "
                               "you may need to set them manually once in the portal)."
                }

            datasources = ds_resp.json().get("value", [])
            logger.info("[Credentials] Found %d datasource(s)", len(datasources))

            bound_count = 0
            for ds in datasources:
                ds_type = ds.get("datasourceType", "").lower()
                if "sharepoint" not in ds_type:
                    continue

                gateway_id    = ds.get("gatewayId", "")
                datasource_id = ds.get("datasourceId", "")

                if not gateway_id or not datasource_id:
                    logger.warning("[Credentials] Missing gatewayId/datasourceId for SP datasource")
                    continue

                logger.info(
                    "[Credentials] Binding SP credentials to datasource %s (gateway %s)",
                    datasource_id, gateway_id
                )

                # Step 2 — Patch credentials using ServicePrincipal credential type
                # credentialData carries the SP tenantId + clientId + clientSecret
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
                        "credentialType":        "ServicePrincipal",
                        "credentials":           cred_data,
                        "encryptedConnection":   "Encrypted",
                        "encryptionAlgorithm":   "None",
                        "privacyLevel":          "Organizational",
                    }
                }
                patch_resp = requests.patch(
                    patch_url, headers=pbi_headers, json=patch_body, timeout=15
                )
                if patch_resp.ok:
                    logger.info(
                        "[Credentials] SP credentials bound successfully for datasource %s",
                        datasource_id
                    )
                    bound_count += 1
                else:
                    logger.warning(
                        "[Credentials] Credential bind failed: %d %s",
                        patch_resp.status_code, patch_resp.text[:300]
                    )

            if bound_count > 0:
                return {
                    "success": True,
                    "message": f"SharePoint credentials auto-bound ({bound_count} datasource(s)). "
                               "Refresh should work without manual portal steps."
                }
            else:
                return {
                    "success": False,
                    "message": (
                        "SharePoint datasource found but credential bind failed. "
                        "Please set credentials manually once in the dataset settings portal, "
                        "or grant Sites.ReadAll to the Service Principal in Azure AD."
                    )
                }

        except Exception as exc:
            logger.warning("[Credentials] Unexpected error during credential bind: %s", exc)
            return {
                "success": False,
                "message": "Credentials not bound (unexpected error — see logs)."
            }

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
        """Look up a semantic model by name in the workspace."""
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

    # -- Strategy 2: Push Dataset (fallback) ----------------------------------

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
