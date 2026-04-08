


import "./AppsPage.css";
import { useEffect, useState } from "react";
import { downloadBusinessSpecificDoc, fetchApps, fetchTables } from "../api/qlikApi";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../context/WizardContext";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";

interface App {
  id: string;
  name: string;
  lastModifiedDate?: string;
}

export default function AppsPage() {
  const [apps, setApps] = useState<App[]>([]);
  const [tableCount, setTableCount] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [appsError, setAppsError] = useState<string | null>(null);
  const [favourites, setFavourites] = useState<string[]>([]);
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [generatingBrd, setGeneratingBrd] = useState(false);

  const [query, setQuery] = useState("");
  const [sortNewestFirst] = useState(true);
  

  const nav = useNavigate();
  const { stopTimer, startTimer } = useWizard();
  useEffect(() => {
    // 🔑 Get tenant URL saved during login (use sessionStorage like ConnectPage)
    const tenantUrl = sessionStorage.getItem("tenant_url");

    if (!tenantUrl) {
      alert("Tenant URL missing. Please login again.");
      nav("/");
      return;
    }

    // ensure we have a running timer for /apps when arriving directly
    if (sessionStorage.getItem("lastTimerTarget") !== "/apps") {
      startTimer?.("/apps");
    }

    fetchApps(tenantUrl)
      .then(async (appList) => {
        setAppsError(null);
        setApps(appList || []);

        const counts: Record<string, number> = {};

        for (const app of appList || []) {
          try {
            const tables = await fetchTables(app.id);
            counts[app.id] = tables.length;
          } catch {
            // Silently fail - app still shows without table count
            counts[app.id] = 0;
          }
        }

        setTableCount(counts);
      })
      .catch((err: any) => {
        const message = err?.message || "Backend not connected";
        setAppsError(message);
        setApps([]);
      })
      .finally(() => {
        const elapsed = stopTimer?.("/apps");
        setPageLoadTime(elapsed);
        setLoading(false);
      });
  }, [nav, stopTimer]);

  const toggleFav = (id: string) => {
    setFavourites((prev) =>
      prev.includes(id)
        ? prev.filter((i) => i !== id)
        : [...prev, id]
    );
  };

  const openSummary = (appId: string, appName?: string) => {
    // set selection flag so Stepper and validation know an app was chosen
    sessionStorage.setItem("appSelected", appId);
    sessionStorage.setItem("appName", appName || appId);

    // start timing for Summary loading
    startTimer?.("/summary");

    nav("/summary", { state: { appId, appName } });
  };

  if (loading) {
    return (
      <LoadingOverlay
        isVisible={loading}
        message="Loading QlikSense applications..."
      />
    );
  }



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

  const handleBusinessDocDownload = async () => {
    try {
      setGeneratingBrd(true);
      await downloadBusinessSpecificDoc();
    } catch (error: any) {
      alert(`Failed to generate the consolidated Business Specific Doc: ${error?.message || "Unknown error"}`);
    } finally {
      setGeneratingBrd(false);
    }
  };

  return (
    <div className="wrap">
      {/* HEADER */}
      <div className="qlik-header">
         <div className="qlik-header-left">
          {apps.length} Application{apps.length !== 1 ? 's' : ''} to explore
        </div>

        <div className="qlik-header-right">
          <div className="tools">
            <input
              type="search"
              placeholder="Search apps..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="apps-search"
            />

            <button
              type="button"
              className="business-doc-btn"
              onClick={handleBusinessDocDownload}
              disabled={generatingBrd || apps.length === 0}
              title="Generate and download the consolidated Business Requirements Document for the entire project"
            >
              {generatingBrd ? "Generating BRD..." : "Business Requirement Document"}
            </button>

            {/* <buttonz
              className="sort-btn"
              onClick={() => setSortNewestFirst((s) => !s)}
              title={sortNewestFirst ? "Sorting: newest first" : "Sorting: name"}
            >
              {sortNewestFirst ? "Newest" : "A→Z"}
            </button> */}

            {/* Page loading time */}
            {pageLoadTime && !loading && (
              <div className="timer-badge">
                Analysis Time: {pageLoadTime}
              </div>
            )}
          </div>
        </div>
      </div>

      {appsError && (
        <div style={{ marginBottom: 12, color: "#b91c1c", fontWeight: 600 }}>
          {appsError}
        </div>
      )}

      {/* APP CARDS */}
      <div className="card-container">
        {(
          filteredApps
            .map((app) => {
              const count = tableCount[app.id] ?? 0;
              const isDisabled = count === 0;
              const handleClick = () => {
                if (!isDisabled) openSummary(app.id, app.name);
              };
              return (
                <div
                  key={app.id}
                  className={`app-card ${isDisabled ? "disabled" : ""}`}
                  onClick={handleClick}
                  role="button"
                  aria-disabled={isDisabled}
                  title={isDisabled ? "No tables available" : "Open summary"}
                >
                  {/* IMAGE */}
                  <div className="card-center">
                    <img
                      src="/qlik-chart.png"
                      className="qlik-img"
                      alt="qlik"
                    />
                  </div>

                  {/* FOOTER */}
                  <div className="card-footer">
                    {/* LEFT SIDE */}
                    <div className="footer-left">
                      <span className="app-label">{app.name}</span>

                      <span className="last-modified">
                        {getRelativeTime(app.lastModifiedDate)}
                      </span>
                    </div>

                    {/* RIGHT SIDE */}
                    <div className="right-actions">
                      <span className="badge">
                        {count}
                      </span>

                      <span
                        className="fav-icon"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleFav(app.id);
                        }}
                      >
                        {favourites.includes(app.id) ? "★" : "☆"}
                      </span>

                      <span className="dot-menu">⋯</span>
                    </div>
                  </div>
                </div>
              );
            })
        )}
      </div>
    </div>
  );
}
