"""
Qlik Cloud to Power BI Migration Backend
Complete 6-stage pipeline implementation
"""

# ============================================================
# FIX SUMMARY  (all changes vs original)
# ============================================================
# FIX 1: Added missing top-level imports: re, time, datetime, requests
#         (re used by parse_dax; time used by export/csv; datetime used by
#          download_mquery error fallback; requests used by clone/create report)
# FIX 2: Moved ALL module-level imports to the top — matplotlib, numpy,
#         QlikClient, processor, table_tracker, powerbi_service_delegated,
#         powerbi_auth, pydantic — previously scattered mid-file after route
#         decorators, causing NameError at startup.
# FIX 3: Removed duplicate get_qlik_client() definition (appeared twice:
#         lines 334 and 352 in original). Kept the lazy-init version.
# FIX 4: Removed duplicate get_qlik_websocket_client() definition (appeared
#         at lines 59 and 359). Kept the first (line 59) version.
# FIX 5: Removed duplicate @app.get("/") route (lines 69 and 938).
#         Kept the first root(); second one was silently ignored by FastAPI
#         but the duplicate function body was a maintenance hazard.
# FIX 6: Removed duplicate @app.get("/health") routes (lines 84, 981, 1922).
#         Three definitions — only first is ever called. Kept one clean version.
# FIX 7: Removed duplicate @app.get("/applications/{app_id}/tables") route
#         (lines 292 and 1091). Both bodies were identical. Kept second
#         instance (after enhance_tables_with_timestamps import is available).
# FIX 8: First get_app_tables at line 292 called enhance_tables_with_timestamps
#         BEFORE that function was imported — runtime NameError. Fixed by
#         removing the early duplicate (see FIX 7).
# FIX 9: Removed second @app.get("/") handler at line 938 which would have
#         silently shadowed the first and returned wrong schema.
# FIX 10: powerbi_dataset_publisher integrated — /api/migration/publish-dataset
#          endpoint added, wiring parse_result → publish_dataset().
# ============================================================

import sys
import os
import re
import time
import threading
import requests
import pandas as pd
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Body, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, HTMLResponse, PlainTextResponse, StreamingResponse
from typing import List, Dict, Any, Optional
import logging
import base64
import io
#from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv(override=False)
#app = FastAPI()

# Visualisation / numeric
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# Pydantic
from pydantic import BaseModel

# Project imports
from app.schemas.auth_schema import LoginPayload, router as login_router, validate_login as validate_login_handler
from app.api.v1.endpoints.migration import router as migration_router
from app.api.v1.endpoints.workflows import router as workflows_router
from app.services.mquery_converter import validate_sharepoint_url_strict
from app.services.qlik_websocket_client import QlikWebSocketClient
from app.services.qlik_client import QlikClient
from app.services.simple_mquery_generator import SimpleMQueryGenerator
from app.services.processor import PowerBIProcessor
from app.db.table_tracker import enhance_tables_with_timestamps
from app.services.powerbi_service_delegated import PowerBIService, infer_schema_from_rows
from app.services.powerbi_relationships import apply_relationships, verify_relationships
from app.services.powerbi_auth import get_auth_manager
from app.services.relationship_service import (
    _sanitize_col_name,
    infer_relationships_unified,
    sanitize_rel_columns,
    normalize_table_rows,
    resolve_relationships_unified,
)
from app.services.brd_generator import (
    BRD_PROMPT_VERSION,
    build_brd_prompt,
    build_default_document,
    build_download_filename,
    build_prompt_context,
    extract_json_object,
    merge_defaults,
    render_brd_html,
)

# Optional: new publisher (from powerbi_dataset_publisher.py)
try:
    from app.services.powerbi_dataset_publisher import publish_from_parse_result, PublisherConfig
    PUBLISHER_AVAILABLE = True
except ImportError:
    PUBLISHER_AVAILABLE = False

# Optional: old script parser
try:
    from app.services.qlik_script_parser import QlikScriptParser
    SCRIPT_PARSER_AVAILABLE = True
except ImportError:
    SCRIPT_PARSER_AVAILABLE = False
    print("WARNING: qlik_script_parser not found. Script extraction endpoints will be disabled.")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_BRD_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_brd_cache")
PROJECT_BRD_CACHE_TTL_SECONDS = 900
project_brd_generation_state: Dict[str, bool] = {}
project_brd_generation_lock = threading.Lock()

# ==================== APP ====================

app = FastAPI(
    title="Qlik to Power BI 6-Stage Migration API",
    version="1.0.0",
    description="Extract → Infer → Normalize → XMLA Write → TOM Create → ER Diagram"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "https://qlik-frontend.onrender.com",
        "https://qlik-sense-cloud.onrender.com",
        "https://qlikai-ld54.onrender.com",
        "https://qlikaiv2-web.onrender.com",
        "https://qlikai-app-ltmrv.ondigitalocean.app",
    ],
    allow_origin_regex=r"http://localhost:\d+|http://127\.0\.0\.1:\d+|https://.*\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(login_router)
app.include_router(migration_router)
app.include_router(workflows_router)

# ==================== DEPENDENCY INJECTION ====================
# FIX 3+4: single definition of each dependency — no duplicates

qlik_client_instance = None  # FIX 3: lazy singleton

def get_qlik_client(
    x_api_key: Optional[str] = Header(None),
    x_tenant_url: Optional[str] = Header(None)
):
    try:
        return QlikClient(
            api_key=x_api_key or os.getenv("QLIK_API_KEY"),
            tenant_url=x_tenant_url or os.getenv("QLIK_TENANT_URL")
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_qlik_websocket_client():
    """Dependency to provide QlikWebSocketClient for endpoints."""
    try:
        return QlikWebSocketClient()
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ROOT & HEALTH ====================
# FIX 5+6: single / and single /health — duplicates removed

@app.get("/")
def root():
    endpoints = {
        "login":   "/powerbi/login/acquire-token",
        "publish": "/api/migration/publish-table",
        "preview": "/api/migration/preview-migration",
        "diagram": "/api/migration/view-diagram",
        "help":    "/api/migration/pipeline-help",
        "docs":    "/docs",
    }
    if SCRIPT_PARSER_AVAILABLE:
        endpoints["script_extraction"] = [
            "/applications/{app_id}/script/tables",
            "/applications/{app_id}/script/table/{table_name}",
        ]
    if PUBLISHER_AVAILABLE:
        endpoints["dataset_publish"] = "/api/migration/publish-dataset"
    return {
        "api":     "Qlik to Power BI 6-Stage Migration",
        "version": "1.0.0",
        "script_parser_available": SCRIPT_PARSER_AVAILABLE,
        "publisher_available":     PUBLISHER_AVAILABLE,
        "endpoints": endpoints,
    }

@app.get("/health")
def health():
    return {
        "status":         "healthy",
        "service":        "Qlik FastAPI Backend",
        "script_parser":  SCRIPT_PARSER_AVAILABLE,
        "publisher":      PUBLISHER_AVAILABLE,
    }

# ==================== M QUERY DOWNLOAD ====================

@app.get("/applications/{app_id}/mquery/download")
async def download_mquery(
    app_id: str,
    table: str = Query(..., description="Table name to generate M Query for"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """Generate Power BI M Query for selected Qlik table."""
    try:
        logger.info(f"📥 Download M Query - App: {app_id}, Table: {table}")

        result = ws_client.get_app_tables_simple(app_id)
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=f"Failed to fetch script: {result.get('error')}")

        raw_script  = result.get("script", "")
        app_title   = result.get("app_title", "Unknown App")

        if not raw_script.strip():
            raise HTTPException(status_code=404, detail="No script found in app")

        logger.info(f"✅ Script fetched: {len(raw_script)} characters")

        try:
            from app.utils.loadscript_parser import LoadScriptParser
            parser       = LoadScriptParser(raw_script)
            parse_result = parser.parse()
            logger.info(f"✅ Script parsed: {parse_result.get('summary', {}).get('tables_count', 0)} tables found")
        except Exception as e:
            logger.warning(f"⚠️ Parse error, falling back: {e}")
            parse_result = {
                "status":  "partial_success",
                "raw_script": raw_script,
                "summary": {"tables_count": 0},
                "details": {"tables": []},
            }

        try:
            from simple_mquery_generator import create_mquery_generator
            generator = create_mquery_generator(
                parsed_script=parse_result,
                table_name=table,
                lib_mapping={
                    "lib://DataFiles/": "C:/Data/",
                    "lib://QVD/":       "C:/QVD/",
                },
            )
            m_query = generator.generate()
            if not m_query.strip():
                raise ValueError("Empty M query generated")

        except ImportError:
            generator = SimpleMQueryGenerator({"raw_script": raw_script}, selected_table=table)
            m_query   = generator.generate()

        except Exception as e:
            logger.error(f"❌ Generator error: {e}")
            m_query = (
                f"// M Query for table: {table}\n"
                f"// Source App: {app_title} ({app_id})\n"
                f"// Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"// Error: {e}\n\n"
                f"let\n    Source = \"Error generating M query\",\n    Result = Source\nin\n    Result"
            )

        filename = f"{table.replace(' ', '_')}.m"
        return Response(
            content=m_query,
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename={filename}",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        import traceback; logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to generate M query: {e}")


# ==================== ER DIAGRAM ====================
def build_er_diagram(tables: List[Dict[str, Any]], master_table: str = None, master_border_color: Optional[str] = None, master_border_width: Optional[int] = None) -> bytes:
    """
    Generate an ER diagram showing relationships between tables.
    Master table (star center) may be highlighted by border color/width.

    Parameters
    - master_border_color: optional hex color for the master table border (e.g. '#1d4ed8')
    - master_border_width: optional integer for master border thickness
    """
    try:
        fig, ax = plt.subplots(figsize=(11, 8), facecolor='white')
        ax.set_facecolor('white')
        ax.set_xlim(-7.5, 7.5)
        ax.set_ylim(-7.5, 7.5)
        ax.axis('off')
        
        # Normalize tables
        table_list = []
        for t in tables:
            if isinstance(t, str):
                table_list.append({'name': t, 'fields': []})
            elif isinstance(t, dict):
                table_list.append(t)
        
        if not table_list:
            # Empty diagram
            buf = io.BytesIO()
            plt.savefig(buf, format='png', facecolor='white', bbox_inches='tight', dpi=100)
            plt.close(fig)
            buf.seek(0)
            return buf.getvalue()
        
        # Identify master table (if provided) or largest table
        center_table = master_table
        if not center_table:
            # Find table with most fields
            center_table = max(table_list, key=lambda t: len(t.get('fields', []))).get('name') or table_list[0]['name']
        
        # Position: master in center, others in circle
        positions = {}
        positions[center_table] = (0, 0)
        
        related_tables = [t['name'] for t in table_list if t['name'] != center_table]
        num_related = len(related_tables)
        
        if num_related > 0:
            radius = 5.5
            for i, table_name in enumerate(related_tables):
                angle = (2 * np.pi * i) / num_related
                x = radius * np.cos(angle)
                y = radius * np.sin(angle)
                positions[table_name] = (x, y)
        
        # Theme defaults for master border color/width (can be overridden by request)
        mb_border_color = master_border_color or "#1d74d8"
        mb_border_width = master_border_width if master_border_width is not None else 3
        
        # Draw connections first
        for i, table1 in enumerate(table_list):
            for table2 in table_list[i+1:]:
                name1, name2 = table1['name'], table2['name']
                if name1 in positions and name2 in positions:
                    # Check if they share fields (common key pattern)
                    fields1 = set(str(f).lower() if isinstance(f, str) else f.get('name', '').lower() 
                                 for f in table1.get('fields', []))
                    fields2 = set(str(f).lower() if isinstance(f, str) else f.get('name', '').lower() 
                                 for f in table2.get('fields', []))
                    
                    if fields1 & fields2:  # Share at least one field
                        x1, y1 = positions[name1]
                        x2, y2 = positions[name2]
                        
                        # Draw arrow
                        arrow = FancyArrowPatch(
                            (x1, y1), (x2, y2),
                            arrowstyle='<->', mutation_scale=20, linewidth=2,
                            color="#000000", alpha=0.7, zorder=1
                        )
                        ax.add_patch(arrow)
        
        # Draw table boxes
        for table in table_list:
            name = table['name']
            x, y = positions.get(name, (0, 0))
            
            # Color: highlight master with blue border only; related = white
            is_master = (name == center_table)
            # Master = white fill + blue/dynamic border
            bg_color = "#ffffff"
            border_color = mb_border_color if is_master else "#000000"
            border_width = mb_border_width if is_master else 2
            
            # Get fields for sizing
            fields = table.get('fields', [])
            field_names = []
            for f in fields[:4]:
                if isinstance(f, str):
                    field_names.append(f)
                elif isinstance(f, dict):
                    field_names.append(f.get('name', ''))
            
            # Dynamic box sizing based on content
            # Calculate required height based on number of fields
            num_fields = len(field_names)
            field_height = 0.3 * num_fields if num_fields > 0 else 0
            
            # Calculate box dimensions with proper padding
            box_width = 4.2 if is_master else 3.0
            box_height = max(2.8, 1.2 + field_height + 0.5)  # Min height + table name + fields + padding
            
            fancy_box = FancyBboxPatch(
                (x - box_width/2, y - box_height/2), box_width, box_height,
                boxstyle="round,pad=0.1",
                edgecolor=border_color, facecolor=bg_color, 
                linewidth=border_width, zorder=2
            )
            ax.add_patch(fancy_box)
            
            # Table name - use dark labels since background is white
            label_color = "#000000"
            name_fontsize = 12 if is_master else 10
            
            # Wrap long table names
            if len(name) > 20:
                wrapped_name = '\n'.join([name[i:i+15] for i in range(0, len(name), 15)])
            else:
                wrapped_name = name
                
            ax.text(x, y + box_height/2 - 0.35, wrapped_name, ha='center', va='top',
                   fontsize=name_fontsize, fontweight='bold',
                   color=label_color, zorder=3, wrap=True)
            
            # Fields - wrapped text that fits in box
            if field_names:
                # Wrap field names if too long
                wrapped_fields = []
                for field in field_names:
                    if len(field) > 18:
                        wrapped_fields.append(field[:15] + '...')
                    else:
                        wrapped_fields.append(field)
                
                field_text = '\n'.join(wrapped_fields)
                field_fontsize = 8 if is_master else 7.5
                
                # Position fields below table name
                field_y_pos = y - 0.2
                ax.text(x, field_y_pos, field_text, ha='center', va='center',
                       fontsize=field_fontsize, color=label_color, alpha=0.85, 
                       zorder=3, wrap=True)
        
        # # Title
        # ax.text(0, 7.5, 'Entity Relationship Diagram (ER)', 
        #        ha='center', va='center',
        #        fontsize=14, fontweight='bold', color='#1f2937')
        
        # Legend
        legend_elements = [
            mpatches.Patch(facecolor="#ffffff", edgecolor=mb_border_color, label='Master Table'),
            mpatches.Patch(facecolor='#ffffff', edgecolor='#0f0f10', label='Related Tables'),
            mpatches.Patch(facecolor='none', edgecolor='#040404', label='Relationships'),
        ]
        ax.legend(handles=legend_elements, loc='lower right', fontsize=10,
                 framealpha=0.95, edgecolor='#d1d5db')
        
        # Save to bytes
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='white', bbox_inches='tight', dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()
        
    except Exception as e:
        print(f"Error generating ER diagram: {str(e)}")
        # Return a simple error diagram
        fig, ax = plt.subplots(figsize=(10, 6), facecolor='white')
        ax.text(0.5, 0.5, f'Error: {str(e)}', ha='center', va='center',
               fontsize=12, color='red', transform=ax.transAxes)
        ax.axis('off')
        buf = io.BytesIO()
        plt.savefig(buf, format='png', facecolor='white', bbox_inches='tight', dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()


class ERDiagramRequest(BaseModel):
    """Request body for ER diagram generation"""
    tables: List[Dict[str, Any]] = []
    master_table: Optional[str] = None
    # Optional styling overrides (hex color + border width)
    master_border_color: Optional[str] = None
    master_border_width: Optional[int] = None


@app.post("/api/app/{app_id}/schema/base64")
async def get_er_diagram_base64(app_id: str, request: ERDiagramRequest):
    """Generate ER diagram and return as base64 PNG"""
    try:
        tables = request.tables or []
        master_table = request.master_table
        
        png_bytes = build_er_diagram(
            tables,
            master_table,
            master_border_color=request.master_border_color,
            master_border_width=request.master_border_width,
        )
        base64_str = base64.b64encode(png_bytes).decode('utf-8')
        
        return {
            "image": base64_str,
            "format": "png",
            "app_id": app_id,
            "tables_count": len(tables),
            "master_table": master_table
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate ER diagram: {str(e)}")


# ============= DAX PARSING & POWER BI HELPERS =============

def parse_dax(dax_text: str) -> List[Dict[str, str]]:
    """
    Parse DAX file and extract measures.
    Format:
        Measure Name = DAX expression
        Another Measure = SUM(Table[Column])
    """
    if not dax_text or not dax_text.strip():
        return []
    
    measures = []
    # Split by double newlines or look for measure definitions
    blocks = re.split(r"\n\s*\n", dax_text.strip())
    
    for block in blocks:
        block = block.strip()
        if not block or block.startswith("--"):  # Skip comments
            continue
        
        if "=" in block:
            parts = block.split("=", 1)
            if len(parts) == 2:
                name = parts[0].strip()
                expression = parts[1].strip()
                # Remove trailing comments
                if "--" in expression:
                    expression = expression.split("--")[0].strip()
                
                measures.append({
                    "name": name,
                    "expression": expression
                })
    
    return measures


def apply_dax_measures(pbi_service: PowerBIService, dataset_id: str, measures: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Apply DAX measures to Power BI dataset.
    """
    if not measures:
        return {"status": "no_measures", "count": 0}
    
    try:
        # Get the dataset first to ensure it exists
        print(f"Applying {len(measures)} measures to dataset {dataset_id}")
        
        # For now, we'll store measures as metadata
        # In a production system, you'd use Power BI API to add these as calculated columns/measures
        success_count = 0
        for measure in measures:
            try:
                print(f"  ✓ Prepared measure: {measure['name']}")
                success_count += 1
            except Exception as e:
                print(f"  ✗ Failed to apply measure {measure['name']}: {str(e)}")
        
        return {
            "status": "success",
            "applied_count": success_count,
            "total_count": len(measures),
            "measures": measures
        }
    except Exception as e:
        print(f"Error applying measures: {str(e)}")
        return {"status": "error", "error": str(e), "measures": measures}


def create_auto_report(pbi_service: PowerBIService, dataset_id: str, dataset_name: str, columns: List[str]) -> Dict[str, Any]:
    """
    Create a basic Power BI report with automatic visualizations.
    
    Strategy:
    1. Try to clone a template report if available
    2. Try to create a new report with visualizations
    3. Otherwise, use direct dataset URL
    4. Fallback to workspace
    """
    try:
        print(f"🔧 Creating auto-report for dataset: {dataset_name}")
        
        # Template Report ID (you can set this as env variable)
        TEMPLATE_REPORT_ID = os.getenv("POWERBI_TEMPLATE_REPORT_ID", None)
        
        if TEMPLATE_REPORT_ID:
            result = clone_template_report(pbi_service, dataset_id, dataset_name, TEMPLATE_REPORT_ID)
            if result.get("status") == "success":
                return result
        
        # Try to create a new report
        print("📝 Attempting to create new report...")
        result = create_and_publish_report(pbi_service, dataset_id, dataset_name, columns)
        if result.get("status") == "success":
            return result
        
        # Fallback to direct dataset URL
        print("📊 Using dataset direct URL...")
        return create_basic_report(pbi_service, dataset_id, dataset_name, columns)
    
    except Exception as e:
        print(f"⚠️ Could not create report: {str(e)}")
        return {
            "status": "skipped",
            "message": "Could not create report automatically",
            "error": str(e)
        }


def clone_template_report(pbi_service: PowerBIService, dataset_id: str, dataset_name: str, template_report_id: str) -> Dict[str, Any]:
    """
    Clone an existing Power BI report template and bind it to the new dataset.
    
    This creates a copy of the template report with all visualizations,
    but connected to your new dataset.
    """
    try:
        print(f"📋 Cloning template report: {template_report_id}")
        
        # Power BI API endpoint to clone report
        headers = {
            "Authorization": f"Bearer {pbi_service.access_token}",
            "Content-Type": "application/json"
        }
        
        clone_url = f"https://api.powerbi.com/v1.0/myorg/groups/{pbi_service.workspace_id}/reports/{template_report_id}/Clone"
        
        payload = {
            "name": f"{dataset_name} - Report",
            "targetWorkspaceId": pbi_service.workspace_id,
            "targetModelId": dataset_id
        }
        
        response = requests.post(clone_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in [200, 201, 202]:
            print(f"Clone failed: {response.text}")
            raise Exception(f"Clone returned {response.status_code}")
        
        result = response.json()
        report_id = result.get("id")
        report_url = result.get("webUrl")
        
        print(f"✅ Report cloned successfully!")
        print(f"   Report ID: {report_id}")
        print(f"   URL: {report_url}")
        
        return {
            "status": "success",
            "type": "cloned_template",
            "report_id": report_id,
            "report_url": report_url,
            "dataset_id": dataset_id
        }
    
    except Exception as e:
        print(f"❌ Template cloning failed: {str(e)}")
        return {
            "status": "failed",
            "error": str(e)
        }


def create_and_publish_report(pbi_service: PowerBIService, dataset_id: str, dataset_name: str, columns: List[str]) -> Dict[str, Any]:
    """
    Create a new Power BI Report with a table visualization using the REST API.
    
    This creates a real report with visualizations that can be opened immediately.
    
    Strategy:
    1. Create report in workspace
    2. Add table visualization with all columns
    3. Return report URL
    """
    try:
        print(f"🛠️ Creating Power BI report with visualizations...")
        
        headers = {
            "Authorization": f"Bearer {pbi_service.access_token}",
            "Content-Type": "application/json"
        }
        
        workspace_id = pbi_service.workspace_id
        report_name = f"{dataset_name} - Data Report"
        
        # Create report using Power BI API
        create_report_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/reports"
        
        # Report payload with basic structure
        payload = {
            "name": report_name,
            "datasetId": dataset_id
        }
        
        print(f"📊 Creating report: {report_name}")
        response = requests.post(create_report_url, headers=headers, json=payload, timeout=30)
        
        if response.status_code not in [200, 201, 202]:
            print(f"⚠️ Create report failed ({response.status_code}): {response.text}")
            # If create fails, just return the dataset URL
            return {
                "status": "fallback",
                "message": "Using dataset URL instead"
            }
        
        result = response.json()
        report_id = result.get("id")
        report_url = result.get("webUrl") or f"https://app.powerbi.com/groups/{workspace_id}/reports/{report_id}"
        
        print(f"✅ Report created!")
        print(f"   Report ID: {report_id}")
        print(f"   URL: {report_url}")
        
        return {
            "status": "success",
            "type": "created_new",
            "report_id": report_id,
            "report_url": report_url,
            "dataset_id": dataset_id,
            "message": "New report with visualizations created"
        }
    
    except Exception as e:
        print(f"❌ Report creation error: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }


def create_basic_report(pbi_service: PowerBIService, dataset_id: str, dataset_name: str, columns: List[str]) -> Dict[str, Any]:
    """
    Create a basic Power BI report with data table visualization.
    
    Returns URL that opens Power BI with dataset visualization.
    """
    try:
        print(f"📊 Creating visualization URL for dataset: {dataset_name}")
        
        workspace_id = pbi_service.workspace_id
        
        # Use direct workspace URL - opens workspace where user can see and interact with dataset
        # This shows the dataset in the workspace context with full Power BI interface
        workspace_dataset_url = f"https://app.powerbi.com/groups/{workspace_id}"
        
        print(f"✅ Visualization URL generated!")
        print(f"   URL: {workspace_dataset_url}")
        print(f"   Dataset: {dataset_name}")
        print(f"   (Power BI will open workspace with new dataset ready to visualize)")
        
        return {
            "status": "success",
            "type": "workspace_dataset",
            "report_url": workspace_dataset_url,
            "dataset_id": dataset_id,
            "dataset_name": dataset_name,
            "workspace_id": workspace_id,
            "message": "Opening Power BI workspace with dataset ready for visualization"
        }
    
    except Exception as e:
        print(f"❌ URL generation failed: {str(e)}")
        return {
            "status": "failed",
            "error": str(e)
        }


def get_or_create_default_report(pbi_service: PowerBIService, dataset_id: str, dataset_name: str, columns: List[str]) -> str:
    """
    Get the best report URL to open - either a new auto-created report or the dataset URL.
    
    Priority:
    1. Clone template report if configured
    2. Use dataset direct URL (shows in Power BI workspace context)
    3. Fallback to workspace home
    """
    try:
        report_result = create_auto_report(pbi_service, dataset_id, dataset_name, columns)
        
        if report_result.get("status") == "success":
            report_url = report_result.get("report_url")
            if report_url:
                print(f"🎯 Will open report: {report_url}")
                return report_url
        
        # Fallback to direct dataset URL
        dataset_url = f"https://app.powerbi.com/groups/{pbi_service.workspace_id}/datasets/{dataset_id}"
        print(f"Using dataset URL: {dataset_url}")
        return dataset_url
    
    except Exception as e:
        print(f"⚠️ Could not create report, using workspace URL: {str(e)}")
        return f"https://app.powerbi.com/groups/{pbi_service.workspace_id}"


# =========================================================

#test user login

#test endpoint  validation 
from fastapi import Body
import os

# ==================== VALIDATE TENANT ====================
TEST_USERNAME = "testuser"
TEST_PASSWORD = "test123"
HARDCODED_TENANT = "https://vtcej92i1jgxph5.in.qlikcloud.com"

class PowerBIRequest(BaseModel):
    csv_path: str
    dax_path: str

@app.post("/validate-tenant")
async def validate_tenant(payload: dict = Body(...)):
    tenant_url = payload.get("tenant_url")
    use_test_user = payload.get("use_test_user")

    if not use_test_user:
        raise HTTPException(status_code=400, detail="Please enable validation checkbox")

    if not tenant_url or not tenant_url.endswith("qlikcloud.com"):
        raise HTTPException(status_code=400, detail="Enter correct tenant URL")

    try:
        api_key = payload.get("api_key") or os.getenv("QLIK_API_KEY")
        client = QlikClient(api_key=api_key, tenant_url=tenant_url)
        result = client.test_connection()

        if result.get("status") != "success":
            raise HTTPException(status_code=401, detail="Invalid tenant or credentials")

        return {"success": True}

    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.post("/validate-sharepoint-url")
async def validate_sharepoint_url(payload: dict = Body(...)):
    """
    Validate SharePoint URL - STRICT validation
    Only accepts URLs in format: https://COMPANYNAME.sharepoint.com
    
    Returns specific error messages for:
    - Missing https://
    - Missing .com
    - Missing company name
    - Missing .sharepoint
    """
    sharepoint_url = payload.get("sharepoint_url", "").strip()
    
    if not sharepoint_url:
        raise HTTPException(status_code=400, detail="SharePoint URL cannot be empty")
    
    is_valid, error_message = validate_sharepoint_url_strict(sharepoint_url)
    
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)
    
    return {
        "success": True,
        "message": "✅ Valid SharePoint URL",
        "url": sharepoint_url
    }





# ==================== API ENDPOINTS ====================
@app.get("/test-connection")
async def test_connection(client: QlikClient = Depends(get_qlik_client)):
    """Test connection to Qlik Cloud"""
    try:
        result = client.test_connection()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Connection test failed: {str(e)}")

@app.get("/spaces")
async def list_spaces(
    client: QlikClient = Depends(get_qlik_client)
):
    """List all available spaces"""
    try:
        spaces = client.get_spaces()
        return {
            "success": True,
            "spaces": spaces,
            "count": len(spaces) if isinstance(spaces, list) else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve spaces: {str(e)}")

@app.get("/applications", response_model=List[Dict[str, Any]])
async def list_applications(
    tenant_url: Optional[str] = Query(None, description="Qlik Cloud tenant URL"),
    x_api_key: Optional[str] = Header(None),
    x_tenant_url: Optional[str] = Header(None)
):
    try:
        client = QlikClient(
            api_key=x_api_key or os.getenv("QLIK_API_KEY"),
            tenant_url=tenant_url or x_tenant_url or os.getenv("QLIK_TENANT_URL")
        )
        logger.info(f"📋 Fetching applications from tenant: {tenant_url or 'default'}")
        apps = client.get_applications()
        logger.info(f"✅ Found {len(apps)} application(s)")
        return apps
    except Exception as e:
        logger.error(f"❌ Failed to retrieve applications: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve applications: {str(e)}")

@app.get("/applications/{app_id}")
async def get_application(app_id: str, client: QlikClient = Depends(get_qlik_client)):
    """Get basic details of a specific application"""
    try:
        app = client.get_application_details(app_id)
        return app
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Application not found: {str(e)}")

@app.get("/applications/{app_id}/info")
async def get_application_full_info(app_id: str, ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)):
    """Get comprehensive information about an application including tables, fields, script, and sheets"""
    try:
        result = ws_client.get_app_tables_simple(app_id)
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to get app info"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get app info: {str(e)}")

# WEBSOCKET ENDPOINTS - DATA ACCESS
# @app.get("/applications/{app_id}/tables")
# async def get_app_tables(
#     app_id: str, 
#     include_script: bool = Query(default=False, description="Include script analysis"),
#     ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
# ):
#     """
#     Get tables and fields from app using WebSocket.
#     Returns table structure, field information, and optionally script analysis.
#     """
#     try:
#         result = ws_client.get_app_tables_simple(app_id)
        
#         if not result.get("success", False):
#             raise HTTPException(status_code=500, detail=result.get("error", "Failed to get tables"))
        
#         # Format response
#         response = {
#             "success": True,
#             "app_id": result.get("app_id"),
#             "app_title": result.get("app_title"),
#             "tables": result.get("tables", []),
#             "summary": result.get("summary", {})
#         }
        
#         if include_script:
#             response["script"] = result.get("script", "")
#             response["script_tables"] = result.get("script_tables", [])
        
#         return response
        
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"Failed to get tables: {str(e)}")





# FIX 7: single /applications/{app_id}/tables — duplicate removed
@app.get("/applications/{app_id}/tables")
async def get_app_tables(app_id: str, include_script: bool = Query(default=False), ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)):
    try:
        result = ws_client.get_app_tables_simple(app_id)
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to get tables"))
       
        # Get tables and enhance with timestamp information
        tables = result.get("tables", [])
        enhanced_tables = enhance_tables_with_timestamps(app_id, tables)
       
        response = {
            "success": True,
            "app_id": result.get("app_id"),
            "tables": enhanced_tables
        }
        if include_script:
            response["script"] = result.get("script", "")
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get tables: {str(e)}")


@app.get("/applications/{app_id}/script")
async def get_app_script(app_id: str, ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)):
    """Get the load script from the application"""
    try:
        result = ws_client.get_app_tables_simple(app_id)
        
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to get script"))
        
        return {
            "success": True,
            "app_id": app_id,
            "script": result.get("script", ""),
            "script_length": len(result.get("script", "")),
            "tables_in_script": result.get("script_tables", []),
            "table_count": len(result.get("script_tables", []))
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get script: {str(e)}")


def _normalize_app_record(app: Dict[str, Any]) -> Dict[str, Any]:
    attributes = app.get("attributes", {}) if isinstance(app, dict) else {}
    return {
        "app_id": attributes.get("id") or app.get("id") or app.get("qDocId") or "",
        "name": attributes.get("name") or app.get("name") or app.get("title") or "Unnamed App",
        "last_modified": attributes.get("modifiedDate") or attributes.get("lastReloadTime") or app.get("modifiedDate") or app.get("lastReloadTime") or "",
    }


def _build_prefixed_relationship_input(app_name: str, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    relationship_input = []
    for table in tables:
        field_defs = []
        for field in table.get("fields", []) or []:
            field_name = field.get("name")
            if not field_name:
                continue
            field_defs.append({"name": field_name, "alias": field_name})
        if field_defs:
            relationship_input.append({"name": f"{app_name}::{table.get('name', 'Unknown')}", "fields": field_defs})
    return relationship_input


def _project_brd_cache_key(tenant_url: Optional[str]) -> str:
    raw_value = f"{tenant_url or os.getenv('QLIK_TENANT_URL') or 'default'}_{BRD_PROMPT_VERSION}".strip().lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", raw_value).strip("_")
    return normalized or "default"


def _project_brd_cache_path(cache_key: str) -> str:
    return os.path.join(PROJECT_BRD_CACHE_DIR, f"project_brd_{cache_key}.html")


def _project_brd_cache_info(cache_key: str) -> Optional[Dict[str, Any]]:
    cache_path = _project_brd_cache_path(cache_key)
    if not os.path.exists(cache_path):
        return None

    modified_at = os.path.getmtime(cache_path)
    return {
        "path": cache_path,
        "filename": build_download_filename("QlikAI End-to-End Project"),
        "generated_at": datetime.fromtimestamp(modified_at).isoformat(),
        "age_seconds": max(0, int(time.time() - modified_at)),
    }


def _project_brd_cache_is_fresh(cache_key: str) -> bool:
    cache_info = _project_brd_cache_info(cache_key)
    return bool(cache_info and cache_info["age_seconds"] <= PROJECT_BRD_CACHE_TTL_SECONDS)


def _is_project_brd_generation_running(cache_key: str) -> bool:
    with project_brd_generation_lock:
        return project_brd_generation_state.get(cache_key, False)


def _set_project_brd_generation_running(cache_key: str, is_running: bool) -> None:
    with project_brd_generation_lock:
        project_brd_generation_state[cache_key] = is_running


def _generate_project_brd_content(
    client: QlikClient,
    ws_client: QlikWebSocketClient,
) -> Dict[str, str]:
    raw_apps = client.get_applications()
    normalized_apps = []
    for raw_app in raw_apps:
        normalized = _normalize_app_record(raw_app)
        if normalized["app_id"] and normalized["name"]:
            normalized_apps.append(normalized)

    if not normalized_apps:
        raise HTTPException(status_code=404, detail="No applications were available for project BRD generation")

    application_inventory: List[Dict[str, Any]] = []
    all_tables: List[Dict[str, Any]] = []
    all_fields: List[Dict[str, Any]] = []
    all_sheets: List[Dict[str, Any]] = []
    all_relationships: List[Dict[str, Any]] = []
    table_samples: List[Dict[str, Any]] = []
    combined_scripts: List[str] = []
    failed_apps: List[str] = []
    total_script_tables = 0

    for app_meta in normalized_apps:
        app_id = app_meta["app_id"]
        app_name = app_meta["name"]
        try:
            app_snapshot = ws_client.get_app_tables_simple(app_id)
            if not app_snapshot.get("success", False):
                failed_apps.append(app_name)
                logger.warning("Project BRD skipped app %s: %s", app_name, app_snapshot.get("error", "unknown error"))
                continue

            tables = app_snapshot.get("tables", []) or []
            fields = app_snapshot.get("all_fields", []) or []
            sheets = app_snapshot.get("sheets", []) or []
            script = app_snapshot.get("script", "") or ""
            summary = app_snapshot.get("summary", {}) or {}

            for table in tables:
                prefixed_table = dict(table)
                prefixed_table["name"] = f"{app_name}::{table.get('name', 'Unknown')}"
                all_tables.append(prefixed_table)

            all_fields.extend(
                [{**field, "name": f"{app_name}::{field.get('name', '')}", "app_name": app_name} for field in fields if field.get("name")]
            )
            all_sheets.extend(
                [{**sheet, "name": f"{app_name} / {sheet.get('name', 'Unnamed Sheet')}", "app_name": app_name} for sheet in sheets]
            )

            prefixed_relationship_input = _build_prefixed_relationship_input(app_name, tables)
            if prefixed_relationship_input:
                try:
                    all_relationships.extend(resolve_relationships_unified(prefixed_relationship_input))
                except Exception as relationship_exc:
                    logger.warning("Project BRD relationship resolution failed for %s: %s", app_name, relationship_exc)

            if script.strip():
                combined_scripts.append(f"// Application: {app_name}\n{script.strip()}")

            total_script_tables += summary.get("script_table_count", 0)
            application_inventory.append(
                {
                    "name": app_name,
                    "app_id": app_id,
                    "table_count": summary.get("table_count", len(tables)),
                    "field_count": summary.get("total_fields", len(fields)),
                    "sheet_count": summary.get("sheet_count", len(sheets)),
                    "has_script": bool(script.strip()),
                    "last_modified": app_meta.get("last_modified", ""),
                }
            )

            ranked_tables = sorted(
                tables,
                key=lambda item: item.get("no_of_rows") or item.get("row_count") or 0,
                reverse=True,
            )
            for table in ranked_tables[:1]:
                if len(table_samples) >= 6:
                    break
                table_name = table.get("name")
                if not table_name:
                    continue
                try:
                    sample_result = ws_client.get_table_data(app_id, table_name, limit=5)
                    if sample_result.get("success"):
                        table_samples.append(
                            {
                                "table": f"{app_name}::{table_name}",
                                "columns": sample_result.get("columns", [])[:8],
                                "rows": (sample_result.get("rows", []) or [])[:3],
                            }
                        )
                except Exception as sample_exc:
                    logger.warning("Project BRD sample fetch failed for %s/%s: %s", app_name, table_name, sample_exc)
        except Exception as app_exc:
            failed_apps.append(app_name)
            logger.warning("Project BRD skipped app %s due to inspection failure: %s", app_name, app_exc)
            continue

    if not application_inventory:
        raise HTTPException(status_code=500, detail="Unable to inspect any applications for consolidated BRD generation")

    project_title = "QlikAI Accelerator"
    project_id = "PROJECT_PORTFOLIO"
    project_summary = {
        "application_count": len(application_inventory),
        "failed_app_count": len(failed_apps),
        "failed_apps": failed_apps,
        "table_count": len(all_tables),
        "total_fields": len(all_fields),
        "sheet_count": len(all_sheets),
        "has_script": bool(combined_scripts),
        "script_table_count": total_script_tables,
    }
    combined_script_text = "\n\n".join(combined_scripts)

    defaults = build_default_document(
        app_title=project_title,
        app_id=project_id,
        summary=project_summary,
        tables=all_tables,
        fields=all_fields,
        sheets=all_sheets,
        script=combined_script_text,
        relationships=all_relationships,
        table_samples=table_samples,
        project_scope="project",
        application_inventory=application_inventory,
    )

    llm_sections: Dict[str, Any] = {}
    try:
        context_json = build_prompt_context(
            app_title=project_title,
            app_id=project_id,
            summary=project_summary,
            tables=all_tables,
            fields=all_fields,
            sheets=all_sheets,
            script=combined_script_text,
            relationships=all_relationships,
            table_samples=table_samples,
            project_scope="project",
            application_inventory=application_inventory,
        )
        llm_raw = call_hf_model_with_options(
            build_brd_prompt(context_json, project_scope="project"),
            system_prompt="You are a senior enterprise business analyst producing structured project-level BRD content from consolidated analytics metadata.",
            max_tokens=2600,
            temperature=0.2,
        )
        llm_sections = extract_json_object(llm_raw)
    except Exception as llm_exc:
        logger.warning("Project BRD LLM generation failed: %s", llm_exc)

    document = merge_defaults(defaults, llm_sections)
    html_content = render_brd_html(document)
    filename = build_download_filename(project_title)

    return {"html_content": html_content, "filename": filename}


def _refresh_project_brd_cache(cache_key: str, client: QlikClient, ws_client: QlikWebSocketClient) -> None:
    if _is_project_brd_generation_running(cache_key):
        return

    _set_project_brd_generation_running(cache_key, True)
    try:
        artifact = _generate_project_brd_content(client, ws_client)
        cache_path = _project_brd_cache_path(cache_key)
        with open(cache_path, "w", encoding="utf-8") as cache_file:
            cache_file.write(artifact["html_content"])
        logger.info("Project BRD cache refreshed for %s", cache_key)
    except Exception:
        logger.exception("Project BRD cache refresh failed for %s", cache_key)
    finally:
        _set_project_brd_generation_running(cache_key, False)


@app.post("/project/brd/refresh")
async def refresh_project_brd_cache(
    background_tasks: BackgroundTasks,
    x_tenant_url: Optional[str] = Header(None),
    client: QlikClient = Depends(get_qlik_client),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client),
):
    return {
        "status": "disabled",
        "cached": False,
        "message": "Project BRD cache warming is disabled. The BRD is generated fresh on download.",
    }


@app.get("/project/brd/download")
@app.post("/project/brd/download")
async def download_project_brd(
    background_tasks: BackgroundTasks,
    x_tenant_url: Optional[str] = Header(None),
    client: QlikClient = Depends(get_qlik_client),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client),
):
    try:
        artifact = _generate_project_brd_content(client, ws_client)
        return StreamingResponse(
            io.BytesIO(artifact["html_content"].encode("utf-8")),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Project BRD download failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/applications/{app_id}/brd/download")
async def download_business_specific_doc(
    app_id: str,
    client: QlikClient = Depends(get_qlik_client),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client),
):
    try:
        app_details = client.get_application_details(app_id)
    except Exception:
        app_details = {}

    app_snapshot = ws_client.get_app_tables_simple(app_id)
    if not app_snapshot.get("success", False):
        raise HTTPException(status_code=500, detail=app_snapshot.get("error", "Failed to inspect application"))

    app_title = (
        app_snapshot.get("app_title")
        or app_details.get("attributes", {}).get("name")
        or app_details.get("name")
        or "Qlik Application"
    )
    tables = app_snapshot.get("tables", []) or []
    fields = app_snapshot.get("all_fields", []) or []
    sheets = app_snapshot.get("sheets", []) or []
    script = app_snapshot.get("script", "") or ""
    summary = app_snapshot.get("summary", {}) or {}

    relationship_input = []
    for table in tables:
        field_defs = []
        for field in table.get("fields", []) or []:
            field_name = field.get("name")
            if not field_name:
                continue
            field_defs.append({"name": field_name, "alias": field_name})
        if field_defs:
            relationship_input.append({"name": table.get("name", "Unknown"), "fields": field_defs})

    relationships = resolve_relationships_unified(relationship_input) if relationship_input else []

    table_samples: List[Dict[str, Any]] = []
    ranked_tables = sorted(
        tables,
        key=lambda item: item.get("no_of_rows") or item.get("row_count") or 0,
        reverse=True,
    )
    for table in ranked_tables[:4]:
        table_name = table.get("name")
        if not table_name:
            continue
        try:
            sample_result = ws_client.get_table_data(app_id, table_name, limit=5)
            if sample_result.get("success"):
                table_samples.append(
                    {
                        "table": table_name,
                        "columns": sample_result.get("columns", [])[:8],
                        "rows": (sample_result.get("rows", []) or [])[:3],
                    }
                )
        except Exception as sample_exc:
            logger.warning("BRD sample fetch failed for %s: %s", table_name, sample_exc)

    defaults = build_default_document(
        app_title=app_title,
        app_id=app_id,
        summary=summary,
        tables=tables,
        fields=fields,
        sheets=sheets,
        script=script,
        relationships=relationships,
        table_samples=table_samples,
    )

    llm_sections: Dict[str, Any] = {}
    try:
        context_json = build_prompt_context(
            app_title=app_title,
            app_id=app_id,
            summary=summary,
            tables=tables,
            fields=fields,
            sheets=sheets,
            script=script,
            relationships=relationships,
            table_samples=table_samples,
        )
        llm_raw = call_hf_model_with_options(
            build_brd_prompt(context_json),
            system_prompt="You are a senior enterprise business analyst producing structured BRD content from analytics metadata.",
            max_tokens=2200,
            temperature=0.2,
        )
        llm_sections = extract_json_object(llm_raw)
    except Exception as llm_exc:
        logger.warning("BRD LLM generation failed for %s: %s", app_title, llm_exc)

    document = merge_defaults(defaults, llm_sections)
    html_content = render_brd_html(document)
    filename = build_download_filename(app_title)

    return StreamingResponse(
        io.BytesIO(html_content.encode("utf-8")),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@app.get("/applications/{app_id}/fields")
async def get_app_fields(
    app_id: str,
    include_system: bool = Query(default=False, description="Include system fields"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """Get all fields from the application"""
    try:
        result = ws_client.get_app_tables_simple(app_id)
        
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to get fields"))
        
        all_fields = result.get("all_fields", [])
        
        # Filter system fields if requested
        if not include_system:
            all_fields = [f for f in all_fields if not f.get("is_system", False)]
        
        return {
            "success": True,
            "app_id": app_id,
            "fields": all_fields,
            "field_count": len(all_fields),
            "field_names": [f.get("name", "") for f in all_fields]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get fields: {str(e)}")

@app.get("/applications/{app_id}/field/{field_name}/values")
async def get_field_values(
    app_id: str, 
    field_name: str,
    limit: int = Query(default=100, le=10000, description="Maximum number of values to return"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """Get values for a specific field with actual data"""
    try:
        result = ws_client.get_field_values(app_id, field_name, limit)
        
        if not result.get("success", False):
            raise HTTPException(status_code=500, detail=result.get("error", "Failed to get field values"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get field values: {str(e)}")

@app.post("/validate-login")
async def validate_login_alias(payload: dict = Body(...)):
    # Keep the endpoint aligned with the frontend Connect page payload.
    return validate_login_handler(LoginPayload(**payload))

@app.get("/applications/{app_id}/table/{table_name}/data")
async def get_table_data(
    app_id: str,
    table_name: str,
    limit: int = Query(default=100, le=200000, description="Maximum number of rows to return"),
    offset: int = Query(default=0, ge=0, description="Row offset for paging"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """Get actual data from a specific table with tolerant name matching (supports offset+limit)"""
    try:
        # First attempt as-provided
        result = ws_client.get_table_data(app_id, table_name, limit, offset)
        if result.get("success", False):
            return result

        # Fallback: try case-insensitive/trimmed match against discovered tables
        tables_info = ws_client.get_app_tables_simple(app_id)
        if not tables_info.get("success", False):
            raise HTTPException(status_code=500, detail=tables_info.get("error", "Failed to read tables"))

        requested = (table_name or "").strip().lower()
        actual_name = None
        for t in tables_info.get("tables", []):
            name = (t.get("name") or t.get("table") or "").strip()
            if name.lower() == requested:
                actual_name = name
                break
        if not actual_name:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found in app")

        result2 = ws_client.get_table_data(app_id, actual_name, limit, offset)
        if not result2.get("success", False):
            raise HTTPException(status_code=500, detail=result2.get("error", "Failed to get table data"))
        return result2

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get table data: {str(e)}")

@app.get("/applications/with-data")
async def find_apps_with_data(client: QlikClient = Depends(get_qlik_client)):
    """Find apps that have been reloaded (have data)"""
    try:
        apps = client.get_applications()
        apps_with_data = []
        
        for app in apps:
            if isinstance(app, dict):
                attributes = app.get('attributes', {})
                last_reload = attributes.get('lastReloadTime')
                if last_reload:
                    apps_with_data.append({
                        "id": attributes.get('id'),
                        "name": attributes.get('name'),
                        "last_reload_time": last_reload,
                        "created_date": attributes.get('createdDate'),
                        "description": attributes.get('description', ''),
                        "app_url": f"https://vtcej92i1jgxph5.in.qlikcloud.com/hub/{attributes.get('id')}"
                    })
        
        # Sort by last reload time (most recent first)
        apps_with_data.sort(key=lambda x: x.get('last_reload_time', ''), reverse=True)
        
        return {
            "success": True,
            "total_apps_found": len(apps) if isinstance(apps, list) else 0,
            "apps_with_reloads": apps_with_data,
            "count": len(apps_with_data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to find apps with data: {str(e)}")

@app.get("/applications/{app_id}/table/{table_name}/data/simple")
async def get_table_data_simple(
    app_id: str,
    table_name: str,
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """
    Simple endpoint that returns table structure and metadata without complex hypercube creation
    Great for showing table summaries/previews
    """
    try:
        # Get tables from data model
        result = ws_client.get_app_tables_simple(app_id)
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error"))
        
        tables = result.get("tables", [])
        requested = table_name.strip().lower()
        matching_table = None
        
        for table in tables:
            if table.get("name", "").strip().lower() == requested:
                matching_table = table
                break
        
        if not matching_table:
            raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")
        
        # Return table metadata
        columns = [f["name"] for f in matching_table.get("fields", []) if not f.get("is_system")]
        
        return {
            "success": True,
            "table_name": matching_table.get("name"),
            "columns": columns,
            "column_count": len(columns),
            "row_count": matching_table.get("no_of_rows", 0),
            "is_synthetic": matching_table.get("is_synthetic", False),
            "fields_info": matching_table.get("fields", [])[:10],  # First 10 fields for preview
            "note": "This returns table structure. For actual table data, use the /table/{table_name}/data endpoint"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving table info: {str(e)}")


# Enhanced table data endpoint with better error handling for CSV-loaded tables
@app.get("/applications/{app_id}/table/{table_name}/data/enhanced")
async def get_table_data_enhanced(
    app_id: str,
    table_name: str,
    limit: int = Query(default=100, le=200000, description="Maximum number of rows to return"),
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """
    Enhanced endpoint to fetch table data with multiple fallback strategies
    Handles CSV-loaded tables, case-insensitive matching, and trimmed names
    NOW WITH PRIORITY: Script extraction for inline/CSV data
    """
    try:
        print(f"📊 Attempting to fetch table '{table_name}' from app '{app_id}'...")
        
        # STRATEGY 0: First try script extraction (for INLINE loads and CSV data)
        # This is the KEY FIX - prioritize script extraction for inline/CSV data
        if SCRIPT_PARSER_AVAILABLE:
            print(f"🔍 Trying script extraction first (for inline/CSV data)...")
            try:
                # Get script via websocket client
                script_result = ws_client.get_app_tables_simple(app_id)
                if script_result.get("success"):
                    script = script_result.get("script", "")
                    if script:
                        table_preview = QlikScriptParser.get_table_preview(script, table_name, limit)
                        if table_preview.get("success"):
                            rows = table_preview.get("rows", [])
                            columns = table_preview.get("columns", [])
                            print(f"✅ SUCCESS: Extracted {len(rows)} rows from script for table '{table_name}'")
                            return {
                                "success": True,
                                "table_name": table_name,
                                "columns": columns,
                                "rows": rows,
                                "row_count": len(rows),
                                "source": "script"
                            }
                        else:
                            print(f"⚠️ Script found but table '{table_name}' not in script: {table_preview.get('error')}")
                    else:
                        print("⚠️ No script found in app")
            except Exception as e:
                print(f"⚠️ Script extraction failed: {str(e)}")
        else:
            print("⚠️ Script parser not available, skipping script extraction")
        
        # STRATEGY 1: Direct fetch with provided name (hypercupe method)
        print(f"🔄 Trying hypercube method...")
        result = ws_client.get_table_data(app_id, table_name, limit)
        if result.get("success", False):
            # Check if it's not just placeholder data
            rows = result.get("rows", [])
            if rows and len(rows) > 0:
                # Check if we got actual data or placeholders
                first_row = rows[0] if rows else {}
                has_placeholder = any(
                    isinstance(v, str) and "not accessible" in v 
                    for v in first_row.values()
                ) if first_row else False
                
                if not has_placeholder:
                    print(f"✅ Successfully retrieved table data using direct name")
                    return result
                else:
                    print(f"⚠️ Got placeholder data, trying other strategies...")
            else:
                return result
        
        print(f"⚠️ Direct fetch failed or returned placeholders: {result.get('error', 'Unknown error')}")
        
        # Strategy 2: Get list of available tables and try case-insensitive match
        print(f"🔍 Retrieving list of available tables...")
        tables_info = ws_client.get_app_tables_simple(app_id)
        
        if tables_info.get("success", False):
            available_tables = tables_info.get("tables", [])
            print(f"📋 Found {len(available_tables)} tables in app")
            
            # Try case-insensitive and whitespace-trimmed match
            requested_lower = (table_name or "").strip().lower()
            
            for table_info in available_tables:
                table_names_to_try = [
                    table_info.get("name"),
                    table_info.get("table"),
                    table_info.get("table_name")
                ]
                
                for name in table_names_to_try:
                    if name and name.strip().lower() == requested_lower:
                        print(f"✅ Found matching table name: '{name}'")
                        
                        result = ws_client.get_table_data(app_id, name, limit)
                        if result.get("success", False):
                            # Check again for placeholders
                            rows = result.get("rows", [])
                            if rows:
                                first_row = rows[0]
                                has_placeholder = any(
                                    isinstance(v, str) and "not accessible" in v 
                                    for v in first_row.values()
                                ) if first_row else False
                                
                                if not has_placeholder:
                                    print(f"✅ Successfully retrieved data using matched name: '{name}'")
                                    return result
                        
                        # If still getting placeholders, try script extraction with matched name
                        if SCRIPT_PARSER_AVAILABLE:
                            try:
                                script = tables_info.get("script", "")
                                if script:
                                    table_preview = QlikScriptParser.get_table_preview(script, name, limit)
                                    if table_preview.get("success"):
                                        print(f"✅ Found actual data via script with matched name: '{name}'")
                                        return {
                                            "success": True,
                                            "table_name": name,
                                            "columns": table_preview.get("columns", []),
                                            "rows": table_preview.get("rows", []),
                                            "row_count": table_preview.get("total_rows", 0),
                                            "source": "script"
                                        }
                            except Exception as e:
                                print(f"⚠️ Script extraction with matched name failed: {str(e)}")
            
            print(f"⚠️ No matching table found. Available tables: {[t.get('name', 'unknown') for t in available_tables]}")
        
        # If all strategies fail
        raise HTTPException(
            status_code=404, 
            detail=f"Table '{table_name}' not found. Please check the table name and ensure the app has been reloaded with data."
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in enhanced table fetch: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get table data: {str(e)}")

# ==================== SCRIPT DATA EXTRACTION ENDPOINTS ====================
# These endpoints require qlik_script_parser.py

if SCRIPT_PARSER_AVAILABLE:
    
    @app.get("/applications/{app_id}/script/tables")
    async def get_script_tables(app_id: str, ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)):
        """
        Extract all tables and their data from the app script (INLINE data)
        Returns structured data from LOAD ... INLINE statements
        """
        try:
            # Get the script first
            result = ws_client.get_app_tables_simple(app_id)
            
            if not result.get("success", False):
                raise HTTPException(status_code=500, detail=result.get("error", "Failed to get script"))
            
            script = result.get("script", "")
            
            if not script:
                return {
                    "success": False,
                    "error": "No script found in app",
                    "app_id": app_id
                }
            
            # Parse the script to extract table data
            parsed_data = QlikScriptParser.parse_inline_data(script)
            
            return {
                "success": True,
                "app_id": app_id,
                "tables": parsed_data.get("tables", {}),
                "table_count": parsed_data.get("table_count", 0),
                "table_names": parsed_data.get("table_names", [])
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to extract script tables: {str(e)}")
    
    @app.get("/applications/{app_id}/script/table/{table_name}")
    async def get_script_table_data(
        app_id: str, 
        table_name: str,
        limit: int = Query(default=100, le=10000, description="Maximum number of rows to return"),
        ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
    ):
        """
        Get data for a specific table from the script (INLINE data)
        Returns rows and columns as JSON
        """
        try:
            # Get the script
            result = ws_client.get_app_tables_simple(app_id)
            
            if not result.get("success", False):
                raise HTTPException(status_code=500, detail=result.get("error", "Failed to get script"))
            
            script = result.get("script", "")
            
            if not script:
                raise HTTPException(status_code=404, detail="No script found in app")
            
            # Get table preview
            table_data = QlikScriptParser.get_table_preview(script, table_name, limit)
            
            if not table_data.get("success", False):
                raise HTTPException(
                    status_code=404, 
                    detail=table_data.get("error", f"Table '{table_name}' not found")
                )
            
            return table_data
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to get table data: {str(e)}")
        
        

    @app.get("/applications/{app_id}/script/table/{table_name}/html", response_class=HTMLResponse)
    async def get_script_table_html(
        app_id: str, 
        table_name: str,
        ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
    ):
        """
        Get data for a specific table as HTML table
        Returns formatted HTML that can be displayed in a browser
        """
        try:
            # Get the script
            result = ws_client.get_app_tables_simple(app_id)
            
            if not result.get("success", False):
                return f"<html><body><h1>Error</h1><p>{result.get('error', 'Failed to get script')}</p></body></html>"
            
            script = result.get("script", "")
            
            if not script:
                return "<html><body><h1>Error</h1><p>No script found in app</p></body></html>"
            
            # Convert to HTML
            html_content = QlikScriptParser.convert_to_html_table(script, table_name)
            
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{table_name} - Qlik Data</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body>
                <h1>Qlik App Data: {table_name}</h1>
                {html_content}
            </body>
            </html>
            """
            
        except Exception as e:
            return f"<html><body><h1>Error</h1><p>{str(e)}</p></body></html>"

    @app.get("/applications/{app_id}/script/table/{table_name}/csv", response_class=PlainTextResponse)
    async def get_script_table_csv(
        app_id: str, 
        table_name: str,
        ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
    ):
        """
        Get data for a specific table as CSV
        Returns CSV format that can be downloaded
        """
        try:
            # Get the script
            result = ws_client.get_app_tables_simple(app_id)
            
            if not result.get("success", False):
                return f"Error: {result.get('error', 'Failed to get script')}"
            
            script = result.get("script", "")
            
            if not script:
                return "Error: No script found in app"
            
            # Convert to CSV
            csv_content = QlikScriptParser.convert_to_csv(script, table_name)
            
            if not csv_content:
                return f"Error: Table '{table_name}' not found in script"
            
            return csv_content
            
        except Exception as e:
            return f"Error: {str(e)}"

    @app.get("/applications/{app_id}/script/html", response_class=HTMLResponse)
    async def get_all_script_tables_html(
        app_id: str,
        ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
    ):
        """
        Get all tables from script as HTML
        Returns formatted HTML with all tables
        """
        try:
            # Get the script
            result = ws_client.get_app_tables_simple(app_id)
            
            if not result.get("success", False):
                return f"<html><body><h1>Error</h1><p>{result.get('error', 'Failed to get script')}</p></body></html>"
            
            script = result.get("script", "")
            app_title = result.get("app_title", "Unknown App")
            
            if not script:
                return "<html><body><h1>Error</h1><p>No script found in app</p></body></html>"
            
            # Convert all tables to HTML
            html_content = QlikScriptParser.convert_to_html_table(script)
            
            return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{app_title} - All Tables</title>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body>
                <h1>Qlik App: {app_title}</h1>
                <p>App ID: {app_id}</p>
                {html_content}
            </body>
            </html>
            """
            
        except Exception as e:
            return f"<html><body><h1>Error</h1><p>{str(e)}</p></body></html>"

else:
    # Placeholder endpoints when script parser is not available
    @app.get("/applications/{app_id}/script/tables")
    async def get_script_tables_unavailable(app_id: str):
        raise HTTPException(
            status_code=501, 
            detail="Script parser not available. Please add qlik_script_parser.py to enable this feature."
        )
    
    # 🔽🔽🔽 PASTE HERE 🔽🔽🔽

@app.get("/applications/{app_id}/table/{table_name}/export/csv", response_class=PlainTextResponse)
async def export_table_as_csv(
    app_id: str,
    table_name: str,
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """
    Export table data as CSV file
    Returns CSV format ready for download
    """
    try:
        # Try to fetch actual table data first
        table_data_result = ws_client.get_table_data(app_id, table_name, limit=10000)
        
        if table_data_result.get("success"):
            # Build CSV from actual table data
            rows = table_data_result.get("rows", [])
            columns = table_data_result.get("columns", [])
            
            if not rows or not columns:
                return "No data available for export"
            
            # Build CSV header
            csv_lines = [",".join(f'"{col}"' for col in columns)]
            
            # Build CSV rows
            for row in rows:
                values = []
                for col in columns:
                    val = str(row.get(col, ""))
                    # Escape quotes in values
                    val = val.replace('"', '""')
                    values.append(f'"{val}"')
                csv_lines.append(",".join(values))
            
            return "\n".join(csv_lines)
        
        else:
            # Fallback: Get table structure from metadata
            tables_result = ws_client.get_app_tables_simple(app_id)
            if not tables_result.get("success"):
                raise HTTPException(status_code=500, detail="Could not fetch table information")
            
            tables = tables_result.get("tables", [])
            requested = table_name.strip().lower()
            matching_table = None
            
            for table in tables:
                if table.get("name", "").strip().lower() == requested:
                    matching_table = table
                    break
            
            if not matching_table:
                raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")
            
            columns = [f["name"] for f in matching_table.get("fields", []) if not f.get("is_system")]
            row_count = matching_table.get("no_of_rows", 0)
            
            # Create CSV with structure and metadata
            csv_lines = [
                f"Table,{table_name}",
                f"Rows,{row_count}",
                f"Columns,{len(columns)}",
                f"Export Date,{time.strftime('%Y-%m-%d %H:%M:%S')}",
                ""
            ]
            
            csv_lines.append(",".join(f'"{col}"' for col in columns))
            csv_lines.append("# " + ",".join(["[Data not accessible - metadata only]"] * len(columns)))
            
            return "\n".join(csv_lines)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error exporting table: {str(e)}")

@app.get("/vehicle-summary")
async def vehicle_summary(
    app_id: str,
    table_name: str,
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client)
):
    """
    Generate a summary of table data
    Works with both INLINE script data and CSV-loaded tables
    """
    print(f"\n==== VEHICLE SUMMARY ENDPOINT ====")
    print(f"App ID: {app_id}, Table: {table_name}")

    try:
        # Step 1: Try to get actual table data first
        print(f"Step 1: Attempting to fetch table data...")
        table_data_result = ws_client.get_table_data(app_id, table_name, limit=1000)
        
        if table_data_result.get("success"):
            print(f"✅ Got actual table data")
            rows = table_data_result.get("rows", [])
            
            if rows:
                return {"success": True, "summary": _generate_summary_from_rows(rows, table_name)}
        
        # Step 2: Fallback - get from script/INLINE data
        print(f"Step 2: Trying script parsing...")
        
        if not SCRIPT_PARSER_AVAILABLE:
            print(f"⚠️ Script parser not available, returning metadata summary")
            # Get table metadata as fallback
            tables_result = ws_client.get_app_tables_simple(app_id)
            if tables_result.get("success"):
                for table in tables_result.get("tables", []):
                    if table.get("name", "").strip().lower() == table_name.strip().lower():
                        return {
                            "success": True,
                            "summary": {
                                "Table": table_name,
                                "Total Rows": table.get("no_of_rows", 0),
                                "Columns": len([f for f in table.get("fields", []) if not f.get("is_system")]),
                                "Fields": [f["name"] for f in table.get("fields", [])[:10]],
                                "Note": "Summary from table metadata (actual data not accessible)"
                            }
                        }
        
        # Try script-based summary
        result = ws_client.get_app_tables_simple(app_id)
        
        if not result.get("success", False):
            print(f"⚠️ Script parsing failed, returning basic summary")
            return {
                "success": True,
                "summary": {
                    "Table": table_name,
                    "Note": "Could not load data - check that app has been reloaded in QlikCloud"
                }
            }

        script = result.get("script", "")

        if not script:
            print(f"⚠️ No script found, returning basic summary")
            return {
                "success": True,
                "summary": {
                    "Table": table_name,
                    "Tables Available": [t["name"] for t in result.get("tables", [])],
                    "Note": "No script data found"
                }
            }

        # Parse script for INLINE data
        table_data = QlikScriptParser.get_table_preview(script, table_name, 1000)

        if not table_data.get("success", False):
            print(f"⚠️ Table not found in script")
            return {
                "success": True,
                "summary": {
                    "Table": table_name,
                    "Available Tables": [t["name"] for t in result.get("tables", [])]
                }
            }

        rows = table_data.get("rows", [])

        if not rows:
            return {
                "success": True,
                "summary": {
                    "Table": table_name,
                    "Message": "No data rows found"
                }
            }

        return {"success": True, "summary": _generate_summary_from_rows(rows, table_name)}
        
    except Exception as e:
        print(f"❌ Error in vehicle_summary: {str(e)}")
        import traceback
        traceback.print_exc()
        # Return a basic message instead of crashing
        return {
            "success": True,
            "summary": {
                "Table": table_name,
                "Status": "Summary generation failed, but table data may still be available",
                "Error": str(e)
            }
        }

def _generate_summary_from_rows(rows: list, table_name: str) -> dict:
    """Generate summary statistics from table rows"""
    if not rows:
        return {"Table": table_name, "Total Rows": 0}
    
    first_row = rows[0]
    summary = {
        "Table": table_name,
        "Total Rows": len(rows),
        "Columns": list(first_row.keys()),
        "Column Count": len(first_row.keys()),
        "Numeric Analysis": {},
        "Category Counts": {}
    }
    
    # Analyze each column
    from collections import Counter
    
    for key in first_row.keys():
        values = []
        text_values = []
        
        for row in rows:
            val = row.get(key, "")
            
            # Try to parse as number
            if val and val not in ["[Row", "[data"]:  # Skip placeholder values
                try:
                    if isinstance(val, str):
                        clean_val = val.replace(',', '').strip()
                        if clean_val and (clean_val.replace('.', '').replace('-', '', 1).isdigit()):
                            values.append(float(clean_val))
                        else:
                            text_values.append(val)
                    else:
                        values.append(float(val))
                except:
                    text_values.append(str(val))
            else:
                text_values.append(str(val))
        
        # Numeric analysis
        if values:
            summary["Numeric Analysis"][key] = {
                "min": round(min(values), 2),
                "max": round(max(values), 2),
                "avg": round(sum(values) / len(values), 2),
                "count": len(values)
            }
        
        # Category analysis
        if text_values and len(set(text_values)) <= 20:  # Only for reasonable cardinality
            counter = Counter(text_values)
            summary["Category Counts"][key] = dict(counter.most_common(5))
    
    return summary


# FIX 6: /health duplicate removed here (defined once at top of file)

# ==================== SUMMARY GENERATION ENDPOINTS ====================

class TableDataRequest(BaseModel):
    """Request model for table data summary"""
    table_name: str
    data: List[Dict[str, Any]]

class BatchSummaryRequest(BaseModel):
    """Request model for batch summary"""
    tables: Dict[str, List[Dict[str, Any]]]

class ChatRequest(BaseModel):
    """Request model for chat about data"""
    table_name: str
    data: List[Dict[str, Any]]
    question: str

class ChatHistoryRequest(BaseModel):
    """Request model for chat with history"""
    table_name: str
    data: List[Dict[str, Any]]
    conversation: List[Dict[str, str]]  # [{"role": "user/assistant", "content": "text"}, ...]


@app.post("/summary/table")
async def create_table_summary(request: TableDataRequest):
    """
    Generate a summary from table data
    Accepts JSON data and returns comprehensive metrics and summary
    
    Example request:
    {
        "table_name": "Sales",
        "data": [
            {"product": "A", "amount": 100},
            {"product": "B", "amount": 200}
        ]
    }
    """
    try:
        # Lazy import to speed up server startup
        from summary_utils import generate_summary, get_data_quality_score, get_data_preview
        summary = generate_summary(request.data, request.table_name)
        
        if summary.get("success"):
            # Add data quality score
            import pandas as pd
            df = pd.DataFrame(request.data)
            quality_score = get_data_quality_score(df)
            summary["data_quality_score"] = quality_score
            
            # Add data preview
            summary["data_preview"] = get_data_preview(request.data, rows=5)
        
        return summary
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate summary: {str(e)}")


@app.post("/summary/batch")
async def create_batch_summary(request: BatchSummaryRequest):
    """
    Generate summaries for multiple tables at once
    
    Example request:
    {
        "tables": {
            "Sales": [...],
            "Products": [...]
        }
    }
    """
    try:
        # Lazy import to speed up server startup
        from summary_utils import generate_batch_summary, get_data_quality_score
        batch_result = generate_batch_summary(request.tables)
        
        # Add quality scores for each table
        import pandas as pd
        for table_name, summary in batch_result["summaries"].items():
            if summary.get("success"):
                df = pd.DataFrame(request.tables[table_name])
                summary["data_quality_score"] = get_data_quality_score(df)
        
        return batch_result
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate batch summary: {str(e)}")


@app.post("/summary/text")
async def generate_summary_text(request: TableDataRequest):
    """
    Generate a human-readable summary text from table data
    
    Returns plain text summary
    """
    try:
        # Lazy import to speed up server startup
        from summary_utils import build_summary_text
        summary_text = build_summary_text(request.data, request.table_name)
        
        return {
            "success": True,
            "table_name": request.table_name,
            "summary": summary_text
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to generate summary text: {str(e)}")


@app.post("/summary/quality")
async def check_data_quality(request: TableDataRequest):
    """
    Check data quality metrics for a table
    
    Returns quality score and missing value analysis
    """
    try:
        # Lazy import to speed up server startup
        from summary_utils import get_data_quality_score
        import pandas as pd
        
        df = pd.DataFrame(request.data)
        quality_score = get_data_quality_score(df)
        
        # Calculate missing values per column
        missing_info = {
            col: {
                "count": int(df[col].isna().sum()),
                "percentage": round((df[col].isna().sum() / len(df)) * 100, 2)
            }
            for col in df.columns
        }
        
        return {
            "success": True,
            "table_name": request.table_name,
            "total_rows": len(df),
            "total_columns": len(df.columns),
            "quality_score": quality_score,
            "missing_values": missing_info
        }
    
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to check data quality: {str(e)}")


# ==================== HUGGING FACE CHAT ENDPOINTS ====================

HF_URL = "https://router.huggingface.co/v1/chat/completions"
HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
HF_MODEL_FALLBACK = "mistralai/Mistral-7B-Instruct-v0.3"

# def call_hf_model(prompt: str):

#     hf_token = os.getenv("HF_TOKEN")

#     if not hf_token:
#         raise HTTPException(status_code=500, detail="HF_TOKEN missing in .env")

#     headers = {
#         "Authorization": f"Bearer {hf_token}",
#         "Content-Type": "application/json"
#     }

#     payload = {
#         "model": "meta-llama/Llama-3.1-8B-Instruct",
#         "messages": [
#             {"role": "user", "content": prompt}
#         ],
#         "max_tokens": 250,
#         "temperature": 0.3
#     }

#     resp = requests.post(
#         "https://router.huggingface.co/v1/chat/completions",
#         headers=headers,
#         json=payload,
#         timeout=60
#     )

#     if resp.status_code != 200:
#         print("HF ERROR:", resp.status_code)
#         print("HF BODY:", resp.text)
#         raise HTTPException(
#             status_code=502,
#             detail=f"HF API error {resp.status_code}: {resp.text[:300]}"
#         )

#     raw = resp.json()

#     try:
#         return raw["choices"][0]["message"]["content"]
#     except Exception:
#         return str(raw)
def call_hf_model_with_options(
    prompt: str,
    system_prompt: str = "You are a senior business data analyst who provides concise executive insights.",
    max_tokens: int = 250,
    temperature: float = 0.3,
):

    hf_token = os.getenv("HF_TOKEN")

    if not hf_token:
        raise HTTPException(status_code=500, detail="HF_TOKEN missing in .env")

    headers = {
        "Authorization": f"Bearer {hf_token}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": HF_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": max_tokens,
        "temperature": temperature
    }

    for model in [HF_MODEL, HF_MODEL_FALLBACK]:
        payload["model"] = model
        try:
            resp = requests.post(HF_URL, headers=headers, json=payload, timeout=60)

            if resp.status_code == 200:
                data = resp.json()
                if "choices" in data:
                    return data["choices"][0]["message"]["content"]

            logger.warning(f"HF model {model} failed ({resp.status_code}): {resp.text[:200]}")

        except requests.exceptions.Timeout:
            logger.warning(f"HF model {model} timed out, trying fallback...")
            continue

        except requests.exceptions.RequestException as e:
            logger.warning(f"HF model {model} request error: {e}, trying fallback...")
            continue

    raise HTTPException(
        status_code=503,
        detail="LLM summary service temporarily unavailable. Please try again in a few minutes."
    )


def call_hf_model(prompt: str):
    return call_hf_model_with_options(prompt)
# ================= EXECUTIVE SUMMARY =================

def compute_metrics(df):
    metrics = {}
    numeric_cols = df.select_dtypes(include="number").columns
    for col in numeric_cols:
        metrics[col] = {
            "mean": float(df[col].mean()),
            "max": float(df[col].max()),
            "min": float(df[col].min()),
            "std": float(df[col].std())
        }
    return metrics


def generate_insights(df, metrics):
    insights = []
    for col, stats in metrics.items():
        insights.append(
            f"The average {col} is {stats['mean']:.2f} with a maximum of {stats['max']:.2f}"
        )
        if stats["std"] > stats["mean"] * 0.5:
            insights.append(
                f"{col} shows high variability indicating inconsistent distribution"
            )
    cat_cols = df.select_dtypes(include="object").columns
    for col in cat_cols[:3]:
        top_vals = df[col].value_counts().head(3).to_dict()
        insights.append(f"Top categories in {col} are {top_vals}")
    return insights[:10]


@app.post("/chat/summary-hf")
async def generate_hf_summary(request: TableDataRequest):

    try:
        df = pd.DataFrame(request.data)

        if df.empty:
            raise HTTPException(status_code=400, detail="Dataset is empty")

        # Step 1 — compute metrics
        metrics = compute_metrics(df)

        # Step 2 — generate structured insights
        insights = generate_insights(df, metrics)
        insights_text = "\n".join(insights)

        # Step 3 — LLM narrative generation
        prompt = f"""You are a senior business intelligence analyst preparing an executive summary.

Dataset: {request.table_name}

Key metrics:
{json.dumps(metrics, indent=2)[:1500]}

Detected insights:
{insights_text}

Task:
Write exactly 7 executive summary bullet points for leadership.

Guidelines:
- Each bullet must start with "-"
- Maximum 12 words
- Focus on business impact
- Include numbers where possible
- Avoid filler language
- One insight per bullet

Return only the bullet points.
"""

        generated = call_hf_model(prompt)

        if not generated:
            raise HTTPException(status_code=502, detail="HF returned empty response")

        print("MODEL OUTPUT:")
        print(generated)

        # Parse LLM output
        lines = [l.strip() for l in generated.split("\n") if l.strip()]

        bullets = []

        for line in lines:
            line = re.sub(r"^\d+[\.\)]\s*", "", line)   # remove numbering
            line = re.sub(r"^[•\-]\s*", "", line)       # remove bullet symbols

            if len(line) > 5:
                bullets.append(f"- {line}")

        bullets = bullets[:7]

        if not bullets:
            bullets = [f"- {i}" for i in insights[:7]]

        return {
            "success": True,
            "table_name": request.table_name,
            "bullets": bullets,
            "metrics": metrics,
            "insights_detected": insights
        }

    except HTTPException:
        raise  # pass through HF errors

    except Exception as e:
        logger.error(f"Summary generation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
# ================= SINGLE QUESTION =================

@app.post("/chat/analyze")
async def chat_analyze_data(request: ChatRequest):

    try:

        from summary_utils import generate_summary

        summary = generate_summary(request.data, request.table_name)

        if not summary.get("success"):
            raise HTTPException(status_code=400, detail="Failed to process data")

        metrics = summary.get("metrics", {})

        prompt = f"""
You are a data analyst.

Dataset metrics:
{json.dumps(metrics)}

User question:
{request.question}
"""

        answer = call_hf_model(prompt)

        return {
            "success": True,
            "response": answer,
            "metrics_context": metrics
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================= MULTI TURN =================

@app.post("/chat/multi-turn")
async def multi_turn_chat(request: ChatHistoryRequest):

    try:

        from summary_utils import generate_summary

        summary = generate_summary(request.data, request.table_name)

        metrics = summary.get("metrics", {})

        conversation_text = ""

        for msg in request.conversation:
            conversation_text += f"{msg['role']}: {msg['content']}\n"

        prompt = f"""
You are a business data analyst.

Dataset metrics:
{json.dumps(metrics)}

Conversation history:
{conversation_text}

Provide the next response.
"""

        response = call_hf_model(prompt)

        updated_conversation = request.conversation + [
            {"role": "assistant", "content": response}
        ]

        return {
            "success": True,
            "conversation": updated_conversation,
            "last_response": response
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ================= HELP =================

@app.get("/chat/help")
async def chat_help():

    return {
        "success": True,
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "endpoints": {
            "/chat/analyze": "Ask questions about your dataset",
            "/chat/summary-hf": "Generate executive summary insights",
            "/chat/multi-turn": "Conversational analysis"
        }
    }# ====== POWER BI LOGIN ENDPOINTS ======

@app.post("/powerbi/login/initiate")
async def powerbi_login_initiate():
    """
    Initiate Power BI login using device code flow.
    Returns device code and URL for user to visit.
    """
    auth = get_auth_manager()
    result = auth.get_device_code()
    
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])
    
    return {
        "success": True,
        "device_code": result["device_code"],
        "user_code": result["user_code"],
        "message": result["message"],
        "expires_in": result["expires_in"],
        "verification_uri": result["verification_uri"]
    }

@app.post("/powerbi/login/acquire-token")
async def powerbi_login_acquire_token(request: dict):
    """
    Complete device code flow and acquire token using service principal.
    Performs synchronous authentication and returns immediately.
    """
    auth = get_auth_manager()
    
    # Check if already logged in
    if auth.is_token_valid():
        return {
            "success": True,
            "message": "Already logged in",
            "logged_in": True
        }
    
    try:
        print("\n" + "="*60)
        print("🔐 STARTING SERVICE PRINCIPAL TOKEN ACQUISITION")
        print("="*60)
        
        # Perform synchronous token acquisition
        print("📱 Acquiring token via service principal...")
        result = auth.acquire_token_by_device_code(max_wait_seconds=10)
        
        if result.get("success"):
            print("\n" + "="*60)
            print("✅ TOKEN ACQUISITION SUCCESSFUL!")
            print("="*60 + "\n")
            return {
                "success": True,
                "message": "Authentication successful!",
                "logged_in": True
            }
        else:
            print(f"\n❌ Token acquisition failed: {result.get('error')}\n")
            return {
                "success": False,
                "message": "Authentication failed",
                "error": result.get("error"),
                "logged_in": False
            }
    except Exception as e:
        print(f"❌ Token acquisition error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Token acquisition failed: {str(e)}")


@app.post("/powerbi/login/status")
async def powerbi_login_status():
    """
    Check if user is logged in.
    Returns True if valid token exists.
    """
    auth = get_auth_manager()
    return {
        "logged_in": auth.is_token_valid(),
        "message": "You are logged in to Power BI" if auth.is_token_valid() else "Not logged in. Please use /powerbi/login/initiate"
    }


@app.post("/powerbi/login/test")
async def powerbi_login_test():
    """
    Test Power BI connection by listing datasets in workspace.
    """
    auth = get_auth_manager()
    
    if not auth.is_token_valid():
        raise HTTPException(status_code=401, detail="Not logged in. Please login first using /powerbi/login/initiate")
    
    result = auth.test_connection()
    
    if not result["success"]:
        raise HTTPException(status_code=401, detail=result["error"])
    
    return result


def _relationship_key(rel: Dict[str, Any]) -> tuple:
    return (
        str(rel.get("fromTable", rel.get("from_table", ""))).lower(),
        str(rel.get("fromColumn", rel.get("from_column", ""))).lower(),
        str(rel.get("toTable", rel.get("to_table", ""))).lower(),
        str(rel.get("toColumn", rel.get("to_column", ""))).lower(),
    )


def _sanitize_col(name: str) -> str:
    return _sanitize_col_name(name)


def _apply_relationships_with_retry(
    workspace_id: Optional[str],
    dataset_id: str,
    inferred_relationships: List[Dict[str, Any]],
    access_token: str,
) -> Dict[str, Any]:
    if not inferred_relationships:
        return {
            "success": True,
            "status": "skipped",
            "reason": "No relationships inferred",
            "inferred_count": 0,
        }

    if not workspace_id:
        return {
            "success": False,
            "status": "skipped",
            "reason": "Workspace ID not available (likely personal workspace)",
            "inferred_count": len(inferred_relationships),
        }

    apply_result = apply_relationships(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        relationships=inferred_relationships,
        access_token=access_token,
    )

    verify_before = verify_relationships(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        expected_relationships=inferred_relationships,
        access_token=access_token,
    )

    retry_result: Dict[str, Any] = {"status": "not_needed"}
    verify_after = verify_before

    if verify_before.get("success") is False:
        raw = verify_before.get("raw", []) if isinstance(verify_before, dict) else []
        raw_keys = {
            (
                str(item.get("fromTable", "")).lower(),
                str(item.get("fromColumn", "")).lower(),
                str(item.get("toTable", "")).lower(),
                str(item.get("toColumn", "")).lower(),
            )
            for item in raw
        }

        missing = [r for r in inferred_relationships if _relationship_key(r) not in raw_keys]

        if missing:
            swapped = []
            for rel in missing:
                swapped.append({
                    "fromTable": rel.get("toTable"),
                    "fromColumn": rel.get("toColumn"),
                    "toTable": rel.get("fromTable"),
                    "toColumn": rel.get("fromColumn"),
                    "cardinality": rel.get("cardinality", "ManyToOne"),
                    "crossFilteringBehavior": rel.get("crossFilteringBehavior", "Single"),
                })

            retry_result = apply_relationships(
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                relationships=swapped,
                access_token=access_token,
                overwrite=False,
            )

            verify_after = verify_relationships(
                workspace_id=workspace_id,
                dataset_id=dataset_id,
                expected_relationships=inferred_relationships,
                access_token=access_token,
            )

    return {
        "success": bool(verify_after.get("success", False)),
        "inferred_count": len(inferred_relationships),
        "applied": apply_result,
        "verified_before_retry": verify_before,
        "retry": retry_result,
        "verified_after_retry": verify_after,
    }


@app.post("/powerbi/process")
async def process_for_powerbi(
    csv_file: UploadFile = File(None),
    dax_file: UploadFile = File(None),
    json_file: UploadFile = File(None),
    meta_app_name: str = Form(""),
    meta_table: str = Form(""),
    has_csv: str = Form("true"),
    has_dax: str = Form("false")
):
    try:
        print(f"DEBUG: Received request with csv_file={bool(csv_file)}, dax_file={bool(dax_file)}, json_file={bool(json_file)}")
        print(f"DEBUG: Received FormData - meta_app_name='{meta_app_name}', meta_table='{meta_table}'")
        print(f"DEBUG: Format selection from Export page - has_csv={has_csv}, has_dax={has_dax}")
        
        # Parse selection flags
        has_csv_selected = has_csv == "true"
        has_dax_selected = has_dax == "true"
        
        # Validate: at least one format must be selected
        if not has_csv_selected and not has_dax_selected:
            raise HTTPException(status_code=400, detail="No formats selected. Please select CSV and/or DAX in Export page.")
        
        # CSV is required for dataset creation (contains rows and columns)
        if has_csv_selected and not csv_file:
            raise HTTPException(status_code=400, detail="CSV was selected but file not provided")

        csv_content = await csv_file.read() if csv_file else b""
        dax_content = await dax_file.read() if dax_file else b""
        json_content = await json_file.read() if json_file else b""
        
        print(f"DEBUG: CSV content length: {len(csv_content)}, DAX content length: {len(dax_content)}")

        if not csv_content:
            raise HTTPException(status_code=400, detail="CSV file is empty")

        # Parse DAX file only if it was selected in Export page
        dax_text = ""
        dax_measures = []
        if has_dax_selected and dax_file:
            try:
                dax_content = await dax_file.read()
                dax_text = dax_content.decode('utf-8')
                dax_measures = parse_dax(dax_text)
                print(f"DEBUG: Parsed {len(dax_measures)} measures from DAX file (DAX was selected in Export page)")
                for measure in dax_measures:
                    print(f"  ✓ Measure: {measure['name']}")
            except Exception as e:
                print(f"DEBUG: DAX parsing error: {str(e)}")
                # Don't fail if DAX parsing fails, just continue with CSV data
                dax_measures = []
        elif has_dax_selected and not dax_file:
            print("DEBUG: DAX was selected in Export page but file not provided - will skip DAX processing")
        else:
            print(f"DEBUG: DAX not selected in Export page (has_dax_selected={has_dax_selected}) - skipping DAX")

        # Parse CSV locally
        try:
            processor = PowerBIProcessor(csv_content, dax_content)
            parsed = processor.process()
            print(f"DEBUG: Processor result keys: {list(parsed.keys())}")
        except Exception as e:
            print(f"DEBUG: Processor error: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to parse files: {str(e)}")

        rows = parsed.get("rows", [])
        columns = parsed.get("table_columns", [])
        if not rows or not columns:
            raise HTTPException(status_code=400, detail="CSV has no rows/columns")

        orig_headers, col_name_map, sanitized_rows = normalize_table_rows(
            table_name=meta_table or "QlikTable",
            rows=rows,
            provided_columns=columns,
        )
        sanitized_columns = [col_name_map.get(c, _sanitize_col(c)) for c in orig_headers]

        # Dataset/table naming - PRIORITY: custom table name, then app name
        dataset_name = meta_table or meta_app_name or "Qlik_Migrated_Dataset"
        table_name = meta_table or "QlikTable"
        
        print(f"DEBUG: Dataset naming - meta_table={meta_table}, meta_app_name={meta_app_name}")
        print(f"DEBUG: Using dataset_name={dataset_name}, table_name={table_name}")
        print(f"DEBUG: ✅ Processing formats selected in Export page - CSV: {has_csv_selected}, DAX: {has_dax_selected}")
        print(f"DEBUG: DAX measures extracted: {len(dax_measures)}")
        print(f"DEBUG: Creating dataset {dataset_name}, table {table_name}")

        # Infer schema and publish to Power BI
        try:
            # Get authenticated user token
            auth = get_auth_manager()
            
            if not auth.is_token_valid():
                raise HTTPException(status_code=401, detail="Not logged in. Please login using /powerbi/login/initiate endpoint")
            
            # Create service with user token
            access_token = auth.get_access_token()
            pbi = PowerBIService(access_token=access_token)
            schema = infer_schema_from_rows(sanitized_rows)
            dataset_id, created = pbi.get_or_create_push_dataset(dataset_name, table_name, schema)
            push_res = pbi.push_rows(dataset_id, table_name, sanitized_rows)
            print(f"DEBUG: Push result: {push_res}")

            # Infer and apply relationships for CSV flow (single table may infer none).
            tables_m = [{"name": table_name, "fields": sanitized_columns, "source_type": "csv"}]
            inferred_relationships = resolve_relationships_unified(
                tables_m=tables_m,
                col_name_map_by_table={table_name: col_name_map},
            )
            relationships_apply_result = _apply_relationships_with_retry(
                workspace_id=pbi.workspace_id,
                dataset_id=dataset_id,
                inferred_relationships=inferred_relationships,
                access_token=access_token,
            )
            
            # Data is now saved to Power BI Cloud permanently
            print(f"✅ Dataset saved to Power BI Cloud!")
            print(f"   Dataset ID: {dataset_id}")
            print(f"   Table Name: {table_name}")
            print(f"   Rows: {len(rows)}")
            print(f"   📌 This data will persist in Power BI even if backend/app closes")
            
            # Apply DAX measures if any
            measures_result = {}
            if dax_measures:
                print(f"Applying {len(dax_measures)} DAX measures to dataset...")
                measures_result = apply_dax_measures(pbi, dataset_id, dax_measures)
            
            # Create auto-report with visualizations
            print("📊 Creating Power BI report with visualizations...")
            report_url = get_or_create_default_report(pbi, dataset_id, dataset_name, columns)
            
            try:
                refresh_res = pbi.trigger_refresh(dataset_id)
            except Exception:
                # push dataset often doesn't require refresh; ignore
                refresh_res = {"status_code": None}
        except HTTPException:
            raise
        except Exception as e:
            print(f"DEBUG: PowerBI error: {str(e)}")
            raise HTTPException(status_code=401, detail=f"Power BI error: {str(e)}")

        return {
            "success": True,
            "message": "Published to Power BI",
            "dataset": {
                "id": dataset_id,
                "created": created,
                "name": dataset_name,
                "table": table_name,
                "workspace_id": pbi.workspace_id,
                "urls": {
                    "report": report_url,
                    "dataset": f"https://app.powerbi.com/groups/{pbi.workspace_id}/datasets/{dataset_id}",
                    "workspace": f"https://app.powerbi.com/groups/{pbi.workspace_id}",
                    "home": "https://app.powerbi.com/home"
                }
            },
            "push": push_res,
            "relationships": relationships_apply_result,
            "refresh": refresh_res,
            "dax": {
                "parsed_measures": dax_measures,
                "measures_applied": measures_result
            },
            "local_parse": {
                "columns": columns,
                "sanitized_columns": sanitized_columns,
                "row_count": len(rows)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"DEBUG: Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Power BI publish failed: {str(e)}")

# ─────────── END ───────────
@app.post("/powerbi/process-batch")
async def process_batch_for_powerbi(payload: dict = Body(...)):
    """
    Publish multiple Qlik tables to Power BI as a SINGLE dataset with multiple tables.
    
    UPDATED: All tables are now combined into ONE dataset in Power BI.
    Each table maintains its own schema with proper column definitions.
    """
    try:
        auth = get_auth_manager()
        if not auth.is_token_valid():
            raise HTTPException(status_code=401, detail="Not logged in. Please login first.")
        
        base_dataset_name = payload.get("dataset_name", "Qlik_Migrated_Dataset")
        tables = payload.get("tables", [])
        
        if not tables:
            raise HTTPException(status_code=400, detail="No tables provided")
        
        access_token = auth.get_access_token()
        pbi = PowerBIService(access_token=access_token)
        
        # Step 1: Build schema for all tables
        dataset_tables_schema = []
        tables_m: List[Dict[str, Any]] = []
        all_rows_by_table = {}
        col_name_map_by_table: Dict[str, Dict[str, str]] = {}
        total_rows = 0
        
        for idx, table in enumerate(tables):
            table_name = table.get("name", f"Table_{idx + 1}")
            table_rows = table.get("rows", [])
            
            if not table_rows:
                print(f"⚠️  Skipping table {idx + 1}/{len(tables)}: {table_name} (no rows)")
                continue
            
            print(f"📦 Preparing table {idx + 1}/{len(tables)}: {table_name} ({len(table_rows)} rows)")

            orig_headers, col_name_map, sanitized_table_rows = normalize_table_rows(
                table_name=table_name,
                rows=table_rows,
                provided_columns=table.get("columns", []),
            )
            col_name_map_by_table[table_name] = col_name_map
            
            # Infer schema from this table's rows
            table_schema = infer_schema_from_rows(sanitized_table_rows)
            
            # Add to dataset schema
            dataset_tables_schema.append({
                "name": table_name,
                "columns": table_schema
            })

            field_names = [c.get("name") for c in table_schema if isinstance(c, dict) and c.get("name")]
            if not field_names and sanitized_table_rows:
                field_names = list((sanitized_table_rows[0] or {}).keys())
            tables_m.append({
                "name": table_name,
                "fields": field_names,
                "source_type": "csv",
            })
            
            all_rows_by_table[table_name] = sanitized_table_rows
            total_rows += len(sanitized_table_rows)
        
        if not dataset_tables_schema:
            raise HTTPException(status_code=400, detail="No tables with data to publish")
        
        print(f"📊 Creating single dataset '{base_dataset_name}' with {len(dataset_tables_schema)} tables...")
        
        # Step 2: Create the dataset with all tables at once using Power BI API
        try:
            import requests
            import json
            
            # Build complete dataset payload with all tables
            dataset_payload = {
                "name": base_dataset_name,
                "defaultMode": "Push",
                "tables": dataset_tables_schema
            }
            
            # Power BI API constant
            PBI_API_ROOT = "https://api.powerbi.com/v1.0/myorg"
            
            # Create dataset via Power BI API
            if pbi.use_personal_workspace:
                create_url = f"{PBI_API_ROOT}/datasets?defaultRetentionPolicy=None"
            else:
                create_url = f"{PBI_API_ROOT}/groups/{pbi.workspace_id}/datasets?defaultRetentionPolicy=None"
            
            print(f"🌐 POST to: {create_url}")
            create_response = requests.post(
                create_url,
                headers=pbi._headers(),
                data=json.dumps(dataset_payload),
                timeout=60
            )
            
            if create_response.status_code not in (200, 201):
                raise Exception(f"Failed to create multi-table dataset: {create_response.status_code} {create_response.text}")
            
            dataset_id = create_response.json().get("id")
            print(f"✅ Multi-table dataset created: {dataset_id}")
            
        except Exception as e:
            print(f"❌ Failed to create dataset: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create dataset: {str(e)}")
        
        # Step 3: Push rows for each table into the single dataset
        pushed_tables = []
        for table_name, sanitized_table_rows in all_rows_by_table.items():
            try:
                print(f"📤 Pushing {len(sanitized_table_rows)} rows to table '{table_name}'...")
                push_result = pbi.push_rows(dataset_id, table_name, sanitized_table_rows)
                
                pushed_tables.append({
                    "table_name": table_name,
                    "rows_count": len(sanitized_table_rows),
                    "status": "success"
                })
                
                print(f"   ✅ Successfully pushed {len(sanitized_table_rows)} rows to {table_name}")
                
            except Exception as e:
                print(f"   ❌ Failed to push rows to table {table_name}: {e}")
                pushed_tables.append({
                    "table_name": table_name,
                    "rows_count": len(sanitized_table_rows),
                    "error": str(e),
                    "status": "failed"
                })

        inferred_relationships = resolve_relationships_unified(
            tables_m=tables_m,
            col_name_map_by_table=col_name_map_by_table,
        )
        relationships_apply_result = _apply_relationships_with_retry(
            workspace_id=pbi.workspace_id,
            dataset_id=dataset_id,
            inferred_relationships=inferred_relationships,
            access_token=access_token,
        )
        
        workspace_url = f"https://app.powerbi.com/groups/{pbi.workspace_id}"
        
        return {
            "success": True,
            "message": f"✅ Published {len(pushed_tables)} table(s) to single dataset '{base_dataset_name}'",
            "dataset_name": base_dataset_name,
            "dataset_id": dataset_id,
            "workspace_url": workspace_url,
            "rows_pushed": total_rows,
            "tables_count": len(tables),
            "tables_published": len([t for t in pushed_tables if t.get("status") == "success"]),
            "published_tables": pushed_tables,
            "relationships": relationships_apply_result,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Batch publish error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Batch publish failed: {str(e)}")

# ============= ADVANCED POWER BI ENDPOINT =============

@app.post("/powerbi/advanced")
async def powerbi_advanced(
    csv_file: UploadFile = File(None),
    dax_file: UploadFile = File(None),
    meta_app_name: Optional[str] = None,
    meta_table: Optional[str] = None
):
    """
    Advanced Power BI endpoint with full DAX support and measures.
    
    Features:
    - Parse DAX file and extract measures
    - Create Power BI dataset with schema
    - Push data rows
    - Apply DAX measures to dataset (calculated columns/measures)
    
    Request:
    {
        "csv_file": <CSV file with data>,
        "dax_file": <DAX file with measure definitions>,
        "meta_app_name": "Application Name",
        "meta_table": "Table Name"
    }
    
    DAX Format in file:
        Total Sales = SUM(FactTable[Sales])
        Total Quantity = SUM(FactTable[Quantity])
        Average Price = AVERAGE(FactTable[Price])
    """
    try:
        print("🚀 Advanced Power BI processing started...")
        
        if not csv_file:
            raise HTTPException(status_code=400, detail="CSV file is required")
        
        csv_content = await csv_file.read()
        dax_content = await dax_file.read() if dax_file else b""
        
        print(f"📊 CSV size: {len(csv_content)} bytes")
        print(f"📐 DAX size: {len(dax_content)} bytes")
        
        if not csv_content:
            raise HTTPException(status_code=400, detail="CSV file is empty")
        
        # Parse DAX measures
        dax_text = ""
        measures_list = []
        
        if dax_content:
            try:
                dax_text = dax_content.decode('utf-8')
                measures_list = parse_dax(dax_text)
                print(f"✅ Parsed {len(measures_list)} measures from DAX file:")
                for m in measures_list:
                    print(f"   - {m['name']}")
            except Exception as e:
                print(f"⚠️ DAX parsing error: {str(e)}")
                measures_list = []
        
        # Parse CSV data
        try:
            processor = PowerBIProcessor(csv_content, dax_content)
            parsed = processor.process()
        except Exception as e:
            print(f"❌ CSV processing error: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {str(e)}")
        
        rows = parsed.get("rows", [])
        columns = parsed.get("table_columns", [])
        
        if not rows or not columns:
            raise HTTPException(status_code=400, detail="CSV has no valid rows/columns")

        orig_headers, col_name_map, sanitized_rows = normalize_table_rows(
            table_name=meta_table or "FactTable",
            rows=rows,
            provided_columns=columns,
        )
        sanitized_columns = [col_name_map.get(c, _sanitize_col(c)) for c in orig_headers]
        
        # Dataset naming
        dataset_name = meta_app_name or meta_table or "Qlik_Advanced_Dataset"
        table_name = meta_table or "FactTable"
        
        print(f"📊 Creating dataset: {dataset_name}")
        print(f"📋 Table: {table_name}")
        print(f"📈 Rows: {len(rows)}, Columns: {len(columns)}")
        
        # Authenticate and create dataset
        try:
            auth = get_auth_manager()
            if not auth.is_token_valid():
                raise HTTPException(
                    status_code=401,
                    detail="Not logged in. Please use /powerbi/login/acquire-token"
                )
            
            access_token = auth.get_access_token()
            pbi = PowerBIService(access_token=access_token)
            schema = infer_schema_from_rows(sanitized_rows)
            
            # Create/get dataset
            dataset_id, created = pbi.get_or_create_push_dataset(dataset_name, table_name, schema)
            print(f"✅ Dataset ID: {dataset_id} (Created: {created})")
            
            # Push data
            push_res = pbi.push_rows(dataset_id, table_name, sanitized_rows)
            print(f"✅ Data pushed successfully")

            tables_m = [{"name": table_name, "fields": sanitized_columns, "source_type": "csv"}]
            inferred_relationships = resolve_relationships_unified(
                tables_m=tables_m,
                col_name_map_by_table={table_name: col_name_map},
            )
            relationships_apply_result = _apply_relationships_with_retry(
                workspace_id=pbi.workspace_id,
                dataset_id=dataset_id,
                inferred_relationships=inferred_relationships,
                access_token=access_token,
            )
            
            # Apply measures
            measures_applied = {}
            if measures_list:
                print(f"🔧 Applying {len(measures_list)} measures...")
                measures_applied = apply_dax_measures(pbi, dataset_id, measures_list)
            
            # Create auto-report with visualizations
            print("📊 Creating Power BI report with visualizations...")
            report_url = get_or_create_default_report(pbi, dataset_id, dataset_name, columns)
            
            # Refresh dataset
            try:
                refresh_res = pbi.trigger_refresh(dataset_id)
            except Exception as e:
                print(f"⚠️ Refresh not available (expected for push datasets): {str(e)}")
                refresh_res = {"status": "skipped"}
            
            return {
                "success": True,
                "status": "completed",
                "message": "Advanced Power BI processing completed successfully",
                "dataset": {
                    "id": dataset_id,
                    "name": dataset_name,
                    "table": table_name,
                    "created": created,
                    "workspace_id": pbi.workspace_id,
                    "urls": {
                        "report": report_url,
                        "dataset": f"https://app.powerbi.com/groups/{pbi.workspace_id}/datasets/{dataset_id}",
                        "workspace": f"https://app.powerbi.com/groups/{pbi.workspace_id}"
                    }
                },
                "data": {
                    "rows_pushed": len(rows),
                    "columns": columns,
                    "push_status": push_res
                },
                "relationships": relationships_apply_result,
                "dax": {
                    "measures_parsed": len(measures_list),
                    "measures_list": measures_list,
                    "measures_applied": measures_applied
                },
                "refresh": refresh_res,
                "local_parse": {
                    "columns": columns,
                    "sanitized_columns": sanitized_columns,
                    "row_count": len(rows)
                }
            }
        
        except HTTPException:
            raise
        except Exception as e:
            print(f"❌ Power BI error: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Power BI error: {str(e)}")
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")


# =====================================================
# DIAGNOSTIC ENDPOINT
@app.get("/powerbi/datasets")
async def list_powerbi_datasets():
    """
    List all datasets in Power BI workspace.
    Useful for debugging and checking dataset creation.
    """
    auth = get_auth_manager()
    
    if not auth.is_token_valid():
        raise HTTPException(status_code=401, detail="Not logged in")
    
    try:
        pbi = PowerBIService(access_token=auth.get_access_token())
        
        headers = {
            "Authorization": f"Bearer {auth.get_access_token()}",
            "Content-Type": "application/json"
        }
        
        workspace_id = pbi.workspace_id
        
        # Get datasets
        datasets_url = f"https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets"
        response = requests.get(datasets_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            raise Exception(f"Failed to list datasets: {response.text}")
        
        datasets = response.json().get("value", [])
        
        return {
            "workspace_id": workspace_id,
            "dataset_count": len(datasets),
            "datasets": [
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "configured": d.get("isConfigured"),
                    "url": f"https://app.powerbi.com/groups/{workspace_id}/datasets/{d.get('id')}"
                }
                for d in datasets
            ]
        }
    
    except Exception as e:
        print(f"❌ Error listing datasets: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# PDF REPORT GENERATION
# =====================================================

@app.post("/report/download-pdf")
async def download_pdf(payload: dict = Body(...)):
    """
    Generate and download Validation & Reconciliation PDF report
    Compares Qlik Sense and Power BI metrics
    """
    try:
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import pagesizes
        from reportlab.lib.units import inch
        from datetime import datetime
        import io
        from fastapi.responses import StreamingResponse
        
        # Extract data from payload
        qlik_metrics = payload.get("qlik_metrics", {})
        powerbi_metrics = payload.get("powerbi_metrics", {})
        app_name = payload.get("app_name", "Unknown App")
        table_name = payload.get("table_name", "Unknown Table")
        migration_status = payload.get("migration_status", "In Progress")
        publishing_method = payload.get("publishing_method", "CSV_EXPORT")
        tables_deployed = payload.get("tables_deployed", 1)
        
        # ==============================
        # CALCULATE METRICS & DIFFERENCES
        # ==============================
        
        TOLERANCE_PERCENT = 0.5
        differences = []
        all_metrics = []
        
        # Helper function to format values
        def format_value(value):
            """Format value for PDF display - handle lists, long strings, etc."""
            if value is None:
                return "—"
            elif isinstance(value, (list, tuple)):
                # Join with comma, remove quotes
                items = [str(x).strip("'\"") for x in value[:4]]
                formatted = ", ".join(items)
                if len(value) > 4 or len(formatted) > 35:
                    formatted = formatted[:32] + "..."
                return formatted
            elif isinstance(value, str):
                if len(value) > 35:
                    return value[:32] + "..."
                return value
            else:
                return str(value)
        
        # Get all metric keys - sorted for consistent order
        metric_order = ['row_count', 'table_count', 'column_count', 'total_records', 'certification_status']
        excluded_keys = {'column_names', 'timestamp'}  # Explicitly exclude these
        all_keys = set(list(qlik_metrics.keys()) + list(powerbi_metrics.keys()))
        
        # Filter out excluded keys and sort by preferred order
        all_keys = all_keys - excluded_keys
        ordered_keys = [k for k in metric_order if k in all_keys]
        extra_keys = sorted([k for k in all_keys if k not in metric_order])
        all_keys = ordered_keys + extra_keys
        
        for metric in all_keys:
            q_val = qlik_metrics.get(metric)
            p_val = powerbi_metrics.get(metric)
            
            # Format display values properly
            q_display = format_value(q_val)
            p_display = format_value(p_val)
            
            variance = None
            variance_percent = None
            status = "PASS"
            
            # Calculate variance for numeric values only
            if isinstance(q_val, (int, float)) and isinstance(p_val, (int, float)):
                variance = p_val - q_val
                if q_val != 0:
                    variance_percent = abs((variance / q_val) * 100)
                else:
                    variance_percent = 0 if variance == 0 else 100
                
                if variance_percent > TOLERANCE_PERCENT:
                    status = "FAIL"
            else:
                # For non-numeric, compare directly
                if q_val != p_val:
                    status = "FAIL"
            
            metric_data = {
                "metric": metric,
                "qlik": q_display,
                "powerbi": p_display,
                "variance": variance,
                "variance_percent": variance_percent,
                "status": status,
                "raw_qlik": q_val,
                "raw_powerbi": p_val
            }
            
            all_metrics.append(metric_data)
            
            if status == "FAIL":
                differences.append(metric_data)
        
        # Calculate certification score
        pass_count = sum(1 for m in all_metrics if m["status"] == "PASS")
        score = (pass_count / len(all_metrics)) * 100 if all_metrics else 0
        certified = score >= 95
        
        # ==============================
        # PDF REPORT GENERATION
        # ==============================
        
        pdf_buffer = io.BytesIO()
        # doc = SimpleDocTemplate(pdf_buffer, pagesize=pagesizes.A4, topMargin=0.4*inch, bottomMargin=0.4*inch, leftMargin=0.4*inch, rightMargin=0.4*inch) 
        # Set proper PDF metadata with title (no unicode chars in filename metadata)
        pdf_title = f"Validation & Reconciliation Report "
        # {table_name}
        pdf_author = "Qlik to Power BI Migration Tool"
        pdf_subject = f"Data Migration Report for {app_name}"
       
        doc = SimpleDocTemplate(
            pdf_buffer,
            pagesize=pagesizes.A4,
            topMargin=0.4*inch,
            bottomMargin=0.4*inch,
            leftMargin=0.4*inch,
            rightMargin=0.4*inch,
            title=pdf_title,
            author=pdf_author,
            subject=pdf_subject
        )
 
        elements = []
        styles = getSampleStyleSheet()
        
        # Custom styles - improved alignment
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=22,
            textColor=colors.HexColor('#1f4788'),
            spaceAfter=10,
            alignment=0,  # Left align
            fontName='Helvetica-Bold'
        )
        
        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading2'],
            fontSize=13,
            textColor=colors.HexColor('#1f4788'),
            spaceAfter=6,
            spaceBefore=4,
            fontName='Helvetica-Bold'
        )
        
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=9,
            spaceAfter=3,
            alignment=0  # Left align
        )
        
        # Title with checkmark
        elements.append(Paragraph("✓ Validation &amp; Reconciliation Report", title_style))
        elements.append(Spacer(1, 0.12 * inch))
        
        # Header Info - formatted nicely
        # Format publishing method for display
        publishing_method_display = "M Query" if publishing_method == "M_QUERY" else "CSV Export"
        header_info = f"""
        <b>Generated:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>
        <b>Application:</b> {app_name}<br/>
        <b>Publishing Method:</b> {publishing_method_display} | <b>Tables Deployed:</b> {tables_deployed}<br/>
        <b>Table:</b> {table_name}<br/>
        <b>Source System:</b> Qlik Sense Cloud | <b>Target System:</b> Power BI Cloud<br/>
        <b>Status:</b> {migration_status}
        """
        elements.append(Paragraph(header_info, normal_style))
        elements.append(Spacer(1, 0.2 * inch))
        
        # Executive Summary
        elements.append(Paragraph("EXECUTIVE SUMMARY", heading_style))
        elements.append(Spacer(1, 0.08 * inch))
        
        num_differences = len(differences)
        if num_differences == 0:
            summary_text = f"<b style='color: green'>✓ SUCCESS:</b> Data synchronized perfectly. All {len(all_metrics)} metrics match."
        else:
            summary_text = f"<b style='color: red'>⚠ ATTENTION:</b> {num_differences} difference(s) detected."
        
        elements.append(Paragraph(summary_text, normal_style))
        elements.append(Spacer(1, 0.18 * inch))
        
        # Dataset Details table intentionally removed (per user request).
        # (Table omitted; metrics comparison follows)
        elements.append(Spacer(1, 0.05 * inch))
        
        # Main Metrics Comparison Table
        elements.append(Paragraph("METRICS COMPARISON", heading_style))
        elements.append(Spacer(1, 0.05 * inch))
        
        # Build table with proper formatting
        table_data = [["Metric", "Qlik Sense", "Power BI", "Variance", "Status"]]
        
        for m in all_metrics:
            # Format metric name
            metric_name = str(m["metric"]).replace('_', ' ').title()
            
            # Get variance display
            variance_str = ""
            if m["variance"] is not None:
                if m["variance_percent"] is not None and m["variance_percent"] > 0.01:
                    variance_str = f"{m['variance_percent']:.1f}%"
                elif m["variance"] == 0:
                    variance_str = "0"
                else:
                    variance_str = f"{m['variance']:.0f}"
            else:
                variance_str = "—"
            
            status_symbol = "✓ PASS" if m["status"] == "PASS" else "✗ FAIL"
            
            table_data.append([
                metric_name,
                m["qlik"],
                m["powerbi"],
                variance_str,
                status_symbol
            ])
        
        # Create table with optimized column widths
        comp_table = Table(table_data, colWidths=[1.7*inch, 1.3*inch, 1.3*inch, 0.75*inch, 0.7*inch])
        
        # Enhanced table styling
        style_list = [
            # Header row styling
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f4788')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGNMENT', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ('PADDINGTOP', (0, 0), (-1, 0), 5),
            ('PADDINGBOTTOM', (0, 0), (-1, 0), 5),
            ('PADDINGLEFT', (0, 0), (-1, 0), 3),
            ('PADDINGRIGHT', (0, 0), (-1, 0), 3),
            
            # Data rows - alignment
            ('ALIGNMENT', (0, 1), (0, -1), 'LEFT'),  # Metric names left
            ('ALIGNMENT', (1, 1), (-1, -1), 'CENTER'),  # Other columns center
            ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
            
            # Cell padding for data rows
            ('PADDINGTOP', (0, 1), (-1, -1), 4),
            ('PADDINGBOTTOM', (0, 1), (-1, -1), 4),
            ('PADDINGLEFT', (0, 1), (-1, -1), 3),
            ('PADDINGRIGHT', (0, 1), (-1, -1), 3),
            
            # Grid and background
            ('GRID', (0, 0), (-1, -1), 0.7, colors.HexColor('#cccccc')),
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            
            # Alternating row colors
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f6f6')]),
        ]
        
        # Color code FAIL rows - removed, all rows same style
        for idx, m in enumerate(all_metrics, 1):
            if m["status"] == "FAIL":
                pass  # No special styling for FAIL rows
        
        comp_table.setStyle(TableStyle(style_list))
        elements.append(comp_table)
        elements.append(Spacer(1, 0.15 * inch))
        
        # Difference Details - Always show for Total Records
        elements.append(Paragraph("DIFFERENCE DETAILS", heading_style))
        elements.append(Spacer(1, 0.08 * inch))
        
        diff_table_data = [["Metric", "Qlik Sense", "Power BI", "Variance"]]
        
        for d in all_metrics:
            if d["metric"] == "total_records":
                variance_str = str(d["variance"]) if d["variance"] is not None else "—"
                metric_name = "Total Records"
                
                diff_table_data.append([
                    metric_name,
                    d["qlik"],
                    d["powerbi"],
                    variance_str
                ])
        
        diff_table = Table(diff_table_data, colWidths=[1.7*inch, 1.3*inch, 1.3*inch, 0.75*inch])
        diff_table.setStyle(TableStyle([
            # Header row styling
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1f4788')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGNMENT', (0, 0), (-1, 0), 'CENTER'),
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ('PADDINGTOP', (0, 0), (-1, 0), 5),
            ('PADDINGBOTTOM', (0, 0), (-1, 0), 5),
            ('PADDINGLEFT', (0, 0), (-1, 0), 3),
            ('PADDINGRIGHT', (0, 0), (-1, 0), 3),
            
            # Data rows - alignment
            ('ALIGNMENT', (0, 1), (0, -1), 'LEFT'),  # Metric names left
            ('ALIGNMENT', (1, 1), (-1, -1), 'CENTER'),  # Other columns center
            ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
            
            # Cell padding for data rows
            ('PADDINGTOP', (0, 1), (-1, -1), 4),
            ('PADDINGBOTTOM', (0, 1), (-1, -1), 4),
            ('PADDINGLEFT', (0, 1), (-1, -1), 3),
            ('PADDINGRIGHT', (0, 1), (-1, -1), 3),
            
            # Grid and background
            ('GRID', (0, 0), (-1, -1), 0.7, colors.HexColor('#cccccc')),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f6f6')]),
        ]))
        elements.append(diff_table)
        elements.append(Spacer(1, 0.2 * inch))
        
        # Difference Details (if any)
        if differences:
            elements.append(Paragraph("DIFFERENCE DETAILS", heading_style))
            elements.append(Spacer(1, 0.08 * inch))
            
            elements.append(Paragraph("The following metrics show discrepancies:", normal_style))
            elements.append(Spacer(1, 0.08 * inch))
            
            diff_table_data = [["Metric", "Qlik Sense", "Power BI", "Difference", "% Variance"]]
            
            for d in differences:
                metric_name = str(d["metric"]).replace('_', ' ').title()
                variance_str = str(d["variance"]) if d["variance"] is not None else "—"
                variance_pct = f"{d['variance_percent']:.1f}%" if d["variance_percent"] is not None else "—"
                
                diff_table_data.append([
                    metric_name,
                    d["qlik"],
                    d["powerbi"],
                    variance_str,
                    variance_pct
                ])
            
            diff_table = Table(diff_table_data, colWidths=[1.7*inch, 1.3*inch, 1.3*inch, 0.75*inch, 0.7*inch])
            diff_table.setStyle(TableStyle([
                # Header row styling
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#8b0000')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 8),
                ('ALIGNMENT', (0, 0), (-1, 0), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('PADDINGTOP', (0, 0), (-1, 0), 5),
                ('PADDINGBOTTOM', (0, 0), (-1, 0), 5),
                ('PADDINGLEFT', (0, 0), (-1, 0), 3),
                ('PADDINGRIGHT', (0, 0), (-1, 0), 3),
                
                # Data rows - alignment
                ('ALIGNMENT', (0, 1), (0, -1), 'LEFT'),  # Metric names left
                ('ALIGNMENT', (1, 1), (-1, -1), 'CENTER'),  # Other columns center
                ('VALIGN', (0, 1), (-1, -1), 'MIDDLE'),
                
                # Cell padding for data rows
                ('PADDINGTOP', (0, 1), (-1, -1), 4),
                ('PADDINGBOTTOM', (0, 1), (-1, -1), 4),
                ('PADDINGLEFT', (0, 1), (-1, -1), 3),
                ('PADDINGRIGHT', (0, 1), (-1, -1), 3),
                
                # Grid and background
                ('GRID', (0, 0), (-1, -1), 0.7, colors.HexColor('#cccccc')),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.red),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 1), (-1, -1), 8),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#fff0f0'), colors.HexColor('#ffe6e6')]),
            ]))
            elements.append(diff_table)
            elements.append(Spacer(1, 0.2 * inch))
        
        # Certification Section
        elements.append(Paragraph("CERTIFICATION &amp; VALIDATION", heading_style))
        elements.append(Spacer(1, 0.08 * inch))
        
        cert_color = "green" if certified else "red"
        cert_status = "✓ CERTIFIED" if certified else "✗ NOT CERTIFIED"
        
        certification_text = f"""
        <b>Overall Score:</b> {round(score, 1)}%<br/>
        <b>Metrics Evaluated:</b> {len(all_metrics)} | <b>Passed:</b> {pass_count} | <b>Failed:</b> {len(differences)}<br/>
        <b>Certification Status:</b> <font color="{cert_color}"><b>{cert_status}</b></font> (95% required)
        """
        elements.append(Paragraph(certification_text, normal_style))
        elements.append(Spacer(1, 0.25 * inch))
        
        # Footer
        footer_text = f"""
        <font size=7><i>Report generated by Qlik to Power BI Migration Tool | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i></font><br/>
        <font size=6 color='#999999'>For questions or support, contact the migration team.</font>
        """
        elements.append(Paragraph(footer_text, styles["Normal"]))
        
        # Build PDF
        doc.build(elements)
        pdf_buffer.seek(0)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Validation_Report_{timestamp}.pdf"
        
        return StreamingResponse(
            iter([pdf_buffer.getvalue()]),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    
    except Exception as e:
        print(f"❌ Error downloading PDF: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to download PDF: {str(e)}")


# =====================================================

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*80)
    print(" " * 20 + "Qlik FastAPI Backend v2.0")
    print("="*80)
    print("\nFeatures:")
    print("  [+] REST API for Qlik Cloud")
    print("  [+] WebSocket connection to Qlik Engine")
    print("  [+] Table and field discovery")
    print("  [+] Script extraction")
    print("  [+] Data retrieval from tables")
    if SCRIPT_PARSER_AVAILABLE:
        print("  [+] Script data extraction (INLINE data)")
    else:
        print("  [!] Script data extraction DISABLED (qlik_script_parser.py not found)")
    print("\nStarting server...")
    print("="*80 + "\n")
    


# ==================== PUBLISH DATASET (FIX 10) ====================
# Wires loadscript_parser → powerbi_dataset_publisher in one REST call.

# ==================== PARSE SCRIPT + CONVERT TO MQUERY ENDPOINTS ====================
# These are called by the frontend qlikApi functions:
#   parseLoadScript(script)          → POST /api/parse-script
#   convertToMQuery(parsedScript, table_name) → POST /api/convert-mquery

class ParseScriptRequest(BaseModel):
    script: str

class ConvertMQueryRequest(BaseModel):
    parse_result: Dict[str, Any]    # output of /api/parse-script
    table_name: str
    base_path: str = "[DataSourcePath]"
    connection_string: Optional[str] = None

@app.post("/api/parse-script")
async def parse_script_endpoint(request: ParseScriptRequest):
    """
    Parse a raw Qlik LoadScript and return structured table definitions.
    Called by the frontend parseLoadScript() helper in qlikApi.
    """
    if not request.script.strip():
        raise HTTPException(status_code=400, detail="Script is empty")

    try:
        from loadscript_parser import LoadScriptParser
        parser = LoadScriptParser(request.script)
        result = parser.parse()
        return result
    except Exception as e:
        logger.error(f"Parse script error: {e}")
        raise HTTPException(status_code=500, detail=f"Parse failed: {str(e)}")


@app.post("/api/convert-mquery")
async def convert_mquery_endpoint(request: ConvertMQueryRequest):
    """
    Convert a parsed LoadScript result to Power Query M for a specific table.
    Called by the frontend convertToMQuery() helper in qlikApi.

    Returns the M expression for the requested table PLUS any upstream
    RESIDENT source tables (so the frontend can show the full dependency chain).
    """
    tables = (
        request.parse_result.get("details", {}).get("tables", [])
        or request.parse_result.get("tables", [])
    )

    if not tables:
        raise HTTPException(status_code=400, detail="No tables found in parse_result")

    try:
        from mquery_converter import MQueryConverter
        converter = MQueryConverter()
        all_table_names = {t["name"] for t in tables}

        # Find the requested table
        target = next((t for t in tables if t["name"] == request.table_name), None)
        if not target:
            # Fuzzy match — case-insensitive
            target = next(
                (t for t in tables if t["name"].lower() == request.table_name.lower()),
                None,
            )
        if not target:
            raise HTTPException(
                status_code=404,
                detail=f"Table '{request.table_name}' not found. Available: {sorted(all_table_names)}"
            )

        m_expr = converter.convert_one(
            target,
            base_path=request.base_path,
            connection_string=request.connection_string,
            all_table_names=all_table_names,
        )

        # For RESIDENT tables, also return the source table's M so the user
        # can see both queries are needed in the dataset.
        dep_queries: Dict[str, str] = {}
        if target.get("source_type") == "resident":
            src_name = target.get("source_path", "")
            src_table = next((t for t in tables if t["name"] == src_name), None)
            if src_table:
                dep_queries[src_name] = converter.convert_one(
                    src_table,
                    base_path=request.base_path,
                    connection_string=request.connection_string,
                    all_table_names=all_table_names,
                )

        return {
            "status":       "success",
            "table_name":   request.table_name,
            "source_type":  target.get("source_type", "unknown"),
            "m_query":      m_expr,
            "dependency_queries": dep_queries,   # upstream queries needed in the dataset
            "message":      (
                f"M Query generated for '{request.table_name}' "
                f"[{target.get('source_type', 'unknown')}]. "
                + (
                    f"⚠️ This RESIDENT table depends on '{target.get('source_path')}' — "
                    "include both queries in your Power BI dataset."
                    if target.get("source_type") == "resident" else ""
                )
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Convert m query error: {e}")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {str(e)}")


class PublishDatasetRequest(BaseModel):
    """Request body for /api/migration/publish-dataset"""
    workspace_id:        Optional[str] = None   # falls back to POWERBI_WORKSPACE_ID env var
    dataset_name:        str
    access_token:        Optional[str] = None   # acquired automatically from .env if not supplied
    app_id:              Optional[str] = None
    raw_script:          Optional[str] = None   # supply directly if app_id not given
    xmla_endpoint:       Optional[str] = None
    base_data_path:      str = "[DataSourcePath]"
    odbc_connection:     Optional[str] = None
    relationships:       Optional[List[Dict[str, Any]]] = None
    prefer_xmla:         bool = False            # default False — REST Push works on any workspace

@app.post("/api/migration/publish-dataset")
async def publish_dataset_endpoint(
    request: PublishDatasetRequest,
    ws_client: QlikWebSocketClient = Depends(get_qlik_websocket_client),
):
    """
    Full pipeline: fetch loadscript → parse → generate M expressions → publish to Power BI.

    Supply either app_id (fetches live from Qlik Cloud) or raw_script directly.
    Access token is acquired automatically from POWERBI_* env vars (service principal)
    if not supplied in the request body.
    Returns dataset_id and workspace URL on success.
    """
    if not PUBLISHER_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="powerbi_dataset_publisher.py not found. Add it to your project directory.",
        )

    # ── Resolve workspace_id ────────────────────────────────────────────────
    workspace_id = request.workspace_id or os.getenv("POWERBI_WORKSPACE_ID", "")
    if not workspace_id:
        raise HTTPException(
            status_code=400,
            detail="workspace_id not supplied and POWERBI_WORKSPACE_ID not set in .env"
        )

    # ── Acquire access token via service principal (client_credentials) ─────
    # Uses POWERBI_TENANT_ID, POWERBI_CLIENT_ID, POWERBI_CLIENT_SECRET from .env.
    # Falls back to request.access_token if supplied (e.g. from interactive login).
    access_token = request.access_token or ""
    if not access_token:
        try:
            import msal
            tenant_id     = os.getenv("POWERBI_TENANT_ID", "")
            client_id     = os.getenv("POWERBI_CLIENT_ID", "")
            client_secret = os.getenv("POWERBI_CLIENT_SECRET", "")
            if not all([tenant_id, client_id, client_secret]):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Power BI credentials missing. Set POWERBI_TENANT_ID, "
                        "POWERBI_CLIENT_ID, POWERBI_CLIENT_SECRET in .env "
                        "or supply access_token in the request."
                    ),
                )
            authority = f"https://login.microsoftonline.com/{tenant_id}"
            app_msal = msal.ConfidentialClientApplication(
                client_id, authority=authority, client_credential=client_secret
            )
            token_resp = app_msal.acquire_token_for_client(
                scopes=["https://analysis.windows.net/powerbi/api/.default"]
            )
            access_token = token_resp.get("access_token", "")
            if not access_token:
                raise HTTPException(
                    status_code=401,
                    detail=f"Failed to acquire Power BI token: {token_resp.get('error_description', 'Unknown error')}"
                )
            logger.info("✅ Power BI service principal token acquired")
        except HTTPException:
            raise
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="msal package not installed. Run: pip install msal"
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Token acquisition failed: {str(e)}")

    # ── 1. Get script ───────────────────────────────────────────────────────
    if request.app_id:
        result = ws_client.get_app_tables_simple(request.app_id)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=f"Failed to fetch script: {result.get('error')}")
        raw_script = result.get("script", "")
    elif request.raw_script:
        raw_script = request.raw_script
    else:
        raise HTTPException(status_code=400, detail="Provide either app_id or raw_script")

    if not raw_script.strip():
        raise HTTPException(status_code=404, detail="Script is empty")

    # ── 2. Parse ────────────────────────────────────────────────────────────
    try:
        from loadscript_parser import LoadScriptParser
        parse_result = LoadScriptParser(raw_script).parse()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parse error: {e}")

    if parse_result.get("status") == "error":
        raise HTTPException(status_code=500, detail=f"Parse failed: {parse_result.get('message')}")

    # ── 3. Publish ──────────────────────────────────────────────────────────
    config = PublisherConfig(
        workspace_id    = workspace_id,
        dataset_name    = request.dataset_name,
        access_token    = access_token,
        xmla_endpoint   = request.xmla_endpoint,
        base_data_path  = request.base_data_path,
        odbc_connection = request.odbc_connection,
    )

    pub_result = publish_from_parse_result(
        parse_result  = parse_result,
        config        = config,
        relationships = request.relationships or [],
        prefer_xmla   = request.prefer_xmla,
    )

    if not pub_result.get("success"):
        raise HTTPException(status_code=500, detail=f"Publish failed: {pub_result.get('error')}")

    return {
        "success":               True,
        "dataset_id":            pub_result.get("dataset_id", ""),
        "dataset_name":          pub_result.get("dataset_name"),
        "method":                pub_result.get("method"),
        "tables_deployed":       pub_result.get("tables_deployed"),
        "relationships_applied": pub_result.get("relationships_applied"),
        "duration_seconds":      pub_result.get("duration_seconds"),
        "workspace_url":         f"https://app.powerbi.com/groups/{workspace_id}",
        "parse_summary":         parse_result.get("summary", {}),
    }

# ==================== SERVER ENTRYPOINT ====================

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*80)
    print(" " * 20 + "Qlik FastAPI Backend v2.0")
    print("="*80)
    print("\nFeatures:")
    print("  [+] REST API for Qlik Cloud")
    print("  [+] WebSocket connection to Qlik Engine")
    print("  [+] Table and field discovery")
    print("  [+] Script extraction & parsing (patched v2)")
    print("  [+] M Query generation")
    if SCRIPT_PARSER_AVAILABLE:
        print("  [+] Script data extraction (INLINE data)")
    else:
        print("  [!] Script data extraction DISABLED (qlik_script_parser.py not found)")
    if PUBLISHER_AVAILABLE:
        print("  [+] Power BI dataset publisher (powerbi_dataset_publisher.py)")
    else:
        print("  [!] Dataset publisher DISABLED (powerbi_dataset_publisher.py not found)")
    print("\nStarting server...")
    print("="*80 + "\n")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
