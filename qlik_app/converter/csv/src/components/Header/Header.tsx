import "./Header.css";
import qlikaiLogo from "../../assets/qlikai.png";
import { useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";

export default function Header() {
  const navigate = useNavigate();
  const [userInfo, setUserInfo] = useState<any>(null);

  useEffect(() => {
    // Check for user info in session
    const tenantUrl = sessionStorage.getItem("tenant_url");
    if (tenantUrl) {
      setUserInfo({ tenant: tenantUrl });
    }
  }, []);

  const handleLogout = () => {
    // Clear all session storage
    sessionStorage.clear();
    
    // Navigate to connect page
    navigate("/connect");
  };

  return (
    <header className="header">
      <div className="header-left">
        <div className="logo-section">
          <img
            src={qlikaiLogo}
            alt="QlikAI Logo"
            className="logo-image"
          />
        </div>

        <div className="header-text">
          <p className="logo-subtitle">
            QlikAI is an AI-powered analytics acceleration platform designed to transform how enterprises consume, understand, and act on QlikSense data. By leveraging advanced AI/LLM capabilities, QlikAI automatically summarizes complex dashboards, generates contextual insights, and enables seamless export of analytics into downstream platforms such as Power BI—reducing manual effort and accelerating decision-making.
          </p>
        </div>
      </div>

      <div className="header-right">
        <a href="#">Docs</a>
        <a href="#">Support</a>
        {userInfo ? (
          <div className="profile" onClick={handleLogout} style={{ cursor: "pointer" }} title="Click to logout">
            👤 {userInfo.name || userInfo.email}
          </div>
        ) : (
          <div className="profile">👤</div>
        )}
      </div>
    </header>
  );
}
