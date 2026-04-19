import hashlib
import json
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any
import re


SUPPORTED_WORKFLOW_EXTENSIONS = {".yxmd", ".yxmc", ".yxwz"}
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


def _node_id(node: ET.Element, fallback: int) -> str:
    return node.attrib.get("ToolID") or node.attrib.get("ToolId") or node.attrib.get("id") or str(fallback)


def _workflow_name(filename: str, root: ET.Element) -> str:
    metadata = root.find(".//MetaInfo/Name")
    if metadata is not None and metadata.text:
        return metadata.text.strip()
    return os.path.splitext(os.path.basename(filename))[0]


def _classify_tool(plugin: str) -> tuple[bool, str | None]:
    lowered = plugin.lower()
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


def _extract_sources(node: ET.Element, plugin: str) -> list[dict[str, Any]]:
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
    if not candidates and any(token in plugin.lower() for token in ("input", "download", "database", "indb")):
        short_blob = " ".join(blob.split())[:500]
        if short_blob:
            candidates.append(short_blob)

    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for value in candidates:
        cleaned = value.strip().strip(";,)")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        sources.append({
            "name": os.path.basename(cleaned.split("?")[0]) or cleaned[:80],
            "type": _source_type(cleaned, plugin),
            "path": cleaned,
            "tool": plugin,
        })
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
            elif entry_ext in SUPPORTED_ARCHIVE_EXTENSIONS:
                nested_name = f"{filename}!{entry.filename}"
                workflows.extend(_extract_from_archive(nested_name, archive.read(entry)))
    return workflows


def ingest_uploaded_files(files: list[tuple[str, bytes]]) -> dict[str, Any]:
    workflows: list[WorkflowInventoryItem] = []
    rejected: list[dict[str, str]] = []

    for filename, content in files:
        ext = _extension(filename)
        try:
            if ext in SUPPORTED_WORKFLOW_EXTENSIONS:
                workflows.append(parse_workflow_xml(filename, content))
            elif ext in SUPPORTED_ARCHIVE_EXTENSIONS:
                workflows.extend(_extract_from_archive(filename, content))
            else:
                rejected.append({
                    "file": filename,
                    "reason": "Unsupported file type. Use .yxmd, .yxmc, .yxwz, .yxzp, or .zip.",
                })
        except zipfile.BadZipFile:
            rejected.append({"file": filename, "reason": "Archive is not a valid zip/yxzp file."})
        except Exception as exc:
            rejected.append({"file": filename, "reason": str(exc)})

    batch_id = _stable_id(str(time.time()), *[name for name, _ in files])
    workflow_dicts = [asdict(workflow) for workflow in workflows]
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

    for workflow in workflows:
        by_complexity[workflow.get("complexity", "high")] = by_complexity.get(workflow.get("complexity", "high"), 0) + 1
        by_convertibility[workflow.get("convertibility", "manual_review")] = (
            by_convertibility.get(workflow.get("convertibility", "manual_review"), 0) + 1
        )
        total_tools += int(workflow.get("toolCount", 0))
        unsupported_tools += int(workflow.get("unsupportedToolCount", 0))

    return {
        "workflow_count": len(workflows),
        "rejected_count": len(rejected),
        "total_tool_count": total_tools,
        "unsupported_tool_count": unsupported_tools,
        "by_complexity": by_complexity,
        "by_convertibility": by_convertibility,
    }
