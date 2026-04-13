"""
Simple M Query Generator  (v3 – handles real Qlik loadscript patterns)

Fixes vs. v2
────────────
1. raw_script missing from parse_result → generator now also accepts the
   loadscript string directly via parsed_script["raw_script"] (injected by
   the fixed migration_api.py), AND has a fallback that tries to infer source
   from parsed["details"]["data_connections"] when raw_script is absent.
2. MAPPING LOAD / RESIDENT tables (e.g. __cityName2Key) have no FROM source
   → detected and skipped; they are internal Qlik join helpers, not data tables.
3. Internal geo-helper tables (__cityAliasesBase, __cityGeoBase, __city*)
   → filtered out; they are auto-generated Qlik geo scaffolding.
4. Complex field expressions with nested functions (APPLYMAP, LOWER, etc.)
   → _extract_alias now strips the full expression and returns only the AS alias.
5. .qvd source files → mapped to the correct Qlik QVD binary read comment
   (QVDs are Qlik-proprietary; they need a note in the M Query output).
6. self.tables always populated in __init__ → no AttributeError for callers.
7. New helpers:
     get_available_tables()          → list[str]
     get_loadscript_for_table(name)  → str  (original Qlik snippet)
"""

import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# RelationshipExtractor lives alongside this module
try:
    from relationship_extractor import RelationshipExtractor
    _HAS_REL_EXTRACTOR = True
except ImportError:
    _HAS_REL_EXTRACTOR = False


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

# Tables that Qlik auto-generates for geo / mapping purposes – not real data
_INTERNAL_TABLE_PREFIXES = ("__city", "__geo")

# Qlik keywords that are never real table/field names
_SKIP_KEYWORDS = {
    "FROM", "WHERE", "RESIDENT", "LOAD", "SELECT", "DISTINCT",
    "GROUP", "ORDER", "BY", "AS", "AND", "OR", "NOT", "MAPPING",
    "DROP", "TAG", "SET", "LET", "FOR", "NEXT", "IF", "THEN",
    "ENDIF", "DO", "LOOP", "WHILE", "DERIVE", "DECLARE",
}


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_name(raw: str) -> str:
    """Strip surrounding brackets / quotes from a name token."""
    return raw.strip().strip('[]"\'')


def _sanitize_step_name(name: str) -> str:
    """Convert a table name to a valid M Query step identifier."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return safe or "_Table"


def _is_internal_table(name: str) -> bool:
    """Return True for Qlik-internal auto-generated tables we should skip."""
    lower = name.lower()
    return any(lower.startswith(p) for p in _INTERNAL_TABLE_PREFIXES)


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────

class SimpleMQueryGenerator:
    """
    Convert a parsed Qlik LoadScript structure into Power BI M Query code.

    Parameters
    ----------
    parsed_script : dict
        Either the dict from LoadScriptParser.parse() (with
        details.tables / details.data_connections), OR any dict that
        also/only has a "raw_script" key with the raw loadscript text.
        Both shapes work together.
    lib_path_map : dict, optional
        e.g. {"DataFiles": "C:/ActualData"} – replaces lib:// segments.
    selected_table : str, optional
        Single-table mode: generate M Query only for this table.
    """

    def __init__(
        self,
        parsed_script: Dict[str, Any],
        lib_path_map: Optional[Dict[str, str]] = None,
        selected_table: Optional[str] = None,
    ):
        self.parsed = parsed_script or {}
        self.lib_path_map = lib_path_map or {}
        self.selected_table = selected_table
        self.warnings: List[str] = []

        # Always populate self.tables so callers can do generator.tables
        self.tables: List[Dict[str, Any]] = self._extract_tables()
        logger.info(
            "SimpleMQueryGenerator ready – %d data table(s) found",
            len(self.tables),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def generate(self) -> str:
        """Return M Query string (single-table or multi-table)."""
        logger.info("Starting M Query generation...")

        if not self.tables:
            return self._empty_query("No tables found in LoadScript")

        # Build relationship comment blocks once (keyed by table name)
        rel_comments: Dict[str, str] = {}
        if _HAS_REL_EXTRACTOR:
            try:
                extractor = RelationshipExtractor(self.tables)
                rel_comments = extractor.to_m_query_comment_block()
            except Exception as e:
                logger.warning("Relationship extraction failed: %s", e)

        if self.selected_table:
            logger.info("Single-table mode: %s", self.selected_table)
            match = [
                t for t in self.tables
                if t["name"].lower() == self.selected_table.lower()
            ]
            if not match:
                return self._empty_query(
                    'Table "%s" not found' % self.selected_table
                )
            table = match[0]
            comment = rel_comments.get(table["name"], "")
            return comment + self._build_table_query(table)

        # Multi-table – one named block per table
        output: List[str] = []
        for table in self.tables:
            output.append("// ===== TABLE: %s =====" % table["name"])
            comment = rel_comments.get(table["name"], "")
            if comment:
                output.append(comment)
            output.append(self._build_table_query(table))
            output.append("")
        return "\n".join(output)

    def get_available_tables(self) -> List[str]:
        """Return list of data table names (internal Qlik tables excluded)."""
        return [t["name"] for t in self.tables]

    def get_loadscript_for_table(self, table_name: str) -> str:
        """
        Return the original Qlik LoadScript snippet for one table.
        Returns "" if raw_script is unavailable or the table is not found.
        """
        raw_script = self.parsed.get("raw_script", "")
        if not raw_script:
            logger.warning("get_loadscript_for_table: no raw_script in parsed dict")
            return ""

        escaped = re.escape(table_name)
        pattern = re.compile(
            r"(?:\[" + escaped + r"\]|(?<!\w)" + escaped + r"(?!\w))"
            r"\s*:\s*(?:MAPPING\s+)?LOAD[\s\S]*?;",
            re.IGNORECASE,
        )
        m = pattern.search(raw_script)
        if m:
            return m.group(0).strip()

        logger.warning(
            "get_loadscript_for_table: '%s' not found in raw_script", table_name
        )
        return ""

    # ─────────────────────────────────────────────────────────────────────
    # Table extraction
    # ─────────────────────────────────────────────────────────────────────

    def _extract_tables(self) -> List[Dict[str, Any]]:
        """
        Build the internal table list from whatever shape the caller provided.

        Priority:
          1. parsed["details"]["tables"] from LoadScriptParser (enriched via raw_script)
          2. Direct re-parse of raw_script (fallback / complement)

        Internal tables (__city*, __geo*) and MAPPING/RESIDENT tables are excluded.
        """
        tables: List[Dict[str, Any]] = []
        seen: set = set()

        details = self.parsed.get("details", {})
        parsed_tables = details.get("tables", [])
        data_connections = details.get("data_connections", [])
        raw_script = self.parsed.get("raw_script", "")

        # Path 1 – use LoadScriptParser's structured table list
        if parsed_tables:
            for pt in parsed_tables:
                name = _clean_name(pt.get("name", ""))
                if not name or name.lower() in seen:
                    continue
                if _is_internal_table(name):
                    logger.debug("Skipping internal table: %s", name)
                    continue
                seen.add(name.lower())
                enriched = self._enrich_table(name, raw_script, data_connections)
                if enriched is not None:
                    tables.append(enriched)

        # Path 2 – scan raw_script directly for tables not yet captured
        if raw_script:
            for t in self._parse_tables_from_raw(raw_script):
                if t["name"].lower() not in seen:
                    seen.add(t["name"].lower())
                    tables.append(t)

        return tables

    def _enrich_table(
        self,
        table_name: str,
        raw_script: str,
        data_connections: List[Dict],
    ) -> Optional[Dict[str, Any]]:
        """
        Find the table's LOAD block and extract source + fields.
        Returns None if the table has no FROM source (MAPPING/RESIDENT → skip).
        """
        if raw_script:
            result = self._parse_single_table_from_raw(table_name, raw_script)
            if result is not None:
                return result

        # Fallback: infer source from data_connections when raw_script absent
        source = self._infer_source_from_connections(table_name, data_connections)
        if not source:
            logger.warning(
                "No source found for '%s' and no raw_script available", table_name
            )
            return {"name": table_name, "source": "", "fields": [],
                    "where": "", "is_distinct": False}

        return {"name": table_name, "source": source, "fields": [],
                "where": "", "is_distinct": False}

    def _infer_source_from_connections(
        self, table_name: str, data_connections: List[Dict]
    ) -> str:
        """
        Try to find a matching lib:// path in data_connections by matching
        the table name against the filename in the path.
        e.g. table "Vehicle_Fact_MASTER" → matches "Vehicle_Fact_MASTER.csv"
        """
        name_lower = table_name.lower()
        for conn in data_connections:
            path = conn.get("source", conn.get("path", ""))
            if name_lower in path.lower():
                # Strip leading lib:// and any trailing bracket
                clean = re.sub(r"^lib://", "", path, flags=re.IGNORECASE)
                clean = clean.strip().rstrip("]")
                return "lib://" + clean
        return ""

    def _parse_single_table_from_raw(
        self, table_name: str, raw_script: str
    ) -> Optional[Dict[str, Any]]:
        """
        Locate one table's LOAD block in raw_script and extract everything.
        Returns None if the table uses RESIDENT (no file source).
        """
        escaped = re.escape(table_name)
        pattern = re.compile(
            r"(?:\[" + escaped + r"\]|(?<!\w)" + escaped + r"(?!\w))"
            r"\s*:\s*(MAPPING\s+)?LOAD([\s\S]*?);",
            re.IGNORECASE,
        )
        m = pattern.search(raw_script)
        if not m:
            return None

        is_mapping = bool(m.group(1))
        load_body = m.group(2)

        # MAPPING LOAD ... RESIDENT → internal join helper, skip
        if is_mapping or re.search(r"\bRESIDENT\b", load_body, re.IGNORECASE):
            logger.debug(
                "Skipping MAPPING/RESIDENT table: %s", table_name
            )
            return None

        source = self._extract_source(load_body)
        if not source:
            logger.warning("No source found in LOAD body for: %s", table_name)
            return None

        return {
            "name": table_name,
            "source": source,
            "fields": self._extract_fields_from_load(load_body),
            "where": self._extract_where(load_body),
            "is_distinct": bool(
                re.search(r"\bDISTINCT\b", load_body, re.IGNORECASE)
            ),
        }

    def _parse_tables_from_raw(self, raw_script: str) -> List[Dict[str, Any]]:
        """
        Scan raw_script directly for all TABLE: LOAD ... ; blocks.
        Used as fallback when parsed_script has no details.tables.
        """
        tables: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"(?:\[([^\]]+)\]|(\b\w+\b))\s*:\s*(MAPPING\s+)?LOAD([\s\S]*?);",
            re.IGNORECASE,
        )
        for m in pattern.finditer(raw_script):
            name = _clean_name(m.group(1) or m.group(2))
            if not name or name.upper() in _SKIP_KEYWORDS:
                continue
            if _is_internal_table(name):
                continue

            is_mapping = bool(m.group(3))
            load_body = m.group(4)

            if is_mapping or re.search(r"\bRESIDENT\b", load_body, re.IGNORECASE):
                continue

            source = self._extract_source(load_body)
            if not source:
                continue

            tables.append({
                "name": name,
                "source": source,
                "fields": self._extract_fields_from_load(load_body),
                "where": self._extract_where(load_body),
                "is_distinct": bool(
                    re.search(r"\bDISTINCT\b", load_body, re.IGNORECASE)
                ),
            })
        return tables

    # ─────────────────────────────────────────────────────────────────────
    # Source / WHERE extraction
    # ─────────────────────────────────────────────────────────────────────

    def _extract_source(self, load_body: str) -> str:
        """
        Pull the file path from a LOAD body.
        Handles:
          FROM [lib://DataFiles/file.csv]    ← path on same line as FROM
          FROM [lib://DataFiles/file.csv]\n  ← path on next line
        """
        # lib:// reference (exclude trailing ] bracket from Qlik syntax)
        lib_match = re.search(
            r"lib://([^\s;'\"\(\)\]]+)", load_body, re.IGNORECASE
        )
        if lib_match:
            lib_path = lib_match.group(1).strip()
            for lib_key, real_path in self.lib_path_map.items():
                if lib_key.lower() in lib_path.lower():
                    return lib_path.replace(lib_key, real_path)
            return "lib://" + lib_path

        # Bare FROM 'path' or FROM path (no lib://)
        from_match = re.search(
            r"\bFROM\s+['\"\[]?([^'\"\];\s\(\r\n]+)",
            load_body, re.IGNORECASE
        )
        if from_match:
            return from_match.group(1).strip().rstrip("]")

        return ""

    def _extract_where(self, load_body: str) -> str:
        """Extract WHERE clause, stopping before Qlik file options like (txt,...)."""
        m = re.search(
            r"\bWHERE\s+(.+?)(?=\bGROUP\b|\bORDER\b|\bFROM\b|\(txt|\(csv|\(qvd|;|$)",
            load_body, re.IGNORECASE | re.DOTALL,
        )
        return m.group(1).strip() if m else ""

    # ─────────────────────────────────────────────────────────────────────
    # Field extraction
    # ─────────────────────────────────────────────────────────────────────

    def _extract_fields_from_load(self, load_body: str) -> List[str]:
        """
        Extract field / alias names from the LOAD field-list.
        Handles: plain names, [Bracketed Names], expr AS alias,
                 func(args) AS alias, nested APPLYMAP(...) AS alias.
        """
        fields: List[str] = []

        # Isolate the field-list between LOAD [DISTINCT] and FROM/RESIDENT
        fm = re.search(
            r"(?:DISTINCT\s+)?([\s\S]*?)(?=\bFROM\b|\bRESIDENT\b|;|$)",
            load_body, re.IGNORECASE,
        )
        if not fm:
            return fields

        fields_text = fm.group(1).strip()
        if not fields_text:
            return fields

        # Split on top-level commas (ignore commas inside parentheses/brackets)
        depth = 0
        current: List[str] = []
        for ch in fields_text:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth -= 1
            elif ch == "," and depth == 0:
                token = "".join(current).strip()
                name = self._extract_alias(token)
                if name:
                    fields.append(name)
                current = []
                continue
            current.append(ch)

        token = "".join(current).strip()
        name = self._extract_alias(token)
        if name:
            fields.append(name)

        return fields

    def _extract_alias(self, field_str: str) -> Optional[str]:
        """
        Return the best output name for a single field token.
        For  'APPLYMAP(...) AS [Dealer_Master.City_GeoInfo]'  returns
        'Dealer_Master.City_GeoInfo'.
        For  '[ServiceID] AS [DealerID-ServiceID]'  returns 'DealerID-ServiceID'.
        For  '[ModelName]'  returns 'ModelName'.
        """
        field_str = field_str.strip()
        if not field_str:
            return None

        # Prefer AS alias – split on the LAST ' AS ' to handle nested expressions
        as_match = re.split(r"\s+[Aa][Ss]\s+", field_str)
        if len(as_match) > 1:
            alias = _clean_name(as_match[-1])
            return alias if alias and alias.upper() not in _SKIP_KEYWORDS else None

        # No alias – use the bare field name; strip function calls and brackets
        # e.g. "[City]" → "City",  "SalePrice" → "SalePrice"
        bare = _clean_name(field_str.split("(")[0].strip())
        return bare if bare and bare.upper() not in _SKIP_KEYWORDS else None

    # ─────────────────────────────────────────────────────────────────────
    # M Query builder
    # ─────────────────────────────────────────────────────────────────────

    # Field name patterns → M type  (checked via substring match, lower-cased)
    _TYPE_HINTS: List[tuple] = [
        # Dates / timestamps first (before generic "id" / "price" checks)
        (["date", "timestamp", "createdat", "updatedat", "modifiedat",
          "productiondate", "servicedate"], "type date"),
        # Numeric IDs and measures
        (["price", "cost", "amount", "salary", "revenue", "sales",
          "quantity", "qty", "count", "total", "rate", "score",
          "year", "age", "num"], "type number"),
        # Boolean-ish
        (["isactive", "enabled", "flag", "bool"], "type logical"),
        # Everything else defaults to text – handled in _infer_m_type
    ]

    def _infer_m_type(self, field_name: str) -> str:
        """
        Heuristically map a field name to an M type.
        Defaults to 'type text' when no pattern matches.
        """
        lower = field_name.lower().replace(" ", "").replace("_", "").replace("-", "")
        for keywords, m_type in self._TYPE_HINTS:
            if any(kw in lower for kw in keywords):
                return m_type
        return "type text"

    def _build_table_query(self, table: Dict[str, Any]) -> str:
        """
        Emit a complete, idiomatic Power BI M Query let…in block for one table.

        Steps generated (matching what Power BI Desktop produces natively):
          1. Source              – Csv.Document / Excel.Workbook / File.Contents
          2. #"Promoted Headers" – Table.PromoteHeaders
          3. #"Selected Columns" – Table.SelectColumns  (when field list known)
          4. #"Changed Type"     – Table.TransformColumnTypes with inferred types
          5. #"Removed Blanks"   – Table.SelectRows filtering null key column
                                   (only when a key column can be identified)
          6. #"Removed Duplicates" – Table.Distinct  (LOAD DISTINCT only)

        File path:
          lib:// paths are emitted as a FilePath parameter reference so the
          user only needs to update one parameter rather than every query.
          e.g.  File.Contents( FilePath & "Vehicle_Fact_MASTER.csv" )
        """
        table_name = table["name"]
        source_path = table.get("source", "")
        fields: List[str] = table.get("fields", [])
        where_clause = table.get("where", "")
        is_distinct = table.get("is_distinct", False)

        if not source_path:
            return self._empty_query(
                "No source found for table '%s'" % table_name
            )

        if source_path.lower().endswith(".qvd"):
            return self._qvd_note(table_name, source_path)

        source_expr = self._build_source_expression(source_path)

        # ── Build steps using Power BI naming convention ──────────────────
        # Step references with spaces must be quoted with #"..."
        lines: List[str] = ["let"]

        lines.append('    Source = %s,' % source_expr)
        lines.append(
            '    #"Promoted Headers" = '
            'Table.PromoteHeaders(Source, [PromoteAllScalars=true]),'
        )
        prev = '#"Promoted Headers"'

        # Select only declared columns (drops Qlik-internal helper columns)
        if fields:
            col_list = ",\n        ".join(
                '{"%s"}' % f for f in fields
            )
            lines.append(
                '    #"Selected Columns" = Table.SelectColumns(\n'
                '        %s,\n'
                '        {\n'
                '        %s\n'
                '        }, MissingField.Ignore),' % (prev, col_list)
            )
            prev = '#"Selected Columns"'

        # Type assignment – critical for DAX measures, relationships, date hierarchies
        if fields:
            type_pairs = ",\n        ".join(
                '{"%s", %s}' % (f, self._infer_m_type(f)) for f in fields
            )
            lines.append(
                '    #"Changed Type" = Table.TransformColumnTypes(\n'
                '        %s,\n'
                '        {\n'
                '        %s\n'
                '        }),' % (prev, type_pairs)
            )
            prev = '#"Changed Type"'

        # Remove blank rows – filter on the first field (most likely a key)
        # This is correct M: compare a single typed column value to null
        if fields:
            key_col = fields[0]
            lines.append(
                '    #"Removed Blanks" = Table.SelectRows(%s, each [%s] <> null),'
                % (prev, key_col)
            )
            prev = '#"Removed Blanks"'

        # DISTINCT (LOAD DISTINCT in Qlik)
        if is_distinct:
            lines.append(
                '    #"Removed Duplicates" = Table.Distinct(%s),' % prev
            )
            prev = '#"Removed Duplicates"'

        # Remove trailing comma from last step
        lines[-1] = lines[-1].rstrip(",")
        lines.append("in")
        lines.append("    %s" % prev)

        # Prepend WHERE clause as a comment for the developer's reference
        header = ""
        if where_clause:
            header = (
                "// ── Qlik WHERE clause (translate manually to M filter) ──\n"
                "// WHERE %s\n\n" % where_clause
            )
        return header + "\n".join(lines)

    def _build_source_expression(self, path: str) -> str:
        """
        Return the correct M source function call.

        lib:// paths become a FilePath parameter reference:
            File.Contents( FilePath & "filename.csv" )
        so the user only updates the FilePath query parameter once.

        Absolute / non-lib paths are kept as-is.
        """
        path = path.strip()
        is_lib = path.lower().startswith("lib://")

        # Extract just the filename / relative portion
        if is_lib:
            # lib://DataFiles/foo.csv → DataFiles/foo.csv
            rel = re.sub(r"^lib://", "", path, flags=re.IGNORECASE)
            file_ref = 'FilePath & "%s"' % rel
        else:
            file_ref = '"%s"' % path

        lower = path.lower()

        if lower.endswith(".csv"):
            return (
                "Csv.Document(\n"
                "        File.Contents(%s),\n"
                "        [Delimiter = \",\", Encoding = 65001, "
                "QuoteStyle = QuoteStyle.Csv])" % file_ref
            )
        if lower.endswith(".xlsx") or lower.endswith(".xls"):
            return (
                "Excel.Workbook(\n"
                "        File.Contents(%s), null, true)" % file_ref
            )
        if lower.endswith(".parquet"):
            return "Parquet.Document(File.Contents(%s))" % file_ref

        return "File.Contents(%s)" % file_ref

    @staticmethod
    def _qvd_note(table_name: str, source_path: str) -> str:
        """
        QVD files are Qlik-proprietary binary format, unreadable by Power BI.
        Emit a descriptive stub so the developer knows what action to take.
        """
        rel = re.sub(r"^lib://", "", source_path, flags=re.IGNORECASE)
        return (
            "// ⚠️  TABLE: {name}\n"
            "// Source: {rel}\n"
            "// QVD is a Qlik-proprietary binary format — Power BI cannot read it directly.\n"
            "// Action required (choose one):\n"
            "//   1. In Qlik Sense: store the table as CSV, then update FilePath below.\n"
            "//   2. Use a third-party QVD reader Power Query connector.\n"
            "let\n"
            "    Source = \"⚠️  Replace this with: Csv.Document(File.Contents(FilePath & \\\"{rel}\\\"), ...)\"\n"
            "in\n"
            "    Source"
        ).format(name=table_name, rel=rel)

    @staticmethod
    def _empty_query(reason: str) -> str:
        return (
            "// Could not generate M Query: %s\n"
            "let\n"
            '    Source = "No data available"\n'
            "in\n"
            "    Source"
        ) % reason


# ─────────────────────────────────────────────────────────────────────────────
# Factory – drop-in for migration_api.py's create_mquery_generator fallback
# ─────────────────────────────────────────────────────────────────────────────

def create_mquery_generator(
    parsed_script: Dict[str, Any],
    table_name: Optional[str] = None,
    lib_mapping: Optional[Dict[str, str]] = None,
) -> SimpleMQueryGenerator:
    """
    Drop-in factory used by migration_api.py when EnhancedMQueryGenerator
    is not installed. Returns a fully initialised SimpleMQueryGenerator
    with self.tables already populated.
    """
    return SimpleMQueryGenerator(
        parsed_script=parsed_script,
        lib_path_map=lib_mapping,
        selected_table=table_name,
    )
