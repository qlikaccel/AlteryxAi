import "./AppsPage.css";
import { useEffect, useState } from "react";
import { downloadBusinessSpecificDoc, fetchApps, fetchTables } from "../api/qlikApi";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../context/WizardContext";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";

// ── Types ────────────────────────────────────────────────────────────────────

interface App {
  id: string;
  name: string;
  lastModifiedDate?: string;
}

interface AlteryxWorkflow {
  id: string;
  name: string;
  lastModifiedDate?: string;
  runCount?: number;
  credentialType?: string;
  workerTag?: string;
}

// ── Alteryx API helpers ──────────────────────────────────────────────────────

const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

async function fetchAlteryxWorkflows(workspaceId: string, accessToken: string): Promise<AlteryxWorkflow[]> {
  const storedRefreshToken = sessionStorage.getItem("alteryx_refresh_token");
  const headers: Record<string, string> = {
    "Authorization": `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  };

  if (storedRefreshToken) {
    headers["X-Alteryx-Refresh-Token"] = storedRefreshToken;
  }

  const res = await fetch(
    `${BASE_URL}/api/alteryx/workflows?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      headers,
    }
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to fetch workflows (${res.status})`);
  }

  const rotatedRefreshToken = res.headers.get("X-Alteryx-Refresh-Token");
  if (rotatedRefreshToken) {
    sessionStorage.setItem("alteryx_refresh_token", rotatedRefreshToken);
  }

  const data = await res.json();
  return data.workflows || [];
}

// ── Component ────────────────────────────────────────────────────────────────

export default function AppsPage() {
  const platform = sessionStorage.getItem("platform") || "qlik";

  // Qlik state
  const [apps, setApps] = useState<App[]>([]);
  const [tableCount, setTableCount] = useState<Record<string, number>>({});

  // Alteryx state
  const [workflows, setWorkflows] = useState<AlteryxWorkflow[]>([]);

  // Shared state
  const [loading, setLoading] = useState(true);
  const [brdDownloading, setBrdDownloading] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [favourites, setFavourites] = useState<string[]>([]);
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sortNewestFirst] = useState(true);

  const nav = useNavigate();
  const { stopTimer, startTimer } = useWizard();

  // ── Data fetch ─────────────────────────────────────────────────────────────
  useEffect(() => {
    if (platform === "alteryx") {
      // ── Alteryx path ──
      const workspaceId = sessionStorage.getItem("alteryx_workspace_id");
      const accessToken = sessionStorage.getItem("alteryx_access_token");

      if (!workspaceId || !accessToken) {
        alert("Alteryx session missing. Please login again.");
        nav("/");
        return;
      }

      if (sessionStorage.getItem("lastTimerTarget") !== "/apps") {
        startTimer?.("/apps");
      }

      fetchAlteryxWorkflows(workspaceId, accessToken)
        .then((list) => {
          setPageError(null);
          setWorkflows(list);
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

    } else {
      // ── Qlik path (unchanged) ──
      const tenantUrl = sessionStorage.getItem("tenant_url");

      if (!tenantUrl) {
        alert("Tenant URL missing. Please login again.");
        nav("/");
        return;
      }

      if (sessionStorage.getItem("lastTimerTarget") !== "/apps") {
        startTimer?.("/apps");
      }

      fetchApps(tenantUrl)
        .then(async (appList) => {
          setPageError(null);
          setApps(appList || []);

          const counts: Record<string, number> = {};
          for (const app of appList || []) {
            try {
              const tables = await fetchTables(app.id);
              counts[app.id] = tables.length;
            } catch {
              counts[app.id] = 0;
            }
          }
          setTableCount(counts);
        })
        .catch((err: any) => {
          setPageError(err?.message || "Backend not connected");
          setApps([]);
        })
        .finally(() => {
          const elapsed = stopTimer?.("/apps");
          setPageLoadTime(elapsed ?? null);
          setLoading(false);
        });
    }
  }, [nav, stopTimer]);

  // ── Helpers ────────────────────────────────────────────────────────────────

  const toggleFav = (id: string) =>
    setFavourites((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );

  const openSummary = (id: string, name?: string) => {
    sessionStorage.setItem("appSelected", id);
    sessionStorage.setItem("appName", name || id);
    startTimer?.("/summary");
    nav("/summary", { state: { appId: id, appName: name } });
  };

  const getRelativeTime = (dateStr?: string) => {
    if (!dateStr) return "Updated —";
    const diffMs = Date.now() - new Date(dateStr).getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHr / 24);
    if (diffMin < 1) return "Updated just now";
    if (diffMin < 60) return `Updated ${diffMin} minute${diffMin > 1 ? "s" : ""} ago`;
    if (diffHr < 24) return `Updated ${diffHr} hour${diffHr > 1 ? "s" : ""} ago`;
    return `Updated ${diffDay} day${diffDay > 1 ? "s" : ""} ago`;
  };

  const handleBusinessDocDownload = async () => {
    setBrdDownloading(true);
    try {
      await downloadBusinessSpecificDoc();
    } catch (error: any) {
      alert(`Failed to generate the consolidated Business Specific Doc: ${error?.message || "Unknown error"}`);
    } finally {
      setBrdDownloading(false);
    }
  };

  // ── Filtered lists ─────────────────────────────────────────────────────────

  const filteredApps = apps
    .filter((a) => a.name.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => {
      if (sortNewestFirst) {
        const da = a.lastModifiedDate ? new Date(a.lastModifiedDate).getTime() : 0;
        const db = b.lastModifiedDate ? new Date(b.lastModifiedDate).getTime() : 0;
        return db - da;
      }
      return a.name.localeCompare(b.name);
    });

  const filteredWorkflows = workflows
    .filter((w) => w.name.toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => {
      if (sortNewestFirst) {
        const da = a.lastModifiedDate ? new Date(a.lastModifiedDate).getTime() : 0;
        const db = b.lastModifiedDate ? new Date(b.lastModifiedDate).getTime() : 0;
        return db - da;
      }
      return a.name.localeCompare(b.name);
    });

  const totalItems = platform === "alteryx" ? workflows.length : apps.length;
  const workspaceName = sessionStorage.getItem("alteryx_workspace_name") || "";

  // ── Loading ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <LoadingOverlay
        isVisible={loading}
        message={
          platform === "alteryx"
            ? `Loading Alteryx workflows from "${workspaceName}"...`
            : "Loading QlikSense applications..."
        }
      />
    );
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="wrap">
      {/* HEADER */}
      <div className="qlik-header">
        <div className="qlik-header-left-group">
          <div className="qlik-header-left">
            {platform === "alteryx" ? (
              <>
                <span className="platform-badge alteryx-badge">Alteryx</span>
                {totalItems} Workflow{totalItems !== 1 ? "s" : ""}
                {workspaceName && (
                  <span className="workspace-pill" title={workspaceName}>
                    {workspaceName}
                  </span>
                )}
              </>
            ) : (
              <>
                {totalItems} Application{totalItems !== 1 ? "s" : ""}
              </>
            )}
          </div>

          {/* BRD download — Qlik only */}
          {platform === "qlik" && (
            <button
              type="button"
              className="business-doc-icon-btn"
              onClick={handleBusinessDocDownload}
              disabled={apps.length === 0 || brdDownloading}
              title="Business Requirements Document"
              aria-label="Download Business Requirements Document"
            >
              <svg className="business-doc-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path
                  d="M7 2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm6 1.5V8h4.5"
                  fill="none" stroke="currentColor" strokeWidth="1.8"
                  strokeLinecap="round" strokeLinejoin="round"
                />
                <path
                  d="M8.5 11h7M8.5 14.5h7M8.5 18h4.5"
                  fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
                />
              </svg>
              {brdDownloading && <span className="business-doc-spinner" aria-hidden="true" />}
            </button>
          )}
        </div>

        <div className="qlik-header-right">
          <div className="tools">
            <input
              type="search"
              placeholder={platform === "alteryx" ? "Search Workflows..." : "Search Apps..."}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="apps-search"
            />
            {pageLoadTime && !loading && (
              <div className="timer-badge">Analysis Time: {pageLoadTime}</div>
            )}
          </div>
        </div>
      </div>

      {/* Error banner */}
      {pageError && (
        <div style={{ marginBottom: 12, color: "#b91c1c", fontWeight: 600 }}>
          {pageError}
        </div>
      )}

      {/* ── QLIK APP CARDS ── */}
      {platform === "qlik" && (
        <div className="card-container">
          {filteredApps.map((app) => {
            const count = tableCount[app.id] ?? 0;
            const isDisabled = count === 0;
            return (
              <div
                key={app.id}
                className={`app-card ${isDisabled ? "disabled" : ""}`}
                onClick={() => { if (!isDisabled) openSummary(app.id, app.name); }}
                role="button"
                aria-disabled={isDisabled}
                title={isDisabled ? "No tables available" : "Open summary"}
              >
                <div className="card-center">
                  <img src="/qlik-chart.png" className="qlik-img" alt="qlik" />
                </div>
                <div className="card-footer">
                  <div className="footer-left">
                    <span className="app-label">{app.name}</span>
                    <span className="last-modified">{getRelativeTime(app.lastModifiedDate)}</span>
                  </div>
                  <div className="right-actions">
                    <span className="badge">{count}</span>
                    <span className="fav-icon" onClick={(e) => { e.stopPropagation(); toggleFav(app.id); }}>
                      {favourites.includes(app.id) ? "★" : "☆"}
                    </span>
                    <span className="dot-menu">⋯</span>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── ALTERYX WORKFLOW CARDS ── */}
      {platform === "alteryx" && (
        <div className="card-container">
          {filteredWorkflows.length === 0 && !pageError && (
            <div className="empty-state">
              <span className="empty-icon">🔍</span>
              <p>No workflows found in workspace <strong>{workspaceName}</strong>.</p>
            </div>
          )}
          {filteredWorkflows.map((wf) => (
            <div
              key={wf.id}
              className="app-card alteryx-card"
              onClick={() => openSummary(wf.id, wf.name)}
              role="button"
              title="Open workflow summary"
            >
              <div className="card-center">
                <div className="alteryx-workflow-icon">
                  <svg viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect width="40" height="40" rx="8" fill="#0070c0" />
                    <path d="M8 20h6m18 0h-6M20 8v6m0 18v-6" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
                    <circle cx="20" cy="20" r="5" fill="white" fillOpacity="0.9"/>
                    <path d="M14 14l4 4m8 8l-4-4M26 14l-4 4M14 26l4-4" stroke="white" strokeWidth="1.5" strokeLinecap="round"/>
                  </svg>
                </div>
              </div>
              <div className="card-footer">
                <div className="footer-left">
                  <span className="app-label">{wf.name}</span>
                  <span className="last-modified">{getRelativeTime(wf.lastModifiedDate)}</span>
                </div>
                <div className="right-actions">
                  {wf.runCount !== undefined && (
                    <span className="badge" title="Run count">{wf.runCount}</span>
                  )}
                  <span className="fav-icon" onClick={(e) => { e.stopPropagation(); toggleFav(wf.id); }}>
                    {favourites.includes(wf.id) ? "★" : "☆"}
                  </span>
                  <span className="dot-menu">⋯</span>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
