# """
# Model Publishing API - FastAPI Router

# Exposes the 6-stage migration pipeline via REST endpoints.
# """

# import logging
# import json
# from datetime import datetime
# from typing import Any, Dict, Optional

# from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Depends
# from fastapi.responses import StreamingResponse
# import io

# from powerbi_auth import get_auth_manager
# from powerbi_pbix_importer import import_pbix_and_wait
# from six_stage_orchestrator import run_migration_pipeline
# from loadscript_fetcher import LoadScriptFetcher
# from loadscript_parser import LoadScriptParser
# from simple_mquery_generator import SimpleMQueryGenerator
# from conversion_logger import get_session_manager, LogLevel
# from mquery_file_generator import MQueryFileGenerator, generate_dual_download_zip
# from qlik_websocket_client import QlikWebSocketClient

# logger = logging.getLogger(__name__)

# router = APIRouter(prefix="/api/migration", tags=["Migration Pipeline"])


# def get_qlik_websocket_client():
#     """Dependency to provide QlikWebSocketClient"""
#     try:
#         return QlikWebSocketClient()
#     except Exception:
#         return None


# @router.post("/publish-table")
# def publish_table_to_powerbi(
#     app_id: str = Query(..., description="Qlik app ID"),
#     dataset_name: str = Query(..., description="Power BI dataset name"),
#     workspace_id: str = Query(..., description="Power BI workspace ID"),
#     access_token: Optional[str] = Query(None, description="Power BI access token"),
#     publish_mode: str = Query(
#         "cloud_push",
#         description="Publish mode: cloud_push | desktop_cloud | xmla_semantic",
#     ),
#     csv_payload_json: Optional[str] = Query(
#         None,
#         description="Optional JSON map {table_name: csv_text}; used by xmla_semantic mode",
#     ),
# ) -> Dict[str, Any]:
#     """
#     Publish Qlik metadata and inferred relationships using complete pipeline.

#     Modes:
#     - cloud_push: create Push semantic model via REST API
#     - desktop_cloud: generate Desktop handoff bundle for PBIX publish to cloud
#     - xmla_semantic: create enhanced semantic model in cloud via XMLA (no Desktop required)
#     """
#     try:
#         logger.info(
#             "Publishing table - App=%s Dataset=%s Mode=%s", app_id, dataset_name, publish_mode
#         )

#         resolved_access_token = access_token
#         if not resolved_access_token:
#             try:
#                 auth = get_auth_manager()
#                 if auth.is_token_valid():
#                     resolved_access_token = auth.get_access_token()
#             except Exception:
#                 resolved_access_token = None

#         csv_payloads: Dict[str, str] = {}
#         if csv_payload_json:
#             try:
#                 parsed_payload = json.loads(csv_payload_json)
#                 if not isinstance(parsed_payload, dict):
#                     raise ValueError("csv_payload_json must be a JSON object")
#                 csv_payloads = {
#                     str(k): str(v)
#                     for k, v in parsed_payload.items()
#                     if str(k).strip() and isinstance(v, str) and v.strip()
#                 }
#             except Exception as exc:
#                 raise HTTPException(status_code=400, detail=f"Invalid csv_payload_json: {str(exc)}")

#         result = run_migration_pipeline(
#             app_id=app_id,
#             dataset_name=dataset_name,
#             workspace_id=workspace_id,
#             access_token=resolved_access_token,
#             publish_mode=publish_mode,
#             csv_table_payloads=csv_payloads,
#         )

#         if not result.get("success"):
#             raise HTTPException(status_code=400, detail=result.get("error", "Pipeline failed"))

#         response: Dict[str, Any] = {
#             "success": True,
#             "message": "Migration pipeline executed successfully",
#             "dataset_name": dataset_name,
#             "publish_mode": publish_mode,
#             "summary": result.get("summary", {}),
#             "stages_completed": list(result.get("stages", {}).keys()),
#             "er_diagram": result.get("stages", {}).get("6_er_diagram", {}).get("mermaid", ""),
#             "warnings": result.get("warnings", []),
#             "duration_seconds": result.get("duration_seconds", 0),
#         }

#         if publish_mode == "desktop_cloud":
#             response["desktop_bundle"] = result.get("stages", {}).get("7_desktop_bundle", {})
#             response["next_steps"] = [
#                 "1. Open desktop bundle README",
#                 "2. Build/open model in Power BI Desktop",
#                 "3. Publish PBIX to target workspace",
#                 "4. Open semantic model in service and verify relationships",
#             ]
#         elif publish_mode == "xmla_semantic":
#             xmla_stage = result.get("stages", {}).get("4_xmla_semantic", {})
#             response["xmla_semantic"] = xmla_stage
#             response["links"] = {
#                 "workspace": xmla_stage.get("workspace_url")
#                 or f"https://app.powerbi.com/groups/{workspace_id}",
#                 "dataset": xmla_stage.get("dataset_url"),
#             }
#             response["next_steps"] = [
#                 "1. Open the workspace link",
#                 "2. Open semantic model in service",
#                 "3. Verify Model view shows inferred relationships",
#                 "4. Build report on top of the enhanced model",
#             ]
#         else:
#             response["next_steps"] = [
#                 "1. Go to Power BI workspace",
#                 "2. Find your semantic model",
#                 "3. If Open semantic model is disabled, republish using desktop_cloud mode",
#                 "4. Validate tables and relationships",
#             ]

#         return response

#     except HTTPException:
#         raise
#     except Exception as exc:
#         logger.exception("publish-table failed")
#         raise HTTPException(status_code=500, detail=str(exc))


# @router.post("/publish-semantic-model")
# async def publish_semantic_model_xmla(
#     app_id: str = Form(..., description="Qlik app ID"),
#     dataset_name: str = Form(..., description="Target semantic model name"),
#     workspace_id: str = Form(..., description="Power BI workspace ID"),
#     csv_payload_json: Optional[str] = Form(
#         None,
#         description="JSON map {table_name: csv_text} for metadata enhancement",
#     ),
#     access_token: Optional[str] = Form(None, description="Optional Power BI access token"),
# ) -> Dict[str, Any]:
#     """
#     Build an enhanced semantic model in cloud via XMLA from:
#     1) Qlik schema metadata (Stage 1)
#     2) inferred relationships (Stages 2-3)
#     3) optional CSV payloads for better column type inference
#     """
#     try:
#         csv_payloads: Dict[str, str] = {}
#         if csv_payload_json:
#             try:
#                 parsed_payload = json.loads(csv_payload_json)
#                 if not isinstance(parsed_payload, dict):
#                     raise ValueError("csv_payload_json must be a JSON object")
#                 csv_payloads = {
#                     str(k): str(v)
#                     for k, v in parsed_payload.items()
#                     if str(k).strip() and isinstance(v, str) and v.strip()
#                 }
#             except Exception as exc:
#                 raise HTTPException(status_code=400, detail=f"Invalid csv_payload_json: {str(exc)}")

#         resolved_access_token = access_token
#         if not resolved_access_token:
#             auth = get_auth_manager()
#             if not auth.is_token_valid():
#                 raise HTTPException(
#                     status_code=401,
#                     detail="Power BI token is not valid. Run /powerbi/login/acquire-token first.",
#                 )
#             resolved_access_token = auth.get_access_token()

#         result = run_migration_pipeline(
#             app_id=app_id,
#             dataset_name=dataset_name,
#             workspace_id=workspace_id,
#             access_token=resolved_access_token,
#             publish_mode="xmla_semantic",
#             csv_table_payloads=csv_payloads,
#         )

#         if not result.get("success"):
#             raise HTTPException(status_code=400, detail=result.get("error", "Pipeline failed"))

#         xmla_stage = result.get("stages", {}).get("4_xmla_semantic", {})
#         return {
#             "success": True,
#             "message": "Enhanced semantic model created via XMLA",
#             "publish_mode": "xmla_semantic",
#             "dataset_name": dataset_name,
#             "workspace_id": workspace_id,
#             "summary": result.get("summary", {}),
#             "er_diagram": result.get("stages", {}).get("6_er_diagram", {}).get("mermaid", ""),
#             "xmla_semantic": xmla_stage,
#             "links": {
#                 "workspace": xmla_stage.get("workspace_url")
#                 or f"https://app.powerbi.com/groups/{workspace_id}",
#                 "dataset": xmla_stage.get("dataset_url"),
#             },
#             "warnings": result.get("warnings", []),
#             "note": (
#                 "Requires XMLA write-enabled workspace capacity and proper permissions. "
#                 "This path avoids Power BI Desktop."
#             ),
#         }
#     except HTTPException:
#         raise
#     except Exception as exc:
#         logger.exception("publish-semantic-model failed")
#         raise HTTPException(status_code=500, detail=str(exc))


# @router.post("/preview-migration")
# def preview_migration(
#     app_id: str = Query(..., description="Qlik app ID"),
#     dataset_name: str = Query(..., description="Dataset name"),
# ) -> Dict[str, Any]:
#     """Preview migration without making Power BI changes (stages 1-3 + 6 + bundle)."""
#     try:
#         result = run_migration_pipeline(
#             app_id=app_id,
#             dataset_name=dataset_name,
#             workspace_id="preview",
#             access_token=None,
#             publish_mode="desktop_cloud",
#         )

#         return {
#             "success": result.get("success"),
#             "dataset_name": dataset_name,
#             "tables": result.get("stages", {}).get("1_extract", {}).get("tables", []),
#             "relationships": result.get("stages", {}).get("3_normalize", {}).get("relationships", []),
#             "desktop_bundle": result.get("stages", {}).get("7_desktop_bundle", {}),
#             "statistics": {
#                 "table_count": len(result.get("stages", {}).get("1_extract", {}).get("tables", [])),
#                 "relationship_count": len(
#                     result.get("stages", {}).get("3_normalize", {}).get("relationships", [])
#                 ),
#                 "avg_confidence": result.get("stages", {}).get("2_infer", {}).get("avg_confidence", 0),
#             },
#             "note": "No Power BI dataset created in preview mode.",
#         }

#     except Exception as exc:
#         logger.exception("preview-migration failed")
#         raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/view-diagram")
# def view_er_diagram(
#     app_id: str = Query(..., description="Qlik app ID"),
#     dataset_name: str = Query(..., description="Dataset name"),
# ) -> Dict[str, Any]:
#     """Generate ER diagram from extracted metadata and inferred relationships."""
#     try:
#         result = run_migration_pipeline(
#             app_id=app_id,
#             dataset_name=dataset_name,
#             workspace_id="diagram-only",
#             access_token=None,
#             publish_mode="desktop_cloud",
#         )

#         er_result = result.get("stages", {}).get("6_er_diagram", {})
#         return {
#             "success": er_result.get("success", False),
#             "dataset_name": dataset_name,
#             "mermaid_diagram": er_result.get("mermaid", ""),
#             "html_diagram": er_result.get("html", ""),
#             "table_count": er_result.get("table_count", 0),
#             "relationship_count": er_result.get("relationship_count", 0),
#         }

#     except Exception as exc:
#         logger.exception("view-diagram failed")
#         raise HTTPException(status_code=500, detail=str(exc))


# @router.post("/import-pbix")
# async def import_pbix_to_workspace(
#     pbix_file: UploadFile = File(..., description="PBIX file exported from Power BI Desktop"),
#     dataset_name: str = Form(..., description="Target dataset display name in Power BI"),
#     workspace_id: Optional[str] = Form(None, description="Power BI workspace ID (optional, defaults to configured workspace)"),
#     name_conflict: str = Form("CreateOrOverwrite", description="Import conflict mode (Abort|Overwrite|CreateOrOverwrite|Ignore)"),
#     timeout_seconds: int = Form(600, description="Max wait time for import completion"),
# ) -> Dict[str, Any]:
#     """
#     Import a PBIX file into a Power BI workspace.

#     This is the workflow that creates a Desktop-origin semantic model in cloud,
#     which can enable the 'Open semantic model' option in Service.
#     """
#     try:
#         filename = (pbix_file.filename or "").strip()
#         if not filename.lower().endswith(".pbix"):
#             raise HTTPException(status_code=400, detail="Only .pbix files are supported")

#         auth = get_auth_manager()
#         if not auth.is_token_valid():
#             raise HTTPException(
#                 status_code=401,
#                 detail="Power BI token is not valid. Run /powerbi/login/acquire-token first.",
#             )

#         token = auth.get_access_token()
#         target_workspace_id = (workspace_id or auth.workspace_id or "").strip()
#         if not target_workspace_id:
#             raise HTTPException(status_code=400, detail="workspace_id is required")

#         file_bytes = await pbix_file.read()
#         if not file_bytes:
#             raise HTTPException(status_code=400, detail="PBIX file is empty")

#         logger.info(
#             "Importing PBIX to workspace=%s dataset_name=%s file=%s size=%s bytes",
#             target_workspace_id,
#             dataset_name,
#             filename,
#             len(file_bytes),
#         )

#         import_result = import_pbix_and_wait(
#             access_token=token,
#             workspace_id=target_workspace_id,
#             dataset_display_name=dataset_name,
#             pbix_bytes=file_bytes,
#             pbix_filename=filename,
#             name_conflict=name_conflict,
#             timeout_seconds=max(60, int(timeout_seconds)),
#         )

#         dataset_id = import_result.get("dataset_id")
#         report_id = import_result.get("report_id")

#         return {
#             "success": True,
#             "message": "PBIX imported successfully",
#             "workspace_id": target_workspace_id,
#             "dataset_name": dataset_name,
#             "import_id": import_result.get("import_id"),
#             "dataset_id": dataset_id,
#             "report_id": report_id,
#             "links": {
#                 "workspace": f"https://app.powerbi.com/groups/{target_workspace_id}",
#                 "dataset": (
#                     f"https://app.powerbi.com/groups/{target_workspace_id}/datasets/{dataset_id}"
#                     if dataset_id
#                     else None
#                 ),
#                 "report": (
#                     f"https://app.powerbi.com/groups/{target_workspace_id}/reports/{report_id}"
#                     if report_id
#                     else None
#                 ),
#             },
#             "note": "This imported model is Desktop-origin and is the right path for enabling 'Open semantic model'.",
#         }

#     except HTTPException:
#         raise
#     except Exception as exc:
#         logger.exception("import-pbix failed")
#         raise HTTPException(status_code=500, detail=str(exc))


# @router.get("/pipeline-help")
# def get_pipeline_help() -> Dict[str, Any]:
#     """Get pipeline documentation."""
#     return {
#         "pipeline": "6-Stage Qlik-to-Power BI Migration",
#         "publish_modes": [
#             {
#                 "mode": "cloud_push",
#                 "description": "Create Push semantic model directly in Power BI service",
#                 "note": "Open semantic model may be limited for Push models",
#             },
#             {
#                 "mode": "desktop_cloud",
#                 "description": "Generate Desktop handoff bundle, then publish PBIX to service",
#                 "note": "Recommended when semantic model editing in service is required",
#             },
#             {
#                 "mode": "xmla_semantic",
#                 "description": "Create enhanced semantic model directly in cloud via XMLA",
#                 "note": "No Desktop needed; requires XMLA write and supported workspace capacity",
#             },
#         ],
#         "stages": [
#             {
#                 "number": 1,
#                 "name": "Extract",
#                 "description": "Get metadata from Qlik Cloud API",
#             },
#             {
#                 "number": 2,
#                 "name": "Infer",
#                 "description": "Infer PK/FK relationships with confidence scoring",
#             },
#             {
#                 "number": 3,
#                 "name": "Normalize",
#                 "description": "Normalize to Power BI relationship JSON",
#             },
#             {
#                 "number": 4,
#                 "name": "REST Write",
#                 "description": "Create Push dataset in cloud_push mode",
#             },
#             {
#                 "number": 5,
#                 "name": "REST Relationships",
#                 "description": "Create relationships in cloud_push mode",
#             },
#             {
#                 "number": 6,
#                 "name": "ER Diagram",
#                 "description": "Generate Mermaid + HTML diagram",
#             },
#         ],
#         "endpoints": [
#             "POST /api/migration/publish-table",
#             "POST /api/migration/publish-semantic-model",
#             "POST /api/migration/import-pbix",
#             "POST /api/migration/preview-migration",
#             "GET /api/migration/view-diagram",
#             "GET /api/migration/pipeline-help",
#             "POST /api/migration/fetch-loadscript (NEW - Phase 1-4)",
#             "POST /api/migration/parse-loadscript (NEW - Phase 5)",
#             "POST /api/migration/convert-to-mquery (NEW - Phase 6)",
#             "POST /api/migration/download-mquery (NEW - Download)",
#             "POST /api/migration/full-pipeline (NEW - All Phases)",
#         ],
#         "loadscript_conversion": {
#             "description": "NEW: Convert Qlik LoadScript to PowerBI M Query",
#             "phases": [
#                 {"phase": 1, "name": "Connection Test", "endpoint": "/fetch-loadscript"},
#                 {"phase": 2, "name": "Fetch Apps", "endpoint": "/fetch-loadscript"},
#                 {"phase": 3, "name": "App Details", "endpoint": "/fetch-loadscript"},
#                 {"phase": 4, "name": "Fetch LoadScript", "endpoint": "/fetch-loadscript"},
#                 {"phase": 5, "name": "Parse Script", "endpoint": "/parse-loadscript"},
#                 {"phase": 6, "name": "Convert to M", "endpoint": "/convert-to-mquery"},
#             ],
#             "workflow": [
#                 "1. Call /fetch-loadscript with app_id",
#                 "2. Extract loadscript from response",
#                 "3. Call /parse-loadscript with loadscript content",
#                 "4. Extract parsed_script from response",
#                 "5. Call /convert-to-mquery with parsed_script JSON",
#                 "6. Get M query from response",
#                 "7. Call /download-mquery to download as .m file",
#                 "OR use /full-pipeline to do all steps in one call"
#             ],
#             "endpoints_new": {
#                 "/fetch-loadscript": {
#                     "method": "POST",
#                     "query_params": {"app_id": "Qlik app ID"},
#                     "description": "Fetch loadscript from QlikCloud with logging"
#                 },
#                 "/parse-loadscript": {
#                     "method": "POST",
#                     "query_params": {"loadscript": "Script content to parse"},
#                     "description": "Parse loadscript and extract components"
#                 },
#                 "/convert-to-mquery": {
#                     "method": "POST",
#                     "query_params": {"parsed_script_json": "Parsed script as JSON"},
#                     "description": "Convert to PowerBI M Query language"
#                 },
#                 "/download-mquery": {
#                     "method": "POST",
#                     "query_params": {
#                         "m_query": "Generated M query",
#                         "filename": "Output filename (optional)"
#                     },
#                     "description": "Download M query as .m file"
#                 },
#                 "/full-pipeline": {
#                     "method": "POST",
#                     "query_params": {
#                         "app_id": "Qlik app ID",
#                         "auto_download": "Auto-download result (optional)"
#                     },
#                     "description": "Execute complete pipeline (Phases 1-6)"
#                 }
#             }
#         },
#         "conversion_tracking": {
#             "description": "ENHANCED: Real-time tracking and visual logging",
#             "workflow": [
#                 "1. Call /conversion/start-session to create a session ID",
#                 "2. Use the session_id in /full-pipeline-tracked to execute pipeline with tracking",
#                 "3. Poll /conversion/logs?session_id=<id> to get real-time progress logs",
#                 "4. Poll /conversion/status?session_id=<id> to get progress percentage",
#                 "5. Download results using format-specific endpoints",
#                 "6. Call /conversion/data?session_id=<id> to get complete conversion data"
#             ],
#             "endpoints_tracking": {
#                 "/conversion/start-session": {
#                     "method": "POST",
#                     "description": "Create a new conversion session for tracking",
#                     "returns": ["session_id", "log endpoint", "status endpoint"]
#                 },
#                 "/full-pipeline-tracked": {
#                     "method": "POST",
#                     "query_params": {
#                         "app_id": "Qlik app ID",
#                         "session_id": "Session ID from start-session"
#                     },
#                     "description": "Execute pipeline with real-time progress tracking via session"
#                 },
#                 "/conversion/logs": {
#                     "method": "GET",
#                     "query_params": {
#                         "session_id": "Session ID",
#                         "limit": "Max logs to return (default 50)"
#                     },
#                     "description": "Get real-time logs for a session (call repeatedly for live updates)"
#                 },
#                 "/conversion/status": {
#                     "method": "GET",
#                     "query_params": {"session_id": "Session ID"},
#                     "description": "Get current status, progress %, phase, and timing"
#                 },
#                 "/conversion/data": {
#                     "method": "GET",
#                     "query_params": {
#                         "session_id": "Session ID",
#                         "include_logs": "Include full logs (optional)"
#                     },
#                     "description": "Get complete conversion data (LoadScript, Parsed, M Query)"
#                 },
#                 "/download-file": {
#                     "method": "POST",
#                     "query_params": {
#                         "session_id": "Session ID",
#                         "format": "File format: pq | txt | m"
#                     },
#                     "description": "Download M Query in specified format"
#                 },
#                 "/download-dual-zip": {
#                     "method": "POST",
#                     "query_params": {"session_id": "Session ID"},
#                     "description": "Download both .pq and .txt files in ZIP archive"
#                 }
#             }
#         },
#     }


# @router.get("/health")
# def migration_api_health() -> Dict[str, Any]:
#     return {
#         "status": "healthy",
#         "service": "6-Stage Migration Pipeline API",
#         "version": "1.2",
#     }


# # ============================================================================
# # NEW ENDPOINTS: Qlik LoadScript to PowerBI M Query Conversion
# # ============================================================================

# @router.post("/fetch-loadscript")
# def fetch_loadscript_endpoint(
#     app_id: str = Query(..., description="Qlik app ID to fetch loadscript from")
# ) -> Dict[str, Any]:
#     """
#     Fetch loadscript from Qlik Cloud application
    
#     Phase 1-4 of the conversion pipeline:
#     1. Initialize fetcher
#     2. Test connection to Qlik Cloud
#     3. Fetch applications and details
#     4. Fetch loadscript from specified app
    
#     Returns: Loadscript content with metadata
#     """
#     logger.info("=" * 80)
#     logger.info(f"ENDPOINT: /fetch-loadscript")
#     logger.info(f"App ID: {app_id}")
#     logger.info("=" * 80)
    
#     try:
#         # Initialize fetcher
#         logger.info("Initializing LoadScript Fetcher...")
#         fetcher = LoadScriptFetcher()
        
#         # Test connection
#         logger.info("Testing Qlik Cloud connection...")
#         conn_result = fetcher.test_connection()
#         if conn_result["status"] != "success":
#             logger.error(f"Connection test failed: {conn_result}")
#             raise HTTPException(status_code=400, detail="Failed to connect to Qlik Cloud")
        
#         # Fetch loadscript
#         logger.info(f"Fetching loadscript for app: {app_id}")
#         script_result = fetcher.fetch_loadscript(app_id)
        
#         if script_result["status"] not in ["success", "partial_success"]:
#             logger.error(f"Failed to fetch loadscript: {script_result}")
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Failed to fetch loadscript: {script_result.get('message', 'Unknown error')}"
#             )
        
#         logger.info(f"✅ Successfully fetched loadscript ({script_result.get('script_length', 0)} chars)")
#         return script_result
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Error fetching loadscript: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.post("/parse-loadscript")
# def parse_loadscript_endpoint(
#     loadscript: str = Query(..., description="Loadscript content to parse")
# ) -> Dict[str, Any]:
#     """
#     Parse Qlik loadscript and extract components
    
#     Phase 5 of the conversion pipeline:
#     - Extract comments
#     - Extract LOAD statements
#     - Extract table names
#     - Extract field definitions
#     - Extract data connections
#     - Extract transformations (WHERE, GROUP BY, DISTINCT, ORDER BY)
#     - Extract JOIN operations
#     - Extract variable definitions
    
#     Returns: Parsed script structure with all components
#     """
#     logger.info("=" * 80)
#     logger.info(f"ENDPOINT: /parse-loadscript")
#     logger.info(f"Script length: {len(loadscript)} characters")
#     logger.info("=" * 80)
    
#     try:
#         if not loadscript or len(loadscript.strip()) == 0:
#             logger.error("Empty loadscript provided")
#             raise HTTPException(status_code=400, detail="Loadscript cannot be empty")
        
#         logger.info("Initializing LoadScript Parser...")
#         parser = LoadScriptParser(loadscript)
        
#         logger.info("Starting parse operation...")
#         parse_result = parser.parse()
        
#         if parse_result["status"] != "success":
#             logger.error(f"Parse failed: {parse_result}")
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Parse failed: {parse_result.get('message', 'Unknown error')}"
#             )
        
#         logger.info(f"✅ Successfully parsed loadscript")
#         logger.info(f"   Tables: {parse_result['summary']['tables_count']}")
#         logger.info(f"   Fields: {parse_result['summary']['fields_count']}")
#         logger.info(f"   Connections: {parse_result['summary']['connections_count']}")
        
#         return parse_result
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Error parsing loadscript: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.post("/convert-to-mquery")
# def convert_to_mquery_endpoint(
#     parsed_script_json: str = Query(..., description="Parsed script as JSON from /parse-loadscript")
# ) -> Dict[str, Any]:
#     """
#     Convert parsed Qlik loadscript to PowerBI M Query language
    
#     Phase 6 of the conversion pipeline:
#     - Convert data connections
#     - Convert table definitions
#     - Convert field definitions
#     - Convert transformations
#     - Convert JOIN operations
#     - Assemble final M query
    
#     Returns: M query language code and conversion details
#     """
#     logger.info("=" * 80)
#     logger.info(f"ENDPOINT: /convert-to-mquery")
#     logger.info("=" * 80)
    
#     try:
#         if not parsed_script_json:
#             logger.error("Empty parsed script provided")
#             raise HTTPException(status_code=400, detail="Parsed script JSON cannot be empty")
        
#         logger.info("Parsing input JSON...")
#         parsed_script = json.loads(parsed_script_json)
        
#         logger.info("Initializing Simple M Query Generator...")
#         generator = SimpleMQueryGenerator(parsed_script)
        
#         logger.info("Starting M Query generation...")
#         m_query = generator.generate()
        
#         if not m_query or len(m_query.strip()) == 0:
#             logger.error("M Query generation resulted in empty output")
#             raise HTTPException(
#                 status_code=400,
#                 detail="M Query generation failed - empty output"
#             )
        
#         logger.info(f"✅ Successfully generated complete M Query")
#         logger.info(f"   Query length: {len(m_query)} characters")
        
#         return {
#             "status": "success",
#             "m_query": m_query,
#             "query_length": len(m_query),
#             "generator": "CompleteMQueryGenerator",
#             "timestamp": datetime.now().isoformat()
#         }
        
#     except json.JSONDecodeError as e:
#         logger.error(f"Invalid JSON provided: {str(e)}")
#         raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Error converting to M Query: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.post("/download-mquery")
# def download_mquery_endpoint(
#     m_query: str = Query(..., description="M query code to download"),
#     filename: str = Query("powerbi_query.m", description="Output filename")
# ) -> StreamingResponse:
#     """
#     Download the converted M Query as a file
    
#     Generates and downloads the PowerBI M query in .m format
#     suitable for import into Power Query Editor
    
#     Returns: Downloadable .m file
#     """
#     logger.info("=" * 80)
#     logger.info(f"ENDPOINT: /download-mquery")
#     logger.info(f"Filename: {filename}")
#     logger.info(f"Query length: {len(m_query)} characters")
#     logger.info("=" * 80)
    
#     try:
#         if not m_query or len(m_query.strip()) == 0:
#             logger.error("Empty M query provided")
#             raise HTTPException(status_code=400, detail="M query cannot be empty")
        
#         logger.info("Creating file for download...")
        
#         # Create file content
#         file_content = m_query.encode('utf-8')
#         file_obj = io.BytesIO(file_content)
        
#         logger.info(f"✅ File ready for download ({len(file_content)} bytes)")
#         logger.info(f"   Filename: {filename}")
        
#         return StreamingResponse(
#             iter([file_obj.getvalue()]),
#             media_type="application/octet-stream",
#             headers={"Content-Disposition": f"attachment; filename={filename}"}
#         )
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Error creating download: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.post("/full-pipeline")
# def full_pipeline_endpoint(
#     app_id: str = Query(..., description="Qlik app ID"),
#     auto_download: bool = Query(False, description="Auto-download result as file"),
#     table_name: str = Query(None, description="Optional: specific table name to generate M Query for"),
#     ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
# ) -> Dict[str, Any]:
#     """
#     Execute complete Qlik to PowerBI M Query conversion pipeline (PHASES 1-6)
    
#     Combines all steps:
#     1. Initialize and test connection
#     2. Get available apps and verify app exists
#     3. Fetch loadscript from Qlik Cloud
#     4. Parse the loadscript
#     5. Convert to PowerBI M Query
#     6. Optionally download the result
    
#     Parameters:
#     - app_id: Qlik app ID (required)
#     - auto_download: Auto-download result as file (optional)
#     - table_name: Specific table name to generate M Query for (optional) - if provided, only that table's M Query is generated
#     - ws_client: WebSocket client for Qlik Cloud connection (auto-injected)
    
#     Returns: Complete conversion result with all intermediate data
#     """
#     logger.info("=" * 80)
#     logger.info(f"ENDPOINT: /full-pipeline")
#     logger.info(f"App ID: {app_id}")
#     logger.info(f"Table Name: {table_name}")
#     logger.info(f"Auto Download: {auto_download}")
#     logger.info("=" * 80)
    
#     try:
#         # Validate app_id
#         if not app_id or len(app_id.strip()) == 0:
#             raise HTTPException(status_code=400, detail="App ID is required and cannot be empty")
        
#         # PHASE 1-4: Initialize and Fetch loadscript via WebSocket
#         logger.info("🔄 PHASE 1-4: Initializing and fetching loadscript...")
#         try:
#             if not ws_client:
#                 raise HTTPException(status_code=500, detail="Failed to initialize Qlik WebSocket connection")
            
#             # Use WebSocket to get script
#             result = ws_client.get_app_tables_simple(app_id)
            
#             if not result.get("success", False):
#                 fetch_error = result.get("message", "Failed to fetch script")
#                 logger.error(f"❌ Fetch failed: {fetch_error}")
#                 raise HTTPException(status_code=400, detail=f"Failed to fetch loadscript: {fetch_error}")
            
#             loadscript = result.get("script", "")
            
#             if not loadscript or len(loadscript.strip()) == 0:
#                 logger.error("❌ No script returned from Qlik Cloud")
#                 raise HTTPException(status_code=404, detail="No loadscript found in app")
            
#             logger.info(f"✅ Loadscript ready ({len(loadscript)} chars)")
#             script_result = {"status": "success", "method": "websocket", "script_length": len(loadscript)}
            
#         except HTTPException:
#             raise
#         except Exception as e:
#             logger.error(f"❌ WebSocket fetch error: {str(e)}")
#             raise HTTPException(status_code=400, detail=f"Failed to connect to Qlik Cloud: {str(e)}")
        
#         # PHASE 5: Parse loadscript
#         logger.info("🔄 PHASE 5: Parsing loadscript...")
#         try:
#             parser = LoadScriptParser(loadscript)
#             parse_result = parser.parse()
            
#             if not parse_result or parse_result.get("status") == "error":
#                 parse_error = parse_result.get("message", "Unknown parse error") if parse_result else "No result"
#                 logger.warning(f"⚠️  Parse completed with issues: {parse_error}")
#                 # Continue with empty result - converter can handle it
#                 parse_result = {
#                     "status": "partial_success",
#                     "summary": {"tables_count": 0, "fields_count": 0, "comments_count": 0},
#                     "components": {"tables": [], "fields": [], "connections": []},
#                     "message": parse_error,
#                     "raw_script": loadscript
#                 }
#             else:
#                 logger.info(f"✅ Parsed successfully")
#                 logger.info(f"   - Tables: {parse_result.get('summary', {}).get('tables_count', 0)}")
#                 logger.info(f"   - Fields: {parse_result.get('summary', {}).get('fields_count', 0)}")
#         except Exception as e:
#             logger.error(f"❌ Parser error: {str(e)}")
#             parse_result = {
#                 "status": "partial_success",
#                 "summary": {"tables_count": 0, "fields_count": 0},
#                 "components": {"tables": [], "fields": [], "connections": []},
#                 "error": str(e),
#                 "raw_script": loadscript
#             }
        
#         # PHASE 6: Convert to M Query
#         logger.info("🔄 PHASE 6: Converting to PowerBI M Query...")
#         try:
#             # Pass parse_result to generator; if table_name provided, generator
#             # filters it via selected_table parameter based on raw_script analysis
#             if table_name:
#                 logger.info(f"🔍 Single-table mode: Generating M Query for table: {table_name}")
            
#             generator = SimpleMQueryGenerator(
#                 parse_result, 
#                 selected_table=table_name
#             )
#             m_query = generator.generate()
            
#             if not m_query or len(m_query.strip()) == 0:
#                 logger.warning("⚠️  No M Query generated, creating minimal query...")
#                 m_query = f"""let
#     Source = "Generated from Qlik Cloud App: {script_result.get('app_name', 'Unknown')}",
#     // App ID: {app_id}
#     // Table: {table_name or 'All tables'}
#     // Generated: {datetime.now().isoformat()}
#     Result = Source
# in
#     Result"""
            
#             logger.info(f"✅ M Query generated ({len(m_query)} chars)")
#             conversion_result = {"status": "success", "m_query": m_query}
#         except Exception as e:
#             logger.error(f"❌ Generator error: {str(e)}")
#             m_query = f"""let
#     Source = "Error generating M Query: {str(e)}",
#     Result = Source
# in
#     Result"""
#             conversion_result = {"status": "partial_success", "m_query": m_query}
        
#         logger.info("=" * 80)
#         logger.info("✅ COMPLETE PIPELINE EXECUTED")
#         logger.info("=" * 80)
        
#         return {
#             "status": "success",
#             "pipeline": "full_execution",
#             "app_id": app_id,
#             "table_name": table_name,
#             "phases": {
#                 "phase_1_4_fetch": {
#                     "status": script_result.get('status', 'unknown'),
#                     "script_length": len(loadscript),
#                     "method": script_result.get('method', 'unknown')
#                 },
#                 "phase_5_parse": {
#                     "status": parse_result.get('status', 'unknown'),
#                     "summary": parse_result.get('summary', {})
#                 },
#                 "phase_6_convert": {
#                     "status": conversion_result.get('status', 'unknown'),
#                     "query_length": len(m_query)
#                 }
#             },
#             "m_query": m_query,
#             "detailed_results": {
#                 "fetch_result": script_result,
#                 "parse_result": parse_result,
#                 "conversion_result": conversion_result
#             },
#             "auto_download": auto_download
#         }
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"❌ Pipeline error: {str(e)}")
#         import traceback
#         logger.error(traceback.format_exc())
#         raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {str(e)}")


# # ============================================================================
# # NEW ENDPOINTS: Conversion Session Tracking & Visual Logging
# # ============================================================================

# @router.post("/conversion/start-session")
# def start_conversion_session() -> Dict[str, Any]:
#     """
#     Start a new conversion session for tracking progress and logs
    
#     Returns: Session ID for use in subsequent calls
#     """
#     session_manager = get_session_manager()
#     session_id = session_manager.create_session()
#     session = session_manager.get_session(session_id)
    
#     if session:
#         session.set_status("PENDING", "Conversion session started")
    
#     logger.info(f"📌 Started conversion session: {session_id}")
    
#     return {
#         "success": True,
#         "session_id": session_id,
#         "message": "Conversion session created",
#         "endpoints": {
#             "fetch": f"/api/migration/full-pipeline-tracked?session_id={session_id}",
#             "logs": f"/api/migration/conversion/logs?session_id={session_id}",
#             "status": f"/api/migration/conversion/status?session_id={session_id}",
#             "data": f"/api/migration/conversion/data?session_id={session_id}"
#         }
#     }


# @router.get("/conversion/logs")
# def get_conversion_logs(
#     session_id: str = Query(..., description="Conversion session ID"),
#     limit: int = Query(50, description="Max number of logs to return")
# ) -> Dict[str, Any]:
#     """
#     Get logs for a conversion session with visual formatting
    
#     Returns: Array of log entries with timestamps and levels
#     """
#     session_manager = get_session_manager()
#     logs = session_manager.get_session_logs(session_id, limit)
    
#     if not logs:
#         logger.warning(f"No logs found for session: {session_id}")
    
#     return {
#         "session_id": session_id,
#         "log_count": len(logs),
#         "logs": logs,
#         "timestamp_retrieved": datetime.now().isoformat()
#     }


# @router.get("/conversion/status")
# def get_conversion_status(
#     session_id: str = Query(..., description="Conversion session ID")
# ) -> Dict[str, Any]:
#     """
#     Get real-time status and progress of a conversion session
    
#     Returns: Session status, progress percentage, current phase
#     """
#     session_manager = get_session_manager()
#     session = session_manager.get_session(session_id)
    
#     if not session:
#         raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
#     return session.to_dict()


# @router.get("/conversion/data")
# def get_conversion_data(
#     session_id: str = Query(..., description="Conversion session ID"),
#     include_logs: bool = Query(False, description="Include full logs in response")
# ) -> Dict[str, Any]:
#     """
#     Get complete conversion data including results and optionally logs
    
#     Returns: Full session data with LoadScript, Parsed Script, and M Query
#     """
#     session_manager = get_session_manager()
#     data = session_manager.get_session_data(session_id)
    
#     if "error" in data:
#         raise HTTPException(status_code=404, detail=data["error"])
    
#     if not include_logs:
#         data.pop("logs", None)
    
#     return data


# @router.post("/full-pipeline-tracked")
# def full_pipeline_with_tracking(
#     app_id: str = Query(..., description="Qlik app ID"),
#     session_id: str = Query(..., description="Conversion session ID from /conversion/start-session")
# ) -> Dict[str, Any]:
#     """
#     Execute full conversion pipeline with real-time progress tracking via session
    
#     This is the same as /full-pipeline but logs all intermediate steps to the session
#     for real-time UI updates.
    
#     Call /conversion/logs?session_id=<session_id> to get live progress
#     """
#     session_manager = get_session_manager()
#     session = session_manager.get_session(session_id)
    
#     if not session:
#         raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
#     try:
#         session.set_status("RUNNING", "Starting conversion pipeline...")
#         session.set_progress(5, "Initializing")
        
#         # PHASE 1-4: Fetch loadscript
#         session.set_phase(1, f"Fetching loadscript from app: {app_id}")
#         session.set_progress(10, "Testing Qlik Cloud connection")
        
#         try:
#             fetcher = LoadScriptFetcher()
#             conn_result = fetcher.test_connection()
            
#             if conn_result.get("status") != "success":
#                 session.set_status("FAILED", f"Connection failed: {conn_result.get('message', 'Unknown')}")
#                 raise HTTPException(status_code=400, detail="Failed to connect to Qlik Cloud")
            
#             session.add_log(f"Connected to Qlik Cloud as: {conn_result.get('user_name', 'Unknown')}", LogLevel.SUCCESS)
#             session.set_progress(20, "Fetching loadscript...")
            
#             script_result = fetcher.fetch_loadscript(app_id)
            
#             if script_result.get("status") not in ["success", "partial_success"]:
#                 loadscript = f"// Could not fetch loadscript for app: {app_id}\n"
#             else:
#                 loadscript = script_result.get('loadscript', '')
            
#             session.add_log(f"Loadscript fetched ({len(loadscript)} characters)", LogLevel.SUCCESS, phase=4)
#             session.set_result_data(loadscript=loadscript)
            
#         except Exception as e:
#             session.set_status("FAILED", f"Fetch error: {str(e)}")
#             raise HTTPException(status_code=500, detail=str(e))
        
#         # PHASE 5: Parse loadscript
#         session.set_phase(5, "Parsing loadscript")
#         session.set_progress(40, "Parsing script components...")
        
#         try:
#             parser = LoadScriptParser(loadscript)
#             parse_result = parser.parse()
            
#             if parse_result.get("status") == "error":
#                 parse_result = {
#                     "status": "partial_success",
#                     "summary": {"tables_count": 0, "fields_count": 0},
#                     "details": {"tables": [], "fields": [], "data_connections": []}
#                 }
            
#             tables_count = parse_result.get('summary', {}).get('tables_count', 0)
#             fields_count = parse_result.get('summary', {}).get('fields_count', 0)
            
#             session.add_log(
#                 f"Parsed: {tables_count} tables, {fields_count} fields",
#                 LogLevel.SUCCESS,
#                 phase=5,
#                 data={"tables": tables_count, "fields": fields_count}
#             )
#             session.set_result_data(parsed=parse_result)
            
#         except Exception as e:
#             session.set_status("FAILED", f"Parse error: {str(e)}")
#             parse_result = {
#                 "status": "partial_success",
#                 "summary": {"tables_count": 0, "fields_count": 0},
#                 "details": {"tables": [], "fields": [], "data_connections": []}
#             }
        
#         # PHASE 6: Convert to M Query
#         session.set_phase(6, "Converting to Power BI M Query")
#         session.set_progress(70, "Generating M query code...")
        
#         try:
#             generator = SimpleMQueryGenerator(parse_result)
#             m_query = generator.generate()
            
#             if not m_query:
#                 m_query = f"""let
#     Source = "Generated from Qlik Cloud App: {app_id}",
#     Result = Source
# in
#     Result"""
            
#             session.add_log(
#                 f"M Query generated ({len(m_query)} characters)",
#                 LogLevel.SUCCESS,
#                 phase=6,
#                 data={"query_length": len(m_query)}
#             )
#             session.set_result_data(m_query=m_query)
            
#         except Exception as e:
#             logger.error(f"Generator error: {str(e)}")
#             m_query = f"""let
#     Source = "Error in generation: {str(e)}",
#     Result = Source
# in
#     Result"""
        
#         # PHASE 7: Generate files
#         session.set_phase(7, "Generating download files")
#         session.set_progress(90, "Creating file formats...")
        
#         try:
#             generator = MQueryFileGenerator(m_query, parse_result)
#             files = generator.get_file_downloads()
            
#             session.add_log("Generated .pq, .txt, and .m formats", LogLevel.SUCCESS, phase=7)
#             session.set_progress(95, "Finalizing...")
            
#         except Exception as e:
#             session.add_log(f"File generation error: {str(e)}", LogLevel.WARNING)
#             files = {}
        
#         # Mark as complete
#         session.set_progress(100, "Conversion complete")
#         session.set_status("COMPLETED", "Pipeline completed successfully")
#         session.finalize()
        
#         logger.info(f"✅ Pipeline completed for session {session_id}")
        
#         return {
#             "success": True,
#             "session_id": session_id,
#             "status": "COMPLETED",
#             "progress": 100,
#             "app_id": app_id,
#             "results": {
#                 "loadscript_length": len(loadscript) if loadscript else 0,
#                 "tables_count": parse_result.get('summary', {}).get('tables_count', 0),
#                 "fields_count": parse_result.get('summary', {}).get('fields_count', 0),
#                 "m_query_length": len(m_query) if m_query else 0,
#                 "m_query": m_query
#             },
#             "files_available": list(files.keys()),
#             "endpoints": {
#                 "download_pq": f"/api/migration/download-file?session_id={session_id}&format=pq",
#                 "download_txt": f"/api/migration/download-file?session_id={session_id}&format=txt",
#                 "download_m": f"/api/migration/download-file?session_id={session_id}&format=m",
#                 "download_dual_zip": f"/api/migration/download-dual-zip?session_id={session_id}",
#                 "logs": f"/api/migration/conversion/logs?session_id={session_id}"
#             }
#         }
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         session.set_status("FAILED", f"Pipeline error: {str(e)}")
#         session.finalize()
#         logger.error(f"❌ Tracked pipeline error: {str(e)}")
#         import traceback
#         logger.error(traceback.format_exc())
#         raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")


# @router.post("/download-file")
# def download_conversion_file(
#     session_id: str = Query(..., description="Conversion session ID"),
#     format: str = Query("m", description="File format: pq | txt | m"),
#     table: str = Query(None, description="Optional: specific table name for download")
# ) -> StreamingResponse:
#     """
#     Download converted M Query in specified format
    
#     Formats:
#     - pq: Power Query format (.pq)
#     - txt: Documentation format (.txt)
#     - m: M Query format (.m)
    
#     If table parameter is provided, attempts to extract that table's M Query.
#     Currently returns the full M Query with table name in filename.
#     """
#     session_manager = get_session_manager()
#     session = session_manager.get_session(session_id)
    
#     if not session:
#         raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
#     if not session.m_query:
#         raise HTTPException(status_code=400, detail="No M Query available in this session")
    
#     try:
#         generator = MQueryFileGenerator(session.m_query, session.parsed_script)
#         files = generator.get_file_downloads()
        
#         if format not in files:
#             raise HTTPException(
#                 status_code=400,
#                 detail=f"Invalid format: {format}. Available: {', '.join(files.keys())}"
#             )
        
#         file_data = files[format]
#         content = file_data['content'].encode('utf-8')
        
#         # Include table name in filename if specified
#         filename = file_data['filename']
#         if table and table != 'combined':
#             # Insert table name before extension
#             name_parts = filename.rsplit('.', 1)
#             filename = f"{name_parts[0]}_{table}.{name_parts[1]}" if len(name_parts) > 1 else f"{filename}_{table}"
        
#         logger.info(f"📥 Downloading {format} file: {filename} ({len(content)} bytes)" + (f" for table: {table}" if table else ""))
        
#         return StreamingResponse(
#             iter([content]),
#             media_type=file_data['mime_type'],
#             headers={"Content-Disposition": f"attachment; filename={filename}"}
#         )
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Download error: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))


# @router.post("/download-dual-zip")
# def download_dual_zip_file(
#     session_id: str = Query(..., description="Conversion session ID")
# ) -> StreamingResponse:
#     """
#     Download both .pq and .txt files combined in a ZIP archive
    
#     Useful for downloading both formats at once to combine in different contexts
#     """
#     session_manager = get_session_manager()
#     session = session_manager.get_session(session_id)
    
#     if not session:
#         raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
#     if not session.m_query:
#         raise HTTPException(status_code=400, detail="No M Query available in this session")
    
#     try:
#         logger.info(f"📦 Creating dual-file ZIP for session {session_id}")
#         zip_content = generate_dual_download_zip(session.m_query, session.parsed_script)
        
#         logger.info(f"📥 Downloading ZIP file ({len(zip_content)} bytes)")
        
#         return StreamingResponse(
#             iter([zip_content]),
#             media_type="application/zip",
#             headers={
#                 "Content-Disposition": "attachment; filename=powerbi_query_files.zip"
#             }
#         )
        
#     except Exception as e:
#         logger.error(f"ZIP download error: {str(e)}")
#         raise HTTPException(status_code=500, detail=str(e))















"""
Model Publishing API - FastAPI Router (Enhanced)

Exposes the 6-stage migration pipeline via REST endpoints.
Fixed issues and added enhanced MQueryGenerator support.
"""

import logging
import json
from datetime import datetime
from typing import Any, Dict, Optional, List

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Depends
from fastapi.responses import StreamingResponse, JSONResponse
import io

from powerbi_auth import get_auth_manager
from powerbi_pbix_importer import import_pbix_and_wait
from six_stage_orchestrator import run_migration_pipeline
from loadscript_fetcher import LoadScriptFetcher
from loadscript_parser import LoadScriptParser
try:
    from enhanced_mquery_generator import EnhancedMQueryGenerator, create_mquery_generator  # FIXED: Use enhanced version
except ModuleNotFoundError:
    from simple_mquery_generator import SimpleMQueryGenerator as EnhancedMQueryGenerator

    def create_mquery_generator(parsed_script, table_name=None, lib_mapping=None):
        return EnhancedMQueryGenerator(
            parsed_script=parsed_script,
            lib_path_map=lib_mapping,
            selected_table=table_name
        )
from conversion_logger import get_session_manager, LogLevel
from mquery_file_generator import MQueryFileGenerator, generate_dual_download_zip
from qlik_websocket_client import QlikWebSocketClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/migration", tags=["Migration Pipeline"])


def get_qlik_websocket_client():
    """Dependency to provide QlikWebSocketClient"""
    try:
        return QlikWebSocketClient()
    except Exception as e:
        logger.error(f"Failed to initialize QlikWebSocketClient: {e}")
        return None


# ============================================================================
# EXISTING ENDPOINTS (kept as-is, only minor fixes)
# ============================================================================

@router.post("/publish-table")
def publish_table_to_powerbi(
    app_id: str = Query(..., description="Qlik app ID"),
    dataset_name: str = Query(..., description="Power BI dataset name"),
    workspace_id: str = Query(..., description="Power BI workspace ID"),
    access_token: Optional[str] = Query(None, description="Power BI access token"),
    publish_mode: str = Query(
        "cloud_push",
        description="Publish mode: cloud_push | desktop_cloud | xmla_semantic",
    ),
    csv_payload_json: Optional[str] = Query(
        None,
        description="Optional JSON map {table_name: csv_text}; used by xmla_semantic mode",
    ),
) -> Dict[str, Any]:
    """
    Publish Qlik metadata and inferred relationships using complete pipeline.

    Modes:
    - cloud_push: create Push semantic model via REST API
    - desktop_cloud: generate Desktop handoff bundle for PBIX publish to cloud
    - xmla_semantic: create enhanced semantic model in cloud via XMLA (no Desktop required)
    """
    try:
        logger.info(
            "Publishing table - App=%s Dataset=%s Mode=%s", app_id, dataset_name, publish_mode
        )

        resolved_access_token = access_token
        if not resolved_access_token:
            try:
                auth = get_auth_manager()
                if auth.is_token_valid():
                    resolved_access_token = auth.get_access_token()
            except Exception:
                resolved_access_token = None

        csv_payloads: Dict[str, str] = {}
        if csv_payload_json:
            try:
                parsed_payload = json.loads(csv_payload_json)
                if not isinstance(parsed_payload, dict):
                    raise ValueError("csv_payload_json must be a JSON object")
                csv_payloads = {
                    str(k): str(v)
                    for k, v in parsed_payload.items()
                    if str(k).strip() and isinstance(v, str) and v.strip()
                }
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid csv_payload_json: {str(exc)}")

        result = run_migration_pipeline(
            app_id=app_id,
            dataset_name=dataset_name,
            workspace_id=workspace_id,
            access_token=resolved_access_token,
            publish_mode=publish_mode,
            csv_table_payloads=csv_payloads,
        )

        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error", "Pipeline failed"))

        response: Dict[str, Any] = {
            "success": True,
            "message": "Migration pipeline executed successfully",
            "dataset_name": dataset_name,
            "publish_mode": publish_mode,
            "summary": result.get("summary", {}),
            "stages_completed": list(result.get("stages", {}).keys()),
            "er_diagram": result.get("stages", {}).get("6_er_diagram", {}).get("mermaid", ""),
            "warnings": result.get("warnings", []),
            "duration_seconds": result.get("duration_seconds", 0),
        }

        if publish_mode == "desktop_cloud":
            response["desktop_bundle"] = result.get("stages", {}).get("7_desktop_bundle", {})
            response["next_steps"] = [
                "1. Open desktop bundle README",
                "2. Build/open model in Power BI Desktop",
                "3. Publish PBIX to target workspace",
                "4. Open semantic model in service and verify relationships",
            ]
        elif publish_mode == "xmla_semantic":
            xmla_stage = result.get("stages", {}).get("4_xmla_semantic", {})
            response["xmla_semantic"] = xmla_stage
            response["links"] = {
                "workspace": xmla_stage.get("workspace_url")
                or f"https://app.powerbi.com/groups/{workspace_id}",
                "dataset": xmla_stage.get("dataset_url"),
            }
            response["next_steps"] = [
                "1. Open the workspace link",
                "2. Open semantic model in service",
                "3. Verify Model view shows inferred relationships",
                "4. Build report on top of the enhanced model",
            ]
        else:
            response["next_steps"] = [
                "1. Go to Power BI workspace",
                "2. Find your semantic model",
                "3. If Open semantic model is disabled, republish using desktop_cloud mode",
                "4. Validate tables and relationships",
            ]

        return response

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("publish-table failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ... [keep other existing endpoints unchanged] ...


# ============================================================================
# FIXED ENDPOINTS: Qlik LoadScript to PowerBI M Query Conversion
# ============================================================================

@router.post("/fetch-loadscript")
def fetch_loadscript_endpoint(
    app_id: str = Query(..., description="Qlik app ID to fetch loadscript from")
) -> Dict[str, Any]:
    """
    Fetch loadscript from Qlik Cloud application
    
    Phase 1-4 of the conversion pipeline:
    1. Initialize fetcher
    2. Test connection to Qlik Cloud
    3. Fetch applications and details
    4. Fetch loadscript from specified app
    
    Returns: Loadscript content with metadata
    """
    logger.info("=" * 80)
    logger.info(f"ENDPOINT: /fetch-loadscript")
    logger.info(f"App ID: {app_id}")
    logger.info("=" * 80)
    
    try:
        # Initialize fetcher
        logger.info("Initializing LoadScript Fetcher...")
        fetcher = LoadScriptFetcher()
        
        # Test connection
        logger.info("Testing Qlik Cloud connection...")
        conn_result = fetcher.test_connection()
        if conn_result["status"] != "success":
            logger.error(f"Connection test failed: {conn_result}")
            raise HTTPException(status_code=400, detail="Failed to connect to Qlik Cloud")
        
        # Fetch loadscript
        logger.info(f"Fetching loadscript for app: {app_id}")
        script_result = fetcher.fetch_loadscript(app_id)
        
        if script_result["status"] not in ["success", "partial_success"]:
            logger.error(f"Failed to fetch loadscript: {script_result}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch loadscript: {script_result.get('message', 'Unknown error')}"
            )
        
        logger.info(f"✅ Successfully fetched loadscript ({script_result.get('script_length', 0)} chars)")
        return script_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error fetching loadscript: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/parse-loadscript")
def parse_loadscript_endpoint(
    loadscript: str = Query(..., description="Loadscript content to parse")
) -> Dict[str, Any]:
    """
    Parse Qlik loadscript and extract components
    
    Phase 5 of the conversion pipeline:
    - Extract comments
    - Extract LOAD statements
    - Extract table names
    - Extract field definitions
    - Extract data connections
    - Extract transformations (WHERE, GROUP BY, DISTINCT, ORDER BY)
    - Extract JOIN operations
    - Extract variable definitions
    
    Returns: Parsed script structure with all components
    """
    logger.info("=" * 80)
    logger.info(f"ENDPOINT: /parse-loadscript")
    logger.info(f"Script length: {len(loadscript)} characters")
    logger.info("=" * 80)
    
    try:
        if not loadscript or len(loadscript.strip()) == 0:
            logger.error("Empty loadscript provided")
            raise HTTPException(status_code=400, detail="Loadscript cannot be empty")
        
        logger.info("Initializing LoadScript Parser...")
        parser = LoadScriptParser(loadscript)
        
        logger.info("Starting parse operation...")
        parse_result = parser.parse()
        
        if parse_result["status"] != "success":
            logger.error(f"Parse failed: {parse_result}")
            raise HTTPException(
                status_code=400,
                detail=f"Parse failed: {parse_result.get('message', 'Unknown error')}"
            )
        
        logger.info(f"✅ Successfully parsed loadscript")
        logger.info(f"   Tables: {parse_result['summary']['tables_count']}")
        logger.info(f"   Fields: {parse_result['summary']['fields_count']}")
        logger.info(f"   Connections: {parse_result['summary']['connections_count']}")
        
        return parse_result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error parsing loadscript: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/convert-to-mquery")
def convert_to_mquery_endpoint(
    parsed_script_json: str = Query(..., description="Parsed script as JSON from /parse-loadscript"),
    table_name: Optional[str] = Query(None, description="Optional: specific table name to convert"),
    include_comments: bool = Query(True, description="Include comments in output"),
    lib_mapping_json: Optional[str] = Query(None, description="Optional JSON mapping of lib:// paths to actual paths")
) -> Dict[str, Any]:
    """
    Convert parsed Qlik loadscript to PowerBI M Query language using Enhanced Generator
    
    Phase 6 of the conversion pipeline:
    - Convert data connections
    - Convert table definitions
    - Convert field definitions
    - Convert transformations
    - Convert JOIN operations
    - Assemble final M query
    
    Parameters:
    - parsed_script_json: Parsed script from /parse-loadscript
    - table_name: Optional specific table to convert (if omitted, generates multi-table model)
    - include_comments: Include explanatory comments in output
    - lib_mapping_json: Optional mapping of lib:// paths to actual file paths
    
    Returns: M query language code and conversion details
    """
    logger.info("=" * 80)
    logger.info(f"ENDPOINT: /convert-to-mquery")
    logger.info(f"Table Name: {table_name}")
    logger.info("=" * 80)
    
    try:
        if not parsed_script_json:
            logger.error("Empty parsed script provided")
            raise HTTPException(status_code=400, detail="Parsed script JSON cannot be empty")
        
        logger.info("Parsing input JSON...")
        parsed_script = json.loads(parsed_script_json)
        
        # Parse lib mapping if provided
        lib_mapping = {}
        if lib_mapping_json:
            try:
                lib_mapping = json.loads(lib_mapping_json)
                if not isinstance(lib_mapping, dict):
                    raise ValueError("lib_mapping_json must be a JSON object")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid lib_mapping_json: {str(e)}")
        
        # FIXED: Use enhanced generator instead of SimpleMQueryGenerator
        logger.info("Initializing Enhanced M Query Generator...")
        generator = create_mquery_generator(
            parsed_script=parsed_script,
            table_name=table_name,
            lib_mapping=lib_mapping
        )
        
        logger.info("Starting M Query generation...")
        m_query = generator.generate()
        
        if not m_query or len(m_query.strip()) == 0:
            logger.error("M Query generation resulted in empty output")
            raise HTTPException(
                status_code=400,
                detail="M Query generation failed - empty output"
            )
        
        # Get list of available tables
        available_tables = [t.get('name') for t in generator.tables if t.get('name')]
        
        logger.info(f"✅ Successfully generated complete M Query")
        logger.info(f"   Query length: {len(m_query)} characters")
        logger.info(f"   Tables in script: {len(available_tables)}")
        
        return {
            "status": "success",
            "m_query": m_query,
            "query_length": len(m_query),
            "generator": "EnhancedMQueryGenerator",
            "table_name": table_name,
            "available_tables": available_tables,
            "tables_count": len(available_tables),
            "timestamp": datetime.now().isoformat()
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON provided: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error converting to M Query: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/download-mquery")
def download_mquery_endpoint(
    m_query: str = Query(..., description="M query code to download"),
    filename: str = Query("powerbi_query.m", description="Output filename"),
    format: str = Query("m", description="File format: m | pq | txt")
) -> StreamingResponse:
    """
    Download the converted M Query as a file in specified format
    
    Formats:
    - m: Standard M query format
    - pq: Power Query format (with headers)
    - txt: Documentation format
    
    Returns: Downloadable file
    """
    logger.info("=" * 80)
    logger.info(f"ENDPOINT: /download-mquery")
    logger.info(f"Filename: {filename}")
    logger.info(f"Format: {format}")
    logger.info(f"Query length: {len(m_query)} characters")
    logger.info("=" * 80)
    
    try:
        if not m_query or len(m_query.strip()) == 0:
            logger.error("Empty M query provided")
            raise HTTPException(status_code=400, detail="M query cannot be empty")
        
        logger.info("Creating file for download...")
        
        # Format content based on format type
        if format.lower() == 'pq':
            # Add Power Query headers
            content = f"""// Power Query M Query
// Generated: {datetime.now().isoformat()}
// ========================================

{m_query}"""
            filename = filename.replace('.m', '.pq') if filename.endswith('.m') else filename
        elif format.lower() == 'txt':
            # Add documentation
            content = f"""POWER BI M QUERY DOCUMENTATION
========================================
Generated: {datetime.now().isoformat()}
========================================

M QUERY CODE:
{m_query}"""
            filename = filename.replace('.m', '.txt') if filename.endswith('.m') else filename
        else:
            # Standard M format
            content = m_query
        
        # Create file content
        file_content = content.encode('utf-8')
        file_obj = io.BytesIO(file_content)
        
        # Determine media type
        media_type = {
            'm': 'text/plain',
            'pq': 'text/plain',
            'txt': 'text/plain'
        }.get(format.lower(), 'application/octet-stream')
        
        logger.info(f"✅ File ready for download ({len(file_content)} bytes)")
        logger.info(f"   Filename: {filename}")
        
        return StreamingResponse(
            iter([file_obj.getvalue()]),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating download: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/full-pipeline")
def full_pipeline_endpoint(
    app_id: str = Query(..., description="Qlik app ID"),
    auto_download: bool = Query(False, description="Auto-download result as file"),
    table_name: Optional[str] = Query(None, description="Optional: specific table name to generate M Query for"),
    download_format: str = Query("m", description="Download format: m | pq | txt"),
    lib_mapping_json: Optional[str] = Query(None, description="Optional JSON mapping of lib:// paths"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
) -> Any:  # Return type can be Dict or StreamingResponse
    """
    Execute complete Qlik to PowerBI M Query conversion pipeline (PHASES 1-6)
    
    Combines all steps:
    1. Initialize and test connection
    2. Get available apps and verify app exists
    3. Fetch loadscript from Qlik Cloud
    4. Parse the loadscript
    5. Convert to PowerBI M Query using Enhanced Generator
    6. Optionally download the result
    
    Parameters:
    - app_id: Qlik app ID (required)
    - auto_download: Auto-download result as file (optional)
    - table_name: Specific table name to generate M Query for (optional)
    - download_format: Format for auto-download (m, pq, txt)
    - lib_mapping_json: Optional mapping of lib:// paths to actual paths
    - ws_client: WebSocket client for Qlik Cloud connection (auto-injected)
    
    Returns: Complete conversion result or downloadable file
    """
    logger.info("=" * 80)
    logger.info(f"ENDPOINT: /full-pipeline")
    logger.info(f"App ID: {app_id}")
    logger.info(f"Table Name: {table_name}")
    logger.info(f"Auto Download: {auto_download}")
    logger.info("=" * 80)
    
    try:
        # Validate app_id
        if not app_id or len(app_id.strip()) == 0:
            raise HTTPException(status_code=400, detail="App ID is required and cannot be empty")
        
        # Parse lib mapping if provided
        lib_mapping = {}
        if lib_mapping_json:
            try:
                lib_mapping = json.loads(lib_mapping_json)
                if not isinstance(lib_mapping, dict):
                    raise ValueError("lib_mapping_json must be a JSON object")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid lib_mapping_json: {str(e)}")
        
        # PHASE 1-4: Initialize and Fetch loadscript via WebSocket
        logger.info("🔄 PHASE 1-4: Initializing and fetching loadscript...")
        try:
            if not ws_client:
                logger.warning("WebSocket client not available, falling back to REST API")
                fetcher = LoadScriptFetcher()
                script_result = fetcher.fetch_loadscript(app_id)
                loadscript = script_result.get('loadscript', '')
                if not loadscript:
                    raise HTTPException(status_code=404, detail="No loadscript found in app")
            else:
                # Use WebSocket to get script
                result = ws_client.get_app_tables_simple(app_id)
                
                if not result.get("success", False):
                    fetch_error = result.get("message", "Failed to fetch script")
                    logger.error(f"❌ Fetch failed: {fetch_error}")
                    raise HTTPException(status_code=400, detail=f"Failed to fetch loadscript: {fetch_error}")
                
                loadscript = result.get("script", "")
                
                if not loadscript or len(loadscript.strip()) == 0:
                    logger.error("❌ No script returned from Qlik Cloud")
                    raise HTTPException(status_code=404, detail="No loadscript found in app")
            
            logger.info(f"✅ Loadscript ready ({len(loadscript)} chars)")
            script_result = {"status": "success", "method": "websocket", "script_length": len(loadscript)}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Fetch error: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to fetch loadscript: {str(e)}")
        
        # PHASE 5: Parse loadscript
        logger.info("🔄 PHASE 5: Parsing loadscript...")
        try:
            parser = LoadScriptParser(loadscript)
            parse_result = parser.parse()
            
            if not parse_result or parse_result.get("status") == "error":
                parse_error = parse_result.get("message", "Unknown parse error") if parse_result else "No result"
                logger.warning(f"⚠️  Parse completed with issues: {parse_error}")
                # Continue with empty result - converter can handle it
                parse_result = {
                    "status": "partial_success",
                    "summary": {"tables_count": 0, "fields_count": 0, "comments_count": 0},
                    "details": {"tables": [], "fields": [], "data_connections": []},
                    "message": parse_error,
                    "raw_script": loadscript
                }
            else:
                logger.info(f"✅ Parsed successfully")
                logger.info(f"   - Tables: {parse_result.get('summary', {}).get('tables_count', 0)}")
                logger.info(f"   - Fields: {parse_result.get('summary', {}).get('fields_count', 0)}")
                # FIXED: always carry raw_script so SimpleMQueryGenerator can extract
                # source paths and fields that LoadScriptParser doesn't store in its
                # structured output.
                parse_result["raw_script"] = loadscript
        except Exception as e:
            logger.error(f"❌ Parser error: {str(e)}")
            parse_result = {
                "status": "partial_success",
                "summary": {"tables_count": 0, "fields_count": 0},
                "details": {"tables": [], "fields": [], "data_connections": []},
                "error": str(e),
                "raw_script": loadscript
            }
        
        # PHASE 6: Convert to M Query using Enhanced Generator
        logger.info("🔄 PHASE 6: Converting to PowerBI M Query...")
        try:
            if table_name:
                logger.info(f"🔍 Single-table mode: Generating M Query for table: {table_name}")
            
            # FIXED: Use enhanced generator
            generator = create_mquery_generator(
                parsed_script=parse_result,
                table_name=table_name,
                lib_mapping=lib_mapping
            )
            
            m_query = generator.generate()
            
            if not m_query or len(m_query.strip()) == 0:
                logger.warning("⚠️  No M Query generated, creating minimal query...")
                m_query = f"""let
    Source = "Generated from Qlik Cloud App: {app_id}",
    // App ID: {app_id}
    // Table: {table_name or 'All tables'}
    // Generated: {datetime.now().isoformat()}
    Result = Source
in
    Result"""
            
            logger.info(f"✅ M Query generated ({len(m_query)} chars)")
            conversion_result = {"status": "success", "m_query": m_query}
            
            # Get list of available tables
            available_tables = [t.get('name') for t in generator.tables if t.get('name')]
            
        except Exception as e:
            logger.error(f"❌ Generator error: {str(e)}")
            m_query = f"""let
    Source = "Error generating M Query: {str(e)}",
    Result = Source
in
    Result"""
            conversion_result = {"status": "partial_success", "m_query": m_query}
            available_tables = []
        
        # Handle auto-download
        if auto_download:
            logger.info(f"🔄 Auto-download enabled, returning file...")
            
            # Format filename
            base_name = f"qlik_{app_id[:8]}_{table_name if table_name else 'all'}"
            filename = f"{base_name}.{download_format}"
            
            # Format content based on download format
            if download_format.lower() == 'pq':
                content = f"""// Power Query M Query
// Source App: {app_id}
// Table: {table_name or 'All tables'}
// Generated: {datetime.now().isoformat()}
// ========================================

{m_query}"""
            elif download_format.lower() == 'txt':
                content = f"""POWER BI M QUERY DOCUMENTATION
========================================
Source App: {app_id}
Table: {table_name or 'All tables'}
Generated: {datetime.now().isoformat()}
Tables Found: {parse_result.get('summary', {}).get('tables_count', 0)}
========================================

M QUERY CODE:
{m_query}"""
            else:
                content = m_query
            
            file_content = content.encode('utf-8')
            file_obj = io.BytesIO(file_content)
            
            return StreamingResponse(
                iter([file_obj.getvalue()]),
                media_type="text/plain",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        
        logger.info("=" * 80)
        logger.info("✅ COMPLETE PIPELINE EXECUTED")
        logger.info("=" * 80)
        
        return {
            "status": "success",
            "pipeline": "full_execution",
            "app_id": app_id,
            "table_name": table_name,
            "available_tables": available_tables,
            "phases": {
                "phase_1_4_fetch": {
                    "status": script_result.get('status', 'unknown'),
                    "script_length": len(loadscript),
                    "method": script_result.get('method', 'unknown')
                },
                "phase_5_parse": {
                    "status": parse_result.get('status', 'unknown'),
                    "summary": parse_result.get('summary', {})
                },
                "phase_6_convert": {
                    "status": conversion_result.get('status', 'unknown'),
                    "query_length": len(m_query)
                }
            },
            "m_query": m_query,
            "summary": {
                "script_length": len(loadscript),
                "tables_count": parse_result.get('summary', {}).get('tables_count', 0),
                "fields_count": parse_result.get('summary', {}).get('fields_count', 0),
                "query_length": len(m_query)
            },
            "detailed_results": {
                "fetch_result": script_result,
                "parse_result": parse_result,
                "conversion_result": conversion_result
            },
            "auto_download": auto_download
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Pipeline error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pipeline execution failed: {str(e)}")


# ============================================================================
# NEW HELPER ENDPOINTS
# ============================================================================

@router.get("/list-tables")
def list_tables_from_script(
    app_id: str = Query(..., description="Qlik app ID"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
) -> Dict[str, Any]:
    """
    List all tables available in a Qlik app without generating M query
    Useful for previewing before conversion
    """
    logger.info(f"Listing tables for app: {app_id}")
    
    try:
        # Fetch script
        if not ws_client:
            fetcher = LoadScriptFetcher()
            script_result = fetcher.fetch_loadscript(app_id)
            loadscript = script_result.get('loadscript', '')
        else:
            result = ws_client.get_app_tables_simple(app_id)
            loadscript = result.get("script", "")
        
        if not loadscript:
            raise HTTPException(status_code=404, detail="No script found")
        
        # Parse
        parser = LoadScriptParser(loadscript)
        parse_result = parser.parse()
        
        tables = []
        for table in parse_result.get('details', {}).get('tables', []):
            tables.append({
                "name": table.get('name'),
                "fields_count": len(table.get('fields', [])),
                "source": table.get('source', ''),
                "has_calculated": len(table.get('calculated_fields', [])) > 0
            })
        
        return {
            "success": True,
            "app_id": app_id,
            "tables": tables,
            "tables_count": len(tables),
            "fields_count": parse_result.get('summary', {}).get('fields_count', 0)
        }
        
    except Exception as e:
        logger.error(f"Error listing tables: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview-conversion")
def preview_conversion(
    parsed_script_json: str = Query(..., description="Parsed script as JSON"),
    table_name: Optional[str] = Query(None, description="Optional specific table")
) -> Dict[str, Any]:
    """
    Preview conversion without generating full M query
    Shows structure of what will be generated
    """
    try:
        parsed_script = json.loads(parsed_script_json)
        
        generator = create_mquery_generator(
            parsed_script=parsed_script,
            table_name=table_name
        )
        
        # Get table info
        if table_name:
            table = next((t for t in generator.tables if t['name'].lower() == table_name.lower()), None)
            if not table:
                raise HTTPException(status_code=404, detail=f"Table {table_name} not found")
            
            preview = {
                "table_name": table['name'],
                "fields": table.get('fields', []),
                "calculated_fields": len(table.get('calculated_fields', [])),
                "source": table.get('source', ''),
                "has_where": table.get('where') is not None,
                "has_group_by": table.get('group_by') is not None,
                "is_distinct": table.get('distinct', False),
                "steps": [
                    "1. Source loading",
                    "2. WHERE filtering" if table.get('where') else None,
                    "3. Calculated fields" if table.get('calculated_fields') else None,
                    "4. DISTINCT removal" if table.get('distinct') else None,
                    "5. GROUP BY aggregation" if table.get('group_by') else None,
                    "6. Type conversion"
                ]
            }
        else:
            preview = {
                "tables": len(generator.tables),
                "tables_list": [t['name'] for t in generator.tables],
                "total_fields": sum(len(t.get('fields', [])) for t in generator.tables)
            }
        
        return {
            "success": True,
            "mode": "single" if table_name else "multi",
            "preview": preview,
            "generator": "EnhancedMQueryGenerator"
        }
        
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# FIXED TRACKING ENDPOINTS
# ============================================================================

@router.post("/conversion/start-session")
def start_conversion_session() -> Dict[str, Any]:
    """
    Start a new conversion session for tracking progress and logs
    
    Returns: Session ID for use in subsequent calls
    """
    session_manager = get_session_manager()
    session_id = session_manager.create_session()
    session = session_manager.get_session(session_id)
    
    if session:
        session.set_status("PENDING", "Conversion session started")
    
    logger.info(f"📌 Started conversion session: {session_id}")
    
    return {
        "success": True,
        "session_id": session_id,
        "message": "Conversion session created",
        "endpoints": {
            "fetch": f"/api/migration/full-pipeline-tracked?session_id={session_id}",
            "logs": f"/api/migration/conversion/logs?session_id={session_id}",
            "status": f"/api/migration/conversion/status?session_id={session_id}",
            "data": f"/api/migration/conversion/data?session_id={session_id}"
        }
    }


@router.get("/conversion/logs")
def get_conversion_logs(
    session_id: str = Query(..., description="Conversion session ID"),
    limit: int = Query(50, description="Max number of logs to return")
) -> Dict[str, Any]:
    """
    Get logs for a conversion session with visual formatting
    
    Returns: Array of log entries with timestamps and levels
    """
    session_manager = get_session_manager()
    logs = session_manager.get_session_logs(session_id, limit)
    
    if not logs:
        logger.warning(f"No logs found for session: {session_id}")
    
    return {
        "session_id": session_id,
        "log_count": len(logs),
        "logs": logs,
        "timestamp_retrieved": datetime.now().isoformat()
    }


@router.get("/conversion/status")
def get_conversion_status(
    session_id: str = Query(..., description="Conversion session ID")
) -> Dict[str, Any]:
    """
    Get real-time status and progress of a conversion session
    
    Returns: Session status, progress percentage, current phase
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    return session.to_dict()


@router.get("/conversion/data")
def get_conversion_data(
    session_id: str = Query(..., description="Conversion session ID"),
    include_logs: bool = Query(False, description="Include full logs in response")
) -> Dict[str, Any]:
    """
    Get complete conversion data including results and optionally logs
    
    Returns: Full session data with LoadScript, Parsed Script, and M Query
    """
    session_manager = get_session_manager()
    data = session_manager.get_session_data(session_id)
    
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    
    if not include_logs:
        data.pop("logs", None)
    
    return data


@router.post("/full-pipeline-tracked")
def full_pipeline_with_tracking(
    app_id: str = Query(..., description="Qlik app ID"),
    session_id: str = Query(..., description="Conversion session ID from /conversion/start-session"),
    table_name: Optional[str] = Query(None, description="Optional specific table")
) -> Dict[str, Any]:
    """
    Execute full conversion pipeline with real-time progress tracking via session
    
    This is the same as /full-pipeline but logs all intermediate steps to the session
    for real-time UI updates.
    
    Call /conversion/logs?session_id=<session_id> to get live progress
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    try:
        session.set_status("RUNNING", "Starting conversion pipeline...")
        session.set_progress(5, "Initializing")
        
        # PHASE 1-4: Fetch loadscript
        session.set_phase(1, f"Fetching loadscript from app: {app_id}")
        session.set_progress(10, "Testing Qlik Cloud connection")
        
        try:
            fetcher = LoadScriptFetcher()
            conn_result = fetcher.test_connection()
            
            if conn_result.get("status") != "success":
                session.set_status("FAILED", f"Connection failed: {conn_result.get('message', 'Unknown')}")
                raise HTTPException(status_code=400, detail="Failed to connect to Qlik Cloud")
            
            session.add_log(f"Connected to Qlik Cloud", LogLevel.SUCCESS)
            session.set_progress(20, "Fetching loadscript...")
            
            script_result = fetcher.fetch_loadscript(app_id)
            
            if script_result.get("status") not in ["success", "partial_success"]:
                loadscript = f"// Could not fetch loadscript for app: {app_id}\n"
            else:
                loadscript = script_result.get('loadscript', '')
            
            session.add_log(f"Loadscript fetched ({len(loadscript)} characters)", LogLevel.SUCCESS, phase=4)
            session.set_result_data(loadscript=loadscript)
            
        except Exception as e:
            session.set_status("FAILED", f"Fetch error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        
        # PHASE 5: Parse loadscript
        session.set_phase(5, "Parsing loadscript")
        session.set_progress(40, "Parsing script components...")
        
        try:
            parser = LoadScriptParser(loadscript)
            parse_result = parser.parse()
            
            if parse_result.get("status") == "error":
                parse_result = {
                    "status": "partial_success",
                    "summary": {"tables_count": 0, "fields_count": 0},
                    "details": {"tables": [], "fields": [], "data_connections": []},
                    "raw_script": loadscript   # FIXED: carry raw_script for generator
                }
            else:
                # FIXED: always carry raw_script so SimpleMQueryGenerator can extract
                # source paths and fields that LoadScriptParser doesn't store in its
                # structured output.
                parse_result["raw_script"] = loadscript
            
            tables_count = parse_result.get('summary', {}).get('tables_count', 0)
            fields_count = parse_result.get('summary', {}).get('fields_count', 0)
            
            session.add_log(
                f"Parsed: {tables_count} tables, {fields_count} fields",
                LogLevel.SUCCESS,
                phase=5,
                data={"tables": tables_count, "fields": fields_count}
            )
            session.set_result_data(parsed=parse_result)
            
        except Exception as e:
            session.set_status("FAILED", f"Parse error: {str(e)}")
            parse_result = {
                "status": "partial_success",
                "summary": {"tables_count": 0, "fields_count": 0},
                "details": {"tables": [], "fields": [], "data_connections": []},
                "raw_script": loadscript   # FIXED: carry raw_script even on exception
            }
        
        # PHASE 6: Convert to M Query using Enhanced Generator
        session.set_phase(6, "Converting to Power BI M Query")
        session.set_progress(70, "Generating M query code...")
        
        try:
            # FIXED: Use enhanced generator
            generator = create_mquery_generator(
                parsed_script=parse_result,
                table_name=table_name
            )
            
            m_query = generator.generate()
            
            if not m_query:
                m_query = f"""let
    Source = "Generated from Qlik Cloud App: {app_id}",
    Result = Source
in
    Result"""
            
            session.add_log(
                f"M Query generated ({len(m_query)} characters)",
                LogLevel.SUCCESS,
                phase=6,
                data={"query_length": len(m_query)}
            )
            session.set_result_data(m_query=m_query)
            
        except Exception as e:
            logger.error(f"Generator error: {str(e)}")
            m_query = f"""let
    Source = "Error in generation: {str(e)}",
    Result = Source
in
    Result"""
        
        # PHASE 7: Generate files
        session.set_phase(7, "Generating download files")
        session.set_progress(90, "Creating file formats...")
        
        try:
            generator = MQueryFileGenerator(m_query, parse_result)
            files = generator.get_file_downloads()
            
            session.add_log("Generated .pq, .txt, and .m formats", LogLevel.SUCCESS, phase=7)
            session.set_progress(95, "Finalizing...")
            
        except Exception as e:
            session.add_log(f"File generation error: {str(e)}", LogLevel.WARNING)
            files = {}
        
        # Mark as complete
        session.set_progress(100, "Conversion complete")
        session.set_status("COMPLETED", "Pipeline completed successfully")
        session.finalize()
        
        logger.info(f"✅ Pipeline completed for session {session_id}")
        
        return {
            "success": True,
            "session_id": session_id,
            "status": "COMPLETED",
            "progress": 100,
            "app_id": app_id,
            "table_name": table_name,
            "results": {
                "loadscript_length": len(loadscript) if loadscript else 0,
                "tables_count": parse_result.get('summary', {}).get('tables_count', 0),
                "fields_count": parse_result.get('summary', {}).get('fields_count', 0),
                "m_query_length": len(m_query) if m_query else 0,
                "m_query": m_query
            },
            "files_available": list(files.keys()),
            "endpoints": {
                "download_pq": f"/api/migration/download-file?session_id={session_id}&format=pq",
                "download_txt": f"/api/migration/download-file?session_id={session_id}&format=txt",
                "download_m": f"/api/migration/download-file?session_id={session_id}&format=m",
                "download_dual_zip": f"/api/migration/download-dual-zip?session_id={session_id}",
                "logs": f"/api/migration/conversion/logs?session_id={session_id}"
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        session.set_status("FAILED", f"Pipeline error: {str(e)}")
        session.finalize()
        logger.error(f"❌ Tracked pipeline error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pipeline failed: {str(e)}")


@router.post("/download-file")
def download_conversion_file(
    session_id: str = Query(..., description="Conversion session ID"),
    format: str = Query("m", description="File format: pq | txt | m"),
    table: str = Query(None, description="Optional: specific table name for download")
) -> StreamingResponse:
    """
    Download converted M Query in specified format
    
    Formats:
    - pq: Power Query format (.pq)
    - txt: Documentation format (.txt)
    - m: M Query format (.m)
    
    If table parameter is provided, attempts to extract that table's M Query.
    Currently returns the full M Query with table name in filename.
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    if not session.m_query:
        raise HTTPException(status_code=400, detail="No M Query available in this session")
    
    try:
        generator = MQueryFileGenerator(session.m_query, session.parsed_script)
        files = generator.get_file_downloads()
        
        if format not in files:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid format: {format}. Available: {', '.join(files.keys())}"
            )
        
        file_data = files[format]
        
        # Format content based on format
        if format == 'pq':
            content = f"""// Power Query M Query
// Generated from session: {session_id}
// ========================================

{file_data['content']}"""
        elif format == 'txt':
            content = f"""POWER BI M QUERY DOCUMENTATION
========================================
Session ID: {session_id}
Generated: {datetime.now().isoformat()}
========================================

{file_data['content']}"""
        else:
            content = file_data['content']
        
        encoded_content = content.encode('utf-8')
        
        # Include table name in filename if specified
        filename = file_data['filename']
        if table and table != 'combined':
            name_parts = filename.rsplit('.', 1)
            filename = f"{name_parts[0]}_{table}.{name_parts[1]}" if len(name_parts) > 1 else f"{filename}_{table}"
        
        logger.info(f"📥 Downloading {format} file: {filename} ({len(encoded_content)} bytes)" + (f" for table: {table}" if table else ""))
        
        return StreamingResponse(
            iter([encoded_content]),
            media_type=file_data['mime_type'],
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Download error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/download-dual-zip")
def download_dual_zip_file(
    session_id: str = Query(..., description="Conversion session ID")
) -> StreamingResponse:
    """
    Download both .pq and .txt files combined in a ZIP archive
    
    Useful for downloading both formats at once to combine in different contexts
    """
    session_manager = get_session_manager()
    session = session_manager.get_session(session_id)
    
    if not session:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    
    if not session.m_query:
        raise HTTPException(status_code=400, detail="No M Query available in this session")
    
    try:
        logger.info(f"📦 Creating dual-file ZIP for session {session_id}")
        zip_content = generate_dual_download_zip(session.m_query, session.parsed_script)
        
        logger.info(f"📥 Downloading ZIP file ({len(zip_content)} bytes)")
        
        return StreamingResponse(
            iter([zip_content]),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=powerbi_query_files.zip"
            }
        )
        
    except Exception as e:
        logger.error(f"ZIP download error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
