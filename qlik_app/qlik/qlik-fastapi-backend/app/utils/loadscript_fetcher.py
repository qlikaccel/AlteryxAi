"""
Qlik LoadScript Fetcher  (PATCHED v3 - AUTO SCHEMA)

v3 changes over v2:
  ✅ Fix A: fetch_and_parse() now calls GetTablesAndKeys via WebSocket
            to collect the real field-per-table map BEFORE parsing.
            The map is passed to LoadScriptParser.parse(qlik_fields_map=...)
            so LOAD * tables automatically get explicit column lists.

  ✅ Fix B: get_data_model_fields() is a new public helper that any caller
            can use to retrieve {table_name: [field_names]} from a Qlik app
            via WebSocket Engine API.

  ✅ Fix C: fetch_loadscript() is unchanged — backward compatible.
            Only fetch_and_parse() is extended.
"""

import os
import json
import requests
import logging
from typing import Dict, Any, Optional, List
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# Configure logging with timestamps
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


class LoadScriptFetcher:
    """Fetch loadscript from Qlik Cloud applications (v3 - auto schema)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        tenant_url: Optional[str] = None,
    ):
        logger.info("=" * 80)
        logger.info("PHASE 0: Initializing LoadScript Fetcher")
        logger.info("=" * 80)

        self.api_key    = api_key    or os.getenv('QLIK_API_KEY', '')
        self.tenant_url = tenant_url or os.getenv('QLIK_TENANT_URL', '')
        api_base_env    = os.getenv('QLIK_API_BASE_URL', '')

        if api_base_env:
            self.api_base_url = api_base_env
        elif self.tenant_url:
            self.api_base_url = self.tenant_url.rstrip('/') + '/api/v1'
        else:
            self.api_base_url = ''

        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type':  'application/json',
            'Accept':        'application/json',
        }

        if not self.api_key:
            raise ValueError('QLIK_API_KEY is not set in environment variables')
        if not self.api_base_url:
            raise ValueError('QLIK_API_BASE_URL or QLIK_TENANT_URL is not set')

        logger.info(f"✅ Initialized with Tenant URL: {self.tenant_url}")
        logger.info(f"✅ API Base URL: {self.api_base_url}")

    # ------------------------------------------------------------------
    # Phase 1
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        logger.info("=" * 80)
        logger.info("PHASE 1: Testing Connection to Qlik Cloud")
        logger.info("=" * 80)
        url = f'{self.api_base_url}/users/me'
        logger.info(f"🔌 Testing connection to: {url}")
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            logger.info(f"📊 Response Status Code: {resp.status_code}")
            resp.raise_for_status()
            user_data = resp.json()
            logger.info(f"✅ Connection successful!")
            logger.info(f"✅ User ID:   {user_data.get('id', 'N/A')}")
            logger.info(f"✅ User Name: {user_data.get('name', 'N/A')}")
            return {"status": "success", "data": user_data}
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Connection test failed: {str(e)}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Phase 2
    # ------------------------------------------------------------------

    def get_applications(self) -> Dict[str, Any]:
        logger.info("=" * 80)
        logger.info("PHASE 2: Fetching Applications List from Qlik Cloud")
        logger.info("=" * 80)
        url = f'{self.api_base_url}/apps'
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                apps = data
            elif isinstance(data, dict):
                apps = data.get('data') or data.get('items') or data.get('value') or []
            else:
                apps = []
            logger.info(f"✅ Successfully fetched {len(apps)} application(s)")
            return {"status": "success", "count": len(apps), "data": apps}
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error fetching applications: {str(e)}")
            return {"status": "error", "message": str(e), "data": []}

    # ------------------------------------------------------------------
    # Phase 3
    # ------------------------------------------------------------------

    def get_application_details(self, app_id: str) -> Dict[str, Any]:
        url = f'{self.api_base_url}/apps/{app_id}'
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            app_data = resp.json()
            attrs = app_data.get('attributes', app_data)
            logger.info(f"✅ App Name: {attrs.get('name', 'N/A')}")
            return {"status": "success", "data": app_data}
        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # ✅ Fix B: NEW — get real column names from Qlik data model
    # ------------------------------------------------------------------

    def get_data_model_fields(self, app_id: str) -> Dict[str, List[str]]:
        """
        ✅ Fix B — Fetch the complete {table_name: [field_names]} map from
        a Qlik app's data model via WebSocket GetTablesAndKeys.

        This is the key data that makes LOAD * tables publish with real
        column names instead of 0 columns.

        Returns:
            Dict mapping table_name → sorted list of field names.
            Empty dict on failure (caller must handle gracefully).
        """
        logger.info("=" * 80)
        logger.info("FETCHING DATA MODEL FIELDS via GetTablesAndKeys")
        logger.info("=" * 80)

        try:
            from qlik_websocket_client import QlikWebSocketClient
            ws = QlikWebSocketClient()
            result = ws.get_data_model(app_id)

            if not result.get('success'):
                logger.warning(
                    "[get_data_model_fields] WebSocket get_data_model failed: %s",
                    result.get('error', 'unknown')
                )
                return {}

            # Build table → field list from the response
            # Expected format from QlikWebSocketClient.get_data_model():
            #   result['tables'] = list of {name, fields: [{name, ...}]}
            fields_map: Dict[str, List[str]] = {}
            tables = result.get('tables', [])

            for table in tables:
                tname  = table.get('name') or table.get('qName', '')
                if not tname:
                    continue
                flist  = table.get('fields') or table.get('qFields', [])
                cols: List[str] = []
                for f in flist:
                    fname = f.get('name') or f.get('qName', '')
                    # Skip Qlik system fields ($rowno, $key, etc.)
                    if fname and not fname.startswith('$'):
                        cols.append(fname)
                if cols:
                    fields_map[tname] = cols
                    logger.info(
                        "   ✅ %s → %d fields: %s",
                        tname, len(cols), cols[:6]
                    )

            logger.info(
                "✅ get_data_model_fields: %d tables, %d total fields",
                len(fields_map),
                sum(len(v) for v in fields_map.values())
            )
            return fields_map

        except ImportError:
            logger.warning("[get_data_model_fields] qlik_websocket_client not available")
        except Exception as exc:
            logger.warning("[get_data_model_fields] Failed: %s", exc)

        return {}

    # ------------------------------------------------------------------
    # Phase 4
    # ------------------------------------------------------------------

    def fetch_loadscript(self, app_id: str) -> Dict[str, Any]:
        logger.info("=" * 80)
        logger.info("PHASE 4: FETCHING LOADSCRIPT FROM QLIK CLOUD")
        logger.info("=" * 80)
        logger.info(f"📋 Target App ID: {app_id}")
        logger.info(f"⏰ Fetch Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Method 1: WebSocket
        logger.info("📍 Step 4.1: Attempting to fetch via WebSocket (Engine API)...")
        try:
            from qlik_websocket_client import QlikWebSocketClient
            ws_client = QlikWebSocketClient()
            script_result = ws_client._get_app_script_websocket(app_id)
            logger.info(
                f"   WebSocket result: success={script_result.get('success')},"
                f" has_script={bool(script_result.get('script'))}"
            )
            if script_result and script_result.get('success') and script_result.get('script'):
                loadscript = script_result['script']
                logger.info(f"✅ Successfully fetched real loadscript via WebSocket!")
                logger.info(f"📏 Script length: {len(loadscript)} characters")
                return {
                    "status":        "success",
                    "app_id":        app_id,
                    "app_name":      script_result.get('app_name', 'Unknown'),
                    "loadscript":    loadscript,
                    "method":        "websocket_engine_api",
                    "timestamp":     datetime.now().isoformat(),
                    "script_length": len(loadscript),
                    "tables":        script_result.get('tables', []),
                    "message":       "✅ Full loadscript fetched via WebSocket Engine API",
                }
            else:
                logger.warning(f"⚠️  WebSocket non-success: {script_result.get('error', 'No error message')}")
        except ImportError:
            logger.warning("⚠️  qlik_websocket_client not available — skipping WebSocket method")
        except Exception as ws_error:
            logger.warning(f"⚠️  WebSocket method failed: {str(ws_error)}")

        # Method 2: REST API
        logger.info("📍 Step 4.2: Attempting REST API direct script endpoint...")
        script_endpoints = [
            f'{self.api_base_url}/apps/{app_id}/script',
            f'{self.api_base_url}/apps/{app_id}/scripts/main',
        ]
        for script_url in script_endpoints:
            try:
                logger.info(f"🔍 Trying: {script_url}")
                resp = requests.get(script_url, headers=self.headers, timeout=15)
                logger.info(f"📊 Response status: {resp.status_code}")
                if resp.status_code == 200:
                    loadscript = self._parse_script_response(resp)
                    if loadscript:
                        logger.info(f"✅ Successfully fetched loadscript from REST API!")
                        return {
                            "status":        "success",
                            "app_id":        app_id,
                            "app_name":      self._get_app_name(app_id),
                            "loadscript":    loadscript,
                            "method":        "rest_api_direct",
                            "timestamp":     datetime.now().isoformat(),
                            "script_length": len(loadscript),
                            "message":       "Loadscript fetched via REST API",
                        }
            except Exception as e:
                logger.warning(f"⚠️  REST endpoint {script_url} failed: {str(e)}")

        # Method 3: Metadata reconstruction
        logger.info("📍 Step 4.3: Falling back to metadata reconstruction...")
        return self._metadata_reconstruction(app_id)

    # ------------------------------------------------------------------
    # ✅ Fix A: fetch_and_parse() — auto-wires qlik_fields_map
    # ------------------------------------------------------------------

    def fetch_and_parse(
        self,
        app_id: str,
        qlik_fields_map: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        """
        ✅ Fix A — Fetch the loadscript AND parse it in one call.

        NEW in v3:
          1. If qlik_fields_map is not supplied by the caller, it is fetched
             automatically from the Qlik data model via GetTablesAndKeys.
          2. The map is passed to LoadScriptParser.parse() so LOAD * tables
             get explicit column lists baked into their table dicts.
          3. The map is also returned in the result dict so the caller can
             pass it to publish_semantic_model() without a separate call.

        This means the entire pipeline (fetch → parse → convert → publish)
        is automatic — no manual column lists, no hardcoding.
        """
        # Step 1: fetch loadscript
        fetch_result = self.fetch_loadscript(app_id)
        if fetch_result['status'] not in ('success', 'partial_success'):
            return {
                **fetch_result,
                "raw_script":      "",
                "parse_result":    None,
                "qlik_fields_map": {},
            }
        loadscript = fetch_result.get('loadscript', '')

        # Step 2: get real column names if not provided
        if not qlik_fields_map:
            logger.info("📍 Fetching data model fields (GetTablesAndKeys)...")
            qlik_fields_map = self.get_data_model_fields(app_id)
            if qlik_fields_map:
                logger.info(
                    "✅ Auto-fetched qlik_fields_map: %d tables",
                    len(qlik_fields_map)
                )
            else:
                logger.warning(
                    "⚠️  Could not fetch qlik_fields_map — LOAD * tables will use "
                    "dynamic schema (columns appear after first Power BI refresh)."
                )

        # Step 3: parse with injected map
        try:
            from app.services.loadscript_parser import LoadScriptParser
        except ImportError:
            logger.error("❌ loadscript_parser not found")
            return {
                **fetch_result,
                "raw_script":      loadscript,
                "parse_result":    None,
                "qlik_fields_map": qlik_fields_map,
            }

        parser       = LoadScriptParser(loadscript)
        parse_result = parser.parse(qlik_fields_map=qlik_fields_map)

        return {
            **fetch_result,
            "raw_script":      loadscript,
            "parse_result":    parse_result,
            "qlik_fields_map": qlik_fields_map,   # always present for downstream
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_script_response(self, resp: requests.Response) -> str:
        """
        Extract script text from any Qlik Cloud script response format.

        Known formats:
          • {"script": "..."}                  — most common
          • {"qScript": "..."}                 — Engine API variant
          • [{"script": "...", "id": 1}, ...]  — section array
          • "raw string"                        — direct text body
        """
        content_type = resp.headers.get('Content-Type', '')
        if 'json' in content_type:
            try:
                data = resp.json()
            except Exception:
                return resp.text.strip()
            if isinstance(data, str):
                return data.strip()
            if isinstance(data, list):
                sections = [
                    s.get('script', '') or s.get('qScript', '')
                    for s in data if isinstance(s, dict)
                ]
                return '\n\n'.join(s for s in sections if s).strip()
            if isinstance(data, dict):
                return (data.get('script') or data.get('qScript') or data.get('content') or '').strip()
        return resp.text.strip()

    def _get_app_name(self, app_id: str) -> str:
        try:
            resp = requests.get(f'{self.api_base_url}/apps/{app_id}', headers=self.headers, timeout=10)
            if resp.ok:
                data  = resp.json()
                attrs = data.get('attributes', data)
                return attrs.get('name', app_id)
        except Exception:
            pass
        return app_id

    def _metadata_reconstruction(self, app_id: str) -> Dict[str, Any]:
        app_name    = self._get_app_name(app_id)
        connections = self._fetch_connections()
        lines = [
            f"// ==================================================",
            f"// Qlik Cloud Application — Reconstructed LoadScript",
            f"// Application : {app_name}",
            f"// App ID      : {app_id}",
            f"// Generated   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"// WARNING     : Real loadscript was NOT accessible.",
            f"// ==================================================", "",
        ]
        if connections:
            lines.append(f"// Data Connections ({len(connections)})")
            for conn in connections:
                lines.append(f"// {conn.get('name','Unknown')}  ({conn.get('connectionType','Unknown')})")
            lines.append("")
        script_snippet = '\n'.join(lines)
        return {
            "status":             "partial_success",
            "app_id":             app_id,
            "app_name":           app_name,
            "loadscript":         script_snippet,
            "method":             "metadata_reconstruction",
            "timestamp":          datetime.now().isoformat(),
            "script_length":      len(script_snippet),
            "connections_count":  len(connections),
            "connections":        connections[:5],
            "message": (
                "⚠️ Loadscript partially available — "
                "WebSocket Engine API and REST /script endpoint both unavailable."
            ),
        }

    def _fetch_connections(self) -> list:
        try:
            resp = requests.get(f'{self.api_base_url}/data-connections', headers=self.headers, timeout=15)
            if resp.ok:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get('value') or data.get('items') or data.get('data') or []
        except Exception as e:
            logger.warning(f"⚠️  Could not fetch connections: {str(e)}")
        return []
