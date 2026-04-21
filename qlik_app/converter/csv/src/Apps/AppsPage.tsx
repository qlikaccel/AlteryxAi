import "./AppsPage.css";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../context/WizardContext";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
import {
  fetchAlteryxWorkflows,
  fetchUploadedAlteryxWorkflows,
  materializeCloudAlteryxWorkflow,
} from "../api/alteryxApi";
import type { AlteryxWorkflow } from "../api/alteryxApi";

export default function AppsPage() {
  const platform = sessionStorage.getItem("platform") || "alteryx_upload";
  const [workflows, setWorkflows] = useState<AlteryxWorkflow[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [favourites, setFavourites] = useState<string[]>([]);
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sortNewestFirst] = useState(true);

  const nav = useNavigate();
  const { stopTimer, startTimer } = useWizard();

  useEffect(() => {
    if (sessionStorage.getItem("lastTimerTarget") !== "/apps") {
      startTimer?.("/apps");
    }

    const loadWorkflows = async () => {
      if (platform === "alteryx_upload") {
        const batchId = sessionStorage.getItem("alteryx_batch_id");
        if (!batchId) {
          nav("/");
          return;
        }
        return fetchUploadedAlteryxWorkflows(batchId);
      }

      const workspaceId = sessionStorage.getItem("alteryx_workspace_id");
      const accessToken = sessionStorage.getItem("alteryx_access_token");
      if (!workspaceId || !accessToken) {
        nav("/");
        return;
      }
      return fetchAlteryxWorkflows(workspaceId, accessToken);
    };

    loadWorkflows()
      .then((list) => {
        setPageError(null);
        setWorkflows(list || []);
      })
      .catch((err: any) => {
        setPageError(err?.message || "Failed to load Alteryx workflows");
        setWorkflows([]);
      })
      .finally(() => {
        const elapsed = stopTimer?.("/apps");
        setPageLoadTime(elapsed ?? null);
        setLoading(false);
      });
  }, [nav, platform, startTimer, stopTimer]);

  const toggleFav = (id: string) =>
    setFavourites((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );

  const openSummary = async (workflow: AlteryxWorkflow) => {
    sessionStorage.setItem("appSelected", workflow.id);
    sessionStorage.setItem("appName", workflow.name);
    sessionStorage.setItem("alteryx_workflow_id", workflow.id);
    sessionStorage.setItem("alteryx_workflow_name", workflow.name);
    sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(workflow));

    if (platform !== "alteryx_upload") {
      setLoading(true);
      setPageError(null);
      try {
        const materialized = await materializeCloudAlteryxWorkflow({
          workflow_id: workflow.id,
          workflow_name: workflow.name,
          workspace_id: sessionStorage.getItem("alteryx_workspace_id") || undefined,
          workspace_name: sessionStorage.getItem("alteryx_workspace_name") || undefined,
        });
        const parsedWorkflow = materialized.workflow || materialized.workflows?.[0];
        if (!parsedWorkflow?.id || !materialized.batch_id) {
          throw new Error("Cloud workflow package was downloaded, but no parseable workflow was found.");
        }
        sessionStorage.setItem("platform", "alteryx_upload");
        sessionStorage.setItem("alteryx_batch_id", materialized.batch_id);
        sessionStorage.setItem("alteryx_batch_summary", JSON.stringify(materialized.summary || {}));
        sessionStorage.setItem("alteryx_workflow_id", parsedWorkflow.id);
        sessionStorage.setItem("alteryx_workflow_name", parsedWorkflow.name || workflow.name);
        sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(parsedWorkflow));
        sessionStorage.setItem("alteryx_cloud_source_workflow_id", workflow.id);
        sessionStorage.setItem("alteryx_cloud_artifact_name", materialized.artifact_name || "");
        startTimer?.("/summary");
        nav("/summary", { state: { workflowId: parsedWorkflow.id, workflowName: parsedWorkflow.name || workflow.name } });
      } catch (err: any) {
        setLoading(false);
        setPageError(err?.message || "Unable to download the full workflow package from Alteryx Cloud.");
        startTimer?.("/summary");
        nav("/summary", { state: { workflowId: workflow.id, workflowName: workflow.name, cloudMaterializeError: err?.message } });
      }
      return;
    }

    startTimer?.("/summary");
    nav("/summary", { state: { workflowId: workflow.id, workflowName: workflow.name } });
  };

  const getRelativeTime = (dateStr?: string) => {
    if (!dateStr) return "Updated date unavailable";
    const diffMs = Date.now() - new Date(dateStr).getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHr / 24);
    if (diffMin < 1) return "Updated just now";
    if (diffMin < 60) return `Updated ${diffMin} minute${diffMin > 1 ? "s" : ""} ago`;
    if (diffHr < 24) return `Updated ${diffHr} hour${diffHr > 1 ? "s" : ""} ago`;
    return `Updated ${diffDay} day${diffDay > 1 ? "s" : ""} ago`;
  };

  const filteredWorkflows = workflows
    .filter((workflow) => workflow.name.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => {
      if (sortNewestFirst) {
        const da = a.lastModifiedDate ? new Date(a.lastModifiedDate).getTime() : 0;
        const db = b.lastModifiedDate ? new Date(b.lastModifiedDate).getTime() : 0;
        return db - da;
      }
      return a.name.localeCompare(b.name);
    });

  const workspaceName = sessionStorage.getItem("alteryx_workspace_name") || "";

  if (loading) {
    return (
      <LoadingOverlay
        isVisible={loading}
        message={
          platform === "alteryx_upload"
            ? "Loading uploaded Alteryx workflow assessment..."
            : `Loading Alteryx workflows from "${workspaceName}"...`
        }
      />
    );
  }

  return (
    <div className="wrap">
      <div className="qlik-header">
        <div className="qlik-header-left-group">
          <div className="qlik-header-left">
            {/* <span className="platform-badge alteryx-badge">Alteryx</span> */}
            {workflows.length} Workflow{workflows.length !== 1 ? "s" : ""}
            {platform === "alteryx_upload" && (
              <span className="workspace-pill" title="Bulk upload assessment">
                Bulk Upload
              </span>
            )}
            {/* {workspaceName && (
              <span className="workspace-pill" title={workspaceName}>
                {workspaceName}
              </span>
            )} */}
          </div>
        </div>

        <div className="qlik-header-right">
          <div className="tools">
            <input
              type="search"
              placeholder="Search workflows..."
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="apps-search"
            />
            {pageLoadTime && !loading && (
              <div className="timer-badge">Assessment Time: {pageLoadTime}</div>
            )}
          </div>
        </div>
      </div>

      {pageError && (
        <div style={{ marginBottom: 12, color: "#b91c1c", fontWeight: 600 }}>
          {pageError}
        </div>
      )}
      
      

      <div className="card-container">
        {filteredWorkflows.length === 0 && !pageError && (
          <div className="empty-state">
            <span className="empty-icon">No workflows</span>
            <p>No Alteryx workflows were found for this migration batch.</p>
          </div>
        )}

        {filteredWorkflows.map((workflow) => (
          <div
            key={workflow.id}
            className="app-card alteryx-card"
            onClick={() => openSummary(workflow)}
            role="button"
            title="Open workflow assessment"
          >
            <div className="card-center">
              <div className="alteryx-workflow-icon">
  <div className="alteryx-workflow-icon">
    <svg viewBox="0 0 100 115" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M10 5 L65 5 L88 28 L88 105 L10 105 Z" fill="white" stroke="#1a7fd4" strokeWidth="4" strokeLinejoin="round"/>
    <path d="M65 5 L65 28 L88 28" fill="#cce0f5" stroke="#1a7fd4" strokeWidth="4" strokeLinejoin="round"/>
    <circle cx="34" cy="42" r="4" fill="#1a7fd4"/>
    <circle cx="34" cy="72" r="4" fill="#1a7fd4"/>
    <circle cx="66" cy="57" r="4" fill="#1a7fd4"/>
    <line x1="34" y1="42" x2="66" y2="57" stroke="#1a7fd4" strokeWidth="3" strokeLinecap="round"/>
    <line x1="34" y1="72" x2="66" y2="57" stroke="#1a7fd4" strokeWidth="3" strokeLinecap="round"/>
    <polygon points="42,50 42,64 54,57" fill="#1a7fd4"/>
    <circle cx="88" cy="105" r="14" fill="#1a7fd4"/>
    <polygon points="89,93 83,105 89,105 86,117 95,104 89,104" fill="white"/>
  </svg>
</div>
</div>
            </div>

            <div className="card-footer">
              <div className="footer-left">
                <span className="app-label">{workflow.name}</span>
                <span className="last-modified">{getRelativeTime(workflow.lastModifiedDate)}</span>
              </div>
              <div className="right-actions">
                {workflow.toolCount !== undefined && (
                  <span className="badge" title="Tool count">
                    {workflow.toolCount}
                  </span>
                )}
                {/* <span className="fav-icon" onClick={(event) => { event.stopPropagation(); toggleFav(workflow.id); }}>
                  {favourites.includes(workflow.id) ? "*" : "+"}
                </span> */}
                
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
