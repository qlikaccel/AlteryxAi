import "./Stepper.css";
import { useNavigate, useLocation } from "react-router-dom";
 
// ✅ ICON IMAGES (ONLY ADDITION)
import connectImg from "../assets/connect3.jpg";
import discoveryImg from "../assets/discovery.png";
import summaryImg from "../assets/summary3.png";
import publishImg from "../assets/Publish.png";

const steps = [
  { id: 1, label: "Connect", sub: "Connect to Alteryx Cloud", icon: connectImg, path: "/" },
  { id: 2, label: "Discovery", sub: "Workflows & Metadata", icon: discoveryImg, path: "/apps" },
  { id: 3, label: "Summary", sub: "Assessment", icon: summaryImg, path: "/summary" },
  { id: 4, label: "Publish", sub: "Publish Results", icon: publishImg, path: "/publish" }
];
 
export default function Stepper() {
  const navigate = useNavigate();
  const location = useLocation();
 
  const getActive = () => {
    const url = location.pathname;
    if (url.includes("/apps")) return 2;
    if (url.includes("/summary")) return 3;
    if (url.includes("/publish")) return 4;
    return 1;
  };
 
  const activeStep = getActive();
 
  const handleNavigate = (path: string) => {
    const connected = sessionStorage.getItem("connected") === "true";
    const appSelected = !!sessionStorage.getItem("appSelected");
    const summaryComplete = sessionStorage.getItem("summaryComplete") === "true";

    if (path === "/") return navigate(path);
    if (path === "/apps" && !connected) return navigate("/");
    if (path === "/summary" && !appSelected) return navigate("/apps");
    if (path === "/publish" && !summaryComplete) return navigate("/summary");

    navigate(path);
  };
 
  const isStepDisabled = (id: number) => {
    const connected = sessionStorage.getItem("connected") === "true";
    const appSelected = !!sessionStorage.getItem("appSelected");
    const summaryComplete = sessionStorage.getItem("summaryComplete") === "true";

    if (id === 1) return false;
    if (id === 2) return !connected;
    if (id === 3) return !appSelected;
    if (id === 4) return !summaryComplete;

    return false;
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
