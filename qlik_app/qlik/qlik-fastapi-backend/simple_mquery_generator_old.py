import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class SimpleMQueryGenerator:
    def __init__(self, parsed_script: Dict[str, Any], lib_path_map=None, selected_table: str = None):
        self.parsed = parsed_script
        self.lib_path_map = lib_path_map or {}
        self.selected_table = selected_table
        self.warnings = []
    
    def generate(self) -> str:
        logger.info("Starting M Query generation...")
        if not self.parsed:
            return self._empty_query("No parsed script provided")
        tables = self._extract_tables()
        if not tables:
            return self._empty_query("No tables found in LoadScript")
        if self.selected_table:
            logger.info(f"Single-table mode: {self.selected_table}")
            tables = [t for t in tables if t["name"].lower() == self.selected_table.lower()]
            if not tables:
                return self._empty_query(f'Table "{self.selected_table}" not found')
            return self._build_table_query(tables[0])
        output = []
        for table in tables:
            output.append(f"// ===== TABLE: {table['name']} =====")
            output.append(self._build_table_query(table))
            output.append("")
        return "\n".join(output)
    
    def _extract_tables(self) -> list:
        raw_script = self.parsed.get("raw_script", "")
        tables = []
        pattern = re.compile(r'(?:\[([^\]]+)\]|(\b\w+\b))\s*:\s*LOAD\s+([\s\S]*?);', re.IGNORECASE)
        for match in pattern.finditer(raw_script):
            table_name = (match.group(1) or match.group(2)).strip()
            load_body = match.group(3)
            source_match = re.search(r'\bFROM\s+[\'\"[]?([^\'\"]+)', load_body, re.IGNORECASE)
            source = source_match.group(1).strip() if source_match else ""
            fields = self._extract_fields_from_load(load_body)
            tables.append({"name": table_name, "source": source, "fields": fields})
        return tables
    
    def _extract_fields_from_load(self, load_body: str) -> list:
        fields = []
        fields_match = re.search(r'LOAD\s+(?:DISTINCT\s+)?([\s\S]*?)(?=\bFROM\b|\bRESIDENT\b|;)', load_body, re.IGNORECASE)
        if not fields_match:
            return fields
        fields_text = fields_match.group(1).strip()
        depth, current_field = 0, []
        for ch in fields_text:
            if ch == '(': depth += 1
            elif ch == ')': depth -= 1
            elif ch == ',' and depth == 0:
                field = ''.join(current_field).strip()
                if field:
                    field_name = self._extract_field_name(field)
                    if field_name: fields.append(field_name)
                current_field = []
                continue
            current_field.append(ch)
        field = ''.join(current_field).strip()
        if field:
            field_name = self._extract_field_name(field)
            if field_name: fields.append(field_name)
        return fields
    
    def _extract_field_name(self, field_str: str) -> str:
        as_match = re.split(r'\s+[Aa][Ss]\s+', field_str, maxsplit=1)
        name = (as_match[1].strip().strip('[]"') if len(as_match) > 1 else as_match[0].strip().strip('[]"').split('(')[0].strip())
        return name if name and name.upper() not in ('FROM', 'WHERE', 'RESIDENT', 'LOAD', 'SELECT') else None
    
    def _build_table_query(self, table: Dict[str, Any]) -> str:
        table_name, source_path = table["name"], table["source"]
        fields = table.get("fields", [])
        if not source_path: return self._empty_query(f"No source found for table '{table_name}'")
        source_m = self._build_source_expression(source_path)
        query_lines = ["let", f"    Source = {source_m},", "    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true])"]
        query_lines.extend([",", f"    RemoveNulls = Table.SelectRows(PromotedHeaders, each [Column1] <> null)", "in", "    RemoveNulls"])
        return "\n".join(query_lines)
    
    def _build_source_expression(self, path: str) -> str:
        path = path.strip()
        if path.lower().endswith(".csv"): return f'Csv.Document(File.Contents("{path}"), [Delimiter=",", Encoding=1252, QuoteStyle=QuoteStyle.Csv])'
        if path.lower().endswith((".xlsx", ".xls")): return f'Excel.Workbook(File.Contents("{path}"), [DelimiterType=Delimiter.Comma])'
        return f'File.Contents("{path}")'
    
    @staticmethod
    def _empty_query(reason: str) -> str:
        return f"""// Could not generate M Query: {reason}\nlet\n    Source = "No data available"\nin\n    Source"""
