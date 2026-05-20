from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.ai_context import build_migration_context
from app.services.agent_audit import write_agent_audit_event
from app.services.alteryx_bulk_ingestion import load_batch
from app.services.alteryx_converter import convert_workflow_to_m
from app.services.alteryx_migration_engine import get_primary_source
from app.services.llm_routing import decide_llm_route
from app.services.reconciliation_engine import (
    ReconciliationReport,
    ValidationCheck,
    build_llm_validation_explanation_context,
)


router = APIRouter(prefix="/api/context-engineering", tags=["Context Engineering"])


class ExplanationRequest(BaseModel):
    source_name: str
    target_name: str
    status: str
    accuracy_score: float
    checks: list[dict[str, Any]]


@router.get("/batches/{batch_id}/workflows/{workflow_id}/context")
def get_workflow_context(
    batch_id: str,
    workflow_id: str,
    target_platform: str = Query(default="generic"),
) -> dict[str, Any]:
    workflow = _find_workflow(batch_id, workflow_id)
    source = get_primary_source(workflow)
    mquery_payload = convert_workflow_to_m(workflow, source)
    context = build_migration_context(
        workflow,
        mquery_payload.get("conversion_steps") or [],
        selected_node_ids=mquery_payload.get("graph_selected_node_ids") or [],
        skipped_node_ids=mquery_payload.get("graph_skipped_node_ids") or [],
        target_context={"target_platform": target_platform or "generic"},
    )
    decision = decide_llm_route(context)
    audit = write_agent_audit_event(
        "context_routing_decision",
        {
            "workflow_id": workflow_id,
            "context_hash": context.context_hash,
            "route": decision.route,
            "primary_provider": decision.primary_provider,
            "fallback_provider": decision.fallback_provider,
            "requires_human_review": decision.requires_human_review,
            "context_controls": context.context_controls,
        },
    )
    return {
        "context_hash": context.context_hash,
        "context": context.to_dict(),
        "routing_decision": decision.__dict__,
        "audit": audit,
    }


@router.get("/batches/{batch_id}/workflows/{workflow_id}/reconciliation")
def get_workflow_reconciliation(batch_id: str, workflow_id: str) -> dict[str, Any]:
    workflow = _find_workflow(batch_id, workflow_id)
    report = _build_available_reconciliation_report(workflow)
    return report.to_dict()


@router.post("/reconciliation/explain")
def explain_reconciliation(request: ExplanationRequest) -> dict[str, Any]:
    checks = [
        ValidationCheck(
            name=str(check.get("name") or "validation_check"),
            status=str(check.get("status") or "pending").lower(),
            severity=str(check.get("severity") or "medium"),
            source_value=check.get("source_value", check.get("expected")),
            target_value=check.get("target_value", check.get("actual")),
            details=str(check.get("details") or check.get("message") or ""),
        )
        for check in request.checks
    ]
    report = ReconciliationReport(
        source_name=request.source_name,
        target_name=request.target_name,
        status=request.status,
        accuracy_score=request.accuracy_score,
        checks=checks,
    )
    explanation_context = build_llm_validation_explanation_context(report)
    audit = write_agent_audit_event(
        "validation_exception_interpretation",
        {
            "source_name": request.source_name,
            "target_name": request.target_name,
            "status": request.status,
            "accuracy_score": request.accuracy_score,
            "checks_total": len(request.checks),
            "checks_sent_to_llm": len(explanation_context.get("failed_or_warned_checks", [])),
            "exception_controls": explanation_context.get("exception_controls", {}),
        },
    )
    return {
        "context": explanation_context,
        "agent_route": {
            "mode": "exception_interpreter",
            "llm_role": "interpretation_only",
            "native_engine_role": "all validation math and pass/fail decisions",
            "raw_rows_sent_to_llm": False,
            "checks_sent_to_llm": len(explanation_context.get("failed_or_warned_checks", [])),
        },
        "audit": audit,
        "explanation": (
            "The deterministic reconciliation result remains the source of truth. "
            "Investigate failed checks by reviewing formula translation, join grain, filters, null handling, "
            "rounding behavior, source refresh timing, and aggregation level before accepting the migration."
        ),
    }


def _find_workflow(batch_id: str, workflow_id: str) -> dict[str, Any]:
    try:
        batch = load_batch(batch_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found") from exc
    for workflow in batch.get("workflows", []):
        if str(workflow.get("id")) == str(workflow_id):
            return workflow
    raise HTTPException(status_code=404, detail=f"Workflow {workflow_id} not found in batch {batch_id}")


def _build_available_reconciliation_report(workflow: dict[str, Any]) -> ReconciliationReport:
    sources = workflow.get("dataSources") or []
    source_name = workflow.get("name") or "Alteryx output"
    row_count = _first_int(
        workflow.get("expected_row_count"),
        workflow.get("row_count"),
        *((source.get("row_count") or source.get("no_of_rows") or source.get("rowCount")) for source in sources if isinstance(source, dict)),
    )
    fields = _field_names(workflow, sources)
    checks = [
        ValidationCheck(
            name="row_count",
            status="pending",
            severity="critical",
            source_value=row_count if row_count else "Not discovered",
            target_value="Target validation not connected",
            details="Source row count can be discovered from workflow metadata. Target row count requires a BQ/Power BI validation query.",
        ),
        ValidationCheck(
            name="column_presence",
            status="pending",
            severity="critical",
            source_value=fields if fields else "Not discovered",
            target_value="Target schema not connected",
            details="Target columns must come from the published dataset or warehouse schema before this can be compared.",
        ),
        ValidationCheck(
            name="numeric_metrics",
            status="pending",
            severity="high",
            source_value="Not calculated from metadata",
            target_value="Requires target query results",
            details="Sum, min, max, average, and not-null metrics should be calculated from real source and target records.",
        ),
    ]
    return ReconciliationReport(
        source_name=source_name,
        target_name="Target validation source",
        status="pending",
        accuracy_score=0.0,
        checks=checks,
    )


def _field_names(workflow: dict[str, Any], sources: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for source in sources:
        for field in source.get("fields") or []:
            name = str(field.get("name") or field.get("field") or "").strip()
            if name and name not in names:
                names.append(name)
    for node in workflow.get("workflowNodes") or []:
        for field in node.get("fields") or []:
            name = str(field.get("name") or field.get("field") or "").strip()
            if name and name not in names:
                names.append(name)
    return names[:40]


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return int(float(str(value).replace(",", "")))
        except Exception:
            continue
    return None
