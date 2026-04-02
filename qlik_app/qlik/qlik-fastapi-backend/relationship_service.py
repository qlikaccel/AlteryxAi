from typing import Any, Dict, List, Optional, Tuple
import logging
import re

logger = logging.getLogger(__name__)


def _sanitize_col_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(value or ""))


def sanitize_rel_columns(
    relationships: List[Dict[str, Any]],
    col_name_map: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Normalize relationship endpoint columns to match sanitized dataset schemas.

    Supported col_name_map shapes:
    1) Flat map: {"orig_col": "safe_col"}
    2) Per-table map: {"TableA": {"orig_col": "safe_col"}, ...}
    """
    if not relationships:
        return []

    is_per_table = any(isinstance(v, dict) for v in (col_name_map or {}).values())
    sanitized: List[Dict[str, Any]] = []

    for rel in relationships:
        out = dict(rel)

        from_table = str(out.get("fromTable", out.get("from_table", "")) or "")
        to_table = str(out.get("toTable", out.get("to_table", "")) or "")

        from_col_raw = str(out.get("fromColumn", out.get("from_column", "")) or "")
        to_col_raw = str(out.get("toColumn", out.get("to_column", "")) or "")

        if is_per_table:
            from_map = col_name_map.get(from_table, {}) if isinstance(col_name_map.get(from_table), dict) else {}
            to_map = col_name_map.get(to_table, {}) if isinstance(col_name_map.get(to_table), dict) else {}
            from_col = from_map.get(from_col_raw, _sanitize_col_name(from_col_raw))
            to_col = to_map.get(to_col_raw, _sanitize_col_name(to_col_raw))
        else:
            from_col = col_name_map.get(from_col_raw, _sanitize_col_name(from_col_raw))
            to_col = col_name_map.get(to_col_raw, _sanitize_col_name(to_col_raw))

        out["fromColumn"] = from_col
        out["toColumn"] = to_col
        out["from_column"] = from_col
        out["to_column"] = to_col
        sanitized.append(out)

    return sanitized


def build_col_name_map_for_tables_m(tables_m: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """Build per-table original->sanitized column map from a tables_m payload."""
    col_name_map_by_table: Dict[str, Dict[str, str]] = {}
    for table in tables_m or []:
        table_name = str(table.get("name", "") or "")
        if not table_name:
            continue

        fields = _extract_field_names(table)
        col_name_map_by_table[table_name] = {
            f: _sanitize_col_name(f)
            for f in fields
            if f and f != "*"
        }
    return col_name_map_by_table


def normalize_table_rows(
    table_name: str,
    rows: List[Dict[str, Any]],
    provided_columns: Optional[List[str]] = None,
) -> Tuple[List[str], Dict[str, str], List[Dict[str, Any]]]:
    """
    Normalize row dictionaries so all rows share a consistent schema.

    Returns:
      - ordered original headers
      - original->sanitized column map
      - sanitized rows with every header present on every row
    """
    headers: List[str] = []
    seen = set()

    for col in (provided_columns or []):
        c = str(col or "").strip()
        if c and c not in seen:
            seen.add(c)
            headers.append(c)

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            k = str(key or "").strip()
            if k and k not in seen:
                seen.add(k)
                headers.append(k)

    col_name_map = {h: _sanitize_col_name(h) for h in headers}
    sanitized_rows: List[Dict[str, Any]] = []

    for row in rows or []:
        out: Dict[str, Any] = {}
        src = row if isinstance(row, dict) else {}
        for h in headers:
            out[col_name_map[h]] = src.get(h)
        sanitized_rows.append(out)

    return headers, col_name_map, sanitized_rows


def resolve_relationships_unified(
    tables_m: List[Dict[str, Any]],
    col_name_map_by_table: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Single source-of-truth for relationship generation + column-name normalization.
    """
    inferred = infer_relationships_unified(tables_m, alias_aware=True)
    if not inferred:
        return []

    effective_map = col_name_map_by_table or build_col_name_map_for_tables_m(tables_m)
    return sanitize_rel_columns(inferred, effective_map)


def _canonical_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _field_alias_candidates(table_name: str, field_name: str) -> List[str]:
    candidates: List[str] = []
    raw = str(field_name or "").strip().strip("[]")
    if not raw:
        return candidates

    candidates.append(raw)

    if "." in raw:
        candidates.append(raw.split(".")[-1])

    table_parts = [p for p in re.split(r"[^a-z0-9]+", str(table_name or "").lower()) if p]
    raw_lower = raw.lower()

    for sep in ("_", "-", "."):
        if table_parts:
            prefix = sep.join(table_parts) + sep
            if raw_lower.startswith(prefix):
                stripped = raw[len(prefix):]
                # Avoid collapsing specific identifiers (e.g. shift_id) into generic id.
                if _canonical_field_name(stripped) not in {"id", "key", "code", "no"}:
                    candidates.append(stripped)

    # Handle common source prefixes that often appear only on fact-side columns.
    # Example: emp_department_name -> department_name
    split_tokens = [t for t in re.split(r"[_\-.]+", raw) if t]
    if len(split_tokens) >= 2 and split_tokens[0].lower() in {
        "emp", "employee", "fact", "dim", "tbl", "stg", "src", "ref"
    }:
        prefixed_stripped = "_".join(split_tokens[1:])
        if prefixed_stripped:
            candidates.append(prefixed_stripped)

    out: List[str] = []
    seen = set()
    for value in candidates:
        key = value.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _extract_field_names(table_obj: Dict[str, Any]) -> List[str]:
    raw_fields = table_obj.get("fields", [])
    if not raw_fields:
        return []

    first = raw_fields[0]
    if isinstance(first, dict):
        return [
            str(f.get("alias") or f.get("name", ""))
            for f in raw_fields
            if f.get("name") and f.get("name") != "*"
        ]

    return [str(f) for f in raw_fields if f and str(f) != "*"]


def _build_alias_lookup(table_name: str, fields: List[str]) -> Dict[str, List[str]]:
    lookup: Dict[str, List[str]] = {}
    for raw_name in fields:
        for candidate in _field_alias_candidates(table_name, raw_name):
            canonical_name = _canonical_field_name(candidate)
            if not canonical_name:
                continue
            values = lookup.setdefault(canonical_name, [])
            if raw_name not in values:
                values.append(raw_name)
    return lookup


def _pick_original_column(candidates: List[str], inferred_column: str) -> str:
    if not candidates:
        return inferred_column
    inferred_lower = str(inferred_column or "").lower()
    candidates_sorted = sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.lower() == inferred_lower else 1,
            0 if candidate.lower().endswith(inferred_lower) else 1,
            len(candidate),
        ),
    )
    return candidates_sorted[0]


def _is_identifier_like(canonical_field: str) -> bool:
    if not canonical_field:
        return False
    return bool(
        canonical_field == "id"
        or canonical_field.endswith("id")
        or canonical_field.endswith("key")
    )


def _source_priority(source: str) -> int:
    priorities = {
        "direct": 0,
        "alias": 1,
        "shared_id": 2,
        "any_field": 3,
    }
    return priorities.get(str(source or ""), 99)


def _is_probable_primary_key(table_name: str, column_name: str) -> bool:
    t = _canonical_field_name(table_name)
    c = _canonical_field_name(column_name)
    if not c:
        return False
    if c == "id":
        return True
    if c in {f"{t}id", f"{t}key"}:
        return True
    return c.endswith("id") and c.startswith(t) and len(c) > len(t)


def _edge_key(rel: Dict[str, Any]) -> tuple:
    a = str(rel.get("fromTable", "")).lower()
    b = str(rel.get("toTable", "")).lower()
    return tuple(sorted([a, b]))


def _pick_best_relationship_per_pair(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_pair: Dict[tuple, Dict[str, Any]] = {}
    for rel in candidates:
        if not rel.get("fromTable") or not rel.get("toTable"):
            continue
        if str(rel.get("fromTable")).lower() == str(rel.get("toTable")).lower():
            continue

        key = _edge_key(rel)
        current = best_by_pair.get(key)
        if current is None:
            best_by_pair[key] = rel
            continue

        rel_rank = (
            _source_priority(str(rel.get("_source", ""))),
            0 if _is_identifier_like(_canonical_field_name(rel.get("fromColumn", ""))) else 1,
            0 if _is_identifier_like(_canonical_field_name(rel.get("toColumn", ""))) else 1,
        )
        current_rank = (
            _source_priority(str(current.get("_source", ""))),
            0 if _is_identifier_like(_canonical_field_name(current.get("fromColumn", ""))) else 1,
            0 if _is_identifier_like(_canonical_field_name(current.get("toColumn", ""))) else 1,
        )
        if rel_rank < current_rank:
            best_by_pair[key] = rel

    return list(best_by_pair.values())


def _orient_relationship(rel: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(rel)
    left_table = str(out.get("fromTable", ""))
    right_table = str(out.get("toTable", ""))
    left_col = str(out.get("fromColumn", ""))
    right_col = str(out.get("toColumn", ""))

    left_pk = _is_probable_primary_key(left_table, left_col)
    right_pk = _is_probable_primary_key(right_table, right_col)

    # Relationship format expects many-side -> one-side.
    if left_pk and not right_pk:
        out["fromTable"] = right_table
        out["fromColumn"] = right_col
        out["toTable"] = left_table
        out["toColumn"] = left_col
    return out


def _connect_disconnected_components(
    selected: List[Dict[str, Any]],
    all_candidates: List[Dict[str, Any]],
    tables_m: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    table_names = [str(t.get("name", "") or "") for t in tables_m if str(t.get("name", "") or "")]
    if len(table_names) <= 1:
        return selected

    def build_components(edges: List[Dict[str, Any]]) -> List[set]:
        adj: Dict[str, set] = {n: set() for n in table_names}
        for e in edges:
            a = str(e.get("fromTable", ""))
            b = str(e.get("toTable", ""))
            if a in adj and b in adj and a != b:
                adj[a].add(b)
                adj[b].add(a)

        seen = set()
        comps: List[set] = []
        for n in table_names:
            if n in seen:
                continue
            stack = [n]
            comp = set()
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                comp.add(cur)
                stack.extend(list(adj.get(cur, set()) - seen))
            comps.append(comp)
        return comps

    selected_by_pair = {_edge_key(r): r for r in selected}
    candidates_by_pair = {_edge_key(r): r for r in _pick_best_relationship_per_pair(all_candidates)}

    while True:
        components = build_components(list(selected_by_pair.values()))
        if len(components) <= 1:
            break

        comp_index: Dict[str, int] = {}
        for idx, comp in enumerate(components):
            for t in comp:
                comp_index[t] = idx

        bridge_options: List[Dict[str, Any]] = []
        for pair_key, cand in candidates_by_pair.items():
            if pair_key in selected_by_pair:
                continue
            a = str(cand.get("fromTable", ""))
            b = str(cand.get("toTable", ""))
            if a not in comp_index or b not in comp_index:
                continue
            if comp_index[a] == comp_index[b]:
                continue
            bridge_options.append(cand)

        if not bridge_options:
            break

        bridge_options.sort(
            key=lambda r: (
                _source_priority(str(r.get("_source", ""))),
                0 if _is_identifier_like(_canonical_field_name(r.get("fromColumn", ""))) else 1,
                0 if _is_identifier_like(_canonical_field_name(r.get("toColumn", ""))) else 1,
            )
        )
        best_bridge = bridge_options[0]
        selected_by_pair[_edge_key(best_bridge)] = best_bridge

    return list(selected_by_pair.values())


def _infer_relationships_shared_id_fallback(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fallback: match on shared ID/key fields only."""
    alias_lookup_by_table: Dict[str, Dict[str, List[str]]] = {}
    table_names: List[str] = []

    for table in tables_m:
        table_name = str(table.get("name", "") or "")
        if not table_name:
            continue
        fields = _extract_field_names(table)
        alias_lookup_by_table[table_name] = _build_alias_lookup(table_name, fields)
        table_names.append(table_name)

    fallback: List[Dict[str, Any]] = []
    for i in range(len(table_names)):
        left = table_names[i]
        left_lookup = alias_lookup_by_table.get(left, {})
        if not left_lookup:
            continue

        for j in range(i + 1, len(table_names)):
            right = table_names[j]
            right_lookup = alias_lookup_by_table.get(right, {})
            if not right_lookup:
                continue

            shared = sorted(set(left_lookup.keys()) & set(right_lookup.keys()))
            for canonical in shared:
                if not _is_identifier_like(canonical):
                    continue

                from_column = _pick_original_column(left_lookup.get(canonical, []), canonical)
                to_column = _pick_original_column(right_lookup.get(canonical, []), canonical)

                fallback.append(
                    {
                        "fromTable": left,
                        "fromColumn": from_column,
                        "toTable": right,
                        "toColumn": to_column,
                        "cardinality": "ManyToOne",
                        "crossFilteringBehavior": "Single",
                    }
                )

    return fallback


def _infer_relationships_any_field_match_fallback(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Comprehensive fallback: match on ANY shared field (like Summary page ER diagram).
    
    This ensures Power BI gets all relationships that Qlik Summary page shows.
    - Finds ANY field name match (case-insensitive)
    - One relationship per table pair (using first matching field for join key)
    - Prefers ID/key fields for the join key
    - Uses simple ManyToOne cardinality (will be refined by dedup/normalizer)
    """
    fallback: List[Dict[str, Any]] = []
    alias_lookup_by_table: Dict[str, Dict[str, List[str]]] = {}
    table_names: List[str] = []

    for table in tables_m:
        table_name = str(table.get("name", "") or "")
        if not table_name:
            continue
        fields = _extract_field_names(table)
        alias_lookup_by_table[table_name] = _build_alias_lookup(table_name, fields)
        table_names.append(table_name)

    if len(table_names) < 2:
        return []

    seen_pairs: set = set()
    generic_aliases = {"id", "key", "code", "no"}

    for i in range(len(table_names)):
        for j in range(i + 1, len(table_names)):
            table_a = table_names[i]
            table_b = table_names[j]
            pair_key = tuple(sorted([table_a, table_b]))
            if pair_key in seen_pairs:
                continue

            lookup_a = alias_lookup_by_table.get(table_a, {})
            lookup_b = alias_lookup_by_table.get(table_b, {})
            if not lookup_a or not lookup_b:
                continue

            shared_fields = sorted(set(lookup_a.keys()) & set(lookup_b.keys()))
            if not shared_fields:
                continue

            # Prefer specific aliases over generic tokens that create false links.
            specific_shared = [f for f in shared_fields if f not in generic_aliases]
            candidates = specific_shared if specific_shared else shared_fields

            identifier_candidates = [f for f in candidates if _is_identifier_like(f)]
            best_field = identifier_candidates[0] if identifier_candidates else candidates[0]

            seen_pairs.add(pair_key)

            col_a = _pick_original_column(lookup_a.get(best_field, []), best_field)
            col_b = _pick_original_column(lookup_b.get(best_field, []), best_field)

            if table_a < table_b:
                many_table, many_col = table_a, col_a
                one_table, one_col = table_b, col_b
            else:
                many_table, many_col = table_b, col_b
                one_table, one_col = table_a, col_a

            fallback.append(
                {
                    "fromTable": many_table,
                    "fromColumn": many_col,
                    "toTable": one_table,
                    "toColumn": one_col,
                    "cardinality": "ManyToOne",
                    "crossFilteringBehavior": "Single",
                }
            )

    return fallback


def _is_summary_like_table(table_name: str) -> bool:
    name = str(table_name or "").lower()
    return any(token in name for token in ("summary", "rollup", "aggregate", "snapshot", "agg"))


def _is_fact_like_table(table_name: str) -> bool:
    name = str(table_name or "").lower()
    return any(token in name for token in ("fact", "activity", "transaction", "detail", "event", "history", "log"))


def _relationship_key_fields(rel: Dict[str, Any]) -> set:
    return {
        _canonical_field_name(rel.get("fromColumn", "")),
        _canonical_field_name(rel.get("toColumn", "")),
    } - {""}


def _prune_powerbi_ambiguous_paths(relationships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove Power BI-invalid direct edges to summary-like tables when an equivalent
    path already exists through a base table on the same key.

    Example pruned pattern:
      Final_Activity -> Employees
      Employees -> Employee_Summary
      Final_Activity -> Employee_Summary   # removed
    """
    if not relationships:
        return []

    pruned: List[Dict[str, Any]] = []
    for rel in relationships:
        from_table = str(rel.get("fromTable", "") or "")
        to_table = str(rel.get("toTable", "") or "")

        if not (_is_summary_like_table(from_table) or _is_summary_like_table(to_table)):
            pruned.append(rel)
            continue

        summary_table = from_table if _is_summary_like_table(from_table) else to_table
        other_table = to_table if summary_table == from_table else from_table
        if not _is_fact_like_table(other_table):
            pruned.append(rel)
            continue
        direct_keys = _relationship_key_fields(rel)
        should_prune = False

        for rel_a in relationships:
            a_from = str(rel_a.get("fromTable", "") or "")
            a_to = str(rel_a.get("toTable", "") or "")
            if summary_table in {a_from, a_to}:
                continue
            if other_table not in {a_from, a_to}:
                continue

            intermediate = a_to if a_from == other_table else a_from
            if not intermediate or intermediate == summary_table:
                continue

            a_keys = _relationship_key_fields(rel_a)
            if direct_keys and a_keys and direct_keys.isdisjoint(a_keys):
                continue

            for rel_b in relationships:
                b_from = str(rel_b.get("fromTable", "") or "")
                b_to = str(rel_b.get("toTable", "") or "")
                if {intermediate, summary_table} != {b_from, b_to}:
                    continue

                b_keys = _relationship_key_fields(rel_b)
                if direct_keys and b_keys and direct_keys.isdisjoint(b_keys):
                    continue

                logger.warning(
                    "[relationship_prune] Removing Power BI-ambiguous edge: %s.%s -> %s.%s via %s",
                    from_table,
                    rel.get("fromColumn", ""),
                    to_table,
                    rel.get("toColumn", ""),
                    intermediate,
                )
                should_prune = True
                break

            if should_prune:
                break

        if not should_prune:
            pruned.append(rel)

    return pruned


def _prune_shortcut_fact_dimension_paths(relationships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove direct fact<->dimension edges when an alternate path already connects
    the same endpoints via other dimension tables.

    This prevents Power BI model import failures caused by ambiguous paths such as:
      Fact -> Model and Fact -> Variant -> Model
    """
    if len(relationships) < 3:
        return relationships

    rels = list(relationships)

    def _has_alternate_path(cur_rels: List[Dict[str, Any]], skip_idx: int, max_depth: int = 4) -> bool:
        start = str(cur_rels[skip_idx].get("fromTable", "") or "")
        end = str(cur_rels[skip_idx].get("toTable", "") or "")
        if not start or not end or start == end:
            return False

        adjacency: Dict[str, set] = {}
        for idx, rel in enumerate(cur_rels):
            if idx == skip_idx:
                continue
            left = str(rel.get("fromTable", "") or "")
            right = str(rel.get("toTable", "") or "")
            if not left or not right or left == right:
                continue
            adjacency.setdefault(left, set()).add(right)
            adjacency.setdefault(right, set()).add(left)

        queue: List[tuple] = [(start, 0)]
        seen = {start}
        while queue:
            node, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for nxt in adjacency.get(node, set()):
                if nxt == end:
                    return True
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue.append((nxt, depth + 1))
        return False

    def _removal_priority(rel: Dict[str, Any]) -> tuple:
        from_col = str(rel.get("fromColumn", "") or "")
        to_col = str(rel.get("toColumn", "") or "")
        canon_from = _canonical_field_name(from_col)
        canon_to = _canonical_field_name(to_col)
        return (
            1 if canon_from != canon_to else 0,  # Prefer removing suspicious mismatched-key joins.
            1 if not _is_identifier_like(canon_from) else 0,
            1 if not _is_identifier_like(canon_to) else 0,
        )

    # Iteratively break cycles by removing one weakest shortcut at a time.
    while True:
        candidates: List[int] = []
        for idx in range(len(rels)):
            if _has_alternate_path(rels, idx):
                candidates.append(idx)

        if not candidates:
            break

        drop_idx = sorted(candidates, key=lambda i: _removal_priority(rels[i]), reverse=True)[0]
        drop_rel = rels[drop_idx]
        logger.warning(
            "[relationship_prune] Removing cycle shortcut edge to avoid Power BI ambiguity: %s.%s -> %s.%s",
            drop_rel.get("fromTable", ""),
            drop_rel.get("fromColumn", ""),
            drop_rel.get("toTable", ""),
            drop_rel.get("toColumn", ""),
        )
        rels.pop(drop_idx)

    return rels


def _remove_ambiguous_relationships(relationships: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not relationships:
        return []

    seen = set()
    unique_rels = []
    for rel in relationships:
        key = (rel["fromTable"], rel["fromColumn"], rel["toTable"], rel["toColumn"])
        if key not in seen:
            seen.add(key)
            unique_rels.append(rel)
        else:
            logger.warning("[relationship_dedup] DUPLICATE REMOVED: %s.%s -> %s.%s", *key)

    pruned_summary = _prune_powerbi_ambiguous_paths(unique_rels)
    return _prune_shortcut_fact_dimension_paths(pruned_summary)


def _infer_relationships_base(tables_m: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from relationship_extractor import RelationshipExtractor
    from relationship_normalizer import RelationshipNormalizer

    extractor_tables = []
    for table in tables_m:
        extractor_tables.append(
            {
                "name": table.get("name", ""),
                "source_type": table.get("source_type", ""),
                "fields": _extract_field_names(table),
            }
        )

    extractor = RelationshipExtractor(extractor_tables)
    raw_rels = extractor.extract()
    valid_rels = [r for r in raw_rels if r.get("cardinality") != "manyToMany"]
    if not valid_rels:
        return []

    normalized = RelationshipNormalizer.normalize_list(valid_rels)
    bim_rels = [
        {
            "fromTable": n.from_table,
            "fromColumn": n.from_column,
            "toTable": n.to_table,
            "toColumn": n.to_column,
            "cardinality": n.cardinality,
            "crossFilteringBehavior": n.direction,
        }
        for n in normalized
    ]
    return _remove_ambiguous_relationships(bim_rels)


def infer_relationships_unified(
    tables_m: List[Dict[str, Any]],
    alias_aware: bool = True,
) -> List[Dict[str, Any]]:
    direct = _infer_relationships_base(tables_m)
    if not alias_aware:
        return direct

    alias_lookup_by_table: Dict[str, Dict[str, List[str]]] = {}
    normalized_tables_m: List[Dict[str, Any]] = []
    for table in tables_m:
        table_name = table.get("name", "")
        fields = _extract_field_names(table)
        alias_lookup = _build_alias_lookup(table_name, fields)
        alias_lookup_by_table[table_name] = alias_lookup
        normalized_tables_m.append(
            {
                "name": table_name,
                "fields": list(alias_lookup.keys()),
                "source_type": table.get("source_type", ""),
            }
        )

    fallback = _infer_relationships_base(normalized_tables_m)

    remapped_fallback: List[Dict[str, Any]] = []
    for rel in fallback:
        from_table = rel.get("fromTable", "")
        to_table = rel.get("toTable", "")
        from_column = rel.get("fromColumn", "")
        to_column = rel.get("toColumn", "")

        from_lookup = alias_lookup_by_table.get(from_table, {})
        to_lookup = alias_lookup_by_table.get(to_table, {})

        mapped_from = _pick_original_column(from_lookup.get(_canonical_field_name(from_column), []), from_column)
        mapped_to = _pick_original_column(to_lookup.get(_canonical_field_name(to_column), []), to_column)

        remapped = dict(rel)
        remapped["fromColumn"] = mapped_from
        remapped["toColumn"] = mapped_to
        remapped_fallback.append(remapped)

    shared_id_fallback = _infer_relationships_shared_id_fallback(tables_m)
    any_field_match_fallback = _infer_relationships_any_field_match_fallback(tables_m)

    tagged_candidates: List[Dict[str, Any]] = []
    for rel in direct:
        tagged = dict(rel)
        tagged["_source"] = "direct"
        tagged_candidates.append(_orient_relationship(tagged))
    for rel in remapped_fallback:
        tagged = dict(rel)
        tagged["_source"] = "alias"
        tagged_candidates.append(_orient_relationship(tagged))
    for rel in shared_id_fallback:
        tagged = dict(rel)
        tagged["_source"] = "shared_id"
        tagged_candidates.append(_orient_relationship(tagged))
    for rel in any_field_match_fallback:
        tagged = dict(rel)
        tagged["_source"] = "any_field"
        tagged_candidates.append(_orient_relationship(tagged))

    best_per_pair = _pick_best_relationship_per_pair(tagged_candidates)
    completed = _connect_disconnected_components(best_per_pair, tagged_candidates, tables_m)

    cleaned: List[Dict[str, Any]] = []
    for rel in completed:
        out = dict(rel)
        out.pop("_source", None)
        cleaned.append(out)

    return _remove_ambiguous_relationships(cleaned)
