"""
STAGE 2: Relationship Inference Engine (Python)

Infers:
- Primary keys
- Foreign keys
- Cardinality (1:*, *:1)
- Confidence scores
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
import re

logger = logging.getLogger(__name__)


class RelationshipInferenceEngine:
    """Infer relationships from table metadata"""
    
    def __init__(self):
        self.relationships = []
        self.confidence_threshold = 0.75
    
    def infer_relationships(self, tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Infer relationships between tables
        
        Strategy A: Name-based Matching
        - CustomerID in multiple tables
        - OrderID vs Order_Id
        
        Strategy B: Data Profiling
        - Distinct count comparison
        - Uniqueness check
        
        Returns all detected relationships with confidence scores
        """
        try:
            logger.info(f"[STAGE 2] Inferring relationships from {len(tables)} tables")
            
            relationships = []
            table_map = {t["name"]: t for t in tables}
            
            # Strategy A: Name-based matching
            for i, table1 in enumerate(tables):
                for table2 in tables[i+1:]:
                    matches = self._match_by_name(table1, table2, table_map)
                    relationships.extend(matches)
            
            # Remove duplicates
            unique_rels = self._deduplicate_relationships(relationships)
            
            # Calculate statistics
            confidence_scores = [r.get("confidence", 0) for r in unique_rels]
            avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0
            
            logger.info(f"[STAGE 2] ✓ Detected {len(unique_rels)} relationships (avg confidence: {avg_confidence:.2f})")
            
            return {
                "success": True,
                "relationships": unique_rels,
                "count": len(unique_rels),
                "avg_confidence": avg_confidence,
                "inference_methods": ["name_based_matching", "id_pattern_detection"]
            }
            
        except Exception as e:
            logger.error(f"Relationship inference failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "relationships": []
            }
    
    def _match_by_name(
        self, 
        table1: Dict, 
        table2: Dict, 
        table_map: Dict
    ) -> List[Dict[str, Any]]:
        """Match tables by field names"""
        matches = []
        
        for field1 in table1.get("fields", []):
            for field2 in table2.get("fields", []):
                match = self._compare_fields(
                    field1, field2,
                    table1.get("name", ""),
                    table2.get("name", "")
                )
                
                if match and match["confidence"] >= self.confidence_threshold:
                    matches.append(match)
        
        return matches
    
    def _compare_fields(
        self, 
        field1: Dict, 
        field2: Dict,
        table1_name: str,
        table2_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        Compare two fields for matching
        Returns match with confidence score if found
        """
        
        name1 = field1.get("name", "").lower().strip()
        name2 = field2.get("name", "").lower().strip()
        
        confidence = 0
        method = ""
        
        # Method 1: Exact match
        if name1 == name2:
            confidence = 0.95
            method = "exact_match"
        
        # Method 2: ID pattern matching
        elif (name1.endswith("id") or name1.endswith("_id")) and \
             (name2.endswith("id") or name2.endswith("_id")):
            
            # Check if one contains the other table name
            table1_prefix = table1_name.lower().replace(" ", "")
            table2_prefix = table2_name.lower().replace(" ", "")
            
            if (table1_prefix in name2 and name2.endswith("id")) or \
               (table2_prefix in name1 and name1.endswith("id")):
                confidence = 0.90
                method = "table_id_pattern"
            
            # Generic ID match
            elif name1 == name2:
                confidence = 0.85
                method = "common_id_pattern"
        
        # Method 3: Suffix matching (e.g., CustomerId vs CustomerId)
        elif name1.rstrip("id").rstrip("_") == name2.rstrip("id").rstrip("_"):
            confidence = 0.80
            method = "suffix_normalization"
        
        # Method 4: Common ID names
        common_ids = ["id", "identifier", "code", "key"]
        name1_id = any(name1.endswith(cid) for cid in common_ids)
        name2_id = any(name2.endswith(cid) for cid in common_ids)
        
        if name1_id and name2_id and name1 == name2:
            confidence = 0.75
            method = "common_id_pattern"
        
        if confidence > 0:
            return {
                "from_table": table1_name,
                "from_column": field1.get("name"),
                "from_type": field1.get("type"),
                "to_table": table2_name,
                "to_column": field2.get("name"),
                "to_type": field2.get("type"),
                "cardinality": self._infer_cardinality(field1, field2),
                "confidence": confidence,
                "method": method
            }
        
        return None
    
    def _infer_cardinality(self, field1: Dict, field2: Dict) -> str:
        """
        Infer cardinality based on field properties
        
        Returns: "1:1" or "1:Many" or "Many:1"
        """
        
        # If one field is marked as key, likely foreign key relationship
        is_key1 = field1.get("is_key", False)
        is_key2 = field2.get("is_key", False)
        
        if is_key1 and not is_key2:
            return "1:Many"  # Table1 is dimension (1), Table2 is fact (Many)
        elif is_key2 and not is_key1:
            return "Many:1"  # Table1 is fact (Many), Table2 is dimension (1)
        else:
            return "Many:1"  # Default to Many:1
    
    def _deduplicate_relationships(self, relationships: List[Dict]) -> List[Dict]:
        """Remove duplicate relationships"""
        seen = set()
        unique = []
        
        for rel in relationships:
            key = (
                rel["from_table"],
                rel["from_column"],
                rel["to_table"],
                rel["to_column"]
            )
            
            if key not in seen:
                seen.add(key)
                unique.append(rel)
        
        return unique
