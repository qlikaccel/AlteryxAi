"""
STAGE 6: Power BI Auto-Generates ER Diagram

Once relationships are written via REST API:
- Open Power BI Desktop (connected to dataset)
- OR Open semantic model online
- Model View auto-renders ER diagram

This stage generates Mermaid format diagrams for visualization
"""

import logging
from typing import Dict, List, Any

logger = logging.getLogger(__name__)


class ERDiagramGenerator:
    """Generate Entity-Relationship diagrams"""
    
    def generate_mermaid_diagram(
        self,
        tables: List[Dict[str, Any]],
        relationships: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate Mermaid ER diagram
        
        Output: Mermaid syntax for ER diagram
        """
        try:
            logger.info(f"[STAGE 6] Generating ER diagram for {len(tables)} tables")
            
            # Start diagram
            diagram_lines = ["erDiagram"]
            
            # Add entity definitions
            for table in tables[:20]:  # Limit to 20 tables for readability
                entity_name = self._sanitize_name(table.get("name", "Entity"))
                
                # Add fields (limit to 10 for readability)
                fields = table.get("fields", [])[:10]
                field_lines = []
                
                for field in fields:
                    field_name = self._sanitize_name(field.get("name", "field"))
                    field_type = self._map_type(field.get("type", "string"))
                    
                    field_lines.append(f"{field_name} {field_type}")
                
                # Format entity
                if field_lines:
                    fields_str = "\n        ".join(field_lines)
                    diagram_lines.append(f"    {entity_name} {{\n        {fields_str}\n    }}")
            
            # Add relationships
            for rel in relationships:
                from_entity = self._sanitize_name(rel.get("fromTable", ""))
                to_entity = self._sanitize_name(rel.get("toTable", ""))
                confidence = rel.get("confidence", 0.75)
                
                # Use cardinality indicators
                cardinality = rel.get("cardinality", "ManyToOne")
                
                if cardinality == "OneToMany":
                    connector = "||--o{"
                elif cardinality == "OneToOne":
                    connector = "||--||"
                else:  # ManyToOne (default)
                    connector = "}o--||"
                
                # Add comment with confidence
                confidence_pct = int(confidence * 100)
                diagram_lines.append(
                    f'    {from_entity} {connector} {to_entity} : "{confidence_pct}%"'
                )
            
            mermaid_text = "\n".join(diagram_lines)
            
            logger.info(f"[STAGE 6] ✓ ER diagram generated ({len(diagram_lines)} lines)")
            
            return {
                "success": True,
                "mermaid": mermaid_text,
                "format": "Mermaid ErDiagram",
                "table_count": len(tables),
                "relationship_count": len(relationships)
            }
        
        except Exception as e:
            logger.error(f"ER diagram generation failed: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "mermaid": ""
            }
    
    def generate_html_diagram(
        self,
        mermaid_text: str,
        title: str = "Entity-Relationship Diagram"
    ) -> str:
        """Generate HTML for embedding Mermaid diagram"""
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #007bff;
            padding-bottom: 10px;
        }}
        .mermaid {{
            display: flex;
            justify-content: center;
            margin: 20px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <div class="mermaid">
{mermaid_text}
        </div>
    </div>
    <script>
        mermaid.initialize({{ startOnLoad: true }});
        mermaid.contentLoaded();
    </script>
</body>
</html>
"""
        return html
    
    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for Mermaid syntax"""
        # Remove special characters, replace spaces
        sanitized = name.replace(" ", "_").replace("-", "_").replace(".", "_")
        # Keep only alphanumeric and underscore
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        return sanitized or "Entity"
    
    def _map_type(self, field_type: str) -> str:
        """Map data types to Mermaid type abbreviations"""
        type_map = {
            "string": "string",
            "integer": "int",
            "decimal": "decimal",
            "date": "date",
            "datetime": "datetime",
            "boolean": "bool",
            "float": "float",
            "real": "float"
        }
        return type_map.get(field_type.lower(), "string")


class DiagramExporter:
    """Export diagrams in various formats"""
    
    @staticmethod
    def export_mermaid(diagram: str, filepath: str) -> Dict[str, Any]:
        """Export Mermaid diagram to file"""
        try:
            with open(filepath, 'w') as f:
                f.write(diagram)
            return {
                "success": True,
                "filepath": filepath,
                "format": "Mermaid (.txt)"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def export_html(html: str, filepath: str) -> Dict[str, Any]:
        """Export HTML diagram to file"""
        try:
            with open(filepath, 'w') as f:
                f.write(html)
            return {
                "success": True,
                "filepath": filepath,
                "format": "HTML"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def export_json(
        diagram_data: Dict[str, Any],
        filepath: str
    ) -> Dict[str, Any]:
        """Export diagram data as JSON"""
        try:
            import json
            with open(filepath, 'w') as f:
                json.dump(diagram_data, f, indent=2)
            return {
                "success": True,
                "filepath": filepath,
                "format": "JSON"
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
