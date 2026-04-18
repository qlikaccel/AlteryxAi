// import "./Stepper.css";
// import { useNavigate, useLocation } from "react-router-dom";

// const steps = [
//   { id: 1, label: "Connect", sub: "Connect to Qlik Cloud", icon: "🔗", path: "/" },

//   { id: 2, label: "Discovery", sub: "Apps & Metadata", icon: "🔍", path: "/apps" },

//   { id: 3, label: "Summary", sub: "Assessment", icon: "📋", path: "/summary" },

//   { id: 4, label: "Export", sub: "Build & Convert", icon: "⚡", path: "/export" },

//   { id: 5, label: "Migration", sub: "Migration Results", icon: "📊", path: "/migration" }
// ];

// export default function Stepper() {
//   const navigate = useNavigate();
//   const location = useLocation();

//   const getActive = () => {
//     const url = location.pathname;

//     if (url.includes("/apps")) return 2;
//     if (url.includes("/summary")) return 3;
//     if (url.includes("/export")) return 4;
//     if (url.includes("/migration")) return 5;

//     return 1;
//   };

//   const activeStep = getActive();

//   // 🔥 MAIN LOGIC HERE
//   const handleNavigate = (path: string) => {
//     const connected = sessionStorage.getItem("connected") === "true";
//     const appSelected = !!sessionStorage.getItem("appSelected");
//     const summaryComplete = sessionStorage.getItem("summaryComplete") === "true";
//     const exportComplete = sessionStorage.getItem("exportComplete") === "true";

//     // CONNECT always allowed
//     if (path === "/") {
//       navigate(path);
//       return;
//     }

//     // APPS requires connected
//     if (path === "/apps" && !connected) {
//       navigate("/");
//       return;
//     }

//     // SUMMARY requires app selected
//     if (path === "/summary" && !appSelected) {
//       navigate("/apps");
//       return;
//     }

//     // EXPORT requires summary completed
//     if (path === "/export" && !summaryComplete) {
//       // guide the user to summary first
//       navigate("/summary");
//       return;
//     }

//     // MIGRATION requires export complete
//     if (path === "/migration" && !exportComplete) {
//       navigate("/export");
//       return;
//     }

//     // otherwise navigate
//     navigate(path);
//   };

//   const isStepDisabled = (id: number) => {
//     const connected = sessionStorage.getItem("connected") === "true";
//     const appSelected = !!sessionStorage.getItem("appSelected");
//     const summaryComplete = sessionStorage.getItem("summaryComplete") === "true";
//     const exportComplete = sessionStorage.getItem("exportComplete") === "true";

//     if (id === 1) return false;
//     if (id === 2) return !connected;
//     if (id === 3) return !appSelected;
//     if (id === 4) return !summaryComplete;
//     if (id === 5) return !exportComplete;

//     return false;
//   };

//   return (
//     <div className="stepper">
//       {steps.map((step) => {
//         const disabled = isStepDisabled(step.id);
//         return (
//           <div
//             key={step.id}
//             className={`step ${disabled ? "disabled" : ""}`}
//             onClick={() => !disabled && handleNavigate(step.path)}
//             title={disabled ? "Complete previous steps first" : step.sub}
//             style={{ opacity: disabled ? 0.6 : 1, cursor: disabled ? "not-allowed" : "pointer" }}
//           >
//             <div className={`circle ${activeStep === step.id ? "active" : ""}`}>
//               {step.icon}
//             </div>

//             <div className="step-text">
//               <div className="title">{step.label}</div>
//               <div className="sub">{step.sub}</div>
//             </div>
//           </div>
//         );
//       })}
//       {/* <div style={{ marginLeft: 16, marginTop: 6, fontSize: 12, color: '#0a66c2' }}>
//         {lastElapsed ? `Last nav: ${lastElapsed}` : "No recent timing"}
//       </div> */}
//     </div>
//   );
// }


import "./Stepper.css";
import { useNavigate, useLocation } from "react-router-dom";

// ✅ ICON IMAGES
import connectImg from "../assets/connect3.jpg";
import discoveryImg from "../assets/discovery.png";
import summaryImg from "../assets/summary3.png";
import exportImg from "../assets/export.png";
import publishImg from "../assets/Publish.png";

const steps = [
  { id: 1, label: "Connect", sub: "Connect to Alteryx Cloud", icon: connectImg, path: "/" },
  { id: 2, label: "Discovery", sub: "Workflows & Metadata", icon: discoveryImg, path: "/apps" },
  { id: 3, label: "Summary", sub: "Assessment", icon: summaryImg, path: "/summary" },
  { id: 4, label: "Export", sub: "Build & Convert", icon: exportImg, path: "/export" },
  { id: 5, label: "Publish", sub: "Publish Results", icon: publishImg, path: "/publish" }
];

export default function Stepper() {
  const navigate = useNavigate();
  const location = useLocation();

  const getActive = () => {
    const url = location.pathname;
    if (url.includes("/apps")) return 2;
    if (url.includes("/summary")) return 3;
    if (url.includes("/export")) return 4;
    if (url.includes("/publish")) return 5;
    return 1;
  };

  const activeStep = getActive();

  const handleNavigate = (path: string) => {
    const connected = sessionStorage.getItem("connected") === "true";

    if (path === "/") return navigate(path);
    if (path === "/apps" && !connected) return navigate("/");

    navigate(path);
  };

  // Progressive step unlocking: Steps 1 through activeStep are clickable
  // All steps beyond activeStep are disabled until the user reaches them
  const isStepDisabled = (id: number) => {
    return id > activeStep;
  };

  return (
    <div className="stepper">
      {steps.map((step) => {
        const disabled = isStepDisabled(step.id);

        return (
          <div
            key={step.id}
            className={`step ${disabled ? "disabled" : ""} ${activeStep === step.id ? "active-step" : ""}`}
            onClick={() => !disabled && handleNavigate(step.path)}
            title={disabled ? "Complete previous steps first" : step.sub}
            style={{
              opacity: disabled ? 0.6 : 1,
              cursor: disabled ? "not-allowed" : "pointer"
            }}
          >
            <div className={`circle ${activeStep === step.id ? "active" : ""}`}>
              <img src={step.icon} alt={step.label} />
            </div>
 
            <div className="step-text">
              <div className="title">{step.label}</div>
              <div className="sub">{step.sub}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
}