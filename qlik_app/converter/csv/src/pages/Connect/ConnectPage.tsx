import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";

export default function ConnectPage() {
  // ── Alteryx state ────────────────────────────────────────────────────────
  const [alteryxWorkspaceName, setAlteryxWorkspaceName] = useState("");
  const [workspaceTouched, setWorkspaceTouched] = useState(false);

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

  const trimmedWorkspaceName = alteryxWorkspaceName.trim();
  const isWorkspaceNameValid = /^[^-]+-[^-]+-[^-]+-(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{4}$/.test(trimmedWorkspaceName);
  const canConnectAlteryx = isWorkspaceNameValid;

  const handleConnect = async () => {
    if (!canConnectAlteryx) return;
    setLoading(true);
    setError("");
    try {
      const workspace = alteryxWorkspaceName.trim();
      sessionStorage.setItem("platform", "alteryx");
      sessionStorage.setItem("alteryx_workspace_name", workspace);
      sessionStorage.setItem("connected", "true");

      // Temporarily bypass access token validation and proceed directly.
      // The actual token-based validation will be implemented later.
      startTimer?.("/convert");
      navigate("/convert");
    } catch (err: any) {
      setError(err?.message || "Unable to continue. Please try again.");
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
            onChange={(e) => {
              setAlteryxWorkspaceName(e.target.value);
              setError("");
            }}
            onBlur={() => setWorkspaceTouched(true)}
            disabled={loading}
          />
          {/* <p className="field-hint">
            Visible in the top-right corner of Alteryx One
          </p> */}
          {workspaceTouched && trimmedWorkspaceName && !isWorkspaceNameValid && (
            <p className="field-error">Enter a valid workspace name. The last segment must be exactly 4 alphanumeric characters with both letters and numbers.</p>
          )}
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
