"""
STAGE 4 & 5: Power BI REST API - Create Dataset & Relationships

Workflow:
1. Stage 4: Create dataset in Power BI via REST API
2. Stage 5: Create relationships in Power BI via REST API
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class PowerBIDatasetCreator:
    """Stage 4: Create dataset in Power BI via REST API."""

    def __init__(self, workspace_id: str, access_token: str):
        self.workspace_id = workspace_id
        self.access_token = access_token
        self.base_url = "https://api.powerbi.com/v1.0/myorg"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def create_dataset(
        self,
        dataset_name: str,
        tables: List[Dict[str, Any]],
        relationships: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create dataset in Power BI with table structure.

        Accepts table metadata from either:
        - stage1 format: table["fields"]
        - legacy format: table["columns"]
        """
        try:
            logger.info(
                "[STAGE 4] Creating dataset '%s' with %s tables", dataset_name, len(tables)
            )

            table_definitions: List[Dict[str, Any]] = []
            for table in tables:
                table_name = str(table.get("name", "")).strip()
                columns = self._extract_columns(table)

                if not table_name or not columns:
                    logger.warning(
                        "[STAGE 4] Skipping table with insufficient metadata: %s",
                        table_name or "unnamed",
                    )
                    continue

                table_definitions.append(
                    {
                        "name": table_name,
                        "columns": columns,
                    }
                )

            if not table_definitions:
                return {
                    "success": False,
                    "status": "error",
                    "message": "No valid tables/columns found in extracted metadata",
                    "error": "table_definitions_empty",
                }

            payload: Dict[str, Any] = {
                "name": dataset_name,
                "tables": table_definitions,
                "defaultMode": "Push",
            }

            if relationships:
                payload_relationships = []
                for rel in relationships:
                    normalized = PowerBIRelationshipManager.normalize_relationship(rel)
                    if normalized:
                        payload_relationships.append(
                            {
                                "name": normalized["name"],
                                "fromTable": normalized["fromTable"],
                                "fromColumn": normalized["fromColumn"],
                                "toTable": normalized["toTable"],
                                "toColumn": normalized["toColumn"],
                                "crossFilteringBehavior": normalized[
                                    "crossFilteringBehavior"
                                ],
                            }
                        )
                if payload_relationships:
                    payload["relationships"] = payload_relationships

            url = f"{self.base_url}/groups/{self.workspace_id}/datasets"
            logger.debug("Dataset create payload: %s", json.dumps(payload)[:2000])

            response = requests.post(url, headers=self.headers, json=payload, timeout=30)

            if response.status_code in (200, 201):
                result = response.json()
                dataset_id = result.get("id")
                logger.info("[STAGE 4] Dataset created successfully: %s", dataset_id)
                return {
                    "success": True,
                    "status": "success",
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "tables_created": len(table_definitions),
                    "message": f"Dataset '{dataset_name}' created",
                    "timestamp": datetime.now().isoformat(),
                }

            logger.error(
                "[STAGE 4] Failed to create dataset (%s): %s",
                response.status_code,
                response.text,
            )
            return {
                "success": False,
                "status": "error",
                "message": f"Failed to create dataset: HTTP {response.status_code}",
                "error": response.text,
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "status": "error",
                "message": "Request timeout",
                "error": "The request took too long. Try again.",
            }
        except requests.exceptions.RequestException as exc:
            return {
                "success": False,
                "status": "error",
                "message": "Request failed",
                "error": str(exc),
            }
        except Exception as exc:
            logger.exception("[STAGE 4] Unexpected error")
            return {
                "success": False,
                "status": "error",
                "message": "Unexpected error",
                "error": str(exc),
            }

    def _extract_columns(self, table: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_columns = table.get("columns") or table.get("fields") or []
        columns: List[Dict[str, Any]] = []
        seen = set()

        for col in raw_columns:
            if isinstance(col, str):
                col_name = col.strip()
                col_type = "String"
            else:
                col_name = str(col.get("name", "")).strip()
                col_type = col.get("data_type") or col.get("type") or "String"

            if not col_name or col_name in seen:
                continue

            seen.add(col_name)
            columns.append(
                {
                    "name": col_name,
                    "dataType": self._map_data_type(col_type),
                }
            )

        return columns

    def _map_data_type(self, qlik_type: str) -> str:
        mapping = {
            "text": "string",
            "string": "string",
            "integer": "Int64",
            "int": "Int64",
            "number": "Double",
            "real": "Double",
            "decimal": "Double",
            "double": "Double",
            "date": "DateTime",
            "time": "DateTime",
            "datetime": "DateTime",
            "timestamp": "DateTime",
            "boolean": "Boolean",
            "bool": "Boolean",
            "flag": "Boolean",
        }
        return mapping.get(str(qlik_type).lower().strip(), "string")


class PowerBIRelationshipManager:
    """Stage 5: Create relationships via Power BI REST API."""

    def __init__(self, workspace_id: str, dataset_id: str, access_token: str):
        self.workspace_id = workspace_id
        self.dataset_id = dataset_id
        self.access_token = access_token
        self.base_url = "https://api.powerbi.com/v1.0/myorg"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    def create_relationships(self, relationships: List[Dict[str, Any]]) -> Dict[str, Any]:
        logger.info(
            "[STAGE 5] Creating %s relationships via REST API", len(relationships)
        )

        created: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []

        for rel in relationships:
            rel_name = rel.get("name", "Unknown")
            normalized = self.normalize_relationship(rel)
            if not normalized:
                failed.append({"name": rel_name, "error": "Invalid relationship payload"})
                continue

            result = self._create_single_relationship(normalized)
            if result.get("success"):
                created.append(
                    {
                        "name": normalized["name"],
                        "status": "success",
                        "relationship_id": result.get("relationship_id"),
                    }
                )
            else:
                failed.append(
                    {
                        "name": normalized["name"],
                        "error": result.get("error", "Unknown error"),
                    }
                )

        if not failed:
            status = "success"
        elif not created:
            status = "error"
        else:
            status = "partial"

        return {
            "success": status in ("success", "partial"),
            "status": status,
            "created": created,
            "failed": failed,
            "relationships_created": len(created),
            "total_created": len(created),
            "total_failed": len(failed),
            "message": f"Created {len(created)} relationships"
            + (f", {len(failed)} failed" if failed else ""),
            "timestamp": datetime.now().isoformat(),
        }

    @staticmethod
    def normalize_relationship(relationship: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from_table = relationship.get("fromTable") or relationship.get("from_table")
        from_column = relationship.get("fromColumn") or relationship.get("from_column")
        to_table = relationship.get("toTable") or relationship.get("to_table")
        to_column = relationship.get("toColumn") or relationship.get("to_column")

        if not all([from_table, from_column, to_table, to_column]):
            return None

        name = relationship.get("name") or f"{from_table}_{to_table}_{from_column}"
        cardinality = str(relationship.get("cardinality", "ManyToOne"))
        cross_filter = relationship.get("crossFilteringBehavior") or relationship.get(
            "cross_filtering_behavior", "bothDirections"
        )

        if cardinality == "OneToMany":
            from_cardinality, to_cardinality = "one", "many"
        elif cardinality == "OneToOne":
            from_cardinality, to_cardinality = "one", "one"
        else:
            from_cardinality, to_cardinality = "many", "one"

        cross_filter_norm = str(cross_filter).lower()
        if cross_filter_norm in ("both", "bothdirections", "bi", "bidirectional"):
            cross_filter_norm = "bothDirections"
        elif cross_filter_norm == "onedirection":
            cross_filter_norm = "oneDirection"
        elif cross_filter_norm not in ("bothDirections", "oneDirection"):
            cross_filter_norm = "bothDirections"

        return {
            "name": name,
            "fromTable": from_table,
            "fromColumn": from_column,
            "toTable": to_table,
            "toColumn": to_column,
            "fromCardinality": from_cardinality,
            "toCardinality": to_cardinality,
            "crossFilteringBehavior": cross_filter_norm,
        }

    def _create_single_relationship(self, relationship: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a single relationship via REST API.

        Endpoint:
        POST /groups/{group_id}/datasets/{dataset_id}/relationships
        """
        try:
            url = (
                f"{self.base_url}/groups/{self.workspace_id}/datasets/{self.dataset_id}/"
                "relationships"
            )

            response = requests.post(url, headers=self.headers, json=relationship, timeout=30)

            if response.status_code in (200, 201):
                body = response.json() if response.text else {}
                return {"success": True, "relationship_id": body.get("id")}

            if response.status_code == 409:
                return {
                    "success": True,
                    "relationship_id": None,
                    "note": "Relationship already exists",
                }

            if response.status_code in (404, 405):
                return {
                    "success": False,
                    "error": "Relationship endpoint unavailable for this dataset/workspace",
                }

            return {
                "success": False,
                "error": f"API Error {response.status_code}: {response.text[:300]}",
            }

        except requests.exceptions.Timeout:
            return {"success": False, "error": "Request timeout"}
        except requests.exceptions.RequestException as exc:
            return {"success": False, "error": f"Request failed: {str(exc)}"}
        except Exception as exc:
            logger.exception("Relationship creation failed")
            return {"success": False, "error": f"Unexpected error: {str(exc)}"}


# Backward compatibility alias
TabularEditorManager = PowerBIRelationshipManager
