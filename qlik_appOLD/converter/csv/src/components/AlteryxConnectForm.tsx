// src/components/AlteryxConnectForm.tsx

import { useState } from "react";

interface AlteryxSession {
  workspaceId: string;
  workspaceName: string;
  accessToken: string;
  refreshToken?: string;
}

interface Props {
  onConnected: (session: AlteryxSession) => void;
}

export default function AlteryxConnectForm({ onConnected }: Props) {
  const [accessToken, setAccessToken] = useState("");
  const [refreshToken, setRefreshToken] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  const handleConnect = async () => {
    if (!accessToken.trim() || !workspaceName.trim()) {
      setErrorMsg("Access Token and Workspace Name are required.");
      setStatus("error");
      return;
    }

    setStatus("loading");
    setErrorMsg("");

    try {
      const res = await fetch(
        `${import.meta.env.VITE_API_URL}/api/alteryx/validate-auth`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            access_token: accessToken.trim(),
            refresh_token: refreshToken.trim() || null,
            workspace_name: workspaceName.trim(),
          }),
        }
      );

      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || "Connection failed.");
      }

      setStatus("success");
      onConnected({
        workspaceId: data.workspace_id,
        workspaceName: data.workspace_name,
        accessToken: data.access_token,   // use this going forward (may be refreshed)
        refreshToken: data.refresh_token,
      });
    } catch (err: any) {
      setStatus("error");
      setErrorMsg(err.message);
    }
  };

  return (
    <div className="alteryx-connect-form">
      <h3>Connect to Alteryx One</h3>

      <label>
        Access Token <span className="required">*</span>
        <input
          type="password"
          placeholder="Paste your OAuth2 Access Token"
          value={accessToken}
          onChange={(e) => setAccessToken(e.target.value)}
          disabled={status === "loading" || status === "success"}
        />
        <small>
          Get this from{" "}
          <a
            href="https://us1.alteryxcloud.com"
            target="_blank"
            rel="noreferrer"
          >
            Alteryx One
          </a>{" "}
          → Profile → OAuth 2.0 API Tokens → Generate
        </small>
      </label>

      <label>
        Refresh Token <span className="optional">(optional — enables auto-renewal)</span>
        <input
          type="password"
          placeholder="Paste your Refresh Token"
          value={refreshToken}
          onChange={(e) => setRefreshToken(e.target.value)}
          disabled={status === "loading" || status === "success"}
        />
      </label>

      <label>
        Workspace Name <span className="required">*</span>
        <input
          type="text"
          placeholder="e.g. sorim-alteryx-trial-2hcg"
          value={workspaceName}
          onChange={(e) => setWorkspaceName(e.target.value)}
          disabled={status === "loading" || status === "success"}
        />
        <small>Visible in the top-right corner of Alteryx One</small>
      </label>

      {status !== "success" && (
        <button onClick={handleConnect} disabled={status === "loading"}>
          {status === "loading" ? "Connecting…" : "Validate & Connect"}
        </button>
      )}

      {status === "success" && (
        <div className="success-banner">
          ✅ Connected to <strong>{workspaceName}</strong>
        </div>
      )}

      {status === "error" && (
        <div className="error-banner">⚠️ {errorMsg}</div>
      )}
    </div>
  );
}
