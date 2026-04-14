

import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useWizard } from "../../context/WizardContext";
import { alteryxLogin } from "../../api/qlikApi";

const DEFAULT_BASE_URL = "https://us1.alteryxcloud.com";

export default function ConnectPage() {
  const [baseUrl, setBaseUrl] = useState("");
  const [connectAsUser, setConnectAsUser] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();

  // ✅ Restore base URL + checkbox ONLY for current browser session
  useEffect(() => {
    const savedUrl = sessionStorage.getItem("alteryx_base_url");
    const savedConnectAsUser = sessionStorage.getItem("connect_as_user");

    if (savedUrl) {
      setBaseUrl(savedUrl);
    } else {
      setBaseUrl(DEFAULT_BASE_URL);
    }

    if (savedConnectAsUser === "true") {
      setConnectAsUser(true);
    }

    setLoading(false);
  }, []);

  const validateUrl = (input: string): boolean => {
    try {
      const parsed = new URL(input);
      return (
        parsed.hostname.endsWith("alteryxcloud.com") ||
        parsed.hostname === "localhost" ||
        parsed.hostname === "127.0.0.1"
      );
    } catch {
      return false;
    }
  };

  const { startTimer } = useWizard();

  const handleConnect = async () => {
    if (!validateUrl(baseUrl)) {
      setError(
        "Please enter a valid Alteryx Cloud URL (e.g., https://us1.alteryxcloud.com)"
      );
      return;
    }

    if (!connectAsUser) {
      setError("Please check 'Connect as test User' to continue.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      // Call backend API to authenticate with Alteryx Cloud
      const response = await alteryxLogin(baseUrl);

      if (response.success) {
        // Save for this browser session
        sessionStorage.setItem("alteryx_base_url", baseUrl);
        sessionStorage.setItem("connected", "true");
        sessionStorage.setItem("alteryx_username", response.username || "");

        // Save checkbox state
        sessionStorage.setItem("connect_as_user", "true");

        // Start timer and navigate to apps
        startTimer?.("/apps");
        navigate("/apps");
      } else {
        setError(response.message || "Authentication failed. Please try again.");
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : "Connection failed";
      setError(errorMessage);
      setLoading(false);
    }
  };

  const isValidUrl = validateUrl(baseUrl);
  const canConnect = isValidUrl && connectAsUser;

  return (
    <div className="connect-wrapper">
      <div className="connect-card">
        <div className="card-header">
          <div className="card-header-text">
            <h1>Connect to Alteryx Cloud</h1>
            <p>
              Enter your Alteryx Cloud base URL and authenticate to begin the
              migration assessment. Using fixed service account credentials.
            </p>
          </div>
        </div>

        <div className="field-group">
          <label htmlFor="alteryx-url">Alteryx Cloud Base URL</label>
          <input
            id="alteryx-url"
            type="text"
            placeholder="https://us1.alteryxcloud.com"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setError("");
            }}
            className={baseUrl && !isValidUrl ? "invalid" : ""}
            disabled={loading}
          />

          {baseUrl && !isValidUrl && (
            <p className="field-error">
              ⚠️ Please enter a valid Alteryx Cloud URL.
            </p>
          )}
        </div>

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

