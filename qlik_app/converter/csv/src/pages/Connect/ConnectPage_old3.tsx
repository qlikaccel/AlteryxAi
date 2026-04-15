import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { validateLogin } from "../../api/qlikApi";
import { useWizard } from "../../context/WizardContext";

type Platform = "qlik" | "alteryx";
type QlikMethod = "apikey" | "oauth2" | "cookie";

export default function ConnectPage() {
  // ── Platform toggle ──────────────────────────────────────────────────────
  const [platform, setPlatform] = useState<Platform>("qlik");

  // ── Qlik state ───────────────────────────────────────────────────────────
  const [connectionMethod, setConnectionMethod] = useState<QlikMethod>("oauth2");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [cookie, setCookie] = useState("");
  const [connectAsUser, setConnectAsUser] = useState(false);

  // ── Alteryx state ────────────────────────────────────────────────────────
  const [alteryxToken, setAlteryxToken] = useState("");
  const [alteryxRefreshToken, setAlteryxRefreshToken] = useState("");
  const [alteryxWorkspaceName, setAlteryxWorkspaceName] = useState("");

  // ── Shared state ─────────────────────────────────────────────────────────
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();
  const { startTimer } = useWizard();

  // ── Restore session values ───────────────────────────────────────────────
  useEffect(() => {
    const savedUrl = sessionStorage.getItem("tenant_url");
    const savedApiKey = sessionStorage.getItem("qlik_api_key");
    const savedCookie = sessionStorage.getItem("qlik_session_cookie");
    const savedMethod = sessionStorage.getItem("connection_method") as QlikMethod | null;
    const savedConnectAsUser = sessionStorage.getItem("connect_as_user");
    const savedPlatform = sessionStorage.getItem("platform") as Platform | null;
    const savedAlteryxWorkspace = sessionStorage.getItem("alteryx_workspace_name");

    if (savedUrl) setUrl(savedUrl);
    if (savedApiKey) setApiKey(savedApiKey);
    if (savedCookie) setCookie(savedCookie);
    if (savedMethod) setConnectionMethod(savedMethod);
    if (savedConnectAsUser === "true") setConnectAsUser(true);
    if (savedPlatform) setPlatform(savedPlatform);
    if (savedAlteryxWorkspace) setAlteryxWorkspaceName(savedAlteryxWorkspace);
  }, []);

  // ── Qlik helpers ─────────────────────────────────────────────────────────
  const validateUrl = (input: string) => {
    try {
      const parsed = new URL(input);
      return parsed.hostname.endsWith("qlikcloud.com");
    } catch {
      return false;
    }
  };

  const validateApiKey = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return false;
    const parts = trimmed.split(".");
    const isJwt =
      parts.length === 3 &&
      parts.every((p) => /^[A-Za-z0-9_-]+$/.test(p) && p.length > 0);
    return isJwt || trimmed.length >= 24;
  };

  const isValidUrl = validateUrl(url);

  const canConnectQlik =
    isValidUrl &&
    connectAsUser &&
    (connectionMethod !== "apikey" || validateApiKey(apiKey)) &&
    (connectionMethod !== "cookie" || cookie.trim().length > 0);

  const canConnectAlteryx =
    alteryxToken.trim().length > 0 && alteryxWorkspaceName.trim().length > 0;

  // ── Qlik connect ─────────────────────────────────────────────────────────
  const handleQlikConnect = async () => {
    if (!canConnectQlik) return;
    setLoading(true);
    setError("");
    try {
      await validateLogin(
        url,
        true,
        "ponnuchamy.vellaikannu@sorimtechnologies.com",
        "qlikCloud000"
      );
      sessionStorage.setItem("tenant_url", url);
      sessionStorage.setItem("connected", "true");
      sessionStorage.setItem("connection_method", connectionMethod);
      sessionStorage.setItem("qlik_api_key", apiKey.trim());
      sessionStorage.setItem("platform", "qlik");
      if (cookie.trim()) sessionStorage.setItem("qlik_session_cookie", cookie.trim());

      startTimer?.("/apps");
      navigate("/apps");
    } catch (err: any) {
      setError(
        err?.message ||
        err?.response?.data?.detail ||
        "Connection failed. Please check your credentials and try again."
      );
    } finally {
      setLoading(false);
    }
  };

  // ── Alteryx connect ──────────────────────────────────────────────────────
  const handleAlteryxConnect = async () => {
    if (!canConnectAlteryx) return;
    setLoading(true);
    setError("");
    try {
      const BASE_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const res = await fetch(`${BASE_URL}/api/alteryx/validate-auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          access_token: alteryxToken.trim(),
          refresh_token: alteryxRefreshToken.trim() || null,
          workspace_name: alteryxWorkspaceName.trim(),
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Alteryx connection failed.");

      // Persist resolved values for downstream pages
      sessionStorage.setItem("platform", "alteryx");
      sessionStorage.setItem("alteryx_access_token", data.access_token);
      sessionStorage.setItem("alteryx_workspace_id", data.workspace_id);
      sessionStorage.setItem("alteryx_workspace_name", data.workspace_name);
      if (alteryxRefreshToken.trim()) {
        sessionStorage.setItem("alteryx_refresh_token", alteryxRefreshToken.trim());
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

  const handleConnect = platform === "qlik" ? handleQlikConnect : handleAlteryxConnect;
  const canConnect = platform === "qlik" ? canConnectQlik : canConnectAlteryx;

  return (
    <div className="connect-wrapper">
      <div className="connect-card">

        {/* ── Platform Toggle ── */}
        <div className="platform-toggle">
          <button
            type="button"
            className={`platform-btn ${platform === "qlik" ? "active" : ""}`}
            onClick={() => { setPlatform("qlik"); setError(""); sessionStorage.setItem("platform", "qlik"); }}
            disabled={loading}
          >
            <span className="platform-logo qlik-logo">Q</span>
            <span className="platform-label">Qlik Sense</span>
          </button>
          <button
            type="button"
            className={`platform-btn ${platform === "alteryx" ? "active" : ""}`}
            onClick={() => { setPlatform("alteryx"); setError(""); sessionStorage.setItem("platform", "alteryx"); }}
            disabled={loading}
          >
            <span className="platform-logo alteryx-logo">A</span>
            <span className="platform-label">Alteryx</span>
          </button>
        </div>

        {/* ── Card Header ── */}
        <div className="card-header">
          <div className="card-header-text">
            <h1>
              {platform === "qlik"
                ? "Connect to Qlik Cloud"
                : "Connect to Alteryx One"}
            </h1>
            <p>
              {platform === "qlik"
                ? "Enter your Qlik Cloud tenant URL and authenticate to begin the migration assessment. Your credentials are never stored — OAuth tokens are used for session-only access."
                : "Paste your OAuth2 Access Token from Alteryx One and enter your workspace name to begin workflow discovery."}
            </p>
          </div>
        </div>

        {/* ══ QLIK FIELDS ══════════════════════════════════════════════════ */}
        {platform === "qlik" && (
          <>
            <div className="connection-methods">
              {(["apikey", "oauth2", "cookie"] as QlikMethod[]).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`method-btn ${connectionMethod === m ? "active" : ""}`}
                  onClick={() => { setConnectionMethod(m); setError(""); sessionStorage.setItem("connection_method", m); }}
                  disabled={loading}
                >
                  <span className="method-title">
                    {m === "apikey" ? "API Key / JWT" : m === "oauth2" ? "OAuth 2.0" : "Session Cookie"}
                  </span>
                  <span className="method-subtitle">
                    {m === "apikey" ? "Recommended for CI/CD" : m === "oauth2" ? "Browser-based login" : "Dev / testing only"}
                  </span>
                </button>
              ))}
            </div>

            <div className="field-group">
              <label htmlFor="qlik-url">Tenant URL</label>
              <input
                id="qlik-url"
                type="text"
                placeholder="https://your-tenant.qlikcloud.com"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setError(""); }}
                className={url && !isValidUrl ? "invalid" : ""}
                disabled={loading}
              />
              {url && !isValidUrl && (
                <p className="field-error">⚠️ Please enter a valid Qlik Sense Cloud URL ending with .qlikcloud.com</p>
              )}
            </div>

            {connectionMethod === "apikey" && (
              <div className="field-group">
                <label htmlFor="qlik-api-key">API Key / JWT</label>
                <input
                  id="qlik-api-key"
                  type="password"
                  placeholder="Paste your API Key or JWT"
                  value={apiKey}
                  onChange={(e) => { setApiKey(e.target.value); setError(""); }}
                  disabled={loading}
                />
              </div>
            )}

            {connectionMethod === "cookie" && (
              <div className="field-group">
                <label htmlFor="qlik-cookie">Session Cookie</label>
                <input
                  id="qlik-cookie"
                  type="password"
                  placeholder="Paste your session cookie"
                  value={cookie}
                  onChange={(e) => { setCookie(e.target.value); setError(""); }}
                  disabled={loading}
                />
              </div>
            )}

            <div className="field-group checkbox-group">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={connectAsUser}
                  onChange={(e) => {
                    setConnectAsUser(e.target.checked);
                    setError("");
                    sessionStorage.setItem("connect_as_user", e.target.checked ? "true" : "false");
                  }}
                  disabled={loading}
                />
                <span>Connect as test User</span>
              </label>
            </div>
          </>
        )}

        {/* ══ ALTERYX FIELDS ═══════════════════════════════════════════════ */}
        {platform === "alteryx" && (
          <>
            <div className="field-group">
              <label htmlFor="alteryx-token">
                Access Token <span className="required-star">*</span>
              </label>
              <input
                id="alteryx-token"
                type="password"
                placeholder="Paste your OAuth2 Access Token"
                value={alteryxToken}
                onChange={(e) => { setAlteryxToken(e.target.value); setError(""); }}
                disabled={loading}
              />
              <p className="field-hint">
                Get this from{" "}
                <a href="https://us1.alteryxcloud.com" target="_blank" rel="noreferrer">
                  Alteryx One
                </a>{" "}
                → Profile → OAuth 2.0 API Tokens → Generate
              </p>
            </div>

            <div className="field-group">
              <label htmlFor="alteryx-refresh">
                Refresh Token{" "}
                <span className="optional-label">(optional — enables auto-renewal)</span>
              </label>
              <input
                id="alteryx-refresh"
                type="password"
                placeholder="Paste your Refresh Token"
                value={alteryxRefreshToken}
                onChange={(e) => { setAlteryxRefreshToken(e.target.value); setError(""); }}
                disabled={loading}
              />
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
          </>
        )}

        {/* ── Error ── */}
        {error && (
          <div className="error">
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "18px" }}>⚠️</span>
              <span>{error}</span>
            </div>
          </div>
        )}

        {/* ── CTA ── */}
        <div className="actions">
          <button
            onClick={handleConnect}
            disabled={!canConnect || loading}
            style={{ opacity: canConnect ? 1 : 0.5, cursor: canConnect ? "pointer" : "not-allowed" }}
          >
            {loading
              ? "Connecting..."
              : platform === "qlik"
              ? "Connect"
              : "Validate & Connect"}
          </button>
        </div>

      </div>
    </div>
  );
}
