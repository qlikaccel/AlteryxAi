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
        field_map: Dict[str, List[str]] = defaultdict(list)
        for table in self.tables:
            for field in table.get("fields", []):
                field_map[field].append(table["name"])

        relationships: List[Dict[str, Any]] = []
        seen_pairs: set = set()

        for field, table_list in field_map.items():
            if len(table_list) < 2:
                continue

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

                    relationships.append(make_relationship(
                        from_table=many_side,
                        from_column=field,
                        to_table=one_side,
                        to_column=field,
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
        return relationships

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
        dim_keywords  = ["master", "dim", "lookup", "ref", "vin_details",
                         "variant_master", "model_master", "dealer_master"]
        fact_keywords = ["fact", "history", "transaction", "detail", "sales"]

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
        denorm_hint_fields = {"servicetype", "servicecost", "fueltype", "vehiclechannel"}
        if field.lower().replace("-","").replace("_","") in denorm_hint_fields:
            return (
                "manyToMany",
                "⚠️  '%s' looks denormalized (same value in both tables). "
                "Consider using a bridge table or filtering to one direction." % field,
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
