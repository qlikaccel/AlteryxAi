import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";

export default function ConnectPage() {
  // ── Alteryx state ────────────────────────────────────────────────────────
  const [alteryxWorkspaceName, setAlteryxWorkspaceName] = useState("");

  // ── Shared state ─────────────────────────────────────────────────────────
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();
  const { startTimer } = useWizard();

  // ── Restore session values ───────────────────────────────────────────────
  useEffect(() => {
    const savedAlteryxWorkspace = sessionStorage.getItem("alteryx_workspace_name");
    if (savedAlteryxWorkspace) setAlteryxWorkspaceName(savedAlteryxWorkspace);
  }, []);

  const canConnectAlteryx = alteryxWorkspaceName.trim().length > 0;

  const handleConnect = async () => {
    if (!canConnectAlteryx) return;
    setLoading(true);
    setError("");
    try {
      const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const payload: Record<string, unknown> = {
        access_token: "",
        workspace_name: alteryxWorkspaceName.trim(),
      };
      const res = await fetch(`${BASE_URL}/api/alteryx/validate-auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Alteryx connection failed.");

      sessionStorage.setItem("platform", "alteryx");
      sessionStorage.setItem("alteryx_access_token", data.access_token);
      sessionStorage.setItem("alteryx_workspace_id", data.workspace_id);
      sessionStorage.setItem("alteryx_workspace_name", data.workspace_name);
      if (data.refresh_token) {
        sessionStorage.setItem("alteryx_refresh_token", data.refresh_token);
      }
      sessionStorage.setItem("connected", "true");

      startTimer?.("/apps");
      navigate("/apps");
    } catch (err: any) {
      setError(err?.message || "Connection failed. Please check your token and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="connect-wrapper">
      <div className="connect-card">
        <div className="card-header">
          <div className="card-header-text">
            <h1>Connect to Alteryx Cloud</h1>
            <p>Workspace Name only. Access and Refresh tokens are loaded automatically from backend environment variables.</p>
          </div>
        </div>

        <div className="field-group">
          <label htmlFor="alteryx-workspace">
            Workspace Name <span className="required-star">*</span>
          </label>
          <input
            id="alteryx-workspace"
            type="text"
            placeholder="e.g. sorim-alteryx-trial-2hcg"
            value={alteryxWorkspaceName}
            onChange={(e) => { setAlteryxWorkspaceName(e.target.value); setError(""); }}
            disabled={loading}
          />
          <p className="field-hint">
            Visible in the top-right corner of Alteryx One
          </p>
        </div>

        {error && (
          <div className="error">
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "18px" }}>⚠️</span>
              <span>{error}</span>
            </div>
          </div>
        )}

        <div className="actions">
          <button
            onClick={handleConnect}
            disabled={!canConnectAlteryx || loading}
            style={{ opacity: canConnectAlteryx ? 1 : 0.5, cursor: canConnectAlteryx ? "pointer" : "not-allowed" }}
          >
            {loading ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
