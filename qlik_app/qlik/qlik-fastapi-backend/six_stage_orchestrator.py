"""
⚠️  DEPRECATED - Old 6-stage orchestrator no longer used

Replaced by: migration_api.py
Reason: Simplified, streamlined API-based approach (more efficient)

Status: PRESERVED FOR REFERENCE (not used in active pipeline)
To reactivate: Remove this notice and uncomment imports below
"""

# ============================================================================
# ORIGINAL CODE BELOW (DEPRECATED)
# ============================================================================

"""
Complete 6-stage orchestrator for Qlik-to-Power BI migration.

Publish modes:
- cloud_push: create Power BI Push semantic model via REST API
- desktop_cloud: generate Desktop handoff bundle for PBIX publish to cloud
- xmla_semantic: create enhanced semantic model directly in cloud via XMLA
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ❌ DISABLED - Use migration_api.py instead
# from stage1_qlik_extractor import QlikMetadataExtractor
# from stage2_relationship_inference import RelationshipInferenceEngine
# from stage3_relationship_normalizer import RelationshipNormalizer
# from stage45_tabular_editor import PowerBIDatasetCreator, PowerBIRelationshipManager
from stage6_er_diagram import ERDiagramGenerator
from powerbi_xmla_connector import create_semantic_model_via_xmla

logger = logging.getLogger(__name__)


class SixStageOrchestrator:
    """Run extract -> infer -> normalize -> publish -> relationships -> diagram."""

    SUPPORTED_MODES = {"cloud_push", "desktop_cloud", "xmla_semantic"}

    def __init__(self):
        self.extractor = QlikMetadataExtractor()
        self.inference_engine = RelationshipInferenceEngine()
        self.normalizer = RelationshipNormalizer()
        self.er_generator = ERDiagramGenerator()

    def execute_pipeline(
        self,
        app_id: str,
        dataset_name: str,
        workspace_id: str,
        access_token: Optional[str] = None,
        publish_mode: str = "cloud_push",
        csv_table_payloads: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        start_time = datetime.now()
        mode = (publish_mode or "cloud_push").strip().lower()

        result: Dict[str, Any] = {
            "success": False,
            "pipeline_start": start_time.isoformat(),
            "app_id": app_id,
            "dataset_name": dataset_name,
            "workspace_id": workspace_id,
            "publish_mode": mode,
            "stages": {},
            "warnings": [],
        }

        if mode not in self.SUPPORTED_MODES:
            result["error"] = (
                f"Invalid publish_mode '{publish_mode}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_MODES))}"
            )
            return result

        try:
            logger.info("%s", "=" * 70)
            logger.info("Starting 6-stage migration pipeline (mode=%s)", mode)
            logger.info("%s", "=" * 70)

            # Stage 1: Extract
            stage1_result = self._stage_1_extract(app_id)
            result["stages"]["1_extract"] = stage1_result
            if not stage1_result.get("success"):
                stage_error = stage1_result.get("error") or "unknown error"
                result["error"] = f"Stage 1 (extract) failed: {stage_error}"
                return result

            tables = stage1_result.get("tables", [])

            # Stage 2: Infer relationships
            stage2_result = self._stage_2_infer(tables)
            result["stages"]["2_infer"] = stage2_result
            if not stage2_result.get("success"):
                stage_error = stage2_result.get("error") or "unknown error"
                result["error"] = f"Stage 2 (infer) failed: {stage_error}"
                return result

            inferred_relationships = stage2_result.get("relationships", [])

            # Stage 3: Normalize relationships
            stage3_result = self._stage_3_normalize(tables, inferred_relationships)
            result["stages"]["3_normalize"] = stage3_result
            if not stage3_result.get("success"):
                stage_error = stage3_result.get("error") or "unknown error"
                result["error"] = f"Stage 3 (normalize) failed: {stage_error}"
                return result

            normalized_relationships = stage3_result.get("relationships", [])

            # Stage 4 + 5 depend on mode
            dataset_id = None
            if mode == "cloud_push":
                if access_token:
                    stage4_result = self._stage_4_rest_write(
                        dataset_name=dataset_name,
                        tables=tables,
                        workspace_id=workspace_id,
                        access_token=access_token,
                    )
                else:
                    stage4_result = {
                        "success": False,
                        "status": "skipped",
                        "message": "No access token provided",
                    }
                result["stages"]["4_rest_write"] = stage4_result
                dataset_id = stage4_result.get("dataset_id")

                if access_token and dataset_id and normalized_relationships:
                    stage5_result = self._stage_5_rest_relationships(
                        workspace_id=workspace_id,
                        dataset_id=dataset_id,
                        relationships=normalized_relationships,
                        access_token=access_token,
                    )
                elif access_token and dataset_id and not normalized_relationships:
                    stage5_result = {
                        "success": True,
                        "status": "success",
                        "relationships_created": 0,
                        "message": "No relationships inferred; nothing to create",
                    }
                else:
                    stage5_result = {
                        "success": False,
                        "status": "skipped",
                        "relationships_created": 0,
                        "message": "Skipped (missing access token or dataset_id)",
                    }
                result["stages"]["5_rest_relationships"] = stage5_result

                if dataset_id:
                    result["warnings"].append(
                        "Push semantic models may not allow 'Open semantic model' in service. "
                        "Use publish_mode=desktop_cloud for Desktop->Cloud workflow."
                    )

            elif mode == "xmla_semantic":
                stage4_xmla_result = self._stage_4_xmla_semantic(
                    dataset_name=dataset_name,
                    tables=tables,
                    relationships=normalized_relationships,
                    workspace_id=workspace_id,
                    access_token=access_token,
                    csv_table_payloads=csv_table_payloads,
                )
                result["stages"]["4_xmla_semantic"] = stage4_xmla_result
                result["stages"]["4_rest_write"] = {
                    "success": True,
                    "status": "skipped",
                    "message": "Skipped in xmla_semantic mode",
                }
                result["stages"]["5_rest_relationships"] = {
                    "success": True,
                    "status": "skipped",
                    "relationships_created": 0,
                    "message": "Relationships are embedded in XMLA semantic model create",
                }
                dataset_id = stage4_xmla_result.get("dataset_id")

            else:
                # desktop_cloud mode
                result["stages"]["4_rest_write"] = {
                    "success": True,
                    "status": "skipped",
                    "message": "Skipped in desktop_cloud mode",
                }
                result["stages"]["5_rest_relationships"] = {
                    "success": True,
                    "status": "skipped",
                    "relationships_created": 0,
                    "message": "Skipped in desktop_cloud mode",
                }

                stage7_bundle = self._stage_7_desktop_bundle(
                    dataset_name=dataset_name,
                    workspace_id=workspace_id,
                    tables=tables,
                    relationships=normalized_relationships,
                )
                result["stages"]["7_desktop_bundle"] = stage7_bundle

            # Stage 6: Generate ER diagram
            stage6_result = self._stage_6_er_diagram(tables, normalized_relationships)
            result["stages"]["6_er_diagram"] = stage6_result

            # Final status
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            base_ok = bool(
                stage1_result.get("success")
                and stage2_result.get("success")
                and stage3_result.get("success")
                and stage6_result.get("success")
            )

            if mode == "cloud_push":
                stage4_ok = result["stages"]["4_rest_write"].get("success", False)
                stage5_ok = result["stages"]["5_rest_relationships"].get("success", False)
                publish_ok = bool(stage4_ok and stage5_ok)
                pipeline_success = base_ok and publish_ok
            elif mode == "xmla_semantic":
                stage4_ok = result["stages"].get("4_xmla_semantic", {}).get("success", False)
                pipeline_success = base_ok and bool(stage4_ok)
            else:
                bundle_ok = result["stages"].get("7_desktop_bundle", {}).get("success", False)
                pipeline_success = base_ok and bundle_ok

            result["success"] = pipeline_success
            result["pipeline_end"] = end_time.isoformat()
            result["duration_seconds"] = duration
            result["summary"] = {
                "mode": mode,
                "tables": len(tables),
                "inferred_relationships": len(inferred_relationships),
                "normalized_relationships": len(normalized_relationships),
                "dataset_created": bool(dataset_id),
                "relationships_created": (
                    result["stages"].get("5_rest_relationships", {}).get("relationships_created", 0)
                    if mode != "xmla_semantic"
                    else result["stages"].get("4_xmla_semantic", {}).get("relationships_applied", 0)
                ),
                "desktop_bundle_created": bool(
                    result["stages"].get("7_desktop_bundle", {}).get("success", False)
                ),
            }

            return result

        except Exception as exc:
            logger.exception("Pipeline failed")
            result["error"] = str(exc)
            return result

    def _stage_1_extract(self, app_id: str) -> Dict[str, Any]:
        return self.extractor.extract_metadata(app_id)

    def _stage_2_infer(self, tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self.inference_engine.infer_relationships(tables)

    def _stage_3_normalize(
        self,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self.normalizer.normalize_relationships(tables, relationships)

    def _stage_4_rest_write(
        self,
        dataset_name: str,
        tables: List[Dict[str, Any]],
        workspace_id: str,
        access_token: str,
    ) -> Dict[str, Any]:
        creator = PowerBIDatasetCreator(workspace_id=workspace_id, access_token=access_token)
        return creator.create_dataset(dataset_name=dataset_name, tables=tables)

    def _stage_4_xmla_semantic(
        self,
        dataset_name: str,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
        workspace_id: str,
        access_token: Optional[str],
        csv_table_payloads: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        return create_semantic_model_via_xmla(
            dataset_name=dataset_name,
            workspace_id=workspace_id,
            qlik_tables=tables,
            normalized_relationships=relationships,
            csv_table_payloads=csv_table_payloads or {},
            access_token=access_token,
        )

    def _stage_5_rest_relationships(
        self,
        workspace_id: str,
        dataset_id: str,
        relationships: List[Dict[str, Any]],
        access_token: str,
    ) -> Dict[str, Any]:
        manager = PowerBIRelationshipManager(
            workspace_id=workspace_id,
            dataset_id=dataset_id,
            access_token=access_token,
        )
        return manager.create_relationships(relationships)

    def _stage_6_er_diagram(
        self,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = self.er_generator.generate_mermaid_diagram(tables, relationships)
        if result.get("success"):
            result["html"] = self.er_generator.generate_html_diagram(
                result.get("mermaid", ""),
                title="Data Model - Entity Relationship Diagram",
            )
        return result

    def _stage_7_desktop_bundle(
        self,
        dataset_name: str,
        workspace_id: str,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate a Desktop handoff package.

        This package is intended for the workflow:
        Power BI Desktop -> Publish -> Power BI Service (semantic model open enabled)
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", dataset_name).strip("_") or "model"

            bundle_dir = (
                Path(__file__).resolve().parent
                / "desktop_bundles"
                / f"{safe_name}_{timestamp}"
            )
            bundle_dir.mkdir(parents=True, exist_ok=True)

            schema_path = bundle_dir / "tables_schema.json"
            rels_path = bundle_dir / "relationships_normalized.json"
            mermaid_path = bundle_dir / "er_diagram.mmd"
            readme_path = bundle_dir / "README.md"

            schema_path.write_text(json.dumps({"tables": tables}, indent=2), encoding="utf-8")
            rels_path.write_text(
                json.dumps({"relationships": relationships}, indent=2), encoding="utf-8"
            )
            mermaid = self.er_generator.generate_mermaid_diagram(tables, relationships).get(
                "mermaid", ""
            )
            mermaid_path.write_text(mermaid, encoding="utf-8")

            readme = f"""# Desktop + Cloud Publish Bundle

Generated: {datetime.now().isoformat()}
Dataset name: {dataset_name}
Target workspace: {workspace_id}

## Files
- `tables_schema.json`: table/field metadata extracted from Qlik
- `relationships_normalized.json`: inferred normalized relationships
- `er_diagram.mmd`: Mermaid ER diagram

## Publish steps
1. Open Power BI Desktop (latest version).
2. Build/import your data model using this schema.
3. Apply relationships from `relationships_normalized.json`.
4. Save PBIX and click Publish.
5. Publish to workspace `{workspace_id}`.
6. In Power BI Service, open the semantic model and Model view.

## Why this mode
This mode is for semantic models that must be fully editable in service.
Push datasets created by REST API can limit "Open semantic model".
"""
            readme_path.write_text(readme, encoding="utf-8")

            return {
                "success": True,
                "bundle_dir": str(bundle_dir),
                "files": {
                    "tables_schema": str(schema_path),
                    "relationships": str(rels_path),
                    "er_diagram": str(mermaid_path),
                    "readme": str(readme_path),
                },
            }
        except Exception as exc:
            logger.exception("Desktop bundle generation failed")
            return {"success": False, "error": str(exc)}


def run_migration_pipeline(
    app_id: str,
    dataset_name: str,
    workspace_id: str,
    access_token: Optional[str] = None,
    publish_mode: str = "cloud_push",
    csv_table_payloads: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    orchestrator = SixStageOrchestrator()
    return orchestrator.execute_pipeline(
        app_id=app_id,
        dataset_name=dataset_name,
        workspace_id=workspace_id,
        access_token=access_token,
        publish_mode=publish_mode,
        csv_table_payloads=csv_table_payloads,
    )
