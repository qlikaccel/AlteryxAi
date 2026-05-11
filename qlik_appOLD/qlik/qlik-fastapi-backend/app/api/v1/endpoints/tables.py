"""
table_data_router.py
====================
FastAPI router that correctly fetches table data from QlikSense
for BOTH inline-loaded and CSV file-loaded (lib://DataFiles/) tables.

The root cause of the display error:
  - INLINE tables  → data lives in the app session immediately
  - CSV/lib:// tables → data must be fetched via the Qlik Engine
    using createSessionObject + getLayout + getHyperCubeData
    (a plain REST call to /v1/apps/{id}/data/tables only returns
     the schema, NOT the actual row values)
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional


import websockets
import httpx
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ─────────────────────────────────────────────────────────────
#  Config  (override via env vars in production)
# ─────────────────────────────────────────────────────────────
QLIK_BASE_URL   = "https://your-tenant.us.qlikcloud.com"   # ← your tenant URL
QLIK_API_KEY    = "your-api-key-here"                      # ← your API key
MAX_ROWS        = 200000   # max rows to pull per table (increased to support large exports) 


# ─────────────────────────────────────────────────────────────
#  Response model
# ─────────────────────────────────────────────────────────────
print("entering reponse model")
class TableDataResponse(BaseModel):
    table_name: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    total_rows: int
    source: str   # "engine_hypercube" | "rest_fallback"


# ─────────────────────────────────────────────────────────────
#  Main endpoint
# ─────────────────────────────────────────────────────────────
@router.get(
    "/applications/{app_id}/table/{table_name}/data",
    response_model=TableDataResponse,
)
async def get_table_data(
    app_id: str  = Path(..., description="QlikSense app GUID"),
    table_name: str = Path(..., description="Table name as defined in load script"),
    limit: int  = Query(MAX_ROWS, le=MAX_ROWS),
    offset: int = Query(0, ge=0, description="Row offset for paging"),
):
    """
    Fetch actual row data for a QlikSense table, supports offset+limit paging.
    """
    try:
        result = await fetch_table_via_engine(app_id, table_name, limit, offset)
        
        return result
    except Exception as e:
        logger.error(f"Engine fetch failed for {table_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

print("exiting table data router")
# ─────────────────────────────────────────────────────────────
#  Core: Qlik Engine JSON API over WebSocket
# ─────────────────────────────────────────────────────────────
async def fetch_table_via_engine(
    app_id: str,
    table_name: str,
    limit: int = MAX_ROWS,
    offset: int = 0,
) -> TableDataResponse:
    """
    Uses the Qlik Engine JSON API (WebSocket) to:
    1. Open the app
    2. Get all field names for the requested table
    3. Build a HyperCube with those fields
    4. Page through rows starting at `offset` and return up to `limit`
    """

    ws_url = (
        f"wss://{QLIK_BASE_URL.replace('https://', '')}"
        f"/app/{app_id}"
    )
    headers = {
        "Authorization": f"Bearer {QLIK_API_KEY}",
    }

    async with websockets.connect(ws_url, extra_headers=headers) as ws:
        msg_id = 0

        async def rpc(method: str, handle: int, params: list) -> dict:
            nonlocal msg_id
            msg_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": method,
                "handle": handle,
                "params": params,
            }
            await ws.send(json.dumps(payload))
            while True:
                raw = await ws.recv()
                resp = json.loads(raw)
                if resp.get("id") == msg_id:
                    if "error" in resp:
                        raise RuntimeError(f"Qlik RPC error: {resp['error']}")
                    return resp.get("result", {})

        # ── Step 1: Open the app ──────────────────────────────
        result = await rpc("OpenDoc", -1, [app_id])
        app_handle = result["qReturn"]["qHandle"]
        logger.info(f"✅ App opened, handle={app_handle}")

        # ── Step 2: Get table fields from data model ──────────
        fields = await get_table_fields(rpc, app_handle, table_name)

        if not fields:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Table '{table_name}' not found in app data model. "
                    f"Ensure the load script ran successfully and data model "
                    f"is not empty."
                ),
            )

        logger.info(f"✅ Fields for {table_name}: {fields}")

        # ── Step 3: Create HyperCube session object ───────────
        hyper_cube_def = build_hypercube_def(fields)

        obj_result = await rpc(
            "CreateSessionObject",
            app_handle,
            [
                {
                    "qInfo": {"qType": "table_extract"},
                    "qHyperCubeDef": hyper_cube_def,
                }
            ],
        )
        obj_handle = obj_result["qReturn"]["qHandle"]
        logger.info(f"✅ Session object created, handle={obj_handle}")

        # ── Step 4: Get layout to know total row count ─────────
        layout = await rpc("GetLayout", obj_handle, [])
        cube   = layout["qLayout"]["qHyperCube"]
        total  = cube["qSize"]["qcy"]   # total rows
        n_cols = cube["qSize"]["qcx"]   # total cols

        logger.info(f"✅ HyperCube size: {total} rows × {n_cols} cols")

        # ── Step 5: Page through rows starting at `offset` ───
        all_rows: List[Dict[str, Any]] = []
        page_size = 1000
        top = offset if offset and offset > 0 else 0

        # Compute the fetch target (don't go beyond `total` if engine reported it)
        target = (offset or 0) + (limit or MAX_ROWS)
        if total and total > 0:
            target = min(target, total)

        # If offset is beyond the available rows, return empty list
        if total and offset and offset >= total:
            return TableDataResponse(
                table_name=table_name,
                columns=fields,
                rows=[],
                total_rows=total,
                source="engine_hypercube",
            )

        while top < target:
            rows_to_fetch = min(page_size, target - top)

            page_result = await rpc(
                "GetHyperCubeData",
                obj_handle,
                [
                    "/qHyperCubeDef",
                    [
                        {
                            "qLeft": 0,
                            "qTop": top,
                            "qWidth": n_cols,
                            "qHeight": rows_to_fetch,
                        }
                    ],
                ],
            )

            qPages = page_result.get("qDataPages", [])
            for page in qPages:
                for qRow in page.get("qMatrix", []):
                    row: Dict[str, Any] = {}
                    for col_idx, cell in enumerate(qRow):
                        col_name = fields[col_idx] if col_idx < len(fields) else f"col_{col_idx}"
                        if cell.get("qIsNull"):
                            row[col_name] = None
                        elif cell.get("qText", "") not in ("", "-"):
                            row[col_name] = cell["qText"]
                        elif "qNum" in cell and cell["qNum"] != "NaN":
                            row[col_name] = cell["qNum"]
                        else:
                            row[col_name] = None
                    all_rows.append(row)

            top += rows_to_fetch
            logger.info(f"  Fetched rows {top - rows_to_fetch}–{top} / {total}")

        return TableDataResponse(
            table_name=table_name,
            columns=fields,
            rows=all_rows,
            total_rows=total,
            source="engine_hypercube",
        )


# ─────────────────────────────────────────────────────────────
#  Get field names for a specific table from the data model
# ─────────────────────────────────────────────────────────────
async def get_table_fields(rpc, app_handle: int, table_name: str) -> List[str]:
    """
    Calls GetTablesAndKeys to find all fields in the requested table.
    Works for BOTH inline and lib://DataFiles/ loaded tables.
    """
    tables_result = await rpc(
        "GetTablesAndKeys",
        app_handle,
        [
            {"qcx": 0, "qcy": 0},   # windowSize (ignored for metadata)
            {"qcx": 0, "qcy": 0},   # nullSize
            30,                      # cellHeight
            True,                    # sysTables
            False,                   # showHidden
        ],
    )

    qtr = tables_result.get("qtr", [])   # list of tables

    for table in qtr:
        name = table.get("qName", "")
        if name.lower() == table_name.lower():
            # Return field names in order
            return [
                f.get("qName", f"field_{i}")
                for i, f in enumerate(table.get("qFields", []))
                if not f.get("qName", "").startswith("$")   # skip system fields
            ]

    # If exact match not found, try partial (e.g. "Model_Master" → "Ford_Model_Master")
    for table in qtr:
        if table_name.lower() in table.get("qName", "").lower():
            return [
                f.get("qName", f"field_{i}")
                for i, f in enumerate(table.get("qFields", []))
                if not f.get("qName", "").startswith("$")
            ]

    return []


# ─────────────────────────────────────────────────────────────
#  Build HyperCube definition from field list
# ─────────────────────────────────────────────────────────────
def build_hypercube_def(fields: List[str]) -> dict:
    """
    Creates a HyperCube that exposes all fields of the table
    as dimensions (no aggregation = raw row-level data).
    """
    return {
        "qStateName": "$",
        "qDimensions": [
            {
                "qDef": {
                    "qFieldDefs": [field],
                    "qFieldLabels": [field],
                    "qSortCriterias": [{"qSortByLoadOrder": 1}],
                },
                "qNullSuppression": False,
                "qOtherTotalSpec": {"qOtherMode": "OTHER_OFF"},
            }
            for field in fields
        ],
        "qMeasures": [],
        "qInitialDataFetch": [],  # we'll use GetHyperCubeData manually
        "qSuppressZero": False,
        "qSuppressMissing": False,
        "qMode": "S",   # Straight table mode
    }


# ─────────────────────────────────────────────────────────────
#  REST fallback: get table schema (field names only, no data)
#  Only useful for column names, NOT row data
# ─────────────────────────────────────────────────────────────
async def get_table_schema_rest(app_id: str, table_name: str) -> List[str]:
    """
    Uses the QlikSense REST API to get field names.
    NOTE: This returns SCHEMA only, not actual row data.
    Use fetch_table_via_engine() for real data.
    """
    url = f"{QLIK_BASE_URL}/api/v1/apps/{app_id}/data/tables"
    headers = {"Authorization": f"Bearer {QLIK_API_KEY}"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        tables = resp.json().get("data", [])

    for table in tables:
        if table.get("name", "").lower() == table_name.lower():
            return [f["name"] for f in table.get("fields", [])]

    return 