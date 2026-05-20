from __future__ import annotations

import ast
import copy
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_ARCHITECTURE_CONFIG: dict[str, Any] = {
    "context": {
        "node_limit": 120,
        "edge_limit": 200,
        "source_limit": 40,
        "macro_limit": 40,
        "conversion_step_limit": 120,
        "unresolved_construct_limit": 10,
        "unresolved_batch_size": 10,
        "failed_check_limit": 50,
    },
    "routing": {
        "deterministic_first": True,
        "llm_is_source_of_truth": False,
        "llm_primary_for_complex_workflows": "anthropic",
        "llm_fallback_for_complex_workflows": "openai",
        "llm_for_brd_and_summary": "huggingface",
        "complex_tools": [
            "dynamic input",
            "download",
            "json parse",
            "xml parse",
            "multi-row formula",
            "multi-field formula",
            "join",
            "join multiple",
            "append fields",
            "text to columns",
            "transpose",
            "cross tab",
            "find replace",
            "in-db",
            "macro",
            "batch macro",
            "iterative macro",
        ],
        "partial_support_tools": [
            "select",
            "formula",
            "filter",
            "summarize",
            "sort",
            "union",
            "sample",
            "data cleansing",
        ],
        "max_llm_retries": 2,
    },
    "validation": {
        "row_count": True,
        "column_presence": True,
        "not_null_counts": True,
        "min_max": True,
        "sum_average": True,
        "distinct_counts": False,
        "numeric_tolerance_absolute": 0.0001,
        "numeric_tolerance_relative": 0.001,
        "llm_allowed_for_verdict": False,
        "llm_allowed_for_explanation": True,
    },
    "audit": {
        "enabled": True,
        "log_dir": "runtime/audit",
        "include_raw_rows": False,
        "include_full_prompt": False,
    },
}


def load_architecture_config(path: str | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_ARCHITECTURE_CONFIG)
    config_path = Path(path or os.getenv("ALTERYXAI_ARCHITECTURE_CONFIG") or _default_config_path())
    if not config_path.exists():
        return config
    try:
        loaded = _load_config_file(config_path)
    except Exception:
        return config
    if isinstance(loaded, dict):
        _deep_update(config, loaded)
    return config


def config_int(config: dict[str, Any], section: str, key: str, default: int) -> int:
    try:
        return int(config.get(section, {}).get(key, default))
    except (TypeError, ValueError):
        return default


def config_float(config: dict[str, Any], section: str, key: str, default: float) -> float:
    try:
        return float(config.get(section, {}).get(key, default))
    except (TypeError, ValueError):
        return default


def config_bool(config: dict[str, Any], section: str, key: str, default: bool) -> bool:
    value = config.get(section, {}).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def config_list(config: dict[str, Any], section: str, key: str, default: list[str]) -> list[str]:
    value = config.get(section, {}).get(key, default)
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    return list(default)


def _default_config_path() -> str:
    return str(Path(__file__).resolve().parents[2] / "config" / "architecture.yaml")


def _load_config_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return _parse_minimal_yaml(text)


def _parse_minimal_yaml(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_section = ""
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            result.setdefault(current_section, {})
            current_key = ""
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_section and current_key:
            result[current_section].setdefault(current_key, []).append(_parse_scalar(stripped[2:]))
            continue
        if ":" not in stripped or not current_section:
            continue
        key, value = stripped.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if value:
            result[current_section][current_key] = _parse_scalar(value)
        else:
            result[current_section][current_key] = []
    return result


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return [item.strip() for item in value[1:-1].split(",") if item.strip()]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
