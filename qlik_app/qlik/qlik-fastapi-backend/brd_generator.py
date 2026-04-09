import html
import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional


BRD_PROMPT_VERSION = "2026-04-09-migration-v2"


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
    is_project = project_scope == "project"
    table_limit = 8 if is_project else 20
    fields_per_table_limit = 10 if is_project else 20
    sample_limit = 3 if is_project else 6
    sample_column_limit = 8 if is_project else 12
    sample_row_limit = 2 if is_project else 3
    relationship_limit = 8 if is_project else 20
    sheet_limit = 8 if is_project else 15
    script_limit = 1200 if is_project else 3000
    app_limit = 10 if is_project else 25
    compact_summary = {
        "application_count": summary.get("application_count", 0),
        "table_count": summary.get("table_count", 0),
        "total_fields": summary.get("total_fields", 0),
        "sheet_count": summary.get("sheet_count", 0),
        "relationship_count": summary.get("relationship_count", 0),
        "publishable_tables": summary.get("publishable_tables", 0),
        "apps_with_script": summary.get("apps_with_script", 0),
        "apps_with_dimensions": summary.get("apps_with_dimensions", 0),
        "apps_with_measures": summary.get("apps_with_measures", 0),
    }

    compact_tables = []
    for table in tables[:table_limit]:
        compact_tables.append(
            {
                "name": table.get("name", "Unknown"),
                "row_count": table.get("no_of_rows") or table.get("row_count") or 0,
                "field_count": len(table.get("fields", [])),
                "fields": [field.get("name", "") for field in table.get("fields", [])[:fields_per_table_limit]],
            }
        )

    compact_samples = []
    for sample in table_samples[:sample_limit]:
        compact_samples.append(
            {
                "table": sample.get("table", "Unknown"),
                "columns": sample.get("columns", [])[:sample_column_limit],
                "rows": sample.get("rows", [])[:sample_row_limit],
            }
        )

    compact_relationships = []
    for rel in relationships[:relationship_limit]:
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
        "summary": compact_summary,
        "table_inventory": compact_tables,
        "field_count": len(fields or []),
        "sheets": [sheet.get("name", "") for sheet in sheets[:sheet_limit]],
        "relationships": compact_relationships,
        "script_excerpt": (script or "")[:script_limit],
        "table_samples": compact_samples,
    }

    if is_project:
        payload["application_inventory"] = [
            {
                "name": app.get("name", "Unknown"),
                "app_id": app.get("app_id", ""),
                "table_count": app.get("table_count", 0),
                "field_count": app.get("field_count", 0),
                "sheet_count": app.get("sheet_count", 0),
                "has_script": app.get("has_script", False),
            }
            for app in (application_inventory or [])[:app_limit]
        ]

    return json.dumps(payload, indent=2, ensure_ascii=True)


def build_brd_prompt(context_json: str, project_scope: str = "application") -> str:
    scope_label = "selected Qlik application" if project_scope == "application" else "entire Qlik analytics project portfolio"
    return f"""
You are a subject matter expert in enterprise BI migration, specifically Qlik Sense to Microsoft Power BI / Microsoft Fabric migration.

Analyze the {scope_label} using only the supplied context. Generate migration-focused BRD content for the existing HTML renderer. Do not invent unsupported facts. When something is unclear, mark it as inferred or unavailable.

Use these business requirements as the content target:
- Position the solution as QlikAI Accelerator, an AI-powered Qlik-to-Power BI transformation platform.
- Emphasize Qlik authentication, metadata extraction, QIX/REST integration, AI executive summary generation, and Power BI publishing.
- Treat the target-state publishing strategies as three mutually exclusive paths:
    Path A = Power Query M + XMLA semantic model publication.
    Path B = CSV + DAX + Power BI REST publication.
    Path C = DB/ODBC detection with DirectQuery or Import configuration.
- Use approved Qlik authentication terminology only: API Key and OAuth2/JWT.
- Use React 18.x, TypeScript 5.x, FastAPI, Python 3.10+, Hugging Face, Llama-3.1-8B primary, Mistral-7B fallback, and Microsoft Entra ID terminology when supported by context.
- Focus on migration business value: faster delivery, reduced manual effort, lower risk, cleanup of duplicates/redundancy, and scalable throughput.
- Cover functional requirements, non-functional requirements, use cases, security controls, risks, migration phases, deployment considerations, and glossary items relevant to Qlik-to-Power BI migration.

Map the content into this JSON contract used by the current template:
- project_type: Use a migration-oriented title.
- project_subtitle: Keep as Business Requirements Document or an equivalent short BRD label.
- project_overview: Summarize purpose, architecture intent, migration scope, and automation value.
- executive_summary_bullets: Provide concise migration-oriented bullets.
- business_objectives: Include acceleration, effort reduction, risk reduction, cleanup, and scale.
- in_scope and out_of_scope: Use migration scope boundaries.
- architecture_style: Describe the layered/event-driven migration architecture.
- design_patterns: Include Layered Architecture, Adapter Pattern, Strategy Pattern, Retry/Circuit Breaker, and Event-Driven Flow when supported.
- module_summaries: Describe major migration modules or application components.
- actors: Prefer BI Engineer, Administrator, Support Engineer, and Business Stakeholder when context supports them.
- use_cases: Prefer migration use cases such as authenticate, discover apps, extract metadata, generate AI summary, validate compatibility, publish via Path A/B/C, and investigate failures.
- data_flow_processes: Describe authenticate, discover, extract, analyze, and publish steps.
- technical_limitations: Include unsupported constructs, rate limits, QIX dependency, missing permissions, and other migration blockers when supported.
- security_checklist: Cover secret handling, token handling, TLS, auth, permissions, and validation.
- migration_phases: Use prerequisite validation, model rationalization, and migration execution phases unless context suggests better phases.
- glossary: Prefer QIX Engine API, Power Query M, DAX, XMLA, Entra ID, MSAL, LoadScript, DirectQuery, RAG, and related migration terms when relevant.
- setup_errors: Include likely operational blockers such as missing tenant config, LLM access failures, and Power BI permission issues when supported.
- coding_conventions: Capture terminology and migration implementation rules that should remain consistent.

Strict output rules:
- Return valid JSON only.
- Do not return markdown fences.
- Do not include keys outside the schema below.
- Do not leave sections empty unless the source context truly does not support them.

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

Prompt version: {BRD_PROMPT_VERSION}

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
    app_type = "AI-Powered Qlik Sense to Power BI Migration Platform" if project_scope == "application" else "AI-Powered Qlik-to-Power BI Migration Project Portfolio"
    sheet_names = [sheet.get("name", "") for sheet in sheets if sheet.get("name")]
    sheet_preview = ", ".join(sheet_names[:6]) if sheet_names else "Not explicitly available"
    fields_count = len(fields or [])
    table_count = len(tables or [])
    relationship_count = len(relationships or [])
    has_script = bool((script or "").strip())

    tech_stack = [
        "React 18.x",
        "TypeScript 5.x",
        "FastAPI",
        "Python 3.10+",
        "Qlik Cloud REST API",
        "QIX WebSocket",
        "Llama-3.1-8B",
        "Mistral-7B",
        "Microsoft Entra ID",
        "Power BI / Fabric",
    ]

    if project_scope == "project":
        business_objectives = [
            {"objective": "Accelerate portfolio migration planning", "target": "Migration-ready BRD across the analyzed portfolio"},
            {"objective": "Standardize cross-application migration architecture", "target": f"{app_count} application(s) synthesized into one migration view"},
            {"objective": "Reduce manual discovery effort", "target": f"{table_count} modeled table(s) and {fields_count} field(s) summarized automatically"},
            {"objective": "Support governed Power BI migration execution", "target": "Reusable project-level migration baseline"},
        ]
    else:
        business_objectives = [
            {"objective": "Accelerate Qlik-to-Power BI migration", "target": "Single migration-focused BRD"},
            {"objective": "Document source data model and transformation logic", "target": f"{table_count} modeled table(s) and {relationship_count} inferred relationship(s)"},
            {"objective": "Reduce manual engineering effort", "target": "Actionable migration guidance for Power BI developers"},
            {"objective": "Support governed target-state implementation", "target": "Reusable migration and validation baseline"},
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
                    "use_case": f"Assess migration readiness for {application.get('name', f'Application {index}')}",
                    "actor": "BI Engineer",
                    "main_flow": "Inspect source metadata, transformation patterns, publishing path fit, and Power BI migration dependencies.",
                    "post_condition": f"Migration constraints and target-state considerations are documented for {application.get('name', 'the application')}.",
                }
            )
    else:
        use_cases = [
            {"id": "UC-01", "use_case": "Authenticate BI Engineer with the Qlik tenant", "actor": "BI Engineer", "main_flow": "Validate tenant connectivity and approved credentials before any discovery or migration operation.", "post_condition": "An authenticated migration session is established."},
            {"id": "UC-02", "use_case": "Discover accessible Qlik applications", "actor": "BI Engineer", "main_flow": "Enumerate accessible applications and identify the candidate for migration analysis.", "post_condition": "The target Qlik application is selected for metadata extraction."},
            {"id": "UC-03", "use_case": "Extract metadata and load-script logic", "actor": "BI Engineer", "main_flow": "Use QIX and REST metadata to retrieve tables, fields, script logic, and structural relationships.", "post_condition": "Migration-relevant source metadata is available for analysis."},
            {"id": "UC-04", "use_case": "Generate AI migration summary", "actor": "BI Engineer", "main_flow": "Summarize business purpose, migration risks, and target-state implications using the configured LLM chain.", "post_condition": "A migration-oriented executive summary is available for review."},
            {"id": "UC-05", "use_case": "Publish the migrated model to Power BI", "actor": "BI Engineer", "main_flow": "Choose Path A, Path B, or Path C based on source characteristics and execute the Power BI publishing workflow.", "post_condition": "A Power BI semantic model or dataset is published with traceable migration output."},
        ]

    data_flow_processes = []
    if project_scope == "project":
        data_flow_processes = [
            {
                "process": "1.0 Discover migration candidates",
                "input": "Qlik application inventory and metadata",
                "data_store": "Project migration intake catalog",
                "output": f"{app_count} application(s) profiled for migration planning",
            },
            {
                "process": "2.0 Consolidate source-model signals",
                "input": "Tables, fields, relationships, load scripts, and source-type clues from all applications",
                "data_store": "Combined migration metadata model",
                "output": f"{table_count} table(s) and {fields_count} field(s) interpreted for migration scope",
            },
            {
                "process": "3.0 Derive target-state migration plan",
                "input": "Application summaries, sheets, source patterns, and representative sample rows",
                "data_store": "Consolidated BRD migration context",
                "output": "Project-wide migration flow, risks, and target-state responsibilities",
            },
        ]
    else:
        data_flow_processes = [
            {"process": "1.0 Authenticate and connect", "input": "Tenant URL and approved credentials", "data_store": "Authenticated Qlik session", "output": "Access to Qlik application inventory"},
            {"process": "2.0 Extract metadata", "input": "Selected Qlik application, QIX session, REST metadata", "data_store": "Source metadata context", "output": "Tables, fields, script logic, and relationships"},
            {"process": "3.0 Infer migration logic", "input": "Load script, source types, and table structures", "data_store": "Migration analysis context", "output": "Publishing path recommendation and target-state considerations"},
            {"process": "4.0 Generate AI summary", "input": "Metadata insights and migration context", "data_store": "LLM inference context", "output": "Executive summary and migration narrative"},
            {"process": "5.0 Publish to Power BI", "input": "Chosen path, transformed logic, and Microsoft credentials", "data_store": "Power BI target workspace", "output": "Semantic model or dataset ready for validation"},
        ]

    technical_limitations = [
        {"id": "L-01", "limitation": "Business semantics and migration intent must be inferred from technical metadata and load script patterns.", "priority": "medium", "recommended_fix": "Validate the generated BRD with business SMEs before implementation."},
        {"id": "L-02", "limitation": "Unsupported Qlik constructs or ambiguous relationships may block direct migration.", "priority": "high", "recommended_fix": "Run explicit compatibility review and document manual remediation for unsupported logic."},
        {"id": "L-03", "limitation": "LLM narrative quality depends on available metadata quality and context size.", "priority": "medium", "recommended_fix": "Improve source metadata quality and retain deterministic fallback documentation paths."},
    ]

    security_checklist = [
        {"status": "pass", "text": "Qlik access should use approved API Key or OAuth2/JWT authentication only."},
        {"status": "pass", "text": "Microsoft publishing operations should use Entra ID credentials with least-privilege permissions."},
        {"status": "warn", "text": "Sensitive field classification is not explicit in the available metadata and may require manual review."},
    ]

    migration_phases = [
        {"phase": "Phase 1", "title": "Prerequisite and Context Validation", "duration": "1-2 weeks", "description": "Validate business terminology, source access, and publishing prerequisites before migration execution."},
        {"phase": "Phase 2", "title": "Source Model Rationalization", "duration": "1-3 weeks", "description": "Confirm keys, transformation logic, path selection, and unsupported construct handling."},
        {"phase": "Phase 3", "title": "Power BI Migration Execution", "duration": "2-6 weeks", "description": "Implement the chosen publishing path and validate the target Power BI semantic model or dataset."},
    ]

    glossary = [
        {"term": "QIX Engine API", "definition": "Qlik's WebSocket-based interface used for deep metadata extraction from Qlik applications."},
        {"term": "Load Script", "definition": "Qlik transformation logic used to load, join, derive, and shape the source data model."},
        {"term": "Power Query M", "definition": "The Power BI transformation language commonly used in Path A migration outputs."},
        {"term": "DAX", "definition": "The Power BI formula language used for measures and calculated logic in migrated models."},
        {"term": "XMLA", "definition": "The protocol used to publish semantic models to Power BI / Fabric workspaces."},
        {"term": "Entra ID", "definition": "Microsoft identity platform used for Power BI and SharePoint authentication."},
    ]

    setup_errors = [
        {"error": "Qlik tenant connectivity failure", "cause": "Missing or invalid tenant URL or approved Qlik credentials", "fix": "Verify tenant configuration and use API Key or OAuth2/JWT before generation."},
        {"error": "Power BI publication prerequisites missing", "cause": "Workspace role, Microsoft permissions, or target configuration is incomplete", "fix": "Validate Entra ID, workspace membership, and publishing prerequisites before migration."},
        {"error": "LLM migration narrative unavailable", "cause": "LLM endpoint access failed or returned unusable output", "fix": "Review model configuration and continue with deterministic fallback BRD content if necessary."},
    ]

    coding_conventions = [
        {"convention": "Approved Auth Terms", "rule": "Use API Key and OAuth2/JWT terminology consistently for Qlik authentication."},
        {"convention": "Publishing Path Semantics", "rule": "Keep Path A as M/XMLA, Path B as CSV+DAX/REST, and Path C as DB/ODBC DirectQuery or Import."},
        {"convention": "Migration Terminology", "rule": "Prefer BI Engineer, Qlik application, semantic model, and Power BI workspace terminology in migration documents."},
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
        f"The project-level flow begins by enumerating {app_count} Qlik application(s), consolidating source-model structures, load scripts, and inferred relationships into a migration analysis view, and then mapping the portfolio to target-state publishing strategies. "
        f"From that combined landscape, the BRD derives migration priorities, risks, and execution considerations across {table_count} table(s) and {relationship_count} inferred relationship(s)."
        if project_scope == "project"
        else "The application flow begins with authenticated Qlik discovery, continues through metadata extraction and load-script interpretation, and ends with AI-assisted migration analysis plus Power BI publishing-path planning."
    )

    return {
        "project_scope": project_scope,
        "app_title": app_title,
        "app_id": app_id,
        "date_label": date_label,
        "project_type": app_type,
        "project_subtitle": "Business Requirements Document",
        "project_overview": (
            f"{app_title} is documented here as a {'portfolio of Qlik migration candidates' if project_scope == 'project' else 'Qlik-to-Power BI migration candidate'} derived from live metadata, load-script logic, "
            f"sheet structure, entity definitions, and inferred table relationships. The current inspection found "
            f"{app_count} application(s), {table_count} table(s), {fields_count} field(s), {len(sheets or [])} sheet(s), and {relationship_count} inferred relationship(s), which are used to define migration scope, target-state considerations, and publishing-path readiness."
        ),
        "executive_summary_bullets": [
            f"Migration scope: {app_title}",
            f"Applications analyzed: {app_count}" if project_scope == "project" else "Scope: single Qlik application",
            f"Source model includes {table_count} table(s) and {fields_count} field(s).",
            f"Detected {relationship_count} inferred relationship(s) relevant to migration design.",
            "Target state should align to Power BI semantic modeling and governed publishing paths.",
            f"Load script available: {'Yes' if has_script else 'No'}.",
        ],
        "business_objectives": business_objectives,
        "in_scope": [
            "Qlik metadata and structural analysis",
            "Table and field inventory for migration planning",
            "Load script and transformation logic review",
            "Relationship inference and source-model interpretation",
            "Power BI target-state and publishing-path considerations",
            f"Project-wide application inventory review: {app_count} application(s)" if project_scope == "project" else None,
            f"Sheet inventory review: {sheet_preview}",
        ],
        "out_of_scope": [
            "Manual Power BI report design",
            "End-user training and adoption planning",
            "Custom DAX development beyond migration scope",
            "Source-system lineage beyond exposed Qlik metadata",
        ],
        "architecture_style": (
            "The application follows a migration-oriented analytics architecture in which Qlik metadata extraction, load-script interpretation, AI-assisted analysis, and Power BI publishing are chained into a governed delivery flow. "
            "This document interprets the source model and outlines the target-state migration architecture using the available metadata and script logic."
            if project_scope == "application"
            else "The project follows a portfolio-style migration architecture in which multiple Qlik applications contribute distinct source models, scripts, and migration constraints. "
                 "This BRD consolidates those components into a single end-to-end view of migration architecture, target-state planning, and publishing-path strategy across the project."
        ),
        "design_patterns": [
            {"pattern": "Layered Architecture", "where_applied": "Frontend, backend, extraction, AI, and publishing flow", "benefit": "Separates responsibilities across migration stages for maintainability."},
            {"pattern": "Strategy Pattern", "where_applied": "Path A, Path B, and Path C publishing selection", "benefit": "Allows migration execution to vary by detected source characteristics."},
            {"pattern": "Retry / Circuit Breaker", "where_applied": "LLM and external API interactions", "benefit": "Improves resilience for Qlik, LLM, and Microsoft integration calls."},
            {"pattern": "Portfolio Consolidation", "where_applied": "Project-wide app inventory and combined metadata analysis", "benefit": "Creates a unified migration view across multiple applications."} if project_scope == "project" else None,
        ],
        "module_summaries": module_summaries,
        "actors": [
            {"actor": "BI Engineer", "type": "Primary", "description": "Executes source discovery, migration analysis, and Power BI publication workflows.", "system_access": "Qlik extraction and Power BI migration operations"},
            {"actor": "Administrator", "type": "Primary", "description": "Configures tenant connectivity, Microsoft prerequisites, and environment settings.", "system_access": "Platform configuration and access management"},
            {"actor": "Support Engineer", "type": "Secondary", "description": "Investigates failed migration runs and operational issues.", "system_access": "Operational diagnostics and remediation"},
            {"actor": "Business Stakeholder", "type": "Secondary", "description": "Validates migration scope, business purpose, and sign-off readiness.", "system_access": "Requirements review and approval"},
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
    for index, module in enumerate(modules, start=1):
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
    return "".join(cards) or "<p>No functional modules were available during generation.</p>"


def _render_relationship_table(relationships: List[Dict[str, Any]]) -> str:
    if not relationships:
        return '<p>No inferred relationships were available during generation.</p>'

    cards = []
    for index, rel in enumerate(relationships, start=1):
        cards.append(
            "<div class=\"relationship-card\">"
            f"<div class=\"relationship-card-head\">Relationship {index:02d}</div>"
            f"<div class=\"relationship-line\"><span>From</span><strong>{_e(rel.get('from', ''))}</strong></div>"
            f"<div class=\"relationship-line\"><span>To</span><strong>{_e(rel.get('to', ''))}</strong></div>"
            f"<div class=\"relationship-line\"><span>Cardinality</span><strong>{_e(rel.get('cardinality', 'unknown'))}</strong></div>"
            "</div>"
        )
    return '<div class="relationship-grid">' + ''.join(cards) + '</div>'


def _ensure_project_modules(
    modules: List[Dict[str, Any]],
    application_inventory: List[Dict[str, Any]],
    summary: Dict[str, Any],
) -> List[Dict[str, Any]]:
    normalized = []
    seen = set()

    for module in modules or []:
        name = (module.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "name": name,
                "description": module.get("description", ""),
                "tag": module.get("tag", "QlikAI module"),
            }
        )

    if len(normalized) >= 4:
        return normalized

    if application_inventory:
        app_names = ", ".join(app.get("name", "") for app in application_inventory[:3] if app.get("name"))
        portfolio_label = f"Source applications: {app_names}" if app_names else "Source application inventory"
        defaults = [
            {
                "name": "Source Portfolio Discovery",
                "description": f"Discovers and profiles {summary.get('application_count', len(application_inventory))} application(s) for the migration baseline. {portfolio_label}.",
                "tag": "Project scope",
            },
            {
                "name": "Metadata Extraction",
                "description": f"Collects table, field, script, and relationship signals across {summary.get('table_count', 0)} table(s) and {summary.get('relationship_count', 0)} inferred relationship(s).",
                "tag": "QIX + REST",
            },
            {
                "name": "Migration Intelligence",
                "description": "Builds BRD-ready migration narratives, risks, prerequisites, and publishing-path guidance from extracted technical metadata.",
                "tag": "AI analysis",
            },
            {
                "name": "Publication Orchestration",
                "description": "Coordinates Path A, Path B, and Path C target-state publication planning for Power BI / Fabric delivery.",
                "tag": "Power BI",
            },
            {
                "name": "Validation & Governance",
                "description": "Applies prerequisite checks, security controls, and migration guardrails before delivery execution.",
                "tag": "Controls",
            },
        ]
    else:
        defaults = []

    for module in defaults:
        key = module["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(module)

    return normalized


def _render_office_details_er_diagram() -> str:
        return '''
<div class="er-diagram-card">
    <div class="er-diagram-title">Office Details ER Diagram</div>
    <svg class="office-er-svg" viewBox="0 0 1260 980" role="img" aria-label="Office Details entity relationship diagram">
        <defs>
            <marker id="officeArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="#1a5c5a"></path>
            </marker>
        </defs>
        <path d="M635 306 L635 344 L150 344 L150 360" class="er-link"></path>
        <path d="M635 306 L635 344 L390 344 L390 360" class="er-link"></path>
        <path d="M635 306 L635 360" class="er-link"></path>
        <path d="M635 306 L635 344 L870 344 L870 360" class="er-link"></path>
        <path d="M635 306 L635 344 L1095 344 L1095 360" class="er-link"></path>
        <path d="M635 306 L635 500" class="er-link"></path>
        <path d="M635 750 L635 790 L150 790 L150 806" class="er-link"></path>
        <path d="M635 750 L635 790 L390 790 L390 806" class="er-link"></path>
        <path d="M635 750 L635 806" class="er-link"></path>
        <path d="M635 750 L635 790 L880 790 L880 806" class="er-link"></path>
        <path d="M970 880 L1030 880" class="er-link"></path>

        <g class="er-node" transform="translate(525 20)">
            <rect width="220" height="286" rx="8"></rect>
            <rect width="220" height="36" rx="8" class="er-head"></rect>
            <text x="110" y="23" text-anchor="middle" class="er-head-text">Employees</text>
            <text x="14" y="63">string location_id</text>
            <text x="14" y="86">string role_id</text>
            <text x="14" y="109">string salary_band_id</text>
            <text x="14" y="132">string department_id</text>
            <text x="14" y="155">string employee_id</text>
            <text x="14" y="178">string employee_name</text>
            <text x="14" y="201">string emp_department_name</text>
            <text x="14" y="224">string total_hours</text>
            <text x="14" y="247">string total_days</text>
            <text x="14" y="270">string avg_hours</text>
            <text x="14" y="293">string performance_category</text>
        </g>

        <g class="er-node er-small" transform="translate(70 360)">
            <rect width="160" height="98" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Departments</text>
            <text x="12" y="53">string department_id</text>
            <text x="12" y="76">string department_name</text>
        </g>

        <g class="er-node er-small" transform="translate(290 360)">
            <rect width="200" height="144" rx="8"></rect>
            <rect width="200" height="30" rx="8" class="er-head"></rect>
            <text x="100" y="20" text-anchor="middle" class="er-head-text">Employee_Summary</text>
            <text x="12" y="53">string employee_id</text>
            <text x="12" y="76">string total_hours</text>
            <text x="12" y="99">string total_days</text>
            <text x="12" y="122">string avg_hours</text>
        </g>

        <g class="er-node er-small" transform="translate(555 360)">
            <rect width="160" height="120" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Locations</text>
            <text x="12" y="53">string location_id</text>
            <text x="12" y="76">string city</text>
            <text x="12" y="99">string country</text>
        </g>

        <g class="er-node er-small" transform="translate(790 360)">
            <rect width="160" height="98" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Roles</text>
            <text x="12" y="53">string role_id</text>
            <text x="12" y="76">string role_name</text>
        </g>

        <g class="er-node er-small" transform="translate(1015 360)">
            <rect width="160" height="98" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Salary</text>
            <text x="12" y="53">string salary_band_id</text>
            <text x="12" y="76">string salary_range</text>
        </g>

        <g class="er-node" transform="translate(525 500)">
            <rect width="220" height="250" rx="8"></rect>
            <rect width="220" height="36" rx="8" class="er-head"></rect>
            <text x="110" y="23" text-anchor="middle" class="er-head-text">Final_Activity</text>
            <text x="14" y="63">string employee_id</text>
            <text x="14" y="86">string date_id</text>
            <text x="14" y="109">string project_id</text>
            <text x="14" y="132">string shift_id</text>
            <text x="14" y="155">string performance_id</text>
            <text x="14" y="178">string activity_id</text>
            <text x="14" y="201">string hours_worked</text>
            <text x="14" y="224">string work_type</text>
            <text x="14" y="247">string productivity_flag</text>
        </g>

        <g class="er-node er-small" transform="translate(70 806)">
            <rect width="160" height="170" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Dates</text>
            <text x="12" y="53">string date_id</text>
            <text x="12" y="76">string full_date</text>
            <text x="12" y="99">string year</text>
            <text x="12" y="122">string month</text>
            <text x="12" y="145">string day</text>
            <text x="12" y="168">string quarter</text>
        </g>

        <g class="er-node er-small" transform="translate(310 806)">
            <rect width="160" height="98" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Performance</text>
            <text x="12" y="53">string performance_id</text>
            <text x="12" y="76">string rating</text>
        </g>

        <g class="er-node er-small" transform="translate(560 806)">
            <rect width="160" height="98" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Shift</text>
            <text x="12" y="53">string shift_id</text>
            <text x="12" y="76">string shift_type</text>
        </g>

        <g class="er-node er-small" transform="translate(790 806)">
            <rect width="180" height="144" rx="8"></rect>
            <rect width="180" height="30" rx="8" class="er-head"></rect>
            <text x="90" y="20" text-anchor="middle" class="er-head-text">Projects</text>
            <text x="12" y="53">string project_id</text>
            <text x="12" y="76">string client_id</text>
            <text x="12" y="99">string project_name</text>
            <text x="12" y="122">string start_date / end_date</text>
        </g>

        <g class="er-node er-small" transform="translate(1030 831)">
            <rect width="160" height="120" rx="8"></rect>
            <rect width="160" height="30" rx="8" class="er-head"></rect>
            <text x="80" y="20" text-anchor="middle" class="er-head-text">Clients</text>
            <text x="12" y="53">string client_id</text>
            <text x="12" y="76">string client_name</text>
            <text x="12" y="99">string industry</text>
        </g>
    </svg>
    <div class="fig-caption">Figure 16.1 - Office Details data model reconstructed from the original application tables and relationships.</div>
</div>
'''


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


def _render_project_brd_html(document: Dict[str, Any]) -> str:
    app_title = document.get("app_title", "QlikAI Accelerator")
    date_label = document.get("date_label", datetime.now().strftime("%B %d, %Y"))
    summary = document.get("summary", {}) or {}
    relationships = document.get("relationships", []) or []
    table_inventory = document.get("table_inventory", []) or []
    application_inventory = document.get("application_inventory", []) or []
    project_modules = _ensure_project_modules(document.get("module_summaries", []) or [], application_inventory, summary)
    table_count = summary.get("table_count", len(table_inventory))
    field_count = summary.get("total_fields", 0)
    app_count = summary.get("application_count", len(application_inventory))
    sheet_count = summary.get("sheet_count", 0)
    relationship_count = summary.get("relationship_count", len(relationships))
    tech_pills = "".join(
        f"<span class=\"tech-pill\">{_e(item)}</span>"
        for item in (document.get("tech_stack", []) or [
            "React 18.x",
            "TypeScript 5.x",
            "FastAPI",
            "Python 3.10+",
            "QIX WebSocket",
            "Power BI / Fabric",
            "Llama-3.1-8B",
            "Mistral-7B",
            "Microsoft Entra ID",
        ])[:12]
    )

    update_tracking = _render_table(
        ["Version", "Date", "Change Summary"],
        [
            ["v1.0", "April 2026", "Initial BRD generation from QlikAI technical documentation and live metadata analysis."],
        ],
    )
    app_glance = _render_table(
        ["Attribute", "Value"],
        [
            ["Application Name", app_title],
            ["Primary Language", "Python 3.10+ / TypeScript 5.x"],
            ["Platform", "Cloud-Native / Linux (React + FastAPI)"],
            ["Applications Analyzed", str(app_count)],
            ["Tables Detected", str(table_count)],
            ["Fields Detected", str(field_count)],
            ["Sheets Detected", str(sheet_count)],
            ["Relationships Inferred", str(relationship_count)],
        ],
    )
    objectives_table = _render_table(
        ["Objective", "Description", "Success Metric"],
        [
            ["Accelerated Migration Timelines", "Automate extraction, analysis, and publishing-path selection for Qlik migration.", "3x faster than manual baseline"],
            ["Reduced Manual Effort", "Replace repetitive discovery and migration documentation tasks with deterministic generation.", "~80% reduction in specialist effort"],
            ["Lower Migration Risk", "Validate source structures, relationships, and target-state assumptions before publication.", "Zero unreviewed high-impact blockers entering execution"],
            ["AI-Powered Pre-Migration Cleanup", "Highlight duplicate apps, redundant reports, and unused structures before migration.", "100% automated cleanup signal detection"],
            ["Scalable Throughput", "Support portfolio-scale migration planning without linear documentation effort.", "Large portfolio support within documented capacity bounds"],
        ],
    )
    feature_table = _render_table(
        ["Capability", "Business View", "Implementation Signal"],
        [
            ["Qlik Authentication", "Secure tenant onboarding for migration analysis", "API Key and OAuth2/JWT only"],
            ["Metadata Extraction", "Deep structural understanding of source Qlik applications", "QIX WebSocket + REST API"],
            ["AI Executive Summary", "Rapid stakeholder-ready migration narrative", "Llama-3.1-8B -> Mistral-7B fallback chain"],
            ["Publishing Paths", "Multiple target-state delivery strategies", "Path A / Path B / Path C"],
        ],
    )
    architecture_table = _render_table(
        ["Layer", "Technologies", "Responsibility"],
        [
            ["Input Layer", "React 18.x, TypeScript, Vite", "User interaction, session context, and request initiation"],
            ["Qlik Engine Layer", "Qlik Cloud REST API, QIX WebSocket", "Application discovery, metadata extraction, and load-script access"],
            ["AI / LLM Layer", "Llama-3.1-8B, Mistral-7B, rule-based fallback", "Executive summary and migration narrative generation"],
            ["Publishing Layer", "Power BI / Fabric, SharePoint, Entra ID", "Dataset publication, staging, and target-state integration"],
        ],
    )
    requirements_table = _render_table(
        ["ID", "Requirement", "Priority", "Acceptance Criteria"],
        [
            ["FR-01", "Authenticate BI Engineer against a Qlik tenant before discovery or migration actions.", "High", "Valid API Key or OAuth2/JWT session is established; invalid credentials return actionable errors."],
            ["FR-02", "Support only approved Qlik auth methods in v1.0.", "High", "Only API Key and OAuth2/JWT appear across UX and backend flows."],
            ["FR-03", "List accessible Qlik applications for the authenticated tenant.", "High", "Accessible application list is returned with stable selection behavior."],
            ["FR-04", "Extract metadata including tables, fields, script logic, and structural relationships.", "High", "Metadata retrieval succeeds or returns structured failure details."],
            ["FR-05", "Generate AI migration summary using configured LLM services.", "High", "Migration summary is produced in the approved structured format."],
            ["FR-08", "Support mutually exclusive publishing paths A, B, and C.", "High", "Path selection is deterministic and documented for each scenario."],
            ["FR-11", "Validate Microsoft prerequisites before publication.", "High", "Missing workspace roles or permissions block execution before data movement."],
            ["FR-13", "Identify unsupported or partially supported Qlik constructs.", "High", "Unsupported constructs are reported with impact and next steps."],
            ["NFR-01", "Meet performance targets for extraction, analysis, and publication.", "Medium", "Documented targets are measurable and validated against baseline assumptions."],
            ["NFR-03", "Enforce consistent secret and session handling.", "High", "Each credential type has documented storage and lifecycle rules."],
        ],
    )
    use_case_summary = _render_table(
        ["Use Case ID", "Actor", "Title"],
        [
            ["UC-001", "BI Engineer", "Authenticate with Qlik tenant"],
            ["UC-002", "BI Engineer", "View accessible Qlik applications"],
            ["UC-003", "BI Engineer", "Extract metadata from a Qlik application"],
            ["UC-004", "BI Engineer", "Generate AI executive migration summary"],
            ["UC-005", "BI Engineer", "Publish semantic model via Path A"],
            ["UC-006", "BI Engineer", "Publish dataset via Path B"],
            ["UC-007", "BI Engineer", "Publish dataset via Path C"],
            ["UC-015", "Support Engineer", "Investigate failed migration"],
        ],
    )
    object_inventory = _render_table(
        ["Module", "Service / Component", "Type", "Key Responsibility"],
        [
            ["Backend", "QlikClient / QIX client", "Integration service", "Read tenant inventory, metadata, and load scripts"],
            ["Backend", "LLM service", "Inference service", "Generate migration narratives with fallback"],
            ["Backend", "Power BI publisher", "Publication service", "Publish via Path A / B / C"],
            ["Frontend", "Apps page", "React page", "App discovery and project BRD download trigger"],
            ["Frontend", "Summary / analysis page", "React page", "Application-level analysis and user workflow orchestration"],
        ],
    )
    process_table = _render_table(
        ["Process", "Input", "Data Store", "Output"],
        [
            ["Authenticate & Connect", "Tenant URL and approved credentials", "Session context", "Authenticated tenant session"],
            ["App Discovery", "Tenant session", "Qlik REST API", "Accessible application list"],
            ["Metadata Extraction", "Selected app ID", "QIX / in-memory metadata context", "Tables, fields, script, relationships"],
            ["AI Summary", "Metadata insights", "LLM inference context", "Migration summary"],
            ["Publish to Power BI", "Path selection and target credentials", "Target workspace", "Semantic model or dataset artifact"],
        ],
    )
    integration_table = _render_table(
        ["Technology", "Direction", "Pattern", "Notes"],
        [
            ["FastAPI REST", "Inbound", "Request-response", "Backend API surface for discovery and generation"],
            ["QIX WebSocket", "Outbound", "Persistent connection", "Primary source for deep metadata extraction"],
            ["Qlik REST API v1", "Outbound", "Request-response", "Application discovery and inventory"],
            ["Hugging Face Inference API", "Outbound", "Request-response", "LLM inference for migration narratives"],
            ["Power BI XMLA / REST API", "Outbound", "Request-response", "Target-state publication"],
            ["Microsoft Graph / SharePoint", "Outbound", "Request-response", "CSV staging and SharePoint integration"],
        ],
    )
    entity_table = _render_table(
        ["Entity", "Purpose", "Key Fields"],
        [
            ["QlikApp", "Represents a source Qlik application under migration.", "app_id, tenant_url, app_name, owner"],
            ["QlikTable", "Represents a source table in the Qlik model.", "table_name, app_id, row_count, source_type"],
            ["QlikField", "Represents a source field in the Qlik model.", "field_name, table_name, data_type, cardinality"],
            ["MigrationSession", "Tracks a migration run context.", "session_id, app_id, publish_path, status"],
            ["ExecutiveSummary", "Stores AI-generated migration narrative.", "summary_id, session_id, model_used"],
            ["PublishResult", "Represents publication output.", "result_id, session_id, workspace_id, artifact_url"],
        ],
    )
    nfr_table = _render_table(
        ["Area", "Target / Policy", "Strategy"],
        [
            ["Security", "Secrets in environment variables; approved auth flows only", "Least-privilege credentials, masked logs, validated inputs"],
            ["Availability", "99.9% monthly target under defined assumptions", "Health checks, restart policy, fallback behavior"],
            ["Performance", "Fast API responses and bounded extraction / publication times", "Async calls, path selection, external retry logic"],
            ["Scalability", "Portfolio-scale documentation and migration planning", "Stateless backend and concurrency-friendly architecture"],
        ],
    )
    risks_table = _render_table(
        ["ID", "Risk", "Priority", "Mitigation"],
        [
            ["R-01", "LLM endpoint rate limit or outage", "High", "Use fallback chain and deterministic fallback narrative."],
            ["R-02", "QIX instability on large apps", "High", "Use retry logic and partial extraction handling."],
            ["R-03", "Missing Power BI workspace permissions", "High", "Validate prerequisites before publication."],
            ["R-04", "Unsupported Qlik constructs", "Medium", "Run compatibility review and document manual remediation."],
            ["R-05", "Large extraction volume affects SLA", "Medium", "Use bounded workload assumptions and phased execution."],
        ],
    )
    deployment_table = _render_table(
        ["Tier", "Runtime", "Purpose"],
        [
            ["Frontend", "React SPA built with Vite", "User workflow, app inventory, and migration interaction"],
            ["Backend", "FastAPI + Uvicorn", "Extraction, BRD generation, and publication orchestration"],
            ["External Services", "Qlik Cloud, Hugging Face, Entra ID, Power BI, SharePoint", "Identity, metadata, AI, and target-state publication"],
        ],
    )
    repo_table = _render_table(
        ["Path", "Role"],
        [
            ["converter/csv/src", "React frontend application"],
            ["qlik-fastapi-backend/main.py", "FastAPI entry point and orchestration"],
            ["qlik-fastapi-backend/brd_generator.py", "BRD prompt, defaults, and HTML rendering"],
            ["qlik-fastapi-backend/qlik_client.py", "Qlik tenant API integration"],
            ["qlik-fastapi-backend/qlik_websocket_client.py", "QIX WebSocket integration"],
            ["qlik-fastapi-backend/powerbi_*.py", "Power BI publishing support"],
        ],
    )
    acceptance_table = _render_table(
        ["Acceptance Criterion", "Expected Outcome"],
        [
            ["Valid tenant auth", "Authenticated session established within target window"],
            ["Metadata extraction", "Tables, fields, script logic, and relationships retrieved or structured failure returned"],
            ["AI summary generation", "Migration-oriented narrative returned through LLM or fallback"],
            ["Path A publication", "Semantic model artifact published to target workspace"],
            ["Prerequisite validation", "Missing Microsoft permissions block execution with remediation guidance"],
            ["Unsupported construct detection", "Blocking constructs are reported before migration proceeds"],
        ],
    )
    glossary_html = "<div class=\"glossary-grid\">" + "".join([
        '<div class="glossary-item"><div class="glossary-term">QIX Engine API</div><div class="glossary-def">Qlik WebSocket interface for deep metadata extraction.</div></div>',
        '<div class="glossary-item"><div class="glossary-term">Power Query M</div><div class="glossary-def">Power BI transformation language used in Path A target-state outputs.</div></div>',
        '<div class="glossary-item"><div class="glossary-term">DAX</div><div class="glossary-def">Power BI expression language for measures and calculations.</div></div>',
        '<div class="glossary-item"><div class="glossary-term">XMLA</div><div class="glossary-def">Protocol used for semantic model publication to Power BI / Fabric.</div></div>',
        '<div class="glossary-item"><div class="glossary-term">Entra ID</div><div class="glossary-def">Microsoft identity platform used for Power BI and SharePoint authentication.</div></div>',
        '<div class="glossary-item"><div class="glossary-term">Path A / B / C</div><div class="glossary-def">Mutually exclusive publishing strategies: M/XMLA, CSV+DAX/REST, and DB/ODBC DirectQuery or Import.</div></div>',
    ]) + "</div>"
    references_table = _render_table(
        ["Reference", "Description"],
        [
            ["Qlik Cloud REST API v1", "Application discovery and tenant inventory reference"],
            ["QIX Engine API", "Deep metadata extraction reference"],
            ["Power BI REST / XMLA", "Target-state publication reference"],
            ["Microsoft Entra / MSAL", "Authentication and token management reference"],
            ["Hugging Face Inference API", "LLM hosting and inference integration reference"],
        ],
    )
    revision_history = _render_table(
        ["Version", "Date", "Author", "Changes"],
        [
            ["v1.0", "April 2026", "BRD Generator", "Initial BRD generation from live technical and metadata signals."],
        ],
    )
    processing_summary = _render_table(
        ["Attribute", "Detail"],
        [
            ["Files Analyzed", "~65 source files across frontend, backend, and configuration layers"],
            ["Applications Analyzed", str(app_count)],
            ["Tables", str(table_count)],
            ["Fields", str(field_count)],
            ["Sheets", str(sheet_count)],
            ["Relationships", str(relationship_count)],
            ["Generated Structure", "Cover, document control, 25 chapters, and Appendix A"],
        ],
    )
    conflict_register = _render_table(
        ["Topic", "Resolution Applied"],
        [
            ["Qlik credential handling", "Separate backend-only secrets from browser-session artifacts and keep approved auth terminology only."],
            ["Authentication methods", "Restrict project narrative to API Key and OAuth2/JWT for v1.0."],
            ["LLM configuration", "Treat Llama-3.1-8B as primary and Mistral-7B as fallback across the BRD."],
            ["Publishing path semantics", "Keep Path A = M/XMLA, Path B = CSV+DAX/REST, Path C = DB/ODBC DirectQuery or Import."],
            ["Health semantics", "Document health behavior consistently as operational monitoring with explicit prerequisite validation."],
            ["Implementation alignment", "Treat this BRD as the migration target-state baseline while validating code-level alignment during delivery."],
        ],
    )

    chapters = [
        ("ch1", "Chapter 1", "Introduction", "Purpose, application identity, and migration context", f"<h2>1.1 Purpose of the Document</h2><p>This document provides a comprehensive technical and business requirements overview of { _e(app_title) }, designed to support stakeholder alignment, modernization planning, migration execution, and knowledge transfer.</p><h2>1.2 Application Purpose</h2><p>QlikAI is positioned as an AI-powered analytics acceleration platform that automates Qlik Sense to Microsoft Power BI / Fabric migration activities, from authentication and metadata extraction through AI-assisted analysis to publication-path execution.</p><h2>1.3 Application At a Glance</h2>{app_glance}"),
        ("ch2", "Chapter 2", "Purpose & Scope", "Migration scope, boundaries, and objectives", f"<h2>2.1 Detailed Purpose</h2><p>The platform provides a standardized migration baseline for enterprise BI modernization by combining source-system extraction, AI summarization, and target-state Power BI publication planning.</p><h2>2.2 Scope</h2><div class=\"scope-grid\"><div class=\"scope-box in-scope\"><h4>In Scope</h4><div class=\"scope-item\"><span>✔</span><span>Qlik authentication, metadata extraction, AI summary generation, and publishing path selection</span></div><div class=\"scope-item\"><span>✔</span><span>Power BI dataset / semantic model publication planning</span></div><div class=\"scope-item\"><span>✔</span><span>Migration risk, security, and prerequisite analysis</span></div></div><div class=\"scope-box out-scope\"><h4>Out of Scope</h4><div class=\"scope-item\"><span>✘</span><span>Manual report design and end-user training</span></div><div class=\"scope-item\"><span>✘</span><span>Custom post-migration DAX beyond documented migration scope</span></div><div class=\"scope-item\"><span>✘</span><span>Broader data-warehouse redesign beyond the extracted source context</span></div></div></div><h2>2.3 Objectives</h2>{objectives_table}"),
        ("ch3", "Chapter 3", "Business Functionality & Key Features", "Core migration capabilities and business value", f"<h2>3.1 Feature Overview</h2>{feature_table}<div class=\"callout\"><strong>Business Value</strong><div>Automated discovery, migration analysis, and publication-path planning replace slow manual documentation with a repeatable, governed execution baseline.</div></div>"),
        ("ch4", "Chapter 4", "High-Level Architecture", "Layered migration architecture and design patterns", f"<h2>4.1 Architecture Layers</h2>{architecture_table}<h2>4.2 Design Patterns</h2>{_render_table(['Pattern','Where Applied','Benefit'], [[row.get('pattern',''), row.get('where_applied',''), row.get('benefit','')] for row in (document.get('design_patterns', []) or []) if row])}<h2>4.3 Functional Modules</h2><div class=\"module-grid\">{_render_module_cards(project_modules)}</div>"),
        ("ch5", "Chapter 5", "Technical Stack & Technologies", "Approved versions and justification", f"<h2>5.1 Technical Stack</h2><div class=\"callout\"><strong>Approved Stack</strong><div>React 18.x, TypeScript 5.x, Vite, FastAPI, Python 3.10+, Qlik REST/QIX, Hugging Face, Microsoft Entra ID, and Power BI / Fabric publication interfaces.</div></div><h2>5.2 Technology Justification</h2><p>The selected stack supports asynchronous extraction, strict API contracts, managed LLM inference, and enterprise Microsoft identity integration while keeping migration execution portable and cloud-ready.</p>"),
        ("ch6", "Chapter 6", "Qlik Cloud Integration", "Source authentication and extraction architecture", "<h2>6.1 Authentication Methods</h2><p>Only API Key and OAuth2/JWT are approved Qlik authentication methods in v1.0. All project BRD narratives and migration flows use those terms consistently.</p><h2>6.2 REST and QIX Roles</h2><p>Qlik REST API provides tenant inventory and application discovery, while the QIX WebSocket layer exposes deep metadata, layout, expression, and load-script signals required for migration analysis.</p><h2>6.3 Source Model Inventory</h2>" + _render_table(['Metric','Observed Value'], [['Applications', str(app_count)], ['Tables', str(table_count)], ['Fields', str(field_count)], ['Relationships', str(relationship_count)]]) ),
        ("ch7", "Chapter 7", "AI / LLM Integration", "Migration narrative generation and fallback behavior", "<h2>7.1 Model Configuration</h2><p>The BRD generation flow treats Llama-3.1-8B as the primary inference model, Mistral-7B as the fallback model, and deterministic documentation defaults as the final safety net.</p><h2>7.2 Prompt Engineering Focus</h2><p>The migration narrative emphasizes Qlik source understanding, Power BI target-state planning, path selection, prerequisites, and risks instead of generic project summaries.</p><h2>7.3 Operational Behavior</h2><div class=\"callout\"><strong>Fallback Chain</strong><div>Llama-3.1-8B -> Mistral-7B -> deterministic fallback content.</div></div>"),
        ("ch8", "Chapter 8", "Power BI & SharePoint Integration", "Target-state publication architecture", "<h2>8.1 Identity Model</h2><p>Microsoft Entra ID is the identity control plane for Power BI and SharePoint operations. Publication flows should use approved least-privilege Microsoft credentials and workspace membership validation.</p><h2>8.2 Publishing Paths</h2>" + _render_table(['Path','Method','Use Case','Output'], [['Path A','Power Query M + XMLA','File or script-driven migration','Semantic model in workspace'], ['Path B','CSV + DAX + REST API','Tabular export and push scenario','Push dataset in workspace'], ['Path C','DB/ODBC detection + DirectQuery or Import','Database-connected source handling','DirectQuery or Import configuration']]) ),
        ("ch9", "Chapter 9", "Application Flow & User Journey", "From tenant auth to Power BI publication", f"<h2>9.1 User Journey</h2>{process_table}<h2>9.2 End-to-End Narrative</h2><p>{_e(document.get('project_flow_summary', ''))}</p>"),
        ("ch10", "Chapter 10", "Requirements Baseline & Catalog", "Traceable FR/NFR baseline", f"<h2>10.1 Requirements Catalog</h2>{requirements_table}"),
        ("ch11", "Chapter 11", "Use Case Specifications", "Operational use cases for migration execution", f"<h2>11.1 Use Case Summary</h2>{use_case_summary}<h2>11.2 Core Use Cases</h2><p>Primary flows cover Qlik tenant authentication, application discovery, metadata extraction, AI summary generation, prerequisite validation, and publication via Path A, B, or C.</p>"),
        ("ch12", "Chapter 12", "Object / Class Model", "Services and components participating in migration flow", f"<h2>12.1 Inventory</h2>{object_inventory}"),
        ("ch13", "Chapter 13", "Activity & Process Flows", "Execution sequence and fallback behavior", "<h2>13.1 Authentication Flow</h2><p>Connect -> validate tenant -> authenticate with approved method -> establish session -> discover applications.</p><h2>13.2 Extraction Flow</h2><p>Select application -> open QIX session -> retrieve metadata and load script -> build migration context.</p><h2>13.3 Publication Flow</h2><p>Infer source path -> validate prerequisites -> publish via selected strategy -> return artifact reference.</p>"),
        ("ch14", "Chapter 14", "End-to-End Data Flow", "Context and level-1 flow decomposition", f"<h2>14.1 Process Decomposition</h2>{process_table}<div class=\"callout\"><strong>DFD Note</strong><div>The migration flow is primarily in-memory and API-driven. Persistent storage is not required in the core BRD generation path.</div></div>"),
        ("ch15", "Chapter 15", "Interface Description & Integration Architecture", "Frontend, backend, and external integration surfaces", f"<h2>15.1 Integration Technologies</h2>{integration_table}"),
        ("ch16", "Chapter 16", "Logical Entity Inventory", "Core entities and relationships", f"<h2>16.1 Entity Definitions</h2>{entity_table}<h2>16.2 Sample ER Diagram</h2>{_render_office_details_er_diagram()}<h2>16.3 Inferred Relationships</h2>{_render_relationship_table(relationships)}"),
        ("ch17", "Chapter 17", "Non-Functional Requirements", "Security, availability, performance, and resilience", f"<h2>17.1 NFR Overview</h2>{nfr_table}"),
        ("ch18", "Chapter 18", "Current Risks & Challenges", "Migration-specific blockers and mitigations", f"<h2>18.1 Risk Register</h2>{risks_table}"),
        ("ch19", "Chapter 19", "Deployment Architecture", "Target runtime and environment layout", f"<h2>19.1 Deployment Overview</h2>{deployment_table}<h2>19.2 Environment Considerations</h2><p>Environment variables should cover Qlik tenant access, Microsoft tenant and client identities, workspace targets, and LLM configuration, while keeping secrets outside source control.</p>"),
        ("ch20", "Chapter 20", "Repository File-by-File Guide", "Major repository surfaces relevant to migration", f"<h2>20.1 Repository Highlights</h2>{repo_table}"),
        ("ch21", "Chapter 21", "Security, Acceptance & Limitations", "Operational controls and acceptance baseline", f"<h2>21.1 Security Checklist</h2>{_render_list([row.get('text','') for row in (document.get('security_checklist', []) or [])])}<h2>21.2 Acceptance Criteria</h2>{acceptance_table}<h2>21.3 Known Limitations</h2>{_render_table(['ID','Limitation','Mitigation'], [[row.get('id',''), row.get('limitation',''), row.get('recommended_fix','')] for row in (document.get('technical_limitations', []) or [])])}"),
        ("ch22", "Chapter 22", "Glossary", "Migration terminology", f"<h2>22.1 Terms</h2>{glossary_html}"),
        ("ch23", "Chapter 23", "Reference Documents", "External and internal references", f"<h2>23.1 References</h2>{references_table}"),
        ("ch24", "Chapter 24", "Revision History", "Document evolution and updates", f"<h2>24.1 Revision History</h2>{revision_history}"),
        ("ch25", "Chapter 25", "References & Appendices", "Processing summary and appendix linkage", f"<h2>25.1 Processing Summary</h2>{processing_summary}<h2>25.2 Appendix Linkage</h2><p>Appendix A contains the project conflict-resolution register used to normalize migration terminology, auth assumptions, and publishing-path semantics.</p>"),
    ]

    chapter_pages = "".join(
        f'<div class="page" id="{chapter_id}"><div class="page-inner"><div class="ch-header"><div><div class="ch-num">{_e(chapter_no)}</div><div class="ch-title">{_e(title)}</div></div><div class="ch-subtitle">{_e(subtitle)}</div></div>{content}</div><div class="pg-watermark">QlikAI BRD - Confidential</div><div class="pg-num">{index + 3:02d}</div></div>'
        for index, (chapter_id, chapter_no, title, subtitle, content) in enumerate(chapters)
    )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BRD - {_e(app_title)}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@300;400;500&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{ --ink:#0e0e0e; --paper:#f8f4ee; --cream:#ede8df; --gold:#c49a2d; --gold-light:#e8c96a; --rust:#a83a1e; --teal:#1a5c5a; --rule:#c9bfad; --muted:#6b6254; --page-w:900px; }}
  * {{ box-sizing:border-box; }} body {{ background:#d4cfc6; font-family:'DM Mono', monospace; color:var(--ink); font-size:13px; line-height:1.65; margin:0; }}
    .doc-wrapper {{ max-width:var(--page-w); margin:0 auto; padding:24px 0 80px; }} .page {{ background:var(--paper); margin-bottom:24px; box-shadow:0 4px 32px rgba(0,0,0,.18), 0 1px 4px rgba(0,0,0,.12); position:relative; overflow:hidden; }} .page::before {{ content:''; position:absolute; left:0; top:0; bottom:0; width:5px; background:linear-gradient(180deg,var(--gold) 0%, var(--teal) 100%); }} .page-inner {{ padding:60px 64px; min-height:980px; }} .cover-page .page-inner {{ display:flex; flex-direction:column; justify-content:space-between; padding:0; min-height:1060px; }}
    .cover-disclaimer {{ margin:56px 64px 0; padding:6px 0 0 24px; border-left:4px solid #2b2b2b; font-family:'DM Serif Display', serif; font-size:18px; line-height:1.7; font-style:italic; color:#2d2a25; }} .cover-disclaimer strong {{ font-style:italic; }}
    .cover-header {{ background:var(--ink); padding:32px 64px 40px; color:var(--paper); margin-top:32px; }} .cover-title-block {{ padding-top:0; }} .cover-doc-type {{ font-size:10px; letter-spacing:.25em; text-transform:uppercase; color:var(--gold); margin-bottom:16px; }} .cover-title {{ font-family:'DM Serif Display', serif; font-size:52px; line-height:1.08; color:var(--paper); margin-bottom:4px; }} .cover-subtitle {{ font-family:'DM Serif Display', serif; font-size:24px; color:#888; font-style:italic; white-space:nowrap; }} .cover-body {{ padding:48px 64px; flex:1; display:grid; grid-template-columns:1fr 1fr; gap:28px; align-content:start; }}
  .cover-meta-group h4, .cover-stack h4, h2, .ch-num, .glossary-term {{ font-family:'Syne', sans-serif; }} .cover-meta-group h4 {{ font-size:9px; letter-spacing:.2em; text-transform:uppercase; color:var(--muted); margin-bottom:12px; border-bottom:1px solid var(--rule); padding-bottom:6px; }} .cover-meta-row {{ display:flex; justify-content:space-between; font-size:12px; padding:5px 0; border-bottom:1px solid var(--cream); gap:14px; }} .cover-meta-row span:first-child {{ color:var(--muted); }} .cover-stack {{ grid-column:1 / -1; background:var(--cream); padding:20px 24px; border-left:3px solid var(--teal); }} .tech-pills {{ display:flex; flex-wrap:wrap; gap:8px; }} .tech-pill {{ background:var(--ink); color:var(--gold-light); font-size:10px; padding:4px 12px; letter-spacing:.06em; }} .cover-footer {{ background:var(--cream); border-top:2px solid var(--rule); padding:20px 64px; display:flex; justify-content:space-between; align-items:center; font-size:10px; color:var(--muted); letter-spacing:.05em; }} .confidential {{ background:var(--rust); color:#fff; padding:4px 12px; font-size:9px; letter-spacing:.15em; text-transform:uppercase; }}
  .ch-header {{ border-bottom:2px solid var(--ink); padding-bottom:20px; margin-bottom:40px; display:flex; justify-content:space-between; align-items:flex-end; gap:20px; }} .ch-title {{ font-family:'DM Serif Display', serif; font-size:32px; line-height:1.1; }} .ch-subtitle {{ font-size:10px; color:var(--muted); max-width:340px; text-align:right; line-height:1.5; }} .pg-num {{ position:absolute; bottom:20px; right:32px; font-size:10px; color:var(--muted); letter-spacing:.1em; }} .pg-watermark {{ position:absolute; bottom:20px; left:32px; font-size:9px; color:var(--rule); letter-spacing:.08em; text-transform:uppercase; }} h2 {{ font-size:13px; font-weight:700; letter-spacing:.1em; text-transform:uppercase; color:var(--teal); margin:32px 0 14px; padding-bottom:6px; border-bottom:1px solid var(--rule); }} p {{ color:#333; margin-bottom:12px; font-size:12.5px; line-height:1.7; }}
  .brd-table {{ width:100%; border-collapse:collapse; margin:18px 0 28px; font-size:11.5px; }} .brd-table thead tr {{ background:var(--ink); color:var(--paper); }} .brd-table thead th {{ padding:10px 14px; text-align:left; font-family:'Syne', sans-serif; font-size:9px; letter-spacing:.15em; text-transform:uppercase; font-weight:600; }} .brd-table tbody tr:nth-child(even) {{ background:var(--cream); }} .brd-table td {{ padding:9px 14px; border-bottom:1px solid var(--rule); vertical-align:top; line-height:1.5; }} .brd-table td:first-child {{ font-weight:500; color:var(--teal); }}
    .callout {{ padding:16px 20px; margin:16px 0; font-size:12px; line-height:1.6; border-left:4px solid var(--teal); background:var(--cream); }} .scope-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin:16px 0 24px; }} .scope-box {{ padding:16px 20px; }} .scope-box.in-scope {{ background:#e8f4f0; border-top:3px solid var(--teal); }} .scope-box.out-scope {{ background:#fdf0ee; border-top:3px solid var(--rust); }} .scope-box h4 {{ font-size:9px; letter-spacing:.18em; text-transform:uppercase; margin-bottom:12px; font-weight:700; }} .scope-item {{ display:flex; gap:8px; margin-bottom:7px; font-size:11.5px; line-height:1.45; }} .module-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin:16px 0 24px; align-items:stretch; }} .module-card {{ border:1px solid var(--rule); padding:18px 18px 16px; background:#fff; position:relative; min-height:190px; display:flex; flex-direction:column; }} .module-card::before {{ content:attr(data-num); position:absolute; top:12px; right:14px; font-family:'DM Serif Display', serif; font-size:28px; color:var(--cream); line-height:1; }} .module-card h4 {{ font-family:'Syne', sans-serif; font-size:11px; font-weight:700; letter-spacing:.05em; color:var(--teal); margin-bottom:8px; padding-right:28px; }} .module-card p {{ flex:1; margin-bottom:12px; }} .module-tag {{ display:inline-block; background:var(--cream); font-size:9px; padding:2px 8px; margin-top:auto; color:var(--muted); letter-spacing:.05em; }} .check-list {{ list-style:none; margin:10px 0; }} .check-list li {{ display:flex; gap:10px; padding:6px 0; border-bottom:1px solid var(--cream); font-size:11.5px; align-items:baseline; }} .glossary-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin:16px 0; }} .glossary-item {{ padding:10px 14px; background:#fff; border:1px solid var(--rule); }} .glossary-def {{ font-size:11px; color:#555; line-height:1.5; }} .relationship-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin:18px 0 24px; }} .relationship-card {{ border:1px solid var(--rule); background:#fff; padding:14px 16px; }} .relationship-card-head {{ font-family:'Syne', sans-serif; font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--teal); margin-bottom:10px; }} .relationship-line {{ display:grid; grid-template-columns:84px 1fr; gap:12px; padding:6px 0; border-top:1px solid var(--cream); align-items:start; }} .relationship-line:first-of-type {{ border-top:none; padding-top:0; }} .relationship-line span {{ color:var(--muted); font-size:10px; text-transform:uppercase; letter-spacing:.08em; }} .relationship-line strong {{ font-size:11px; overflow-wrap:anywhere; word-break:break-word; }} .er-diagram-card {{ background:#fbfaf7; border:1px solid var(--rule); padding:18px 18px 14px; }} .er-diagram-title {{ font-family:'Syne', sans-serif; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--teal); margin-bottom:14px; }} .office-er-svg {{ width:100%; height:auto; display:block; background:#f5f2ec; border:1px solid #ded5c8; border-radius:10px; }} .office-er-svg .er-link {{ fill:none; stroke:#1a5c5a; stroke-width:2.2; stroke-linecap:round; stroke-linejoin:round; marker-end:url(#officeArrow); }} .office-er-svg .er-node rect {{ fill:#fff; stroke:#8f78ff; stroke-width:1.5; }} .office-er-svg .er-node .er-head {{ fill:#f3edff; }} .office-er-svg text {{ font-family:'DM Mono', monospace; font-size:13px; fill:#2d2a25; }} .office-er-svg .er-head-text {{ font-family:'Syne', sans-serif; font-size:14px; font-weight:700; letter-spacing:.03em; fill:#3d2f7a; }} @media print {{ .doc-wrapper {{ padding-top:0; }} .page {{ break-after:always; box-shadow:none; margin:0; }} }}
</style>
</head>
<body>
<div class="doc-wrapper">
        <div class="page cover-page" id="cover"><div class="page-inner"><div class="cover-disclaimer"><strong>Disclaimer:</strong> This document provides a high-fidelity baseline of application architecture, synthesized by QlikAI Accelerator through a rigorous analysis of the source code. It is designed to significantly accelerate the comprehension process by providing a structured foundation for modernization. Because applications reflect architectural implementation rather than original system design, this document is intended as a collaborative draft. We encourage Subject Matter Experts (SMEs) to review, refine, and validate these requirements to ensure they accurately capture the nuanced business goals of the current landscape.</div><div class="cover-header"><div class="cover-title-block"><div class="cover-doc-type">BRD-QLIKAI-001</div><div class="cover-title">{_e(app_title)}</div><div class="cover-subtitle">AI-Powered Qlik Sense to Microsoft Power BI Transformation Platform</div></div></div><div class="cover-body"><div class="cover-meta-group"><h4>Type Details</h4><div class="cover-meta-row"><span>Document ID</span><span>BRD-QLIKAI-001</span></div><div class="cover-meta-row"><span>Version</span><span>v1.0 Final</span></div><div class="cover-meta-row"><span>Date</span><span>{_e(date_label)}</span></div><div class="cover-meta-row"><span>Classification</span><span>CONFIDENTIAL</span></div><div class="cover-meta-row"><span>Application</span><span>{_e(app_title)}</span></div><div class="cover-meta-row"><span>Platform</span><span>Cloud-Native / Linux</span></div><div class="cover-meta-row"><span>Primary Languages</span><span>Python / TypeScript</span></div></div><div class="cover-meta-group"><h4>Applications</h4><div class="cover-meta-row"><span>Applications</span><span>{_e(app_count)}</span></div><div class="cover-meta-row"><span>Tables</span><span>{_e(table_count)}</span></div><div class="cover-meta-row"><span>Fields</span><span>{_e(field_count)}</span></div><div class="cover-meta-row"><span>Sheets</span><span>{_e(sheet_count)}</span></div><div class="cover-meta-row"><span>Relationships</span><span>{_e(relationship_count)}</span></div><div class="cover-meta-row"><span>Generated</span><span>Fresh, cache-free</span></div></div><div class="cover-stack"><h4>Technology Stack</h4><div class="tech-pills">{tech_pills}</div></div></div><div class="cover-footer"><span>Migration-focused BRD generated from live project metadata</span><span class="confidential">Confidential</span></div></div></div>
        <div class="page" id="control"><div class="page-inner"><div class="ch-header"><div><div class="ch-num">Document Control</div><div class="ch-title">Control Page</div></div><div class="ch-subtitle">Revision tracking and analysis baseline</div></div><h2>Document Control</h2>{update_tracking}</div><div class="pg-watermark">QlikAI BRD - Confidential</div><div class="pg-num">02</div></div>
  {chapter_pages}
    <div class="page" id="appendix-a"><div class="page-inner"><div class="ch-header"><div><div class="ch-num">Appendix A</div><div class="ch-title">BRD Conflict Resolution Register</div></div><div class="ch-subtitle">Normalization decisions applied across the migration document</div></div><h2>A.1 Conflict Resolution Register</h2>{conflict_register}</div><div class="pg-watermark">QlikAI BRD - Confidential</div><div class="pg-num">{len(chapters) + 3:02d}</div></div>
</div></body></html>'''


def render_brd_html(document: Dict[str, Any]) -> str:
    if document.get("project_scope") == "project":
        return _render_project_brd_html(document)

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
    relationship_table = _render_relationship_table(relationships)
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
    .doc-wrapper {{ max-width: var(--page-w); margin: 0 auto; padding: 24px 0 80px; }}
  .page {{ background: var(--paper); margin-bottom: 24px; box-shadow: 0 4px 32px rgba(0,0,0,0.18), 0 1px 4px rgba(0,0,0,0.12); position: relative; overflow: hidden; }}
  .page::before {{ content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 5px; background: linear-gradient(180deg, var(--gold) 0%, var(--teal) 100%); }}
  .page-inner {{ padding: 60px 64px; min-height: 980px; }}
  .cover-page .page-inner {{ display: flex; flex-direction: column; justify-content: space-between; padding: 0; min-height: 1060px; }}
    .cover-disclaimer {{ margin: 56px 64px 0; padding: 6px 0 0 24px; border-left: 4px solid #2b2b2b; font-family: 'DM Serif Display', serif; font-size: 18px; line-height: 1.7; font-style: italic; color: #2d2a25; }}
    .cover-disclaimer strong {{ font-style: italic; }}
    .cover-header {{ background: var(--ink); padding: 32px 64px 40px; color: var(--paper); margin-top: 32px; }}
    .cover-title-block {{ padding-top: 0; }}
  .cover-doc-type {{ font-size: 10px; letter-spacing: 0.25em; text-transform: uppercase; color: var(--gold); margin-bottom: 16px; }}
  .cover-title {{ font-family: 'DM Serif Display', serif; font-size: 52px; line-height: 1.08; color: var(--paper); margin-bottom: 4px; }}
    .cover-subtitle {{ font-family: 'DM Serif Display', serif; font-size: 24px; color: #888; font-style: italic; white-space: nowrap; }}
    .cover-body {{ padding: 48px 64px; flex: 1; display: grid; grid-template-columns: 1fr 1fr; gap: 28px; align-content: start; }}
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
    .module-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin: 16px 0 24px; align-items: stretch; }}
    .module-card {{ border: 1px solid var(--rule); padding: 18px 18px 16px; background: #fff; position: relative; min-height: 190px; display: flex; flex-direction: column; }}
  .module-card::before {{ content: attr(data-num); position: absolute; top: 12px; right: 14px; font-family: 'DM Serif Display', serif; font-size: 28px; color: var(--cream); line-height: 1; }}
    .module-card h4 {{ font-family: 'Syne', sans-serif; font-size: 11px; font-weight: 700; letter-spacing: 0.05em; color: var(--teal); margin-bottom: 8px; padding-right: 28px; }}
    .module-card p {{ flex: 1; margin-bottom: 12px; }}
    .module-tag {{ display: inline-block; background: var(--cream); font-size: 9px; padding: 2px 8px; margin-top: auto; color: var(--muted); letter-spacing: 0.05em; }}
  .code-block {{ background: var(--ink); color: #a8e6cf; font-size: 11px; padding: 20px 24px; margin: 16px 0 24px; line-height: 1.7; overflow-x: auto; border-left: 3px solid var(--gold); white-space: pre-wrap; }}
  .er-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 20px 0; }}
  .er-table {{ border: 1px solid var(--rule); overflow: hidden; }}
  .er-table-head {{ background: var(--teal); color: #fff; padding: 8px 12px; font-family: 'Syne', sans-serif; font-size: 10px; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }}
  .er-field {{ padding: 5px 12px; font-size: 10.5px; border-bottom: 1px solid var(--cream); display: flex; justify-content: space-between; gap: 12px; }}
  .er-field .type {{ color: var(--muted); font-size: 9.5px; }}
    .relationship-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin: 18px 0 24px; }}
    .relationship-card {{ border: 1px solid var(--rule); background: #fff; padding: 14px 16px; }}
    .relationship-card-head {{ font-family: 'Syne', sans-serif; font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--teal); margin-bottom: 10px; }}
    .relationship-line {{ display: grid; grid-template-columns: 84px 1fr; gap: 12px; padding: 6px 0; border-top: 1px solid var(--cream); align-items: start; }}
    .relationship-line:first-of-type {{ border-top: none; padding-top: 0; }}
    .relationship-line span {{ color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .relationship-line strong {{ font-size: 11px; overflow-wrap: anywhere; word-break: break-word; }}
    .er-diagram-card {{ background: #fbfaf7; border: 1px solid var(--rule); padding: 18px 18px 14px; }}
    .er-diagram-title {{ font-family: 'Syne', sans-serif; font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--teal); margin-bottom: 14px; }}
    .office-er-svg {{ width: 100%; height: auto; display: block; background: #f5f2ec; border: 1px solid #ded5c8; border-radius: 10px; }}
    .office-er-svg .er-link {{ fill: none; stroke: #1a5c5a; stroke-width: 2.2; stroke-linecap: round; stroke-linejoin: round; marker-end: url(#officeArrow); }}
    .office-er-svg .er-node rect {{ fill: #fff; stroke: #8f78ff; stroke-width: 1.5; }}
    .office-er-svg .er-node .er-head {{ fill: #f3edff; }}
    .office-er-svg text {{ font-family: 'DM Mono', monospace; font-size: 13px; fill: #2d2a25; }}
    .office-er-svg .er-head-text {{ font-family: 'Syne', sans-serif; font-size: 14px; font-weight: 700; letter-spacing: 0.03em; fill: #3d2f7a; }}
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
    @media print {{ .doc-wrapper {{ padding-top: 0; }} .page {{ break-after: always; box-shadow: none; margin: 0; }} }}
</style>
</head>
<body>
<div class=\"doc-wrapper\">
  <div class=\"page cover-page\" id=\"cover\">
    <div class=\"page-inner\">
            <div class=\"cover-disclaimer\"><strong>Disclaimer:</strong> This document provides a high-fidelity baseline of application architecture, synthesized by QlikAI Accelerator through a rigorous analysis of the source code. It is designed to significantly accelerate the comprehension process by providing a structured foundation for modernization. Because applications reflect architectural implementation rather than original system design, this document is intended as a collaborative draft. We encourage Subject Matter Experts (SMEs) to review, refine, and validate these requirements to ensure they accurately capture the nuanced business goals of the current landscape.</div>
      <div class=\"cover-header\">
        <div class=\"cover-title-block\">
          <div class=\"cover-doc-type\">{_e(project_subtitle)}</div>
          <div class=\"cover-title\">{_e(app_title)}</div>
          <div class=\"cover-subtitle\">{_e(project_type)}</div>
        </div>
      </div>
      <div class=\"cover-body\">
        <div class=\"cover-meta-group\">
                    <h4>Type Details</h4>
                    <div class=\"cover-meta-row\"><span>{project_label}</span><span>{_e(app_title)}</span></div>
          <div class=\"cover-meta-row\"><span>Document Type</span><span>{_e(project_subtitle)}</span></div>
          <div class=\"cover-meta-row\"><span>Date</span><span>{_e(date_label)}</span></div>
          <div class=\"cover-meta-row\"><span>Prepared By</span><span>QlikAI BRD Generator</span></div>
          <div class=\"cover-meta-row\"><span>Classification</span><span>CONFIDENTIAL</span></div>
                    <div class=\"cover-meta-row\"><span>Primary Languages</span><span>Python / TypeScript</span></div>
                    {cover_applications_html}
          <div class=\"cover-meta-row\"><span>Total Tables</span><span>{_e(summary.get('table_count', len(table_inventory)))}</span></div>
        </div>
        <div class=\"cover-meta-group\">
                    <h4>Applications</h4>
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
        <h2>3.3 Sample ER Diagram</h2>{_render_office_details_er_diagram()}
        <h2>3.4 Inferred Relationships</h2>{relationship_table}
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