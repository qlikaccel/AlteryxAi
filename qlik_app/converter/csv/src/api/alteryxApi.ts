const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

export interface AlteryxWorkflow {
  id: string;
  name: string;
  lastModifiedDate?: string;
  runCount?: number;
  credentialType?: string;
  workerTag?: string;
  sourceFile?: string;
  packageFile?: string | null;
  fileType?: string;
  toolCount?: number;
  connectionCount?: number;
  convertibility?: string;
  complexity?: string;
  supportedToolCount?: number;
  unsupportedToolCount?: number;
  toolTypes?: string[];
  unsupportedTools?: string[];
  recommendations?: string[];
  dataSources?: Array<Record<string, any>>;
  workflowNodes?: Array<Record<string, any>>;
  workflowEdges?: Array<Record<string, any>>;
}

export interface AlteryxBatch {
  batch_id: string;
  created_at?: number;
  summary: Record<string, any>;
  workflows: AlteryxWorkflow[];
  rejected: Array<{ file: string; reason: string }>;
}

export async function fetchUploadedAlteryxWorkflows(batchId: string): Promise<AlteryxWorkflow[]> {
  const res = await fetch(`${BASE_URL}/api/alteryx/batches/${encodeURIComponent(batchId)}/workflows`);
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Failed to fetch uploaded workflows (${res.status})`);
  }

  return (data.workflows || []).map(normalizeWorkflow);
}

export async function fetchUploadedAlteryxWorkflow(
  batchId: string,
  workflowId: string
): Promise<AlteryxWorkflow> {
  const res = await fetch(
    `${BASE_URL}/api/alteryx/batches/${encodeURIComponent(batchId)}/workflows/${encodeURIComponent(workflowId)}`
  );
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Failed to fetch workflow assessment (${res.status})`);
  }

  return normalizeWorkflow(data.workflow || data);
}

export async function fetchAlteryxWorkflowAnalysis(
  batchId: string,
  workflowId: string,
  sharePointUrl = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx",
  fileName = "sales_data_1M.csv"
): Promise<any> {
  const params = new URLSearchParams({ sharepoint_url: sharePointUrl, file_name: fileName });
  const res = await fetch(
    `${BASE_URL}/api/alteryx/batches/${encodeURIComponent(batchId)}/workflows/${encodeURIComponent(workflowId)}/analysis?${params.toString()}`
  );
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Failed to analyze workflow (${res.status})`);
  }

  return data;
}

export async function fetchAlteryxWorkflowMQuery(
  batchId: string,
  workflowId: string,
  sharePointUrl = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx",
  fileName = "sales_data_1M.csv"
): Promise<any> {
  const params = new URLSearchParams({ sharepoint_url: sharePointUrl, file_name: fileName });
  const res = await fetch(
    `${BASE_URL}/api/alteryx/batches/${encodeURIComponent(batchId)}/workflows/${encodeURIComponent(workflowId)}/mquery?${params.toString()}`
  );
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Failed to generate M Query (${res.status})`);
  }

  return data;
}

export async function fetchAlteryxBrdHtml(
  batchId: string,
  workflowId: string,
  sharePointUrl = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx",
  fileName = "sales_data_1M.csv"
): Promise<string> {
  const params = new URLSearchParams({ sharepoint_url: sharePointUrl, file_name: fileName });
  const res = await fetch(
    `${BASE_URL}/api/alteryx/batches/${encodeURIComponent(batchId)}/workflows/${encodeURIComponent(workflowId)}/brd?${params.toString()}`
  );
  const text = await res.text();

  if (!res.ok) {
    throw new Error(text || `Failed to generate BRD (${res.status})`);
  }

  return text;
}

export async function publishAlteryxMQuery(payload: {
  dataset_name: string;
  combined_mquery: string;
  data_source_path?: string;
  sharepoint_url?: string;
  access_token?: string;
}): Promise<any> {
  const res = await fetch(`${BASE_URL}/api/migration/publish-mquery`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dataset_name: payload.dataset_name,
      combined_mquery: payload.combined_mquery,
      data_source_path: payload.data_source_path || payload.sharepoint_url || "",
      sharepoint_url: payload.sharepoint_url || "",
      access_token: payload.access_token || sessionStorage.getItem("powerbi_access_token") || "",
      qlik_fields_map: {},
      app_id: "",
    }),
  });
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Power BI publish failed (${res.status})`);
  }

  return {
    ...data,
    api_endpoint: `${BASE_URL}/api/migration/publish-mquery`,
  };
}

export async function fetchAlteryxWorkflows(workspaceId: string, accessToken: string): Promise<AlteryxWorkflow[]> {
  const workspaceName = sessionStorage.getItem("alteryx_workspace_name");
  const alteryxUsername = sessionStorage.getItem("alteryx_username");
  const storedRefreshToken = sessionStorage.getItem("alteryx_refresh_token");
  const headers: Record<string, string> = {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };

  if (storedRefreshToken) headers["X-Alteryx-Refresh-Token"] = storedRefreshToken;
  if (alteryxUsername) headers["X-Alteryx-Username"] = alteryxUsername;

  const res = await fetch(
    `${BASE_URL}/api/alteryx/workflows?workspace_id=${encodeURIComponent(workspaceId)}${
      workspaceName ? `&workspace_name=${encodeURIComponent(workspaceName)}` : ""
    }`,
    { headers }
  );
  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    throw new Error(data.detail || `Failed to fetch workflows (${res.status})`);
  }

  const refreshedAccessToken = res.headers.get("X-Alteryx-Access-Token");
  if (refreshedAccessToken) sessionStorage.setItem("alteryx_access_token", refreshedAccessToken);

  const rotatedRefreshToken = res.headers.get("X-Alteryx-Refresh-Token");
  if (rotatedRefreshToken) sessionStorage.setItem("alteryx_refresh_token", rotatedRefreshToken);

  return (data.workflows || []).map(normalizeWorkflow);
}

export function normalizeWorkflow(workflow: any): AlteryxWorkflow {
  return {
    ...workflow,
    id: String(workflow?.id || workflow?.workflowId || workflow?.assetId || workflow?.name || ""),
    name: String(workflow?.name || workflow?.workflowName || workflow?.title || "Untitled workflow"),
    lastModifiedDate: workflow?.lastModifiedDate || workflow?.updatedAt || workflow?.modifiedAt,
    runCount: workflow?.runCount ?? workflow?.toolCount,
    credentialType: workflow?.credentialType ?? workflow?.convertibility,
    workerTag: workflow?.workerTag ?? workflow?.complexity,
  };
}
