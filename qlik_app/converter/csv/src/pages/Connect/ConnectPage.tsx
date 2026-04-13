

import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";

const DEFAULT_TENANT_URL =
  "https://us1.alteryxcloud.com/cloud-portal/?workspaceGid=01KNS7RVQS14ZM22MY51EZRGKJ";
const OLD_TENANT_URL = "https://vtcej92i1jgxph5.in.qlikcloud.com/";

export default function ConnectPage() {
  const [connectionMethod, setConnectionMethod] = useState<
    "apikey" | "oauth2" | "cookie"
  >("oauth2");
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [cookie, setCookie] = useState("");
  const [connectAsUser, setConnectAsUser] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();

  // ✅ Restore URL + API Key + checkbox ONLY for current browser session
  useEffect(() => {
    const savedUrl = sessionStorage.getItem("tenant_url");
    const savedApiKey = sessionStorage.getItem("qlik_api_key");
    const savedCookie = sessionStorage.getItem("qlik_session_cookie");
    const savedMethod = sessionStorage.getItem("connection_method");
    const savedConnectAsUser = sessionStorage.getItem("connect_as_user");

    if (savedUrl) {
      setUrl(savedUrl === OLD_TENANT_URL ? DEFAULT_TENANT_URL : savedUrl);
    } else {
      setUrl(DEFAULT_TENANT_URL);
    }

    if (savedApiKey) {
      setApiKey(savedApiKey);
    }

    if (savedCookie) {
      setCookie(savedCookie);
    }

    if (savedMethod === "apikey" || savedMethod === "oauth2" || savedMethod === "cookie") {
      setConnectionMethod(savedMethod);
    }

    if (savedConnectAsUser === "true") {
      setConnectAsUser(true);
    }

    setLoading(false);
  }, []);

  const normalizeTenantUrl = (input: string) => {
    try {
      const parsed = new URL(input.trim());
      return parsed.origin;
    } catch {
      return "";
    }
  };

  const validateUrl = (input: string) => {
    try {
      const parsed = new URL(input);
      return (
        parsed.hostname.endsWith("alteryxcloud.com") ||
        parsed.hostname.endsWith("qlikcloud.com")
      );
    } catch {
      return false;
    }
  };

  const validateApiKey = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) return false;

    // Treat as JWT if it has 3 dot-separated parts with valid Base64URL chars.
    const parts = trimmed.split(".");
    const isJwt =
      parts.length === 3 &&
      parts.every((p) => /^[A-Za-z0-9_-]+$/.test(p) && p.length > 0);

    if (isJwt) return true;

    // Otherwise, require a reasonable length for an API key (e.g., 24+ chars)
    return trimmed.length >= 24;
  };

  const { startTimer } = useWizard();

  const handleConnect = async () => {
    const normalizedUrl = normalizeTenantUrl(url);

    if (!validateUrl(url)) {
      setError(
        "Please enter a valid Alteryx Cloud URL (e.g., https://us1.alteryxcloud.com/cloud-portal/?workspaceGid=...)"
      );
      return;
    }

    if (!connectAsUser) {
      setError("Please select 'Connect as test User' to continue.");
      return;
    }

    if (connectionMethod === "apikey") {
      if (!apiKey.trim()) {
        setError("Please enter your API Key / JWT to continue.");
        return;
      }
      if (!validateApiKey(apiKey)) {
        setError("Please enter a valid API Key / JWT.");
        return;
      }
    }

    if (connectionMethod === "cookie" && !cookie.trim()) {
      setError("Please enter your session cookie to continue.");
      return;
    }

    setLoading(true);
    setError("");

    // Save for this browser session and proceed directly to Alteryx discovery.
    sessionStorage.setItem("tenant_url", normalizedUrl);
    sessionStorage.setItem("connected", "true");
    sessionStorage.setItem("connection_method", connectionMethod);
    sessionStorage.setItem("qlik_api_key", apiKey.trim());

    if (cookie.trim()) {
      sessionStorage.setItem("qlik_session_cookie", cookie.trim());
    }

    startTimer?.("/apps");
    navigate("/apps");
    setLoading(false);
  };

  const isValidUrl = validateUrl(url);
  const canConnect =
    isValidUrl &&
    connectAsUser &&
    (connectionMethod !== "apikey" || validateApiKey(apiKey)) &&
    (connectionMethod !== "cookie" || cookie.trim().length > 0);

  return (
    <div className="connect-wrapper">
      <div className="connect-card">
        <div className="card-header">
          <div className="card-header-text">
            <h1>Connect to Alteryx Cloud</h1>
            <p>
              Enter your Alteryx Cloud tenant URL and authenticate to begin the
              migration assessment. Your credentials are never stored — OAuth tokens
              are used for session-only access.
            </p>
          </div>
        </div>

        <div className="connection-methods">
          <button
            type="button"
            className={`method-btn ${connectionMethod === "apikey" ? "active" : ""}`}
            onClick={() => {
              setConnectionMethod("apikey");
              setError("");
              sessionStorage.setItem("connection_method", "apikey");
            }}
            disabled={loading}
          >
            <span className="method-title">API Key / JWT</span>
            <span className="method-subtitle">Recommended for CI/CD</span>
          </button>
          <button
            type="button"
            className={`method-btn ${connectionMethod === "oauth2" ? "active" : ""}`}
            onClick={() => {
              setConnectionMethod("oauth2");
              setError("");
              sessionStorage.setItem("connection_method", "oauth2");
            }}
            disabled={loading}
          >
            <span className="method-title">OAuth 2.0</span>
            <span className="method-subtitle">Browser-based login</span>
          </button>
          <button
            type="button"
            className={`method-btn ${connectionMethod === "cookie" ? "active" : ""}`}
            onClick={() => {
              setConnectionMethod("cookie");
              setError("");
              sessionStorage.setItem("connection_method", "cookie");
            }}
            disabled={loading}
          >
            <span className="method-title">Session Cookie</span>
            <span className="method-subtitle">Dev / testing only</span>
          </button>
        </div>

        <div className="field-group">
          <label htmlFor="qlik-url">Tenant URL</label>
          <input
            id="qlik-url"
            type="text"
            placeholder="https://us1.alteryxcloud.com/cloud-portal/?workspaceGid=..."
            value={url}
            onChange={(e) => {
              setUrl(e.target.value);
              setError("");
            }}
            className={url && !isValidUrl ? "invalid" : ""}
            disabled={loading}
          />

          {url && !isValidUrl && (
            <p className="field-error">
              ⚠️ Please enter a valid Alteryx Cloud URL.
            </p>
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
              onChange={(e) => {
                setApiKey(e.target.value);
                setError("");
              }}
              disabled={loading}
              title="Generate from: Alteryx Cloud Console → Admin → API Keys"
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
              onChange={(e) => {
                setCookie(e.target.value);
                setError("");
              }}
              disabled={loading}
              title="Use the session cookie from your browser session"
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

                // 🔁 Keep checkbox state in session
                sessionStorage.setItem(
                  "connect_as_user",
                  e.target.checked ? "true" : "false"
                );
              }}
              disabled={loading}
            />
            <span>Connect as test User</span>
          </label>
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
            disabled={!canConnect || loading}
            style={{
              opacity: canConnect ? 1 : 0.5,
              cursor: canConnect ? "pointer" : "not-allowed",
            }}
          >
            {loading ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
