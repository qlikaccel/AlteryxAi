

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

from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel as _BaseModel

from six_stage_orchestrator import SixStageOrchestrator, run_migration_pipeline
from relationship_service import (
    _sanitize_col_name,
    infer_relationships_unified,
    sanitize_rel_columns,
    build_col_name_map_for_tables_m,
    normalize_table_rows,
    resolve_relationships_unified,
)

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
                from powerbi_auth import get_auth_manager
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
        from loadscript_fetcher import LoadScriptFetcher
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
        from loadscript_parser import LoadScriptParser
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
        from mquery_converter import MQueryConverter
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
        from loadscript_fetcher import LoadScriptFetcher
        fetcher = LoadScriptFetcher()
        fetch_result = fetcher.fetch_loadscript(app_id)
        if fetch_result.get("status") not in ("success", "partial_success"):
            raise HTTPException(status_code=503, detail=f"Fetch failed: {fetch_result.get('message')}")
        loadscript = fetch_result.get("loadscript", "")

        from loadscript_parser import LoadScriptParser
        parse_result = LoadScriptParser(loadscript).parse()
        tables = parse_result.get("details", {}).get("tables", [])

        from mquery_converter import MQueryConverter
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
    from mquery_converter import MQueryConverter
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
        map_fields = qlik_fields_map.get(table_name, []) or next(
            (v for k, v in qlik_fields_map.items() if k.lower() == table_name.lower()),
            [],
        )
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


def _fetch_table_rows_for_cardinality(app_id: str, table_name: str, limit: int = 5000) -> List[Dict[str, Any]]:
    if not app_id or not table_name:
        return []
    try:
        from qlik_websocket_client import QlikWebSocketClient

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


def _apply_row_aware_cardinality(
    relationships: List[Dict[str, Any]],
    app_id: str,
) -> List[Dict[str, Any]]:
    if not relationships or not app_id:
        return relationships

    table_rows_cache: Dict[str, List[Dict[str, Any]]] = {}
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

        if from_table not in table_rows_cache:
            table_rows_cache[from_table] = _fetch_table_rows_for_cardinality(app_id, from_table)
        if to_table not in table_rows_cache:
            table_rows_cache[to_table] = _fetch_table_rows_for_cardinality(app_id, to_table)

        from_unique = _column_is_unique(table_rows_cache[from_table], from_col)
        to_unique = _column_is_unique(table_rows_cache[to_table], to_col)

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
        logger.warning(
            "[publish_mquery] No app_id or qlik_fields_map provided. "
            "LOAD * tables will publish with 0 columns until first refresh. "
            "Pass app_id in the request to fix this automatically."
        )

    # ── Step 2: Parse M Query / LoadScript into table list ───────────────────
    try:
        from pbit_generator import parse_combined_mquery

        # Prefer regenerating from raw_script whenever it is available.
        # The frontend may carry an older combined_mquery string produced before
        # converter fixes, while raw_script lets publish use the latest backend
        # parser/converter logic and the real current SharePoint path.
        if raw_script.strip():
            from loadscript_parser import LoadScriptParser
            from mquery_converter  import MQueryConverter

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
                from powerbi_publisher import _extract_fields_from_m
                for t in tables_m:
                    if not t.get("fields") or len(t["fields"]) == 0:
                        extracted_fields = _extract_fields_from_m(t.get("m_expression", ""))
                        if extracted_fields:
                            t["fields"] = extracted_fields
                            logger.info("[publish_mquery] Extracted %d fields from '%s' M expression", 
                                       len(extracted_fields), t["name"])
            except Exception as extract_exc:
                logger.warning("[publish_mquery] Field extraction failed: %s", extract_exc)

            before   = len(tables_m)
            tables_m = [t for t in tables_m if not _is_system_table(t["name"])]
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
            map_fields = qlik_fields_map.get(table_name, [])
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
        from powerbi_publisher import publish_semantic_model

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
        return {
            "success":            True,
            "dataset_id":         result.get("dataset_id", ""),
            "dataset_name":       dataset_name,
            "tables_deployed":    len(tables_m),
            "method":             result.get("method", ""),
            "workspace_url":      result.get("workspace_url", ""),
            "qlik_fields_map_used": len(qlik_fields_map),
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


@router.post("/generate-pbit")
async def generate_pbit_endpoint(request: GeneratePbitRequest):
    import base64 as _b64
    logger.info("[generate_pbit] Dataset: %s", request.dataset_name)

    combined_m = request.combined_mquery or ""
    raw_script  = request.raw_script or ""

    if not combined_m and not raw_script:
        raise HTTPException(status_code=400, detail="Provide combined_mquery or raw_script.")

    try:
        from pbit_generator import parse_combined_mquery, build_pbit

        if combined_m.strip():
            tables_m = parse_combined_mquery(combined_m)
            if not tables_m:
                raise HTTPException(status_code=400, detail="Could not parse table sections from combined_mquery.")
        else:
            from loadscript_parser import LoadScriptParser
            from mquery_converter import MQueryConverter
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

        total_rows = len(normalized_rows)
        if MAX_INLINE_ROWS > 0 and total_rows > MAX_INLINE_ROWS:
            logger.info("[publish_tables] Table '%s': capping %d rows to %d", name, total_rows, MAX_INLINE_ROWS)
            normalized_rows = normalized_rows[:MAX_INLINE_ROWS]

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