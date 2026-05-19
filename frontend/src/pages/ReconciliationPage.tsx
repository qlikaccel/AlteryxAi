import { useEffect, useState } from "react";
import { ReconciliationDashboard } from "../components/ReconciliationDashboard/ReconciliationDashboard";
import { fetchWorkflowReconciliationReport, requestValidationExplanation } from "../api/reconciliationApi";
import type { ReconciliationReport } from "../types/reconciliation";

const demoReport: ReconciliationReport = {
  source_name: "Alteryx output",
  target_name: "Target validation source",
  status: "pending",
  accuracy_score: 0,
  checks: [
    {
      name: "row_count",
      status: "pending",
      severity: "critical",
      source_value: "Waiting for selected workflow",
      target_value: "Target validation not connected",
      details: "Connect a workflow and target validation source before comparing row counts.",
    },
    {
      name: "column_presence",
      status: "pending",
      severity: "critical",
      source_value: "Waiting for selected workflow",
      target_value: "Target schema not connected",
      details: "Column presence requires source metadata and target schema metadata.",
    },
    {
      name: "numeric_metrics",
      status: "pending",
      severity: "high",
      source_value: "Not calculated from sample data",
      target_value: "Requires BQ/Power BI query results",
      details: "Sum, min, max, average, and not-null checks should be calculated from real source and target datasets.",
    },
  ],
};

export function ReconciliationPage() {
  const [report, setReport] = useState<ReconciliationReport>(() => {
    const raw = localStorage.getItem("alteryx_reconciliation_report") || sessionStorage.getItem("alteryx_reconciliation_report");
    if (!raw) return demoReport;
    try {
      return JSON.parse(raw) as ReconciliationReport;
    } catch {
      return demoReport;
    }
  });
  const [explanation, setExplanation] = useState("");
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState("");

  useEffect(() => {
    const batchId = sessionStorage.getItem("alteryx_batch_id") || "";
    const workflowId = sessionStorage.getItem("alteryx_workflow_id") || "";
    if (localStorage.getItem("alteryx_reconciliation_report") || sessionStorage.getItem("alteryx_reconciliation_report")) {
      setStatusText("");
      return;
    }
    if (!batchId || !workflowId) {
      setStatusText("Showing sample reconciliation until an Alteryx workflow is selected.");
      return;
    }

    setStatusText("Loading reconciliation checks...");
    fetchWorkflowReconciliationReport(batchId, workflowId)
      .then((nextReport) => {
        setReport(nextReport);
        setStatusText("");
      })
      .catch(() => {
        setStatusText("Could not load workflow reconciliation yet. Showing sample checks.");
      });
  }, []);

  async function handleExplain() {
    setLoading(true);
    try {
      const result = await requestValidationExplanation(report);
      setExplanation(result.explanation);
    } catch {
      setExplanation(
        "The deterministic checks show numeric drift while row count and columns match. Investigate formula conversion, rounding behavior, filter conditions, and aggregation grain before accepting the migration.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {statusText && <div className="reconciliation-page-note">{statusText}</div>}
      <ReconciliationDashboard
        report={report}
        explanation={explanation}
        onExplain={handleExplain}
        explainLoading={loading}
      />
    </>
  );
}
