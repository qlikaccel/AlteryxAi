from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from qlik_client import QlikClient


@dataclass
class ExtractedRelationship:
    from_table: str
    from_field: str
    to_table: str
    to_field: str
    source: str
    confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_table": self.from_table,
            "from_field": self.from_field,
            "to_table": self.to_table,
            "to_field": self.to_field,
            "source": self.source,
            "confidence": self.confidence,
        }


def _normalize_name(name: str) -> str:
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_key_like(name: str) -> bool:
    n = name.lower()
    return n.endswith("id") or "_id" in n or n.startswith("id")


def _split_qualified_field(raw: str) -> Tuple[Optional[str], str]:
    if not raw:
        return None, ""
    cleaned = raw.strip().strip("[]")
    if "." in cleaned:
        parts = cleaned.split(".", 1)
        return parts[0].strip("[]"), parts[1].strip("[]")
    return None, cleaned


def _extract_from_script(script_content: str) -> List[ExtractedRelationship]:
    rels: List[ExtractedRelationship] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    # Pattern A: Left Join (TargetTable) ... Resident SourceTable;
    join_resident = re.compile(
        r"(?:left|right|inner|outer)?\s*join\s*\(([^\)]+)\).*?resident\s+([^;\n]+)",
        re.IGNORECASE | re.DOTALL,
    )

    # Pattern B: LOAD a, b, c FROM/RESIDENT ... ; infer shared field names
    load_block = re.compile(
        r"(?:(\w+)\s*:\s*)?load\s+(.*?)\s+(?:resident|from)\s+[^;]+;",
        re.IGNORECASE | re.DOTALL,
    )

    for m in join_resident.finditer(script_content or ""):
        target_table = m.group(1).strip().strip("[]")
        source_table = m.group(2).strip().strip("[]")

        if target_table and source_table:
            key = (source_table, "<join-key>", target_table, "<join-key>")
            if key not in seen:
                seen.add(key)
                rels.append(
                    ExtractedRelationship(
                        from_table=source_table,
                        from_field="<join-key>",
                        to_table=target_table,
                        to_field="<join-key>",
                        source="qlik_script_join",
                        confidence=0.75,
                    )
                )

    # Build table->fields map from LOAD statements
    table_fields: Dict[str, Set[str]] = defaultdict(set)
    for m in load_block.finditer(script_content or ""):
        table_name = (m.group(1) or "").strip().strip("[]")
        field_block = m.group(2) or ""
        if not table_name:
            continue

        for token in field_block.split(","):
            candidate = token.strip()
            # Drop aliases "A as B" -> use B
            alias = re.search(r"\bas\s+([\[\]\w\.]+)$", candidate, re.IGNORECASE)
            if alias:
                candidate = alias.group(1)

            _, col = _split_qualified_field(candidate)
            if col:
                table_fields[table_name].add(col)

    table_names = list(table_fields.keys())
    for i in range(len(table_names)):
        for j in range(i + 1, len(table_names)):
            t1, t2 = table_names[i], table_names[j]
            shared = table_fields[t1].intersection(table_fields[t2])
            for fld in shared:
                # prefer key-like shared fields to reduce noise
                if not _is_key_like(fld):
                    continue
                key = (t1, fld, t2, fld)
                if key in seen:
                    continue
                seen.add(key)
                rels.append(
                    ExtractedRelationship(
                        from_table=t1,
                        from_field=fld,
                        to_table=t2,
                        to_field=fld,
                        source="qlik_script_shared_field",
                        confidence=0.8,
                    )
                )

    return rels


def _extract_from_qlik_api(app_id: str, api_key: Optional[str]) -> List[ExtractedRelationship]:
    rels: List[ExtractedRelationship] = []
    seen: Set[Tuple[str, str, str, str]] = set()

    # If api_key is provided we override env for this call scope
    kwargs: Dict[str, Any] = {}
    if api_key:
        kwargs["api_key"] = api_key

    client = QlikClient(**kwargs)

    # Try app metadata endpoint first
    app_meta = client.get_application_data(app_id)
    if isinstance(app_meta, dict):
        possible_fields = app_meta.get("fields") or app_meta.get("qFields") or []
        if isinstance(possible_fields, list):
            table_field_map: Dict[str, Set[str]] = defaultdict(set)
            for f in possible_fields:
                if not isinstance(f, dict):
                    continue
                table = f.get("table") or f.get("tableName") or f.get("qTable")
                field = f.get("name") or f.get("field") or f.get("qName")
                if table and field:
                    table_field_map[str(table)].add(str(field))

            tables = list(table_field_map.keys())
            for i in range(len(tables)):
                for j in range(i + 1, len(tables)):
                    t1, t2 = tables[i], tables[j]
                    shared = table_field_map[t1].intersection(table_field_map[t2])
                    for fld in shared:
                        if not _is_key_like(fld):
                            continue
                        key = (t1, fld, t2, fld)
                        if key in seen:
                            continue
                        seen.add(key)
                        rels.append(
                            ExtractedRelationship(
                                from_table=t1,
                                from_field=fld,
                                to_table=t2,
                                to_field=fld,
                                source="qlik_api_shared_field",
                                confidence=0.7,
                            )
                        )

    return rels


def _dedupe_relationships(rels: List[ExtractedRelationship]) -> List[ExtractedRelationship]:
    # Keep highest confidence for same endpoint pair regardless of direction
    best: Dict[Tuple[str, str, str, str], ExtractedRelationship] = {}

    for r in rels:
        a = (r.from_table, r.from_field, r.to_table, r.to_field)
        b = (r.to_table, r.to_field, r.from_table, r.from_field)
        key = a if _normalize_name("|".join(a)) <= _normalize_name("|".join(b)) else b

        current = best.get(key)
        if current is None or r.confidence > current.confidence:
            best[key] = r

    return list(best.values())


def extract_relationships_from_qlik(
    app_id: Optional[str] = None,
    script_content: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Extract candidate relationships from Qlik metadata and/or load script.
    """
    all_relationships: List[ExtractedRelationship] = []
    methods: List[str] = []

    if app_id:
        try:
            rels_api = _extract_from_qlik_api(app_id=app_id, api_key=api_key)
            if rels_api:
                methods.append("qlik_api")
                all_relationships.extend(rels_api)
        except Exception as exc:
            methods.append(f"qlik_api_failed:{exc}")

    if script_content:
        rels_script = _extract_from_script(script_content)
        if rels_script:
            methods.append("qlik_script")
            all_relationships.extend(rels_script)

    deduped = _dedupe_relationships(all_relationships)

    source_counts: Dict[str, int] = {}
    for r in deduped:
        source_counts[r.source] = source_counts.get(r.source, 0) + 1

    return {
        "relationships": [r.to_dict() for r in deduped],
        "total_count": len(deduped),
        "extraction_methods": methods,
        "pattern_analysis": {
            "source_counts": source_counts,
            "key_like_ratio": (
                round(
                    sum(1 for r in deduped if _is_key_like(r.from_field) or _is_key_like(r.to_field))
                    / len(deduped),
                    2,
                )
                if deduped
                else 0.0
            ),
        },
    }
