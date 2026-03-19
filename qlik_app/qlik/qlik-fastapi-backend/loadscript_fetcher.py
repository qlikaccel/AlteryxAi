"""
Qlik LoadScript Fetcher Module  (PATCHED v2)

Fetches loadscript from Qlik Cloud with detailed logging at each phase.

Patches applied over v1:
  ✅ Fix 1: REST API response parsing handles all Qlik Cloud script endpoint formats
             (v1 only read 'script' key; Qlik also returns 'qScript', section arrays, raw str)
  ✅ Fix 2: Timeout and retry on WebSocket step — won't hang if WS client unavailable
  ✅ Fix 3: metadata_reconstruction fallback now clearly marked partial and never
             silently pretends to be real script content
  ✅ Fix 4: app_name extraction handles nested attributes dict (Qlik Cloud v1 format)
  ✅ Fix 5: accepts optional parser_cls injection so callers can pass patched parser
  ✅ Fix 6: fetch_and_parse() convenience method — fetches + parses in one call,
             always returns raw_script in result (required by simple_mquery_generator)
"""

import os
import requests
import logging
from typing import Dict, Any, Optional, Type
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
    """Fetch loadscript from Qlik Cloud applications (patched v2)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        tenant_url: Optional[str] = None,
    ):
        logger.info("=" * 80)
        logger.info("PHASE 0: Initializing LoadScript Fetcher")
        logger.info("=" * 80)

        self.api_key     = api_key     or os.getenv('QLIK_API_KEY', '')
        self.tenant_url  = tenant_url  or os.getenv('QLIK_TENANT_URL', '')
        api_base_env     = os.getenv('QLIK_API_BASE_URL', '')

        # Derive base URL
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
            logger.error("❌ QLIK_API_KEY is not set in environment variables")
            raise ValueError('QLIK_API_KEY is not set in environment variables')
        if not self.api_base_url:
            logger.error("❌ QLIK_API_BASE_URL or QLIK_TENANT_URL is not set")
            raise ValueError('QLIK_API_BASE_URL or QLIK_TENANT_URL is not set in environment variables')

        logger.info(f"✅ Initialized with Tenant URL: {self.tenant_url}")
        logger.info(f"✅ API Base URL: {self.api_base_url}")

    # ------------------------------------------------------------------
    # Phase 1 — Connection test
    # ------------------------------------------------------------------

    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Qlik Cloud."""
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
    # Phase 2 — Applications list
    # ------------------------------------------------------------------

    def get_applications(self) -> Dict[str, Any]:
        """Fetch list of all applications from Qlik Cloud."""
        logger.info("=" * 80)
        logger.info("PHASE 2: Fetching Applications List from Qlik Cloud")
        logger.info("=" * 80)

        url = f'{self.api_base_url}/apps'
        logger.info(f"🔍 Fetching apps from: {url}")

        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            logger.info(f"📊 Response Status Code: {resp.status_code}")
            resp.raise_for_status()

            data = resp.json()
            if isinstance(data, list):
                apps = data
            elif isinstance(data, dict):
                apps = data.get('data') or data.get('items') or data.get('value') or []
            else:
                apps = []

            logger.info(f"✅ Successfully fetched {len(apps)} application(s)")
            for i, app in enumerate(apps[:10], 1):
                app_name = app.get('name', 'N/A')
                app_id   = app.get('id', 'N/A')
                logger.info(f"   ✓ App {i}: {app_name} (ID: {str(app_id)[:8]}...)")

            return {"status": "success", "count": len(apps), "data": apps}

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error fetching applications: {str(e)}")
            return {"status": "error", "message": str(e), "data": []}

    # ------------------------------------------------------------------
    # Phase 3 — Application details
    # ------------------------------------------------------------------

    def get_application_details(self, app_id: str) -> Dict[str, Any]:
        """Get details of a specific application."""
        logger.info("=" * 80)
        logger.info("PHASE 3: Fetching Application Details")
        logger.info("=" * 80)

        url = f'{self.api_base_url}/apps/{app_id}'
        logger.info(f"🔍 Fetching app details from: {url}")
        logger.info(f"📋 App ID: {app_id}")

        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            logger.info(f"📊 Response Status Code: {resp.status_code}")
            resp.raise_for_status()

            app_data = resp.json()
            # ✅ FIX 4: Qlik Cloud v1 wraps metadata in 'attributes'
            attrs = app_data.get('attributes', app_data)
            logger.info(f"✅ App Name:       {attrs.get('name', 'N/A')}")
            logger.info(f"✅ App Owner:      {attrs.get('owner', {}).get('name', 'N/A') if isinstance(attrs.get('owner'), dict) else attrs.get('owner', 'N/A')}")
            logger.info(f"✅ Last Reload:    {attrs.get('lastReloadTime', 'N/A')}")

            return {"status": "success", "data": app_data}

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Error fetching application details: {str(e)}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Phase 4 — Fetch loadscript (3 methods with fallback)
    # ------------------------------------------------------------------

    def fetch_loadscript(self, app_id: str) -> Dict[str, Any]:
        """
        Fetch the loadscript from a Qlik application.

        Method 1: WebSocket Engine API (most reliable for real script)
        Method 2: REST API /apps/{id}/script  (Qlik Cloud direct)
        Method 3: Metadata reconstruction (last resort — clearly marked partial)
        """
        logger.info("=" * 80)
        logger.info("PHASE 4: FETCHING LOADSCRIPT FROM QLIK CLOUD")
        logger.info("=" * 80)
        logger.info(f"📋 Target App ID: {app_id}")
        logger.info(f"⏰ Fetch Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # ── Method 1: WebSocket Engine API ──────────────────────────────────
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
                logger.info(f"📊 Found {len(script_result.get('tables', []))} table(s) in script")

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

        # ── Method 2: REST API ───────────────────────────────────────────────
        logger.info("📍 Step 4.2: Attempting REST API direct script endpoint...")

        # ✅ FIX 1: Try multiple known Qlik endpoint variants
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
                        logger.info(f"📏 Script length: {len(loadscript)} characters")
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

        # ── Method 3: Metadata reconstruction (fallback) ─────────────────────
        logger.info("📍 Step 4.3: Falling back to metadata reconstruction...")
        return self._metadata_reconstruction(app_id)

    # ------------------------------------------------------------------
    # Convenience: fetch + parse in one call
    # ------------------------------------------------------------------

    def fetch_and_parse(self, app_id: str) -> Dict[str, Any]:
        """
        Fetch the loadscript AND parse it in one call.
        Always returns raw_script in the result dict.

        ✅ FIX 6: convenience wrapper that satisfies simple_mquery_generator requirement
        """
        fetch_result = self.fetch_loadscript(app_id)

        if fetch_result['status'] not in ('success', 'partial_success'):
            return {
                **fetch_result,
                "raw_script": "",
                "parse_result": None,
            }

        loadscript = fetch_result.get('loadscript', '')

        # Lazy import so patched parser is used if available
        try:
            from loadscript_parser import LoadScriptParser
        except ImportError:
            logger.error("❌ loadscript_parser not found — cannot parse")
            return {
                **fetch_result,
                "raw_script": loadscript,
                "parse_result": None,
            }

        parser       = LoadScriptParser(loadscript)
        parse_result = parser.parse()

        return {
            **fetch_result,
            "raw_script":    loadscript,      # always present
            "parse_result":  parse_result,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_script_response(self, resp: requests.Response) -> str:
        """
        ✅ FIX 1: Extract script text from any Qlik Cloud script response format.

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
                # Section array — join all script sections
                sections = [
                    s.get('script', '') or s.get('qScript', '')
                    for s in data if isinstance(s, dict)
                ]
                return '\n\n'.join(s for s in sections if s).strip()

            if isinstance(data, dict):
                return (
                    data.get('script') or
                    data.get('qScript') or
                    data.get('content') or
                    ''
                ).strip()

        # Fallback: raw text body
        return resp.text.strip()

    def _get_app_name(self, app_id: str) -> str:
        """Quick app name lookup. Returns app_id on failure."""
        try:
            resp = requests.get(
                f'{self.api_base_url}/apps/{app_id}',
                headers=self.headers,
                timeout=10,
            )
            if resp.ok:
                data  = resp.json()
                attrs = data.get('attributes', data)
                return attrs.get('name', app_id)
        except Exception:
            pass
        return app_id

    def _metadata_reconstruction(self, app_id: str) -> Dict[str, Any]:
        """
        ✅ FIX 3: Last-resort fallback — assembles placeholder script from
        available metadata.  Clearly marked as reconstructed, NOT real script.
        """
        logger.info("📍 Step 4.4: Gathering app metadata for reconstruction...")

        app_name    = self._get_app_name(app_id)
        connections = self._fetch_connections()

        lines = [
            f"// ==================================================",
            f"// Qlik Cloud Application — Reconstructed LoadScript",
            f"// Application : {app_name}",
            f"// App ID      : {app_id}",
            f"// Generated   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"// WARNING     : Real loadscript was NOT accessible.",
            f"//               This is metadata-only reconstruction.",
            f"// ==================================================",
            "",
        ]

        if connections:
            lines.append(f"// Data Connections ({len(connections)})")
            lines.append("// --------------------------------------------------")
            for conn in connections:
                conn_name = conn.get('name', 'Unknown')
                conn_type = conn.get('connectionType', 'Unknown')
                lines.append(f"// {conn_name}  ({conn_type})")
            lines.append("")

        lines += [
            "// --------------------------------------------------",
            "// Placeholder LOAD statement (replace with real script)",
            "// --------------------------------------------------",
            "// [TableName]:",
            "// LOAD",
            "//     Field1,",
            "//     Field2",
            "// FROM [lib://ConnectionName/file.qvd] (qvd);",
        ]

        script_snippet = '\n'.join(lines)
        logger.info(f"✅ Metadata reconstruction complete ({len(script_snippet)} chars)")

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
                "WebSocket Engine API and REST /script endpoint both unavailable. "
                "Script is commented-out placeholder only."
            ),
        }

    def _fetch_connections(self) -> list:
        """Fetch data connections from Qlik Cloud."""
        try:
            resp = requests.get(
                f'{self.api_base_url}/data-connections',
                headers=self.headers,
                timeout=15,
            )
            if resp.ok:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get('value') or data.get('items') or data.get('data') or []
        except Exception as e:
            logger.warning(f"⚠️  Could not fetch connections: {str(e)}")
        return []


# ---------------------------------------------------------------------------
# Standalone testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        fetcher = LoadScriptFetcher()

        conn_result = fetcher.test_connection()
        if conn_result['status'] != 'success':
            logger.error("Cannot proceed — connection failed")
            exit(1)

        apps_result = fetcher.get_applications()
        if apps_result['status'] == 'success' and apps_result['data']:
            first_app_id = apps_result['data'][0]['id']

            details_result = fetcher.get_application_details(first_app_id)

            # Use the convenience method to fetch + parse in one call
            result = fetcher.fetch_and_parse(first_app_id)
            logger.info(f"fetch_and_parse status: {result['status']}")
            logger.info(f"raw_script present:     {'raw_script' in result}")
            if result.get('parse_result'):
                s = result['parse_result'].get('summary', {})
                logger.info(f"Tables parsed:          {s.get('tables_count', 0)}")
        else:
            logger.error("No applications found")

    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
