import { Routes, Route } from "react-router-dom";
import ConnectPage from "../pages/Connect/ConnectPage";
import AppsPage from "../Apps/AppsPage";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<ConnectPage />} />
      <Route path="/connect" element={<ConnectPage />} />
      <Route path="/apps" element={<AppsPage />} />
    </Routes>
  );
}


