
import "./AppsPage.css";
import { useEffect, useMemo, useState } from "react";

type WorkflowItem = {
  id: string;
  name: string;
  modifiedAt: string;
  status: string;
  toolCount: number | null;
};

const toArray = (payload: any): any[] => {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.items)) return payload.items;
  if (Array.isArray(payload?.workflows)) return payload.workflows;
  return [];
};

const normalizeWorkflow = (raw: any): WorkflowItem | null => {
  const id =
    raw?.id ||
    raw?.workflowId ||
    raw?.attributes?.id ||
    raw?.uuid ||
    "";
  const name =
    raw?.name ||
    raw?.workflowName ||
    raw?.attributes?.name ||
    raw?.title ||
    "Unnamed workflow";

  if (!id) return null;

  return {
    id,
    name,
    modifiedAt:
      raw?.modifiedAt ||
      raw?.updatedAt ||
      raw?.lastModified ||
      raw?.attributes?.modifiedAt ||
      "",
    status:
      raw?.status ||
      raw?.state ||
      raw?.attributes?.status ||
      "available",
    toolCount:
      typeof raw?.toolCount === "number"
        ? raw.toolCount
        : typeof raw?.attributes?.toolCount === "number"
        ? raw.attributes.toolCount
        : null,
  };
};

const getBadgeClass = (status: string) => {
  const value = status.toLowerCase();
  if (value.includes("fail") || value.includes("error") || value.includes("block")) return "failed";
  if (value.includes("warn") || value.includes("review") || value.includes("draft")) return "review";
  return "done";
};

const formatRelativeTime = (input: string) => {
  if (!input) return "unknown time";
  const time = new Date(input).getTime();
  if (Number.isNaN(time)) return "unknown time";

  const diffMs = Date.now() - time;
  const diffMinutes = Math.floor(diffMs / (1000 * 60));
  if (diffMinutes < 1) return "just now";
  if (diffMinutes < 60) return `${diffMinutes} min ago`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} hr ago`;

  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays} day${diffDays === 1 ? "" : "s"} ago`;
};

export default function AppsPage() {
  const [workflows, setWorkflows] = useState<WorkflowItem[]>([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(true);
  const [workflowsError, setWorkflowsError] = useState("");

  const sources = useMemo(
    () => [
      { label: ".yxmd workflow", status: "Standard", kind: "ok" },
      { label: ".yxwz packaged", status: "Extracts all workflows", kind: "ok" },
      { label: ".yxmc macro", status: "Inline expansion", kind: "ok" },
      { label: ".yxzp package", status: "Partial - no assets", kind: "warn" },
    ],
    []
  );

  useEffect(() => {
    const tenant = (sessionStorage.getItem("tenant_url") || "").trim().replace(/\/$/, "");
    const token = (sessionStorage.getItem("qlik_api_key") || "").trim();

    if (!tenant) {
      setWorkflowsError("Tenant URL missing. Connect again to load workflows.");
      setLoadingWorkflows(false);
      return;
    }

    if (!token) {
      setWorkflowsError("Access token missing. Paste token on Connect page first.");
      setLoadingWorkflows(false);
      return;
    }

    const controller = new AbortController();

    const fetchWorkflows = async () => {
      setLoadingWorkflows(true);
      setWorkflowsError("");

      const endpoints = [
        `${tenant}/v3/workflows?limit=50`,
        `${tenant}/v1/workflows?limit=50`,
      ];

      let lastError = "Failed to fetch workflows";

      for (const endpoint of endpoints) {
        try {
          const response = await fetch(endpoint, {
            method: "GET",
            headers: {
              Authorization: `Bearer ${token}`,
              Accept: "application/json",
            },
            signal: controller.signal,
          });

          if (!response.ok) {
            const body = await response.text();
            lastError = `Workflow API error (${response.status}): ${body || response.statusText}`;
            continue;
          }

          const payload = await response.json();
          const normalized = toArray(payload)
            .map(normalizeWorkflow)
            .filter((w): w is WorkflowItem => Boolean(w));

          setWorkflows(normalized);
          setLoadingWorkflows(false);
          return;
        } catch (error: any) {
          lastError = error?.message || "Network error while loading workflows";
        }
      }

      setWorkflowsError(lastError);
      setLoadingWorkflows(false);
    };

    fetchWorkflows();

    return () => controller.abort();
  }, []);

  return (
    <div className="alx-wrap">
      <div className="alx-top-steps" aria-label="Workflow steps">
        <button className="alx-pill active">1 - Upload &amp; parse</button>
        <button className="alx-pill">2 - Tool mapping</button>
        <button className="alx-pill">3 - M query output</button>
        <button className="alx-pill">4 - Validation</button>
        <button className="alx-pill">5 - Publish</button>
        <button className="alx-pill">6 - Project dashboard</button>
      </div>

      <p className="alx-section-tag">SCREEN 1 OF 6 - UPLOAD &amp; PARSE</p>

      <section className="alx-surface">
        <aside className="alx-sidebar">
          <div className="alx-brand-box">
            <h3>AlteryxAI</h3>
            <p>Alteryx - Power BI / Fabric</p>
          </div>

          <div className="alx-nav-group">
            <span className="alx-group-title">WORKSPACE</span>
            <button className="alx-nav-item active">Dashboard</button>
            <button className="alx-nav-item">Projects</button>
          </div>

          <div className="alx-nav-group">
            <span className="alx-group-title">MIGRATION</span>
            <button className="alx-nav-item">New migration</button>
            <button className="alx-nav-item">Conversion log</button>
            <button className="alx-nav-item">M query library</button>
          </div>

          <div className="alx-nav-group">
            <span className="alx-group-title">TOOLS</span>
            <button className="alx-nav-item">Report intelligence</button>
            <button className="alx-nav-item">Settings</button>
          </div>

          <button className="alx-upload-btn">+ Upload .yxmd / .yxwz</button>
        </aside>

        <div className="alx-main">
          <div className="alx-header-row">
            <div>
              <h2>Upload &amp; parse</h2>
              <p>No file loaded - drop a .yxmd or .yxwz to begin</p>
            </div>
            <button className="alx-browse-btn">Browse files</button>
          </div>

          <div className="alx-flow-row">
            <div className="alx-step done"><span>1</span><small>Upload .yxmd</small></div>
            <div className="alx-line" />
            <div className="alx-step"><span>2</span><small>Tool mapping</small></div>
            <div className="alx-line" />
            <div className="alx-step"><span>3</span><small>M Query gen</small></div>
            <div className="alx-line" />
            <div className="alx-step"><span>4</span><small>Validate</small></div>
            <div className="alx-line" />
            <div className="alx-step"><span>5</span><small>Publish</small></div>
        </div>

          <div className="alx-dropzone">
            <h3>Drag and drop your Alteryx workflow <span>Required</span></h3>
            <p>Supports .yxmd - .yxwz - .yxmc (macros) - .yxzp (package)</p>
            <div className="alx-drop-actions">
              <button>Browse files</button>
              <button>Connect to Alteryx Gallery <em>New</em></button>
            </div>
          </div>

          <div className="alx-grid-panels">
            <div className="alx-panel">
              <div className="alx-panel-head">
                <h4>Supported input sources</h4>
                <span>Auto-detected</span>
              </div>
              <ul>
                {sources.map((source) => (
                  <li key={source.label} className={source.kind === "warn" ? "warn" : "ok"}>
                    <strong>{source.label}</strong>
                    <small>{source.status}</small>
                  </li>
                ))}
              </ul>
            </div>

            <div className="alx-panel">
              <div className="alx-panel-head">
                <h4>Workflows from Alteryx</h4>
              </div>
              <ul>
                {loadingWorkflows && (
                  <li className="alx-upload-row">
                    <div>
                      <strong>Loading workflows...</strong>
                      <small>Contacting Alteryx API</small>
                    </div>
                    <span className="state review">Loading</span>
                  </li>
                )}

                {!loadingWorkflows && workflowsError && (
                  <li className="alx-upload-row">
                    <div>
                      <strong>Could not load workflows</strong>
                      <small>{workflowsError}</small>
                    </div>
                    <span className="state failed">Failed</span>
                  </li>
                )}

                {!loadingWorkflows && !workflowsError && workflows.length === 0 && (
                  <li className="alx-upload-row">
                    <div>
                      <strong>No workflows found</strong>
                      <small>This workspace does not currently have workflows.</small>
                    </div>
                    <span className="state review">Empty</span>
                  </li>
                )}

                {!loadingWorkflows && !workflowsError && workflows.slice(0, 6).map((workflow) => (
                  <li key={workflow.id} className="alx-upload-row">
                    <div>
                      <strong>{workflow.name}</strong>
                      <small>
                        {formatRelativeTime(workflow.modifiedAt)} - {workflow.toolCount ?? "N/A"} tools
                      </small>
                    </div>
                    <span className={`state ${getBadgeClass(workflow.status)}`}>{workflow.status}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="alx-legend">
            <span>.yxmd - standard Alteryx Designer workflow</span>
            <span>Macro expansion - flattened into main graph</span>
            <span>Unsupported - manual intervention required</span>
          </div>
        </div>
      </section>
    </div>
  );
}
