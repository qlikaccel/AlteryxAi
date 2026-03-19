import { Routes, Route } from "react-router-dom";
import ConnectPage from "../pages/Connect/ConnectPage";
import AppsPage from "../Apps/AppsPage";
import SummaryPage from "../Summary/SummaryPage";
import MultiMigrationPage from "../MultiMigration/MultiMigrationPage";
import ExportPage from "../Export/ExportPage";
import PublishPage from "../Publish/PublishPage";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<ConnectPage />} />
      <Route path="/connect" element={<ConnectPage />} />
      <Route path="/apps" element={<AppsPage />} />
      <Route path="/summary" element={<SummaryPage />} />
      <Route path="/export" element={<ExportPage />} />
      <Route path="/multi-migrate" element={<MultiMigrationPage />} />
      <Route path="/publish" element={<PublishPage />} />
    </Routes>
  );
}


