from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Set, Tuple


def _canonical(value: Optional[str]) -> str:
    return (value or "").strip()


@dataclass
class NormalizedRelationship:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relationship_name: Optional[str] = None
    source: str = "auto"
    cardinality: str = "ManyToOne"
    direction: str = "Single"
    confidence: float = 1.0
    is_active: bool = True

    def __post_init__(self) -> None:
        self.from_table = _canonical(self.from_table)
        self.from_column = _canonical(self.from_column)
        self.to_table = _canonical(self.to_table)
        self.to_column = _canonical(self.to_column)
        self.source = _canonical(self.source) or "auto"
        self.cardinality = _canonical(self.cardinality) or "ManyToOne"
        self.direction = _canonical(self.direction) or "Single"

        if not self.relationship_name:
            self.relationship_name = (
                f"{self.from_table}.{self.from_column} -> {self.to_table}.{self.to_column}"
            )

        if self.direction not in {"Single", "Both"}:
            self.direction = "Single"

        allowed_cardinalities = {"ManyToOne", "OneToMany", "OneToOne", "ManyToMany"}
        if self.cardinality not in allowed_cardinalities:
            self.cardinality = "ManyToOne"

        self.confidence = max(0.0, min(float(self.confidence), 1.0))

        if not all([self.from_table, self.from_column, self.to_table, self.to_column]):
            raise ValueError("from/to table/column values are required")

    def endpoint_key(self) -> Tuple[str, str, str, str]:
        a = (self.from_table, self.from_column, self.to_table, self.to_column)
        b = (self.to_table, self.to_column, self.from_table, self.from_column)
        return a if "|".join(a).lower() <= "|".join(b).lower() else b

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["name"] = self.relationship_name
        return data


@dataclass
class RelationshipSchema:
    dataset_name: str
    dataset_id: Optional[str] = None
    relationships: Optional[List[NormalizedRelationship]] = None

    def __post_init__(self) -> None:
        self.dataset_name = _canonical(self.dataset_name)
        self.dataset_id = _canonical(self.dataset_id)
        if self.relationships is None:
            self.relationships = []

    def add_relationship(self, relationship: NormalizedRelationship) -> None:
        # Keep best confidence for duplicate endpoints
        idx = None
        for i, existing in enumerate(self.relationships):
            if existing.endpoint_key() == relationship.endpoint_key():
                idx = i
                break

        if idx is None:
            self.relationships.append(relationship)
            return

        if relationship.confidence > self.relationships[idx].confidence:
            self.relationships[idx] = relationship

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "dataset_id": self.dataset_id,
            "relationship_count": len(self.relationships),
            "relationships": [r.to_dict() for r in self.relationships],
        }


class RelationshipNormalizer:
    @staticmethod
    def normalize(raw: Dict[str, Any], source_type: str = "auto") -> NormalizedRelationship:
        src = source_type if source_type != "auto" else (raw.get("source") or "auto")

        from_table = raw.get("from_table") or raw.get("left_table") or raw.get("table_a")
        to_table = raw.get("to_table") or raw.get("right_table") or raw.get("table_b")

        # Accept either *_column or *_field conventions
        from_column = (
            raw.get("from_column")
            or raw.get("from_field")
            or raw.get("left_column")
            or raw.get("field_a")
        )
        to_column = (
            raw.get("to_column")
            or raw.get("to_field")
            or raw.get("right_column")
            or raw.get("field_b")
        )

        if not (from_table and to_table and from_column and to_column):
            raise ValueError(f"Unsupported relationship payload: {raw}")

        cardinality = raw.get("cardinality") or "ManyToOne"
        direction = raw.get("direction") or raw.get("cross_filter_direction") or "Single"
        confidence = raw.get("confidence", 1.0)
        relationship_name = raw.get("relationship_name") or raw.get("name")
        is_active = bool(raw.get("is_active", True))

        return NormalizedRelationship(
            from_table=str(from_table),
            from_column=str(from_column),
            to_table=str(to_table),
            to_column=str(to_column),
            relationship_name=str(relationship_name) if relationship_name else None,
            source=str(src),
            cardinality=str(cardinality),
            direction=str(direction),
            confidence=float(confidence),
            is_active=is_active,
        )

    @staticmethod
    def normalize_list(relationships: List[Dict[str, Any]], source_type: str = "auto") -> List[NormalizedRelationship]:
        normalized: List[NormalizedRelationship] = []
        seen: Set[Tuple[str, str, str, str]] = set()

        for raw in relationships:
            try:
                rel = RelationshipNormalizer.normalize(raw, source_type)
                key = rel.endpoint_key()
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(rel)
            except Exception:
                # Keep pipeline resilient to mixed-quality inputs
                continue

        return normalized
