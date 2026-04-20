import "./AppsPage.css";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../context/WizardContext";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
import {
  fetchAlteryxWorkflows,
  fetchUploadedAlteryxWorkflows,
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

  const openSummary = (workflow: AlteryxWorkflow) => {
    sessionStorage.setItem("appSelected", workflow.id);
    sessionStorage.setItem("appName", workflow.name);
    sessionStorage.setItem("alteryx_workflow_id", workflow.id);
    sessionStorage.setItem("alteryx_workflow_name", workflow.name);
    sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(workflow));
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
            <span className="platform-badge alteryx-badge">Alteryx</span>
            {workflows.length} Workflow{workflows.length !== 1 ? "s" : ""}
            {platform === "alteryx_upload" && (
              <span className="workspace-pill" title="Bulk upload assessment">
                Bulk Upload
              </span>
            )}
            {workspaceName && (
              <span className="workspace-pill" title={workspaceName}>
                {workspaceName}
              </span>
            )}
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
                <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <rect width="40" height="40" rx="8" fill="#0070c0" />
                  <path d="M8 20h6m18 0h-6M20 8v6m0 18v-6" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
                  <circle cx="20" cy="20" r="5" fill="white" fillOpacity="0.9" />
                  <path d="M14 14l4 4m8 8l-4-4M26 14l-4 4M14 26l4-4" stroke="white" strokeWidth="1.5" strokeLinecap="round" />
                </svg>
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
                <span className="fav-icon" onClick={(event) => { event.stopPropagation(); toggleFav(workflow.id); }}>
                  {favourites.includes(workflow.id) ? "*" : "+"}
                </span>
                <span className="dot-menu">...</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
