import logging
import os
import re
import json
from typing import Any
from urllib.parse import urlparse

try:
    import requests
except ImportError:  # pragma: no cover - runtime dependency may be absent in lightweight test shells.
    requests = None


logger = logging.getLogger(__name__)


DEFAULT_SHAREPOINT_FILE_URL = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx"
DEFAULT_SHAREPOINT_FILE_NAME = "sales_data_1M.csv"


ALTERYX_TOOL_MAPPINGS: dict[str, dict[str, str]] = {
    "input data": {"m": "SharePoint.Files / File.Contents / Odbc.DataSource", "category": "Source"},
    "dynamic input": {"m": "Parameterized connector function", "category": "Source"},
    "download": {"m": "Web.Contents", "category": "Source"},
    "json parse": {"m": "Json.Document / Table.FromRecords", "category": "Parse"},
    "xml parse": {"m": "Xml.Tables", "category": "Parse"},
    "select": {"m": "Table.SelectColumns / Table.RenameColumns / Table.TransformColumnTypes", "category": "Shape"},
    "filter": {"m": "Table.SelectRows", "category": "Transform"},
    "formula": {"m": "Table.AddColumn / Table.TransformColumns", "category": "Transform"},
    "multi-row formula": {"m": "Table.AddIndexColumn + row-context logic", "category": "Transform"},
    "multi-field formula": {"m": "Table.TransformColumns", "category": "Transform"},
    "summarize": {"m": "Table.Group", "category": "Aggregate"},
    "join": {"m": "Table.NestedJoin / Table.ExpandTableColumn", "category": "Combine"},
    "join multiple": {"m": "Table.NestedJoin chain", "category": "Combine"},
    "union": {"m": "Table.Combine", "category": "Combine"},
    "append fields": {"m": "Table.AddColumn / cross join pattern", "category": "Combine"},
    "unique": {"m": "Table.Distinct", "category": "Transform"},
    "sort": {"m": "Table.Sort", "category": "Transform"},
    "sample": {"m": "Table.FirstN / Table.Skip", "category": "Transform"},
    "record id": {"m": "Table.AddIndexColumn", "category": "Transform"},
    "data cleansing": {"m": "Table.TransformColumns + Text.Trim/Text.Clean", "category": "Transform"},
    "text to columns": {"m": "Table.SplitColumn", "category": "Transform"},
    "transpose": {"m": "Table.Transpose", "category": "Shape"},
    "cross tab": {"m": "Table.Pivot", "category": "Shape"},
    "find replace": {"m": "Table.ReplaceValue", "category": "Transform"},
    "auto field": {"m": "Table.TransformColumnTypes", "category": "Shape"},
    "browse": {"m": "No-op preview", "category": "Output"},
    "output data": {"m": "Power BI publish target", "category": "Output"},
    "in-db": {"m": "Value.NativeQuery / source SQL", "category": "Database"},
}

LLM_ROUTE_TOOL_KEYS = {
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
}

RULE_ROUTE_MAX_TOOLS = 25
RULE_ROUTE_MAX_CONNECTIONS = 40
_LLM_EXPRESSION_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_LLM_MAPPING_CACHE: dict[str, tuple[list[str], dict[str, Any]]] = {}


def safe_name(value: str, fallback: str = "AlteryxOutput") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value or "").strip("_")
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned or fallback


def sharepoint_site(url: str) -> str:
    parsed = urlparse(url or DEFAULT_SHAREPOINT_FILE_URL)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://sorimtechnologies.sharepoint.com"


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_pseudo_source_path(value: str) -> bool:
    parsed = urlparse(value or "")
    if not parsed.scheme:
        return False
    return parsed.scheme.lower() not in {"http", "https", "file"}


def _source_quality(source: dict[str, Any]) -> tuple[int, int, int]:
    path = str(source.get("path") or "")
    site_url = str(source.get("siteUrl") or "")
    has_http = int(_is_http_url(path) or _is_http_url(site_url))
    has_sharepoint = int("sharepoint.com" in f"{path} {site_url}".lower())
    has_fields = len(source.get("fields") or [])
    return (has_sharepoint, has_http, has_fields)


def _quoted(value: str) -> str:
    return (value or "").replace('"', '""')


def _short_tool_name(plugin: str) -> str:
    if not plugin:
        return "unknown"
    tail = re.split(r"[\\/]", plugin)[-1]
    parts = [part for part in re.split(r"[. ]+", tail) if part]
    if len(parts) >= 2 and parts[-1].lower() == parts[-2].lower():
        return parts[-1].lower()
    return (parts[-1] if parts else plugin).lower()


def detect_tool_key(plugin: str) -> str:
    lowered = (plugin or "").lower()
    ordered_matches = [
        ("dynamicinput", "dynamic input"),
        ("dbfileinput", "input data"),
        ("input", "input data"),
        ("download", "download"),
        ("jsonparse", "json parse"),
        ("xmlparse", "xml parse"),
        ("alteryxselect", "select"),
        ("select", "select"),
        ("filter", "filter"),
        ("formula", "multi-row formula" if "multirow" in lowered else "multi-field formula" if "multifield" in lowered else "formula"),
        ("summarize", "summarize"),
        ("joinmultiple", "join multiple"),
        ("join", "join"),
        ("union", "union"),
        ("appendfields", "append fields"),
        ("unique", "unique"),
        ("sort", "sort"),
        ("sample", "sample"),
        ("recordid", "record id"),
        ("datacleansing", "data cleansing"),
        ("cleanse", "data cleansing"),
        ("texttocolumns", "text to columns"),
        ("transpose", "transpose"),
        ("crosstab", "cross tab"),
        ("findreplace", "find replace"),
        ("autofield", "auto field"),
        ("browse", "browse"),
        ("output", "output data"),
        ("indb", "in-db"),
    ]
    for token, key in ordered_matches:
        if token in lowered:
            return key
    return _short_tool_name(plugin)


def choose_generation_strategy(workflow: dict[str, Any]) -> dict[str, Any]:
    """Route simple workflows to rules and complex mapping workflows to LLM assistance."""
    nodes = workflow.get("workflowNodes") or []
    tool_keys = [detect_tool_key(str(node.get("plugin", ""))) for node in nodes]
    tool_count = int(workflow.get("toolCount") or len(nodes) or 0)
    connection_count = int(workflow.get("connectionCount") or len(workflow.get("workflowEdges") or []) or 0)
    unsupported_count = int(workflow.get("unsupportedToolCount") or 0)
    complexity = str(workflow.get("complexity") or "low").lower()
    complex_tools = sorted({tool for tool in tool_keys if tool in LLM_ROUTE_TOOL_KEYS})

    indicators: list[str] = []
    if complexity in {"medium", "high", "manual_review"}:
        indicators.append(f"{complexity} workflow complexity")
    if unsupported_count:
        indicators.append(f"{unsupported_count} unsupported tool instance(s)")
    if tool_count > RULE_ROUTE_MAX_TOOLS:
        indicators.append(f"{tool_count} tools exceeds simple-rule threshold")
    if connection_count > RULE_ROUTE_MAX_CONNECTIONS:
        indicators.append(f"{connection_count} connections exceeds simple-rule threshold")
    if complex_tools:
        indicators.append(f"complex mapping tools detected: {', '.join(complex_tools[:6])}")
    expression_nodes = [
        node for node in nodes
        if re.search(r"\bIIF\s*\(|REGEX_|Row-\d+|Contains\(|DateTime|Join|Union", str(node.get("expression") or node.get("configurationText") or ""), re.IGNORECASE)
    ]
    if expression_nodes:
        indicators.append(f"{len(expression_nodes)} expression-heavy transformation(s)")

    if indicators:
        return {
            "generation_method": "llm",
            "generation_label": "LLM-assisted mapping",
            "routing_reason": "; ".join(indicators),
            "complexity_indicators": indicators,
            "llm_used": False,
            "llm_status": "routing_selected",
            "llm_model": os.getenv("ALTERYX_MQUERY_LLM_MODEL", "configured LLM"),
        }

    return {
        "generation_method": "rule_based",
        "generation_label": "Rule-based mapping",
        "routing_reason": "Low-complexity workflow with supported deterministic tool mappings.",
        "complexity_indicators": [],
        "llm_used": False,
        "llm_status": "not_required",
        "llm_model": "",
    }


def _llm_mapping_guidance(workflow: dict[str, Any], conversion_steps: list[dict[str, Any]]) -> list[str]:
    """Create an auditable LLM handoff summary for complex mappings."""
    name = workflow.get("name") or "Selected workflow"
    unresolved = [step for step in conversion_steps if not step.get("mapped")]
    complex_steps = [step for step in conversion_steps if step.get("tool") in LLM_ROUTE_TOOL_KEYS]
    guidance = [
        f"Hybrid route selected for {name}: use LLM-assisted semantic mapping for complex transformation intent, then render validated Power Query M.",
        "Review generated steps for multi-input ordering, expression semantics, join cardinality, and source credentials before publishing.",
    ]
    if complex_steps:
        tools = ", ".join(sorted({str(step.get("tool")) for step in complex_steps})[:8])
        guidance.append(f"LLM mapping focus: {tools}.")
    if unresolved:
        tools = ", ".join(sorted({str(step.get("tool")) for step in unresolved})[:8])
        guidance.append(f"Manual/LLM review still required for: {tools}.")
    return guidance


def _llm_settings_for_provider(provider: str) -> dict[str, str]:
    provider = (provider or "huggingface").strip().lower()
    if provider in {"openai", "gpt"}:
        return {
            "provider": "openai",
            "token": os.getenv("OPENAI_API_KEY") or os.getenv("ALTERYX_MQUERY_LLM_API_KEY") or "",
            "model": (
                os.getenv("ALTERYX_COMPLEXITY_OPENAI_MODEL")
                or os.getenv("ALTERYX_MQUERY_OPENAI_MODEL")
                or os.getenv("OPENAI_MODEL")
                or os.getenv("ALTERYX_COMPLEXITY_LLM_MODEL")
                or os.getenv("ALTERYX_MQUERY_LLM_MODEL")
                or "gpt-4o-mini"
            ),
            "url": os.getenv("ALTERYX_MQUERY_OPENAI_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1/chat/completions",
        }
    if provider in {"anthropic", "claude"}:
        return {
            "provider": "anthropic",
            "token": os.getenv("ANTHROPIC_API_KEY") or os.getenv("ALTERYX_MQUERY_LLM_API_KEY") or "",
            "model": (
                os.getenv("ALTERYX_COMPLEXITY_ANTHROPIC_MODEL")
                or os.getenv("ALTERYX_MQUERY_ANTHROPIC_MODEL")
                or os.getenv("ANTHROPIC_MODEL")
                or "claude-3-5-sonnet-latest"
            ),
            "url": os.getenv("ALTERYX_MQUERY_ANTHROPIC_URL") or os.getenv("ANTHROPIC_BASE_URL") or "https://api.anthropic.com/v1/messages",
        }
    return {
        "provider": "huggingface",
        "token": (
            os.getenv("ALTERYX_MQUERY_HF_API_KEY")
            or os.getenv("ALTERYX_MQUERY_LLM_API_KEY")
            or os.getenv("HF_TOKEN")
            or os.getenv("HUGGINGFACE_API_KEY")
            or os.getenv("HF_API_TOKEN")
            or ""
        ),
        "model": os.getenv("ALTERYX_MQUERY_HF_MODEL") or os.getenv("ALTERYX_MQUERY_LLM_MODEL") or "meta-llama/Llama-3.1-8B-Instruct",
        "url": os.getenv("ALTERYX_MQUERY_HF_URL") or os.getenv("ALTERYX_MQUERY_LLM_URL") or "https://router.huggingface.co/v1/chat/completions",
    }


def _llm_settings() -> dict[str, str]:
    provider = (os.getenv("ALTERYX_COMPLEXITY_LLM_PROVIDER") or os.getenv("ALTERYX_MQUERY_LLM_PROVIDER") or "").strip().lower()
    if not provider:
        if os.getenv("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        elif os.getenv("OPENAI_API_KEY"):
            provider = "openai"
        else:
            provider = ""
    return _llm_settings_for_provider(provider)


def _llm_provider_chain() -> list[dict[str, str]]:
    configured_chain = (
        os.getenv("ALTERYX_COMPLEXITY_LLM_PROVIDERS")
        or os.getenv("ALTERYX_MQUERY_LLM_PROVIDERS")
        or ""
    ).strip()
    if configured_chain:
        provider_order = [item.strip().lower() for item in re.split(r"[,;|]", configured_chain) if item.strip()]
    else:
        preferred = (_llm_settings().get("provider") or "").lower()
        provider_order = [preferred] if preferred else []
        for provider in ("anthropic", "openai"):
            if provider not in provider_order:
                provider_order.append(provider)
        if (os.getenv("ALTERYX_COMPLEXITY_ENABLE_HF") or "").strip().lower() in {"1", "true", "yes"}:
            provider_order.append("huggingface")

    settings: list[dict[str, str]] = []
    seen: set[str] = set()
    for provider in provider_order:
        item = _llm_settings_for_provider(provider)
        key = item.get("provider", "")
        if key in seen or not item.get("token"):
            continue
        seen.add(key)
        settings.append(item)
    return settings


def _provider_label(settings: dict[str, str]) -> str:
    model = settings.get("model") or ""
    provider = settings.get("provider") or ""
    return f"{provider}:{model}" if model else provider


def _raise_for_status_with_body(response: Any) -> None:
    try:
        response.raise_for_status()
        return
    except Exception as exc:
        body = ""
        try:
            body = (response.text or "").strip()
        except Exception:
            body = ""
        if len(body) > 500:
            body = body[:500] + "..."
        if body:
            raise RuntimeError(f"{exc}; response={body}") from exc
        raise


def _post_llm_chat(settings: dict[str, str], messages: list[dict[str, str]], max_tokens: int, temperature: float) -> str:
    provider = settings.get("provider", "huggingface")
    token = settings.get("token", "")
    model = settings.get("model", "")
    url = settings.get("url", "")
    if provider == "anthropic":
        system = "\n".join(msg.get("content", "") for msg in messages if msg.get("role") == "system")
        anthropic_messages = [
            {"role": "user" if msg.get("role") != "assistant" else "assistant", "content": msg.get("content", "")}
            for msg in messages
            if msg.get("role") != "system"
        ]
        response = requests.post(
            url,
            headers={
                "x-api-key": token,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "system": system,
                "messages": anthropic_messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=30,
        )
        _raise_for_status_with_body(response)
        data = response.json()
        return "".join(part.get("text", "") for part in data.get("content", []) if isinstance(part, dict))

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=30,
    )
    _raise_for_status_with_body(response)
    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "")


def _call_llm_mapping_model(workflow: dict[str, Any], conversion_steps: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
    if requests is None:
        logger.info("LLM-assisted Alteryx mapping skipped: requests package is unavailable.")
        return [], {"llm_used": False, "llm_status": "requests_unavailable", "llm_model": ""}

    provider_chain = _llm_provider_chain()
    if not provider_chain:
        logger.info("LLM-assisted Alteryx mapping skipped: no configured LLM provider/token found.")
        return [], {"llm_used": False, "llm_status": "not_configured", "llm_model": ""}

    compact_steps = [
        {
            "tool": step.get("tool"),
            "mapped": step.get("mapped"),
            "m_function": step.get("m_function"),
            "note": step.get("note"),
        }
        for step in conversion_steps[:40]
    ]
    prompt = (
        "Map this Alteryx workflow to Power Query M. Return 3-5 concise bullets covering "
        "semantic transformation risks, multi-input mapping order, and any M-query remediation needed.\n\n"
        f"Workflow: {workflow.get('name')}\n"
        f"Complexity: {workflow.get('complexity')}\n"
        f"Tools: {compact_steps}"
    )
    cache_key = f"{workflow.get('id') or workflow.get('name')}|{workflow.get('toolCount')}|{workflow.get('connectionCount')}|{len(conversion_steps)}"
    if cache_key in _LLM_MAPPING_CACHE:
        logger.info("Using cached LLM-assisted Alteryx mapping result for workflow: %s", workflow.get("name"))
        return _LLM_MAPPING_CACHE[cache_key]

    failures: list[str] = []
    logger.info(
        "LLM-assisted Alteryx mapping provider chain: %s",
        " -> ".join(_provider_label(item) for item in provider_chain),
    )
    for llm in provider_chain:
        model = llm.get("model", "")
        try:
            logger.info("Calling LLM-assisted Alteryx mapping provider: %s", _provider_label(llm))
            content = _post_llm_chat(
                llm,
                messages=[
                    {"role": "system", "content": "You are an expert Alteryx to Power Query M migration engineer."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=350,
                temperature=0.1,
            )
            bullets = [
                re.sub(r"^[\s*\-\d.]+", "", line).strip()
                for line in content.splitlines()
                if line.strip()
            ][:5]
            result = (bullets, {"llm_used": True, "llm_status": "completed", "llm_model": model, "llm_provider": llm.get("provider", "")})
            _LLM_MAPPING_CACHE[cache_key] = result
            logger.info("LLM-assisted Alteryx mapping completed: %s", _provider_label(llm))
            return result
        except Exception as exc:
            failure = f"{_provider_label(llm)} failed: {exc}"
            failures.append(failure)
            logger.warning("LLM-assisted Alteryx mapping provider failed; trying fallback if available: %s", failure)

    logger.warning("LLM-assisted Alteryx M-query mapping failed for all providers; using deterministic guidance: %s", " | ".join(failures))
    result = ([], {
        "llm_used": False,
        "llm_status": "failed_fallback",
        "llm_model": ", ".join(item.get("model", "") for item in provider_chain),
        "llm_provider": " -> ".join(item.get("provider", "") for item in provider_chain),
        "llm_error": " | ".join(failures[-3:]),
    })
    _LLM_MAPPING_CACHE[cache_key] = result
    return result


def _sanitize_llm_m_expression(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip().strip("`")
    if not text:
        return ""
    blocked = ("Web.Contents", "File.Contents", "SharePoint.Files", "Odbc.", "OleDb.", "Sql.", "#shared", "Expression.Evaluate")
    if any(token.lower() in text.lower() for token in blocked):
        return ""
    if re.search(r"[\r\n;]", text):
        return ""
    return text


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}


def _call_llm_expression_converter(
    *,
    expression: str,
    tool_key: str,
    current_step: str,
    output_field: str = "",
    output_type: str = "",
    workflow: dict[str, Any],
    node: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Convert one Alteryx expression to a Power Query row expression with strict fallback behavior."""
    if requests is None:
        logger.info("LLM expression conversion skipped for node %s: requests package is unavailable.", node.get("id"))
        return "", {"status": "requests_unavailable"}

    provider_chain = _llm_provider_chain()
    if not provider_chain:
        logger.info("LLM expression conversion skipped for node %s: no configured LLM provider/token found.", node.get("id"))
        return "", {"status": "not_configured"}

    cache_key = "|".join([
        str(tool_key),
        str(output_field),
        str(output_type),
        str(expression),
    ])
    if cache_key in _LLM_EXPRESSION_CACHE:
        logger.info("Using cached LLM expression conversion result for node %s.", node.get("id"))
        return _LLM_EXPRESSION_CACHE[cache_key]

    prompt = {
        "task": "Convert one Alteryx expression into a Power Query M row expression.",
        "strict_output": {
            "m_expression": "single Power Query M expression only, no let block, no data source calls",
            "confidence": "number 0 to 1",
            "warnings": ["short warnings if approximation was needed"],
        },
        "tool_key": tool_key,
        "current_step": current_step,
        "workflow_name": workflow.get("name"),
        "node_id": node.get("id"),
        "output_field": output_field,
        "output_type": output_type,
        "alteryx_expression": expression,
        "available_fields_hint": re.findall(r"\[([^\]]+)\]", str(node.get("configurationText") or expression))[:80],
        "examples": [
            {"alteryx": "IIF(IsNull([Amount]),0,[Amount])", "m": "if [Amount] = null then 0 else [Amount]"},
            {"alteryx": "Contains([Region], \"North\")", "m": "Text.Contains(Text.From([Region]), \"North\")"},
        ],
    }

    failures: list[str] = []
    logger.info(
        "LLM expression conversion provider chain for node %s: %s",
        node.get("id"),
        " -> ".join(_provider_label(item) for item in provider_chain),
    )
    for llm in provider_chain:
        model = llm.get("model", "")
        try:
            logger.info("Calling LLM expression provider for node %s: %s", node.get("id"), _provider_label(llm))
            content = _post_llm_chat(
                llm,
                messages=[
                    {"role": "system", "content": "Return only valid JSON. Do not include markdown."},
                    {"role": "user", "content": json.dumps(prompt)},
                ],
                max_tokens=300,
                temperature=0,
            )
            payload = _extract_json_object(content)
            m_expression = _sanitize_llm_m_expression(payload.get("m_expression"))
            if not m_expression and llm.get("provider") == "huggingface":
                m_expression = _sanitize_llm_m_expression(content)
            if not m_expression:
                failures.append(f"{_provider_label(llm)} returned invalid_response")
                continue
            result = (m_expression, {
                "status": "completed",
                "model": model,
                "provider": llm.get("provider", ""),
                "confidence": payload.get("confidence"),
                "warnings": payload.get("warnings") if isinstance(payload.get("warnings"), list) else [],
            })
            _LLM_EXPRESSION_CACHE[cache_key] = result
            logger.info("LLM expression conversion completed for node %s: %s", node.get("id"), _provider_label(llm))
            return result
        except Exception as exc:
            failure = f"{_provider_label(llm)} failed: {exc}"
            failures.append(failure)
            logger.warning("LLM expression provider failed for node %s; trying fallback if available: %s", node.get("id"), failure)

    logger.warning("LLM expression conversion failed for node %s on all providers: %s", node.get("id"), " | ".join(failures))
    result = ("", {
        "status": "failed_fallback",
        "model": ", ".join(item.get("model", "") for item in provider_chain),
        "provider": " -> ".join(item.get("provider", "") for item in provider_chain),
        "errors": failures[-3:],
    })
    _LLM_EXPRESSION_CACHE[cache_key] = result
    return result


def _field_ref(value: str) -> str:
    value = value.strip()
    if value == "_":
        return "_"
    if value.startswith("[") and value.endswith("]"):
        return value
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", value):
        return f"[{value}]"
    return value


def _m_literal(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return f'"{_quoted(value[1:-1])}"'
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return f'"{_quoted(value[1:-1])}"'
    return value


def _split_top_level_args(value: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(value):
        char = value[index]
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth = max(depth - 1, 0)
            current.append(char)
        elif char == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    args.append("".join(current).strip())
    return args


def _replace_in_operator(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        field = _field_ref(match.group("field"))
        values = [
            _m_literal(item)
            for item in _split_top_level_args(match.group("values"))
            if item.strip()
        ]
        return f"List.Contains({{{', '.join(values)}}}, {field})"

    return re.sub(
        r"(?P<field>\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_ ]*)\s+IN\s*\((?P<values>[^)]*)\)",
        repl,
        text,
        flags=re.IGNORECASE,
    )


def _normalize_m_literals(text: str) -> str:
    text = re.sub(r"'([^']*)'", lambda m: f'"{_quoted(m.group(1))}"', text)
    text = re.sub(r"\bTrue\b", "true", text, flags=re.IGNORECASE)
    text = re.sub(r"\bFalse\b", "false", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNULL\(\)", "null", text, flags=re.IGNORECASE)
    text = re.sub(r"\bNULL\b", "null", text, flags=re.IGNORECASE)
    text = text.replace("==", "=")
    return text


def translate_alteryx_expression(expression: str) -> str:
    """Translate common Alteryx formula syntax into Power Query M syntax."""
    if not expression:
        return "true"

    text = _normalize_m_literals(expression.strip())
    text = _replace_in_operator(text)
    text = re.sub(r"\bAND\b", "and", text, flags=re.IGNORECASE)
    text = re.sub(r"\bOR\b", "or", text, flags=re.IGNORECASE)
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"(?<![<>=!])!=(?!=)", "<>", text)
    text = re.sub(
        r"\bTrimRight\s*\(\s*TrimLeft\s*\(\s*Upper(?:case)?\(([^()]+)\)\s*\)\s*\)",
        lambda m: f"Text.Trim(Text.Upper(Text.From({_field_ref(m.group(1))})))",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bTrimRight\s*\(\s*TrimLeft\s*\(([^()]+)\)\s*\)", lambda m: f"Text.Trim(Text.From({_field_ref(m.group(1))}))", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCEIL\s*\(", "Number.RoundUp(", text, flags=re.IGNORECASE)
    text = re.sub(r"\bToString\s*\(", "Text.From(", text, flags=re.IGNORECASE)
    text = re.sub(r'("[^"]*")\s*\+\s*Text\.From', r'\1 & Text.From', text)

    function_replacements = [
        (r"\bIsNull\(([^()]+)\)", lambda m: f"({_field_ref(m.group(1))} = null)"),
        (r"\bIsEmpty\(([^()]+)\)", lambda m: f"({_field_ref(m.group(1))} = null or Text.Length(Text.From({_field_ref(m.group(1))})) = 0)"),
        (r"\bTrim\(([^()]+)\)", lambda m: f"Text.Trim(Text.From({_field_ref(m.group(1))}))"),
        (r"\bTrimLeft\(([^()]+)\)", lambda m: f"Text.TrimStart(Text.From({_field_ref(m.group(1))}))"),
        (r"\bTrimRight\(([^()]+)\)", lambda m: f"Text.TrimEnd(Text.From({_field_ref(m.group(1))}))"),
        (r"(?<!\.)\bContains\(\s*Lower(?:case)?\((\[[^\]]+\])\)\s*,\s*([^()]+)\)", lambda m: f"Text.Contains(Text.Lower(Text.From({_field_ref(m.group(1))})), {_m_literal(m.group(2))})"),
        (r"(?<!\.)\bContains\(\s*Upper(?:case)?\((\[[^\]]+\])\)\s*,\s*([^()]+)\)", lambda m: f"Text.Contains(Text.Upper(Text.From({_field_ref(m.group(1))})), {_m_literal(m.group(2))})"),
        (r"(?<!\.)\bContains\(([^,()]+),\s*([^()]+)\)", lambda m: f"Text.Contains(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()})"),
        (r"\bStartsWith\(([^,()]+),\s*([^()]+)\)", lambda m: f"Text.StartsWith(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()})"),
        (r"\bEndsWith\(([^,()]+),\s*([^()]+)\)", lambda m: f"Text.EndsWith(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()})"),
        (r"\bLeft\(([^,()]+),\s*([^()]+)\)", lambda m: f"Text.Start(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()})"),
        (r"\bRight\(([^,()]+),\s*([^()]+)\)", lambda m: f"Text.End(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()})"),
        (r"\bSubstring\(([^,()]+),\s*([^,()]+),\s*([^()]+)\)", lambda m: f"Text.Middle(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()}, {m.group(3).strip()})"),
        (r"\bUpper(?:case)?\(([^()]+)\)", lambda m: f"Text.Upper(Text.From({_field_ref(m.group(1))}))"),
        (r"\bLower(?:case)?\(([^()]+)\)", lambda m: f"Text.Lower(Text.From({_field_ref(m.group(1))}))"),
        (r"\bReplace\(([^,()]+),\s*([^,()]+),\s*([^()]+)\)", lambda m: f"Text.Replace(Text.From({_field_ref(m.group(1))}), {m.group(2).strip()}, {m.group(3).strip()})"),
        (r"\bToNumber\(([^()]+)\)", lambda m: f"Number.From({_field_ref(m.group(1))})"),
        (r"\bToString\(([^()]+)\)", lambda m: f"Text.From({_field_ref(m.group(1))})"),
        (r"\bDateTimeNow\(\)", lambda m: "DateTime.LocalNow()"),
        (r"\bDateTimeYear\(([^()]+)\)", lambda m: f"Date.Year(DateTime.Date({_field_ref(m.group(1))}))"),
        (r"\bDateTimeMonth\(([^()]+)\)", lambda m: f"Date.Month(DateTime.Date({_field_ref(m.group(1))}))"),
        (r"\bDateTimeDiff\(DateTime\.LocalNow\(\),\s*([^,()]+),\s*\"days\"\)", lambda m: f"Duration.Days(DateTime.Date(DateTime.LocalNow()) - DateTime.Date({_field_ref(m.group(1))}))"),
        (r"\bDateTimeDiff\(([^,()]+),\s*([^,()]+),\s*\"days\"\)", lambda m: f"Duration.Days(DateTime.Date({m.group(1).strip()}) - DateTime.Date({_field_ref(m.group(2))}))"),
        (r"\bCEIL\(([^()]+)\)", lambda m: f"Number.RoundUp({m.group(1).strip()})"),
    ]
    for pattern, repl in function_replacements:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)

    text = re.sub(r'("[^"]*")\s*\+\s*Text\.From', r'\1 & Text.From', text)
    return text


def _m_type(alteryx_type: str) -> str:
    lowered = (alteryx_type or "").lower()
    if any(token in lowered for token in ("int", "long")):
        return "Int64.Type"
    if any(token in lowered for token in ("double", "float", "decimal", "fixeddecimal", "number")):
        return "type number"
    if "date" in lowered and "time" in lowered:
        return "type datetime"
    if "date" in lowered:
        return "type date"
    if "bool" in lowered:
        return "type logical"
    return "type text"


def _m_value_type(alteryx_type: str) -> str:
    lowered = (alteryx_type or "").lower()
    if any(token in lowered for token in ("int", "double", "float", "decimal", "number", "fixeddecimal")):
        return "type number"
    if "date" in lowered and "time" in lowered:
        return "type datetime"
    if "date" in lowered:
        return "type date"
    if "bool" in lowered:
        return "type logical"
    return "type text"


def _config_lines(node: dict[str, Any]) -> list[str]:
    return [line.strip() for line in str(node.get("configurationText") or "").splitlines() if line.strip()]


def _tool_config_lines(node: dict[str, Any]) -> list[str]:
    lines = _config_lines(node)
    if len(lines) >= 4 and lines[0].isdigit() and "plugins" in lines[1].lower():
        return lines[4:]
    return lines


def _selected_fields(node: dict[str, Any]) -> list[dict[str, str]]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    explicit = config.get("selectedFields") if config else None
    if isinstance(explicit, list) and explicit:
        return [
            {
                "name": str(item.get("name") or ""),
                "rename": str(item.get("rename") or item.get("name") or ""),
                "type": str(item.get("type") or "String"),
            }
            for item in explicit
            if isinstance(item, dict) and item.get("name")
        ]

    lines = _config_lines(node)
    fields: list[dict[str, str]] = []
    index = 0
    while index + 3 < len(lines):
        name, selected, field_type, rename = lines[index:index + 4]
        if selected.lower() in {"true", "false"} and selected.lower() == "true":
            fields.append({"name": name, "rename": rename or name, "type": field_type or "String"})
            index += 4
        else:
            index += 1
    return fields


def _summarize_config(node: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    group_by = list(config.get("groupBy") or []) if config else []
    aggregations = list(config.get("aggregations") or []) if config else []
    if group_by or aggregations:
        return [str(item) for item in group_by], [
            {"field": str(item.get("field") or ""), "action": str(item.get("action") or ""), "rename": str(item.get("rename") or item.get("field") or "")}
            for item in aggregations
            if isinstance(item, dict) and item.get("field")
        ]

    lines = _config_lines(node)
    parsed_groups: list[str] = []
    parsed_aggs: list[dict[str, str]] = []
    index = 0
    while index + 1 < len(lines):
        field, action = lines[index], lines[index + 1]
        action_lower = action.lower()
        if action_lower == "groupby":
            parsed_groups.append(field)
            index += 2
        elif action_lower in {"sum", "count", "average", "avg", "min", "max"}:
            rename = lines[index + 2] if index + 2 < len(lines) else field
            parsed_aggs.append({"field": field, "action": action, "rename": rename or field})
            index += 3
        else:
            index += 1
    return parsed_groups, parsed_aggs


def _formula_config(node: dict[str, Any]) -> list[dict[str, str]]:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    explicit = config.get("formulas") if config else None
    if isinstance(explicit, list) and explicit:
        return [
            {
                "field": str(item.get("field") or ""),
                "expression": str(item.get("expression") or ""),
                "type": str(item.get("type") or "Double"),
            }
            for item in explicit
            if isinstance(item, dict) and item.get("field") and item.get("expression")
        ]

    plugin = str(node.get("plugin") or "").lower()
    lines = _tool_config_lines(node)
    formulas: list[dict[str, str]] = []

    if "multifieldformula" in plugin:
        expression_index = next(
            (idx for idx, line in enumerate(lines) if "[_CurrentField_]" in line or "_CurrentField_" in line),
            -1,
        )
        if expression_index > 0:
            field_type = lines[0] if lines else "String"
            candidates = [
                item for item in lines[1:expression_index]
                if item and not item.isdigit() and item.lower() not in {"true", "false", "same as input"}
            ]
            expression = lines[expression_index]
            return [
                {
                    "field": field,
                    "expression": expression.replace("[_CurrentField_]", "_"),
                    "type": field_type or "String",
                    "mode": "transform_existing",
                }
                for field in candidates
            ]

    known_types = {
        "bool", "boolean", "byte", "int16", "int32", "int64", "integer", "long",
        "float", "double", "decimal", "fixeddecimal", "string", "v_string",
        "wstring", "v_wstring", "date", "datetime", "time",
    }
    index = 0
    while index < max(len(lines) - 4, 0):
        field, field_type, size, _description = lines[index:index + 4]
        if field.lower() in {"true", "false"} or not size.isdigit():
            index += 1
            continue
        if field_type.lower() not in known_types:
            index += 1
            continue
        expression_lines: list[str] = []
        cursor = index + 4
        while cursor < len(lines):
            if lines[cursor] == "0":
                break
            if (
                cursor + 2 < len(lines)
                and lines[cursor + 1].lower() in known_types
                and lines[cursor + 2].isdigit()
            ):
                break
            expression_lines.append(lines[cursor])
            cursor += 1
        expression = " ".join(expression_lines).strip()
        if re.search(r"\[[^\]]+\]|\bIIF\s*\(|\bContains\s*\(|\bDateTime|\bToString|\bCEIL\b|Row-\d+", expression, flags=re.IGNORECASE):
            formulas.append({"field": field, "expression": expression, "type": field_type or "Double"})
        index = max(cursor, index + 1)
    return formulas


def _convert_iif_expression(expression: str) -> str:
    text = expression.strip()
    if re.match(r"^IF\b", text, flags=re.IGNORECASE):
        match = re.match(
            r"^IF\s+(.+?)\s+THEN\s+(.+?)\s+ELSE\s+(.+?)\s+ENDIF$",
            " ".join(text.split()),
            flags=re.IGNORECASE,
        )
        if match:
            condition = translate_alteryx_expression(match.group(1))
            true_value = translate_alteryx_expression(match.group(2))
            false_value = translate_alteryx_expression(match.group(3))
            return f"if {condition} then {true_value} else {false_value}"
    if re.match(r"^IIF\(", text, flags=re.IGNORECASE) and text.endswith(")"):
        inner = text[text.find("(") + 1:-1]
        args = _split_top_level_args(inner)
        if len(args) >= 2:
            condition = translate_alteryx_expression(args[0])
            true_expr = _convert_iif_expression(args[1]) if re.match(r"^IIF\(", args[1], flags=re.IGNORECASE) else translate_alteryx_expression(args[1])
            false_arg = args[2] if len(args) >= 3 and args[2] else "null"
            false_expr = _convert_iif_expression(false_arg) if re.match(r"^IIF\(", false_arg, flags=re.IGNORECASE) else translate_alteryx_expression(false_arg)
            return f"if {condition} then {true_expr} else {false_expr}"
    text = re.sub(r"\bNULL\(\)", "null", text, flags=re.IGNORECASE)
    return translate_alteryx_expression(text)


def _source_steps(source: dict[str, Any], table_name: str) -> list[tuple[str, str]]:
    source_type = (source.get("type") or "csv").lower()
    path = source.get("path", "")
    name = source.get("name") or os.path.basename(path) or DEFAULT_SHAREPOINT_FILE_NAME
    source_fields = [
        item for item in (source.get("fields") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    static_type_ops = ", ".join(
        f'{{"{_quoted(str(item.get("name") or ""))}", {_m_type(str(item.get("type") or ""))}}}'
        for item in source_fields
    )
    changed_types_expr = (
        f"Table.TransformColumnTypes(PromotedHeaders, {{{static_type_ops}}})"
        if static_type_ops
        else "Table.TransformColumnTypes(PromotedHeaders, List.Transform(Table.ColumnNames(PromotedHeaders), each {_, type text}))"
    )

    if source_type == "json":
        if "sharepoint.com" in (path or "").lower() or source.get("siteUrl"):
            site_url = source.get("siteUrl") or sharepoint_site(path)
            return [
                ("Source", f'SharePoint.Files("{_quoted(site_url)}", [ApiVersion = 15])'),
                ("MatchingFiles", f'Table.SelectRows(Source, each [Name] = "{_quoted(name)}")'),
                ("FileContent", f'if Table.RowCount(MatchingFiles) = 0 then error "File not found in SharePoint: {_quoted(name)}" else MatchingFiles{{0}}[Content]'),
                ("JsonData", "Json.Document(FileContent)"),
                ("JsonRows", "if Value.Is(JsonData, type list) then JsonData else if Value.Is(JsonData, type record) then {JsonData} else {}"),
                ("TypedColumns", "Table.FromRecords(JsonRows, null, MissingField.UseNull)"),
            ]
        return [
            ("Source", f'Json.Document(File.Contents("{_quoted(path or name)}"))'),
            ("JsonRows", "if Value.Is(Source, type list) then Source else if Value.Is(Source, type record) then {Source} else {}"),
            ("TypedColumns", "Table.FromRecords(JsonRows, null, MissingField.UseNull)"),
        ]

    if source_type in {"csv", "sharepoint", "unknown"}:
        if "sharepoint.com" in (path or "").lower() or source.get("siteUrl"):
            site_url = source.get("siteUrl") or sharepoint_site(path)
            return [
                ("Source", f'SharePoint.Files("{_quoted(site_url)}", [ApiVersion = 15])'),
                ("MatchingFiles", f'Table.SelectRows(Source, each [Name] = "{_quoted(name)}")'),
                ("FileContent", f'if Table.RowCount(MatchingFiles) = 0 then error "File not found in SharePoint: {_quoted(name)}" else MatchingFiles{{0}}[Content]'),
                ("CsvData", 'Csv.Document(FileContent, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv])'),
                ("PromotedHeaders", "Table.PromoteHeaders(CsvData, [PromoteAllScalars = true])"),
                ("ChangedTypes", changed_types_expr),
            ]
        return [
            ("Source", f'File.Contents("{_quoted(path or name)}")'),
            ("CsvData", 'Csv.Document(Source, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv])'),
            ("PromotedHeaders", "Table.PromoteHeaders(CsvData, [PromoteAllScalars = true])"),
            ("ChangedTypes", changed_types_expr),
        ]

    if source_type == "excel":
        return [
            ("Source", f'Excel.Workbook(File.Contents("{_quoted(path)}"), null, true)'),
            ("FirstSheet", "Source{0}[Data]"),
            ("TypedColumns", "Table.PromoteHeaders(FirstSheet, [PromoteAllScalars = true])"),
        ]

    if source_type == "database":
        return [
            ("Source", 'Odbc.DataSource("[DatabaseConnectionString]", [HierarchicalNavigation = true])'),
            ("TypedColumns", "Source"),
        ]

    if source_type == "api":
        return [
            ("Source", f'Json.Document(Web.Contents("{_quoted(path or "[ApiEndpoint]")}"))'),
            ("TypedColumns", "Table.FromRecords(if Value.Is(Source, type list) then Source else {Source})"),
        ]

    return [
        ("Source", f'"Source metadata unavailable for {safe_name(table_name)}. Configure this workflow data source before refresh."'),
        ("TypedColumns", "Source"),
    ]


def _is_file_input_source(source: dict[str, Any]) -> bool:
    tool = str(source.get("tool") or "").lower()
    name = str(source.get("name") or "").strip()
    path = str(source.get("path") or "").strip()
    source_type = str(source.get("type") or "").lower()
    combined = f"{name} {path}".lower()
    if "output" in tool or name.lower().startswith("output:") or path.lower().startswith("output:"):
        return False
    if source_type not in {"csv", "excel", "json", "sharepoint"}:
        return False
    if not any(token in tool for token in ("input", "dbfileinput", "dynamicinput")):
        return False
    return any(ext in combined for ext in (".csv", ".xlsx", ".xls", ".json"))


def _input_fields_by_file_name(workflow: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    fields_by_file: dict[str, list[dict[str, str]]] = {}
    type_tokens = {"string", "v_wstring", "int64", "integer", "double", "fixeddecimal", "date", "datetime", "bool", "boolean"}
    stop_tokens = {"output", "false", "true", "list_connections"}
    file_pattern = re.compile(r'[^\\/\n]+\.(?:csv|xlsx|xls|json)\b', re.IGNORECASE)

    for node in workflow.get("workflowNodes") or []:
        if "input" not in str(node.get("plugin") or "").lower():
            continue
        lines = [line.strip() for line in str(node.get("configurationText") or "").splitlines() if line.strip()]
        if not lines:
            continue

        file_name = ""
        for line in lines:
            matches = file_pattern.findall(line)
            if matches:
                file_name = matches[-1].replace("\\", "/").split("/")[-1]
                break
        if not file_name:
            continue

        fields: list[dict[str, str]] = []
        seen: set[str] = set()
        index = 1 if lines and lines[0] == str(node.get("id") or "") else 0
        while index < len(lines):
            name = lines[index].strip()
            if file_pattern.search(name) or name.lower() in stop_tokens:
                break
            if index + 2 < len(lines):
                raw_type = lines[index + 1].strip()
                friendly_type = lines[index + 2].strip()
                if raw_type.lower() in type_tokens or friendly_type.lower() in type_tokens:
                    if name and name.lower() not in seen:
                        seen.add(name.lower())
                        fields.append({"name": name, "type": friendly_type or raw_type or "String"})
                    index += 3
                    continue
            index += 1

        if fields:
            fields_by_file[file_name.lower()] = fields

    return fields_by_file


def _workflow_file_sources(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    by_file_name: dict[str, dict[str, Any]] = {}
    fields_by_file_name = _input_fields_by_file_name(workflow)
    for source in workflow.get("dataSources") or []:
        if not isinstance(source, dict) or not _is_file_input_source(source):
            continue
        item = dict(source)
        raw_path = str(item.get("path") or "").strip()
        name = str(item.get("name") or os.path.basename(raw_path)).strip()
        if not name:
            continue

        # Alteryx Cloud JSON may expose VFS/pseudo paths such as s://tenant.
        # They are useful metadata, but Power Query cannot use them as
        # SharePoint roots. Keep the file name, then resolve to the supplied or
        # default SharePoint site at M-generation time.
        path = raw_path or name
        if _is_pseudo_source_path(path):
            path = DEFAULT_SHAREPOINT_FILE_URL

        item["name"] = name
        item["path"] = path
        item["siteUrl"] = sharepoint_site(str(item.get("siteUrl") or path))
        if not item.get("fields") and fields_by_file_name.get(name.lower()):
            item["fields"] = fields_by_file_name[name.lower()]

        key = name.lower()
        existing = by_file_name.get(key)
        if not existing:
            by_file_name[key] = item
            continue

        if len(item.get("fields") or []) > len(existing.get("fields") or []):
            existing["fields"] = item.get("fields") or []
        if _source_quality(item) > _source_quality(existing):
            item["fields"] = item.get("fields") or existing.get("fields") or []
            by_file_name[key] = item

    return list(by_file_name.values())


def _file_name_from_input_node(node: dict[str, Any]) -> str:
    if detect_tool_key(str(node.get("plugin") or "")) != "input data":
        return ""
    file_pattern = re.compile(r'[^\\/\n]+\.(?:csv|xlsx|xls|json)\b', re.IGNORECASE)
    for line in str(node.get("configurationText") or "").splitlines():
        matches = file_pattern.findall(line.strip())
        if matches:
            return matches[-1].replace("\\", "/").split("/")[-1]
    return ""


def _raw_query_name_for_source(source: dict[str, Any], index: int, table_name: str, emitted: set[str]) -> str:
    raw_name = safe_name(
        f"{os.path.splitext(str(source.get('name') or f'Source_{index}'))[0]}_raw",
        f"Source_{index}_raw",
    )
    if raw_name == table_name:
        raw_name = f"{raw_name}_source"
    base = raw_name
    counter = 2
    while raw_name.lower() in emitted:
        raw_name = f"{base}_{counter}"
        counter += 1
    emitted.add(raw_name.lower())
    return raw_name


def _union_source_refs_by_node(
    workflow: dict[str, Any],
    raw_name_by_file_name: dict[str, str],
) -> dict[str, list[str]]:
    nodes = workflow.get("workflowNodes") or []
    edges = workflow.get("workflowEdges") or []
    node_by_id = {str(node.get("id")): node for node in nodes}
    refs_by_union: dict[str, list[str]] = {}

    for node in nodes:
        node_id = str(node.get("id") or "")
        if detect_tool_key(str(node.get("plugin") or "")) != "union":
            continue
        refs: list[str] = []
        seen: set[str] = set()
        for edge in edges:
            if str(edge.get("to")) != node_id:
                continue
            upstream = node_by_id.get(str(edge.get("from") or ""))
            if not upstream:
                continue
            file_name = _file_name_from_input_node(upstream).lower()
            raw_name = raw_name_by_file_name.get(file_name)
            if raw_name and raw_name.lower() not in seen:
                seen.add(raw_name.lower())
                refs.append(raw_name)
        if len(refs) > 1:
            refs_by_union[node_id] = refs

    return refs_by_union


def _source_with_sharepoint_context(source: dict[str, Any], sharepoint_url: str = "") -> dict[str, Any]:
    item = dict(source)
    if sharepoint_url:
        item["path"] = sharepoint_url
        item["siteUrl"] = sharepoint_site(sharepoint_url)
    elif not item.get("siteUrl"):
        item["siteUrl"] = sharepoint_site(str(item.get("path") or ""))
    elif not _is_http_url(str(item.get("siteUrl") or "")):
        item["siteUrl"] = sharepoint_site(str(item.get("path") or ""))
    if _is_pseudo_source_path(str(item.get("path") or "")):
        item["path"] = item.get("siteUrl") or DEFAULT_SHAREPOINT_FILE_URL
    return item


def _source_query_definition(source: dict[str, Any], query_name: str) -> str:
    steps = _source_steps(source, query_name)
    formatted: list[str] = []
    for idx, (name, expression) in enumerate(steps):
        suffix = "," if idx < len(steps) - 1 else ""
        formatted.append(f"    {name} = {expression}{suffix}")
    final_step = steps[-1][0] if steps else "Source"
    return f"{query_name} =\nlet\n" + "\n".join(formatted) + f"\nin\n    {final_step}"


def _primary_upstream(edges: list[dict[str, Any]], node_id: str) -> str:
    incoming = [edge for edge in edges if str(edge.get("to")) == str(node_id)]
    if not incoming:
        return ""
    priority = {
        "input": 0,
        "left": 0,
        "input1": 0,
        "join": 0,
        "true": 0,
        "output": 0,
        "right": 1,
        "input2": 1,
        "append": 2,
    }
    incoming.sort(key=lambda edge: priority.get(str(edge.get("toAnchor") or "").lower(), 5))
    return str(incoming[0].get("from") or "")


def _path_to_node(edges: list[dict[str, Any]], terminal_id: str) -> list[str]:
    path: list[str] = []
    seen: set[str] = set()
    current = str(terminal_id or "")
    while current and current not in seen:
        seen.add(current)
        path.append(current)
        current = _primary_upstream(edges, current)
    return list(reversed(path))


def _primary_workflow_nodes(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = workflow.get("workflowNodes") or []
    edges = workflow.get("workflowEdges") or []
    if not nodes or not edges:
        return nodes

    node_by_id = {str(node.get("id")): node for node in nodes}
    outgoing = {str(edge.get("from")) for edge in edges if edge.get("from")}
    terminal_outputs = [
        node for node in nodes
        if detect_tool_key(str(node.get("plugin", ""))) == "output data"
        and str(node.get("id")) not in outgoing
    ]
    if not terminal_outputs:
        terminal_outputs = [
            node for node in nodes
            if detect_tool_key(str(node.get("plugin", ""))) == "output data"
        ]
    if not terminal_outputs:
        return nodes

    def score(node: dict[str, Any]) -> tuple[int, int, int]:
        path = _path_to_node(edges, str(node.get("id")))
        tool_keys = [detect_tool_key(str(node_by_id.get(node_id, {}).get("plugin", ""))) for node_id in path]
        has_summary = int("summarize" in tool_keys)
        has_combine = int(any(tool in tool_keys for tool in ("join", "join multiple", "union")))
        return (has_summary, has_combine, len(path))

    terminal = max(terminal_outputs, key=score)
    path_ids = _path_to_node(edges, str(terminal.get("id")))
    return [node_by_id[node_id] for node_id in path_ids if node_id in node_by_id]


def _node_expression(node: dict[str, Any]) -> str:
    config = node.get("config") if isinstance(node.get("config"), dict) else {}
    for key in ("filterExpression", "expression", "formula", "condition"):
        value = config.get(key) if config else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("expression", "formula", "condition"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _next_name(prefix: str, index: int) -> str:
    return f"{safe_name(prefix, 'Step')}_{index}"


def _has_row_context(expression: str) -> bool:
    return bool(re.search(r"\[Row[+-]?\d+:", expression or "", flags=re.IGNORECASE))


def _looks_m_unsafe(expression: str) -> bool:
    if not expression:
        return True
    unsafe_patterns = [
        r"\bIIF\s*\(",
        r"\bIN\s*\(",
        r"\bTrimLeft\s*\(",
        r"\bTrimRight\s*\(",
        r"\[Row[+-]?\d+:",
        r",\s*,",
    ]
    return any(re.search(pattern, expression, flags=re.IGNORECASE) for pattern in unsafe_patterns)


def _extract_field_refs(expression: str) -> list[str]:
    seen: set[str] = set()
    fields: list[str] = []
    for match in re.finditer(r"\[([^\]]+)\]", expression or ""):
        field = match.group(1).strip()
        if not field or field.startswith("Row-"):
            continue
        key = field.lower()
        if key in seen:
            continue
        seen.add(key)
        fields.append(field)
    return fields


def _ensure_columns_expr(step_name: str, columns: list[str]) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for column in columns:
        value = str(column or "").strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    if not cleaned:
        return step_name
    column_list = ", ".join(f'"{_quoted(column)}"' for column in cleaned)
    return (
        f"let required = {{{column_list}}}, "
        f"existing = Table.ColumnNames({step_name}), "
        f"missing = List.Select(required, each not List.Contains(existing, _, Comparer.OrdinalIgnoreCase)) "
        f"in List.Accumulate(missing, {step_name}, "
        f"(state, col) => if Table.HasColumns(state, col) then state else Table.AddColumn(state, col, each null, type any))"
    )


def _step_for_tool(
    tool_key: str,
    current: str,
    index: int,
    node: dict[str, Any],
    workflow: dict[str, Any],
    use_llm_expressions: bool = False,
    llm_expression_conversions: list[dict[str, Any]] | None = None,
    union_source_refs_by_node: dict[str, list[str]] | None = None,
) -> tuple[str, str, str]:
    llm_expression_conversions = llm_expression_conversions if llm_expression_conversions is not None else []
    comment = f"{tool_key.title()} tool {node.get('id', index)} mapped to {ALTERYX_TOOL_MAPPINGS.get(tool_key, {}).get('m', 'manual review')}"
    original_expression = _node_expression(node)
    expression = translate_alteryx_expression(original_expression)

    if tool_key in {"input data", "browse", "output data"}:
        return _next_name("Checkpoint", index), current, comment
    if tool_key == "select":
        fields = _selected_fields(node)
        if not fields:
            return _next_name("SelectedColumns", index), current, comment + " - preserve all columns until field metadata is available."
        column_list = ", ".join(f'"{_quoted(item["rename"] or item["name"])}"' for item in fields)
        source_list = ", ".join(f'{{"{_quoted(item["name"])}", {_m_type(item.get("type", ""))}}}' for item in fields)
        selected_step = (
            "let "
            f"cols = Table.ColumnNames({current}), "
            f"ops = {{{source_list}}}, "
            f"TypedColumns = Table.TransformColumnTypes({current}, List.Select(ops, each List.Contains(cols, _{{0}}))) "
            f"in Table.SelectColumns(TypedColumns, {{{column_list}}}, MissingField.UseNull)"
        )
        return _next_name("SelectedFields", index), selected_step, comment + f" - selected {len(fields)} configured field(s)."
    if tool_key == "filter":
        if use_llm_expressions and original_expression:
            llm_expression, llm_meta = _call_llm_expression_converter(
                expression=original_expression,
                tool_key=tool_key,
                current_step=current,
                workflow=workflow,
                node=node,
            )
            llm_expression_conversions.append({
                "node_id": node.get("id"),
                "tool": tool_key,
                "source_expression": original_expression,
                "fallback_expression": expression,
                "llm_expression": llm_expression,
                **llm_meta,
            })
            if llm_expression:
                expression = llm_expression
                comment += " - filter expression converted by LLM with deterministic fallback available."
        required_refs = _extract_field_refs(expression)
        required_list = ", ".join(f'"{_quoted(column)}"' for column in required_refs)
        guarded_filter = (
            f"let required = {{{required_list}}}, "
            f"existing = Table.ColumnNames({current}), "
            f"missing = List.Select(required, each not List.Contains(existing, _, Comparer.OrdinalIgnoreCase)) "
            f"in if List.Count(missing) > 0 "
            f"then {current} "
            f"else Table.SelectRows({current}, each try ({expression}) otherwise false)"
        )
        return _next_name("FilteredRows", index), guarded_filter, comment + " - skipped automatically if referenced columns are unavailable."
    if tool_key in {"formula", "multi-field formula", "multi-row formula"}:
        formulas = _formula_config(node)
        if not formulas:
            return _next_name("FormulaApplied", index), current, comment + " - no formula metadata was found."
        if formulas and all(item.get("mode") == "transform_existing" for item in formulas):
            ops = []
            for formula in formulas:
                field = formula["field"]
                formula_expression = _convert_iif_expression(formula.get("expression", ""))
                ops.append(f'{{"{_quoted(field)}", each {formula_expression}, {_m_value_type(formula.get("type", ""))}}}')
            expression = (
                f"let cols = Table.ColumnNames({current}), "
                f"ops = {{{', '.join(ops)}}} "
                f"in Table.TransformColumns({current}, List.Select(ops, each List.Contains(cols, _{{0}})))"
            )
            return _next_name(f"Calculated_{formulas[-1]['field']}", index), expression, comment + f" - transformed {len(formulas)} existing field(s)."
        step_expr = current
        applied_fields: list[str] = []
        row_context_fields: list[str] = []
        for offset, formula in enumerate(formulas, start=1):
            field = formula["field"]
            source_expression = formula.get("expression", "")
            if _has_row_context(source_expression):
                formula_expression = "null"
                row_context_fields.append(field)
            else:
                formula_expression = _convert_iif_expression(source_expression)
            if use_llm_expressions and source_expression and not _has_row_context(source_expression):
                llm_expression, llm_meta = _call_llm_expression_converter(
                    expression=source_expression,
                    tool_key=tool_key,
                    current_step=step_expr,
                    output_field=field,
                    output_type=formula.get("type", ""),
                    workflow=workflow,
                    node=node,
                )
                llm_expression_conversions.append({
                    "node_id": node.get("id"),
                    "tool": tool_key,
                    "field": field,
                    "source_expression": source_expression,
                    "fallback_expression": formula_expression,
                    "llm_expression": llm_expression,
                    **llm_meta,
                })
                if llm_expression and not _looks_m_unsafe(llm_expression):
                    formula_expression = llm_expression
                    comment += " - formula expression converted by LLM with deterministic fallback available."
            value_type = _m_value_type(formula.get("type", ""))
            step_name = f"__Formula{index}_{offset}"
            if formula.get("mode") == "transform_existing":
                step_expr = (
                    f'let cols = Table.ColumnNames({step_expr}) '
                    f'in if List.Contains(cols, "{_quoted(field)}") '
                    f'then Table.TransformColumns({step_expr}, {{{{"{_quoted(field)}", each {formula_expression}, {value_type}}}}}) '
                    f'else {step_expr}'
                )
            else:
                ensured_step = _ensure_columns_expr(step_expr, _extract_field_refs(formula_expression))
                step_expr = f'let Input = {ensured_step} in Table.AddColumn(Input, "{_quoted(field)}", each try ({formula_expression}) otherwise null, {value_type})'
            applied_fields.append(field)
        if row_context_fields:
            comment += " - row-context formulas require validation; placeholder null columns were emitted to keep Power BI importable."
        return _next_name(f"Calculated_{applied_fields[-1] if applied_fields else 'Formula'}", index), step_expr, comment + f" - applied {len(applied_fields)} formula field(s)."
    if tool_key == "summarize":
        group_by, aggregations = _summarize_config(node)
        group_clause = "{" + ", ".join(f'"{_quoted(item)}"' for item in group_by) + "}"
        agg_parts: list[str] = []
        required_columns = [str(item) for item in group_by]
        for agg in aggregations:
            action = agg.get("action", "").lower()
            field = _quoted(agg.get("field", ""))
            rename = _quoted(agg.get("rename") or agg.get("field", ""))
            if field:
                required_columns.append(field)
            if action == "sum":
                agg_parts.append(f'{{"{rename}", each List.Sum([{field}]), type number}}')
            elif action in {"average", "avg"}:
                agg_parts.append(f'{{"{rename}", each List.Average([{field}]), type number}}')
            elif action == "count":
                agg_parts.append(f'{{"{rename}", each Table.RowCount(_), Int64.Type}}')
            elif action == "min":
                agg_parts.append(f'{{"{rename}", each List.Min([{field}]), type any}}')
            elif action == "max":
                agg_parts.append(f'{{"{rename}", each List.Max([{field}]), type any}}')
        agg_clause = "{" + ", ".join(agg_parts) + "}"
        ensured = _ensure_columns_expr(current, required_columns)
        return _next_name("GroupedData", index), f"let Input = {ensured} in Table.Group(Input, {group_clause}, {agg_clause})", comment
    if tool_key in {"join", "join multiple"}:
        return _next_name("JoinPrepared", index), current, comment + " - join partner tables must be bound during multi-stream conversion."
    if tool_key == "union":
        union_refs = (union_source_refs_by_node or {}).get(str(node.get("id") or ""))
        if union_refs:
            refs = ", ".join(union_refs)
            return _next_name("UnionCombined", index), f"Table.Combine({{{refs}}})", comment + f" - combined {len(union_refs)} incoming source stream(s)."
        return _next_name("UnionPrepared", index), current, comment + " - union requires graph-aware multi-input binding; preserved current stream."
    if tool_key == "append fields":
        return _next_name("AppendFieldsPrepared", index), current, comment + " - append-field cardinality must be validated."
    if tool_key == "unique":
        return _next_name("DistinctRows", index), f"Table.Distinct({current})", comment
    if tool_key == "sort":
        return _next_name("SortedRows", index), current, comment + " - sort columns require tool configuration."
    if tool_key == "sample":
        return _next_name("SampleRows", index), f"Table.FirstN({current}, 1000)", comment
    if tool_key == "record id":
        return _next_name("RecordIdAdded", index), f'Table.AddIndexColumn({current}, "RecordID", 1, 1, Int64.Type)', comment
    if tool_key == "data cleansing":
        return _next_name("CleanedText", index), f"Table.TransformColumns({current}, List.Transform(Table.ColumnNames({current}), each {{_, each if _ is text then Text.Clean(Text.Trim(_)) else _, type any}}))", comment
    if tool_key == "text to columns":
        return _next_name("SplitColumnsPrepared", index), current, comment + " - delimiter and target columns require tool configuration."
    if tool_key == "transpose":
        return _next_name("TransposedRows", index), current, comment + " - transpose requires configured field mapping; preserved current schema."
    if tool_key == "cross tab":
        return _next_name("PivotPrepared", index), current, comment + " - pivot keys and values require tool configuration."
    if tool_key == "find replace":
        return _next_name("ReplacePrepared", index), current, comment + " - replacement fields require tool configuration."
    if tool_key == "auto field":
        return _next_name("AutoTypedColumns", index), f"Table.TransformColumnTypes({current}, List.Transform(Table.ColumnNames({current}), each {{_, type text}}))", comment
    if tool_key == "download":
        return _next_name("DownloadedContent", index), current, comment + " - API URL should be converted to Web.Contents."
    if tool_key == "json parse":
        return _next_name("JsonParsed", index), current, comment + " - parse selected JSON field with Json.Document."
    if tool_key == "xml parse":
        return _next_name("XmlParsed", index), current, comment + " - parse selected XML field with Xml.Tables."
    return _next_name("ManualReview", index), current, f"{node.get('plugin', 'Unknown')} requires manual mapping."


def convert_workflow_to_m(
    workflow: dict[str, Any],
    source: dict[str, Any],
    sharepoint_url: str = "",
    file_name: str = "",
) -> dict[str, Any]:
    strategy = choose_generation_strategy(workflow)
    detected_file_sources = _workflow_file_sources(workflow)
    if sharepoint_url and detected_file_sources:
        detected_file_sources = [
            _source_with_sharepoint_context(item, sharepoint_url)
            for item in detected_file_sources
        ]
    if file_name and len(detected_file_sources) <= 1:
        detected_file_sources = []

    if (sharepoint_url or file_name) and not detected_file_sources:
        supplied_file_name = file_name or source.get("name") or DEFAULT_SHAREPOINT_FILE_NAME
        supplied_source_type = "json" if supplied_file_name.lower().endswith(".json") else "csv"
        source = {
            **source,
            "name": supplied_file_name,
            "type": supplied_source_type,
            "path": sharepoint_url or source.get("path") or DEFAULT_SHAREPOINT_FILE_URL,
            "siteUrl": sharepoint_site(sharepoint_url or source.get("path") or DEFAULT_SHAREPOINT_FILE_URL),
            "tool": "User supplied SharePoint CSV",
        }
    elif detected_file_sources:
        source = detected_file_sources[0]

    table_name = safe_name(workflow.get("name") or source.get("name") or "AlteryxOutput", "AlteryxOutput")
    raw_source_queries: list[dict[str, Any]] = []
    raw_query_defs: list[str] = []
    source_fields_map: dict[str, list[dict[str, Any]]] = {}
    emitted_raw_names: set[str] = set()
    raw_name_by_file_name: dict[str, str] = {}

    for src_index, detected_source in enumerate(detected_file_sources, start=1):
        raw_name = _raw_query_name_for_source(detected_source, src_index, table_name, emitted_raw_names)
        raw_query_defs.append(_source_query_definition(detected_source, raw_name))
        raw_source_queries.append({
            "name": raw_name,
            "source": detected_source,
        })
        file_key = str(detected_source.get("name") or "").strip().lower()
        if file_key:
            raw_name_by_file_name[file_key] = raw_name
        rq_fields = [
            {"name": str(f.get("name") or "").strip(), "type": str(f.get("type") or "string")}
            for f in (detected_source.get("fields") or [])
            if isinstance(f, dict) and str(f.get("name") or "").strip()
        ]
        source_fields_map[raw_name] = rq_fields

    union_source_refs = _union_source_refs_by_node(workflow, raw_name_by_file_name)
    let_steps: list[tuple[str, str, str | None]] = [(name, expr, None) for name, expr in _source_steps(source, table_name)]
    current = let_steps[-1][0]

    conversion_steps: list[dict[str, Any]] = []
    llm_expression_conversions: list[dict[str, Any]] = []
    use_llm_expressions = strategy["generation_method"] == "llm"
    mapped_count = 0
    unmapped_count = 0
    selected_nodes = _primary_workflow_nodes(workflow)
    selected_node_ids = [str(node.get("id")) for node in selected_nodes if node.get("id") is not None]
    for index, node in enumerate(selected_nodes, start=1):
        plugin = str(node.get("plugin", "Unknown"))
        tool_key = detect_tool_key(plugin)
        mapping = ALTERYX_TOOL_MAPPINGS.get(tool_key)
        if mapping:
            mapped_count += 1
        else:
            unmapped_count += 1
        name, expression, comment = _step_for_tool(
            tool_key,
            current,
            index,
            node,
            workflow,
            use_llm_expressions=use_llm_expressions,
            llm_expression_conversions=llm_expression_conversions,
            union_source_refs_by_node=union_source_refs,
        )
        if name != current or expression != current:
            let_steps.append((name, expression, comment))
            current = name
        conversion_steps.append({
            "node_id": node.get("id"),
            "plugin": plugin,
            "tool": tool_key,
            "mapped": bool(mapping),
            "m_function": mapping.get("m") if mapping else "Manual review",
            "category": mapping.get("category") if mapping else "Manual",
            "step": current,
            "note": comment,
        })

    skipped_graph_nodes = [
        str(node.get("id"))
        for node in workflow.get("workflowNodes") or []
        if str(node.get("id")) not in selected_node_ids
    ]

    formatted: list[str] = []
    for idx, (name, expression, comment) in enumerate(let_steps):
        if comment:
            formatted.append(f"    // {comment}")
        suffix = "," if idx < len(let_steps) - 1 else ""
        formatted.append(f"    {name} = {expression}{suffix}")

    output_mquery = f"{table_name} =\nlet\n" + "\n".join(formatted) + f"\nin\n    {current}"
    combined_mquery = "\n\n".join([*raw_query_defs, output_mquery]) if raw_query_defs else output_mquery
    llm_metadata: dict[str, Any] = {}
    if strategy["generation_method"] == "llm":
        model_guidance, llm_metadata = _call_llm_mapping_model(workflow, conversion_steps)
        llm_guidance = model_guidance or _llm_mapping_guidance(workflow, conversion_steps)
    else:
        llm_guidance = []

    successful_expression_conversions = [
        item for item in llm_expression_conversions
        if item.get("status") == "completed" and item.get("llm_expression")
    ]
    if strategy["generation_method"] == "llm":
        if successful_expression_conversions:
            llm_metadata["llm_used"] = True
            llm_metadata["llm_status"] = "completed"
            llm_metadata["llm_expression_conversion_count"] = len(successful_expression_conversions)
            llm_metadata["llm_model"] = successful_expression_conversions[0].get("model") or llm_metadata.get("llm_model", "")
        elif llm_expression_conversions and not llm_metadata.get("llm_used"):
            statuses = sorted({str(item.get("status") or "unknown") for item in llm_expression_conversions})
            llm_metadata["llm_status"] = "expression_fallback_" + "_".join(statuses[:3])

    # Build source_fields_map: raw_table_name -> [{"name": ..., "type": ...}, ...]
    # This carries the field schema for every _raw source table so the publish
    # pipeline can inject static column lists into the BIM even when the CSV
    # files carry no schema in the Alteryx workflow JSON (fields_count == 0).
    #
    # Only per-source fields are safe here. A multi-input workflow can combine
    # sales, customers, and products files with different schemas; workflow-level
    # inferred columns belong to downstream transforms and must not be copied to
    # every raw source table.
    return {
        "dataset_name": table_name,
        "table_name": table_name,
        "source": source,
        "source_queries": raw_source_queries,
        "source_fields_map": source_fields_map,
        "source_count": len(detected_file_sources) or (1 if source else 0),
        "combined_mquery": combined_mquery,
        "raw_script": "",
        "data_source_path": source.get("path", ""),
        "conversion_steps": conversion_steps,
        "graph_selected_node_ids": selected_node_ids,
        "graph_skipped_node_ids": skipped_graph_nodes,
        "mapped_tool_count": mapped_count,
        "unmapped_tool_count": unmapped_count,
        "tool_mappings": ALTERYX_TOOL_MAPPINGS,
        **strategy,
        **llm_metadata,
        "llm_mapping_guidance": llm_guidance,
        "llm_expression_conversions": llm_expression_conversions,
    }
