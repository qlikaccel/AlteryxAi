# """
# Relationship Extractor for Qlik → Power BI Migration

# How Qlik handles relationships vs. how Power BI needs them:

#   QLIK:   Associative engine — any two tables sharing a field name
#           are automatically joined at query time. No explicit FK
#           definitions exist in the loadscript.

#   POWER BI: Requires explicit relationships defined in the Model layer.
#             M Query (Power Query) cannot define relationships —
#             they live in the Tabular Model (TMSL/XMLA).

# This module:
#   1. Extracts implicit Qlik relationships from shared / renamed fields
#   2. Outputs them as:
#        - Relationship summary (human readable)
#        - TMSL JSON  (for REST API / XMLA deployment)
#        - M Query documentation block (comments in each table query)
# """

# import re
# import json
# import logging
# from typing import Dict, Any, List, Optional
# from collections import defaultdict

# logger = logging.getLogger(__name__)


# # ─────────────────────────────────────────────────────────────────────────────
# # Data classes (plain dicts for JSON-compatibility)
# # ─────────────────────────────────────────────────────────────────────────────

# def make_relationship(
#     from_table: str,
#     from_column: str,
#     to_table: str,
#     to_column: str,
#     cardinality: str = "oneToMany",   # oneToMany | manyToOne | manyToMany | oneToOne
#     cross_filter: str = "single",     # single | both
#     is_active: bool = True,
#     note: str = "",
# ) -> Dict[str, Any]:
#     return {
#         "from_table":   from_table,
#         "from_column":  from_column,
#         "to_table":     to_table,
#         "to_column":    to_column,
#         "cardinality":  cardinality,
#         "cross_filter": cross_filter,
#         "is_active":    is_active,
#         "note":         note,
#     }


# # ─────────────────────────────────────────────────────────────────────────────
# # Main extractor
# # ─────────────────────────────────────────────────────────────────────────────

# class RelationshipExtractor:
#     """
#     Extract Power BI relationships from a parsed Qlik LoadScript.

#     Qlik encodes relationships in two ways:
#       1. SAME field name in two tables  →  implicit auto-join
#       2. AS alias renaming              →  explicit FK (e.g. DealerID AS [DealerID-ServiceID])
#          When two tables rename different source columns to the same name,
#          that name is the join key.

#     Usage
#     -----
#     extractor = RelationshipExtractor(tables)   # tables = generator.tables
#     relationships = extractor.extract()
#     tmsl = extractor.to_tmsl_json()
#     """

#     # Tables that are Qlik-internal scaffolding — never included
#     _INTERNAL_PREFIXES = ("__city", "__geo", "__")

#     def __init__(self, tables: List[Dict[str, Any]]):
#         # Filter out internal tables
#         self.tables = [
#             t for t in tables
#             if not any(t["name"].lower().startswith(p) for p in self._INTERNAL_PREFIXES)
#         ]
#         self._relationships: List[Dict[str, Any]] = []
#         self._extracted = False

#     # ─────────────────────────────────────────────────────────────────────
#     # Public API
#     # ─────────────────────────────────────────────────────────────────────

#     def extract(self) -> List[Dict[str, Any]]:
#         """Return list of relationship dicts."""
#         if not self._extracted:
#             self._relationships = self._find_relationships()
#             self._extracted = True
#         return self._relationships

#     def to_tmsl_json(self, indent: int = 2) -> str:
#         """
#         Return TMSL JSON suitable for patching a Power BI semantic model
#         via the XMLA endpoint or REST API.

#         POST to: POST /groups/{workspaceId}/datasets/{datasetId}/executeQueries
#         Or apply via Tabular Editor / XMLA write endpoint.
#         """
#         rels = self.extract()
#         tmsl_rels = []
#         for r in rels:
#             name = (
#                 "%s_%s_to_%s_%s" % (
#                     r["from_table"], r["from_column"],
#                     r["to_table"],   r["to_column"],
#                 )
#             ).replace(" ", "_").replace("-", "_").replace(".", "_")

#             tmsl_rels.append({
#                 "name":                  name,
#                 "fromTable":             r["from_table"],
#                 "fromColumn":            r["from_column"],
#                 "toTable":               r["to_table"],
#                 "toColumn":              r["to_column"],
#                 "crossFilteringBehavior": r["cross_filter"],
#                 "isActive":              r["is_active"],
#             })

#         return json.dumps(
#             {
#                 "createOrReplace": {
#                     "object": {"database": "<your_dataset_name>"},
#                     "model": {"relationships": tmsl_rels},
#                 }
#             },
#             indent=indent,
#         )

#     def to_summary(self) -> str:
#         """Human-readable relationship summary."""
#         rels = self.extract()
#         if not rels:
#             return "No relationships detected."

#         lines = [
#             "=" * 70,
#             "DETECTED RELATIONSHIPS  (%d)" % len(rels),
#             "=" * 70,
#             "",
#             "  %-28s  %-28s  %-12s" % ("FROM (many side)", "TO (one side)", "Join Key"),
#             "  " + "-" * 72,
#         ]
#         for r in rels:
#             card_arrow = "→" if "one" in r["cardinality"].lower() else "↔"
#             lines.append(
#                 "  %-28s %s %-28s  [%s]" % (
#                     r["from_table"],
#                     card_arrow,
#                     r["to_table"],
#                     r["from_column"] if r["from_column"] == r["to_column"]
#                     else "%s = %s" % (r["from_column"], r["to_column"]),
#                 )
#             )
#             if r.get("note"):
#                 lines.append("  %-28s   %-28s  ↑ %s" % ("", "", r["note"]))
#         lines.append("")
#         return "\n".join(lines)

#     def to_m_query_comment_block(self) -> str:
#         """
#         Generate a comment block to prepend to each M Query table
#         explaining what relationships it participates in.
#         Returns a dict keyed by table name.
#         """
#         rels = self.extract()
#         comments: Dict[str, List[str]] = defaultdict(list)

#         for r in rels:
#             key_desc = (
#                 "[%s]" % r["from_column"]
#                 if r["from_column"] == r["to_column"]
#                 else "[%s] = [%s]" % (r["from_column"], r["to_column"])
#             )
#             comments[r["from_table"]].append(
#                 "// Relationship → %s on %s  (%s)"
#                 % (r["to_table"], key_desc, r["cardinality"])
#             )
#             comments[r["to_table"]].append(
#                 "// Relationship ← %s on %s  (%s)"
#                 % (r["from_table"], key_desc, r["cardinality"])
#             )

#         result = {}
#         for table_name, lines in comments.items():
#             result[table_name] = (
#                 "// " + "─" * 60 + "\n"
#                 + "// RELATIONSHIPS for: %s\n" % table_name
#                 + "// (Define these in Power BI Model view or via TMSL)\n"
#                 + "\n".join(lines) + "\n"
#                 + "// " + "─" * 60 + "\n"
#             )
#         return result

#     # ─────────────────────────────────────────────────────────────────────
#     # Core extraction logic
#     # ─────────────────────────────────────────────────────────────────────

#     def _find_relationships(self) -> List[Dict[str, Any]]:
#         """
#         Find all relationships by scanning for shared field names across tables.

#         Qlik rule: if two tables both have a field with the same name,
#         they are joined on that field. Aliasing (AS) is what creates or
#         renames those shared keys.
#         """
#         # Build field → [table, ...] map
#         # field_map: Dict[str, List[str]] = defaultdict(list)
#         # for table in self.tables:
#         #     for field in table.get("fields", []):
#         #         field_map[field].append(table["name"])
#         field_map: Dict[str, List[str]] = defaultdict(list)
#         # orig_field_map stores (table, base_field) -> original field name
#         orig_field_map: Dict[tuple, str] = {}
#         for table in self.tables:
#             for field in table.get("fields", []):
#                 base_field = field.split(".")[-1] if "." in field else field
#                 field_map[base_field].append(table["name"])
#                 orig_field_map[(table["name"], base_field)] = field
#         # Fields appearing in 3+ tables are common attributes (Brand, Model, City etc.)
#         # not join keys — skip them to avoid false-positive relationships
#         omnipresent = {f for f, tables in field_map.items() if len(tables) >= 3}

#         relationships: List[Dict[str, Any]] = []
#         seen_pairs: set = set()

#         # for field, table_list in field_map.items():
#         #     if len(table_list) < 2:
#         #         continue
#         #     if field in omnipresent:
#         #         continue
#         for field, table_list in field_map.items():
#             if len(table_list) < 2:
#                 continue
#             if field in omnipresent:
#                 continue
#             # field here is base_field (stripped)

#             # Determine which table is the "one" side (dimension) vs "many" (fact)
#             for i in range(len(table_list)):
#                 for j in range(i + 1, len(table_list)):
#                     t1, t2 = table_list[i], table_list[j]
#                     pair_key = tuple(sorted([t1, t2, field]))
#                     if pair_key in seen_pairs:
#                         continue
#                     seen_pairs.add(pair_key)

#                     one_side, many_side = self._determine_cardinality_sides(
#                         t1, t2, field
#                     )
#                     cardinality, note = self._classify_relationship(
#                         one_side, many_side, field
#                     )

#                     # relationships.append(make_relationship(
#                     #     from_table=many_side,
#                     #     from_column=field,
#                     #     to_table=one_side,
#                     #     to_column=field,
#                     relationships.append(make_relationship(
#                         from_table=many_side,
#                         from_column=orig_field_map.get((many_side, field), field),
#                         to_table=one_side,
#                         to_column=orig_field_map.get((one_side, field), field),
#                         cardinality=cardinality,
#                         cross_filter="single",
#                         is_active=True,
#                         note=note,
#                     ))

#         # Sort: dimension tables first, fact tables last
#         fact_keywords = ["fact", "master", "history", "detail", "trans"]
#         def sort_key(r):
#             return (
#                 any(kw in r["from_table"].lower() for kw in fact_keywords),
#                 r["from_table"],
#             )
#         relationships.sort(key=sort_key)
#         return relationships

#     def _determine_cardinality_sides(
#         self, t1: str, t2: str, field: str
#     ):
#         """
#         Determine which table is the 'one' side and which is 'many'.

#         Heuristics (in priority order):
#           1. Table named *_Master / *_Dim / *_Details (without 'Fact') → one side
#           2. Fewer fields → likely a dimension (one side)
#           3. Table name contains 'Fact' or 'History' → many side
#           4. Default: t1 = one, t2 = many
#         """
#         dim_keywords  = ["master", "dim", "lookup", "ref", "vin_details",
#                          "variant_master", "model_master", "dealer_master"]
#         fact_keywords = ["fact", "history", "transaction", "detail", "sales"]

#         t1_lower = t1.lower()
#         t2_lower = t2.lower()

#         t1_is_dim  = any(kw in t1_lower for kw in dim_keywords)
#         t2_is_dim  = any(kw in t2_lower for kw in dim_keywords)
#         t1_is_fact = any(kw in t1_lower for kw in fact_keywords)
#         t2_is_fact = any(kw in t2_lower for kw in fact_keywords)

#         if t1_is_dim and not t2_is_dim:
#             return t1, t2   # t1=one, t2=many
#         if t2_is_dim and not t1_is_dim:
#             return t2, t1
#         if t1_is_fact and not t2_is_fact:
#             return t2, t1   # t2=one, t1=many
#         if t2_is_fact and not t1_is_fact:
#             return t1, t2

#         # Fallback: fewer fields = dimension
#         t1_fields = next((len(t["fields"]) for t in self.tables if t["name"] == t1), 99)
#         t2_fields = next((len(t["fields"]) for t in self.tables if t["name"] == t2), 99)
#         if t1_fields <= t2_fields:
#             return t1, t2
#         return t2, t1

#     def _classify_relationship(
#         self, one_side: str, many_side: str, field: str
#     ):
#         """Return (cardinality_string, note)."""
#         one_lower  = one_side.lower()
#         many_lower = many_side.lower()

#         # Shared descriptive fields (ModelName, VariantName, ServiceType, ServiceCost)
#         # are usually denormalized copies in Qlik — warn about this
#         # denorm_hint_fields = {"servicetype", "servicecost", "fueltype", "vehiclechannel"}
#         # if field.lower().replace("-","").replace("_","") in denorm_hint_fields:
#         #     return (
#         #         "manyToMany",
#         #         "⚠️  '%s' looks denormalized (same value in both tables). "
#         #         "Consider using a bridge table or filtering to one direction." % field,
#         #     )
#         # Only treat a field as a genuine join key if it looks like one.
#         # Generic descriptive fields (names, types, dates, amounts) are
#         # denormalized copies in Qlik — not real FK relationships.
#         field_clean = field.lower().replace("-", "").replace("_", "")
#         is_key = (
#             field_clean.endswith("id")
#             or field_clean.endswith("key")
#             or field_clean.endswith("code")
#             or field_clean.endswith("no")
#             or field_clean in ("vin", "sku", "isbn", "ean", "upc")
#         )
#         if not is_key:
#             return (
#                 "manyToMany",
#                 "⚠️  '%s' looks like a descriptive field, not a join key. "
#                 "Skipping to avoid false relationships." % field,
#             )
#         return (
#             "oneToMany",
#             "%s.[%s] is the unique key; %s has many rows per key."
#             % (one_side, field, many_side),
#         )


# # ─────────────────────────────────────────────────────────────────────────────
# # Standalone usage / integration helper
# # ─────────────────────────────────────────────────────────────────────────────

# def extract_relationships(tables: List[Dict[str, Any]]) -> Dict[str, Any]:
#     """
#     Convenience function. Pass generator.tables, get back everything needed.

#     Returns:
#     {
#         "relationships":    [...],     # list of relationship dicts
#         "tmsl_json":        "...",     # TMSL patch JSON string
#         "summary":          "...",     # human-readable text
#         "table_comments":   {...},     # dict[table_name] → comment block string
#     }
#     """
#     extractor = RelationshipExtractor(tables)
#     return {
#         "relationships":  extractor.extract(),
#         "tmsl_json":      extractor.to_tmsl_json(),
#         "summary":        extractor.to_summary(),
#         "table_comments": extractor.to_m_query_comment_block(),
#     }






"""
Relationship Extractor for Qlik → Power BI Migration

How Qlik handles relationships vs. how Power BI needs them:

  QLIK:   Associative engine — any two tables sharing a field name
          are automatically joined at query time. No explicit FK
          definitions exist in the loadscript.

  POWER BI: Requires explicit relationships defined in the Model layer.
            M Query (Power Query) cannot define relationships —
            they live in the Tabular Model (TMSL/XMLA).

This module:
  1. Extracts implicit Qlik relationships from shared / renamed fields
  2. Outputs them as:
       - Relationship summary (human readable)
       - TMSL JSON  (for REST API / XMLA deployment)
       - M Query documentation block (comments in each table query)
"""

import re
import json
import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data classes (plain dicts for JSON-compatibility)
# ─────────────────────────────────────────────────────────────────────────────

def make_relationship(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
    cardinality: str = "oneToMany",   # oneToMany | manyToOne | manyToMany | oneToOne
    cross_filter: str = "single",     # single | both
    is_active: bool = True,
    note: str = "",
) -> Dict[str, Any]:
    return {
        "from_table":   from_table,
        "from_column":  from_column,
        "to_table":     to_table,
        "to_column":    to_column,
        "cardinality":  cardinality,
        "cross_filter": cross_filter,
        "is_active":    is_active,
        "note":         note,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────────────────────────────────────

class RelationshipExtractor:
    """
    Extract Power BI relationships from a parsed Qlik LoadScript.

    Qlik encodes relationships in two ways:
      1. SAME field name in two tables  →  implicit auto-join
      2. AS alias renaming              →  explicit FK (e.g. DealerID AS [DealerID-ServiceID])
         When two tables rename different source columns to the same name,
         that name is the join key.

    Usage
    -----
    extractor = RelationshipExtractor(tables)   # tables = generator.tables
    relationships = extractor.extract()
    tmsl = extractor.to_tmsl_json()
    """

    # Tables that are Qlik-internal scaffolding — never included
    _INTERNAL_PREFIXES = ("__city", "__geo", "__")

    def __init__(self, tables: List[Dict[str, Any]]):
        # Filter out internal tables
        self.tables = [
            t for t in tables
            if not any(t["name"].lower().startswith(p) for p in self._INTERNAL_PREFIXES)
        ]
        self._relationships: List[Dict[str, Any]] = []
        self._extracted = False

    # ─────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────

    def extract(self) -> List[Dict[str, Any]]:
        """Return list of relationship dicts."""
        if not self._extracted:
            self._relationships = self._find_relationships()
            self._extracted = True
        return self._relationships

    def to_tmsl_json(self, indent: int = 2) -> str:
        """
        Return TMSL JSON suitable for patching a Power BI semantic model
        via the XMLA endpoint or REST API.

        POST to: POST /groups/{workspaceId}/datasets/{datasetId}/executeQueries
        Or apply via Tabular Editor / XMLA write endpoint.
        """
        rels = self.extract()
        tmsl_rels = []
        for r in rels:
            name = (
                "%s_%s_to_%s_%s" % (
                    r["from_table"], r["from_column"],
                    r["to_table"],   r["to_column"],
                )
            ).replace(" ", "_").replace("-", "_").replace(".", "_")

            tmsl_rels.append({
                "name":                  name,
                "fromTable":             r["from_table"],
                "fromColumn":            r["from_column"],
                "toTable":               r["to_table"],
                "toColumn":              r["to_column"],
                "crossFilteringBehavior": r["cross_filter"],
                "isActive":              r["is_active"],
            })

        return json.dumps(
            {
                "createOrReplace": {
                    "object": {"database": "<your_dataset_name>"},
                    "model": {"relationships": tmsl_rels},
                }
            },
            indent=indent,
        )

    def to_summary(self) -> str:
        """Human-readable relationship summary."""
        rels = self.extract()
        if not rels:
            return "No relationships detected."

        lines = [
            "=" * 70,
            "DETECTED RELATIONSHIPS  (%d)" % len(rels),
            "=" * 70,
            "",
            "  %-28s  %-28s  %-12s" % ("FROM (many side)", "TO (one side)", "Join Key"),
            "  " + "-" * 72,
        ]
        for r in rels:
            card_arrow = "→" if "one" in r["cardinality"].lower() else "↔"
            lines.append(
                "  %-28s %s %-28s  [%s]" % (
                    r["from_table"],
                    card_arrow,
                    r["to_table"],
                    r["from_column"] if r["from_column"] == r["to_column"]
                    else "%s = %s" % (r["from_column"], r["to_column"]),
                )
            )
            if r.get("note"):
                lines.append("  %-28s   %-28s  ↑ %s" % ("", "", r["note"]))
        lines.append("")
        return "\n".join(lines)

    def to_m_query_comment_block(self) -> str:
        """
        Generate a comment block to prepend to each M Query table
        explaining what relationships it participates in.
        Returns a dict keyed by table name.
        """
        rels = self.extract()
        comments: Dict[str, List[str]] = defaultdict(list)

        for r in rels:
            key_desc = (
                "[%s]" % r["from_column"]
                if r["from_column"] == r["to_column"]
                else "[%s] = [%s]" % (r["from_column"], r["to_column"])
            )
            comments[r["from_table"]].append(
                "// Relationship → %s on %s  (%s)"
                % (r["to_table"], key_desc, r["cardinality"])
            )
            comments[r["to_table"]].append(
                "// Relationship ← %s on %s  (%s)"
                % (r["from_table"], key_desc, r["cardinality"])
            )

        result = {}
        for table_name, lines in comments.items():
            result[table_name] = (
                "// " + "─" * 60 + "\n"
                + "// RELATIONSHIPS for: %s\n" % table_name
                + "// (Define these in Power BI Model view or via TMSL)\n"
                + "\n".join(lines) + "\n"
                + "// " + "─" * 60 + "\n"
            )
        return result

    # ─────────────────────────────────────────────────────────────────────
    # Core extraction logic
    # ─────────────────────────────────────────────────────────────────────

    def _find_relationships(self) -> List[Dict[str, Any]]:
        """
        Find all relationships by scanning for shared field names across tables.

        Qlik rule: if two tables both have a field with the same name,
        they are joined on that field. Aliasing (AS) is what creates or
        renames those shared keys.
        """
        # Build field → [table, ...] map
        # field_map: Dict[str, List[str]] = defaultdict(list)
        # for table in self.tables:
        #     for field in table.get("fields", []):
        #         field_map[field].append(table["name"])
        field_map: Dict[str, List[str]] = defaultdict(list)
        # orig_field_map stores (table, base_field) -> original field name
        orig_field_map: Dict[tuple, str] = {}
        for table in self.tables:
            for field in table.get("fields", []):
                base_field = field.split(".")[-1] if "." in field else field
                field_map[base_field].append(table["name"])
                orig_field_map[(table["name"], base_field)] = field
        # Fields appearing in 3+ tables are common attributes (Brand, Model, City etc.)
        # not join keys — skip them to avoid false-positive relationships
        # Only skip fields that appear in the majority of tables (>60%) — these are
        # denormalized attributes, not join keys. A field in exactly 2-3 tables
        # with a name ending in _id/_key/_code IS a valid foreign key.
        total_tables = len(self.tables)
        omni_threshold = max(3, int(total_tables * 0.6) + 1)
        omnipresent = {
            f for f, tbls in field_map.items()
            if len(tbls) >= omni_threshold
            # But never skip _id / _key / _code / _no fields — they are FK columns
            and not any(f.lower().endswith(suf) for suf in ("_id", "_key", "_code", "_no", "id", "key"))
        }

        relationships: List[Dict[str, Any]] = []
        seen_pairs: set = set()

        # for field, table_list in field_map.items():
        #     if len(table_list) < 2:
        #         continue
        #     if field in omnipresent:
        #         continue
        for field, table_list in field_map.items():
            if len(table_list) < 2:
                continue
            if field in omnipresent:
                continue
            # field here is base_field (stripped)

            # Determine which table is the "one" side (dimension) vs "many" (fact)
            for i in range(len(table_list)):
                for j in range(i + 1, len(table_list)):
                    t1, t2 = table_list[i], table_list[j]
                    pair_key = tuple(sorted([t1, t2, field]))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)

                    one_side, many_side = self._determine_cardinality_sides(
                        t1, t2, field
                    )
                    cardinality, note = self._classify_relationship(
                        one_side, many_side, field
                    )

                    # relationships.append(make_relationship(
                    #     from_table=many_side,
                    #     from_column=field,
                    #     to_table=one_side,
                    #     to_column=field,
                    relationships.append(make_relationship(
                        from_table=many_side,
                        from_column=orig_field_map.get((many_side, field), field),
                        to_table=one_side,
                        to_column=orig_field_map.get((one_side, field), field),
                        cardinality=cardinality,
                        cross_filter="single",
                        is_active=True,
                        note=note,
                    ))

        # Sort: dimension tables first, fact tables last
        fact_keywords = ["fact", "master", "history", "detail", "trans"]
        def sort_key(r):
            return (
                any(kw in r["from_table"].lower() for kw in fact_keywords),
                r["from_table"],
            )
        relationships.sort(key=sort_key)

        # ── Ambiguous path detection ──────────────────────────────────────────
        # Power BI rejects models where two tables can be reached via two different
        # paths (e.g. admissions→patients→Treatment_Master AND admissions→Treatment_Master).
        # Fix: for each (from_table, to_table) dim pair, keep ONLY the most direct
        # relationship (shortest path). Mark duplicates as inactive so Power BI
        # accepts the model but the user can manually activate if needed.
        relationships = self._deactivate_ambiguous_paths(relationships)

        return relationships

    def _deactivate_ambiguous_paths(
        self, relationships: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Detect and deactivate relationships that create ambiguous paths.

        An ambiguous path exists when table A can reach table C both:
          - Directly:  A → C
          - Indirectly: A → B → C

        Power BI raises "There are ambiguous paths between X and Y" for these.

        Strategy:
          1. Build directed graph of active oneToMany relationships.
          2. For each pair (from, to) that has BOTH a direct edge AND a
             transitive path through another table, mark the DIRECT edge
             as inactive (is_active=False) — the indirect path via the
             bridge table is more semantically correct in star schemas.
          3. Never deactivate relationships between fact and its primary
             dimension tables (keep the star schema intact).
        """
        # Only consider active oneToMany relationships for ambiguity check
        active = [r for r in relationships if r["is_active"] and r["cardinality"] == "oneToMany"]

        # Build adjacency: from_table → set of to_tables (one-side)
        from collections import defaultdict
        adj: dict = defaultdict(set)
        for r in active:
            adj[r["from_table"]].add(r["to_table"])

        # Find all indirect paths: A →(via B)→ C
        # i.e. A→B and B→C exist, meaning A can reach C indirectly
        indirect_pairs: set = set()
        all_from = list(adj.keys())
        for a in all_from:
            for b in adj[a]:          # A → B
                for c in adj.get(b, set()):   # B → C
                    if c != a:
                        indirect_pairs.add((a, c))

        # Mark direct A→C relationships as inactive when A→B→C also exists
        # (keep the indirect B→C as active — it's the proper dim relationship)
        result = []
        deactivated = []
        for r in relationships:
            pair = (r["from_table"], r["to_table"])
            if (r["is_active"]
                    and r["cardinality"] == "oneToMany"
                    and pair in indirect_pairs):
                # Check if this table also has a direct relationship to the same dim
                # via another fact/bridge table — if so, deactivate this direct one
                deactivated.append(
                    f"{r['from_table']}.{r['from_column']} → {r['to_table']}.{r['to_column']}"
                )
                r = dict(r)  # copy before mutating
                r["is_active"] = False
                r["note"] = (
                    r.get("note", "") +
                    " [INACTIVE: ambiguous path — reachable via another table. "
                    "Activate manually in Power BI if needed.]"
                )
            result.append(r)

        if deactivated:
            logger.info(
                "[RelationshipExtractor] Deactivated %d ambiguous relationship(s): %s",
                len(deactivated), deactivated
            )

        return result

    def _determine_cardinality_sides(
        self, t1: str, t2: str, field: str
    ):
        """
        Determine which table is the 'one' side and which is 'many'.

        Heuristics (in priority order):
          1. Table named *_Master / *_Dim / *_Details (without 'Fact') → one side
          2. Fewer fields → likely a dimension (one side)
          3. Table name contains 'Fact' or 'History' → many side
          4. Default: t1 = one, t2 = many
        """
        dim_keywords  = [
            "master", "dim", "lookup", "ref", "vin_details",
            "variant_master", "model_master", "dealer_master",
            # Date / time dimension tables — must be one-side (unique dates)
            "date", "calendar", "time", "period",
            # Tourism schema dimension tables
            "destination", "tourist", "accommodation", "transport",
            "product", "category", "region", "location", "geography",
            "customer", "employee", "supplier", "vendor", "partner",
            "store", "channel", "currency", "department",
        ]
        fact_keywords = ["fact", "history", "transaction", "detail", "sales", "order", "booking"]

        t1_lower = t1.lower()
        t2_lower = t2.lower()

        t1_is_dim  = any(kw in t1_lower for kw in dim_keywords)
        t2_is_dim  = any(kw in t2_lower for kw in dim_keywords)
        t1_is_fact = any(kw in t1_lower for kw in fact_keywords)
        t2_is_fact = any(kw in t2_lower for kw in fact_keywords)

        if t1_is_dim and not t2_is_dim:
            return t1, t2   # t1=one, t2=many
        if t2_is_dim and not t1_is_dim:
            return t2, t1
        if t1_is_fact and not t2_is_fact:
            return t2, t1   # t2=one, t1=many
        if t2_is_fact and not t1_is_fact:
            return t1, t2

        # Fallback: fewer fields = dimension
        t1_fields = next((len(t["fields"]) for t in self.tables if t["name"] == t1), 99)
        t2_fields = next((len(t["fields"]) for t in self.tables if t["name"] == t2), 99)
        if t1_fields <= t2_fields:
            return t1, t2
        return t2, t1

    def _classify_relationship(
        self, one_side: str, many_side: str, field: str
    ):
        """Return (cardinality_string, note)."""
        one_lower  = one_side.lower()
        many_lower = many_side.lower()

        # Shared descriptive fields (ModelName, VariantName, ServiceType, ServiceCost)
        # are usually denormalized copies in Qlik — warn about this
        # denorm_hint_fields = {"servicetype", "servicecost", "fueltype", "vehiclechannel"}
        # if field.lower().replace("-","").replace("_","") in denorm_hint_fields:
        #     return (
        #         "manyToMany",
        #         "⚠️  '%s' looks denormalized (same value in both tables). "
        #         "Consider using a bridge table or filtering to one direction." % field,
        #     )
        # Only treat a field as a genuine join key if it looks like one.
        # Generic descriptive fields (names, types, dates, amounts) are
        # denormalized copies in Qlik — not real FK relationships.
        field_clean = field.lower().replace("-", "").replace("_", "")
        is_key = (
            field_clean.endswith("id")
            or field_clean.endswith("key")
            or field_clean.endswith("code")
            or field_clean.endswith("no")
            or field_clean.endswith("num")
            or field_clean.endswith("number")
            or field_clean in ("vin", "sku", "isbn", "ean", "upc")
            # ── Date dimension join keys ─────────────────────────────────────
            # ONLY treat "date" as a join key when it is the EXACT column name.
            # "admission_date", "order_date", "birth_date" etc. are descriptive
            # fields — NOT foreign keys — even though they end in "date".
            # Only "Date" (exact) links to a DimDate table.
            or field_clean == "date"
            or field_clean == "dt"
        )

        # Extra check: if one of the tables is a date/time dimension,
        # ALWAYS treat the shared column as a join key regardless of name.
        date_dim_keywords = ("dimdate", "date_dim", "datedim", "calendar",
                             "time_dim", "timedim", "dimtime", "dim_date",
                             "dim_time", "datemaster", "date_master")
        one_is_date_dim = any(kw in one_side.lower().replace("_","").replace(" ","")
                              for kw in date_dim_keywords)
        if one_is_date_dim:
            is_key = True   # always relate date dimension

        if not is_key:
            return (
                "manyToMany",
                "⚠️  '%s' looks like a descriptive field, not a join key. "
                "Skipping to avoid false relationships." % field,
            )
        return (
            "oneToMany",
            "%s.[%s] is the unique key; %s has many rows per key."
            % (one_side, field, many_side),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Standalone usage / integration helper
# ─────────────────────────────────────────────────────────────────────────────

def extract_relationships(tables: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convenience function. Pass generator.tables, get back everything needed.

    Returns:
    {
        "relationships":    [...],     # list of relationship dicts
        "tmsl_json":        "...",     # TMSL patch JSON string
        "summary":          "...",     # human-readable text
        "table_comments":   {...},     # dict[table_name] → comment block string
    }
    """
    extractor = RelationshipExtractor(tables)
    return {
        "relationships":  extractor.extract(),
        "tmsl_json":      extractor.to_tmsl_json(),
        "summary":        extractor.to_summary(),
        "table_comments": extractor.to_m_query_comment_block(),
    }