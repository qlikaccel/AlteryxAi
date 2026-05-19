import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { WizardProvider } from "./context/WizardContext";
// import "./styles/global.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <WizardProvider>
      <App />
    </WizardProvider>
  </BrowserRouter>
);
