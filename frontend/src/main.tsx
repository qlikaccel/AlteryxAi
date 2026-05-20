import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { WizardProvider } from "./context/WizardContext";
// import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <HashRouter>
    <WizardProvider>
      <App />
    </WizardProvider>
  </HashRouter>
);
