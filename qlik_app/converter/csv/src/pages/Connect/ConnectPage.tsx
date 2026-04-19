import "./ConnectPage.css";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";

export default function ConnectPage() {
  const [connectMode, setConnectMode] = useState<"upload" | "cloud">("upload");
  const [alteryxWorkspaceName, setAlteryxWorkspaceName] = useState("");
  const [alteryxUsername, setAlteryxUsername] = useState("");
  const [alteryxAccessToken, setAlteryxAccessToken] = useState("");
  const [alteryxRefreshToken, setAlteryxRefreshToken] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<FileList | null>(null);
  const [workspaceTouched, setWorkspaceTouched] = useState(false);
  const [usernameTouched, setUsernameTouched] = useState(false);
  const [accessTouched, setAccessTouched] = useState(false);
  const [refreshTouched, setRefreshTouched] = useState(false);

  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();
  const { startTimer } = useWizard();

  useEffect(() => {
    const savedWorkspace = sessionStorage.getItem("alteryx_workspace_name");
    const savedUsername = sessionStorage.getItem("alteryx_username");
    const savedAccessToken = sessionStorage.getItem("alteryx_access_token");
    const savedRefreshToken = sessionStorage.getItem("alteryx_refresh_token");

    if (savedWorkspace) setAlteryxWorkspaceName(savedWorkspace);
    if (savedUsername) setAlteryxUsername(savedUsername);
    if (savedAccessToken) setAlteryxAccessToken(savedAccessToken);
    if (savedRefreshToken) setAlteryxRefreshToken(savedRefreshToken);
  }, []);

  const trimmedWorkspaceName = alteryxWorkspaceName.trim();
  const trimmedUsername = alteryxUsername.trim();
  const trimmedAccessToken = alteryxAccessToken.trim();
  const trimmedRefreshToken = alteryxRefreshToken.trim();

  const isWorkspaceNameValid =
    /^[^-]+-[^-]+-[^-]+-(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]{4}$/.test(trimmedWorkspaceName);
  const canConnectAlteryx =
    isWorkspaceNameValid && Boolean(trimmedAccessToken) && Boolean(trimmedRefreshToken);
  const canUploadPackages = Boolean(selectedFiles?.length);

  const handleConnect = async () => {
    if (!canConnectAlteryx) return;

    setLoading(true);
    setError("");

    try {
      const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const payload: Record<string, unknown> = {
        access_token: trimmedAccessToken,
        refresh_token: trimmedRefreshToken,
        workspace_name: trimmedWorkspaceName,
        username: trimmedUsername || undefined,
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
      if (trimmedUsername) {
        sessionStorage.setItem("alteryx_username", trimmedUsername);
      }
      if (data.refresh_token) {
        sessionStorage.setItem("alteryx_refresh_token", data.refresh_token);
      }
      sessionStorage.setItem("connected", "true");

      startTimer?.("/apps");
      navigate("/apps");
    } catch (err: any) {
      setError(err?.message || "Connection failed. Please check your token pair and try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleBulkUpload = async () => {
    if (!selectedFiles?.length) return;

    setLoading(true);
    setError("");

    try {
      const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const formData = new FormData();
      Array.from(selectedFiles).forEach((file) => formData.append("files", file));

      const res = await fetch(`${BASE_URL}/api/alteryx/bulk-upload`, {
        method: "POST",
        body: formData,
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Bulk upload failed.");

      sessionStorage.setItem("platform", "alteryx_upload");
      sessionStorage.setItem("alteryx_batch_id", data.batch_id);
      sessionStorage.setItem("alteryx_batch_summary", JSON.stringify(data.summary || {}));
      sessionStorage.setItem("connected", "true");

      startTimer?.("/apps");
      navigate("/apps");
    } catch (err: any) {
      setError(err?.message || "Upload failed. Please check the package files and try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="connect-wrapper">
      <div className="connect-card">
        <div className="card-header">
          <div className="card-header-text">
            <h1>Connect to Alteryx</h1>
            <p>For high-volume migration, upload packages in bulk. Cloud token discovery is available as an optional admin path.</p>
          </div>
        </div>

        <div className="connection-methods">
          <button
            type="button"
            className={`method-btn ${connectMode === "upload" ? "active" : ""}`}
            onClick={() => setConnectMode("upload")}
            disabled={loading}
          >
            <span className="method-title">Bulk Upload</span>
            <span className="method-subtitle">.yxmd, .yxzp, .zip</span>
          </button>
          <button
            type="button"
            className={`method-btn ${connectMode === "cloud" ? "active" : ""}`}
            onClick={() => setConnectMode("cloud")}
            disabled={loading}
          >
            <span className="method-title">Cloud API</span>
            <span className="method-subtitle">OAuth tokens</span>
          </button>
        </div>

        {connectMode === "upload" && (
          <>
            <div className="field-group">
              <label htmlFor="alteryx-bulk-files">
                Workflow Packages <span className="required-star">*</span>
              </label>
              <input
                id="alteryx-bulk-files"
                type="file"
                multiple
                accept=".yxmd,.yxmc,.yxwz,.yxzp,.zip"
                onChange={(e) => {
                  setSelectedFiles(e.target.files);
                  setError("");
                }}
                disabled={loading}
              />
              <p className="field-hint">
                Upload individual workflows, packaged workflows, or a bulk zip exported from a repository.
              </p>
            </div>

            {selectedFiles?.length ? (
              <p className="field-hint">{selectedFiles.length} file(s) selected for assessment.</p>
            ) : null}
          </>
        )}

        {connectMode === "cloud" && (
          <>
            <div className="field-group">
          <label htmlFor="alteryx-username">
            Username <span className="optional-label">optional</span>
          </label>
          <input
            id="alteryx-username"
            type="text"
            placeholder="your.name@example.com"
            value={alteryxUsername}
            onChange={(e) => {
              setAlteryxUsername(e.target.value);
              setError("");
            }}
            onBlur={() => setUsernameTouched(true)}
            disabled={loading}
          />
          {usernameTouched && trimmedUsername && !trimmedUsername.includes("@") && (
            <p className="field-error">Username should usually be an email address.</p>
          )}
        </div>

        <div className="field-group">
          <label htmlFor="alteryx-access-token">
            Access Token <span className="required-star">*</span>
          </label>
          <input
            id="alteryx-access-token"
            type="password"
            placeholder="Paste access token from Alteryx One"
            value={alteryxAccessToken}
            onChange={(e) => {
              setAlteryxAccessToken(e.target.value);
              setError("");
            }}
            onBlur={() => setAccessTouched(true)}
            disabled={loading}
          />
          {accessTouched && !trimmedAccessToken && (
            <p className="field-error">Enter the Alteryx access token.</p>
          )}
        </div>

        <div className="field-group">
          <label htmlFor="alteryx-refresh-token">
            Refresh Token <span className="required-star">*</span>
          </label>
          <input
            id="alteryx-refresh-token"
            type="password"
            placeholder="Paste refresh token from Alteryx One"
            value={alteryxRefreshToken}
            onChange={(e) => {
              setAlteryxRefreshToken(e.target.value);
              setError("");
            }}
            onBlur={() => setRefreshTouched(true)}
            disabled={loading}
          />
          {refreshTouched && !trimmedRefreshToken && (
            <p className="field-error">Enter the Alteryx refresh token.</p>
          )}
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
          {workspaceTouched && trimmedWorkspaceName && !isWorkspaceNameValid && (
            <p className="field-error">
              Enter a valid workspace name. The last segment must be exactly 4 alphanumeric characters with both letters and numbers.
            </p>
          )}
        </div>
          </>
        )}

        {error && (
          <div className="error">
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "18px" }}>!</span>
              <span>{error}</span>
            </div>
          </div>
        )}

        <div className="actions">
          <button
            onClick={connectMode === "upload" ? handleBulkUpload : handleConnect}
            disabled={(connectMode === "upload" ? !canUploadPackages : !canConnectAlteryx) || loading}
            style={{
              opacity: (connectMode === "upload" ? canUploadPackages : canConnectAlteryx) ? 1 : 0.5,
              cursor: (connectMode === "upload" ? canUploadPackages : canConnectAlteryx) ? "pointer" : "not-allowed",
            }}
          >
            {loading ? "Working..." : connectMode === "upload" ? "Upload & Assess" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
