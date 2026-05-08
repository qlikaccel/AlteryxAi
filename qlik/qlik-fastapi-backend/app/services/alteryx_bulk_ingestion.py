import hashlib
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any
import re


SUPPORTED_WORKFLOW_EXTENSIONS = {".yxmd", ".yxmc", ".yxwz"}
SUPPORTED_JSON_WORKFLOW_EXTENSIONS = {".json"}
SUPPORTED_ARCHIVE_EXTENSIONS = {".yxzp", ".zip"}
UPLOAD_CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "_alteryx_upload_batches")
)


@dataclass
class WorkflowInventoryItem:
    id: str
    name: str
    sourceFile: str
    packageFile: str | None
    fileType: str
    toolCount: int
    connectionCount: int
    convertibility: str
    complexity: str
    supportedToolCount: int
    unsupportedToolCount: int
    toolTypes: list[str]
    unsupportedTools: list[str]
    recommendations: list[str]
    dataSources: list[dict[str, Any]]
    workflowNodes: list[dict[str, Any]]
    workflowEdges: list[dict[str, Any]]
    isMacroDefinition: bool = False
    macroDependencies: list[dict[str, Any]] = field(default_factory=list)
    macroValidation: dict[str, Any] = field(default_factory=dict)


def _ensure_cache_dir() -> None:
    os.makedirs(UPLOAD_CACHE_DIR, exist_ok=True)


def _extension(filename: str) -> str:
    return os.path.splitext(filename.lower())[1]


def _stable_id(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _plugin_name(node: ET.Element) -> str:
    gui_settings = node.find("GuiSettings")
    if gui_settings is not None:
        plugin = gui_settings.attrib.get("Plugin")
        if plugin:
            return plugin

    engine_settings = node.find("EngineSettings")
    if engine_settings is not None:
        macro = engine_settings.attrib.get("Macro")
        if macro:
            return macro

    return "Unknown"


def _normalize_package_path(value: str) -> str:
    return (value or "").replace("\\", "/").strip().lstrip("./")


def _macro_type_from_path(path: str) -> str:
    lowered = path.lower()
    if "batch" in lowered:
        return "Batch"
    if "iterative" in lowered:
        return "Iterative"
    if "standard" in lowered or "cleanse" in lowered:
        return "Standard"
    return "Macro"


def _config_text(config: ET.Element | None, tag_name: str) -> str:
    if config is None:
        return ""
    direct = config.find(tag_name)
    if direct is not None and direct.text:
        return direct.text.strip()
    for element in config.iter():
        if _strip_namespace(element.tag).lower() == tag_name.lower() and element.text:
            return element.text.strip()
    return ""


def _extract_macro_dependencies(root: ET.Element) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for index, node in enumerate([el for el in root.iter() if _strip_namespace(el.tag) == "Node"], start=1):
        node_id = _node_id(node, index)
        config = node.find(".//Configuration") or node.find("Configuration")
        macro_path = _config_text(config, "MacroPath")
        macro_type = _config_text(config, "MacroType")

        engine_settings = node.find("EngineSettings")
        if engine_settings is not None:
            macro_path = macro_path or engine_settings.attrib.get("EngineDllEntryPoint", "")
            macro_path = macro_path or engine_settings.attrib.get("Macro", "")

        if ".yxmc" not in (macro_path or "").lower():
            continue

        normalized_path = _normalize_package_path(macro_path)
        key = (node_id, normalized_path)
        if key in seen:
            continue
        seen.add(key)

        dependencies.append({
            "toolId": node_id,
            "name": os.path.basename(normalized_path),
            "path": normalized_path,
            "macroType": macro_type or _macro_type_from_path(normalized_path),
            "controlParameter": _config_text(config, "ControlParameter"),
            "iterationLimit": _config_text(config, "IterationLimit"),
            "stopCondition": _config_text(config, "StopCondition"),
            "uploaded": False,
            "matchedFile": "",
            "status": "missing",
        })

    return dependencies


def _node_id(node: ET.Element, fallback: int) -> str:
    return node.attrib.get("ToolID") or node.attrib.get("ToolId") or node.attrib.get("id") or str(fallback)


def _workflow_name(filename: str, root: ET.Element) -> str:
    metadata = root.find(".//MetaInfo/Name")
    if metadata is not None and metadata.text:
        return metadata.text.strip()
    return os.path.splitext(os.path.basename(filename))[0]


def _classify_tool(plugin: str) -> tuple[bool, str | None]:
    lowered = plugin.lower()
    if "summarize" in lowered:
        return True, None
    unsupported_keywords = {
        "python": "Python tools usually need Fabric Notebook or manual rewrite.",
        "rtool": "R tools usually need Fabric Notebook or manual rewrite.",
        "runcommand": "Run Command tools need orchestration outside Power Query.",
        "download": "Download tools require connector/API remediation.",
        "email": "Email tools are operational actions, not Power Query transforms.",
        "spatial": "Spatial tools need GIS-specific remediation.",
        "predictive": "Predictive/modeling tools need ML remediation.",
        "indb": "In-DB tools require database-side SQL or Fabric rewrite.",
        "dynamicinput": "Dynamic Input often requires parameterized connector logic.",
        "macro": "Macros should be expanded and assessed separately.",
    }
    for keyword, reason in unsupported_keywords.items():
        if keyword in lowered:
            return False, reason
    return True, None


def _node_text_blob(node: ET.Element) -> str:
    parts: list[str] = []
    for element in node.iter():
        parts.extend(str(value) for value in element.attrib.values() if value)
        if element.text and element.text.strip():
            parts.append(element.text.strip())
    return "\n".join(parts)


def _node_expression(node: ET.Element) -> str:
    expression_names = ("expression", "formula", "condition", "field", "value")
    candidates: list[str] = []
    for element in node.iter():
        tag = _strip_namespace(element.tag).lower()
        if any(name in tag for name in expression_names):
            if element.text and element.text.strip():
                candidates.append(element.text.strip())
        for key, value in element.attrib.items():
            lowered_key = key.lower()
            if value and any(name in lowered_key for name in expression_names):
                candidates.append(value.strip())
    return candidates[0] if candidates else ""


def _json_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_json_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_json_values(item))
    elif value is not None:
        values.append(str(value))
    return values


def _json_text_blob(value: Any) -> str:
    return "\n".join(item for item in _json_values(value) if item)


def _json_attr(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return None
    variants: list[str] = []
    for key in keys:
        variants.extend([key, key.lower(), key.upper(), key[:1].upper() + key[1:], f"@{key}", f"_{key}"])
    for key in variants:
        if key in value:
            return value[key]
    lowered = {str(k).lower().lstrip("@_"): v for k, v in value.items()}
    for key in keys:
        lookup = key.lower().lstrip("@_")
        if lookup in lowered:
            return lowered[lookup]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _json_node_id(node: dict[str, Any], fallback: int) -> str:
    value = _json_attr(node, "id", "toolId", "ToolID", "ToolId", "nodeId", "NodeId")
    return str(value or fallback)


def _json_plugin_name(node: dict[str, Any]) -> str:
    for container_key in ("GuiSettings", "guiSettings", "EngineSettings", "engineSettings"):
        container = node.get(container_key)
        if isinstance(container, dict):
            value = _json_attr(container, "Plugin", "plugin", "Macro", "macro")
            if value:
                return str(value)

    value = _json_attr(
        node,
        "plugin",
        "Plugin",
        "toolType",
        "tool_type",
        "type",
        "toolName",
        "tool_name",
        "macro",
        "Macro",
    )
    if value:
        return str(value)
    return "Unknown"


def _json_node_expression(value: Any) -> str:
    expression_names = ("expression", "formula", "condition")
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if isinstance(item, str) and item.strip() and any(name in lowered for name in expression_names):
                return item.strip()
        for item in value.values():
            nested = _json_node_expression(item)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _json_node_expression(item)
            if nested:
                return nested
    return ""


def _json_config(node: dict[str, Any]) -> dict[str, Any]:
    config = (
        _json_attr(node, "config", "configuration", "Configuration", "properties", "Properties")
        or node
    )
    if isinstance(config, dict):
        nested = _json_attr(config, "configuration", "Configuration")
        if isinstance(nested, dict):
            return nested
        properties = _json_attr(config, "properties", "Properties")
        if isinstance(properties, dict):
            nested = _json_attr(properties, "configuration", "Configuration")
            if isinstance(nested, dict):
                return nested
    return config if isinstance(config, dict) else {}


def _json_config_items(config: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = _json_attr(config, key)
        if value is not None:
            if isinstance(value, dict):
                for child_key in keys:
                    child_value = _json_attr(value, child_key)
                    if child_value is not None and child_value is not value:
                        return _as_list(child_value)
            return _as_list(value)
    for item in config.values():
        if isinstance(item, dict):
            nested = _json_config_items(item, *keys)
            if nested:
                return nested
    return []


def _extract_json_node_config(node: dict[str, Any], plugin: str) -> dict[str, Any]:
    config = _json_config(node)
    lowered = plugin.lower()
    parsed: dict[str, Any] = {}

    if "select" in lowered:
        fields: list[dict[str, Any]] = []
        for field in _json_config_items(config, "selectedFields", "SelectField", "selectFields", "fields"):
            if not isinstance(field, dict):
                continue
            name = _json_attr(field, "field", "name", "fieldName", "Name")
            if not name:
                continue
            selected = str(_json_attr(field, "selected", "Selected") or "true").lower() != "false"
            rename = _json_attr(field, "rename", "Rename", "newName") or name
            field_type = _json_attr(field, "type", "fieldType", "size") or "String"
            if selected:
                fields.append({"name": str(name), "rename": str(rename), "type": str(field_type)})
        if fields:
            parsed["selectedFields"] = fields

    if "filter" in lowered and "summarize" not in lowered:
        expression = _json_node_expression(config) or _json_node_expression(node)
        if expression:
            parsed["filterExpression"] = expression

    if "summarize" in lowered:
        group_by: list[str] = []
        aggregations: list[dict[str, str]] = []
        for field in _json_config_items(config, "SummarizeField", "summarizeFields", "fields"):
            if not isinstance(field, dict):
                continue
            name = str(_json_attr(field, "field", "name", "fieldName") or "")
            action = str(_json_attr(field, "action", "operation", "summaryAction") or "")
            rename = str(_json_attr(field, "rename", "outputName") or name)
            if not name:
                continue
            if action.lower() == "groupby":
                group_by.append(name)
            else:
                aggregations.append({"field": name, "action": action, "rename": rename})
        if group_by:
            parsed["groupBy"] = group_by
        if aggregations:
            parsed["aggregations"] = aggregations

    if "formula" in lowered:
        formulas: list[dict[str, str]] = []
        for formula in _json_config_items(config, "FormulaField", "formulas", "formulaFields"):
            if not isinstance(formula, dict):
                continue
            field = str(_json_attr(formula, "field", "name", "fieldName") or "")
            expression = str(_json_attr(formula, "expression", "formula") or "")
            field_type = str(_json_attr(formula, "type", "fieldType", "size") or "Double")
            if field and expression:
                formulas.append({"field": field, "expression": expression, "type": field_type})
        if formulas:
            parsed["formulas"] = formulas

    return parsed


def _extract_node_config(node: ET.Element, plugin: str) -> dict[str, Any]:
    config = node.find(".//Configuration") or node.find("Configuration")
    lowered = plugin.lower()
    parsed: dict[str, Any] = {}
    if config is None:
        return parsed

    if "select" in lowered:
        fields: list[dict[str, Any]] = []
        for field in config.findall(".//SelectField"):
            name = field.attrib.get("field") or field.attrib.get("name") or ""
            if not name:
                continue
            selected = field.attrib.get("selected", "True").lower() != "false"
            rename = field.attrib.get("rename") or name
            field_type = field.attrib.get("type") or field.attrib.get("size") or "String"
            if selected:
                fields.append({"name": name, "rename": rename, "type": field_type})
        if fields:
            parsed["selectedFields"] = fields

    if "filter" in lowered and "summarize" not in lowered:
        expression = _node_expression(node)
        if expression:
            parsed["filterExpression"] = expression

    if "summarize" in lowered:
        group_by: list[str] = []
        aggregations: list[dict[str, str]] = []
        for field in config.findall(".//SummarizeField"):
            name = field.attrib.get("field") or ""
            action = field.attrib.get("action") or ""
            rename = field.attrib.get("rename") or name
            if not name:
                continue
            if action.lower() == "groupby":
                group_by.append(name)
            else:
                aggregations.append({"field": name, "action": action, "rename": rename})
        if group_by:
            parsed["groupBy"] = group_by
        if aggregations:
            parsed["aggregations"] = aggregations

    if "formula" in lowered:
        formulas: list[dict[str, str]] = []
        for formula in config.findall(".//FormulaField"):
            field = formula.attrib.get("field") or formula.attrib.get("name") or ""
            expression = formula.attrib.get("expression") or ""
            field_type = formula.attrib.get("type") or formula.attrib.get("size") or "Double"
            if field and expression:
                formulas.append({"field": field, "expression": expression, "type": field_type})
        if formulas:
            parsed["formulas"] = formulas

    return parsed


def _source_type(value: str, plugin: str = "") -> str:
    lowered = f"{value} {plugin}".lower()
    if ".json" in lowered or "json" in lowered:
        return "json"
    if any(token in lowered for token in (".csv", "csv")):
        return "csv"
    if any(token in lowered for token in (".xlsx", ".xls", "excel")):
        return "excel"
    if lowered.startswith("http") or "download" in lowered or "api" in lowered:
        return "api"
    if any(token in lowered for token in ("sql server", "snowflake", "oracle", "postgres", "mysql", "odbc", "oledb", "database")):
        return "database"
    if "sharepoint.com" in lowered:
        return "sharepoint"
    return "unknown"


def _extract_output_fields_from_blob(blob: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in (blob or "").splitlines() if line.strip()]
    try:
        index = lines.index("Output") + 1
    except ValueError:
        return []

    known_types = {
        "bool", "boolean", "byte", "int16", "int32", "int64", "integer", "long",
        "float", "double", "decimal", "fixeddecimal", "string", "v_string",
        "wstring", "v_wstring", "date", "datetime", "time",
    }
    fields: list[dict[str, str]] = []
    while index + 2 < len(lines):
        name, size, field_type = lines[index:index + 3]
        if "dll" in name.lower() or "engine" in name.lower():
            break
        if size.isdigit() and field_type.lower() in known_types:
            fields.append({"name": name, "type": field_type, "size": size})
            index += 3
            continue
        index += 1
    return fields


def _parse_row_count_hint(text: str) -> "int | None":
    """Extract a numeric row count from Alteryx node annotation text.

    Handles patterns like:
      "sales_transactions_2023.csv (~1.5M rows)"
      "Output: enriched_transactions_full.yxdb (~2M records, all fields)"
      "Customer Master 500K"
      "28,591 rows"
    """
    patterns = [
        r"[~(]?\s*([\d,.]+)\s*([kKmMbB]?)\s*(?:rows?|records?)\b",
        r"\b([\d,.]+)\s*([kKmM])\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text or "", flags=re.IGNORECASE):
            try:
                value = float(m.group(1).replace(",", ""))
                suffix = (m.group(2) or "").lower()
                if suffix == "k":
                    value *= 1_000
                elif suffix == "m":
                    value *= 1_000_000
                elif suffix == "b":
                    value *= 1_000_000_000
                result = int(round(value))
                if result > 0:
                    return result
            except (ValueError, IndexError):
                continue
    return None


def _extract_sources(node: ET.Element, plugin: str) -> list[dict[str, Any]]:
    lowered_plugin = (plugin or "").lower()
    if "macroinput" in lowered_plugin or "macrooutput" in lowered_plugin:
        return []

    blob = _node_text_blob(node)
    candidates: list[str] = []
    patterns = [
        r"https?://[^\s\"'<>]+",
        r"[A-Za-z]:[^\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
        r"(?:lib://|file://|\\\\)[^\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
        r"[^\\/\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
    ]
    for pattern in patterns:
        candidates.extend(match.group(0).strip() for match in re.finditer(pattern, blob, flags=re.IGNORECASE))

    # Input tools can store connection strings in attributes without an obvious file extension.
    if not candidates and any(token in lowered_plugin for token in ("input", "download", "database", "indb")):
        short_blob = " ".join(blob.split())[:500]
        if short_blob:
            candidates.append(short_blob)

    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    fields = _extract_output_fields_from_blob(blob)
    for value in candidates:
        cleaned = value.strip().strip(";,)")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        source = {
            "name": os.path.basename(cleaned.split("?")[0]) or cleaned[:80],
            "type": _source_type(cleaned, plugin),
            "path": cleaned,
            "tool": plugin,
        }
        if fields and "input" in lowered_plugin:
            source["fields"] = fields
        # Extract row count hint from node annotation text
        _hint_count = _parse_row_count_hint(blob)
        if _hint_count is not None:
            source["row_count"] = _hint_count
        sources.append(source)
    return sources


def _extract_json_sources(node: dict[str, Any], plugin: str) -> list[dict[str, Any]]:
    blob = _json_text_blob(node)
    candidates: list[str] = []
    patterns = [
        r"https?://[^\s\"'<>]+",
        r"[A-Za-z]:[^\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
        r"(?:lib://|file://|\\\\)[^\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
        r"[^\\/\n\"'<>]+\.(?:csv|xlsx?|json|xml|txt|parquet)",
    ]
    for pattern in patterns:
        candidates.extend(match.group(0).strip() for match in re.finditer(pattern, blob, flags=re.IGNORECASE))

    if not candidates and any(token in plugin.lower() for token in ("input", "download", "database", "indb")):
        short_blob = " ".join(blob.split())[:500]
        if short_blob:
            candidates.append(short_blob)

    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    fields = _extract_output_fields_from_blob(blob)
    for value in candidates:
        cleaned = value.strip().strip("; ,)")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        source = {
            "name": os.path.basename(cleaned.split("?")[0]) or cleaned[:80],
            "type": _source_type(cleaned, plugin),
            "path": cleaned,
            "tool": plugin,
        }
        if fields and "input" in plugin.lower():
            source["fields"] = fields
        # Extract row count hint from node annotation text (e.g. "~1.5M rows", "500K records")
        _hint_count = _parse_row_count_hint(blob)
        if _hint_count is not None:
            source["row_count"] = _hint_count
        sources.append(source)
    return sources


def _extract_edges(root: ET.Element) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for connection in [el for el in root.iter() if _strip_namespace(el.tag) == "Connection"]:
        origin = connection.find(".//Origin")
        destination = connection.find(".//Destination")
        from_id = (origin.attrib.get("ToolID") or origin.attrib.get("ToolId") or "") if origin is not None else ""
        to_id = (destination.attrib.get("ToolID") or destination.attrib.get("ToolId") or "") if destination is not None else ""
        if from_id or to_id:
            edges.append({
                "from": from_id,
                "to": to_id,
                "fromAnchor": origin.attrib.get("Connection") if origin is not None else "",
                "toAnchor": destination.attrib.get("Connection") if destination is not None else "",
            })
    return edges


def _looks_like_json_node(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if _json_attr(value, "ToolID", "ToolId", "toolId", "nodeId", "id") is None:
        return False
    return bool(_json_plugin_name(value) != "Unknown" or _json_attr(value, "config", "configuration", "GuiSettings", "EngineSettings"))


def _find_json_node_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("nodes", "Nodes", "tools", "Tools", "workflowNodes", "workflow_nodes"):
            raw_items = value.get(key)
            if isinstance(raw_items, dict):
                raw_items = (
                    raw_items.get("Node")
                    or raw_items.get("node")
                    or raw_items.get("items")
                    or raw_items.get("Items")
                    or raw_items
                )
            items = _as_list(raw_items)
            dict_items = [item for item in items if isinstance(item, dict)]
            if dict_items and sum(1 for item in dict_items if _looks_like_json_node(item)) >= max(1, len(dict_items) // 2):
                return dict_items
        for item in value.values():
            found = _find_json_node_list(item)
            if found:
                return found
    elif isinstance(value, list):
        dict_items = [item for item in value if isinstance(item, dict)]
        if dict_items and sum(1 for item in dict_items if _looks_like_json_node(item)) >= max(1, len(dict_items) // 2):
            return dict_items
        for item in value:
            found = _find_json_node_list(item)
            if found:
                return found
    return []


def _edge_endpoint(value: Any, *direct_keys: str) -> str:
    if isinstance(value, dict):
        direct = _json_attr(value, *direct_keys)
        if direct is not None and not isinstance(direct, dict):
            return str(direct)
        for nested_key in ("Origin", "origin", "source", "from", "Destination", "destination", "target", "to"):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                endpoint = _json_attr(nested, "ToolID", "ToolId", "toolId", "nodeId", "id")
                if endpoint is not None:
                    return str(endpoint)
    elif value is not None:
        return str(value)
    return ""


def _json_edge_from(edge: dict[str, Any]) -> str:
    value = _json_attr(edge, "from", "source", "sourceId", "sourceToolId", "fromToolId", "origin")
    return _edge_endpoint(value, "from", "source", "sourceId", "sourceToolId", "fromToolId") or _edge_endpoint(edge.get("Origin") or edge.get("origin"), "ToolID", "ToolId", "toolId")


def _json_edge_to(edge: dict[str, Any]) -> str:
    value = _json_attr(edge, "to", "target", "targetId", "destination", "destinationToolId", "toToolId")
    return _edge_endpoint(value, "to", "target", "targetId", "destinationToolId", "toToolId") or _edge_endpoint(edge.get("Destination") or edge.get("destination"), "ToolID", "ToolId", "toolId")


def _find_json_edges(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key in ("connections", "Connections", "edges", "Edges", "workflowEdges", "workflow_edges"):
            raw_items = value.get(key)
            if isinstance(raw_items, dict):
                raw_items = (
                    raw_items.get("Connection")
                    or raw_items.get("connection")
                    or raw_items.get("items")
                    or raw_items.get("Items")
                    or raw_items
                )
            items = _as_list(raw_items)
            for item in items:
                if not isinstance(item, dict):
                    continue
                from_id = _json_edge_from(item)
                to_id = _json_edge_to(item)
                if from_id or to_id:
                    candidates.append({
                        "from": from_id,
                        "to": to_id,
                        "fromAnchor": str(_json_attr(item.get("Origin") or item.get("origin") or {}, "Connection") or _json_attr(item, "fromAnchor") or ""),
                        "toAnchor": str(_json_attr(item.get("Destination") or item.get("destination") or {}, "Connection") or _json_attr(item, "toAnchor") or ""),
                    })
            if candidates:
                return candidates
        for item in value.values():
            found = _find_json_edges(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_json_edges(item)
            if found:
                return found
    return candidates


def _json_workflow_name(filename: str, payload: Any) -> str:
    if isinstance(payload, dict):
        value = _json_attr(payload, "name", "workflowName", "title")
        if value:
            return str(value)
        for key in ("workflow", "Workflow", "metaInfo", "MetaInfo", "metadata", "Metadata"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                value = _json_attr(nested, "name", "workflowName", "title")
                if value:
                    return str(value)
    return os.path.splitext(os.path.basename(filename))[0]


def _complexity(tool_count: int, unsupported_count: int, connection_count: int) -> str:
    if unsupported_count >= 3 or tool_count > 75 or connection_count > 120:
        return "high"
    if unsupported_count > 0 or tool_count > 25 or connection_count > 40:
        return "medium"
    return "low"


def _convertibility(tool_count: int, unsupported_count: int) -> str:
    if tool_count == 0:
        return "manual_review"
    ratio = unsupported_count / max(tool_count, 1)
    if ratio == 0:
        return "high"
    if ratio <= 0.2:
        return "medium"
    return "low"


def parse_workflow_xml(filename: str, content: bytes, package_file: str | None = None) -> WorkflowInventoryItem:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        workflow_id = _stable_id(filename, str(exc))
        return WorkflowInventoryItem(
            id=workflow_id,
            name=os.path.basename(filename),
            sourceFile=filename,
            packageFile=package_file,
            fileType=_extension(filename).lstrip("."),
            toolCount=0,
            connectionCount=0,
            convertibility="manual_review",
            complexity="high",
            supportedToolCount=0,
            unsupportedToolCount=1,
            toolTypes=[],
            unsupportedTools=["Invalid XML"],
            recommendations=[f"Could not parse workflow XML: {exc}"],
            dataSources=[],
            workflowNodes=[],
            workflowEdges=[],
        )

    nodes = [el for el in root.iter() if _strip_namespace(el.tag) == "Node"]
    connections = [el for el in root.iter() if _strip_namespace(el.tag) == "Connection"]
    macro_dependencies = _extract_macro_dependencies(root)

    tool_types: list[str] = []
    unsupported_tools: list[str] = []
    recommendations: list[str] = []

    data_sources: list[dict[str, Any]] = []
    workflow_nodes: list[dict[str, Any]] = []

    for index, node in enumerate(nodes, start=1):
        plugin = _plugin_name(node)
        node_id = _node_id(node, index)
        tool_types.append(plugin)
        workflow_nodes.append({
            "id": node_id,
            "plugin": plugin,
            "supported": True,
            "expression": _node_expression(node),
            "configurationText": _node_text_blob(node)[:4000],
            "config": _extract_node_config(node, plugin),
        })
        data_sources.extend(_extract_sources(node, plugin))
        supported, reason = _classify_tool(plugin)
        workflow_nodes[-1]["supported"] = supported
        if not supported:
            unsupported_tools.append(plugin)
            if reason and reason not in recommendations:
                recommendations.append(reason)

    unique_tool_types = sorted(set(tool_types))
    unique_unsupported = sorted(set(unsupported_tools))
    unsupported_count = len(unsupported_tools)
    tool_count = len(nodes)

    if tool_count == 0:
        recommendations.append("No Alteryx tool nodes were found; verify the file is a workflow XML file.")
    if not recommendations:
        recommendations.append("Candidate for automated Power Query/Dataflow conversion.")

    return WorkflowInventoryItem(
        id=_stable_id(filename, package_file or "", str(len(content))),
        name=_workflow_name(filename, root),
        sourceFile=filename,
        packageFile=package_file,
        fileType=_extension(filename).lstrip("."),
        toolCount=tool_count,
        connectionCount=len(connections),
        convertibility=_convertibility(tool_count, unsupported_count),
        complexity=_complexity(tool_count, unsupported_count, len(connections)),
        supportedToolCount=max(tool_count - unsupported_count, 0),
        unsupportedToolCount=unsupported_count,
        toolTypes=unique_tool_types,
        unsupportedTools=unique_unsupported,
        recommendations=recommendations,
        dataSources=data_sources,
        workflowNodes=workflow_nodes,
        workflowEdges=_extract_edges(root),
        isMacroDefinition=_extension(filename) == ".yxmc",
        macroDependencies=macro_dependencies,
        macroValidation={
            "referenced": len(macro_dependencies),
            "uploaded": 0,
            "missing": len(macro_dependencies),
            "status": "not_applicable" if not macro_dependencies else "missing_macros",
            "message": "No macro dependencies detected." if not macro_dependencies else "Macro dependencies need validation.",
        },
    )


def parse_workflow_json(filename: str, content: bytes, package_file: str | None = None) -> WorkflowInventoryItem:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except Exception as exc:
        workflow_id = _stable_id(filename, str(exc))
        return WorkflowInventoryItem(
            id=workflow_id,
            name=os.path.basename(filename),
            sourceFile=filename,
            packageFile=package_file,
            fileType="json",
            toolCount=0,
            connectionCount=0,
            convertibility="manual_review",
            complexity="high",
            supportedToolCount=0,
            unsupportedToolCount=1,
            toolTypes=[],
            unsupportedTools=["Invalid JSON"],
            recommendations=[f"Could not parse workflow JSON: {exc}"],
            dataSources=[],
            workflowNodes=[],
            workflowEdges=[],
        )

    nodes = _find_json_node_list(payload)
    edges = _find_json_edges(payload)
    tool_types: list[str] = []
    unsupported_tools: list[str] = []
    recommendations: list[str] = []
    data_sources: list[dict[str, Any]] = []
    workflow_nodes: list[dict[str, Any]] = []

    for index, node in enumerate(nodes, start=1):
        plugin = _json_plugin_name(node)
        node_id = _json_node_id(node, index)
        tool_types.append(plugin)
        workflow_nodes.append({
            "id": node_id,
            "plugin": plugin,
            "supported": True,
            "expression": _json_node_expression(node),
            "configurationText": _json_text_blob(node)[:4000],
            "config": _extract_json_node_config(node, plugin),
        })
        data_sources.extend(_extract_json_sources(node, plugin))
        supported, reason = _classify_tool(plugin)
        workflow_nodes[-1]["supported"] = supported
        if not supported:
            unsupported_tools.append(plugin)
            if reason and reason not in recommendations:
                recommendations.append(reason)

    unique_tool_types = sorted(set(tool_types))
    unique_unsupported = sorted(set(unsupported_tools))
    unsupported_count = len(unsupported_tools)
    tool_count = len(nodes)

    if tool_count == 0:
        recommendations.append("No Alteryx tool nodes were found in the JSON; verify this is a full workflow JSON export, not only metadata.")
    if not recommendations:
        recommendations.append("Candidate for automated Power Query/Dataflow conversion from workflow JSON.")

    return WorkflowInventoryItem(
        id=_stable_id(filename, package_file or "", str(len(content))),
        name=_json_workflow_name(filename, payload),
        sourceFile=filename,
        packageFile=package_file,
        fileType="json",
        toolCount=tool_count,
        connectionCount=len(edges),
        convertibility=_convertibility(tool_count, unsupported_count),
        complexity=_complexity(tool_count, unsupported_count, len(edges)),
        supportedToolCount=max(tool_count - unsupported_count, 0),
        unsupportedToolCount=unsupported_count,
        toolTypes=unique_tool_types,
        unsupportedTools=unique_unsupported,
        recommendations=recommendations,
        dataSources=data_sources,
        workflowNodes=workflow_nodes,
        workflowEdges=edges,
    )


def _extract_from_archive(filename: str, content: bytes) -> list[WorkflowInventoryItem]:
    workflows: list[WorkflowInventoryItem] = []
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue

            entry_ext = _extension(entry.filename)
            if entry_ext in SUPPORTED_WORKFLOW_EXTENSIONS:
                workflows.append(
                    parse_workflow_xml(
                        filename=entry.filename,
                        content=archive.read(entry),
                        package_file=filename,
                    )
                )
            elif entry_ext in SUPPORTED_JSON_WORKFLOW_EXTENSIONS:
                workflows.append(
                    parse_workflow_json(
                        filename=entry.filename,
                        content=archive.read(entry),
                        package_file=filename,
                    )
                )
            elif entry_ext in SUPPORTED_ARCHIVE_EXTENSIONS:
                nested_name = f"{filename}!{entry.filename}"
                workflows.extend(_extract_from_archive(nested_name, archive.read(entry)))
    return workflows


def _resolve_macro_dependencies(workflows: list[dict[str, Any]]) -> None:
    uploaded_paths: dict[str, str] = {}
    uploaded_basenames: dict[str, str] = {}

    for workflow in workflows:
        source_file = _normalize_package_path(str(workflow.get("sourceFile") or ""))
        if not source_file:
            continue
        if source_file.lower().endswith(".yxmc"):
            uploaded_paths[source_file.lower()] = source_file
            uploaded_basenames[os.path.basename(source_file).lower()] = source_file

    for workflow in workflows:
        dependencies = workflow.get("macroDependencies") or []
        if not dependencies:
            workflow["macroValidation"] = {
                "referenced": 0,
                "uploaded": 0,
                "missing": 0,
                "status": "not_applicable",
                "message": "No macro dependencies detected.",
            }
            continue

        uploaded_count = 0
        missing: list[str] = []

        for dependency in dependencies:
            macro_path = _normalize_package_path(str(dependency.get("path") or ""))
            macro_name = os.path.basename(macro_path).lower()
            matched_file = uploaded_paths.get(macro_path.lower()) or uploaded_basenames.get(macro_name)
            if matched_file:
                dependency["uploaded"] = True
                dependency["matchedFile"] = matched_file
                dependency["status"] = "ready"
                uploaded_count += 1
            else:
                dependency["uploaded"] = False
                dependency["matchedFile"] = ""
                dependency["status"] = "missing"
                missing.append(macro_path or str(dependency.get("name") or "Unknown macro"))

        referenced = len(dependencies)
        missing_count = referenced - uploaded_count
        workflow["macroValidation"] = {
            "referenced": referenced,
            "uploaded": uploaded_count,
            "missing": missing_count,
            "missingFiles": missing,
            "status": "ready" if missing_count == 0 else "missing_macros",
            "message": (
                f"All {referenced} referenced macro file(s) were uploaded."
                if missing_count == 0
                else f"{missing_count} referenced macro file(s) are missing from this upload."
            ),
        }


def ingest_uploaded_files(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    workflows: list[WorkflowInventoryItem] = []
    rejected: list[dict[str, str]] = []

    for filename, content in files:
        ext = _extension(filename)
        try:
            if ext in SUPPORTED_WORKFLOW_EXTENSIONS:
                workflows.append(parse_workflow_xml(filename, content))
            elif ext in SUPPORTED_JSON_WORKFLOW_EXTENSIONS:
                workflows.append(parse_workflow_json(filename, content))
            elif ext in SUPPORTED_ARCHIVE_EXTENSIONS:
                workflows.extend(_extract_from_archive(filename, content))
            else:
                rejected.append({
                    "file": filename,
                    "reason": "Unsupported file type. Use .yxmd, .yxmc, .yxwz, .json, .yxzp, or .zip.",
                })
        except zipfile.BadZipFile:
            rejected.append({"file": filename, "reason": "Archive is not a valid zip/yxzp file."})
        except Exception as exc:
            rejected.append({"file": filename, "reason": str(exc)})

    batch_id = _stable_id(str(time.time()), *[name for name, _ in files])
    workflow_dicts = [asdict(workflow) for workflow in workflows]
    _resolve_macro_dependencies(workflow_dicts)
    summary = _summarize(workflow_dicts, rejected)
    payload = {
        "batch_id": batch_id,
        "created_at": int(time.time()),
        "summary": summary,
        "workflows": workflow_dicts,
        "rejected": rejected,
    }

    _ensure_cache_dir()
    with open(os.path.join(UPLOAD_CACHE_DIR, f"{batch_id}.json"), "w", encoding="utf-8") as batch_file:
        json.dump(payload, batch_file, indent=2)

    return payload


def load_batch(batch_id: str) -> dict[str, Any]:
    path = os.path.join(UPLOAD_CACHE_DIR, f"{batch_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Alteryx upload batch not found: {batch_id}")
    with open(path, "r", encoding="utf-8") as batch_file:
        return json.load(batch_file)


def _summarize(workflows: list[dict[str, Any]], rejected: list[dict[str, str]]) -> dict[str, Any]:
    by_complexity: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    by_convertibility: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "manual_review": 0}
    total_tools = 0
    unsupported_tools = 0
    macro_reference_count = 0
    uploaded_macro_reference_count = 0
    missing_macro_reference_count = 0
    macro_definition_count = 0

    for workflow in workflows:
        by_complexity[workflow.get("complexity", "high")] = by_complexity.get(workflow.get("complexity", "high"), 0) + 1
        by_convertibility[workflow.get("convertibility", "manual_review")] = (
            by_convertibility.get(workflow.get("convertibility", "manual_review"), 0) + 1
        )
        total_tools += int(workflow.get("toolCount", 0))
        unsupported_tools += int(workflow.get("unsupportedToolCount", 0))
        if workflow.get("isMacroDefinition"):
            macro_definition_count += 1
        macro_validation = workflow.get("macroValidation") or {}
        macro_reference_count += int(macro_validation.get("referenced") or 0)
        uploaded_macro_reference_count += int(macro_validation.get("uploaded") or 0)
        missing_macro_reference_count += int(macro_validation.get("missing") or 0)

    return {
        "workflow_count": len(workflows),
        "rejected_count": len(rejected),
        "total_tool_count": total_tools,
        "unsupported_tool_count": unsupported_tools,
        "macro_definition_count": macro_definition_count,
        "macro_reference_count": macro_reference_count,
        "uploaded_macro_reference_count": uploaded_macro_reference_count,
        "missing_macro_reference_count": missing_macro_reference_count,
        "by_complexity": by_complexity,
        "by_convertibility": by_convertibility,
    }
