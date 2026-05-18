import type { ReconciliationReport } from "../types/reconciliation";

const API_BASE_URL = import.meta.env.VITE_API_URL || import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export async function fetchWorkflowReconciliationReport(
  batchId: string,
  workflowId: string,
): Promise<ReconciliationReport> {
  const response = await fetch(
    `${API_BASE_URL}/api/context-engineering/batches/${encodeURIComponent(batchId)}/workflows/${encodeURIComponent(workflowId)}/reconciliation`,
  );

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Failed to fetch reconciliation report (${response.status})`);
  }

  return response.json();
}

export async function requestValidationExplanation(report: ReconciliationReport): Promise<{ explanation: string }> {
  const response = await fetch(`${API_BASE_URL}/api/context-engineering/reconciliation/explain`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Failed to explain reconciliation report (${response.status})`);
  }

  return response.json();
}
