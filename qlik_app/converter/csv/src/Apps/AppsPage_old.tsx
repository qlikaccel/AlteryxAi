
import "./AppsPage.css";
import { useEffect, useState } from "react";
import { fetchWorkflows } from "../api/qlikApi";

type WorkflowItem = {
  id: string;
  name: string;
  updatedAt: string | null;
  owner: string | null;
  status: string;
  toolCount: number | null;
};

const normalizeWorkflow = (raw: any): WorkflowItem | null => {
  const id = raw?.id || "";
  if (!id) return null;

  return {
    id,
    name: raw?.name || "Unnamed Workflow",
    updatedAt: raw?.updatedAt || null,
    owner: raw?.owner || null,
    status: raw?.status || "available",
    toolCount: raw?.toolCount || null,
  };
};

const formatRelativeTime = (input: string | null) => {
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
  const [filteredWorkflows, setFilteredWorkflows] = useState<WorkflowItem[]>([]);
  const [loadingWorkflows, setLoadingWorkflows] = useState(true);
  const [workflowsError, setWorkflowsError] = useState("");
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    const baseUrl = (sessionStorage.getItem("alteryx_base_url") || "").trim();
    const isConnected = sessionStorage.getItem("connected") === "true";

    if (!isConnected) {
      setWorkflowsError("Not connected to Alteryx Cloud. Please connect on the Connect page.");
      setLoadingWorkflows(false);
      return;
    }

    if (!baseUrl) {
      setWorkflowsError("Alteryx Cloud base URL missing. Please reconnect.");
      setLoadingWorkflows(false);
      return;
    }

    const loadWorkflows = async () => {
      setLoadingWorkflows(true);
      setWorkflowsError("");

      try {
        console.log("🔄 Fetching workflows from backend...");
        const response = await fetchWorkflows(100, 0);

        console.log("📦 Response received:", response);

        if (response.success && response.workflows) {
          const normalized = response.workflows
            .map(normalizeWorkflow)
            .filter((w): w is WorkflowItem => Boolean(w));
          
          console.log(`✅ Found ${normalized.length} workflows`);
          setWorkflows(normalized);
          setFilteredWorkflows(normalized);
        } else {
          const errorMsg = response.message || "Failed to fetch workflows";
          console.error("❌ Backend error:", errorMsg);
          setWorkflowsError(errorMsg);
        }
      } catch (error: any) {
        const errorMsg = error instanceof Error ? error.message : "Failed to fetch workflows";
        console.error("❌ Network/API error:", errorMsg);
        setWorkflowsError(errorMsg);
      } finally {
        setLoadingWorkflows(false);
      }
    };

    loadWorkflows();
  }, []);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    
    if (!value.trim()) {
      setFilteredWorkflows(workflows);
    } else {
      const query = value.toLowerCase();
      const filtered = workflows.filter((workflow) =>
        workflow.name.toLowerCase().includes(query) ||
        workflow.owner?.toLowerCase().includes(query) ||
        workflow.id.toLowerCase().includes(query)
      );
      setFilteredWorkflows(filtered);
    }
  };

  return (
    <div className="qlik-app-container">
      {/* Stepper Navigation */}
      <div className="qlik-stepper">
        <div className="qlik-step-item active">
          <span className="qlik-step-icon">1</span>
          <div className="qlik-step-text">
            <div className="qlik-step-label">Connect</div>
            <div className="qlik-step-sub">Connect to Alteryx Cloud</div>
          </div>
        </div>

        <div className="qlik-step-item active-current">
          <span className="qlik-step-icon">2</span>
          <div className="qlik-step-text">
            <div className="qlik-step-label">Discovery</div>
            <div className="qlik-step-sub">Workflows & Metadata</div>
          </div>
        </div>

        <div className="qlik-step-item">
          <span className="qlik-step-icon">3</span>
          <div className="qlik-step-text">
            <div className="qlik-step-label">Summary</div>
            <div className="qlik-step-sub">Assessment</div>
          </div>
        </div>

        <div className="qlik-step-item">
          <span className="qlik-step-icon">4</span>
          <div className="qlik-step-text">
            <div className="qlik-step-label">Export</div>
            <div className="qlik-step-sub">Build & Convert</div>
          </div>
        </div>

        <div className="qlik-step-item">
          <span className="qlik-step-icon">5</span>
          <div className="qlik-step-text">
            <div className="qlik-step-label">Publish</div>
            <div className="qlik-step-sub">Publish Results</div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="qlik-content">
        {/* Header */}
        <div className="qlik-header">
          <div className="qlik-header-left">
            <h1 className="qlik-title">
              {workflows.length} Workflow{workflows.length !== 1 ? "s" : ""}
            </h1>
          </div>
          <div className="qlik-header-right">
            <input
              type="text"
              placeholder="Search Workflows..."
              value={searchQuery}
              onChange={(e) => handleSearch(e.target.value)}
              className="qlik-search"
            />
          </div>
        </div>

        {/* Workflows Grid */}
        {loadingWorkflows && (
          <div className="qlik-loading">
            <div className="qlik-spinner"></div>
            <p>Loading workflows...</p>
          </div>
        )}

        {!loadingWorkflows && workflowsError && (
          <div className="qlik-error">
            <p>{workflowsError}</p>
            <button onClick={() => window.location.reload()} className="qlik-retry-btn">
              Retry
            </button>
          </div>
        )}

        {!loadingWorkflows && !workflowsError && workflows.length === 0 && (
          <div className="qlik-empty">
            <p>No workflows found</p>
          </div>
        )}

        {!loadingWorkflows && !workflowsError && filteredWorkflows.length === 0 && workflows.length > 0 && (
          <div className="qlik-empty">
            <p>No workflows match your search</p>
          </div>
        )}

        {!loadingWorkflows && !workflowsError && filteredWorkflows.length > 0 && (
          <div className="qlik-grid">
            {filteredWorkflows.map((workflow) => (
              <div key={workflow.id} className="qlik-card">
                <div className="qlik-card-icon">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z" />
                  </svg>
                </div>
                
                <div className="qlik-card-title">{workflow.name}</div>
                
                <div className="qlik-card-meta">
                  <span className="qlik-card-date">Updated {formatRelativeTime(workflow.updatedAt)}</span>
                  {workflow.toolCount !== null && (
                    <span className="qlik-card-tools">{workflow.toolCount}</span>
                  )}
                </div>

                <div className="qlik-card-footer">
                  <button className="qlik-card-star">★</button>
                  <button className="qlik-card-more">⋯</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
