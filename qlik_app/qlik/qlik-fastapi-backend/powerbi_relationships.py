


"""
powerbi_relationships.py
Applies table relationships to an existing Power BI dataset and provides
utilities to verify them and export an ER diagram.

WHY THIS FILE EXISTS
--------------------
The Power BI Push-dataset API (POST /datasets) accepts a `relationships`
array at *creation* time, but silently drops them if the dataset already
exists or if any column name doesn't match exactly.

The Fabric Items API (POST /semanticModels) embeds relationships in the
BIM JSON — those work, but only on Premium/Fabric workspaces.

This module handles:
  1. POST-creation relationship patching via
        PUT  /groups/{wid}/datasets/{did}/relationships          (Update)
        POST /groups/{wid}/datasets/{did}/relationships          (Create)
     These are the only REST endpoints that reliably add/update relationships
     on an already-published Push or Import dataset.

  2. Verification — GET back the relationships and compare.

  3. ER-diagram JSON export (nodes + edges) ready for any front-end renderer
     such as the matplotlib diagram already in main.py.

RELATIONSHIP DICT FORMAT (input / output)
------------------------------------------
Every relationship dict accepted or returned by this module uses snake_case:

    {
        "from_table":  str,    # many side
        "from_column": str,
        "to_table":    str,    # one side
        "to_column":   str,
        "name":        str,    # optional, auto-generated if absent
        "cardinality": str,    # "ManyToOne" | "OneToMany" | "OneToOne" | "ManyToMany"
                               # defaults to "ManyToOne"
        "cross_filter_direction": str,  # "Both" | "Single"  defaults to "Single"
        "is_active":   bool,   # defaults to True
    }

The Power BI REST API uses camelCase; conversion is handled internally.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

VALID_CARDINALITIES = {"ManyToOne", "OneToMany", "OneToOne", "ManyToMany"}
VALID_CROSS_FILTERS = {"Both", "Single"}

# Power BI REST API base
_PBI_BASE = "https://api.powerbi.com/v1.0/myorg"

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _auto_name(rel: Dict[str, Any]) -> str:
    """Generate a stable relationship name from endpoint fields."""
    return (
        f"{rel.get('from_table', '')}_{rel.get('from_column', '')}"
        f"_to_{rel.get('to_table', '')}_{rel.get('to_column', '')}"
    ).replace(" ", "_").replace("-", "_")


def _to_rest_payload(rel: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a snake_case relationship dict to the Power BI REST API camelCase
    payload expected by POST/PUT /datasets/{id}/relationships.
    """
    cardinality = rel.get("cardinality", "ManyToOne")
    if cardinality not in VALID_CARDINALITIES:
        cardinality = "ManyToOne"

    cross_filter = rel.get("cross_filter_direction", rel.get("crossFilteringBehavior", "Single"))
    # Normalise REST vs BIM spelling variants
    if cross_filter in ("oneDirection", "singleDirection"):
        cross_filter = "Single"
    elif cross_filter in ("bothDirections", "both"):
        cross_filter = "Both"
    if cross_filter not in VALID_CROSS_FILTERS:
        cross_filter = "Single"

    name = rel.get("name") or rel.get("relationship_name") or _auto_name(rel)

    return {
        "name": str(name),
        "fromTable": str(rel.get("from_table") or rel.get("fromTable", "")),
        "fromColumn": str(rel.get("from_column") or rel.get("fromColumn", "")),
        "toTable": str(rel.get("to_table") or rel.get("toTable", "")),
        "toColumn": str(rel.get("to_column") or rel.get("toColumn", "")),
        "cardinality": cardinality,
        "crossFilteringBehavior": cross_filter,  # REST API spelling
        "isActive": bool(rel.get("is_active", True)),
    }


def _validate_rel(rel: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (valid, reason). All four endpoint fields must be non-empty."""
    for field in ("from_table", "from_column", "to_table", "to_column"):
        val = rel.get(field) or rel.get(
            {"from_table": "fromTable", "from_column": "fromColumn",
             "to_table": "toTable", "to_column": "toColumn"}[field], ""
        )
        if not str(val).strip():
            return False, f"Missing required field: {field}"
    return True, ""


# ---------------------------------------------------------------------------
# Core: apply relationships to an existing dataset
# ---------------------------------------------------------------------------

def apply_relationships(
    workspace_id: str,
    dataset_id: str,
    relationships: List[Dict[str, Any]],
    access_token: str,
    *,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """
    Apply a list of relationships to an already-published Power BI dataset.

    Strategy
    --------
    1. Fetch existing relationships from the dataset.
    2. For each input relationship:
       a. If it already exists (by name or same endpoint pair) and overwrite=True
          → DELETE then re-POST (the REST API has no PATCH for relationships).
       b. If it doesn't exist → POST.
    3. Return a detailed result dict.

    Args:
        workspace_id:  Power BI workspace GUID.
        dataset_id:    Target dataset/semantic-model GUID.
        relationships: List of relationship dicts (snake_case format).
        access_token:  Azure AD bearer token.
        overwrite:     When True, existing relationships with the same name
                       or endpoint pair are deleted and re-created.

    Returns:
        {
            "success":   bool,
            "created":   int,
            "updated":   int,
            "skipped":   int,
            "errors":    [str, ...],
            "details":   [{name, status, error?}, ...],
        }
    """
    hdrs = _headers(access_token)
    base = f"{_PBI_BASE}/groups/{workspace_id}/datasets/{dataset_id}/relationships"

    # ── Step 1: fetch existing relationships ─────────────────────────────
    existing: Dict[str, Any] = {}  # name → REST payload
    try:
        r = requests.get(base, headers=hdrs, timeout=15)
        if r.ok:
            for item in r.json().get("value", []):
                existing[item.get("name", "")] = item
            logger.info("[Rels] %d existing relationships fetched", len(existing))
        else:
            logger.warning("[Rels] Could not fetch existing relationships: %d %s",
                           r.status_code, r.text[:200])
    except Exception as exc:
        logger.warning("[Rels] Fetch error: %s", exc)

    # ── Step 2: process each relationship ────────────────────────────────
    created = updated = skipped = 0
    errors: List[str] = []
    details: List[Dict[str, Any]] = []

    for rel in relationships:
        valid, reason = _validate_rel(rel)
        if not valid:
            msg = f"Skipped invalid relationship ({reason}): {rel}"
            logger.warning("[Rels] %s", msg)
            errors.append(msg)
            details.append({"name": str(rel), "status": "skipped", "error": reason})
            skipped += 1
            continue

        payload = _to_rest_payload(rel)
        name = payload["name"]

        # Check if an equivalent relationship already exists
        existing_match = _find_existing(existing, payload)

        if existing_match and not overwrite:
            logger.info("[Rels] Skipping existing relationship: %s", name)
            details.append({"name": name, "status": "skipped_existing"})
            skipped += 1
            continue

        if existing_match:
            # Delete before re-creating (no PATCH endpoint)
            del_name = existing_match.get("name", name)
            del_url = f"{base}/{del_name}"
            try:
                dr = requests.delete(del_url, headers=hdrs, timeout=15)
                if not dr.ok:
                    logger.warning("[Rels] Delete failed (%d) for '%s': %s",
                                   dr.status_code, del_name, dr.text[:200])
            except Exception as de:
                logger.warning("[Rels] Delete error for '%s': %s", del_name, de)

        # POST the (new/updated) relationship
        try:
            pr = requests.post(base, headers=hdrs, json=payload, timeout=15)
            if pr.ok or pr.status_code in (200, 201):
                if existing_match:
                    updated += 1
                    details.append({"name": name, "status": "updated"})
                    logger.info("[Rels] ✓ Updated: %s", name)
                else:
                    created += 1
                    details.append({"name": name, "status": "created"})
                    logger.info("[Rels] ✓ Created: %s", name)
            else:
                err_msg = f"POST failed ({pr.status_code}): {pr.text[:300]}"
                logger.warning("[Rels] ✗ %s → %s", name, err_msg)
                errors.append(f"{name}: {err_msg}")
                details.append({"name": name, "status": "error", "error": err_msg})
        except Exception as exc:
            err_msg = str(exc)
            logger.exception("[Rels] POST exception for '%s'", name)
            errors.append(f"{name}: {err_msg}")
            details.append({"name": name, "status": "error", "error": err_msg})

    success = len(errors) == 0
    logger.info("[Rels] Done — created=%d updated=%d skipped=%d errors=%d",
                created, updated, skipped, len(errors))

    return {
        "success":     success,
        "created":     created,
        "updated":     updated,
        "skipped":     skipped,
        "errors":      errors,
        "details":     details,
        "total_input": len(relationships),
    }


def _find_existing(
    existing: Dict[str, Any], payload: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    Return the first existing relationship that matches by name OR by the
    same (fromTable, fromColumn, toTable, toColumn) endpoint pair
    (in either direction).
    """
    name = payload["name"]
    if name in existing:
        return existing[name]

    ft = payload["fromTable"].lower()
    fc = payload["fromColumn"].lower()
    tt = payload["toTable"].lower()
    tc = payload["toColumn"].lower()

    for item in existing.values():
        a = (item.get("fromTable", "").lower(), item.get("fromColumn", "").lower(),
             item.get("toTable", "").lower(),   item.get("toColumn", "").lower())
        b = (item.get("toTable", "").lower(),   item.get("toColumn", "").lower(),
             item.get("fromTable", "").lower(), item.get("fromColumn", "").lower())
        if (ft, fc, tt, tc) in (a, b):
            return item
    return None


# ---------------------------------------------------------------------------
# Verify: read back and compare
# ---------------------------------------------------------------------------

def verify_relationships(
    workspace_id: str,
    dataset_id: str,
    expected_relationships: List[Dict[str, Any]],
    access_token: str,
) -> Dict[str, Any]:
    """
    Read the dataset's relationships from Power BI and compare against
    expected_relationships.

    Returns:
        {
            "success":   bool,           # True if all expected ones are found
            "found":     [name, ...],    # relationships present in Power BI
            "missing":   [name, ...],    # expected but not found
            "extra":     [name, ...],    # in Power BI but not expected
            "raw":       [{...}, ...],   # all relationships from Power BI
        }
    """
    hdrs = _headers(access_token)
    url = f"{_PBI_BASE}/groups/{workspace_id}/datasets/{dataset_id}/relationships"

    try:
        r = requests.get(url, headers=hdrs, timeout=15)
        if not r.ok:
            return {
                "success": False,
                "error": f"GET /relationships failed: {r.status_code} {r.text[:200]}",
            }
        raw = r.json().get("value", [])
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Index by endpoint pair (canonical lower-case)
    def _key(d):
        return (
            d.get("fromTable", d.get("from_table", "")).lower(),
            d.get("fromColumn", d.get("from_column", "")).lower(),
            d.get("toTable", d.get("to_table", "")).lower(),
            d.get("toColumn", d.get("to_column", "")).lower(),
        )

    raw_keys = {_key(item) for item in raw}
    exp_keys = {_key(rel) for rel in expected_relationships}

    found_keys   = raw_keys & exp_keys
    missing_keys = exp_keys - raw_keys
    extra_keys   = raw_keys - exp_keys

    def _fmt(keys):
        return [f"{k[0]}.{k[1]} → {k[2]}.{k[3]}" for k in keys]

    return {
        "success": len(missing_keys) == 0,
        "found":   _fmt(found_keys),
        "missing": _fmt(missing_keys),
        "extra":   _fmt(extra_keys),
        "raw":     raw,
    }


# ---------------------------------------------------------------------------
# ER Diagram data export
# ---------------------------------------------------------------------------

def build_er_diagram_data(
    relationships: List[Dict[str, Any]],
    tables: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build a JSON structure (nodes + edges) that describes the ER diagram.

    This is consumed by:
      - The matplotlib ``build_er_diagram()`` function in main.py
      - Any front-end graph library (e.g. React Flow, D3, Cytoscape)

    Args:
        relationships: List of relationship dicts (snake_case).
        tables:        Optional list of table dicts with ``name`` and
                       ``fields`` keys — used to enrich node metadata.

    Returns:
        {
            "nodes": [
                {
                    "id":     "TableName",
                    "label":  "TableName",
                    "fields": ["col1", "col2", ...],   # from tables arg
                },
                ...
            ],
            "edges": [
                {
                    "id":         "rel_name",
                    "source":     "FromTable",
                    "target":     "ToTable",
                    "sourceField":"from_column",
                    "targetField":"to_column",
                    "cardinality":"ManyToOne",
                    "label":      "FromTable.col → ToTable.col",
                    "is_active":  True,
                },
                ...
            ],
            "table_count":        int,
            "relationship_count": int,
        }
    """
    # Index table metadata by name
    table_meta: Dict[str, List[str]] = {}
    for t in (tables or []):
        name = t.get("name", "")
        if not name:
            continue
        raw_fields = t.get("fields") or t.get("columns") or []
        field_names = []
        for f in raw_fields:
            if isinstance(f, str):
                field_names.append(f)
            elif isinstance(f, dict):
                field_names.append(f.get("name", ""))
        table_meta[name] = [fn for fn in field_names if fn]

    # Collect all table names from relationships
    node_names: set = set()
    for rel in relationships:
        ft = rel.get("from_table") or rel.get("fromTable", "")
        tt = rel.get("to_table")   or rel.get("toTable", "")
        if ft:
            node_names.add(ft)
        if tt:
            node_names.add(tt)
    # Also add tables that appear only in `tables` list
    for name in table_meta:
        node_names.add(name)

    nodes = [
        {
            "id":     name,
            "label":  name,
            "fields": table_meta.get(name, []),
        }
        for name in sorted(node_names)
    ]

    edges = []
    for rel in relationships:
        ft = rel.get("from_table") or rel.get("fromTable", "")
        fc = rel.get("from_column") or rel.get("fromColumn", "")
        tt = rel.get("to_table")   or rel.get("toTable", "")
        tc = rel.get("to_column")  or rel.get("toColumn", "")
        name = (rel.get("name") or rel.get("relationship_name") or
                _auto_name({"from_table": ft, "from_column": fc,
                            "to_table": tt, "to_column": tc}))
        cardinality = rel.get("cardinality", "ManyToOne")
        is_active = bool(rel.get("is_active", True))

        edges.append({
            "id":          name,
            "source":      ft,
            "target":      tt,
            "sourceField": fc,
            "targetField": tc,
            "cardinality": cardinality,
            "label":       f"{ft}.{fc} → {tt}.{tc}",
            "is_active":   is_active,
        })

    return {
        "nodes":              nodes,
        "edges":              edges,
        "table_count":        len(nodes),
        "relationship_count": len(edges),
    }


# ---------------------------------------------------------------------------
# Convenience: apply + verify in one call
# ---------------------------------------------------------------------------

def apply_and_verify(
    workspace_id: str,
    dataset_id: str,
    relationships: List[Dict[str, Any]],
    access_token: str,
    *,
    overwrite: bool = True,
    verify_delay_seconds: float = 2.0,
) -> Dict[str, Any]:
    """
    Apply relationships then verify them.  Returns a combined result dict.

    The short delay before verification gives the Power BI service a moment
    to commit the relationship definitions before we read them back.
    """
    apply_result = apply_relationships(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        relationships=relationships,
        access_token=access_token,
        overwrite=overwrite,
    )

    time.sleep(verify_delay_seconds)

    verify_result = verify_relationships(
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        expected_relationships=relationships,
        access_token=access_token,
    )

    return {
        "apply":   apply_result,
        "verify":  verify_result,
        "success": apply_result.get("success") and verify_result.get("success"),
        "summary": (
            f"Applied {apply_result.get('created', 0)} new, "
            f"{apply_result.get('updated', 0)} updated, "
            f"{apply_result.get('skipped', 0)} skipped. "
            f"Verification: {len(verify_result.get('found', []))} found, "
            f"{len(verify_result.get('missing', []))} missing."
        ),
    }


# ---------------------------------------------------------------------------
# Convenience: build relationships directly from normalised rel list
# ---------------------------------------------------------------------------

def relationships_from_normalized(
    normalized: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Convert relationship_normalizer.NormalizedRelationship.to_dict() output
    (or any dict with from_table / from_column / to_table / to_column) into
    the format expected by apply_relationships().

    This bridges relationship_normalizer.py → powerbi_relationships.py.
    """
    out = []
    for r in normalized:
        from_table  = r.get("from_table") or r.get("fromTable", "")
        from_column = r.get("from_column") or r.get("fromColumn", "")
        to_table    = r.get("to_table")    or r.get("toTable", "")
        to_column   = r.get("to_column")   or r.get("toColumn", "")

        if not all([from_table, from_column, to_table, to_column]):
            continue

        cardinality = r.get("cardinality", "ManyToOne")
        direction   = r.get("direction", r.get("cross_filter_direction", "Single"))
        name        = r.get("relationship_name") or r.get("name") or _auto_name(r)
        is_active   = bool(r.get("is_active", True))

        out.append({
            "from_table":             from_table,
            "from_column":            from_column,
            "to_table":               to_table,
            "to_column":              to_column,
            "name":                   name,
            "cardinality":            cardinality,
            "cross_filter_direction": direction,
            "is_active":              is_active,
        })
    return out