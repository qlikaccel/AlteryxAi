"""
stage1_qlik_extractor.py
─────────────────────────
Stage 1 of the 6-stage pipeline: Extract table definitions from a Qlik app.

Replaces / wraps the Qlik Cloud API call in SixStageOrchestrator._stage_1_extract().

Two extraction paths:
  A) Qlik Cloud REST API  — fetches the live script from a Qlik Sense SaaS app
  B) Local script text    — parses a raw .qvs / .txt load script directly

After extraction, the raw Qlik script is:
  1. Parsed  by QlikScriptParser → list of table dicts (source_type, fields, …)
  2. Converted by MQueryConverter → M expressions per table
  3. Merged into the table dicts so the BIM builder can use real M code

Usage in SixStageOrchestrator:

    from stage1_qlik_extractor import extract_tables_from_app, extract_tables_from_script

    # Via Qlik Cloud:
    tables = extract_tables_from_app(app_id, qlik_token, qlik_tenant_url)

    # Via raw script:
    tables = extract_tables_from_script(script_text, base_path="C:/Data")
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests

from qlik_script_parser import parse_qlik_script
from mquery_converter import convert_to_mquery

logger = logging.getLogger(__name__)

# Qlik Cloud tenant URL from env (e.g. https://your-tenant.us.qlikcloud.com)
QLIK_TENANT_URL = os.getenv("QLIK_TENANT_URL", "").rstrip("/")
QLIK_API_KEY    = os.getenv("QLIK_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_tables_from_app(
    app_id: str,
    api_key: Optional[str] = None,
    tenant_url: Optional[str] = None,
    base_path: str = "[DataSourcePath]",
    connection_string: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the load script from Qlik Cloud and extract + convert table definitions.

    Returns:
        {
          "success": bool,
          "app_name": str,
          "tables": [ table_dict, … ],   # enriched with m_expression
          "error": str | None,
        }
    """
    key  = api_key    or QLIK_API_KEY
    base = tenant_url or QLIK_TENANT_URL

    if not key or not base:
        return {
            "success": False,
            "error":   "QLIK_TENANT_URL and QLIK_API_KEY must be set in .env or passed explicitly.",
        }

    # 1. Get app metadata
    app_name = _fetch_app_name(app_id, key, base)

    # 2. Fetch the load script
    script, err = _fetch_load_script(app_id, key, base)
    if err:
        return {"success": False, "error": err, "app_name": app_name}

    # 3. Parse + convert
    tables = _parse_and_convert(script, base_path, connection_string)

    logger.info(
        "[Stage1] App '%s' (%s): %d table(s) extracted", app_name, app_id, len(tables)
    )
    return {
        "success":  True,
        "app_id":   app_id,
        "app_name": app_name,
        "tables":   tables,
    }


def extract_tables_from_script(
    script_text: str,
    base_path: str = "[DataSourcePath]",
    connection_string: Optional[str] = None,
    app_name: str = "LocalScript",
) -> Dict[str, Any]:
    """
    Parse a raw Qlik load script string and return table definitions with M expressions.
    Useful for local / offline conversion without a Qlik Cloud connection.
    """
    tables = _parse_and_convert(script_text, base_path, connection_string)
    return {
        "success":  True,
        "app_name": app_name,
        "tables":   tables,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_app_name(app_id: str, api_key: str, base_url: str) -> str:
    try:
        resp = requests.get(
            f"{base_url}/api/v1/apps/{app_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.ok:
            return resp.json().get("attributes", {}).get("name", app_id)
    except Exception as e:
        logger.warning("[Stage1] Could not fetch app name: %s", e)
    return app_id


def _fetch_load_script(
    app_id: str, api_key: str, base_url: str
) -> tuple[str, Optional[str]]:
    """
    Fetch the QlikSense load script via:
      GET /api/v1/apps/{appId}/script
    Returns (script_text, error_message_or_None).
    """
    url = f"{base_url}/api/v1/apps/{app_id}/script"
    try:
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            # Qlik Cloud returns either {"script": "..."} or a list of script sections
            if isinstance(data, dict) and "script" in data:
                return data["script"], None
            if isinstance(data, list):
                # Multiple sections — join them
                sections = [s.get("script", "") for s in data if isinstance(s, dict)]
                return "\n\n".join(sections), None
            return str(data), None
        return "", f"Qlik API returned {resp.status_code}: {resp.text[:300]}"
    except Exception as e:
        return "", f"Request error fetching script: {e}"


def _parse_and_convert(
    script: str,
    base_path: str,
    connection_string: Optional[str],
) -> List[Dict[str, Any]]:
    """
    Run QlikScriptParser → MQueryConverter and merge results.

    Each returned dict has the standard table shape expected by the BIM builder
    PLUS an `m_expression` key with ready-to-use Power Query M code.
    """
    parsed    = parse_qlik_script(script)
    converted = convert_to_mquery(parsed, base_path, connection_string)

    # Merge: add m_expression + conversion notes back into the parsed dicts
    name_to_conv = {c["name"]: c for c in converted}
    result = []
    for table in parsed:
        name  = table["name"]
        conv  = name_to_conv.get(name, {})
        merged = {
            **table,
            "m_expression": conv.get("m_expression", ""),
            "conversion_notes": conv.get("notes", ""),
        }
        result.append(merged)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# BIM partition builder  (used by powerbi_xmla_connector._build_bim)
# ─────────────────────────────────────────────────────────────────────────────

def build_partition_from_table(table: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the BIM partition dict for a table that has an m_expression.
    Falls back to an empty typed table if no M expression is available.
    """
    name    = table["name"]
    m_expr  = table.get("m_expression", "")
    fields  = table.get("fields", [])

    if not m_expr:
        # Fallback: schema-only empty table
        type_defs = ", ".join(
            f"{_col_name_for_m(f.get('alias') or f['name'])} = type text"
            for f in fields if f.get("name") not in ("*", "")
        ) or "Column1 = type text"
        m_expr = (
            "let\n"
            f"    Source = #table(type table [{type_defs}], {{}})\n"
            "in\n"
            "    Source"
        )

    return {
        "name": f"{name}_Partition",
        "mode": "import",
        "source": {
            "type":       "m",
            "expression": m_expr,
        },
    }


def _col_name_for_m(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_ ]*$", name):
        return name
    return f'#"{name}"'
