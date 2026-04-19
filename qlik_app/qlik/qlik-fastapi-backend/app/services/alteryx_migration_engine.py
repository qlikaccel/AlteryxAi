import html
import re
from typing import Any
from urllib.parse import urlparse

from app.services.alteryx_converter import (
    ALTERYX_TOOL_MAPPINGS,
    convert_workflow_to_m,
)


DEFAULT_SHAREPOINT_FILE_URL = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx"
DEFAULT_SHAREPOINT_FILE_NAME = "sales_data_1M.csv"


def _safe_name(value: str, fallback: str = "AlteryxWorkflow") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value or "").strip("_")
    return cleaned or fallback


def _sharepoint_site(url: str) -> str:
    parsed = urlparse(url or DEFAULT_SHAREPOINT_FILE_URL)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://sorimtechnologies.sharepoint.com"


def _source_from_override(sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    return {
        "name": file_name or DEFAULT_SHAREPOINT_FILE_NAME,
        "type": "csv",
        "path": sharepoint_url or DEFAULT_SHAREPOINT_FILE_URL,
        "siteUrl": _sharepoint_site(sharepoint_url or DEFAULT_SHAREPOINT_FILE_URL),
        "tool": "User supplied SharePoint CSV",
    }


def get_primary_source(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    if sharepoint_url or file_name:
        return _source_from_override(sharepoint_url, file_name)

    sources = workflow.get("dataSources") or []
    if sources:
        source = dict(sources[0])
        source.setdefault("siteUrl", _sharepoint_site(source.get("path", "")))
        return source

    return _source_from_override("", "")


def generate_m_query(workflow: dict[str, Any], sharepoint_url: str = "", file_name: str = "") -> dict[str, Any]:
    source = get_primary_source(workflow, sharepoint_url, file_name)
    return convert_workflow_to_m(workflow, source, sharepoint_url, file_name)


def generate_executive_summary(workflow: dict[str, Any]) -> dict[str, Any]:
    name = workflow.get("name", "Selected workflow")
    tool_count = workflow.get("toolCount", 0)
    unsupported_count = workflow.get("unsupportedToolCount", 0)
    supported_count = workflow.get("supportedToolCount", max(tool_count - unsupported_count, 0))
    automation_score = round((supported_count / max(tool_count, 1)) * 100) if tool_count else 0
    sources = workflow.get("dataSources") or []
    source_labels = ", ".join(sorted({s.get("type", "unknown") for s in sources})) or "user supplied source metadata"
    fit = workflow.get("convertibility", "manual_review")
    complexity = workflow.get("complexity", "manual_review")
    mapped_names = sorted({
        (tool.rsplit(".", 1)[-1] if "." in tool else tool)
        for tool in (workflow.get("toolTypes") or [])
    })[:8]

    bullets = [
        f"{name} contains {tool_count} Alteryx tool(s) and {workflow.get('connectionCount', 0)} workflow connection(s).",
        f"Detected source type coverage: {source_labels}.",
        f"Automated conversion fit is classified as {fit} with {complexity} complexity and an estimated {automation_score}% mapping score.",
        f"Primary mapped tool families include {', '.join(mapped_names) if mapped_names else 'Input, Select, Filter, Summarize, Formula, and Browse'}.",
        f"{unsupported_count} tool instance(s) require remediation before a fully automated Power BI implementation.",
        "The migration approach converts Alteryx input and transformation intent into Power Query M for Power BI/Fabric.",
        "Power BI should fetch business data directly from governed sources such as SharePoint, databases, Excel, or APIs.",
        "Validation should compare source row counts, target refresh status, schema coverage, and unsupported-tool remediation closure.",
    ]
    return {
        "bullets": bullets,
        "model": "llama_mistral_ready_deterministic_fallback",
        "success": True,
        "automation_score": automation_score,
        "source_types": source_labels,
    }


def generate_workflow_diagram(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow.get("workflowNodes") or []
    edges = workflow.get("workflowEdges") or []
    if not nodes:
        return {
            "type": "workflow",
            "mermaid": "flowchart LR\n    A[Uploaded Alteryx Workflow] --> B[Power BI Conversion Plan]",
            "message": "No node-level workflow inventory was available; showing migration flow.",
        }

    lines = ["flowchart LR"]
    node_ids = set()
    for node in nodes[:80]:
        node_id = _safe_name(str(node.get("id", "")), "Node")
        node_ids.add(str(node.get("id", "")))
        label = f"{node.get('id', '')}: {node.get('plugin', 'Tool')}"
        lines.append(f'    {node_id}["{label}"]')

    for edge in edges[:120]:
        from_raw = str(edge.get("from", ""))
        to_raw = str(edge.get("to", ""))
        if from_raw in node_ids and to_raw in node_ids:
            lines.append(f"    {_safe_name(from_raw, 'From')} --> {_safe_name(to_raw, 'To')}")

    return {"type": "workflow", "mermaid": "\n".join(lines), "message": "Workflow diagram generated from Alteryx tool connections."}


def generate_brd_html(workflow: dict[str, Any], m_query: str = "") -> str:
    summary = generate_executive_summary(workflow)["bullets"]
    diagram = generate_workflow_diagram(workflow)["mermaid"]
    recommendations = workflow.get("recommendations") or []
    sources = workflow.get("dataSources") or []
    mquery_payload = generate_m_query(workflow)
    conversion_steps = mquery_payload.get("conversion_steps") or []

    bullet_html = "".join(f"<li>{html.escape(item)}</li>" for item in summary)
    source_html = "".join(
        f"<tr><td>{html.escape(s.get('name', ''))}</td><td>{html.escape(s.get('type', ''))}</td><td>{html.escape(s.get('path', ''))}</td></tr>"
        for s in sources
    ) or "<tr><td colspan='3'>Source will be supplied during migration configuration.</td></tr>"
    rec_html = "".join(f"<li>{html.escape(item)}</li>" for item in recommendations) or "<li>No blocking remediation detected.</li>"
    mapping_html = "".join(
        "<tr>"
        f"<td>{html.escape(step.get('plugin', ''))}</td>"
        f"<td>{html.escape(step.get('tool', ''))}</td>"
        f"<td>{html.escape(step.get('m_function', ''))}</td>"
        f"<td>{'Mapped' if step.get('mapped') else 'Manual Review'}</td>"
        "</tr>"
        for step in conversion_steps
    ) or "".join(
        f"<tr><td>{html.escape(name.title())}</td><td>{html.escape(meta.get('category', ''))}</td><td>{html.escape(meta.get('m', ''))}</td><td>Available</td></tr>"
        for name, meta in list(ALTERYX_TOOL_MAPPINGS.items())[:18]
    )

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>{html.escape(workflow.get('name', 'Alteryx Workflow'))} BRD</title>
  <style>
    :root {{ --ink:#0e0e0e; --paper:#f8f4ee; --cream:#ede8df; --gold:#c49a2d; --rust:#a83a1e; --teal:#1a5c5a; --rule:#c9bfad; --muted:#6b6254; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; background:#d4cfc6; color:var(--ink); font-family:'Segoe UI', Arial, sans-serif; font-size:13px; line-height:1.65; }}
    .doc-wrapper {{ max-width:900px; margin:0 auto; padding:24px 0 80px; }}
    .page {{ position:relative; overflow:hidden; margin-bottom:24px; background:var(--paper); box-shadow:0 4px 32px rgba(0,0,0,.18); }}
    .page::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:5px; background:linear-gradient(180deg,var(--gold),var(--teal)); }}
    .page-inner {{ min-height:960px; padding:60px 64px; }}
    .cover {{ background:var(--ink); color:var(--paper); padding:58px 64px; margin:-60px -64px 36px; }}
    .doc-type {{ color:var(--gold); letter-spacing:.35em; font-size:10px; text-transform:uppercase; margin-bottom:22px; }}
    h1 {{ font-family:Georgia, serif; font-size:58px; line-height:1; margin:0 0 16px; }}
    h2 {{ margin:32px 0 14px; padding-bottom:6px; border-bottom:1px solid var(--rule); color:var(--teal); font-size:13px; letter-spacing:.1em; text-transform:uppercase; }}
    h3 {{ margin:22px 0 10px; font-family:Georgia, serif; font-size:22px; }}
    p {{ color:#333; }}
    .meta-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; margin:22px 0; }}
    .meta-card {{ background:var(--cream); border-left:3px solid var(--teal); padding:14px 16px; }}
    .meta-card span {{ display:block; color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.12em; }}
    .meta-card strong {{ display:block; margin-top:6px; overflow-wrap:anywhere; }}
    .brd-table {{ width:100%; border-collapse:collapse; margin:18px 0 28px; font-size:11.5px; }}
    .brd-table thead tr {{ background:var(--ink); color:var(--paper); }}
    .brd-table th {{ padding:10px 14px; text-align:left; font-size:10px; letter-spacing:.12em; text-transform:uppercase; }}
    .brd-table td {{ padding:9px 14px; border-bottom:1px solid var(--rule); vertical-align:top; overflow-wrap:anywhere; }}
    .brd-table tbody tr:nth-child(even) {{ background:var(--cream); }}
    .callout {{ padding:16px 20px; margin:16px 0; border-left:4px solid var(--teal); background:var(--cream); }}
    .scope-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin:16px 0 24px; }}
    .scope-box {{ padding:16px 20px; background:#fff; border-top:3px solid var(--teal); }}
    pre {{ background:var(--ink); color:#a8e6cf; padding:20px 24px; overflow:auto; border-left:3px solid var(--gold); white-space:pre-wrap; }}
    .pg-watermark {{ position:absolute; bottom:20px; left:32px; font-size:9px; color:var(--rule); letter-spacing:.08em; text-transform:uppercase; }}
    .pg-num {{ position:absolute; bottom:20px; right:32px; font-size:10px; color:var(--muted); }}
  </style>
</head>
<body>
  <div class="doc-wrapper">
    <section class="page"><div class="page-inner">
      <div class="cover">
        <div class="doc-type">Business Requirements Document</div>
        <h1>{html.escape(workflow.get('name', 'Alteryx Workflow'))}</h1>
        <p style="color:#d7d0c4">Alteryx to Power BI migration accelerator output for a workflow-specific assessment, conversion, publication, and reconciliation plan.</p>
      </div>
      <div class="meta-grid">
        <div class="meta-card"><span>Workflow File</span><strong>{html.escape(workflow.get('sourceFile', 'Uploaded workflow'))}</strong></div>
        <div class="meta-card"><span>Conversion Fit</span><strong>{html.escape(workflow.get('convertibility', 'manual_review'))}</strong></div>
        <div class="meta-card"><span>Tools</span><strong>{workflow.get('toolCount', 0)} tool(s), {workflow.get('connectionCount', 0)} connection(s)</strong></div>
        <div class="meta-card"><span>Target</span><strong>Power BI semantic model / dataflow</strong></div>
      </div>
      <h2>Executive Summary</h2>
      <ul>{bullet_html}</ul>
      <h2>Source Inventory</h2>
      <table class="brd-table"><thead><tr><th>Name</th><th>Type</th><th>Path</th></tr></thead><tbody>{source_html}</tbody></table>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">01</div>
    </div></section>
    <section class="page"><div class="page-inner">
      <h2>Functional Scope</h2>
      <div class="scope-grid">
        <div class="scope-box"><h3>In Scope</h3><p>Parse Alteryx workflow metadata, infer source paths, convert supported tools to M Query, publish to Power BI, and reconcile migration status.</p></div>
        <div class="scope-box"><h3>Requires Review</h3><p>Macros, custom code, spatial/predictive tools, dynamic input, multi-stream joins, and credential-bound database/API connections.</p></div>
      </div>
      <h2>Tool Mapping Register</h2>
      <table class="brd-table"><thead><tr><th>Alteryx Plugin</th><th>Tool Family</th><th>Power Query M Mapping</th><th>Status</th></tr></thead><tbody>{mapping_html}</tbody></table>
      <h2>Migration Requirements</h2>
      <div class="callout">The migrated Power BI artifact must retrieve data directly from governed source paths, preserve Alteryx transformation intent where deterministic mappings exist, and isolate unsupported logic for remediation.</div>
      <ul>
        <li>Convert supported tools such as Filter, Formula, Select, Join, Union, Summarize, Sort, Unique, and Record ID to Power Query M.</li>
        <li>Use SharePoint.Files, File.Contents, Odbc.DataSource, Excel.Workbook, Web.Contents, Json.Document, and Xml.Tables based on the detected source type.</li>
        <li>Publish the generated artifact to the configured Power BI workspace and expose the publish API endpoint for operational traceability.</li>
        <li>Generate validation checks for source detection, conversion completeness, publish status, dataset identifier, and remediation closure.</li>
      </ul>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">02</div>
    </div></section>
    <section class="page"><div class="page-inner">
      <h2>Workflow Diagram</h2>
      <pre>{html.escape(diagram)}</pre>
      <h2>Remediation Notes</h2>
      <ul>{rec_html}</ul>
      <h2>Generated Power Query</h2>
      <pre>{html.escape(m_query or mquery_payload.get('combined_mquery') or 'Generate M Query before publication.')}</pre>
      <div class="pg-watermark">Alteryx Power BI BRD - Confidential</div><div class="pg-num">03</div>
    </div></section>
  </div>
</body>
</html>"""


def validate_migration(workflow: dict[str, Any], publish_result: dict[str, Any] | None = None) -> dict[str, Any]:
    publish_result = publish_result or {}
    checks = [
        {
            "name": "Workflow parsed",
            "status": "pass" if workflow.get("toolCount", 0) > 0 else "warning",
            "detail": f"{workflow.get('toolCount', 0)} tool(s) detected.",
        },
        {
            "name": "Source detected",
            "status": "pass" if workflow.get("dataSources") else "warning",
            "detail": f"{len(workflow.get('dataSources') or [])} source candidate(s) detected.",
        },
        {
            "name": "Unsupported tools",
            "status": "pass" if not workflow.get("unsupportedTools") else "warning",
            "detail": f"{workflow.get('unsupportedToolCount', 0)} unsupported tool instance(s).",
        },
        {
            "name": "Power BI publish",
            "status": "pass" if publish_result.get("success") else "pending",
            "detail": publish_result.get("message") or "Publish has not completed in this session.",
        },
    ]
    return {
        "success": all(check["status"] in {"pass", "warning"} for check in checks),
        "checks": checks,
        "publish_result": publish_result,
    }
