"""
qlik_script_parser.py
─────────────────────
Parses QlikSense / QlikView load scripts and extracts table definitions
with their source type, fields, filters, and metadata.

Supported load types:
  • LOAD … INLINE       — embedded data blocks
  • LOAD … FROM *.qvd   — QVD files
  • LOAD … FROM *.csv / *.txt / *.tsv  — delimited flat files
  • LOAD … FROM *.xlsx / *.xls         — Excel files
  • LOAD … FROM *.json
  • LOAD … RESIDENT <table>            — in-memory table reference
  • SELECT … SQL                       — direct SQL / ODBC
  • Auto-generated key tables (synthetic keys)
  • Aliased tables (AS / ALIAS)
  • NoConcatenate / Concatenate / Join / Keep

Output per table:
  {
    "name":        str,            # table alias or derived name
    "source_type": str,            # inline|qvd|csv|excel|json|resident|sql|unknown
    "source_path": str,            # file path / table ref (if applicable)
    "fields":      [               # list of field dicts
        {"name": str, "expression": str, "alias": str|None, "type": str}
    ],
    "raw_load":    str,            # the verbatim LOAD block
    "options":     dict,           # delimiter, header, sheet, where clause, etc.
  }
"""

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Regex helpers
# ─────────────────────────────────────────────────────────────────────────────

# Strip single-line comments (// …) and block comments (/* … */)
_RE_COMMENT_BLOCK  = re.compile(r"/\*.*?\*/",           re.DOTALL)
_RE_COMMENT_LINE   = re.compile(r"//[^\n]*")

# Matches:  TableName:  (optional label before LOAD / SELECT)
_RE_TABLE_LABEL    = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\. ]*?)\s*:", re.MULTILINE)

# Matches the LOAD keyword with the field list that follows
_RE_LOAD_KEYWORD   = re.compile(r"\bLOAD\b", re.IGNORECASE)
_RE_SELECT_KEYWORD = re.compile(r"\bSELECT\b", re.IGNORECASE)

# FROM clause  — captures the file/table path (handles bare, quoted, and [bracketed] paths)
_RE_FROM           = re.compile(
    r"""\bFROM\b\s+(?:\[([^\]]+)\]|'([^']+)'|"([^"]+)"|([^'";,\s\(]+))""",
    re.IGNORECASE,
)

# INLINE block  — LOAD … INLINE [ … ]
_RE_INLINE_BLOCK   = re.compile(
    r"\bINLINE\b\s*[\[(](.*?)[\])]",
    re.IGNORECASE | re.DOTALL,
)

# RESIDENT <table>
_RE_RESIDENT       = re.compile(r"\bRESIDENT\b\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

# WHERE clause (for filters)
_RE_WHERE          = re.compile(r"\bWHERE\b\s+(.+?)(?=\b(?:GROUP|ORDER|LOAD|SELECT|FROM|;)\b|$)",
                                re.IGNORECASE | re.DOTALL)

# DELIMITER / FORMAT options — handles: DELIMITER IS ','  or  DELIMITER IS ,  or  delimiter is tab
_RE_DELIMITER      = re.compile(
    r"\bDELIMITER\b\s+IS\s+(?:['\"](.+?)['\"]|(\S+))",
    re.IGNORECASE,
)
_RE_HEADER         = re.compile(r"\b(NO\s+LABELS|LABELS|HEADER\s+IS\s+\d+)\b", re.IGNORECASE)

# Table modifiers
_RE_NOCONCATENATE  = re.compile(r"\bNoConcatenate\b", re.IGNORECASE)
_RE_CONCATENATE    = re.compile(r"\bConcatenate\s*(?:\(([^)]+)\))?\b", re.IGNORECASE)
_RE_JOIN           = re.compile(r"\b(Left|Right|Inner|Outer)?\s*Join\s*(?:\(([^)]+)\))?\b", re.IGNORECASE)
_RE_KEEP           = re.compile(r"\b(Left|Right|Inner)?\s*Keep\s*(?:\(([^)]+)\))?\b", re.IGNORECASE)

# ALIAS  — [TableName] AS AliasName  or  ALIAS AliasName
_RE_ALIAS_AS       = re.compile(r"\]\s+AS\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_RE_ALIAS_KEYWORD  = re.compile(r"\bALIAS\b\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

# Field expression / alias inside a LOAD field list
# e.g.  FieldName,  [Field Name],  Expr() AS Alias,  *
_RE_FIELD          = re.compile(
    r"""
    (?:
        ([A-Za-z_*][A-Za-z0-9_\.\*]*)          # bare name or *
        |
        \[([^\]]+)\]                             # [bracketed name]
        |
        ([^,\n]+?)                               # any expression
    )
    (?:\s+AS\s+(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_]*)))?  # optional AS alias
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Extension / type detection from file path
_EXT_MAP = {
    ".qvd":  "qvd",
    ".csv":  "csv",
    ".txt":  "csv",
    ".tsv":  "csv",
    ".tab":  "csv",
    ".xlsx": "excel",
    ".xls":  "excel",
    ".xlsm": "excel",
    ".json": "json",
    ".xml":  "xml",
    ".parquet": "parquet",
}


# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

class QlikScriptParser:
    """
    Parse a Qlik load script and return a list of table definitions.
    """

    def parse(self, script: str) -> List[Dict[str, Any]]:
        """
        Entry point.

        Args:
            script: Raw Qlik load script text.

        Returns:
            List of table definition dicts.
        """
        cleaned  = self._strip_comments(script)
        blocks   = self._split_into_blocks(cleaned)
        tables   = []

        for raw_block, context in blocks:
            td = self._parse_block(raw_block, context)
            if td:
                tables.append(td)

        # Post-process: assign names to anonymous tables
        self._assign_missing_names(tables)
        logger.info("[QlikParser] Parsed %d table(s) from script", len(tables))
        return tables

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1: Strip comments
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_comments(script: str) -> str:
        # Remove block comments /* … */ first
        script = _RE_COMMENT_BLOCK.sub("", script)
        # Remove single-line comments //…  but NOT // inside strings or lib:// paths
        # Strategy: walk char by char, only strip // that are not preceded by ':'
        # and not inside a quoted string or bracket.
        lines = script.split("\n")
        result = []
        for line in lines:
            # Find // that are not part of :// (URLs like lib://)
            # Simple approach: find first // not preceded by :
            idx = 0
            out = []
            in_s = False  # inside single-quote string
            in_d = False  # inside double-quote string
            in_b = 0      # bracket depth
            i = 0
            while i < len(line):
                ch = line[i]
                if ch == "'" and not in_d and in_b == 0:
                    in_s = not in_s
                elif ch == '"' and not in_s and in_b == 0:
                    in_d = not in_d
                elif ch == "[" and not in_s and not in_d:
                    in_b += 1
                elif ch == "]" and not in_s and not in_d:
                    in_b = max(0, in_b - 1)
                elif (ch == "/" and i + 1 < len(line) and line[i+1] == "/"
                      and not in_s and not in_d and in_b == 0):
                    # Check if preceded by ':' (URL scheme separator)
                    if i > 0 and line[i-1] == ":":
                        # This is :// — not a comment, keep it
                        pass
                    else:
                        # Real line comment — truncate here
                        break
                out.append(ch)
                i += 1
            result.append("".join(out))
        return "\n".join(result)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2: Split script into individual LOAD / SELECT blocks
    # ─────────────────────────────────────────────────────────────────────────

    def _split_into_blocks(self, script: str) -> List[Tuple[str, Dict]]:
        """
        Split the full script on semicolons (respecting quoted strings and inline blocks),
        and identify which chunks contain a LOAD or SELECT statement.
        Returns list of (block_text, context_dict).
        """
        statements = self._split_on_semicolons(script)
        results = []

        for stmt in statements:
            stmt_stripped = stmt.strip()
            if not stmt_stripped:
                continue

            has_load   = bool(_RE_LOAD_KEYWORD.search(stmt_stripped))
            has_select = bool(_RE_SELECT_KEYWORD.search(stmt_stripped))

            if not (has_load or has_select):
                continue  # Not a data load statement

            ctx: Dict[str, Any] = {
                "has_load":     has_load,
                "has_select":   has_select,
                "modifier":     self._extract_modifier(stmt_stripped),
            }
            results.append((stmt_stripped, ctx))

        return results

    @staticmethod
    def _split_on_semicolons(script: str) -> List[str]:
        """
        Split on semicolons while respecting:
          • single-quoted strings  'abc'
          • double-quoted strings  "abc"
          • bracket groups         [abc]
        Parentheses are NOT tracked (function args may span lines legitimately).
        """
        parts: List[str] = []
        current: List[str] = []
        in_single = False
        in_double = False
        bracket_depth = 0
        i = 0
        n = len(script)

        while i < n:
            ch = script[i]

            if ch == "'" and not in_double and bracket_depth == 0:
                in_single = not in_single
            elif ch == '"' and not in_single and bracket_depth == 0:
                in_double = not in_double
            elif ch == "[" and not in_single and not in_double:
                bracket_depth += 1
            elif ch == "]" and not in_single and not in_double:
                bracket_depth = max(0, bracket_depth - 1)
            elif (ch == ";"
                  and not in_single
                  and not in_double
                  and bracket_depth == 0):
                parts.append("".join(current))
                current = []
                i += 1
                continue

            current.append(ch)
            i += 1

        if current:
            parts.append("".join(current))

        return parts

    @staticmethod
    def _extract_modifier(block: str) -> str:
        """Return NoConcatenate, Concatenate, Join, Keep, or ''."""
        if _RE_NOCONCATENATE.search(block):
            return "NoConcatenate"
        m = _RE_CONCATENATE.search(block)
        if m:
            return f"Concatenate({m.group(1) or ''})"
        m = _RE_JOIN.search(block)
        if m:
            join_type = (m.group(1) or "").strip()
            return f"{join_type}Join({m.group(2) or ''})".strip()
        m = _RE_KEEP.search(block)
        if m:
            keep_type = (m.group(1) or "").strip()
            return f"{keep_type}Keep({m.group(2) or ''})".strip()
        return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3: Parse a single block
    # ─────────────────────────────────────────────────────────────────────────

    def _parse_block(self, block: str, context: Dict) -> Optional[Dict[str, Any]]:
        table_name  = self._extract_table_label(block)
        source_type = "unknown"
        source_path = ""
        options: Dict[str, Any] = {}
        fields: List[Dict] = []
        raw_load = block

        # ── Detect load type ────────────────────────────────────────────────

        inline_match = _RE_INLINE_BLOCK.search(block)
        resident_match = _RE_RESIDENT.search(block)
        from_match = _RE_FROM.search(block)
        sql_match = _RE_SELECT_KEYWORD.search(block)

        if inline_match:
            source_type = "inline"
            inline_data = inline_match.group(1).strip()
            fields, options = self._parse_inline(inline_data)

        elif resident_match:
            source_type = "resident"
            source_path = resident_match.group(1).strip()
            fields = self._extract_load_fields(block)

        elif from_match:
            # group(1)=[bracketed], group(2)='single', group(3)="double", group(4)=bare
            source_path = (
                from_match.group(1) or from_match.group(2)
                or from_match.group(3) or from_match.group(4) or ""
            ).strip()
            ext = self._file_extension(source_path)
            source_type = _EXT_MAP.get(ext, "file")
            fields = self._extract_load_fields(block)
            options = self._extract_file_options(block, source_type)

        elif sql_match and context.get("has_select"):
            source_type = "sql"
            source_path = self._extract_sql_table(block)
            fields = self._extract_select_fields(block)

        else:
            # LOAD without FROM — could be a preceding SELECT or unusual syntax
            fields = self._extract_load_fields(block)
            source_type = "unknown"

        if not fields and source_type not in ("inline",):
            # Skip empty / pure-variable blocks
            return None

        # ── Where clause ────────────────────────────────────────────────────
        where_m = _RE_WHERE.search(block)
        if where_m:
            options["where"] = where_m.group(1).strip()

        # ── 🔥 GROUP BY detection ────────────────────────────────────────────
        group_match = re.search(r'GROUP\s+BY\s+(.+?)(?=\b(?:ORDER|JOIN|RESIDENT|CONCATENATE|;)\b|$)', 
                               block, re.IGNORECASE | re.DOTALL)
        if group_match:
            group_fields = [f.strip() for f in group_match.group(1).split(',')]
            options["is_group_by"] = True
            options["group_by_columns"] = group_fields
            logger.debug("[QlikParser] GROUP BY detected: %s", group_fields)

        # ── 🔥 JOIN detection ────────────────────────────────────────────
        join_match = re.search(r'(LEFT|INNER|RIGHT|OUTER)?\s*JOIN\s*\((\w+)\)', 
                              block, re.IGNORECASE)
        if join_match:
            join_type = (join_match.group(1) or "inner").lower()
            join_table = join_match.group(2).strip()
            options["is_join"] = True
            options["join_type"] = join_type
            options["join_table"] = join_table
            logger.debug("[QlikParser] JOIN detected: %s JOIN with table '%s'", 
                        join_type.upper(), join_table)

        # ── 🔥 CONCATENATE detection ────────────────────────────────────────
        concat_match = re.search(r'CONCATENATE\s*(?:\((\w+)\))?', 
                                block, re.IGNORECASE)
        if concat_match:
            concat_target = concat_match.group(1).strip() if concat_match.group(1) else ""
            options["is_concatenate"] = True
            if concat_target:
                options["concat_target"] = concat_target
            logger.debug("[QlikParser] CONCATENATE detected with target: %s", 
                        concat_target or "(auto-match)")

        # ── 🔥 KEEP detection ────────────────────────────────────────────────
        keep_match = re.search(r'(LEFT|RIGHT|INNER)?\s*KEEP\s*\((\w+)\)', 
                              block, re.IGNORECASE)
        if keep_match:
            keep_type = (keep_match.group(1) or "inner").lower()
            keep_table = keep_match.group(2).strip()
            options["is_keep"] = True
            options["keep_type"] = keep_type
            options["keep_table"] = keep_table
            logger.debug("[QlikParser] KEEP detected: %s KEEP with table '%s'", 
                        keep_type.upper(), keep_table)

        # ── Alias resolution ────────────────────────────────────────────────
        alias_m = _RE_ALIAS_KEYWORD.search(block) or _RE_ALIAS_AS.search(block)
        if alias_m and not table_name:
            table_name = alias_m.group(1).strip()

        return {
            "name":        table_name or "",
            "source_type": source_type,
            "source_path": source_path,
            "fields":      fields,
            "raw_load":    raw_load,
            "options":     options,
            "modifier":    context.get("modifier", ""),
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Field extraction helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_load_fields(self, block: str) -> List[Dict]:
        """Extract fields from  LOAD f1, f2, [f3] AS alias, * FROM …"""
        # Isolate the LOAD … FROM / INLINE / RESIDENT portion
        load_m = _RE_LOAD_KEYWORD.search(block)
        if not load_m:
            return []

        # Find terminator: FROM, INLINE, RESIDENT, WHERE, semicolon end
        term_pat = re.compile(
            r"\b(?:FROM|INLINE|RESIDENT|WHERE|GROUP\s+BY|ORDER\s+BY)\b",
            re.IGNORECASE,
        )
        field_section_start = load_m.end()
        term_m = term_pat.search(block, field_section_start)
        field_section_end = term_m.start() if term_m else len(block)

        field_text = block[field_section_start:field_section_end].strip()

        return self._parse_field_list(field_text)

    def _extract_select_fields(self, block: str) -> List[Dict]:
        """Extract fields from  SELECT f1, f2, f3 FROM …"""
        sel_m = _RE_SELECT_KEYWORD.search(block)
        if not sel_m:
            return []
        from_m = _RE_FROM.search(block, sel_m.end())
        end = from_m.start() if from_m else len(block)
        field_text = block[sel_m.end():end].strip()
        return self._parse_field_list(field_text)

    def _parse_field_list(self, field_text: str) -> List[Dict]:
        """Split a comma-separated field expression list into dicts."""
        if not field_text or field_text.strip() == "*":
            return [{"name": "*", "expression": "*", "alias": None, "type": "wildcard"}]

        fields = []
        # Split on commas not inside brackets / parentheses
        parts = self._smart_split(field_text)
        for part in parts:
            part = part.strip()
            if not part:
                continue

            # AS alias detection
            alias = None
            as_m = re.search(r"\bAS\b\s+(?:\[([^\]]+)\]|([A-Za-z_][A-Za-z0-9_ ]*))\s*$",
                             part, re.IGNORECASE)
            if as_m:
                alias = (as_m.group(1) or as_m.group(2)).strip()
                expr  = part[:as_m.start()].strip()
            else:
                expr = part

            # Bare field name (possibly bracketed)
            name = alias or self._extract_bare_name(expr)
            data_type = self._infer_field_type(expr)

            fields.append({
                "name":       name,
                "expression": expr,
                "alias":      alias,
                "type":       data_type,
            })

        return fields

    @staticmethod
    def _smart_split(text: str) -> List[str]:
        """Split on commas, respecting brackets [] and parentheses ()."""
        parts = []
        depth_paren  = 0
        depth_bracket = 0
        current = []
        for ch in text:
            if ch == "(":
                depth_paren += 1
            elif ch == ")":
                depth_paren -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1
            elif ch == "," and depth_paren == 0 and depth_bracket == 0:
                parts.append("".join(current).strip())
                current = []
                continue
            current.append(ch)
        if current:
            parts.append("".join(current).strip())
        return parts

    @staticmethod
    def _extract_bare_name(expr: str) -> str:
        """Return the plain field name from a possibly-bracketed expression."""
        expr = expr.strip()
        if expr.startswith("[") and expr.endswith("]"):
            return expr[1:-1]
        # If it's a function call like Date(OrderDate,'YYYY-MM-DD'), take the alias
        if "(" in expr:
            return expr  # will be overridden by alias
        return expr

    @staticmethod
    def _infer_field_type(expr: str) -> str:
        """Heuristic type inference from the expression text."""
        e = expr.upper()
        if re.search(r"\b(DATE|TIMESTAMP|MAKEDATE|YEAR|MONTH|DAY)\s*\(", e):
            return "date"
        if re.search(r"\b(NUM|INT|FLOOR|CEIL|ROUND|SUM|COUNT|AVG|MIN|MAX)\s*\(", e):
            return "integer"
        if re.search(r"\b(TEXT|LEFT|RIGHT|MID|UPPER|LOWER|TRIM|LTRIM|RTRIM|CAT|&)\b", e):
            return "string"
        if re.search(r"\b(IF|PICK|MATCH|DUAL)\s*\(", e):
            return "mixed"
        return "string"  # default

    @staticmethod
    def _infer_type_from_name(name: str) -> str:
        """Infer M type from column name heuristics."""
        n = name.lower().strip()
        if any(x in n for x in ["date", "time", "timestamp", "created", "updated", "dob", "birth"]):
            return "date"
        if any(x in n for x in ["id", "count", "qty", "quantity", "year", "month", "day", "num", "age", "rank"]):
            return "integer"
        if any(x in n for x in ["price", "cost", "amount", "revenue", "salary", "rate", "total", "tax", "discount", "margin"]):
            return "number"
        return "string"

    # ─────────────────────────────────────────────────────────────────────────
    # Inline parser
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_inline(inline_data: str) -> Tuple[List[Dict], Dict]:
        """
        Parse INLINE [ header_row \n data_rows… ] block.
        Returns (fields, options).
        """
        lines = [l.strip() for l in inline_data.splitlines() if l.strip()]
        if not lines:
            return [], {}

        # First line = header
        headers = [h.strip() for h in lines[0].split(",")]
        fields = [
            {"name": h, "expression": h, "alias": None, "type": QlikScriptParser._infer_type_from_name(h)}
            for h in headers if h
        ]

        # Collect sample data rows
        # Collect sample data rows — handle quoted values and multiline rows
        sample_rows = []
        import csv, io
        raw_data = "\n".join(lines[1:])
        try:
            reader = csv.reader(io.StringIO(raw_data), skipinitialspace=True)
            for row_vals in reader:
                if row_vals:
                    # Sanitize each value
                    clean_vals = []
                    for v in row_vals:
                        v = v.strip().strip("'")          # strip Qlik quotes
                        v = v.replace("\n", " ")           # collapse newlines
                        v = v.replace("\r", "")            # remove CR
                        v = " ".join(v.split())            # collapse spaces
                        clean_vals.append(v)
                    sample_rows.append(dict(zip(headers, clean_vals)))
        except Exception:
            # Fallback to simple split
            for line in lines[1:]:
                row_vals = [v.strip().strip("'") for v in line.split(",")]
                if row_vals:
                    sample_rows.append(dict(zip(headers, row_vals)))
        options = {
            "inline_headers": headers,
            "inline_sample":  sample_rows[:5],
            "inline_row_count": len(lines) - 1,
        }
        return fields, options

    # ─────────────────────────────────────────────────────────────────────────
    # File options
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_file_options(block: str, source_type: str) -> Dict[str, Any]:
        options: Dict[str, Any] = {}

        # Delimiter
        delim_m = _RE_DELIMITER.search(block)
        if delim_m:
            options["delimiter"] = (delim_m.group(1) or delim_m.group(2) or ",").strip()
        elif source_type == "csv":
            options["delimiter"] = ","  # default

        # Header
        hdr_m = _RE_HEADER.search(block)
        if hdr_m:
            options["header"] = hdr_m.group(1).strip()

        # Excel sheet (SHEET IS 'SheetName')
        sheet_m = re.search(r"\bSHEET\b\s+(?:IS\s+)?['\"]?([^'\";\s]+)['\"]?", block, re.IGNORECASE)
        if sheet_m:
            options["sheet"] = sheet_m.group(1).strip()

        # Encoding
        enc_m = re.search(r"\b(UTF-?8|UTF-?16|CP\d{4}|LATIN\d|ISO-?\d+)\b", block, re.IGNORECASE)
        if enc_m:
            options["encoding"] = enc_m.group(1)

        # FirstRecord / SkipRows
        skip_m = re.search(r"\bFIRSTRECORD\s+(?:IS\s+)?(\d+)\b", block, re.IGNORECASE)
        if skip_m:
            options["first_record"] = int(skip_m.group(1))

        return options

    # ─────────────────────────────────────────────────────────────────────────
    # SQL / Resident helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_sql_table(block: str) -> str:
        """Pull the table name from  SELECT … FROM <table>."""
        m = _RE_FROM.search(block)
        if m:
            return (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Label / name extraction
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_table_label(block: str) -> str:
        """Return the  TableName:  label that precedes LOAD / SELECT."""
        m = _RE_TABLE_LABEL.match(block)
        if m:
            candidate = m.group(1).strip()
            # Avoid false positives like "NoConcatenate:" etc.
            reserved = {"noconcatenate", "concatenate", "join", "keep", "left",
                        "right", "inner", "outer", "where", "load", "select"}
            if candidate.lower() not in reserved:
                return candidate
        return ""

    @staticmethod
    def _file_extension(path: str) -> str:
        """Return lower-case file extension including the dot."""
        idx = path.rfind(".")
        return path[idx:].lower() if idx != -1 else ""

    # ─────────────────────────────────────────────────────────────────────────
    # Post-processing
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _assign_missing_names(tables: List[Dict]) -> None:
        """
        Give unnamed tables a derived name from their source path or index.
        
        FIX 8: Handle duplicate file loads by appending _1, _2, etc., so that
        when fact_employee_activity_1M.csv is loaded twice without explicit 
        table names, they become fact_employee_activity_1M_1 and 
        fact_employee_activity_1M_2 instead of overwriting.
        """
        counter = 1
        used_names = set()
        
        # First pass: collect all explicit table names (those already set)
        for td in tables:
            if td["name"]:
                used_names.add(td["name"])
        
        # Second pass: assign names to unnamed tables, handling duplicates
        for td in tables:
            if td["name"]:
                continue
            
            path = td.get("source_path", "")
            if path:
                # Derive from file name: /data/Sales Orders.qvd → SalesOrders
                base = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                base = re.sub(r"\.[^.]+$", "", base)          # remove ext
                base = re.sub(r"[^A-Za-z0-9_]", "_", base)   # sanitize
                proposed_name = base or f"Table_{counter}"
            else:
                proposed_name = f"Table_{counter}"
            
            # If name already exists (duplicate file load), append suffix
            if proposed_name in used_names:
                suffix = 1
                unique_name = f"{proposed_name}_{suffix}"
                while unique_name in used_names:
                    suffix += 1
                    unique_name = f"{proposed_name}_{suffix}"
                td["name"] = unique_name
                logger.debug("[_assign_missing_names] Duplicate file detected, renamed '%s' → '%s'", proposed_name, unique_name)
            else:
                td["name"] = proposed_name
            
            used_names.add(td["name"])
            counter += 1


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function
# ─────────────────────────────────────────────────────────────────────────────

def parse_qlik_script(script: str) -> List[Dict[str, Any]]:
    """Parse a Qlik load script and return table definitions."""
    return QlikScriptParser().parse(script)
