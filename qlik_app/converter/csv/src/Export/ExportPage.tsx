import "./ExportPage.css";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAlteryxWorkflowMQuery } from "../api/alteryxApi";

export default function ExportPage() {
  const navigate = useNavigate();
  const workflowId = sessionStorage.getItem("alteryx_workflow_id") || "";
  const batchId = sessionStorage.getItem("alteryx_batch_id") || "";
  const workflowName = sessionStorage.getItem("alteryx_workflow_name") || "Alteryx workflow";
  const sharePointUrl = sessionStorage.getItem("alteryx_sharepoint_url") || "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx";
  const fileName = sessionStorage.getItem("alteryx_file_name") || "sales_data_1M.csv";
  const [mquery, setMquery] = useState(sessionStorage.getItem("migration_mquery") || "");
  const [datasetName, setDatasetName] = useState(sessionStorage.getItem("migration_dataset_name") || workflowName);
  const [dataSourcePath, setDataSourcePath] = useState(sessionStorage.getItem("migration_data_source_path") || sharePointUrl);
  const [generationMethod, setGenerationMethod] = useState(sessionStorage.getItem("migration_generation_method") || "rule_based");
  const [generationLabel, setGenerationLabel] = useState(sessionStorage.getItem("migration_generation_label") || "Rule-based mapping");
  const [generationReason, setGenerationReason] = useState(sessionStorage.getItem("migration_generation_reason") || "Low-complexity workflow with supported deterministic tool mappings.");
  const [generationStatus, setGenerationStatus] = useState(sessionStorage.getItem("migration_llm_status") || "not_required");
  const [loading, setLoading] = useState(!mquery);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!batchId || !workflowId) {
      navigate("/apps");
      return;
    }

    fetchAlteryxWorkflowMQuery(batchId, workflowId, sharePointUrl, fileName)
      .then((data) => {
        setMquery(data.combined_mquery || "");
        setDatasetName(data.dataset_name || workflowName);
        setGenerationMethod(data.generation_method || "rule_based");
        setGenerationLabel(data.generation_label || "Rule-based mapping");
        setGenerationReason(data.routing_reason || "Low-complexity workflow with supported deterministic tool mappings.");
        setGenerationStatus(data.llm_status || "not_required");
        setDataSourcePath(data.data_source_path || sharePointUrl);
        sessionStorage.setItem("migration_mquery", data.combined_mquery || "");
        sessionStorage.setItem("migration_dataset_name", data.dataset_name || workflowName);
        sessionStorage.setItem("migration_data_source_path", data.data_source_path || sharePointUrl);
        sessionStorage.setItem("migration_generation_method", data.generation_method || "rule_based");
        sessionStorage.setItem("migration_generation_label", data.generation_label || "Rule-based mapping");
        sessionStorage.setItem("migration_generation_reason", data.routing_reason || "");
        sessionStorage.setItem("migration_llm_status", data.llm_status || "not_required");
      })
      .catch((err: any) => setError(err?.message || "Failed to generate Power Query"))
      .finally(() => setLoading(false));
  }, [batchId, fileName, navigate, sharePointUrl, workflowId, workflowName]);

  const continueToPublish = () => {
    sessionStorage.setItem("exportComplete", "true");
    sessionStorage.setItem("publishMethod", "M_QUERY");
    navigate("/publish", { state: { workflowName, mquery, datasetName } });
  };

  if (loading) {
    return <div className="export-wrap"><p>Generating Power Query from Alteryx workflow...</p></div>;
  }

  if (error) {
    return (
      <div className="export-wrap">
        <p>{error}</p>
        <button onClick={() => navigate("/summary")}>Back to assessment</button>
      </div>
    );
  }

  return (
    <div className="export-wrap">
      <div className="export-header">
        <div>
          
          <h1>{workflowName}</h1>
          <p>
            Generated Power Query uses the SharePoint CSV source <strong>{fileName}</strong>.
          </p>
          <p className="data-source-path">
            Data Source Path: <strong>{dataSourcePath}</strong>
          </p>
          <div className={`export-generation-badge ${generationMethod === "llm" ? "llm" : "rules"}`}>
            <span>{generationLabel}</span>
            <strong>{generationReason}</strong>
            <em>{generationMethod === "llm" ? `LLM status: ${generationStatus}` : "Rule engine used"}</em>
          </div>
        </div>
        {/* <button onClick={() => navigate("/summary")}>Back to assessment</button> */}
      </div>

      <pre className="mquery-preview">{mquery}</pre>

      <div className="export-actions">
        <button onClick={continueToPublish}>Publish to Power BI</button>
      </div>
    </div>
  );
}
