// import "./Header.css";
// import { useNavigate } from "react-router-dom";
// import { useState, useEffect } from "react";
// import logoImage from "../../assets/alteryx-logo.png";

// export default function Header() {
//   const navigate = useNavigate();
//   const [userInfo, setUserInfo] = useState<any>(null);

//   useEffect(() => {
//     // Check for user info in session
//     const tenantUrl = sessionStorage.getItem("tenant_url");
//     if (tenantUrl) {
//       setUserInfo({ tenant: tenantUrl });
//     }
//   }, []);

//   const handleLogout = () => {
//     // Clear all session storage
//     sessionStorage.clear();
    
//     // Navigate to connect page
//     navigate("/connect");
//   };

//   return (
//     <header className="header">
//       <div className="header-left">
//         <div className="brand-wordmark">Alteryx</div>

//         <div className="header-text">
//           <p className="logo-subtitle">
//             Alteryx is an AI-powered analytics acceleration platform designed to transform how enterprises consume, understand, and act on enterprise analytics data. By leveraging advanced AI/LLM capabilities, Alteryx automatically summarizes complex dashboards, generates contextual insights, and enables seamless export of analytics into downstream platforms such as Power BI—reducing manual effort and accelerating decision-making.
//           </p>
//         </div>
//       </div>

//       <div className="header-right">
//         <a href="#">Docs</a>
//         <a href="#">Support</a>
//         {userInfo ? (
//           <div className="profile" onClick={handleLogout} style={{ cursor: "pointer" }} title="Click to logout">
//             👤 {userInfo.name || userInfo.email}
//           </div>
//         ) : (
//           <div className="profile">👤</div>
//         )}
//       </div>
//     </header>
//   );
// }





import "./Header.css";
import logoImage from "../../assets/alteryx-logo.png";
import { useNavigate } from "react-router-dom";
import { useState, useEffect } from "react";
 
export default function Header() {
  const navigate = useNavigate();
  const [userInfo, setUserInfo] = useState<any>(null);
 
  useEffect(() => {
    const workspaceName = sessionStorage.getItem("alteryx_workspace_name");
    const username = sessionStorage.getItem("alteryx_username");
    const batchId = sessionStorage.getItem("alteryx_batch_id");
    if (workspaceName || username || batchId) {
      setUserInfo({ name: username || workspaceName || "Bulk upload" });
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
          <img src={logoImage} alt="Alteryx AI logo" className="logo-image" />
        </div>
 
        <div className="header-text">
          <p className="logo-subtitle">
            AlteryxAI is an AI-powered workflow acceleration platform designed to transform how enterprises consume, understand, and modernize analytics processes. By leveraging advanced AI/LLM capabilities, AlteryxAI automatically converts existing workflows into interactive Power BI dashboards, generates contextual insights, and streamlines reporting—reducing manual effort, improving productivity, and accelerating data-driven decision-making.
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
 
 
