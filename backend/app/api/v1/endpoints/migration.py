

"""
Migration API -- FastAPI router for the 6-stage Qlik-to-Power BI pipeline.

CHANGES FROM ORIGINAL
─────────────────────
  ✅ Fix C: publish_mquery_endpoint now scans tables_m for APPLYMAP fields
            and auto-injects standalone dimension table M queries into tables_m
            before publishing.  This replaces the fragile inline SharePoint
            lookup pattern and lets Power BI relationships handle the join.

  ✅ Fix D: _infer_relationships_from_tables() extended to include
            ApplyMap dimension tables (injected above) as relationship targets.

  All other endpoints and logic unchanged from original.
"""

import logging
import os
import re
from collections import Counter
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel as _BaseModel

from app.services.powerbi_publisher import _acquire_sp_token
from app.services.six_stage_orchestrator import SixStageOrchestrator, run_migration_pipeline
from app.services.relationship_service import (
    _sanitize_col_name,
    infer_relationships_unified,
    sanitize_rel_columns,
    build_col_name_map_for_tables_m,
    normalize_table_rows,
    resolve_relationships_unified,
)
from app.services.alteryx_transform_plan import transform_publish_blocker_detail

# ─────────────────────────────────────────────────────────────────────────────
# QLIK SYSTEM TABLE FILTER
# ─────────────────────────────────────────────────────────────────────────────
QLIK_SYSTEM_PREFIXES = (
    "__",
    "_",
    "AutoCalendar",
    "MasterCalendar",
    "GeoData",
    "MapData",
    "TempTable",
    "Temp_",
    "_Temp",
)

def _is_system_table(table_name: str) -> bool:
    for prefix in QLIK_SYSTEM_PREFIXES:
        if table_name.startswith(prefix):
            return True
    return False


def _is_applymap_dimension_table(table_obj: Dict[str, Any]) -> bool:
    """Return True for helper tables generated from ApplyMap lookup mappings."""
    opts = table_obj.get("options") if isinstance(table_obj, dict) else None
    return isinstance(opts, dict) and bool(opts.get("is_applymap_dimension"))


def _field_display_name(field: Any) -> str:
    if isinstance(field, dict):
        return str(field.get("alias") or field.get("name") or field.get("field") or "").strip()
    return str(field or "").strip()


def _publish_table_schema(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    published_tables: List[Dict[str, Any]] = []
    for table in tables_m or []:
        table_name = str(table.get("name") or "").strip()
        if not table_name:
            continue
        columns: List[str] = []
        seen = set()
        for field in table.get("fields") or []:
            column_name = _field_display_name(field)
            key = column_name.lower()
            if column_name and column_name != "*" and key not in seen:
                seen.add(key)
                columns.append(column_name)
        published_tables.append({"name": table_name, "columns": columns})
    return published_tables


def _parse_combined_mquery_fallback(combined_m: str) -> List[Dict[str, Any]]:
    """
    Parse simple combined Power Query sections without the legacy pbit_generator module.

    Supports sections like:
      TableName =
      let
        ...
      in
        FinalStep
    """
    text = (combined_m or "").strip()
    if not text:
        return []

    # Only split on non-indented top-level table definitions. Alteryx-generated
    # M commonly contains internal steps such as "    SelectedFields_2 = let ...";
    # those are not publishable tables and must remain inside the parent query.
    pattern = re.compile(
        r'(?ms)^((?:#?"[^"]+")|(?:[A-Za-z_][\w .\-]*))\s*=\s*(let\b.*?)(?=^(?:(?:#?"[^"]+")|(?:[A-Za-z_][\w .\-]*))\s*=\s*let\b|\Z)'
    )
    tables: List[Dict[str, Any]] = []
    for match in pattern.finditer(text):
        raw_name = match.group(1).strip()
        name = raw_name.lstrip("#").strip('"').strip()
        expr = match.group(2).strip()
        lowered = expr.lower()
        if "csv.document" in lowered or "sharepoint.files" in lowered:
            source_type = "csv"
        elif "excel.workbook" in lowered:
            source_type = "excel"
        elif "odbc.datasource" in lowered or "sql.database" in lowered:
            source_type = "database"
        elif "web.contents" in lowered or "json.document" in lowered:
            source_type = "api"
        else:
            source_type = "unknown"
        tables.append({
            "name": name,
            "source_type": source_type,
            "m_expression": expr,
            "fields": [],
            "options": {},
            "source_path": "",
        })

    if tables:
        return tables

    # Last-resort single table if the caller sent a bare let/in expression.
    if text.lower().startswith("let"):
        return [{
            "name": "AlteryxOutput",
            "source_type": "unknown",
            "m_expression": text,
            "fields": [],
            "options": {},
            "source_path": "",
        }]

    return []


def _extract_source_file_name(expr: str) -> str:
    if not expr:
        return ""
    match = re.search(r'\[Name\]\s*=\s*"([^"]+)"', expr)
    if match:
        return os.path.splitext(match.group(1).strip())[0].lower()
    return ""

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/migration", tags=["Migration"])

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _orchestrator() -> SixStageOrchestrator:
    return SixStageOrchestrator()


# ---------------------------------------------------------------------------
# POST /publish-table
# ---------------------------------------------------------------------------

@router.post("/publish-table")
async def publish_table(
    app_id:       str = Query(..., description="Qlik Cloud app ID"),
    dataset_name: str = Query(..., description="Target Power BI dataset / semantic model name"),
    workspace_id: str = Query(..., description="Power BI workspace GUID"),
    access_token: Optional[str] = Query(None, description="Azure AD bearer token"),
    publish_mode: str = Query(
        "xmla_semantic",
        description=(
            "Deployment mode: "
            "'xmla_semantic' (PPU -- full semantic model, ER Diagram visible), "
            "'cloud_push' (REST Push dataset -- limited Model View), "
            "'desktop_cloud' (bundle only, no Power BI write)"
        ),
    ),
):
    if not app_id:
        raise HTTPException(400, "app_id is required")
    if not dataset_name:
        raise HTTPException(400, "dataset_name is required")
    if not workspace_id:
        raise HTTPException(400, "workspace_id is required")

    if publish_mode == "xmla_semantic" and not access_token:
        raise HTTPException(
            400,
            "access_token is required for publish_mode=xmla_semantic. "
            "Obtain one via /powerbi/login/acquire-token.",
        )

    logger.info(
        "[API] publish-table: app_id=%s  dataset=%s  workspace=%s  mode=%s",
        app_id, dataset_name, workspace_id, publish_mode,
    )

    result = run_migration_pipeline(
        app_id=app_id,
        dataset_name=dataset_name,
        workspace_id=workspace_id,
        access_token=access_token,
        publish_mode=publish_mode,
    )

    summary_result = {k: v for k, v in result.items() if k != "er_diagram_html"}
    summary_result["er_diagram_available"] = bool(result.get("er_diagram_html"))
    summary_result["er_diagram_endpoint"]  = (
        f"/api/migration/er-diagram-html?app_id={app_id}&dataset_name={dataset_name}"
    )
    return summary_result


# ---------------------------------------------------------------------------
# POST /preview-migration
# ---------------------------------------------------------------------------

@router.post("/preview-migration")
async def preview_migration(
    app_id:       str = Query(..., description="Qlik Cloud app ID"),
    dataset_name: str = Query("Preview", description="Dataset name (for labelling only)"),
):
    if not app_id:
        raise HTTPException(400, "app_id is required")

    logger.info("[API] preview-migration: app_id=%s", app_id)

    orchestrator = _orchestrator()
    stage1 = orchestrator._stage_1_extract(app_id)
    if not stage1.get("success"):
        raise HTTPException(400, f"Stage 1 failed: {stage1.get('error', 'unknown')}")

    tables    = stage1.get("tables", [])
    stage2    = orchestrator._stage_2_infer(tables)
    inferred  = stage2.get("relationships", [])
    stage3    = orchestrator._stage_3_normalize(tables, inferred)
    normalized = stage3.get("relationships", [])
    stage6    = orchestrator._stage_6_er_diagram(tables, normalized)

    return {
        "success":       True,
        "app_id":        app_id,
        "app_name":      stage1.get("app_name"),
        "dataset_name":  dataset_name,
        "tables":        tables,
        "relationships": normalized,
        "summary": {
            "table_count":        len(tables),
            "relationship_count": len(normalized),
            "avg_confidence":     stage2.get("avg_confidence", 0),
        },
        "er_diagram": {
            "mermaid": stage6.get("mermaid", ""),
            "html":    stage6.get("html", ""),
        },
        "note": "Preview only -- no Power BI changes made.",
    }


# ---------------------------------------------------------------------------
# GET /view-diagram
# ---------------------------------------------------------------------------

@router.get("/view-diagram")
async def view_diagram(
    app_id:       str = Query(..., description="Qlik Cloud app ID"),
    dataset_name: str = Query("Diagram", description="Label for diagram title"),
):
    if not app_id:
        raise HTTPException(400, "app_id is required")

    logger.info("[API] view-diagram: app_id=%s", app_id)

    orchestrator = _orchestrator()
    result = orchestrator.get_er_diagram_only(app_id)

    if not result.get("success"):
        raise HTTPException(500, result.get("error", "ER diagram generation failed"))

    return {
        "success":       True,
        "app_id":        app_id,
        "app_name":      result.get("app_name", ""),
        "tables":        result.get("tables", 0),
        "relationships": result.get("relationships", 0),
        "er_diagram": {
            "mermaid":           result.get("mermaid", ""),
            "html":              result.get("html", ""),
            "iframe_endpoint":   f"/api/migration/er-diagram-html?app_id={app_id}&dataset_name={dataset_name}",
        },
    }


# ---------------------------------------------------------------------------
# GET /er-diagram-html
# ---------------------------------------------------------------------------

@router.get("/er-diagram-html", response_class=HTMLResponse)
async def er_diagram_html(
    app_id:       str = Query(..., description="Qlik Cloud app ID"),
    dataset_name: str = Query("ER Diagram", description="Title shown in diagram"),
):
    if not app_id:
        raise HTTPException(400, "app_id is required")

    logger.info("[API] er-diagram-html: app_id=%s", app_id)

    orchestrator = _orchestrator()
    result = orchestrator.get_er_diagram_only(app_id)

    if not result.get("success"):
        return HTMLResponse(
            content=f"""<!DOCTYPE html><html><body>
            <h3 style="color:red">ER Diagram Error</h3>
            <p>{result.get('error', 'Unknown error')}</p>
            </body></html>""",
            status_code=200,
        )

    from stage6_er_diagram import ERDiagramGenerator
    gen  = ERDiagramGenerator()
    html = gen.generate_html_diagram(
        result.get("mermaid", ""),
        title=f"{dataset_name} -- Entity Relationship Diagram",
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# GET /pipeline-help
# ---------------------------------------------------------------------------

@router.get("/pipeline-help")
async def pipeline_help():
    return {
        "title":    "Qlik-to-Power BI 6-Stage Migration Pipeline",
        "version":  "2.1.0",
        "ppu_note": (
            "For Power BI PPU workspaces, use publish_mode=xmla_semantic. "
            "This deploys a proper Tabular semantic model that shows ER Diagram "
            "and Model View in Power BI Service."
        ),
        "endpoints": {
            "POST /api/migration/publish-table": {
                "description": "Full 6-stage pipeline",
                "params": {
                    "app_id":       "Qlik Cloud app ID (required)",
                    "dataset_name": "Target Power BI semantic model name (required)",
                    "workspace_id": "Power BI workspace GUID (required)",
                    "access_token": "Azure AD bearer token (required for xmla_semantic / cloud_push)",
                    "publish_mode": "xmla_semantic | cloud_push | desktop_cloud  [default: xmla_semantic]",
                },
            },
            "POST /api/migration/preview-migration": {
                "description": "Stages 1-3 + ER diagram only -- no Power BI writes",
            },
            "GET /api/migration/view-diagram": {
                "description": "ER diagram JSON (Mermaid + HTML)",
            },
            "GET /api/migration/er-diagram-html": {
                "description": "Standalone HTML ER diagram for <iframe> embedding",
            },
        },
    }


@router.post("/publish-semantic-model")
async def publish_semantic_model_xmla(
    app_id: str = Form(...),
    dataset_name: str = Form(...),
    workspace_id: str = Form(...),
    csv_payload_json: Optional[str] = Form(None),
    access_token: Optional[str] = Form(None),
) -> Dict[str, Any]:
    try:
        csv_payloads: Dict[str, str] = {}
        if csv_payload_json:
            try:
                parsed = json.loads(csv_payload_json)
                if isinstance(parsed, dict):
                    csv_payloads = {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                pass

        if not access_token:
            try:
                from app.services.powerbi_auth import get_auth_manager
                auth = get_auth_manager()
                if auth.is_token_valid():
                    access_token = auth.get_access_token()
            except Exception:
                pass

        if not access_token:
            raise HTTPException(
                status_code=400,
                detail="No access token available. Please login via /powerbi/login/initiate first.",
            )

        orchestrator = SixStageOrchestrator()
        result = orchestrator.execute_pipeline(
            app_id=app_id,
            dataset_name=dataset_name,
            workspace_id=workspace_id,
            access_token=access_token,
            publish_mode="xmla_semantic",
            csv_table_payloads=csv_payloads,
        )

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Deployment failed"))

        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("publish-semantic-model failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/xmla-login/initiate")
async def xmla_login_initiate():
    from xmla_auth import initiate_xmla_login
    result = initiate_xmla_login()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/xmla-login/complete")
async def xmla_login_complete():
    from xmla_auth import complete_xmla_login
    return complete_xmla_login()


@router.get("/xmla-login/status")
async def xmla_login_status():
    from xmla_auth import get_xmla_login_status
    return get_xmla_login_status()


# ===========================================================================
# LOADSCRIPT -> M QUERY PIPELINE ENDPOINTS
# ===========================================================================

import json
import os

@router.post("/fetch-loadscript")
async def fetch_loadscript_endpoint(
    app_id:     str = Query(..., description="Qlik Cloud app ID"),
    table_name: str = Query("", description="Selected table name (optional)"),
    tenant_url: str = Query("", description="Qlik Cloud tenant URL (optional override)"),
):
    logger.info("[fetch_loadscript_endpoint] App ID: %s", app_id)

    try:
        from app.utils.loadscript_fetcher import LoadScriptFetcher
        fetcher = LoadScriptFetcher()
        conn_result = fetcher.test_connection()
        if conn_result.get("status") != "success":
            raise HTTPException(
                status_code=503,
                detail=f"Qlik Cloud connection failed: {conn_result.get('message', 'Unknown')}"
            )
        result = fetcher.fetch_loadscript(app_id)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[fetch_loadscript_endpoint] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/parse-loadscript")
async def parse_loadscript_endpoint(request: dict):
    loadscript = request.get("loadscript", "") if isinstance(request, dict) else str(request)
    logger.info("[parse_loadscript_endpoint] Script length: %d characters", len(loadscript))

    try:
        from app.utils.loadscript_parser import LoadScriptParser
        parser = LoadScriptParser(loadscript)
        result = parser.parse()
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[parse_loadscript_endpoint] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


class ConvertToMQueryRequest(_BaseModel):
    parsed_script_json: str = ""
    table_name:         str = ""
    base_path:          str = ""
    connection_string:  str = ""
    app_id:             str = ""


@router.post("/convert-to-mquery")
async def convert_to_mquery_endpoint(
    request: ConvertToMQueryRequest,
    parsed_script_json_q: str = Query("", alias="parsed_script_json"),
    table_name_q:         str = Query("", alias="table_name"),
    base_path_q:          str = Query("", alias="base_path"),
    connection_string_q:  str = Query("", alias="connection_string"),
    app_id_q:             str = Query("", alias="app_id"),
):
    _parsed_json    = request.parsed_script_json or parsed_script_json_q
    _table_name     = request.table_name         or table_name_q
    _base_path      = request.base_path or base_path_q or os.getenv("DATA_SOURCE_PATH", "[DataSourcePath]")
    _connection_str = request.connection_string  or connection_string_q
    _app_id         = request.app_id or app_id_q

    logger.info("[convert_to_mquery_endpoint] Table: %s  base_path: %s", _table_name or "(all)", _base_path)

    try:
        parse_result: Dict[str, Any] = json.loads(_parsed_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in parsed_script_json: {exc}")

    tables: List[Dict[str, Any]] = (
        parse_result.get("details", {}).get("tables", [])
        or parse_result.get("tables", [])
    )
    raw_script: str = parse_result.get("raw_script", "")

    # Extract qlik_fields_map if the parse_result already contains it
    # (populated by LoadScriptParser.parse(qlik_fields_map=...) or fetch_and_parse()).
    # This ensures LOAD * tables get real column names in M expressions even when
    # going through convert-to-mquery before publish.
    _qlik_fields_map: Dict[str, List[str]] = (
        parse_result.get("qlik_fields_map")
        or parse_result.get("details", {}).get("qlik_fields_map")
        or {}
    )
    if _qlik_fields_map:
        logger.info(
            "[convert_to_mquery_endpoint] qlik_fields_map found in parse_result: %d tables",
            len(_qlik_fields_map),
        )
    elif _app_id:
        # Final fallback for scripts flow: auto-fetch schema from Qlik data model.
        # This prevents LOAD * tables from becoming dynamic-only during conversion.
        _qlik_fields_map = _build_qlik_fields_map(_app_id)
        if _qlik_fields_map:
            logger.info(
                "[convert_to_mquery_endpoint] qlik_fields_map auto-fetched via app_id: %d tables",
                len(_qlik_fields_map),
            )

    if not tables and not raw_script:
        raise HTTPException(
            status_code=400,
            detail="No tables found in parsed_script_json. Re-run /parse-loadscript first."
        )

    try:
        from app.services.mquery_converter import MQueryConverter
        converter = MQueryConverter()
        all_table_names = {t["name"] for t in tables}

        if _table_name:
            target = next((t for t in tables if t["name"] == _table_name), None)
            if not target:
                target = next((t for t in tables if t["name"].lower() == _table_name.lower()), None)
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail=f"Table '{_table_name}' not found. Available: {sorted(all_table_names)}"
                )

            m_expr = converter.convert_one(
                target, base_path=_base_path,
                connection_string=_connection_str or None,
                all_table_names=all_table_names,
                qlik_fields_map=_qlik_fields_map or None,
            )

            dep_queries: Dict[str, str] = {}
            if target.get("source_type") == "resident":
                src_name = target.get("source_path", "")
                src_table = next((t for t in tables if t["name"] == src_name), None)
                if src_table:
                    dep_queries[src_name] = converter.convert_one(
                        src_table, base_path=_base_path,
                        connection_string=_connection_str or None,
                        all_table_names=all_table_names,
                        qlik_fields_map=_qlik_fields_map or None,
                    )

            return {
                "status":             "success",
                "table_name":         _table_name,
                "source_type":        target.get("source_type", "unknown"),
                "m_query":            m_expr,
                "query_length":       len(m_expr),
                "dependency_queries": dep_queries,
                "message":            f"M Query generated for '{_table_name}'.",
            }

        else:
            user_tables = [t for t in tables if not _is_system_table(t["name"])]
            filtered_count = len(tables) - len(user_tables)
            if filtered_count:
                logger.info("[convert_to_mquery_endpoint] Filtered %d system tables", filtered_count)

            all_converted = converter.convert_all(
                user_tables, base_path=_base_path,
                connection_string=_connection_str or None,
                qlik_fields_map=_qlik_fields_map or None,
            )

            parts = []
            for item in all_converted:
                parts.append(
                    f"// \n// Table: {item['name']}  [{item['source_type']}]\n// \n"
                    f"{item['m_expression']}"
                )
            combined_m = "\n\n".join(parts)
            resident_tables = [t for t in all_converted if t["source_type"] == "resident"]

            return {
                "status":       "success",
                "table_name":   "",
                "m_query":      combined_m,
                "query_length": len(combined_m),
                "all_tables":   all_converted,
                "message":      f"M Query generated for all {len(all_converted)} table(s).",
                "statistics": {
                    "total_tables_converted": len(all_converted),
                    "resident_tables":        len(resident_tables),
                },
            }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[convert_to_mquery_endpoint] Conversion failed")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")


@router.post("/full-pipeline")
async def full_pipeline(
    app_id:            str = Query(...),
    table_name:        str = Query(""),
    base_path:         str = Query("[DataSourcePath]"),
    connection_string: str = Query(""),
    auto_download:     bool = Query(False),
):
    logger.info("[full_pipeline] app_id=%s table=%s", app_id, table_name or "(all)")

    try:
        from app.utils.loadscript_fetcher import LoadScriptFetcher
        fetcher = LoadScriptFetcher()
        fetch_result = fetcher.fetch_loadscript(app_id)
        if fetch_result.get("status") not in ("success", "partial_success"):
            raise HTTPException(status_code=503, detail=f"Fetch failed: {fetch_result.get('message')}")
        loadscript = fetch_result.get("loadscript", "")

        from app.utils.loadscript_parser import LoadScriptParser
        parse_result = LoadScriptParser(loadscript).parse()
        tables = parse_result.get("details", {}).get("tables", [])

        from app.services.mquery_converter import MQueryConverter
        converter = MQueryConverter()
        all_table_names = {t["name"] for t in tables}

        if table_name:
            target = next((t for t in tables if t["name"] == table_name), None)
            if not target:
                raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found.")
            m_query = converter.convert_one(
                target, base_path=base_path,
                connection_string=connection_string or None,
                all_table_names=all_table_names,
            )
        else:
            all_converted = converter.convert_all(
                tables, base_path=base_path, connection_string=connection_string or None
            )
            m_query = "\n\n".join(
                f"// Table: {t['name']}\n{t['m_expression']}" for t in all_converted
            )

        return {
            "status":  "success",
            "app_id":  app_id,
            "m_query": m_query,
            "phases": {
                "fetch":   {"method": fetch_result.get("method"), "script_length": len(loadscript)},
                "parse":   {"tables_count": len(tables)},
                "convert": {"table_requested": table_name or "(all)", "query_length": len(m_query)},
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[full_pipeline] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Relationship inference helpers
# ─────────────────────────────────────────────────────────────────────────────

def _infer_relationships_from_tables(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Backward-compatible wrapper consumed by other modules.
    return infer_relationships_unified(tables_m, alias_aware=True)


def _is_alteryx_raw_source_table(table_name: str) -> bool:
    return str(table_name or "").lower().endswith("_raw")


def _should_disable_alteryx_relationship_inference(tables_m: List[Dict[str, Any]], app_id: str = "") -> bool:
    if app_id:
        return False
    raw_count = sum(1 for table in tables_m if _is_alteryx_raw_source_table(table.get("name", "")))
    return raw_count >= 1


# ─────────────────────────────────────────────────────────────────────────────
# FIX C helper: scan tables_m for APPLYMAP and inject dimension tables
# ─────────────────────────────────────────────────────────────────────────────

def _inject_applymap_dimension_tables(
    tables_m: List[Dict[str, Any]],
    base_path: str,
) -> List[Dict[str, Any]]:
    """
    FIX C — Scan all table fields for APPLYMAP expressions.
    For each unique map table referenced:
      1. Check if it already exists in tables_m (user may have added it manually)
      2. If not, generate a standalone dimension table M query via
         MQueryConverter.convert_applymap_to_dimension_table()
      3. Append it to tables_m so Fabric publishes it as a real query

    This replaces the fragile inline SharePoint lookup that was
    previously generated inside _m_resident_inlined().
    """
    from app.services.mquery_converter import MQueryConverter
    converter = MQueryConverter()

    existing_names = {t["name"] for t in tables_m}
    applymap_dimensions: Dict[str, str] = {}  # map_table_name → key_column

    for t in tables_m:
        for f in t.get("fields", []):
            expr = f.get("expression", "")
            if "APPLYMAP" not in expr.upper():
                continue
            # Parse: ApplyMap('MapTableName', key_column, 'Default')
            m = re.search(
                r"APPLYMAP\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*(\w+)",
                expr,
                re.IGNORECASE,
            )
            if m:
                map_table = m.group(1).strip()
                key_col = m.group(2).strip()
                if map_table not in applymap_dimensions:
                    applymap_dimensions[map_table] = key_col
                    logger.info(
                        "[_inject_applymap_dimension_tables] Found ApplyMap ref: '%s' key='%s' in table '%s'",
                        map_table, key_col, t["name"]
                    )

    injected = 0
    for map_table, key_col in applymap_dimensions.items():
        if map_table in existing_names:
            logger.info(
                "[_inject_applymap_dimension_tables] Dimension '%s' already in tables_m — skipping",
                map_table
            )
            continue

        dim_table = converter.convert_applymap_to_dimension_table(
            map_table_name=map_table,
            base_path=base_path,
            key_column=key_col,
        )
        tables_m.append(dim_table)
        existing_names.add(map_table)
        injected += 1
        logger.info(
            "[_inject_applymap_dimension_tables] Injected dimension table '%s' (key=%s)",
            map_table, key_col
        )

    if injected:
        logger.info(
            "[_inject_applymap_dimension_tables] Total injected: %d dimension table(s)", injected
        )

    return tables_m


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build qlik_fields_map from GetTablesAndKeys response
# ─────────────────────────────────────────────────────────────────────────────

def _build_qlik_fields_map(app_id: str) -> Dict[str, List[str]]:
    """
    Auto-fetch {table_name: [field_names]} from the Qlik data model.
    Uses GetTablesAndKeys via WebSocket — same call your app already makes.
    Returns empty dict on any failure (pipeline continues gracefully).
    """
    if not app_id:
        return {}
    try:
        from loadscript_fetcher import LoadScriptFetcher
        fetcher = LoadScriptFetcher()
        fields_map = fetcher.get_data_model_fields(app_id)
        if fields_map:
            logger.info(
                "[_build_qlik_fields_map] Auto-fetched %d tables from data model for app '%s'",
                len(fields_map), app_id
            )
        else:
            logger.warning(
                "[_build_qlik_fields_map] Could not auto-fetch fields map for app '%s'. "
                "LOAD * tables will use dynamic schema (columns appear after first refresh).",
                app_id
            )
        return fields_map
    except Exception as exc:
        logger.warning("[_build_qlik_fields_map] Failed: %s", exc)
        return {}


def _strip_qlik_qualifier(value: str) -> str:
    text = str(value or "").strip().strip("[]")
    if not text:
        return ""
    if "." in text and "-" not in text:
        text = text.split(".", 1)[-1]
    return text


def _normalize_tables_m_fields(
    tables_m: List[Dict[str, Any]],
    qlik_fields_map: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    normalized_tables: List[Dict[str, Any]] = []

    for table in tables_m:
        table_copy = dict(table)
        table_name = str(table_copy.get("name", "") or "")
        existing_fields = table_copy.get("fields") or []
        map_fields = _lookup_map_fields(table_name, qlik_fields_map)
        canonical_by_lower = {str(col).strip().lower(): str(col).strip() for col in map_fields if str(col).strip()}

        normalized_fields: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for field in existing_fields:
            if not isinstance(field, dict):
                continue
            raw_name = str(field.get("name") or "").strip()
            raw_alias = str(field.get("alias") or raw_name).strip()
            raw_expr = str(field.get("expression") or raw_name).strip()

            plain_name = _strip_qlik_qualifier(raw_name)
            plain_alias = _strip_qlik_qualifier(raw_alias)
            plain_expr = _strip_qlik_qualifier(raw_expr)
            canonical_name = canonical_by_lower.get(plain_alias.lower()) or canonical_by_lower.get(plain_name.lower()) or plain_alias or plain_name

            if not canonical_name or canonical_name == "*":
                continue

            normalized_field = dict(field)
            normalized_field["name"] = canonical_name
            normalized_field["alias"] = canonical_name

            expr_is_passthrough = plain_expr.lower() in {
                raw_name.lower().strip("[]"),
                raw_alias.lower().strip("[]"),
                plain_name.lower(),
                plain_alias.lower(),
                canonical_name.lower(),
            }
            if expr_is_passthrough:
                normalized_field["expression"] = canonical_name

            key = canonical_name.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized_fields.append(normalized_field)

        for map_field in map_fields:
            canonical_name = str(map_field).strip()
            if not canonical_name:
                continue
            key = canonical_name.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized_fields.append({
                "name": canonical_name,
                "alias": canonical_name,
                "expression": canonical_name,
                "type": "string",
                "extracted_from": "qlik_fields_map",
            })

        table_copy["fields"] = normalized_fields
        normalized_tables.append(table_copy)

    return normalized_tables


def _table_publish_quality(table: Dict[str, Any]) -> tuple[int, int, int, int]:
    expr = str(table.get("m_expression") or "")
    source_path = str(table.get("source_path") or "")
    combined = f"{expr} {source_path}".lower()
    has_sharepoint = int("sharepoint.files" in combined and "sharepoint.com" in combined)
    has_http = int("https://" in combined or "http://" in combined)
    avoids_pseudo = int('sharepoint.files("s://' not in combined and "sharepoint.files('s://" not in combined)
    field_count = len(table.get("fields") or [])
    return (has_sharepoint, has_http, avoids_pseudo, field_count)


def _dedupe_tables_m_by_name(tables_m: List[Dict[str, Any]], context: str = "") -> List[Dict[str, Any]]:
    """Power BI/Fabric rejects semantic models with duplicate table names."""
    selected: Dict[str, Dict[str, Any]] = {}
    original_names: Dict[str, str] = {}
    dropped: List[str] = []

    for table in tables_m:
        name = str(table.get("name") or "").strip()
        key = name.lower()
        if not key:
            continue
        existing = selected.get(key)
        if not existing:
            selected[key] = table
            original_names[key] = name
            continue

        if _table_publish_quality(table) > _table_publish_quality(existing):
            dropped.append(original_names.get(key, name))
            selected[key] = table
            original_names[key] = name
        else:
            dropped.append(name)

    if dropped:
        logger.warning(
            "[publish_mquery] Dropped %d duplicate table definition(s)%s: %s",
            len(dropped),
            f" during {context}" if context else "",
            ", ".join(dropped[:10]),
        )

    return list(selected.values())


def _fetch_table_rows_for_cardinality(app_id: str, table_name: str, limit: int = 5000) -> List[Dict[str, Any]]:
    if not app_id or not table_name:
        return []
    try:
        from app.services.qlik_websocket_client import QlikWebSocketClient

        client = QlikWebSocketClient()
        result = client.get_table_data(app_id, table_name, limit=limit)
        rows = result.get("rows", []) if isinstance(result, dict) else []
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        logger.warning("[_fetch_table_rows_for_cardinality] Failed for %s.%s: %s", app_id, table_name, exc)
        return []


def _column_is_unique(rows: List[Dict[str, Any]], column_name: str) -> Optional[bool]:
    if not rows or not column_name:
        return None

    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if column_name not in row:
            continue
        value = row.get(column_name)
        if value in (None, "", "-"):
            continue
        values.append(str(value))

    if not values:
        return None
    counts = Counter(values)
    return max(counts.values(), default=0) <= 1


def _apply_row_aware_cardinality_from_rows(
    relationships: List[Dict[str, Any]],
    table_rows_cache: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    if not relationships:
        return relationships

    adjusted: List[Dict[str, Any]] = []

    for rel in relationships:
        rel_out = dict(rel)
        from_table = str(rel_out.get("fromTable", "") or "")
        to_table = str(rel_out.get("toTable", "") or "")
        from_col = str(rel_out.get("fromColumn", "") or "")
        to_col = str(rel_out.get("toColumn", "") or "")

        if not all([from_table, to_table, from_col, to_col]):
            adjusted.append(rel_out)
            continue

        from_unique = _column_is_unique(table_rows_cache.get(from_table, []), from_col)
        to_unique = _column_is_unique(table_rows_cache.get(to_table, []), to_col)

        if from_unique is True and to_unique is False:
            rel_out["fromTable"] = to_table
            rel_out["fromColumn"] = to_col
            rel_out["toTable"] = from_table
            rel_out["toColumn"] = from_col
            rel_out["cardinality"] = "ManyToOne"
        elif from_unique is False and to_unique is True:
            rel_out["cardinality"] = "ManyToOne"
        elif from_unique is True and to_unique is True:
            rel_out["cardinality"] = "OneToOne"
        elif from_unique is False and to_unique is False:
            rel_out["cardinality"] = "ManyToMany"
            rel_out["crossFilteringBehavior"] = "Both"

        adjusted.append(rel_out)

    return adjusted


def _apply_row_aware_cardinality(
    relationships: List[Dict[str, Any]],
    app_id: str,
) -> List[Dict[str, Any]]:
    if not relationships or not app_id:
        return relationships

    table_rows_cache: Dict[str, List[Dict[str, Any]]] = {}

    for rel in relationships:
        from_table = str(rel.get("fromTable", "") or "")
        to_table = str(rel.get("toTable", "") or "")

        if from_table and from_table not in table_rows_cache:
            table_rows_cache[from_table] = _fetch_table_rows_for_cardinality(app_id, from_table)
        if to_table and to_table not in table_rows_cache:
            table_rows_cache[to_table] = _fetch_table_rows_for_cardinality(app_id, to_table)

    return _apply_row_aware_cardinality_from_rows(relationships, table_rows_cache)


# ===========================================================================
# PUBLISH M QUERY -> POWER BI
# ===========================================================================

class PublishMQueryRequest(_BaseModel):
    dataset_name:         str  = "Qlik_Migrated_Dataset"
    combined_mquery:      str  = ""
    raw_script:           str  = ""
    access_token:         str  = ""
    data_source_path:     str  = ""
    sharepoint_url:       str  = ""
    db_connection_string: str  = ""
    relationships:        list = []
    # ✅ NEW: pass app_id so we can auto-fetch qlik_fields_map from GetTablesAndKeys
    app_id:               str  = ""
    # ✅ NEW: caller can also supply the map directly (overrides auto-fetch)
    qlik_fields_map:      dict = {}
    # Alteryx CSV flow forwards mquery.source_fields_map here:
    # {"sales_1_raw": [{"name": "CustomerID", "type": "string"}, ...]}.
    # These entries must be applied by exact table name so each raw CSV table
    # keeps its own schema.
    alteryx_source_fields: dict = {}
    # Alteryx flow can pass canonical transformation coverage. When present,
    # Power BI publish follows the same parity gate as dbt/Dataform/Python.
    transformation_coverage: dict = {}
    transform_plan: dict = {}


def _allow_partial_transform_publish() -> bool:
    return str(os.getenv("ALLOW_PARTIAL_TRANSFORM_PUBLISH", "")).strip().lower() in {"1", "true", "yes", "y"}


def _enforce_transform_parity_gate() -> bool:
    return str(os.getenv("ENFORCE_TRANSFORM_PARITY_GATE", "")).strip().lower() in {"1", "true", "yes", "y"}


def _mquery_transform_plan_from_request(request: PublishMQueryRequest) -> dict:
    plan = request.transform_plan or {}
    if request.transformation_coverage and not plan.get("coverage"):
        plan = {**plan, "coverage": request.transformation_coverage}
    return plan


def _assert_mquery_transform_publishable(request: PublishMQueryRequest) -> None:
    if _allow_partial_transform_publish() or not _enforce_transform_parity_gate():
        return
    detail = transform_publish_blocker_detail(_mquery_transform_plan_from_request(request), "Power BI")
    if detail:
        raise HTTPException(status_code=400, detail=detail)

@router.post("/publish-mquery")
async def publish_mquery_endpoint(request: PublishMQueryRequest):
    """
    Publish M Query to Power BI as a full semantic model.

    AUTO SCHEMA FIX:
      1. If request.app_id is provided, automatically fetches qlik_fields_map
         from GetTablesAndKeys — works for ANY Qlik app, no hardcoding needed.
      2. If request.qlik_fields_map is provided directly, uses that instead.
      3. qlik_fields_map is passed to MQueryConverter so LOAD * tables get
         explicit column schema in the BIM — all tables show data immediately,
         no refresh needed to discover columns.
    """
    dataset_name = request.dataset_name or "Qlik_Migrated_Dataset"
    combined_m   = request.combined_mquery or ""
    raw_script   = request.raw_script or ""
    data_source_path = request.data_source_path or request.sharepoint_url or "[DataSourcePath]"

    logger.info("[publish_mquery] Dataset: %s", dataset_name)

    _assert_mquery_transform_publishable(request)

    workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="POWERBI_WORKSPACE_ID not set in .env file.")
    if not combined_m and not raw_script:
        raise HTTPException(status_code=400, detail="Provide combined_mquery or raw_script.")

    # ── Step 1: Auto-fetch qlik_fields_map ───────────────────────────────────
    # Priority: caller-supplied > auto-fetched from GetTablesAndKeys
    qlik_fields_map: Dict[str, List[str]] = {}

    if request.qlik_fields_map:
        # Caller already supplied it (e.g. frontend passed it from a previous call)
        qlik_fields_map = dict(request.qlik_fields_map)
        logger.info(
            "[publish_mquery] Using caller-supplied qlik_fields_map: %d tables",
            len(qlik_fields_map)
        )
    elif request.app_id:
        # Auto-fetch from Qlik data model — works for any app automatically
        logger.info(
            "[publish_mquery] Auto-fetching qlik_fields_map for app_id='%s'",
            request.app_id
        )
        qlik_fields_map = _build_qlik_fields_map(request.app_id)
    else:
        logger.info(
            "[publish_mquery] No Qlik app_id/field map supplied; parsing caller-provided M Query directly."
        )

    # ── Step 2: Parse M Query / LoadScript into table list ───────────────────
    try:
        try:
            from pbit_generator import parse_combined_mquery  # type: ignore
        except ModuleNotFoundError:
            parse_combined_mquery = _parse_combined_mquery_fallback

        # Prefer regenerating from raw_script whenever it is available.
        # The frontend may carry an older combined_mquery string produced before
        # converter fixes, while raw_script lets publish use the latest backend
        # parser/converter logic and the real current SharePoint path.
        if raw_script.strip():
            from app.utils.loadscript_parser import LoadScriptParser
            from app.services.mquery_converter import MQueryConverter

            parse_result = LoadScriptParser(raw_script).parse(
                qlik_fields_map=qlik_fields_map
            )
            raw_tables = parse_result.get("details", {}).get("tables", [])
            all_converted = MQueryConverter().convert_all(
                raw_tables,
                base_path=data_source_path,
                qlik_fields_map=qlik_fields_map,
            )
            tables_m = [
                {
                    "name":         t["name"],
                    "source_type":  t["source_type"],
                    "m_expression": t["m_expression"],
                    "fields":       t.get("fields", []),
                    "options":      t.get("options", {}),
                    "source_path":  t.get("source_path", ""),
                }
                for t in all_converted
            ]
            before = len(tables_m)
            tables_m = [t for t in tables_m if not _is_system_table(t["name"])]
            tables_m = _dedupe_tables_m_by_name(tables_m, "raw_script rebuild")
            logger.info(
                "[publish_mquery] Rebuilt from raw_script using current converter: %d tables (%d system filtered)",
                len(tables_m), before - len(tables_m)
            )

        elif combined_m.strip():
            tables_m = parse_combined_mquery(combined_m)
            logger.info("[publish_mquery] Parsed %d tables from combined M", len(tables_m))

            # Initialize fields if missing
            for t in tables_m:
                if "fields" not in t:
                    t["fields"] = []

            # Enrich fields from M expressions
            try:
                from app.services.powerbi_publisher import (
                    _extract_fields_from_m,
                    _infer_alteryx_csv_fields,
                )
                for t in tables_m:
                    extracted_fields = _extract_fields_from_m(t.get("m_expression", ""))
                    if extracted_fields and not request.app_id:
                        # Caller-provided Alteryx M is the source of truth. The
                        # frontend can carry stale parser fields from pre-group or
                        # pre-select steps (for example ValueType), which causes
                        # Power BI to declare columns that the final rowset no
                        # longer returns.
                        previous_count = len(t.get("fields") or [])
                        t["fields"] = extracted_fields
                        logger.info(
                            "[publish_mquery] Replaced '%s' field metadata from final M expression: %d -> %d",
                            t["name"],
                            previous_count,
                            len(extracted_fields),
                        )
                    elif extracted_fields and (not t.get("fields") or len(t["fields"]) == 0):
                        t["fields"] = extracted_fields
                        logger.info("[publish_mquery] Extracted %d fields from '%s' M expression", 
                                   len(extracted_fields), t["name"])
                    elif not t.get("fields") and str(t.get("name", "")).lower().endswith("_raw"):
                        inferred_fields = _infer_alteryx_csv_fields(
                            t.get("name", ""),
                            t.get("m_expression", ""),
                        )
                        if inferred_fields:
                            t["fields"] = inferred_fields
                            logger.info(
                                "[publish_mquery] Inferred %d fields for '%s' from Alteryx CSV source name",
                                len(inferred_fields), t["name"],
                            )
            except Exception as extract_exc:
                logger.warning("[publish_mquery] Field extraction failed: %s", extract_exc)

            before   = len(tables_m)
            tables_m = [t for t in tables_m if not _is_system_table(t["name"])]
            tables_m = _dedupe_tables_m_by_name(tables_m, "combined M parse")
            logger.info(
                "[publish_mquery] Parsed combined M: %d tables (%d system filtered)",
                len(tables_m), before - len(tables_m)
            )

        else:
            raise HTTPException(status_code=400, detail="Provide combined_mquery or raw_script.")

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Script parse/convert error: {exc}")

    if not tables_m:
        raise HTTPException(status_code=400, detail="No tables found in the provided script.")

    # ── Step 2b: Enrich _raw table fields from Alteryx source_fields_map ─────
    # Use exact/case-insensitive table matches only. Multi-source workflows can
    # have different CSV schemas, so copying a generic fallback schema to every
    # _raw table causes invalid Power BI models.
    alteryx_source_fields: Dict[str, List[Any]] = dict(request.alteryx_source_fields or {})
    if alteryx_source_fields:
        _asf_lower: Dict[str, List[Any]] = {
            str(k).lower(): v
            for k, v in alteryx_source_fields.items()
            if v
        }

        for t in tables_m:
            tname = t.get("name", "")
            if t.get("fields"):
                continue

            map_entry: List[Any] = _lookup_map_fields(tname, alteryx_source_fields)
            if not map_entry:
                source_path = str(t.get("source_path") or "").strip()
                source_file = ""
                if source_path:
                    source_file = os.path.splitext(os.path.basename(source_path))[0].strip()
                if not source_file:
                    source_file = _extract_source_file_name(str(t.get("m_expression") or ""))
                if source_file:
                    map_entry = _lookup_map_fields(source_file, alteryx_source_fields)
                    if not map_entry:
                        source_key = _table_match_key(source_file)
                        if source_key:
                            for key, entry in alteryx_source_fields.items():
                                if source_key in _table_match_key(key):
                                    map_entry = entry
                                    logger.info(
                                        "[publish_mquery] alteryx_source_fields: matched '%s' to source file '%s' via loose key '%s'",
                                        tname, source_file, key,
                                    )
                                    break
                    elif map_entry:
                        logger.info(
                            "[publish_mquery] alteryx_source_fields: matched '%s' to source file '%s' via normalized key",
                            tname, source_file,
                        )
                if not map_entry:
                    continue

            normalised: List[Dict[str, Any]] = []
            for item in map_entry:
                if isinstance(item, dict):
                    col_name = str(item.get("name") or "").strip()
                    col_type = str(item.get("type") or "string").strip()
                elif isinstance(item, str):
                    col_name = item.strip()
                    col_type = "string"
                else:
                    continue
                if col_name:
                    normalised.append({
                        "name": col_name,
                        "alias": col_name,
                        "expression": col_name,
                        "type": col_type,
                        "extracted_from": "alteryx_source_fields",
                    })
            if normalised:
                t["fields"] = normalised
                logger.info(
                    "[publish_mquery] alteryx_source_fields: enriched '%s' with %d column(s): %s",
                    tname, len(normalised), [f["name"] for f in normalised[:6]],
                )

    # ── Step 3: Inject ApplyMap dimension tables ──────────────────────────────
    tables_m = _inject_applymap_dimension_tables(tables_m, data_source_path)

    # Keep Scripts flow aligned with CSV flow: do not publish helper/system tables.
    # These helper tables can appear as an unexpected 7th table (e.g. _cityKey2GeoPoint)
    # and create inconsistent model behavior across flows.
    pre_filter_count = len(tables_m)
    tables_m = [
        t for t in tables_m
        if not _is_system_table(t.get("name", ""))
        and not _is_applymap_dimension_table(t)
    ]
    tables_m = _dedupe_tables_m_by_name(tables_m, "helper/system filter")
    if len(tables_m) != pre_filter_count:
        logger.info(
            "[publish_mquery] Filtered %d helper/system table(s) before publish",
            pre_filter_count - len(tables_m),
        )

    # ── Step 4: Relationship inference ───────────────────────────────────────
    if request.relationships:
        logger.info(
            "[publish_mquery] Ignoring client-provided relationships (%d). Using relationship_service as source-of-truth.",
            len(request.relationships),
        )
    # ── Step 3c: Final fallback — populate fields from qlik_fields_map for tables ──
    # that still have no fields after all extraction attempts above.
    # This is the critical path for the M Query flow: parse_combined_mquery returns
    # tables with no fields, _extract_fields_from_m may return [] for dynamic-schema
    # tables, and LoadScript enrichment may also fail. qlik_fields_map (auto-fetched
    # from Qlik GetTablesAndKeys) has authoritative field lists for every table.
    if qlik_fields_map:
        for t in tables_m:
            table_name = t.get("name", "")
            map_fields = _lookup_map_fields(table_name, qlik_fields_map)
            if not map_fields:
                continue

            existing_fields_raw = t.get("fields") or []
            existing_field_names: List[str] = []
            for f in existing_fields_raw:
                if isinstance(f, dict):
                    n = str(f.get("alias") or f.get("name") or "").strip()
                else:
                    n = str(f or "").strip()
                if n and n != "*":
                    existing_field_names.append(n)

            merged_names: List[str] = []
            seen = set()
            for n in existing_field_names + list(map_fields):
                key = str(n).strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    merged_names.append(str(n).strip())

            if not merged_names:
                continue

            existing_by_name: Dict[str, Dict[str, Any]] = {}
            for field in existing_fields_raw:
                if not isinstance(field, dict):
                    continue
                field_name = str(field.get("alias") or field.get("name") or "").strip()
                if field_name:
                    existing_by_name[field_name.lower()] = field

            merged_fields: List[Dict[str, Any]] = []
            for field_name in merged_names:
                existing_field = existing_by_name.get(field_name.lower())
                if existing_field:
                    merged_fields.append(existing_field)
                else:
                    merged_fields.append({
                        "name": field_name,
                        "alias": field_name,
                        "expression": field_name,
                        "type": "string",
                        "extracted_from": "qlik_fields_map",
                    })

            added_count = max(0, len(merged_fields) - len(existing_field_names))
            t["fields"] = merged_fields
            if added_count > 0 or not existing_field_names:
                logger.info(
                    "[publish_mquery] qlik_fields_map merge: '%s' fields %d -> %d (+%d)",
                    table_name,
                    len(existing_field_names),
                    len(merged_fields),
                    added_count,
                )

    tables_m = _normalize_tables_m_fields(tables_m, qlik_fields_map)

    try:
        if _should_disable_alteryx_relationship_inference(tables_m, request.app_id):
            relationships = []
            logger.info(
                "[publish_mquery] Disabled generic relationship inference for Alteryx raw-source publish. "
                "Graph-aware join/cardinality mapping is required before creating relationships."
            )
        else:
            col_name_map_by_table = build_col_name_map_for_tables_m(tables_m)
            relationships = resolve_relationships_unified(tables_m, col_name_map_by_table)
            relationships = _apply_row_aware_cardinality(relationships, request.app_id)
        logger.info(
            "[publish_mquery] Unified inferred %d relationship(s) from %d tables "
            "(fields populated: %d)",
            len(relationships),
            len(tables_m),
            sum(1 for t in tables_m if t.get("fields")),
        )
    except Exception as rel_exc:
        logger.warning("[publish_mquery] Unified relationship inference failed: %s", rel_exc)
        relationships = []

    # ── Step 5: Publish ───────────────────────────────────────────────────────
    try:
        from app.services.powerbi_publisher import publish_semantic_model

        result = publish_semantic_model(
            dataset_name=dataset_name,
            tables_m=tables_m,
            workspace_id=workspace_id,
            relationships=relationships,
            data_source_path=request.data_source_path or "",
            db_connection_string=request.db_connection_string or "",
            access_token=request.access_token or "",
            # ✅ KEY FIX: pass qlik_fields_map so BIM gets explicit columns
            qlik_fields_map=qlik_fields_map,
        )

        if result.get("auth_required"):
            return {
                "success":         False,
                "auth_required":   True,
                "user_code":       result.get("user_code"),
                "device_code_url": result.get("device_code_url"),
                "message":         result.get("message", ""),
            }

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Publish failed"))

        logger.info("[publish_mquery] Published via %s", result.get("method"))
        published_tables = result.get("published_tables") or _publish_table_schema(tables_m)
        dataset_key = re.sub(r"[^a-z0-9]+", "", str(dataset_name or "").lower())
        final_table = next(
            (
                table for table in published_tables
                if re.sub(r"[^a-z0-9]+", "", str(table.get("name") or "").lower()) == dataset_key
            ),
            None,
        )
        if final_table is None and published_tables:
            non_system_tables = [
                table for table in published_tables
                if not str(table.get("name") or "").startswith(("LocalDateTable", "DateTableTemplate", "_"))
            ]
            final_table = non_system_tables[-1] if non_system_tables else published_tables[-1]

        return {
            "success":            True,
            "dataset_id":         result.get("dataset_id", ""),
            "dataset_name":       dataset_name,
            "final_table_name":    (final_table or {}).get("name", dataset_name),
            "published_tables":    published_tables,
            "available_columns":   (final_table or {}).get("columns", []),
            "tables_deployed":    len(tables_m),
            "method":             result.get("method", ""),
            "workspace_url":      result.get("workspace_url", ""),
            "dataset_url":        result.get("dataset_url", ""),
            "qlik_fields_map_used": len(qlik_fields_map),
            "transformation_coverage": request.transformation_coverage or {},
            "transform_plan":      request.transform_plan or {},
            "transform_publish_warning": transform_publish_blocker_detail(_mquery_transform_plan_from_request(request), "Power BI"),
            "message":            result.get("message", f"Published {dataset_name} to Power BI"),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[publish_mquery] Publish failed")
        raise HTTPException(status_code=500, detail=f"Publish failed: {exc}")


class GeneratePbitRequest(_BaseModel):
    dataset_name:     str  = "Qlik_Migrated_Dataset"
    combined_mquery:  str  = ""
    raw_script:       str  = ""
    data_source_path: str  = ""
    relationships:    list = []


class PowerBiValidationRequest(_BaseModel):
    dataset_id: str
    table_name: str
    workspace_id: str = ""
    numeric_columns: List[str] = []
    expected_row_count: Optional[int] = None
    expected_totals: Dict[str, float] = {}


def _dax_table(name: str) -> str:
    return "'" + str(name or "").replace("'", "''") + "'"


def _dax_column(name: str) -> str:
    return "[" + str(name or "").replace("]", "]]") + "]"


def _normalize_execute_query_row(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {}
    for key, value in (row or {}).items():
        clean_key = str(key).strip("[]")
        if "[" in clean_key and "]" in clean_key:
            clean_key = clean_key.split("[")[-1].rstrip("]")
        normalized[clean_key] = value
    return normalized


def _table_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _lookup_map_fields(table_name: str, field_map: Dict[str, Any]) -> List[Any]:
    if not table_name or not field_map:
        return []
    exact_match = field_map.get(table_name)
    if exact_match:
        return exact_match
    lower_table_name = table_name.lower()
    for key, value in field_map.items():
        if key.lower() == lower_table_name:
            return value
    normalized_key = _table_match_key(table_name)
    if normalized_key:
        for key, value in field_map.items():
            if _table_match_key(key) == normalized_key:
                return value
    return []


def _get_powerbi_table_metadata(workspace_id: str, dataset_id: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    tables, _issue = _get_powerbi_table_metadata_result(workspace_id, dataset_id, headers)
    return tables


def _get_powerbi_table_metadata_result(
    workspace_id: str,
    dataset_id: str,
    headers: Dict[str, str],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    try:
        url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
            f"/datasets/{dataset_id}/tables"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.ok:
            tables: List[Dict[str, Any]] = []
            for item in resp.json().get("value", []):
                name = str(item.get("name") or item.get("displayName") or "")
                if not name:
                    continue
                tables.append({
                    "name": name,
                    "columns": [
                        str(col.get("name") or "")
                        for col in item.get("columns", [])
                        if col.get("name")
                    ],
                })
            return tables, {"status": "ok", "message": ""}
        logger.warning("[validate_powerbi] Table discovery failed: %d %s", resp.status_code, resp.text[:300])
        return [], {
            "status": "not_available",
            "http_status": resp.status_code,
            "message": resp.text[:500],
            "is_push_dataset_error": "not Push API dataset" in (resp.text or ""),
        }
    except Exception as exc:
        logger.warning("[validate_powerbi] Table discovery error: %s", exc)
        return [], {"status": "error", "message": str(exc), "is_push_dataset_error": False}


def _get_debug_bim_table_columns(table_name: str) -> List[str]:
    candidates = [
        os.path.join(os.getcwd(), "debug_model.bim"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../debug_model.bim")),
    ]
    requested_key = _table_match_key(table_name)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                bim = json.load(handle)
            for table in bim.get("model", {}).get("tables", []):
                name = str(table.get("name") or "")
                if name == table_name or _table_match_key(name) == requested_key:
                    return [
                        str(col.get("name") or "")
                        for col in table.get("columns", [])
                        if col.get("name")
                    ]
        except Exception as exc:
            logger.debug("[validate_powerbi] debug_model.bim column lookup failed for %s: %s", path, exc)
    return []


def _resolve_powerbi_table_name(requested: str, available: List[Dict[str, Any]]) -> str:
    names = [table.get("name", "") for table in available if table.get("name")]
    if not names:
        return requested
    requested_key = _table_match_key(requested)
    for table in names:
        if table == requested:
            return table
    for table in names:
        if _table_match_key(table) == requested_key:
            return table
    for table in names:
        key = _table_match_key(table)
        if requested_key and (requested_key in key or key in requested_key):
            return table
    non_system = [
        table for table in names
        if not table.startswith(("LocalDateTable", "DateTableTemplate", "_"))
    ]
    return non_system[0] if len(non_system) == 1 else requested


def _resolve_powerbi_columns(requested: List[str], available: List[str]) -> tuple[List[str], List[str]]:
    if not requested:
        return [], []
    if not available:
        return requested, []
    resolved: List[str] = []
    skipped: List[str] = []
    available_by_key = {_table_match_key(col): col for col in available}
    for col in requested:
        if not col:
            continue
        matched = available_by_key.get(_table_match_key(col))
        if matched:
            resolved.append(matched)
        else:
            skipped.append(col)
    return resolved, skipped


def _post_execute_dax(query_url: str, headers: Dict[str, str], dax: str) -> requests.Response:
    body = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    return requests.post(query_url, headers=headers, json=body, timeout=60)


def _extract_execute_query_row(response_json: Dict[str, Any]) -> Dict[str, Any]:
    rows = (
        response_json.get("results", [{}])[0]
        .get("tables", [{}])[0]
        .get("rows", [])
    )
    return _normalize_execute_query_row(rows[0] if rows else {})


def _row_count_fallback_dax(table_ref: str, columns: List[str]) -> str:
    count_items = [f'"RowCount", COUNTROWS({table_ref})']
    for index, col in enumerate((columns or [])[:30], start=1):
        col_ref = f"{table_ref}{_dax_column(col)}"
        count_items.append(
            f'"ColumnCount_{index}", COUNTROWS(FILTER({table_ref}, NOT(ISBLANK({col_ref}))))'
        )
    return "EVALUATE ROW(" + ", ".join(count_items) + ")"


def _fallback_row_count_from_columns(
    query_url: str,
    headers: Dict[str, str],
    table_ref: str,
    columns: List[str],
) -> tuple[Optional[int], Dict[str, Any], str]:
    if not columns:
        return None, {}, ""
    fallback_dax = _row_count_fallback_dax(table_ref, columns)
    fallback_resp = _post_execute_dax(query_url, headers, fallback_dax)
    if not fallback_resp.ok:
        logger.warning(
            "[validate_powerbi] Column-count fallback failed: %d %s",
            fallback_resp.status_code,
            fallback_resp.text[:300],
        )
        return None, {}, fallback_dax
    fallback_actual = _extract_execute_query_row(fallback_resp.json())
    fallback_counts = [
        int(value)
        for key, value in fallback_actual.items()
        if str(key).startswith("ColumnCount_") and isinstance(value, (int, float)) and int(value) > 0
    ]
    if not fallback_counts:
        return None, fallback_actual, fallback_dax
    return max(fallback_counts), fallback_actual, fallback_dax


@router.post("/validate-powerbi")
async def validate_powerbi_dataset_endpoint(request: PowerBiValidationRequest):
    """Return actual row count/totals from the published Power BI semantic model."""
    workspace_id = request.workspace_id or os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="POWERBI_WORKSPACE_ID is not configured.")
    if not request.dataset_id:
        raise HTTPException(status_code=400, detail="dataset_id is required.")
    if not request.table_name:
        raise HTTPException(status_code=400, detail="table_name is required.")

    token = _acquire_sp_token("https://analysis.windows.net/powerbi/api/.default")
    if not token:
        raise HTTPException(status_code=500, detail="Unable to acquire Power BI service principal token.")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    available_metadata, metadata_issue = _get_powerbi_table_metadata_result(workspace_id, request.dataset_id, headers)
    available_tables = [table.get("name", "") for table in available_metadata if table.get("name")]
    if not available_metadata and metadata_issue.get("is_push_dataset_error"):
        logger.info(
            "[validate_powerbi] Push dataset detected; skipping table discovery and querying executeQueries directly."
        )
        available_metadata = []
        available_tables = []

    actual_table_name = _resolve_powerbi_table_name(request.table_name, available_metadata)
    actual_columns = next(
        (table.get("columns", []) for table in available_metadata if table.get("name") == actual_table_name),
        [],
    )
    numeric_columns, skipped_columns = _resolve_powerbi_columns(request.numeric_columns or [], actual_columns)
    table_ref = _dax_table(actual_table_name)
    row_items = [f'"RowCount", COUNTROWS({table_ref})']
    for col in numeric_columns:
        if not col:
            continue
        col_ref = f"{table_ref}{_dax_column(col)}"
        # Columns from CSV semantic models are often stored as text in BIM, so
        # VALUE() keeps totals useful even when Power BI imported string columns.
        safe_metric = re.sub(r"[^A-Za-z0-9_]+", "_", col).strip("_") or "Column"
        numeric_expr = f"IFERROR(VALUE({col_ref}), BLANK())"
        row_items.extend([
            f'"{safe_metric}__NotNull", COUNTROWS(FILTER({table_ref}, NOT(ISBLANK({col_ref}))))',
            f'"{safe_metric}__Sum", SUMX({table_ref}, {numeric_expr})',
            f'"{safe_metric}__Min", MINX({table_ref}, {numeric_expr})',
            f'"{safe_metric}__Max", MAXX({table_ref}, {numeric_expr})',
            f'"{safe_metric}__Average", AVERAGEX({table_ref}, {numeric_expr})',
        ])
    dax = "EVALUATE ROW(" + ", ".join(row_items) + ")"

    query_url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
        f"/datasets/{request.dataset_id}/executeQueries"
    )
    refresh_status: Dict[str, Any] = {}
    try:
        refresh_url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}"
            f"/datasets/{request.dataset_id}/refreshes?$top=1"
        )
        refresh_resp = requests.get(refresh_url, headers=headers, timeout=30)
        if refresh_resp.ok:
            latest = (refresh_resp.json().get("value") or [{}])[0]
            refresh_status = {
                "status": latest.get("status", "Unknown"),
                "startTime": latest.get("startTime"),
                "endTime": latest.get("endTime"),
                "serviceExceptionJson": latest.get("serviceExceptionJson"),
            }
    except Exception as exc:
        logger.warning("[validate_powerbi] Refresh status lookup failed: %s", exc)

    resp = _post_execute_dax(query_url, headers, dax)
    if not resp.ok and actual_table_name == request.table_name:
        fallback_candidates = [
            request.table_name.replace("_", " "),
            request.table_name.replace("_", ""),
            request.table_name.title().replace("_", " "),
        ]
        for candidate in fallback_candidates:
            if not candidate or candidate == actual_table_name:
                continue
            candidate_ref = _dax_table(candidate)
            candidate_items = [f'"RowCount", COUNTROWS({candidate_ref})']
            for col in numeric_columns:
                if col:
                    col_ref = f"{candidate_ref}{_dax_column(col)}"
                    safe_metric = re.sub(r"[^A-Za-z0-9_]+", "_", col).strip("_") or "Column"
                    numeric_expr = f"IFERROR(VALUE({col_ref}), BLANK())"
                    candidate_items.extend([
                        f'"{safe_metric}__NotNull", COUNTROWS(FILTER({candidate_ref}, NOT(ISBLANK({col_ref}))))',
                        f'"{safe_metric}__Sum", SUMX({candidate_ref}, {numeric_expr})',
                        f'"{safe_metric}__Min", MINX({candidate_ref}, {numeric_expr})',
                        f'"{safe_metric}__Max", MAXX({candidate_ref}, {numeric_expr})',
                        f'"{safe_metric}__Average", AVERAGEX({candidate_ref}, {numeric_expr})',
                    ])
            candidate_dax = "EVALUATE ROW(" + ", ".join(candidate_items) + ")"
            candidate_resp = _post_execute_dax(query_url, headers, candidate_dax)
            if candidate_resp.ok:
                actual_table_name = candidate
                table_ref = candidate_ref
                dax = candidate_dax
                resp = candidate_resp
                break

    if not resp.ok:
        raise HTTPException(
            status_code=resp.status_code,
            detail=(
                f"Power BI executeQueries failed for table '{actual_table_name}'. "
                f"Available tables: {available_tables or 'not returned by Power BI'}. "
                f"Available columns: {actual_columns or 'not returned by Power BI'}. "
                f"Response: {resp.text[:500]}"
            ),
        )

    result = resp.json()
    actual = _extract_execute_query_row(result)
    actual_row_count = int(actual.get("RowCount") or 0)
    row_count_method = "countrows"
    fallback_dax = ""
    fallback_actual: Dict[str, Any] = {}
    if actual_row_count == 0:
        fallback_count, fallback_actual, fallback_dax = _fallback_row_count_from_columns(
            query_url,
            headers,
            table_ref,
            actual_columns or numeric_columns,
        )
        if fallback_count is not None and fallback_count > 0:
            actual_row_count = fallback_count
            actual["RowCount"] = fallback_count
            row_count_method = "nonblank_column_count_fallback"

    checks: List[Dict[str, Any]] = []
    if request.expected_row_count is not None:
        checks.append({
            "name": "Row count",
            "expected": request.expected_row_count,
            "actual": actual_row_count,
            "variance": actual_row_count - request.expected_row_count,
            "status": "PASS" if actual_row_count == request.expected_row_count else "WARNING",
        })
    else:
        checks.append({
            "name": "Row count",
            "expected": None,
            "actual": actual_row_count,
            "variance": None,
            "status": "INFO",
        })

    for col, expected in (request.expected_totals or {}).items():
        resolved_col = next(
            (candidate for candidate in numeric_columns if _table_match_key(candidate) == _table_match_key(col)),
            "",
        )
        if not resolved_col:
            checks.append({
                "name": f"Total {col}",
                "expected": expected,
                "actual": None,
                "variance": None,
                "status": "WARNING",
                "message": f"Column '{col}' was not found in Power BI table '{actual_table_name}'.",
            })
            continue
        safe_metric = re.sub(r"[^A-Za-z0-9_]+", "_", resolved_col).strip("_") or "Column"
        actual_value = float(actual.get(f"{safe_metric}__Sum") or 0)
        variance = actual_value - float(expected)
        checks.append({
            "name": f"Total {col}",
            "expected": expected,
            "actual": actual_value,
            "variance": variance,
            "status": "PASS" if abs(variance) < 0.0001 else "WARNING",
        })

    return {
        "success": True,
        "dataset_id": request.dataset_id,
        "table_name": actual_table_name,
        "requested_table_name": request.table_name,
        "available_tables": available_tables,
        "available_columns": actual_columns or numeric_columns,
        "queried_numeric_columns": numeric_columns,
        "skipped_numeric_columns": skipped_columns,
        "workspace_id": workspace_id,
        "dax": dax,
        "row_count_method": row_count_method,
        "row_count_fallback_dax": fallback_dax,
        "row_count_fallback_actual": fallback_actual,
        "actual": actual,
        "refresh": refresh_status,
        "checks": checks,
    }


@router.post("/generate-pbit")
async def generate_pbit_endpoint(request: GeneratePbitRequest):
    import base64 as _b64
    logger.info("[generate_pbit] Dataset: %s", request.dataset_name)

    combined_m = request.combined_mquery or ""
    raw_script  = request.raw_script or ""

    if not combined_m and not raw_script:
        raise HTTPException(status_code=400, detail="Provide combined_mquery or raw_script.")

    try:
        from app.services.pbit_generator import parse_combined_mquery, build_pbit

        if combined_m.strip():
            tables_m = parse_combined_mquery(combined_m)
            if not tables_m:
                raise HTTPException(status_code=400, detail="Could not parse table sections from combined_mquery.")
        else:
            from app.utils.loadscript_parser import LoadScriptParser
            from app.services.mquery_converter import MQueryConverter
            parse_result = LoadScriptParser(raw_script).parse()
            tables = parse_result.get("details", {}).get("tables", [])
            if not tables:
                raise HTTPException(status_code=400, detail="No tables found in raw_script.")
            all_converted = MQueryConverter().convert_all(tables, base_path="[DataSourcePath]")
            tables_m = [
                {"name": t["name"], "source_type": t["source_type"], "m_expression": t["m_expression"]}
                for t in all_converted
            ]

        pbit_bytes = build_pbit(
            tables_m=tables_m,
            dataset_name=request.dataset_name,
            relationships=request.relationships or [],
            data_source_path_default=request.data_source_path or "",
        )

        pbit_b64  = _b64.b64encode(pbit_bytes).decode("ascii")
        safe_name = re.sub(r"[^\w\-]", "_", request.dataset_name)

        return {
            "success":         True,
            "dataset_name":    request.dataset_name,
            "filename":        f"{safe_name}.pbit",
            "tables_count":    len(tables_m),
            "file_size_bytes": len(pbit_bytes),
            "pbit_base64":     pbit_b64,
            "message":         f"{request.dataset_name}.pbit generated with {len(tables_m)} table(s).",
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[generate_pbit] Failed")
        raise HTTPException(status_code=500, detail=f"PBIT generation failed: {exc}")


# ===========================================================================
# PUBLISH TABLES (Flow 2 - CSV Export Path)
# ===========================================================================

# Optional row cap for CSV inline publish.
# Default 0 = no cap (prevents data loss). Set env MAX_INLINE_ROWS to enforce cap.
MAX_INLINE_ROWS = int(os.getenv("MAX_INLINE_ROWS", "0") or "0")


def _sanitize_col(name: str) -> str:
    return _sanitize_col_name(name)


def _to_m_text_literal(value: Any) -> str:
    """Encode arbitrary scalar values safely for M quoted text literals."""
    s = "" if value is None else str(value)
    return (
        s.replace("#", "#(#)")
        .replace("\r\n", "#(cr)#(lf)")
        .replace("\n", "#(lf)")
        .replace("\r", "#(cr)")
        .replace("\t", "#(tab)")
        .replace('"', '""')
    )


def _build_inline_text_m_expression(headers: List[str], rows: List[Dict[str, Any]]) -> str:
    type_pairs = ", ".join('{{"{}", type text}}'.format(h) for h in headers)

    if rows:
        record_rows = []
        for row in rows:
            pairs = ", ".join(
                '{} = "{}"'.format(h, _to_m_text_literal(row.get(h, "")))
                for h in headers
            )
            record_rows.append(f"        [{pairs}]")
        record_rows_str = ",\n".join(record_rows)
        return (
            f"let\n"
            f"    Source = Table.FromRecords({{\n"
            f"{record_rows_str}\n"
            f"    }}),\n"
            f"    TypedTable = Table.TransformColumnTypes(Source, {{{type_pairs}}})\n"
            f"in\n"
            f"    TypedTable"
        )

    header_list = ", ".join(f'"{h}"' for h in headers)
    return (
        f"let\n"
        f"    Source = Table.FromRows({{}}, {{{header_list}}}),\n"
        f"    TypedTable = Table.TransformColumnTypes(Source, {{{type_pairs}}})\n"
        f"in\n"
        f"    TypedTable"
    )


class PublishTablesRequest(_BaseModel):
    dataset_name: str  = "Qlik_Migrated_Dataset"
    tables:       list = []
    relationships: list = []


@router.post("/publish-tables")
async def publish_tables_endpoint(request: PublishTablesRequest):
    logger.info("[publish_tables] Received %d tables", len(request.tables))

    from powerbi_publisher import publish_semantic_model

    workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="POWERBI_WORKSPACE_ID not set")

    tables_m = []
    col_name_map_by_table: Dict[str, Dict[str, str]] = {}
    normalized_rows_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for t in request.tables:
        name = t.get("name", "Table")
        rows = t.get("rows", [])
        provided_columns = t.get("columns", []) or []

        orig_headers, table_col_map, normalized_rows = normalize_table_rows(
            table_name=name,
            rows=rows,
            provided_columns=provided_columns,
        )

        if not orig_headers:
            logger.warning("[publish_tables] Skipping table '%s': no rows and no columns metadata", name)
            continue

        safe_headers = [table_col_map[h] for h in orig_headers]
        col_name_map_by_table[name] = dict(table_col_map)
        normalized_rows_by_table[name] = normalized_rows

        total_rows = len(normalized_rows)
        if MAX_INLINE_ROWS > 0 and total_rows > MAX_INLINE_ROWS:
            logger.info("[publish_tables] Table '%s': capping %d rows to %d", name, total_rows, MAX_INLINE_ROWS)
            normalized_rows = normalized_rows[:MAX_INLINE_ROWS]
            normalized_rows_by_table[name] = normalized_rows

        fields = [{"name": h, "type": "string"} for h in safe_headers]
        m_expr = _build_inline_text_m_expression(safe_headers, normalized_rows)

        tables_m.append({
            "name": name, "source_type": "inline_csv",
            "m_expression": m_expr, "fields": fields,
        })

    if not tables_m:
        raise HTTPException(status_code=400, detail="No tables with data provided")

    if request.relationships:
        logger.info(
            "[publish_tables] Ignoring client-provided relationships (%d). Using relationship_service as source-of-truth.",
            len(request.relationships),
        )

    relationship_source = "relationship_service_unified"
    try:
        relationships = resolve_relationships_unified(tables_m, col_name_map_by_table)
        relationships = _apply_row_aware_cardinality_from_rows(relationships, normalized_rows_by_table)
        logger.info("[publish_tables] Unified inferred %d relationship(s)", len(relationships))
    except Exception as e:
        logger.warning("[publish_tables] Unified relationship inference failed: %s", e)
        relationships = []

    result = publish_semantic_model(
        dataset_name=request.dataset_name,
        tables_m=tables_m,
        relationships=relationships,
        workspace_id=workspace_id,
        data_source_path="",
    )

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Publish failed"))

    return {
        "success":         True,
        "dataset_id":      result.get("dataset_id", ""),
        "dataset_name":    request.dataset_name,
        "tables_deployed": len(tables_m),
        "relationships_source": relationship_source,
        "relationships_count": len(relationships),
        "relationships_applied": relationships,
        "workspace_url":   result.get("workspace_url", ""),
        "message":         result.get("message", ""),
    }
