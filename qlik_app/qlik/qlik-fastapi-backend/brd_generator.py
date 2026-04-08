import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value or "document").strip("_")
    return slug or "document"


def extract_json_object(raw_text: str) -> Dict[str, Any]:
    if not raw_text:
        return {}

    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}

    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def merge_defaults(defaults: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(defaults)
    for key, value in (overrides or {}).items():
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    return merged


def build_prompt_context(
    app_title: str,
    app_id: str,
    summary: Dict[str, Any],
    tables: List[Dict[str, Any]],
    fields: List[Dict[str, Any]],
    sheets: List[Dict[str, Any]],
    script: str,
    relationships: List[Dict[str, Any]],
    table_samples: List[Dict[str, Any]],
    project_scope: str = "application",
    application_inventory: Optional[List[Dict[str, Any]]] = None,
) -> str:
    compact_tables = []
    for table in tables[:20]:
        compact_tables.append(
            {
                "name": table.get("name", "Unknown"),
                "row_count": table.get("no_of_rows") or table.get("row_count") or 0,
                "field_count": len(table.get("fields", [])),
                "fields": [field.get("name", "") for field in table.get("fields", [])[:20]],
            }
        )

    compact_samples = []
    for sample in table_samples[:6]:
        compact_samples.append(
            {
                "table": sample.get("table", "Unknown"),
                "columns": sample.get("columns", [])[:12],
                "rows": sample.get("rows", [])[:3],
            }
        )

    compact_relationships = []
    for rel in relationships[:20]:
        compact_relationships.append(
            {
                "from": f"{rel.get('fromTable', '')}.{rel.get('fromColumn', '')}",
                "to": f"{rel.get('toTable', '')}.{rel.get('toColumn', '')}",
                "cardinality": rel.get("cardinality", "unknown"),
            }
        )

    payload = {
        "project_scope": project_scope,
        "app_title": app_title,
        "app_id": app_id,
        "summary": summary,
        "table_inventory": compact_tables,
        "field_count": len(fields or []),
        "sheets": [sheet.get("name", "") for sheet in sheets[:15]],
        "relationships": compact_relationships,
        "script_excerpt": (script or "")[:5000],
        "table_samples": compact_samples,
    }

    if project_scope == "project":
        payload["application_inventory"] = [
            {
                "name": app.get("name", "Unknown"),
                "app_id": app.get("app_id", ""),
                "table_count": app.get("table_count", 0),
                "field_count": app.get("field_count", 0),
                "sheet_count": app.get("sheet_count", 0),
                "has_script": app.get("has_script", False),
            }
            for app in (application_inventory or [])[:25]
        ]

    return json.dumps(payload, indent=2, ensure_ascii=True)


def build_brd_prompt(context_json: str, project_scope: str = "application") -> str:
    scope_label = "selected Qlik application" if project_scope == "application" else "entire Qlik analytics project portfolio"
    return f"""
You are a senior enterprise business analyst and solution architect.

Analyze the {scope_label} end-to-end using only the supplied context.
You are preparing content for a formal Business Requirements Document.

Requirements:
- Infer the business purpose from metadata, table names, fields, relationships, sheets, and script logic.
- When the scope is a project portfolio, synthesize the shared end-to-end flow across applications and call out major components clearly.
- Be factual and conservative. Do not invent integrations, repositories, or business flows not supported by the context.
- When something is not explicit, phrase it as a reasoned interpretation, not a fact.
- Keep the output concise but rich enough for a multi-section BRD.
- Return valid JSON only.

Return this JSON shape:
{{
  "project_type": "short phrase",
  "project_subtitle": "short phrase",
  "project_overview": "2-4 sentence paragraph",
  "executive_summary_bullets": ["bullet", "bullet", "bullet", "bullet", "bullet"],
  "business_objectives": [{{"objective":"...","target":"..."}}],
  "in_scope": ["..."],
  "out_of_scope": ["..."],
  "architecture_style": "2-3 sentence paragraph",
  "design_patterns": [{{"pattern":"...","where_applied":"...","benefit":"..."}}],
  "module_summaries": [{{"name":"...","description":"...","tag":"..."}}],
  "actors": [{{"actor":"...","type":"...","description":"...","system_access":"..."}}],
  "use_cases": [{{"id":"UC-01","use_case":"...","actor":"...","main_flow":"...","post_condition":"..."}}],
  "data_flow_processes": [{{"process":"...","input":"...","data_store":"...","output":"..."}}],
  "technical_limitations": [{{"id":"L-01","limitation":"...","priority":"critical|high|medium|low","recommended_fix":"..."}}],
  "security_checklist": [{{"status":"pass|warn|fail","text":"..."}}],
  "migration_phases": [{{"phase":"Phase 1","title":"...","duration":"...","description":"..."}}],
  "glossary": [{{"term":"...","definition":"..."}}],
  "setup_errors": [{{"error":"...","cause":"...","fix":"..."}}],
  "coding_conventions": [{{"convention":"...","rule":"..."}}]
}}

Selected application context:
{context_json}
""".strip()


def build_default_document(
    app_title: str,
    app_id: str,
    summary: Dict[str, Any],
    tables: List[Dict[str, Any]],
    fields: List[Dict[str, Any]],
    sheets: List[Dict[str, Any]],
    script: str,
    relationships: List[Dict[str, Any]],
    table_samples: List[Dict[str, Any]],
    project_scope: str = "application",
    application_inventory: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    date_label = datetime.now().strftime("%B %d, %Y")
    summary = dict(summary or {})
    application_inventory = application_inventory or []
    app_count = len(application_inventory)
    app_type = "Qlik Analytics Application" if project_scope == "application" else "Qlik End-to-End Project Portfolio"
    sheet_names = [sheet.get("name", "") for sheet in sheets if sheet.get("name")]
    sheet_preview = ", ".join(sheet_names[:6]) if sheet_names else "Not explicitly available"
    fields_count = len(fields or [])
    table_count = len(tables or [])
    relationship_count = len(relationships or [])
    has_script = bool((script or "").strip())

    tech_stack = [
        "Qlik Cloud",
        "Qlik Engine API",
        "Qlik Load Script",
        "FastAPI",
        "React",
        "Power BI Migration",
    ]

    if project_scope == "project":
        business_objectives = [
            {"objective": f"Document the {app_title} project end to end", "target": "Single consolidated BRD"},
            {"objective": "Explain cross-application architecture and project flow", "target": f"{app_count} application(s) synthesized"},
            {"objective": "Capture shared data handling patterns and component structure", "target": f"{table_count} modeled table(s) across the portfolio"},
            {"objective": "Support migration, governance, and stakeholder alignment", "target": "Reusable project-level documentation"},
        ]
    else:
        business_objectives = [
            {"objective": f"Document the {app_title} application end to end", "target": "Single business-facing BRD"},
            {"objective": "Explain data model structure and business entities", "target": f"{table_count} modeled table(s)"},
            {"objective": "Capture inferred relationships and logic", "target": f"{relationship_count} inferred relationship(s)"},
            {"objective": "Support downstream migration and governance", "target": "Reusable functional documentation"},
        ]

    module_summaries = []
    if project_scope == "project" and application_inventory:
        for index, application in enumerate(application_inventory[:8], start=1):
            module_summaries.append(
                {
                    "name": application.get("name", f"Application {index}"),
                    "description": (
                        f"Project component with {application.get('table_count', 0)} table(s), "
                        f"{application.get('field_count', 0)} field(s), and {application.get('sheet_count', 0)} sheet(s)."
                    ),
                    "tag": "Has Script" if application.get("has_script") else "Metadata Only",
                }
            )
    else:
        for index, table in enumerate(tables[:8], start=1):
            module_summaries.append(
                {
                    "name": table.get("name", f"Module {index}"),
                    "description": (
                        f"Primary data entity with {len(table.get('fields', []))} field(s) and "
                        f"{table.get('no_of_rows') or table.get('row_count') or 0} detected row(s)."
                    ),
                    "tag": ", ".join(field.get("name", "") for field in table.get("fields", [])[:4]) or "Qlik table",
                }
            )

    use_cases = []
    if project_scope == "project" and application_inventory:
        for index, application in enumerate(application_inventory[:6], start=1):
            use_cases.append(
                {
                    "id": f"UC-{index:02d}",
                    "use_case": f"Review {application.get('name', f'Application {index}')} within the consolidated project flow",
                    "actor": "Business Analyst",
                    "main_flow": "Assess the application role, inspect its data entities, and relate it to the wider project architecture.",
                    "post_condition": f"The business purpose and dependencies of {application.get('name', 'the application')} are documented.",
                }
            )
    else:
        for index, table in enumerate(tables[:6], start=1):
            use_cases.append(
                {
                    "id": f"UC-{index:02d}",
                    "use_case": f"Analyze {table.get('name', f'Table {index}')}",
                    "actor": "Business Analyst",
                    "main_flow": "Open the application, inspect relevant sheet objects, and review filtered records.",
                    "post_condition": f"Insights are produced for {table.get('name', 'the selected entity')}",
                }
            )

    data_flow_processes = []
    if project_scope == "project":
        data_flow_processes = [
            {
                "process": "1.0 Discover application landscape",
                "input": "Qlik application inventory and metadata",
                "data_store": "Project application catalog",
                "output": f"{app_count} application(s) profiled for the BRD",
            },
            {
                "process": "2.0 Consolidate data model signals",
                "input": "Tables, fields, relationships, and load scripts from all applications",
                "data_store": "Combined project metadata model",
                "output": f"{table_count} table(s) and {fields_count} field(s) interpreted at project scope",
            },
            {
                "process": "3.0 Infer end-to-end business flow",
                "input": "Application summaries, sheets, and representative sample rows",
                "data_store": "Consolidated BRD analysis context",
                "output": "Project-wide process flow and component responsibilities",
            },
        ]
    else:
        for index, table in enumerate(tables[:5], start=1):
            data_flow_processes.append(
                {
                    "process": f"{index}.0 Prepare {table.get('name', f'Table {index}')}",
                    "input": "Qlik load script and source data",
                    "data_store": table.get("name", f"Table {index}"),
                    "output": f"Modeled {table.get('name', f'table {index}')} dataset",
                }
            )

    technical_limitations = [
        {"id": "L-01", "limitation": "Business semantics must be inferred from technical metadata.", "priority": "medium", "recommended_fix": "Add business definitions and glossary notes directly in the app governance model."},
        {"id": "L-02", "limitation": "Relationship accuracy depends on available table and field naming quality.", "priority": "medium", "recommended_fix": "Add explicit key annotations and standardized naming conventions."},
        {"id": "L-03", "limitation": "Only limited row previews are used for narrative generation.", "priority": "low", "recommended_fix": "Augment BRD generation with curated sample KPIs and sheet screenshots."},
    ]

    security_checklist = [
        {"status": "pass", "text": "Application metadata can be accessed through authenticated Qlik APIs."},
        {"status": "warn", "text": "Business rules are inferred from load script logic and may require analyst validation."},
        {"status": "warn", "text": "Sensitive data classification is not explicit in the available metadata."},
    ]

    migration_phases = [
        {"phase": "Phase 1", "title": "Business Context Validation", "duration": "1-2 weeks", "description": "Review generated BRD content with domain stakeholders and validate business terminology."},
        {"phase": "Phase 2", "title": "Data Model Rationalization", "duration": "1-3 weeks", "description": "Confirm keys, table purpose, and transformation logic before migration or semantic modeling."},
        {"phase": "Phase 3", "title": "Migration Execution", "duration": "2-6 weeks", "description": "Use the validated BRD as the baseline for M Query, CSV, or Power BI semantic-model migration."},
    ]

    glossary = [
        {"term": "Qlik App", "definition": "Packaged analytics application containing data model, script, sheets, and visual objects."},
        {"term": "Load Script", "definition": "Qlik transformation logic used to load, join, derive, and shape the data model."},
        {"term": "Field", "definition": "Named column or attribute available in the application data model."},
        {"term": "Sheet", "definition": "Interactive Qlik canvas where users consume charts, KPIs, and filters."},
        {"term": "Relationship", "definition": "Logical association inferred between entities based on shared business keys or field names."},
    ]

    setup_errors = [
        {"error": "Qlik tenant connectivity failure", "cause": "Missing or invalid tenant URL or API key", "fix": "Verify tenant configuration and session credentials before generation."},
        {"error": "Incomplete business narrative", "cause": "Application metadata lacks descriptive naming", "fix": "Validate the generated document with the app owner and enrich missing business terms."},
    ]

    coding_conventions = [
        {"convention": "Table Naming", "rule": "Use business-readable table names and avoid ambiguous technical abbreviations where possible."},
        {"convention": "Field Semantics", "rule": "Reserve suffixes such as _id, _date, and _amount for consistent business interpretation."},
        {"convention": "Script Maintainability", "rule": "Keep joins, residents, and derived fields explicit so downstream documentation remains reliable."},
    ]

    table_inventory = []
    for table in tables[:20]:
        table_inventory.append(
            {
                "name": table.get("name", "Unknown"),
                "rows": table.get("no_of_rows") or table.get("row_count") or 0,
                "field_count": len(table.get("fields", [])),
                "sample_fields": ", ".join(field.get("name", "") for field in table.get("fields", [])[:6]) or "N/A",
            }
        )

    relationship_rows = []
    for rel in relationships[:25]:
        relationship_rows.append(
            {
                "from": f"{rel.get('fromTable', '')}.{rel.get('fromColumn', '')}",
                "to": f"{rel.get('toTable', '')}.{rel.get('toColumn', '')}",
                "cardinality": rel.get("cardinality", "unknown"),
            }
        )

    project_flow_summary = (
        f"The project-level flow begins by enumerating {app_count} Qlik application(s), then consolidates table structures, fields, sheets, and load scripts into a shared metadata view. "
        f"From that combined landscape, the document derives a unified view of architecture, data handling, and component responsibilities across {table_count} table(s) and {relationship_count} inferred relationship(s)."
        if project_scope == "project"
        else "The application flow begins with load-script driven ingestion, proceeds through data-model shaping and sheet presentation, and ends with business analysis through the curated Qlik experience."
    )

    return {
        "project_scope": project_scope,
        "app_title": app_title,
        "app_id": app_id,
        "date_label": date_label,
        "project_type": app_type,
        "project_subtitle": "Business Requirements Document",
        "project_overview": (
            f"{app_title} is documented here as a {'Qlik analytics project portfolio' if project_scope == 'project' else 'Qlik analytics application'} derived from live metadata, load-script logic, "
            f"sheet structure, entity definitions, and inferred table relationships. The current inspection found "
            f"{app_count} application(s), {table_count} table(s), {fields_count} field(s), {len(sheets or [])} sheet(s), and {relationship_count} inferred relationship(s), forming the basis of this BRD."
        ),
        "executive_summary_bullets": [
            f"{'Project' if project_scope == 'project' else 'Application'} title: {app_title}",
            f"Detected {app_count} application(s) in the analyzed scope." if project_scope == "project" else "Application scope: single app analysis.",
            f"Detected {table_count} modeled table(s) across the {'project scope' if project_scope == 'project' else 'application'}.",
            f"Detected {fields_count} field(s) in the exposed data model.",
            f"Detected {relationship_count} inferred relationship(s) for business navigation.",
            f"Load script available: {'Yes' if has_script else 'No'}.",
        ],
        "business_objectives": business_objectives,
        "in_scope": [
            "Application metadata and structural analysis",
            "Table and field inventory",
            "Load script logic review",
            "Relationship inference and data-model interpretation",
            f"Project-wide application inventory review: {app_count} application(s)" if project_scope == "project" else None,
            f"Sheet inventory review: {sheet_preview}",
        ],
        "out_of_scope": [
            "Direct business-owner interviews",
            "Manual validation of every inferred rule",
            "Source-system lineage beyond exposed Qlik metadata",
            "Production migration execution itself",
        ],
        "architecture_style": (
            "The application follows a Qlik semantic analytics architecture in which load-script transformations shape the data model, "
            "tables and fields define analytical entities, and sheets expose curated user-facing insights. This document interprets that "
            "architecture from the available metadata and script logic."
            if project_scope == "application"
            else "The project follows a portfolio-style Qlik analytics architecture in which multiple applications contribute distinct data models, scripts, and sheets. "
                 "This BRD consolidates those components into a single end-to-end view of architecture, data handling, and functional flow across the project."
        ),
        "design_patterns": [
            {"pattern": "Semantic Layer Modeling", "where_applied": "Qlik tables, fields, and inferred relationships", "benefit": "Creates reusable analytical entities for exploration and reporting."},
            {"pattern": "Script-Based Transformation", "where_applied": "Load script and resident/join logic", "benefit": "Centralizes business rules before visual consumption."},
            {"pattern": "Portfolio Consolidation", "where_applied": "Project-wide app inventory and combined metadata analysis", "benefit": "Creates a unified documentation view across multiple applications."} if project_scope == "project" else None,
        ],
        "module_summaries": module_summaries,
        "actors": [
            {"actor": "Business Analyst", "type": "Primary", "description": "Explores sheets, entities, and KPIs to understand business outcomes.", "system_access": "Qlik application consumer"},
            {"actor": "Data Engineer", "type": "Primary", "description": "Maintains load script, field structure, and migration readiness.", "system_access": "Model and script maintenance"},
            {"actor": "Business Stakeholder", "type": "Secondary", "description": "Validates the BRD narrative against real business processes.", "system_access": "Requirements and sign-off"},
        ],
        "use_cases": use_cases,
        "data_flow_processes": data_flow_processes,
        "technical_limitations": technical_limitations,
        "security_checklist": security_checklist,
        "migration_phases": migration_phases,
        "glossary": glossary,
        "setup_errors": setup_errors,
        "coding_conventions": coding_conventions,
        "table_inventory": table_inventory,
        "relationships": relationship_rows,
        "application_inventory": application_inventory,
        "table_samples": table_samples[:6],
        "tech_stack": tech_stack,
        "summary": {
            **summary,
            "application_count": summary.get("application_count", app_count),
            "relationship_count": summary.get("relationship_count", relationship_count),
        },
        "sheets": sheet_names,
        "project_flow_summary": project_flow_summary,
        "script_excerpt": (script or "")[:3000],
        "revision_history": [
            {"version": "v0.1", "date": date_label, "author": "QlikAI BRD Generator", "changes": f"Initial AI-assisted BRD generation from live {'project-wide' if project_scope == 'project' else 'application'} metadata."},
        ],
    }


def _render_table(headers: List[str], rows: List[List[str]]) -> str:
    head_html = "".join(f"<th>{_e(header)}</th>" for header in headers)
    body_parts = []
    for row in rows:
        cols = "".join(f"<td>{_e(col)}</td>" for col in row)
        body_parts.append(f"<tr>{cols}</tr>")
    body_html = "".join(body_parts) or f"<tr><td colspan=\"{len(headers)}\">No data available</td></tr>"
    return f"<table class=\"brd-table\"><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _render_list(items: List[str]) -> str:
    if not items:
        return "<p>Not explicitly available in current metadata.</p>"
    return "<ul class=\"check-list\">" + "".join(
        f"<li><span class=\"check-mark\">•</span><span>{_e(item)}</span></li>" for item in items
    ) + "</ul>"


def _render_module_cards(modules: List[Dict[str, Any]]) -> str:
    cards = []
    for index, module in enumerate(modules[:8], start=1):
        cards.append(
            "<div class=\"module-card\" data-num=\"{num:02d}\">"
            "<h4>{name}</h4>"
            "<p>{description}</p>"
            "<span class=\"module-tag\">{tag}</span>"
            "</div>".format(
                num=index,
                name=_e(module.get("name", f"Module {index}")),
                description=_e(module.get("description", "")),
                tag=_e(module.get("tag", "Qlik entity")),
            )
        )
    return "<div class=\"module-grid\">" + "".join(cards) + "</div>"


def _render_er_grid(tables: List[Dict[str, Any]]) -> str:
    blocks = []
    for table in tables[:6]:
        fields = table.get("sample_fields", "")
        field_items = [item.strip() for item in fields.split(",") if item.strip()]
        rows = "".join(
            f"<div class=\"er-field\"><span>{_e(name)}</span><span class=\"type\">FIELD</span></div>"
            for name in field_items[:8]
        ) or "<div class=\"er-field\"><span>No fields available</span><span class=\"type\">N/A</span></div>"
        blocks.append(
            f"<div class=\"er-table\"><div class=\"er-table-head\">{_e(table.get('name', 'Unknown'))}</div>{rows}</div>"
        )
    return "<div class=\"er-grid\">" + "".join(blocks) + "</div>"


def _render_sample_tables(samples: List[Dict[str, Any]]) -> str:
    if not samples:
        return "<p>No table preview rows were available during generation.</p>"

    parts = []
    for sample in samples[:4]:
        columns = sample.get("columns", [])
        rows = sample.get("rows", [])
        table_rows = []
        for row in rows[:3]:
            table_rows.append([row.get(column, "") for column in columns[:8]])
        parts.append(f"<h3>{_e(sample.get('table', 'Sample'))}</h3>")
        parts.append(_render_table(columns[:8] or ["Preview"], table_rows or [["No preview rows"]]))
    return "".join(parts)


def render_brd_html(document: Dict[str, Any]) -> str:
    app_title = document.get("app_title", "Qlik Application")
    date_label = document.get("date_label", datetime.now().strftime("%B %d, %Y"))
    project_type = document.get("project_type", "Qlik Analytics Application")
    project_subtitle = document.get("project_subtitle", "Business Requirements Document")
    project_scope = document.get("project_scope", "application")
    summary = document.get("summary", {}) or {}
    business_objectives = document.get("business_objectives", []) or []
    module_summaries = document.get("module_summaries", []) or []
    actors = document.get("actors", []) or []
    use_cases = document.get("use_cases", []) or []
    data_flow_processes = document.get("data_flow_processes", []) or []
    technical_limitations = document.get("technical_limitations", []) or []
    security_checklist = document.get("security_checklist", []) or []
    migration_phases = document.get("migration_phases", []) or []
    glossary = document.get("glossary", []) or []
    setup_errors = document.get("setup_errors", []) or []
    coding_conventions = document.get("coding_conventions", []) or []
    relationships = document.get("relationships", []) or []
    table_inventory = document.get("table_inventory", []) or []
    application_inventory = document.get("application_inventory", []) or []
    revision_history = document.get("revision_history", []) or []
    tech_stack = document.get("tech_stack", []) or []

    objectives_table = _render_table(
        ["#", "Objective", "Target"],
        [[f"{index:02d}", row.get("objective", ""), row.get("target", "")] for index, row in enumerate(business_objectives[:8], start=1)],
    )
    charter_table = _render_table(
        ["Attribute", "Value"],
        [
            row for row in [
                ["Project" if project_scope == "project" else "Application", app_title],
                ["Document Type", project_subtitle],
                ["Project Type", project_type],
                ["Generated On", date_label],
                ["Project ID" if project_scope == "project" else "App ID", document.get("app_id", "")],
                ["Applications Analyzed", str(summary.get("application_count", len(application_inventory)))] if project_scope == "project" else None,
                ["Tables Detected", str(summary.get("table_count", len(table_inventory)))],
                ["Fields Detected", str(summary.get("total_fields", 0))],
                ["Sheets Detected", str(summary.get("sheet_count", len(document.get("sheets", []))))],
                ["Relationships Inferred", str(summary.get("relationship_count", len(relationships)))],
            ] if row
        ],
    )
    architecture_table = _render_table(
        ["Layer", "Technology", "Responsibility"],
        [
            ["Metadata Layer", "Qlik Engine API", "Retrieves application identity, tables, fields, and sheet metadata."],
            ["Transformation Layer", "Qlik Load Script", "Encodes joins, resident loads, and business logic shaping the data model."],
            ["Analytical Layer", "Qlik Sheets", "Exposes user-facing analysis paths and business consumption patterns."],
            ["Documentation Layer", "LLM + Template Renderer", "Transforms technical signals into a business-readable BRD."],
        ],
    )
    design_patterns_table = _render_table(
        ["Pattern", "Where Applied", "Benefit"],
        [[row.get("pattern", ""), row.get("where_applied", ""), row.get("benefit", "")] for row in document.get("design_patterns", [])[:6] if row],
    )
    application_inventory_table = _render_table(
        ["Application", "App ID", "Tables", "Fields", "Sheets", "Script"],
        [
            [
                row.get("name", ""),
                row.get("app_id", ""),
                str(row.get("table_count", 0)),
                str(row.get("field_count", 0)),
                str(row.get("sheet_count", 0)),
                "Yes" if row.get("has_script") else "No",
            ]
            for row in application_inventory[:20]
        ],
    )
    table_inventory_table = _render_table(
        ["Table", "Rows", "Field Count", "Sample Fields"],
        [[row.get("name", ""), str(row.get("rows", 0)), str(row.get("field_count", 0)), row.get("sample_fields", "")] for row in table_inventory[:20]],
    )
    relationship_table = _render_table(
        ["From", "To", "Cardinality"],
        [[row.get("from", ""), row.get("to", ""), row.get("cardinality", "")] for row in relationships[:20]],
    )
    actors_table = _render_table(
        ["Actor", "Type", "Description", "System Access"],
        [[row.get("actor", ""), row.get("type", ""), row.get("description", ""), row.get("system_access", "")] for row in actors[:10]],
    )
    use_cases_table = _render_table(
        ["ID", "Use Case", "Actor", "Main Flow", "Post-Condition"],
        [[row.get("id", ""), row.get("use_case", ""), row.get("actor", ""), row.get("main_flow", ""), row.get("post_condition", "")] for row in use_cases[:12]],
    )
    data_flow_table = _render_table(
        ["Process", "Input", "Data Store", "Output"],
        [[row.get("process", ""), row.get("input", ""), row.get("data_store", ""), row.get("output", "")] for row in data_flow_processes[:10]],
    )
    setup_errors_table = _render_table(
        ["Error", "Cause", "Fix"],
        [[row.get("error", ""), row.get("cause", ""), row.get("fix", "")] for row in setup_errors[:10]],
    )
    coding_conventions_table = _render_table(
        ["Convention", "Rule"],
        [[row.get("convention", ""), row.get("rule", "")] for row in coding_conventions[:10]],
    )
    limitations_table = _render_table(
        ["ID", "Limitation", "Priority", "Recommended Fix"],
        [[row.get("id", ""), row.get("limitation", ""), str(row.get("priority", "")).upper(), row.get("recommended_fix", "")] for row in technical_limitations[:10]],
    )
    revision_table = _render_table(
        ["Version", "Date", "Author", "Changes"],
        [[row.get("version", ""), row.get("date", ""), row.get("author", ""), row.get("changes", "")] for row in revision_history[:10]],
    )

    security_html = "<ul class=\"check-list\">" + "".join(
        f"<li><span class=\"check-mark\">{_e('✔' if row.get('status') == 'pass' else '⚠' if row.get('status') == 'warn' else '✘')}</span><span>{_e(row.get('text', ''))}</span></li>"
        for row in security_checklist[:10]
    ) + "</ul>"

    glossary_html = "<div class=\"glossary-grid\">" + "".join(
        f"<div class=\"glossary-item\"><div class=\"glossary-term\">{_e(row.get('term', ''))}</div><div class=\"glossary-def\">{_e(row.get('definition', ''))}</div></div>"
        for row in glossary[:12]
    ) + "</div>"

    migration_html = "<div class=\"phase-list\">" + "".join(
        f"<div class=\"phase-item\"><div class=\"phase-num\">{_e(row.get('phase', 'Phase'))}</div><div class=\"phase-title\">{_e(row.get('title', ''))}</div><div class=\"phase-dur\">Duration: {_e(row.get('duration', 'TBD'))}</div><div class=\"phase-desc\">{_e(row.get('description', ''))}</div></div>"
        for row in migration_phases[:8]
    ) + "</div>"

    tech_pills = "".join(f"<span class=\"tech-pill\">{_e(item)}</span>" for item in tech_stack[:10])
    sheet_rows = "".join(f"<span class=\"tech-pill\">{_e(item)}</span>" for item in document.get("sheets", [])[:12]) or "<span class=\"tech-pill\">No sheets detected</span>"
    project_label = "Project" if project_scope == "project" else "Application"
    project_id_label = "Project ID" if project_scope == "project" else "App ID"
    cover_applications_html = ""
    if project_scope == "project":
        cover_applications_html = (
            f'<div class="cover-meta-row"><span>Applications</span><span>{_e(summary.get("application_count", len(application_inventory)))}</span></div>'
        )
    application_inventory_section = ""
    if project_scope == "project":
        application_inventory_section = f'<h2>2.4 Application Inventory</h2>{application_inventory_table}'
    sheet_inventory_label = "2.5" if project_scope == "project" else "2.4"

    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
<meta charset=\"UTF-8\">
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
<title>BRD - {_e(app_title)}</title>
<link href=\"https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@300;400;500&family=Syne:wght@400;600;700;800&display=swap\" rel=\"stylesheet\">
<style>
  :root {{
    --ink: #0e0e0e;
    --paper: #f8f4ee;
    --cream: #ede8df;
    --gold: #c49a2d;
    --gold-light: #e8c96a;
    --rust: #a83a1e;
    --teal: #1a5c5a;
    --rule: #c9bfad;
    --muted: #6b6254;
    --page-w: 900px;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #d4cfc6; font-family: 'DM Mono', monospace; color: var(--ink); font-size: 13px; line-height: 1.65; }}
  .toc-nav {{ position: fixed; top: 0; left: 0; right: 0; background: var(--ink); color: #fff; z-index: 100; display: flex; align-items: center; overflow-x: auto; padding: 0 24px; height: 44px; scrollbar-width: none; }}
  .toc-nav::-webkit-scrollbar {{ display: none; }}
  .toc-nav-brand {{ font-family: 'Syne', sans-serif; font-weight: 800; font-size: 11px; letter-spacing: 0.15em; text-transform: uppercase; color: var(--gold-light); white-space: nowrap; margin-right: 24px; }}
  .toc-nav a {{ color: #aaa; text-decoration: none; font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; padding: 0 12px; height: 44px; display: flex; align-items: center; white-space: nowrap; border-right: 1px solid #222; }}
  .doc-wrapper {{ max-width: var(--page-w); margin: 0 auto; padding: 60px 0 80px; }}
  .page {{ background: var(--paper); margin-bottom: 24px; box-shadow: 0 4px 32px rgba(0,0,0,0.18), 0 1px 4px rgba(0,0,0,0.12); position: relative; overflow: hidden; }}
  .page::before {{ content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: linear-gradient(180deg, var(--gold) 0%, var(--teal) 100%); }}
  .page-inner {{ padding: 60px 64px; min-height: 980px; }}
  .cover-page .page-inner {{ display: flex; flex-direction: column; justify-content: space-between; padding: 0; min-height: 1060px; }}
  .cover-header {{ background: var(--ink); padding: 48px 64px 40px; color: var(--paper); }}
  .cover-logo-row {{ display: flex; align-items: center; gap: 16px; margin-bottom: 32px; }}
  .cover-logo-icon {{ width: 48px; height: 48px; border: 2px solid var(--gold); display: flex; align-items: center; justify-content: center; font-size: 22px; }}
  .cover-org {{ font-family: 'Syne', sans-serif; font-weight: 700; font-size: 11px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--gold-light); }}
  .cover-div {{ font-size: 10px; color: #888; letter-spacing: 0.12em; text-transform: uppercase; margin-top: 2px; }}
  .cover-title-block {{ border-top: 1px solid #333; padding-top: 32px; }}
  .cover-doc-type {{ font-size: 10px; letter-spacing: 0.25em; text-transform: uppercase; color: var(--gold); margin-bottom: 16px; }}
  .cover-title {{ font-family: 'DM Serif Display', serif; font-size: 52px; line-height: 1.08; color: var(--paper); margin-bottom: 4px; }}
  .cover-subtitle {{ font-family: 'DM Serif Display', serif; font-size: 28px; color: #888; font-style: italic; }}
  .cover-body {{ padding: 48px 64px; flex: 1; display: grid; grid-template-columns: 1fr 1fr; gap: 40px; align-content: start; }}
  .cover-meta-group h4, .cover-stack h4, h2, .ch-num, .phase-num, .glossary-term {{ font-family: 'Syne', sans-serif; }}
  .cover-meta-group h4 {{ font-size: 9px; letter-spacing: 0.2em; text-transform: uppercase; color: var(--muted); margin-bottom: 12px; border-bottom: 1px solid var(--rule); padding-bottom: 6px; }}
  .cover-meta-row {{ display: flex; justify-content: space-between; font-size: 12px; padding: 5px 0; border-bottom: 1px solid var(--cream); gap: 14px; }}
  .cover-meta-row span:first-child {{ color: var(--muted); }}
  .cover-stack {{ grid-column: 1 / -1; background: var(--cream); padding: 20px 24px; border-left: 3px solid var(--teal); }}
  .tech-pills {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .tech-pill {{ background: var(--ink); color: var(--gold-light); font-size: 10px; padding: 4px 12px; letter-spacing: 0.06em; }}
  .cover-footer {{ background: var(--cream); border-top: 2px solid var(--rule); padding: 20px 64px; display: flex; justify-content: space-between; align-items: center; font-size: 10px; color: var(--muted); letter-spacing: 0.05em; }}
  .confidential {{ background: var(--rust); color: #fff; padding: 4px 12px; font-size: 9px; letter-spacing: 0.15em; text-transform: uppercase; }}
  .ch-header {{ border-bottom: 2px solid var(--ink); padding-bottom: 20px; margin-bottom: 40px; display: flex; justify-content: space-between; align-items: flex-end; gap: 20px; }}
  .ch-title {{ font-family: 'DM Serif Display', serif; font-size: 32px; line-height: 1.1; }}
  .ch-subtitle {{ font-size: 10px; color: var(--muted); max-width: 340px; text-align: right; line-height: 1.5; }}
  .pg-num {{ position: absolute; bottom: 20px; right: 32px; font-size: 10px; color: var(--muted); letter-spacing: 0.1em; }}
  .pg-watermark {{ position: absolute; bottom: 20px; left: 32px; font-size: 9px; color: var(--rule); letter-spacing: 0.08em; text-transform: uppercase; }}
  h2 {{ font-size: 13px; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--teal); margin: 32px 0 14px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }}
  h3 {{ font-family: 'DM Serif Display', serif; font-size: 17px; margin: 22px 0 10px; }}
  p {{ color: #333; margin-bottom: 12px; font-size: 12.5px; line-height: 1.7; }}
  .brd-table {{ width: 100%; border-collapse: collapse; margin: 18px 0 28px; font-size: 11.5px; }}
  .brd-table thead tr {{ background: var(--ink); color: var(--paper); }}
  .brd-table thead th {{ padding: 10px 14px; text-align: left; font-family: 'Syne', sans-serif; font-size: 9px; letter-spacing: 0.15em; text-transform: uppercase; font-weight: 600; }}
  .brd-table tbody tr:nth-child(even) {{ background: var(--cream); }}
  .brd-table td {{ padding: 9px 14px; border-bottom: 1px solid var(--rule); vertical-align: top; line-height: 1.5; }}
  .brd-table td:first-child {{ font-weight: 500; color: var(--teal); }}
  .callout {{ padding: 16px 20px; margin: 16px 0; font-size: 12px; line-height: 1.6; border-left: 4px solid var(--teal); background: var(--cream); }}
  .scope-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 16px 0 24px; }}
  .scope-box {{ padding: 16px 20px; }}
  .scope-box.in-scope {{ background: #e8f4f0; border-top: 3px solid var(--teal); }}
  .scope-box.out-scope {{ background: #fdf0ee; border-top: 3px solid var(--rust); }}
  .scope-box h4 {{ font-size: 9px; letter-spacing: 0.18em; text-transform: uppercase; margin-bottom: 12px; font-weight: 700; }}
  .scope-box.in-scope h4 {{ color: var(--teal); }}
  .scope-box.out-scope h4 {{ color: var(--rust); }}
  .scope-item {{ display: flex; gap: 8px; margin-bottom: 7px; font-size: 11.5px; line-height: 1.45; }}
  .module-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; margin: 16px 0 24px; }}
  .module-card {{ border: 1px solid var(--rule); padding: 14px 16px; background: #fff; position: relative; }}
  .module-card::before {{ content: attr(data-num); position: absolute; top: 12px; right: 14px; font-family: 'DM Serif Display', serif; font-size: 28px; color: var(--cream); line-height: 1; }}
  .module-card h4 {{ font-family: 'Syne', sans-serif; font-size: 11px; font-weight: 700; letter-spacing: 0.05em; color: var(--teal); margin-bottom: 6px; }}
  .module-tag {{ display: inline-block; background: var(--cream); font-size: 9px; padding: 2px 8px; margin-top: 8px; color: var(--muted); letter-spacing: 0.05em; }}
  .code-block {{ background: var(--ink); color: #a8e6cf; font-size: 11px; padding: 20px 24px; margin: 16px 0 24px; line-height: 1.7; overflow-x: auto; border-left: 3px solid var(--gold); white-space: pre-wrap; }}
  .er-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 20px 0; }}
  .er-table {{ border: 1px solid var(--rule); overflow: hidden; }}
  .er-table-head {{ background: var(--teal); color: #fff; padding: 8px 12px; font-family: 'Syne', sans-serif; font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }}
  .er-field {{ padding: 5px 12px; font-size: 10.5px; border-bottom: 1px solid var(--cream); display: flex; justify-content: space-between; gap: 12px; }}
  .er-field .type {{ color: var(--muted); font-size: 9.5px; }}
  .phase-list {{ margin: 20px 0; border-left: 2px solid var(--teal); padding-left: 24px; }}
  .phase-item {{ position: relative; margin-bottom: 24px; }}
  .phase-item::before {{ content: ''; position: absolute; left: -31px; top: 6px; width: 12px; height: 12px; border-radius: 50%; background: var(--teal); border: 2px solid var(--paper); box-shadow: 0 0 0 2px var(--teal); }}
  .phase-title {{ font-family: 'Syne', sans-serif; font-weight: 700; font-size: 13px; margin-bottom: 4px; }}
  .phase-dur {{ font-size: 10.5px; color: var(--muted); margin-bottom: 6px; }}
  .phase-desc {{ font-size: 11.5px; color: #444; }}
  .check-list {{ list-style: none; margin: 10px 0; }}
  .check-list li {{ display: flex; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--cream); font-size: 11.5px; align-items: baseline; }}
  .glossary-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 16px 0; }}
  .glossary-item {{ padding: 10px 14px; background: #fff; border: 1px solid var(--rule); }}
  .glossary-def {{ font-size: 11px; color: #555; line-height: 1.5; }}
  .fig-caption {{ text-align: center; font-size: 10px; color: var(--muted); margin-top: -10px; margin-bottom: 20px; letter-spacing: 0.04em; font-style: italic; }}
  @media print {{ .toc-nav {{ display: none; }} .doc-wrapper {{ padding-top: 0; }} .page {{ break-after: always; box-shadow: none; margin: 0; }} }}
</style>
</head>
<body>
<nav class=\"toc-nav\">
  <div class=\"toc-nav-brand\">BRD - {_e(app_title)}</div>
  <a href=\"#cover\">Cover</a>
  <a href=\"#ch1\">Executive Summary</a>
  <a href=\"#ch2\">Architecture</a>
  <a href=\"#ch3\">Data Model</a>
  <a href=\"#ch4\">Modules</a>
  <a href=\"#ch5\">Use Cases</a>
  <a href=\"#ch6\">Migration</a>
  <a href=\"#ch7\">Appendix</a>
</nav>
<div class=\"doc-wrapper\">
  <div class=\"page cover-page\" id=\"cover\">
    <div class=\"page-inner\">
      <div class=\"cover-header\">
        <div class=\"cover-logo-row\">
          <div class=\"cover-logo-icon\">Q</div>
          <div>
            <div class=\"cover-org\">QlikAI Enterprise Documentation</div>
            <div class=\"cover-div\">Internal Documentation - Confidential</div>
          </div>
        </div>
        <div class=\"cover-title-block\">
          <div class=\"cover-doc-type\">{_e(project_subtitle)}</div>
          <div class=\"cover-title\">{_e(app_title)}</div>
          <div class=\"cover-subtitle\">{_e(project_type)}</div>
        </div>
      </div>
      <div class=\"cover-body\">
        <div class=\"cover-meta-group\">
          <h4>Project Details</h4>
                    <div class=\"cover-meta-row\"><span>{project_label}</span><span>{_e(app_title)}</span></div>
          <div class=\"cover-meta-row\"><span>Document Type</span><span>{_e(project_subtitle)}</span></div>
          <div class=\"cover-meta-row\"><span>Date</span><span>{_e(date_label)}</span></div>
          <div class=\"cover-meta-row\"><span>Prepared By</span><span>QlikAI BRD Generator</span></div>
          <div class=\"cover-meta-row\"><span>Classification</span><span>CONFIDENTIAL</span></div>
                    {cover_applications_html}
          <div class=\"cover-meta-row\"><span>Total Tables</span><span>{_e(summary.get('table_count', len(table_inventory)))}</span></div>
        </div>
        <div class=\"cover-meta-group\">
          <h4>Application Metadata</h4>
                    <div class=\"cover-meta-row\"><span>{project_id_label}</span><span>{_e(document.get('app_id', ''))}</span></div>
          <div class=\"cover-meta-row\"><span>Fields</span><span>{_e(summary.get('total_fields', 0))}</span></div>
          <div class=\"cover-meta-row\"><span>Sheets</span><span>{_e(summary.get('sheet_count', len(document.get('sheets', []))))}</span></div>
          <div class=\"cover-meta-row\"><span>Relationships</span><span>{_e(summary.get('relationship_count', len(relationships)))}</span></div>
          <div class=\"cover-meta-row\"><span>Load Script</span><span>{'Available' if document.get('script_excerpt') else 'Not Available'}</span></div>
          <div class=\"cover-meta-row\"><span>Generated</span><span>AI-assisted</span></div>
        </div>
        <div class=\"cover-stack\">
          <h4>Technology Stack</h4>
          <div class=\"tech-pills\">{tech_pills}</div>
        </div>
      </div>
      <div class=\"cover-footer\">
        <span>Generated from live Qlik application metadata and script analysis</span>
        <span class=\"confidential\">Confidential</span>
      </div>
    </div>
  </div>

  <div class=\"page\" id=\"ch1\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 1</div><div class=\"ch-title\">Executive Summary<br>&amp; Project Overview</div></div><div class=\"ch-subtitle\">Business context, inferred purpose, and documentation scope</div></div>
    <h2>1.1 Project Introduction</h2><p>{_e(document.get('project_overview', ''))}</p>
    <h2>1.2 Executive Summary</h2>{_render_list(document.get('executive_summary_bullets', []))}
    <h2>1.3 Business Objectives</h2>{objectives_table}
    <h2>1.4 Project Charter</h2>{charter_table}
    <h2>1.5 Scope</h2><div class=\"scope-grid\"><div class=\"scope-box in-scope\"><h4>In Scope</h4>{''.join(f'<div class="scope-item"><span>✔</span><span>{_e(item)}</span></div>' for item in document.get('in_scope', []) if item)}</div><div class=\"scope-box out-scope\"><h4>Out of Scope</h4>{''.join(f'<div class="scope-item"><span>✘</span><span>{_e(item)}</span></div>' for item in document.get('out_of_scope', []) if item)}</div></div>
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">02</div></div>

  <div class=\"page\" id=\"ch2\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 2</div><div class=\"ch-title\">System Architecture<br>Overview</div></div><div class=\"ch-subtitle\">Architecture interpretation from metadata, sheets, script, and data model</div></div>
    <h2>2.1 Architecture Style</h2><p>{_e(document.get('architecture_style', ''))}</p>
    <h2>2.2 Layer Descriptions</h2>{architecture_table}
    <h2>2.3 Design Patterns</h2>{design_patterns_table}
        {application_inventory_section}
        <h2>{sheet_inventory_label} Sheet Inventory</h2><div class=\"callout\"><strong>Detected Sheets</strong><div class=\"tech-pills\">{sheet_rows}</div></div>
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">03</div></div>

  <div class=\"page\" id=\"ch3\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 3</div><div class=\"ch-title\">Data Model &amp;<br>Relationships</div></div><div class=\"ch-subtitle\">Entity inventory, inferred joins, and business structure</div></div>
    <h2>3.1 Table Inventory</h2>{table_inventory_table}
    <h2>3.2 Entity Relationship View</h2>{_render_er_grid(table_inventory)}<div class=\"fig-caption\">Figure 3.1 - Entity inventory derived from Qlik tables</div>
    <h2>3.3 Inferred Relationships</h2>{relationship_table}
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">04</div></div>

  <div class=\"page\" id=\"ch4\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 4</div><div class=\"ch-title\">Module Breakdown<br>&amp; Functional Areas</div></div><div class=\"ch-subtitle\">Primary business entities and analytical domains</div></div>
    <h2>4.1 Module Overview</h2><p>The application is decomposed into the following primary analytical modules based on discovered entities, script logic, and business-facing sheet structure.</p>
    { _render_module_cards(module_summaries) }
    <h2>4.2 Actors</h2>{actors_table}
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">05</div></div>

  <div class=\"page\" id=\"ch5\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 5</div><div class=\"ch-title\">Use Cases, Logic<br>&amp; Data Flow</div></div><div class=\"ch-subtitle\">User scenarios, process interpretation, and script-backed logic</div></div>
    <h2>5.1 End-to-End Flow</h2><p>{_e(document.get('project_flow_summary', ''))}</p>
    <h2>5.2 Use Cases</h2>{use_cases_table}
    <h2>5.3 Data Flow Processes</h2>{data_flow_table}
    <h2>5.4 Load Script Excerpt</h2><div class=\"code-block\">{_e(document.get('script_excerpt', 'No load script was available during BRD generation.'))}</div>
    <h2>5.5 Sample Data Preview</h2>{_render_sample_tables(document.get('table_samples', []))}
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">06</div></div>

  <div class=\"page\" id=\"ch6\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 6</div><div class=\"ch-title\">Quality, Security<br>&amp; Migration</div></div><div class=\"ch-subtitle\">Risks, controls, and downstream transition planning</div></div>
    <h2>6.1 Technical Limitations</h2>{limitations_table}
    <h2>6.2 Security Checklist</h2>{security_html}
    <h2>6.3 Migration Phases</h2>{migration_html}
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">07</div></div>

  <div class=\"page\" id=\"ch7\"><div class=\"page-inner\"><div class=\"ch-header\"><div><div class=\"ch-num\">Chapter 7</div><div class=\"ch-title\">Appendix &amp;<br>Glossary</div></div><div class=\"ch-subtitle\">Reference material for onboarding and governance</div></div>
    <h2>7.1 Setup Errors</h2>{setup_errors_table}
    <h2>7.2 Coding Conventions</h2>{coding_conventions_table}
    <h2>7.3 Glossary</h2>{glossary_html}
    <h2>7.4 Revision History</h2>{revision_table}
  </div><div class=\"pg-watermark\">QlikAI BRD - Confidential</div><div class=\"pg-num\">08</div></div>
</div></body></html>"""


def build_download_filename(app_title: str) -> str:
    return f"{_slugify(app_title)}_BRD.html"