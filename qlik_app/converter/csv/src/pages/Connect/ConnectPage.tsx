

import "./ConnectPage.css";
import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { validateLogin } from "../../api/qlikApi";
import { useWizard } from "../../context/WizardContext";

export default function ConnectPage() {
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [connectAsUser, setConnectAsUser] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const navigate = useNavigate();

  // ✅ Restore URL + API Key + checkbox ONLY for current browser session
  useEffect(() => {
    const savedUrl = sessionStorage.getItem("tenant_url");
    const savedApiKey = sessionStorage.getItem("qlik_api_key");
    const savedConnectAsUser = sessionStorage.getItem("connect_as_user");

    if (savedUrl) {
      setUrl(savedUrl);
    }
    
    if (savedApiKey) {
      setApiKey(savedApiKey);
    }

    if (savedConnectAsUser === "true") {
      setConnectAsUser(true);
    }

    setLoading(false);
  }, []);

  const validateUrl = (input: string) => {
    try {
      const parsed = new URL(input);
      return parsed.hostname.endsWith("qlikcloud.com");
    } catch {
      return false;
    }
  };

  const { startTimer } = useWizard();

  const handleConnect = async () => {
    if (!validateUrl(url)) {
      setError(
        "Please enter a valid Qlik Sense Cloud URL (e.g., https://your-tenant.qlikcloud.com)"
      );
      return;
    }

    if (!connectAsUser) {
      setError("Please select 'Connect as test User' to continue.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      await validateLogin(
        url,
        true,
        "ponnuchamy.vellaikannu@sorimtechnologies.com",
        "qlikCloud000"
      );

      // Save for this browser session
      sessionStorage.setItem("tenant_url", url);
      sessionStorage.setItem("connected", "true");
      if (apiKey.trim()) {
        sessionStorage.setItem("qlik_api_key", apiKey.trim());
      }

      // Navigate to apps page
      startTimer?.("/apps");
      navigate("/apps");
    } catch (err: any) {
      setError(
        err.response?.data?.detail ||
          "Connection failed. Please check your credentials and try again."
      );
    } finally {
      setLoading(false);
    }
  };

  const isValidUrl = validateUrl(url);

  return (
    <div className="connect-wrapper">
      <div className="connect-card">
        {/* Logo Section */}
        {/* <div className="logo-section">
          <img
            src={qlikaiLogo}
            alt="QlikAI Logo"
            className="logo-image"
          />
          <h1 className="qlik-title">
            Qlik <span className="AI">AI</span>
            <span className="tagline">– Transform Your QlikSense Data with AI</span>
          </h1>
        </div> */}

        {/* Description */}
        {/* <p className="description">
          QlikAI is an AI-powered analytics acceleration platform designed to transform how enterprises consume, understand, and act on QlikSense data. By leveraging advanced AI/LLM capabilities, QlikAI automatically summarizes complex dashboards, generates contextual insights, and enables seamless export of analytics into downstream platforms such as Power BI—reducing manual effort and accelerating decision-making.
        </p> */}

        <label htmlFor="qlik-url">Enter your QlikSense Cloud URL</label>

        <input
          id="qlik-url"
          type="text"
          placeholder="https://your-tenant.qlikcloud.com"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setError("");
          }}
          className={url && !isValidUrl ? "invalid" : ""}
          disabled={loading}
        />

        {url && !isValidUrl && (
          <p className="error">
            ⚠️ Please enter a valid Qlik Sense Cloud URL ending with
            .qlikcloud.com
          </p>
        )}

        {/* <label htmlFor="qlik-api-key">
          Qlik API Key <span style={{ color: "#999", fontSize: "12px" }}>(Optional - needed for LoadScript)</span>
        </label>

        <input
          id="qlik-api-key"
          type="password"
          placeholder="Paste your Qlik API Key here"
          value={apiKey}
          onChange={(e) => {
            setApiKey(e.target.value);
            setError("");
          }}
          disabled={loading}
          title="Generate from: Qlik Cloud Console → Admin → API Keys"
        /> */}

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
            disabled={!isValidUrl || loading}
            style={{
              opacity: isValidUrl ? 1 : 0.5,
              cursor: isValidUrl ? "pointer" : "not-allowed",
            }}
          >
            {loading ? "Connecting..." : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}
