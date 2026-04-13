"""
STAGE 3: Normalized Relationship JSON

Creates consistent JSON format for Power BI:

{
  "relationships": [
    {
      "name": "Sales_Customer",
      "fromTable": "Sales",
      "fromColumn": "CustomerID",
      "toTable": "Customer",
      "toColumn": "CustomerID",
      "crossFilteringBehavior": "BothDirections",
      "cardinality": "ManyToOne"
    }
  ]
}

This is the contract between Python and Power BI
"""

import logging
from typing import Dict, List, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class RelationshipNormalizer:
    """Normalize relationships to standard Power BI format"""
    
    def normalize_relationships(
        self,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Normalize raw relationships to Power BI schema
        
        Input: Raw relationships from inference engine
        Output: Standard normalized JSON for Power BI
        """
        try:
            logger.info(f"[STAGE 3] Normalizing {len(relationships)} relationships")
            
            normalized = []
            
            for rel in relationships:
                normalized_rel = self._normalize_relationship(rel)
                if normalized_rel:
                    normalized.append(normalized_rel)
            
            # Validate no circular dependencies
            circular = self._detect_circular_dependencies(normalized)
            if circular:
                logger.warning(f"Circular dependencies detected: {circular}")
            
            logger.info(f"[STAGE 3] ✓ {len(normalized)} relationships normalized")
            
            return {
                "success": True,
                "relationships": normalized,
                "count": len(normalized),
                "format": "Power BI Normalized Schema",
                "circular_dependencies": circular,
                "validation": {
                    "all_fields_present": all(
                        r.get("name") and r.get("fromTable") and r.get("toTable")
                        for r in normalized
                    ),
                    "no_duplicates": len(normalized) == len(set(
                        (r["fromTable"], r["fromColumn"], r["toTable"], r["toColumn"])
                        for r in normalized
                    ))
                }
            }
            
        except Exception as e:
            logger.error(f"Normalization failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "relationships": []
            }
    
    def _normalize_relationship(self, raw_rel: Dict[str, Any]) -> Dict[str, Any]:
        """Convert raw relationship to normalized format"""
        try:
            # Generate relationship name
            name = self._generate_relationship_name(
                raw_rel.get("from_table"),
                raw_rel.get("to_table")
            )
            
            # Map cardinality
            cardinality = self._map_cardinality(raw_rel.get("cardinality", "Many:1"))
            
            normalized = {
                "name": name,
                "fromTable": raw_rel.get("from_table"),
                "fromColumn": raw_rel.get("from_column"),
                "toTable": raw_rel.get("to_table"),
                "toColumn": raw_rel.get("to_column"),
                # Keep snake_case aliases for compatibility with legacy callers
                "from_table": raw_rel.get("from_table"),
                "from_column": raw_rel.get("from_column"),
                "to_table": raw_rel.get("to_table"),
                "to_column": raw_rel.get("to_column"),
                "cardinality": cardinality,
                "crossFilteringBehavior": "bothDirections",
                "isActive": True,
                "relyOnReferentialIntegrity": False,
                "confidence": raw_rel.get("confidence", 0.75),
                "inferenceMethod": raw_rel.get("method", "unknown"),
                "timestamp": datetime.now().isoformat()
            }
            
            return normalized
            
        except Exception as e:
            logger.error(f"Failed to normalize relationship: {str(e)}")
            return None
    
    def _generate_relationship_name(self, from_table: str, to_table: str) -> str:
        """Generate relationship name"""
        from_clean = from_table.replace(" ", "").replace("-", "")
        to_clean = to_table.replace(" ", "").replace("-", "")
        return f"{from_clean}_{to_clean}"
    
    def _map_cardinality(self, cardinality: str) -> str:
        """Map inferred cardinality to Power BI format"""
        mapping = {
            "1:1": "OneToOne",
            "1:Many": "OneToMany",
            "Many:1": "ManyToOne",
            "*:1": "ManyToOne",
            "1:*": "OneToMany",
            "*:*": "ManyToOne"  # Default many-to-one
        }
        return mapping.get(cardinality, "ManyToOne")
    
    def _detect_circular_dependencies(self, relationships: List[Dict]) -> List[List[str]]:
        """
        Detect circular dependencies in relationships
        
        Returns: List of circular paths found
        """
        try:
            # Build adjacency graph
            graph = {}
            for rel in relationships:
                from_table = rel["fromTable"]
                to_table = rel["toTable"]
                
                if from_table not in graph:
                    graph[from_table] = []
                graph[from_table].append(to_table)
            
            # DFS to find cycles
            cycles = []
            visited = set()
            rec_stack = set()
            
            def dfs(node, path):
                visited.add(node)
                rec_stack.add(node)
                
                for neighbor in graph.get(node, []):
                    if neighbor not in visited:
                        dfs(neighbor, path + [neighbor])
                    elif neighbor in rec_stack:
                        cycle = path + [neighbor]
                        cycles.append(cycle)
                
                rec_stack.remove(node)
            
            for node in graph:
                if node not in visited:
                    dfs(node, [node])
            
            return cycles
            
        except Exception as e:
            logger.error(f"Failed to detect cycles: {str(e)}")
            return []
    
    def export_to_json(self, relationships: List[Dict]) -> str:
        """Export relationships as JSON string"""
        data = {
            "relationships": relationships,
            "exported_at": datetime.now().isoformat()
        }
        return json.dumps(data, indent=2)
    
    def validate_relationships(self, relationships: List[Dict]) -> Dict[str, Any]:
        """Validate relationship format"""
        required_fields = ["name", "fromTable", "fromColumn", "toTable", "toColumn"]
        
        valid = []
        invalid = []
        
        for rel in relationships:
            if all(rel.get(field) for field in required_fields):
                valid.append(rel)
            else:
                invalid.append(rel)
        
        return {
            "valid_count": len(valid),
            "invalid_count": len(invalid),
            "valid": valid,
            "invalid": invalid,
            "is_valid": len(invalid) == 0
        }
