"""
Migration API -- FastAPI router for the 6-stage Qlik-to-Power BI pipeline.

Endpoints:
  POST /api/migration/publish-table        Full 6-stage pipeline
  POST /api/migration/preview-migration    Stages 1-3 only (no Power BI writes)
  GET  /api/migration/view-diagram         Stages 1-3+6: Mermaid + HTML ER diagram
  GET  /api/migration/er-diagram-html      Returns raw HTML for iframe embedding
  GET  /api/migration/pipeline-help        API documentation

Changes vs previous version:
  - Default publish_mode is now "xmla_semantic" (PPU).
  - /view-diagram returns both mermaid and html in the JSON response.
  - New /er-diagram-html endpoint returns raw HTML (for <iframe> embedding).
  - publish-table accepts publish_mode query param so callers can choose mode.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel as _BaseModel

from six_stage_orchestrator import SixStageOrchestrator, run_migration_pipeline

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
    """
    Execute the full 6-stage migration pipeline.

    Recommended for PPU workspaces: publish_mode=xmla_semantic
      -> Creates a proper Tabular semantic model
      -> Relationships visible in Model View
      -> ER Diagram renders in Power BI Service

    The response includes `er_diagram_html` which can be embedded directly
    in a React frontend via <iframe srcDoc={...}>.
    """
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

    # Strip heavy HTML from the main response to keep it lean
    # (full HTML available via /er-diagram-html endpoint)
    summary_result = {k: v for k, v in result.items() if k != "er_diagram_html"}
    summary_result["er_diagram_available"] = bool(result.get("er_diagram_html"))
    summary_result["er_diagram_endpoint"]  = (
        f"/api/migration/er-diagram-htmlapp_id={app_id}&dataset_name={dataset_name}"
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
    """
    Run stages 1-3 only (Extract -> Infer -> Normalize).
    No Power BI writes.  Returns inferred tables, relationships, and ER diagram.
    """
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
    """
    Generate the ER diagram for a Qlik app without publishing to Power BI.
    Returns both Mermaid syntax and full standalone HTML.

    Tip: Use /er-diagram-html for raw HTML suitable for <iframe> embedding.
    """
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
            "iframe_endpoint":   f"/api/migration/er-diagram-htmlapp_id={app_id}&dataset_name={dataset_name}",
        },
    }


# ---------------------------------------------------------------------------
# GET /er-diagram-html  <- NEW: raw HTML for iframe embedding
# ---------------------------------------------------------------------------

@router.get("/er-diagram-html", response_class=HTMLResponse)
async def er_diagram_html(
    app_id:       str = Query(..., description="Qlik Cloud app ID"),
    dataset_name: str = Query("ER Diagram", description="Title shown in diagram"),
):
    """
    Returns a standalone HTML page containing the rendered Mermaid ER diagram.

    Designed for <iframe> embedding in a React frontend:

        <iframe
          src="/api/migration/er-diagram-htmlapp_id=abc123&dataset_name=Sales"
          style={{ width: '100%', height: '600px', border: 'none' }}
        />
    """
    if not app_id:
        raise HTTPException(400, "app_id is required")

    logger.info("[API] er-diagram-html: app_id=%s", app_id)

    orchestrator = _orchestrator()
    result = orchestrator.get_er_diagram_only(app_id)

    if not result.get("success"):
        # Return a minimal error HTML page rather than a JSON 500
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
    """Return API documentation for the migration pipeline."""
    return {
        "title":    "Qlik-to-Power BI 6-Stage Migration Pipeline",
        "version":  "2.0.0",
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
                "params": {
                    "app_id":       "Qlik Cloud app ID (required)",
                    "dataset_name": "Label for output (optional)",
                },
            },
            "GET /api/migration/view-diagram": {
                "description": "ER diagram JSON (Mermaid + HTML)",
                "params": {
                    "app_id":       "Qlik Cloud app ID (required)",
                    "dataset_name": "Diagram title label (optional)",
                },
            },
            "GET /api/migration/er-diagram-html": {
                "description": "Standalone HTML ER diagram for <iframe> embedding",
                "params": {
                    "app_id":       "Qlik Cloud app ID (required)",
                    "dataset_name": "Diagram title (optional)",
                },
            },
        },
        "publish_modes": {
            "xmla_semantic": {
                "description": "Deploy a full Tabular semantic model via XMLA (PPU/Premium)",
                "requires":    "Tabular Editor 2 CLI + XMLA endpoint enabled in workspace",
                "er_diagram":  "Fully visible in Power BI Service Model View",
                "recommended": True,
            },
            "cloud_push": {
                "description": "Create a Push dataset via REST API",
                "er_diagram":  "Not available in Power BI Service (use embedded HTML instead)",
                "recommended": False,
            },
            "desktop_cloud": {
                "description": "Generate desktop handoff bundle (no Power BI writes)",
                "er_diagram":  "HTML file included in bundle",
                "recommended": False,
            },
        },
        "setup": {
            "tabular_editor": {
                "download": "https://github.com/TabularEditor/TabularEditor2/releases",
                "env_var":  "TABULAR_EDITOR_PATH",
                "example":  r"C:\Program Files\Tabular Editor 2\TabularEditor2.exe",
            },
            "xmla_endpoint": {
                "enable":  "Power BI workspace -> Settings -> Premium -> XMLA Endpoint -> Read Write",
                "env_var": "POWERBI_XMLA_ENDPOINT",
                "format":  "powerbi://api.powerbi.com/v1.0/myorg/<WorkspaceName>",
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
    """
    Deploy a full Tabular semantic model to a PPU Power BI workspace via XMLA.
    Uses Tabular Editor 2 CLI if available and a user token exists,
    otherwise falls back to REST API Push dataset.
    """
    try:
        csv_payloads: Dict[str, str] = {}
        if csv_payload_json:
            try:
                parsed = json.loads(csv_payload_json)
                if isinstance(parsed, dict):
                    csv_payloads = {str(k): str(v) for k, v in parsed.items()}
            except Exception:
                pass

        # Resolve access token from auth manager if not provided
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

        logger.info(
            "[API] publish-semantic-model: app_id=%s dataset=%s workspace=%s",
            app_id, dataset_name, workspace_id,
        )

        # Run stages 1-3 (extract + infer + normalize) then deploy via XMLA
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
    """Start XMLA user login (ROPC or device code fallback)."""
    from xmla_auth import initiate_xmla_login
    result = initiate_xmla_login()
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.post("/xmla-login/complete")
async def xmla_login_complete():
    """Complete device code login after user signs in via browser."""
    from xmla_auth import complete_xmla_login
    return complete_xmla_login()


@router.get("/xmla-login/status")
async def xmla_login_status():
    """Check XMLA user token status."""
    from xmla_auth import get_xmla_login_status
    return get_xmla_login_status()


# ===========================================================================
# LOADSCRIPT -> M QUERY PIPELINE ENDPOINTS
# These endpoints are called by the frontend (SummaryPage / qlikApi.ts):
#   POST /api/migration/fetch-loadscript
#   POST /api/migration/parse-loadscript
#   POST /api/migration/convert-to-mquery
#   POST /api/migration/full-pipeline
# ===========================================================================

import json
import os

@router.post("/fetch-loadscript")
async def fetch_loadscript_endpoint(
    app_id:     str           = Query(..., description="Qlik Cloud app ID"),
    table_name: str           = Query("", description="Selected table name (optional)"),
    tenant_url: str           = Query("", description="Qlik Cloud tenant URL (optional override)"),
):
    """
    Phase 4: Fetch the LoadScript from a Qlik Cloud app via WebSocket Engine API.
    Falls back to REST API if WebSocket is unavailable.
    """
    logger.info("=" * 80)
    logger.info("[fetch_loadscript_endpoint] ENDPOINT: /fetch-loadscript")
    logger.info("[fetch_loadscript_endpoint] App ID: %s", app_id)
    logger.info("=" * 80)

    try:
        from loadscript_fetcher import LoadScriptFetcher
        logger.info("[fetch_loadscript_endpoint] Initializing LoadScript Fetcher...")
        fetcher = LoadScriptFetcher()

        logger.info("[fetch_loadscript_endpoint] Testing Qlik Cloud connection...")
        conn_result = fetcher.test_connection()
        if conn_result.get("status") != "success":
            raise HTTPException(
                status_code=503,
                detail=f"Qlik Cloud connection failed: {conn_result.get('message', 'Unknown')}"
            )

        logger.info("[fetch_loadscript_endpoint] Fetching loadscript for app: %s", app_id)
        result = fetcher.fetch_loadscript(app_id)

        logger.info(
            "[fetch_loadscript_endpoint] [OK] Successfully fetched loadscript (%d chars)",
            len(result.get("loadscript", ""))
        )
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[fetch_loadscript_endpoint] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/parse-loadscript")
async def parse_loadscript_endpoint(
    loadscript: str = Query(..., description="Raw Qlik LoadScript to parse"),
):
    """
    Phase 5: Parse a raw Qlik LoadScript and extract structured table definitions.
    Returns tables, fields, data connections, transformations, variables.
    """
    logger.info("=" * 80)
    logger.info("[parse_loadscript_endpoint] ENDPOINT: /parse-loadscript")
    logger.info("[parse_loadscript_endpoint] Script length: %d characters", len(loadscript))
    logger.info("=" * 80)

    try:
        from loadscript_parser import LoadScriptParser
        logger.info("[parse_loadscript_endpoint] Initializing LoadScript Parser...")
        logger.info("[parse_loadscript_endpoint] Starting parse operation...")
        parser = LoadScriptParser(loadscript)
        result = parser.parse()

        logger.info("[parse_loadscript_endpoint] [OK] Successfully parsed loadscript")
        logger.info("[parse_loadscript_endpoint]    Tables: %d", result.get("summary", {}).get("tables_count", 0))
        logger.info("[parse_loadscript_endpoint]    Fields: %d", result.get("summary", {}).get("fields_count", 0))
        logger.info("[parse_loadscript_endpoint]    Connections: %d", result.get("summary", {}).get("connections_count", 0))
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[parse_loadscript_endpoint] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


class ConvertToMQueryRequest(_BaseModel):
    parsed_script_json: str = ""   # Full parse result JSON from /parse-loadscript
    table_name:         str = ""   # Specific table to convert (empty = all tables)
    base_path:          str = ""   # Base path / SharePoint site URL for file sources
    connection_string:  str = ""   # ODBC connection string for SQL sources


@router.post("/convert-to-mquery")
async def convert_to_mquery_endpoint(
    request: ConvertToMQueryRequest,
    # Legacy query-param support (kept for backward compatibility with old clients)
    parsed_script_json_q: str = Query("", alias="parsed_script_json", description="[deprecated] Use request body instead"),
    table_name_q:         str = Query("", alias="table_name",         description="[deprecated] Use request body instead"),
    base_path_q:          str = Query("", alias="base_path",          description="[deprecated] Use request body instead"),
    connection_string_q:  str = Query("", alias="connection_string",  description="[deprecated] Use request body instead"),
):
    """
    Phase 6: Convert parsed LoadScript to Power Query M expressions.

    Accepts a JSON request body (recommended — avoids HTTP 431 for large scripts).
    Legacy query parameters are also accepted for backward compatibility.

    If table_name is empty, returns M expressions for ALL tables.

    base_path priority: request body → query param → DATA_SOURCE_PATH env var → [DataSourcePath]
    """
    # Body takes priority over legacy query params
    _parsed_json    = request.parsed_script_json or parsed_script_json_q
    _table_name     = request.table_name         or table_name_q
    _base_path      = request.base_path or base_path_q or os.getenv("DATA_SOURCE_PATH", "[DataSourcePath]")
    _connection_str = request.connection_string  or connection_string_q

    logger.info("=" * 80)
    logger.info("[convert_to_mquery_endpoint] ENDPOINT: /convert-to-mquery")
    logger.info("[convert_to_mquery_endpoint] Table: %s", _table_name or "(all)")
    logger.info("[convert_to_mquery_endpoint] base_path: %s", _base_path)
    logger.info("=" * 80)

    # 1. Parse the JSON payload
    try:
        parse_result: Dict[str, Any] = json.loads(_parsed_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON in parsed_script_json: {exc}")

    # 2. Extract tables list
    tables: List[Dict[str, Any]] = (
        parse_result.get("details", {}).get("tables", [])
        or parse_result.get("tables", [])
    )
    raw_script: str = parse_result.get("raw_script", "")

    if not tables and not raw_script:
        raise HTTPException(
            status_code=400,
            detail="No tables found in parsed_script_json. Re-run /parse-loadscript first."
        )

    # 3. Convert using MQueryConverter (handles all source types including RESIDENT)
    try:
        from mquery_converter import MQueryConverter
        converter = MQueryConverter()
        all_table_names = {t["name"] for t in tables}

        if _table_name:
            # Single-table mode
            target = next(
                (t for t in tables if t["name"] == _table_name),
                None
            )
            if not target:
                # Case-insensitive fallback
                target = next(
                    (t for t in tables if t["name"].lower() == _table_name.lower()),
                    None
                )
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Table '{_table_name}' not found. "
                        f"Available tables: {sorted(all_table_names)}"
                    )
                )

            m_expr = converter.convert_one(
                target,
                base_path=_base_path,
                connection_string=_connection_str or None,
                all_table_names=all_table_names,
            )

            # For RESIDENT tables, include the source table's M as a dependency
            dep_queries: Dict[str, str] = {}
            if target.get("source_type") == "resident":
                src_name = target.get("source_path", "")
                src_table = next((t for t in tables if t["name"] == src_name), None)
                if src_table:
                    dep_queries[src_name] = converter.convert_one(
                        src_table,
                        base_path=_base_path,
                        connection_string=_connection_str or None,
                        all_table_names=all_table_names,
                    )

            resident_note = (
                f" [!] RESIDENT table -- also include '{target.get('source_path')}' query in your dataset."
                if target.get("source_type") == "resident" else ""
            )

            logger.info(
                "[convert_to_mquery_endpoint] [OK] Converted table '%s' [%s]",
                _table_name, target.get("source_type", "")
            )

            return {
                "status":             "success",
                "table_name":         _table_name,
                "source_type":        target.get("source_type", "unknown"),
                "m_query":            m_expr,
                "query_length":       len(m_expr),
                "dependency_queries": dep_queries,
                "message":            f"M Query generated for '{_table_name}'.{resident_note}",
                "statistics": {
                    "total_tables_available": len(tables),
                    "resident_dependencies":  len(dep_queries),
                },
            }

        else:
            # All-tables mode -- convert every table
            all_converted = converter.convert_all(
                tables,
                base_path=_base_path,
                connection_string=_connection_str or None,
            )

            # Build a combined M script: each table as a named section
            parts = []
            for item in all_converted:
                parts.append(
                    f"// \n"
                    f"// Table: {item['name']}  [{item['source_type']}]\n"
                    f"// \n"
                    f"{item['m_expression']}"
                )
            combined_m = "\n\n".join(parts)

            resident_tables = [t for t in all_converted if t["source_type"] == "resident"]

            logger.info(
                "[convert_to_mquery_endpoint] [OK] Converted %d tables (%d RESIDENT)",
                len(all_converted), len(resident_tables)
            )

            return {
                "status":       "success",
                "table_name":   "",
                "m_query":      combined_m,
                "query_length": len(combined_m),
                "all_tables":   all_converted,
                "message":      (
                    f"M Query generated for all {len(all_converted)} table(s)."
                    + (
                        f" Note: {len(resident_tables)} RESIDENT table(s) -- "
                        "ensure all source queries are included in your dataset."
                        if resident_tables else ""
                    )
                ),
                "statistics": {
                    "total_tables_converted":    len(all_converted),
                    "total_fields_converted":    sum(len(t.get("fields", [])) for t in tables),
                    "resident_tables":           len(resident_tables),
                },
            }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[convert_to_mquery_endpoint] Conversion failed")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {exc}")


@router.post("/full-pipeline")
async def full_pipeline(
    app_id:            str = Query(..., description="Qlik Cloud app ID"),
    table_name:        str = Query("", description="Specific table to convert (empty = all tables)"),
    base_path:         str = Query("[DataSourcePath]", description="Base path for file sources"),
    connection_string: str = Query("", description="ODBC connection string for SQL sources"),
    auto_download:     bool = Query(False, description="Not used -- kept for compatibility"),
):
    """
    Full pipeline: Fetch LoadScript -> Parse -> Convert to M Query.
    Equivalent to calling fetch-loadscript -> parse-loadscript -> convert-to-mquery in sequence.
    Returns the complete M Query plus pipeline diagnostics.
    """
    logger.info("=" * 80)
    logger.info("[full_pipeline] ENDPOINT: /full-pipeline  app_id=%s table=%s", app_id, table_name or "(all)")
    logger.info("=" * 80)

    try:
        # Phase 4: Fetch
        from loadscript_fetcher import LoadScriptFetcher
        fetcher = LoadScriptFetcher()
        fetch_result = fetcher.fetch_loadscript(app_id)
        if fetch_result.get("status") not in ("success", "partial_success"):
            raise HTTPException(
                status_code=503,
                detail=f"Fetch failed: {fetch_result.get('message', 'Unknown error')}"
            )
        loadscript = fetch_result.get("loadscript", "")

        # Phase 5: Parse
        from loadscript_parser import LoadScriptParser
        parse_result = LoadScriptParser(loadscript).parse()
        tables = parse_result.get("details", {}).get("tables", [])

        # Phase 6: Convert
        from mquery_converter import MQueryConverter
        converter = MQueryConverter()
        all_table_names = {t["name"] for t in tables}

        if table_name:
            target = next((t for t in tables if t["name"] == table_name), None)
            if not target:
                target = next((t for t in tables if t["name"].lower() == table_name.lower()), None)
            if not target:
                raise HTTPException(
                    status_code=404,
                    detail=f"Table '{table_name}' not found. Available: {sorted(all_table_names)}"
                )
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

        logger.info("[full_pipeline] [OK] Pipeline complete -- %d tables, query length %d", len(tables), len(m_query))

        return {
            "status":    "success",
            "app_id":    app_id,
            "app_name":  fetch_result.get("app_name", ""),
            "m_query":   m_query,
            "phases": {
                "fetch": {
                    "method":        fetch_result.get("method"),
                    "script_length": len(loadscript),
                },
                "parse": {
                    "tables_count":  len(tables),
                    "fields_count":  parse_result.get("summary", {}).get("fields_count", 0),
                },
                "convert": {
                    "table_requested": table_name or "(all)",
                    "query_length":    len(m_query),
                },
            },
            "summary": {
                "total_tables_converted": len(tables),
                "query_length":           len(m_query),
            },
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[full_pipeline] Failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ===========================================================================
# PUBLISH M QUERY -> POWER BI  (simple REST Push dataset)
# Called by the "Publish MQuery to PowerBI" button in the frontend.
# Uses service principal credentials from .env -- no user login required.
# ===========================================================================


# ─────────────────────────────────────────────────────────────────────────────
# Relationship inference helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tables_m_to_extractor_format(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert tables_m (from parse_combined_mquery / MQueryConverter) into the
    format that RelationshipExtractor expects.

    tables_m["fields"] is a list of dicts:
        [{"name": "VIN", "type": "string", ...}, ...]

    RelationshipExtractor["fields"] must be a list of plain strings:
        ["VIN", "ModelName", ...]

    We use alias if set (explicit AS rename), otherwise the raw field name.
    This must match exactly what ends up as the column header in the BIM/M
    expression, because relationship column refs are validated against those names.
    """
    result = []
    for t in tables_m:
        raw_fields = t.get("fields", [])
        if raw_fields and isinstance(raw_fields[0], dict):
            # Dict format from loadscript_parser / mquery_converter
            field_names = [
                f.get("alias") or f.get("name", "")
                for f in raw_fields
                if f.get("name") and f.get("name") != "*"
            ]
        else:
            # Already strings
            field_names = [f for f in raw_fields if f and f != "*"]

        result.append({
            "name":        t["name"],
            "source_type": t.get("source_type", ""),
            "fields":      field_names,
        })
    return result


# def _is_qlik_key_column(col_name: str, table_name: str) -> bool:
#     """
#     Return True only if a column name looks like a genuine Qlik key field.

#     Qlik auto-associates tables via shared field names. Only fields that are
#     intentional keys should drive Power BI relationships. We identify keys by:

#     1. Qualified name:  TableName.FieldName  (explicit Qlik cross-table ref)
#     2. ID suffix:       ends with ID, Id, _ID, _Id  (e.g. DealerID, VehicleID)
#     3. Key suffix:      ends with Key, _Key, _key
#     4. Explicit prefix: starts with another table name  (e.g. Model_Master.ModelID)
#     5. Common PK patterns: VIN, SKU, Code, Ref, No  (domain-specific short keys)

#     Explicitly EXCLUDED (too generic — cause false positives):
#       Brand, Model, City, Status, Type, Name, Date, Year, Price,
#       Mileage, Route*, Capacity*, Distance*, Weight*, Speed*, Fuel*,
#       Load*, Last*, Engine*, Ticket*, Captain*, Driver*, Boat*, Truck*
#     """
#     col = col_name.strip()
#     col_lower = col.lower()

#     # Rule 1 — Qualified Qlik field: TableName.FieldName
#     if "." in col:
#         return True

#     # Rule 2 — Ends with ID / Id (but not words that happen to end in 'id')
#     if re.search(r'(?i)(ID|_Id)$', col) and len(col) > 2:
#         return True

#     # Rule 3 — Ends with Key
#     if re.search(r'(?i)(_?key)$', col):
#         return True

#     # Rule 4 — Starts with a known table name prefix (TableName.FieldName sans dot)
#     # e.g. "DealerCity" where "Dealer" is a table — too ambiguous, skip

#     # Rule 5 — Very short well-known surrogate keys
#     if col_lower in ("vin", "sku", "isbn", "ean", "upc"):
#         return True

#     return False


# Columns that are never relationship keys regardless of name
# _GENERIC_COLUMNS = frozenset([
#     "brand", "model", "city", "status", "type", "name", "date", "year",
#     "price", "mileage", "routefrom", "routeto", "capacity", "distance",
#     "weight", "speed", "fueltype", "fuel", "loadtype", "lastservicedate",
#     "enginecc", "ticketprice", "captainname", "drivername", "boatname",
#     "trucknumber", "cyclename", "startdate", "enddate", "description",
#     "address", "phone", "email", "country", "state", "region", "zone",
#     "category", "subcategory", "segment", "channel", "department",
# ])


def _infer_relationships_from_tables(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Auto-infer Power BI relationships from tables_m.

    Only considers columns that follow Qlik key naming conventions:
      - Qualified names (TableName.FieldName)
      - Columns ending in ID, Key
      - Known surrogate key names (VIN, SKU, etc.)

    Generic descriptive columns (Brand, Model, City, Status, etc.) are
    explicitly excluded to prevent false-positive relationships between
    unrelated tables that happen to share common column names.

    Returns a list of BIM-compatible relationship dicts.
    """
    from relationship_extractor import RelationshipExtractor
    from relationship_normalizer import RelationshipNormalizer

    # Convert field format and filter to key columns only
    extractor_tables = []
    for t in tables_m:
        raw_fields = t.get("fields", [])
        if raw_fields and isinstance(raw_fields[0], dict):
            field_names = [
                f.get("alias") or f.get("name", "")
                for f in raw_fields
                if f.get("name") and f.get("name") != "*"
            ]
        else:
            field_names = [f for f in raw_fields if f and f != "*"]

        # Keep only genuine key columns
        # key_fields = [
        #     f for f in field_names
        #     if f.lower() not in _GENERIC_COLUMNS
        #     and _is_qlik_key_column(f, t["name"])
        # ]

        # if key_fields:
        #     extractor_tables.append({
        #         "name":        t["name"],
        #         "source_type": t.get("source_type", ""),
        #         "fields":      key_fields,
        #     })
        if field_names:
            extractor_tables.append({
                "name":        t["name"],
                "source_type": t.get("source_type", ""),
                "fields":      field_names,
            })
    if not extractor_tables:
        return []
    #Below is the block of code for relationship - 06th march
    # from collections import Counter

    # # Step 1: Extract and normalise field names per table
    # # Keep a mapping: normalised_name -> original_name for relationship output
    # all_table_fields = []
    # for t in tables_m:
    #     raw_fields = t.get("fields", [])
    #     if raw_fields and isinstance(raw_fields[0], dict):
    #         field_names = [
    #             f.get("alias") or f.get("name", "")
    #             for f in raw_fields
    #             if f.get("name") and f.get("name") != "*"
    #         ]
    #     else:
    #         field_names = [f for f in raw_fields if f and f != "*"]

    #     # Build mapping: normalised -> original
    #     # "Model_Master.ModelID" -> {"ModelID": "Model_Master.ModelID"}
    #     # Only keep the first mapping if duplicates exist after stripping
    #     norm_to_orig = {}
    #     for f in field_names:
    #         base = f.split(".")[-1] if "." in f else f
    #         if base not in norm_to_orig:
    #             norm_to_orig[base] = f

    #     all_table_fields.append((t, norm_to_orig))

    # # Step 2: Count how many tables each normalised field appears in
    # field_table_count = Counter()
    # for _, norm_to_orig in all_table_fields:
    #     field_table_count.update({k.lower() for k in norm_to_orig})

    # OMNIPRESENT_THRESHOLD = 3
    # omnipresent = {
    #     f for f, count in field_table_count.items()
    #     if count >= OMNIPRESENT_THRESHOLD
    # }

    # # Step 3: Build extractor input using NORMALISED names for matching
    # # but store orig_fields mapping so we can restore original names after
    # extractor_tables = []
    # orig_name_map = {}   # (table_name, normalised_field) -> original_field_name
    # for t, norm_to_orig in all_table_fields:
    #     key_fields = [k for k in norm_to_orig if k.lower() not in omnipresent]
    #     if key_fields:
    #         extractor_tables.append({
    #             "name":        t["name"],
    #             "source_type": t.get("source_type", ""),
    #             "fields":      key_fields,   # normalised names for matching
    #         })
    #         for k in key_fields:
    #             orig_name_map[(t["name"], k)] = norm_to_orig[k]  # restore later

    # if not extractor_tables:
    #     return []
    # Run extraction on key fields only
    extractor = RelationshipExtractor(extractor_tables)
    raw_rels = extractor.extract()

    # Filter out manyToMany — Power BI requires unique keys on the one-side
    valid_rels = [r for r in raw_rels if r.get("cardinality") != "manyToMany"]

    if not valid_rels:
        return []

    normalized = RelationshipNormalizer.normalize_list(valid_rels)
#This return block is commented for relationship - 06th march
    return [
        {
            "fromTable":              n.from_table,
            "fromColumn":             n.from_column,
            "toTable":                n.to_table,
            "toColumn":               n.to_column,
            "cardinality":            n.cardinality,
            "crossFilteringBehavior": n.direction,
        }
        for n in normalized
    ]
    # return [
    #     {
    #         "fromTable":              n.from_table,
    #         "fromColumn":             orig_name_map.get((n.from_table, n.from_column), n.from_column),
    #         "toTable":                n.to_table,
    #         "toColumn":               orig_name_map.get((n.to_table, n.to_column), n.to_column),
    #         "cardinality":            n.cardinality,
    #         "crossFilteringBehavior": n.direction,
    #     }
    #     for n in normalized
    # ]
#Relationship block ends here - 06th march
class PublishMQueryRequest(_BaseModel):
    dataset_name:         str  = "Qlik_Migrated_Dataset"
    combined_mquery:      str  = ""   # combined M Query from Convert to MQuery button
    raw_script:           str  = ""   # fallback: raw Qlik LoadScript
    access_token:         str  = ""   # user-delegated token (if already acquired via login)
    data_source_path:     str  = ""   # optional: default DataSourcePath value
    db_connection_string: str  = ""   # optional: ODBC/SQL Server connection string
    relationships:        list = []   # optional: [{fromTable,fromColumn,toTable,toColumn}]

@router.post("/publish-mquery")
async def publish_mquery_endpoint(
    request: PublishMQueryRequest,
):
    """
    Publish M Query to Power BI as a full semantic model.

    Strategy (tried in order):
      1. Fabric Items API  -- creates real semantic model with M queries (PPU/Fabric)
      2. Push dataset      -- fallback, works on any workspace (limited Model View)

    Auth for PPU workspaces:
      - Provide access_token (user-delegated) in request body, OR
      - Set POWERBI_USER + POWERBI_PASSWORD in .env for silent ROPC login, OR
      - If neither available, returns auth_required=true with device code URL.

    Data source options:
      - data_source_path      : sets the DataSourcePath parameter default in M queries
      - db_connection_string  : rewrites CSV sources to ODBC (e.g. SQL Server)
    """
    dataset_name = request.dataset_name or "Qlik_Migrated_Dataset"
    combined_m   = (request.combined_mquery or "").replace("\r\n", "\n").replace("\r", "\n")
    raw_script   = request.raw_script or ""

    logger.info("=" * 70)
    logger.info("[publish_mquery] ENDPOINT: /publish-mquery")
    logger.info("[publish_mquery] Dataset: %s", dataset_name)
    logger.info("=" * 70)

    workspace_id = os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        raise HTTPException(status_code=400, detail="POWERBI_WORKSPACE_ID not set in .env file.")

    if not combined_m and not raw_script:
        raise HTTPException(status_code=400, detail="Provide combined_mquery or raw_script.")

    # Parse M Query or LoadScript into table list
    try:
        from pbit_generator import parse_combined_mquery
        if combined_m.strip():
            tables_m = parse_combined_mquery(combined_m)
            # Enrich with field metadata — extract from the M expression itself.
            # parse_combined_mquery only returns {name, source_type, m_expression}
            # with no fields. We need fields populated for relationship inference.
            # Primary: parse from the M expression using _extract_fields_from_m.
            # Fallback: if raw_script also provided, use loadscript_parser for richer types.
            try:
                from powerbi_publisher import _extract_fields_from_m
                for t in tables_m:
                    if not t.get("fields"):
                        extracted = _extract_fields_from_m(t.get("m_expression", ""))
                        # Convert from [{name, type}] dicts to the format
                        # _tables_m_to_extractor_format() expects
                        t["fields"] = extracted  # list of {"name": ..., "type": ...}
                        if extracted:
                            logger.info(
                                "[publish_mquery] Extracted %d fields from M for table '%s': %s",
                                len(extracted), t["name"],
                                [f["name"] for f in extracted]
                            )
            except Exception as extract_exc:
                logger.warning("[publish_mquery] M-expression field extraction failed: %s", extract_exc)

            # Secondary enrichment: if raw_script also sent, use loadscript_parser
            # for better type information (overrides M-extracted types)
            if raw_script:
                try:
                    from loadscript_parser import LoadScriptParser
                    from mquery_converter import MQueryConverter
                    parse_result  = LoadScriptParser(raw_script).parse()
                    raw_tables    = parse_result.get("details", {}).get("tables", [])
                    all_converted = MQueryConverter().convert_all(raw_tables)
                    fields_by_name = {t["name"]: t.get("fields", []) for t in all_converted}
                    for t in tables_m:
                        if fields_by_name.get(t["name"]):
                            t["fields"] = fields_by_name[t["name"]]
                except Exception as enrich_exc:
                    logger.warning("[publish_mquery] LoadScript field enrichment failed: %s", enrich_exc)

            QLIK_SYSTEM_PREFIXES = ("__city", "__geo", "__key", "AutoCalendar", "MasterCalendar")
            before = len(tables_m)
            tables_m = [t for t in tables_m if not t["name"].startswith(QLIK_SYSTEM_PREFIXES)]
            logger.info("[publish_mquery] Parsed combined M Query: %d tables (%d system tables filtered)", len(tables_m), before - len(tables_m))
        else:
            from loadscript_parser import LoadScriptParser
            from mquery_converter import MQueryConverter
            parse_result = LoadScriptParser(raw_script).parse()
            raw_tables   = parse_result.get("details", {}).get("tables", [])
            converter    = MQueryConverter()
            all_converted = converter.convert_all(raw_tables, base_path="[DataSourcePath]")
            tables_m = [{"name": t["name"], "source_type": t["source_type"],
                         "m_expression": t["m_expression"],
                         "fields": t.get("fields", [])} for t in all_converted]
            QLIK_SYSTEM_PREFIXES = ("__city", "__geo", "__key", "AutoCalendar", "MasterCalendar")
            before = len(tables_m)
            tables_m = [t for t in tables_m if not t["name"].startswith(QLIK_SYSTEM_PREFIXES)]
            logger.info("[publish_mquery] Converted LoadScript: %d tables (%d system tables filtered)", len(tables_m), before - len(tables_m))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Script parse/convert error: {exc}")

    if not tables_m:
        raise HTTPException(status_code=400, detail="No tables found in the provided script.")

    # ── Relationship inference ────────────────────────────────────────────────
    # If the frontend sent explicit relationships, use them.
    # Otherwise auto-infer from the parsed tables using Qlik naming conventions:
    #   • Shared field names that look like keys (end in ID, Key, VIN, etc.)
    #   • Qualified fields TableName.FieldName that cross-reference another table
    relationships = request.relationships or []
    if not relationships:
        try:
            relationships = _infer_relationships_from_tables(tables_m)
            if relationships:
                logger.info(
                    "[publish_mquery] Auto-inferred %d relationship(s) from table fields",
                    len(relationships),
                )
                for r in relationships:
                    logger.info(
                        "[publish_mquery]   %s.%s -> %s.%s",
                        r["fromTable"], r["fromColumn"],
                        r["toTable"],   r["toColumn"],
                    )
        except Exception as rel_exc:
            logger.warning("[publish_mquery] Relationship inference failed: %s", rel_exc)

    # Publish via new publisher (TMSL/Fabric API -> Push dataset fallback)
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
        )

        # If auth required (PPU device code flow), return 200 with auth_required flag
        if result.get("auth_required"):
            return {
                "success": False,
                "auth_required": True,
                "user_code":       result.get("user_code"),
                "device_code_url": result.get("device_code_url"),
                "message":         result.get("message", ""),
                "instructions":    (
                    f"1. Open {result.get('device_code_url')} in your browser\n"
                    f"2. Enter code: {result.get('user_code')}\n"
                    f"3. Sign in with your Power BI account\n"
                    f"4. Click Publish again"
                ),
            }

        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "Publish failed"))

        logger.info("[publish_mquery] [OK] Published via %s", result.get("method"))
        return {
            "success":         True,
            "dataset_id":      result.get("dataset_id", ""),
            "dataset_name":    dataset_name,
            "tables_deployed": len(tables_m),
            "method":          result.get("method", ""),
            "workspace_url":   result.get("workspace_url", ""),
            "message":         result.get("message", f"Published {dataset_name} to Power BI"),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[publish_mquery] Publish failed")
        raise HTTPException(status_code=500, detail=f"Publish failed: {exc}")


class GeneratePbitRequest(_BaseModel):
    dataset_name:          str = "Qlik_Migrated_Dataset"
    combined_mquery:       str = ""     # combined M Query text (all tables)
    raw_script:            str = ""     # fallback: raw Qlik LoadScript
    data_source_path:      str = ""     # default value for DataSourcePath parameter
    relationships:         list = []    # [{from_table, from_column, to_table, to_column}]


@router.post("/generate-pbit")
async def generate_pbit_endpoint(request: GeneratePbitRequest):
    """
    Generate a Power BI Template (.pbit) file from a combined M Query string.

    The .pbit opens directly in Power BI Desktop -- each Qlik table becomes
    a separate Query with its M expression. DataSourcePath is exposed as a
    Query Parameter so users can point it at their data folder once.

    Accepts JSON body:
      {
        "dataset_name":     "MyDataset",
        "combined_mquery":  "// Table: Orders [csv]\\nlet\\n...",
        "data_source_path": "C:/Data",    // optional default for the parameter
        "relationships":    []            // optional
      }

    Returns: base64-encoded .pbit file + metadata.
    """
    import base64

    logger.info("[generate_pbit] Dataset: %s", request.dataset_name)

    combined_m = request.combined_mquery or ""
    raw_script  = request.raw_script or ""

    if not combined_m and not raw_script:
        raise HTTPException(
            status_code=400,
            detail="Provide either combined_mquery (from Convert to MQuery) or raw_script."
        )

    try:
        from pbit_generator import parse_combined_mquery, build_pbit

        # If we have a combined M query, parse it directly
        if combined_m.strip():
            tables_m = parse_combined_mquery(combined_m)
            if not tables_m:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Could not parse table sections from combined_mquery. "
                        "Make sure it was generated by the Convert to MQuery button "
                        "(expects '// Table: Name [type]' headers)."
                    )
                )
        else:
            # Fallback: run the full conversion pipeline from raw script
            from loadscript_parser import LoadScriptParser
            from mquery_converter import MQueryConverter

            parse_result = LoadScriptParser(raw_script).parse()
            tables = parse_result.get("details", {}).get("tables", [])
            if not tables:
                raise HTTPException(status_code=400, detail="No tables found in raw_script.")

            converter = MQueryConverter()
            all_table_names = {t["name"] for t in tables}
            all_converted = converter.convert_all(
                tables,
                base_path="[DataSourcePath]",
                connection_string=None,
            )
            tables_m = [
                {"name": t["name"], "source_type": t["source_type"], "m_expression": t["m_expression"]}
                for t in all_converted
            ]

        logger.info("[generate_pbit] Building .pbit with %d tables", len(tables_m))

        pbit_bytes = build_pbit(
            tables_m=tables_m,
            dataset_name=request.dataset_name,
            relationships=request.relationships or [],
            data_source_path_default=request.data_source_path or "",
        )

        pbit_b64 = base64.b64encode(pbit_bytes).decode("ascii")
        safe_name = re.sub(r"[^\w\-]", "_", request.dataset_name)

        logger.info("[generate_pbit] [OK] .pbit generated: %d bytes, %d tables", len(pbit_bytes), len(tables_m))

        return {
            "success":        True,
            "dataset_name":   request.dataset_name,
            "filename":       f"{safe_name}.pbit",
            "tables_count":   len(tables_m),
            "tables":         [{"name": t["name"], "source_type": t["source_type"]} for t in tables_m],
            "file_size_bytes": len(pbit_bytes),
            "pbit_base64":    pbit_b64,
            "message": (
                f"[OK] {request.dataset_name}.pbit generated with {len(tables_m)} table(s). "
                "Open in Power BI Desktop -> set DataSourcePath parameter -> Refresh."
            ),
            "instructions": [
                "1. Download the .pbit file",
                "2. Open it in Power BI Desktop",
                "3. When prompted for 'DataSourcePath', enter your data folder path (e.g. C:/Data)",
                "4. Click Refresh -- Power BI will load all tables from the CSVs",
                "5. Publish to Power BI Service from Desktop",
            ],
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[generate_pbit] Failed")
        raise HTTPException(status_code=500, detail=f"PBIT generation failed: {exc}")


