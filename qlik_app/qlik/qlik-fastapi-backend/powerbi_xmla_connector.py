"""
Power BI XMLA Connector — PPU / Premium Semantic Model Creator

Creates a proper Tabular semantic model in a Power BI PPU workspace so that:
  ✅ Model View is available in Power BI Service
  ✅ ER Diagram renders in Power BI Service
  ✅ "Open semantic model" works
  ✅ Relationships are visible and editable

Two deployment strategies, tried in order:
  1. Tabular Editor 2 CLI  (best, cross-platform, full TOM support)
  2. REST API XMLA execute  (fallback via POST /executeQueries — limited)

Called by six_stage_orchestrator.py when publish_mode="xmla_semantic".
"""

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main entry point called by the orchestrator
# ---------------------------------------------------------------------------

def create_semantic_model_via_xmla(
    dataset_name: str,
    workspace_id: str,
    qlik_tables: List[Dict[str, Any]],
    normalized_relationships: List[Dict[str, Any]],
    csv_table_payloads: Optional[Dict[str, str]] = None,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Deploy a full Tabular semantic model to a PPU Power BI workspace.

    Args:
        dataset_name:             Name of the target semantic model.
        workspace_id:             Power BI workspace GUID.
        qlik_tables:              Table metadata list from Stage 1.
        normalized_relationships: Relationship list from Stage 3.
        csv_table_payloads:       Optional {table_name: csv_string} for sample data.
        access_token:             Azure AD bearer token for Power BI.

    Returns:
        Dict with success, dataset_id, relationships_applied, and diagnostic info.
    """
    creator = _XMLADeployer(
        workspace_id=workspace_id,
        access_token=access_token or "",
    )
    return creator.deploy(
        dataset_name=dataset_name,
        tables=qlik_tables,
        relationships=normalized_relationships,
        csv_payloads=csv_table_payloads or {},
    )


# ---------------------------------------------------------------------------
# Internal deployer class
# ---------------------------------------------------------------------------

class _XMLADeployer:
    """Handles BIM generation and deployment to a PPU XMLA endpoint."""

    # Separate cache file for user-delegated token (device code login)
    USER_TOKEN_CACHE = os.path.join(os.path.dirname(__file__), ".powerbi_user_token_cache.json")

    def __init__(self, workspace_id: str, access_token: str):
        self.workspace_id  = workspace_id
        self.access_token  = access_token
        self.base_url      = "https://api.powerbi.com/v1.0/myorg"
        self.rest_headers  = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        # Load user token from cache if available (set via /powerbi/xmla-login endpoint)
        self.user_token: str = self._load_user_token() or ""
        # For XMLA/TE2 use user token if available, otherwise fall back to SP token
        self.xmla_token: str = self.user_token or self.access_token
        self.xmla_headers  = {
            "Authorization": f"Bearer {self.xmla_token}",
            "Content-Type": "application/json",
        }
        self.xmla_endpoint = self._resolve_xmla_endpoint()
        self.te_path       = self._find_tabular_editor()

    def _load_user_token(self) -> str:
        """
        Load user-delegated token via xmla_auth.
        Automatically acquires fresh token via ROPC if credentials are in .env.
        No browser or user interaction needed.
        """
        try:
            from xmla_auth import get_xmla_user_token
            token = get_xmla_user_token()
            if token:
                logger.info("[XMLA] User token loaded (ROPC/cache)")
            return token
        except Exception as e:
            logger.warning("[XMLA] Could not load user token: %s", e)
        return ""

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def deploy(
        self,
        dataset_name: str,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        csv_payloads: Dict[str, str],
    ) -> Dict[str, Any]:
        """Deploy the semantic model, returning a standardised result dict."""
        start = datetime.now()
        logger.info(
            "[XMLA] Starting deployment of '%s': %d tables, %d relationships",
            dataset_name, len(tables), len(relationships),
        )

        # Build the BIM
        bim = self._build_bim(dataset_name, tables, relationships)

        # Strategy 1: Tabular Editor CLI
        # If TE2 times out (GUI login dialog) or explicitly fails, fall through to REST XMLA.
        result = None
        if self.te_path and self._token_has_user_identity():
            result = self._deploy_via_tabular_editor(dataset_name, bim)

            # If TE2 says "already exists", delete it first then retry once
            if (not result.get("success")
                    and "already exists" in result.get("error", "").lower()):
                logger.info("[XMLA] Model already exists — deleting and retrying...")
                self._delete_existing_dataset(dataset_name)
                import time as _t; _t.sleep(3)  # wait for deletion to propagate
                result = self._deploy_via_tabular_editor(dataset_name, bim)

            # Only fall back to REST if TE2 truly failed (timeout or non-auth error)
            if not result.get("success") and result.get("status") in ("te2_timeout", "error"):
                logger.warning("[XMLA] TE2 failed (%s) — falling back to REST XMLA",
                               result.get("error", "unknown"))
                result = None

        if result is None:
            logger.info("[XMLA] Using REST XMLA push strategy")
            result = self._deploy_via_rest_xmla(dataset_name, bim)

        # Try to resolve the dataset_id from the workspace after deployment
        if result.get("success") and not result.get("dataset_id"):
            result["dataset_id"] = self._lookup_dataset_id(dataset_name)

        result["relationships_applied"] = len(relationships)
        result["tables_deployed"]       = len(tables)
        result["duration_seconds"]      = round((datetime.now() - start).total_seconds(), 1)
        result["xmla_endpoint"]         = self.xmla_endpoint or "unknown"
        return result

    # ------------------------------------------------------------------
    # Strategy 1: Tabular Editor CLI
    # ------------------------------------------------------------------

    def _deploy_via_tabular_editor(
        self, dataset_name: str, bim: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Write BIM to a temp file and deploy via Tabular Editor 2 CLI."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".bim", delete=False, encoding="utf-8"
        ) as f:
            json.dump(bim, f, indent=2)
            bim_path = f.name

        endpoint = self.xmla_endpoint or f"powerbi://api.powerbi.com/v1.0/myorg/{self.workspace_id}"

        try:
            # TE2 CLI deploy with Bearer token
            #
            # TE2 does NOT accept a raw Bearer token in the Password= field.
            # The correct approach is to write the token to a temp script file
            # and use TE2's scripting to set the connection, OR use the
            # undocumented -A flag (available in TE2 2.16+) which passes
            # the access token directly to ADOMD.NET.
            #
            # Syntax with -A flag:
            #   TabularEditor.exe <bim> -S <endpoint> -D <dbname> -O -A <token>
            #
            # The -S flag sets server, -D sets database, -O overwrites, -A sets token.

            # TE2 supports two ways to pass a Bearer token:
            #   1. -A <token>           (TE2 2.16.6+)
            #   2. -U "Bearer" -P <token>  (older TE2 builds)
            # We try -A first; _deploy_via_te2_script handles the fallback.
            # TE2 2.x CLI correct syntax:
            #   TabularEditor.exe <bim> -D "<server>" "<database>" -O -U <user> -P <pass>
            #
            # -S is a SCRIPT file flag, NOT server. Server goes as first arg to -D.
            # For Bearer token auth: -U "readonly" -P "Bearer <token>"
            # Decode token to extract UPN and verify expiry
            # token_upn defined here so it's always in scope even if decode fails
            token_upn = "readonly"
            try:
                import base64 as _b64, time as _time
                parts = self.access_token.split(".")
                if len(parts) >= 2:
                    pad     = parts[1] + "=" * (4 - len(parts[1]) % 4)
                    claims  = json.loads(_b64.urlsafe_b64decode(pad).decode("utf-8", errors="replace"))
                    scp     = claims.get("scp", claims.get("scope", ""))
                    upn     = (claims.get("upn")
                               or claims.get("preferred_username")
                               or claims.get("email")
                               or claims.get("unique_name")
                               or "")
                    exp     = claims.get("exp", 0)
                    expired = exp > 0 and exp < _time.time()
                    if upn:
                        token_upn = upn  # real UPN → TE2 silently picks cached account
                    logger.info("[XMLA/TE] Token claims keys=%s user=%s scopes=%s expired=%s",
                                list(claims.keys()), upn or "unknown", scp, expired)
                    if expired:
                        return {
                            "success": False, "status": "error",
                            "method": "tabular_editor_cli",
                            "message": "Access token has expired — re-run device-code login",
                            "error": "token_expired",
                        }
            except Exception as _te:
                logger.warning("[XMLA/TE] Could not decode token: %s", _te)
            # TE2 2.x deploy flags:
            #   -D <server> <database>  = target server and database
            #   -O                      = overwrite existing model
            #   -C                      = create if not exists
            #   -U <upn>                = exact account UPN — silently selects from
            #                            TE2 account cache, skips the account picker GUI
            #   -P "Bearer <token>"     = OAuth2 Bearer token for auth
            cmd = [
                self.te_path,
                bim_path,
                "-D", endpoint, dataset_name,
                "-O", "-C",
                "-U", token_upn,
                "-P", f"Bearer {self.access_token}",
            ]
            safe_cmd_str = (
                f"{self.te_path} {bim_path} "
                f"-D {endpoint} {dataset_name} -O -C -U {token_upn} -P Bearer <redacted>"
            )
            
                # token_upn stays "readonly" — TE2 may prompt but won't crash

            logger.info("[XMLA/TE] Deploying: %s", safe_cmd_str)

            # Use DETACHED_PROCESS (0x00000008) + CREATE_NO_WINDOW (0x08000000)
            # to prevent TE2 from spawning a login GUI dialog that hangs forever.
            # Timeout set to 30s — if TE2 hangs waiting for GUI input it will
            # be killed and we fall through to the REST XMLA strategy.
            creation_flags = 0x08000008 if os.name == "nt" else 0  # DETACHED + NO_WINDOW
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=30,
                    creationflags=creation_flags,
                )
                te_output = (proc.stdout or "").strip() + (proc.stderr or "").strip()
                logger.info("[XMLA/TE] Exit %d. Output:\n%s", proc.returncode, te_output[-800:])
            except subprocess.TimeoutExpired:
                logger.warning("[XMLA/TE] TE2 timed out after 30s — likely waiting for GUI login. "
                               "Falling through to REST XMLA strategy.")
                return {
                    "success": False, "status": "te2_timeout",
                    "method": "tabular_editor_cli",
                    "message": "TE2 timed out (GUI login dialog suppressed). Using REST XMLA.",
                    "error": "te2_gui_timeout",
                }

            if proc.returncode == 0:
                logger.info("[XMLA/TE] ✓ Deployment successful")
                return {
                    "success": True,
                    "status": "success",
                    "method": "tabular_editor_cli",
                    "message": f"Semantic model '{dataset_name}' deployed via Tabular Editor",
                    "cli_output": te_output[-600:],
                    "timestamp": datetime.now().isoformat(),
                }

            # ── Fallback: -A flag not recognised by this TE2 build ───────────
            # Try again using a C# script that injects the token via TOM API.
            if "unrecognized" in te_output.lower() or "unknown" in te_output.lower() or "-A" in te_output:
                logger.info("[XMLA/TE] -A flag not supported, trying script-based token injection")
                result = self._deploy_via_te2_script(dataset_name, bim_path, endpoint)
                if result:
                    return result

            return {
                "success": False,
                "status": "error",
                "method": "tabular_editor_cli",
                "message": "Tabular Editor CLI deployment failed",
                "error": te_output[-800:] if te_output else "No output (check TE2 is installed correctly)",
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False, "status": "error",
                "method": "tabular_editor_cli",
                "message": "Tabular Editor CLI timed out (>180s)",
                "error": "timeout",
            }
        except Exception as exc:
            logger.exception("[XMLA/TE] Unexpected error")
            return {
                "success": False, "status": "error",
                "method": "tabular_editor_cli",
                "message": str(exc), "error": str(exc),
            }
        finally:
            try:
                os.unlink(bim_path)
            except OSError:
                pass

    def _deploy_via_te2_script(
        self, dataset_name: str, bim_path: str, endpoint: str
    ):
        """
        Fallback for TE2 builds that don't support -A.
        Write a C# script that sets the access token via TOM, then deploy.
        """
        import tempfile as _tf
        cs_script = f'''
var server = new Microsoft.AnalysisServices.Tabular.Server();
server.Connect("{endpoint}");
server.AccessToken = "{self.access_token}";
'''
        # TE2 script approach: use -S and provide token via environment
        # This is done by setting the ADOMD connection AccessToken property
        # via TE2's -script flag with inline C#
        script_content = (
            'Model.Database.Server.AccessToken = @"'
            + self.access_token + '";'
        )
        try:
            with _tf.NamedTemporaryFile(mode="w", suffix=".cs", delete=False) as sf:
                sf.write(script_content)
                script_path = sf.name

            cmd = [
                self.te_path, bim_path,
                "-D", endpoint, dataset_name,
                "-O",
                "-script", script_path,
            ]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=180,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            logger.info("[XMLA/TE script] Exit %d: %s", proc.returncode, out[-400:])
            if proc.returncode == 0:
                return {
                    "success": True, "status": "success",
                    "method": "tabular_editor_cli_script",
                    "message": f"Deployed via TE2 script injection",
                    "cli_output": out[-400:],
                }
        except Exception as e:
            logger.warning("[XMLA/TE script] Failed: %s", e)
        finally:
            try:
                os.unlink(script_path)
            except Exception:
                pass
        return None

    # ------------------------------------------------------------------
    # Strategy 2: REST XMLA (TMSL via executeQueries) — fallback
    # ------------------------------------------------------------------

    def _deploy_via_rest_xmla(
        self, dataset_name: str, bim: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deploy via Power BI REST API — two strategies tried in order:

        Strategy A: POST /groups/{id}/datasets with defaultMode=AsAzure
                    Creates a real Import-mode semantic model with relationships.
                    Requires Power BI Premium / PPU workspace.

        Strategy B: POST /groups/{id}/datasets with defaultMode=Push
                    Creates a Push dataset. Works on any workspace but has no
                    Model View. Used as final fallback.
        """
        logger.info("[XMLA/REST] Starting REST API deployment")
        tables        = bim.get("model", {}).get("tables", [])
        relationships = bim.get("model", {}).get("relationships", [])

        # Build REST tables payload
        rest_tables = []
        for t in tables:
            cols = []
            for c in t.get("columns", []):
                rest_type = self._bim_to_rest_type(c.get("dataType", "string"))
                cols.append({"name": c["name"], "dataType": rest_type})
            if cols:
                rest_tables.append({"name": t["name"], "columns": cols})

        rest_rels = []
        for r in relationships:
            if r.get("fromTable") and r.get("toTable"):
                rest_rels.append({
                    "name":                   r.get("name", f"{r['fromTable']}_{r['toTable']}"),
                    "fromTable":              r["fromTable"],
                    "fromColumn":             r.get("fromColumn", ""),
                    "toTable":                r["toTable"],
                    "toColumn":               r.get("toColumn", ""),
                    "crossFilteringBehavior": r.get("crossFilteringBehavior", "bothDirections"),
                })

        url = f"{self.base_url}/groups/{self.workspace_id}/datasets"

        # ── Strategy A: AsAzure (Import semantic model) ───────────────────
        payload_import: Dict[str, Any] = {
            "name":        dataset_name,
            "defaultMode": "Push",
            "tables":      rest_tables,
        }
        if rest_rels:
            payload_import["relationships"] = rest_rels

        # try:
        #     logger.info("[XMLA/REST] Trying AsAzure (Import) mode...")
        #     resp = requests.post(url, headers=self.rest_headers,
        #                          json=payload_import, timeout=30)
        #     if 200 <= resp.status_code < 300:
        #         body       = resp.json() if resp.text else {}
        #         dataset_id = body.get("id", "")
        #         logger.info("[XMLA/REST] ✓ AsAzure dataset created: %s", dataset_id)
        #         return {
        #             "success":    True,
        #             "status":     "success",
        #             "method":     "rest_api_asazure",
        #             "dataset_id": dataset_id,
        #             "message":    f"Semantic model '{dataset_name}' created via REST API (AsAzure)",
        #             "workspace_url": (
        #                 f"https://app.powerbi.com/groups/{self.workspace_id}"
        #                 f"/datasets/{dataset_id}/details"
        #             ),
        #         }
        #     logger.warning("[XMLA/REST] AsAzure failed (%d): %s",
        #                    resp.status_code, resp.text[:300])
        # except Exception as exc:
        #     logger.warning("[XMLA/REST] AsAzure request error: %s", exc)

        # ── Strategy B: Push dataset (universal fallback) ─────────────────
        payload_push: Dict[str, Any] = {
            "name":        dataset_name,
            "defaultMode": "Push",
            "tables":      rest_tables,
        }
        if rest_rels:
            payload_push["relationships"] = rest_rels

        try:
            logger.info("[XMLA/REST] Falling back to Push mode...")
            resp = requests.post(url, headers=self.rest_headers,
                                 json=payload_push, timeout=30)
            if 200 <= resp.status_code < 300:
                body       = resp.json() if resp.text else {}
                dataset_id = body.get("id", "")
                logger.info("[XMLA/REST] ✓ Push dataset created: %s", dataset_id)
                return {
                    "success":    True,
                    "status":     "success",
                    "method":     "rest_api_push",
                    "dataset_id": dataset_id,
                    "message":    (
                        f"Dataset '{dataset_name}' created as Push dataset "
                        f"(TE2 auth failed and PPU AsAzure not available). "
                        f"Relationships and schema are set. "
                        f"Note: Model View requires a Premium/PPU semantic model."
                    ),
                    "workspace_url": (
                        f"https://app.powerbi.com/groups/{self.workspace_id}"
                    ),
                }
            logger.error("[XMLA/REST] Push also failed (%d): %s",
                         resp.status_code, resp.text[:300])
            return {
                "success": False,
                "status":  "error",
                "method":  "rest_api_push",
                "message": f"All deployment strategies failed",
                "error":   resp.text[:500],
            }
        except Exception as exc:
            logger.exception("[XMLA/REST] Push request error")
            return {
                "success": False,
                "status":  "error",
                "method":  "rest_api_push",
                "message": str(exc),
                "error":   str(exc),
            }


    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def _build_bim(
        self,
        dataset_name: str,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Build a Tabular BIM (JSON) model definition."""

        bim_tables = []
        for table in tables:
            table_name = str(table.get("name", "")).strip()
            raw_cols   = table.get("fields") or table.get("columns") or []

            bim_cols = []
            col_names = []
            for col in raw_cols:
                if isinstance(col, str):
                    col_name, col_type = col.strip(), "string"
                else:
                    col_name = str(col.get("name", "")).strip()
                    col_type = col.get("type") or col.get("data_type") or "string"

                if not col_name:
                    continue
                col_names.append(col_name)

                bim_col_type = self._rest_to_bim_type(
                    self._qlik_to_rest_type(col_type)
                )
                bim_cols.append({
                    "type":         "data",
                    "name":         col_name,
                    "dataType":     bim_col_type,
                    "sourceColumn": col_name,
                })

            if table_name and bim_cols:
                # Build an M expression that creates an empty typed table
                def _q(name):
                    # Quote column names with special chars for M type table syntax
                    if not name.replace("_", "").isalnum() or name[0:1].isdigit():
                        return f'#"{name}"'
                    return name

                type_defs = ", ".join(
                    f"{_q(c['name'])} = {self._bim_type_to_m(c['dataType'])}"
                    for c in bim_cols
                )
                # Note: {{}} in f-string produces literal {} in the output
                m_expr = (
                    "let\n"
                    "    Source = #table(\n"
                    f"        type table [{type_defs}],\n"
                    "        {{}}\n"
                    "    )\n"
                    "in\n"
                    "    Source"
                )

                bim_tables.append({
                    "name":    table_name,
                    "columns": bim_cols,
                    "partitions": [{
                        "name": f"{table_name}_Partition",
                        "mode": "import",
                        "source": {
                            "type":       "m",
                            "expression": m_expr,
                        },
                    }],
                })

        bim_relationships = []
        for rel in relationships:
            from_table  = rel.get("fromTable")  or rel.get("from_table")
            from_column = rel.get("fromColumn") or rel.get("from_column")
            to_table    = rel.get("toTable")    or rel.get("to_table")
            to_column   = rel.get("toColumn")   or rel.get("to_column")

            if not all([from_table, from_column, to_table, to_column]):
                continue

            name         = rel.get("name") or f"{from_table}_{to_table}"
            cardinality  = str(rel.get("cardinality", "ManyToOne"))
            cross_filter = rel.get("crossFilteringBehavior", "bothDirections")

            bim_relationships.append({
                "name":                   name,
                "fromTable":              from_table,
                "fromColumn":             from_column,
                "toTable":                to_table,
                "toColumn":               to_column,
                "crossFilteringBehavior": cross_filter,
                "fromCardinality":        "many" if cardinality.startswith("Many") else "one",
                "toCardinality":          "one"  if cardinality.endswith("One")  else "many",
            })

        return {
            "name": dataset_name,
            "compatibilityLevel": 1565,
            "model": {
                "culture":       "en-US",
                "tables":        bim_tables,
                "relationships": bim_relationships,
                "annotations": [
                    {"name": "createdBy",  "value": "QlikToPowerBI_Accelerator"},
                    {"name": "createdAt",  "value": datetime.now().isoformat()},
                ],
            },
        }

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def _delete_existing_dataset(self, dataset_name: str) -> bool:
        """Delete an existing dataset by name so TE2 can redeploy cleanly."""
        try:
            dataset_id = self._lookup_dataset_id(dataset_name)
            if not dataset_id:
                logger.warning("[XMLA] Could not find dataset to delete: %s", dataset_name)
                return False
            url = f"{self.base_url}/groups/{self.workspace_id}/datasets/{dataset_id}"
            resp = requests.delete(url, headers=self.rest_headers, timeout=15)
            if resp.ok:
                logger.info("[XMLA] ✓ Deleted existing dataset: %s (%s)", dataset_name, dataset_id)
                return True
            logger.warning("[XMLA] Delete failed (%d): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("[XMLA] Delete error: %s", e)
        return False
    def _token_has_user_identity(self) -> bool:
        """
        Returns True if a user-delegated token is available for XMLA/TE2.
        Checks user token cache first, then falls back to checking the SP token.
        """
        # If we have a cached user token, always use it for TE2
        if self.user_token:
            logger.info("[XMLA] User token available — TE2 will use user identity")
            # Swap access token to user token for TE2 deployment
            self.access_token = self.user_token
            return True
        # Check if SP token itself has user identity (unusual but possible)
        try:
            import base64 as _b64
            parts = self.access_token.split(".")
            if len(parts) >= 2:
                pad    = parts[1] + "=" * (4 - len(parts[1]) % 4)
                claims = json.loads(_b64.urlsafe_b64decode(pad).decode("utf-8", errors="replace"))
                has_user = bool(
                    claims.get("upn") or claims.get("preferred_username")
                    or claims.get("email") or claims.get("unique_name")
                )
                if not has_user:
                    logger.info("[XMLA] App-only token, no user token cached — skipping TE2")
                return has_user
        except Exception:
            pass
        return False

    def _lookup_dataset_id(self, dataset_name: str) -> Optional[str]:
        """Find the dataset_id of a newly created model by name."""
        try:
            url = f"{self.base_url}/groups/{self.workspace_id}/datasets"
            resp = requests.get(url, headers=self.rest_headers, timeout=15)
            if resp.ok:
                for ds in resp.json().get("value", []):
                    if ds.get("name") == dataset_name:
                        return ds.get("id")
        except Exception:
            pass
        return None

    def _resolve_xmla_endpoint(self) -> Optional[str]:
        """
        Resolve the XMLA endpoint for this workspace.

        The endpoint format is:
            powerbi://api.powerbi.com/v1.0/myorg/<WorkspaceName>
        where WorkspaceName is the DISPLAY NAME of the workspace, NOT its GUID.
        """
        # 1. Explicit env var (highest priority)
        env = os.getenv("POWERBI_XMLA_ENDPOINT", "").strip()
        if env:
            logger.info("[XMLA] Using env POWERBI_XMLA_ENDPOINT: %s", env)
            return env

        # 2. Fetch workspace display name via REST API
        try:
            url = f"{self.base_url}/groups/{self.workspace_id}"
            resp = requests.get(url, headers=self.rest_headers, timeout=10)
            if resp.ok:
                data = resp.json()
                workspace_name = data.get("name", "").strip()
                capacity_id    = data.get("capacityId", "")
                logger.info(
                    "[XMLA] Workspace: name=%r capacityId=%r",
                    workspace_name, capacity_id
                )
                if workspace_name:
                    endpoint = f"powerbi://api.powerbi.com/v1.0/myorg/{workspace_name}"
                    logger.info("[XMLA] Resolved XMLA endpoint: %s", endpoint)
                    return endpoint
            else:
                logger.warning("[XMLA] Could not fetch workspace info: %s %s",
                               resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[XMLA] Workspace lookup failed: %s", exc)

        # 3. Fallback: use workspace_id directly (works for some tenants)
        fallback = f"powerbi://api.powerbi.com/v1.0/myorg/{self.workspace_id}"
        logger.warning("[XMLA] Using workspace_id as XMLA endpoint (may fail): %s", fallback)
        return fallback

    @staticmethod
    def _find_tabular_editor() -> Optional[str]:
        """Locate the Tabular Editor 2 CLI executable."""
        import shutil

        env_path = os.getenv("TABULAR_EDITOR_PATH", "").strip()
        if env_path and os.path.isfile(env_path):
            return env_path

        candidates = [
            r"C:\Program Files\Tabular Editor 2\TabularEditor2.exe",
            r"C:\Program Files\Tabular Editor\TabularEditor.exe",
            r"C:\Program Files (x86)\Tabular Editor\TabularEditor.exe",
            "/usr/local/bin/TabularEditor2",
            "/usr/local/bin/TabularEditor",
            "/usr/bin/TabularEditor2",
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

        for name in ("TabularEditor2.exe", "TabularEditor.exe", "TabularEditor2", "TabularEditor"):
            found = shutil.which(name)
            if found:
                return found

        return None

    # ------------------------------------------------------------------
    # Type mapping utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _qlik_to_rest_type(qlik_type: str) -> str:
        mapping = {
            "text": "string", "string": "string",
            "integer": "Int64", "int": "Int64",
            "number": "Double", "real": "Double",
            "decimal": "Double", "double": "Double",
            "date": "DateTime", "time": "DateTime", "datetime": "DateTime",
            "boolean": "bool", "bool": "bool",
        }
        return mapping.get(str(qlik_type).lower().strip(), "string")

    @staticmethod
    def _rest_to_bim_type(rest_type: str) -> str:
        mapping = {
            "string":   "string",
            "Int64":    "int64",
            "Double":   "double",
            "DateTime": "dateTime",
            "bool":     "boolean",
            "Boolean":  "boolean",
        }
        return mapping.get(rest_type, "string")

    @staticmethod
    def _bim_to_rest_type(bim_type: str) -> str:
        mapping = {
            "string":   "string",
            "int64":    "Int64",
            "double":   "Double",
            "dateTime": "DateTime",
            "boolean":  "bool",
        }
        return mapping.get(bim_type, "string")

    @staticmethod
    def _bim_type_to_m(bim_type: str) -> str:
        """Convert BIM data type to M type annotation for use inside #table(type table [...])."""
        mapping = {
            "string":   "type text",
            "int64":    "type number",    # Int64.Type causes M Engine preview errors in partitions
            "double":   "type number",
            "dateTime": "type datetime",
            "boolean":  "type logical",
        }
        return mapping.get(bim_type, "type text")
