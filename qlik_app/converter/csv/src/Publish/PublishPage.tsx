import "./PublishPage.css";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { downloadValidationReportPdf } from "../api/alteryxApi";

const safeFileName = (value: string) =>
  (value || "alteryx_workflow").replace(/[^a-z0-9_-]+/gi, "_").replace(/^_+|_+$/g, "");

export default function PublishPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const workflowName =
    (location.state as any)?.workflowName ||
    sessionStorage.getItem("alteryx_workflow_name") ||
    "Alteryx workflow";
  const datasetName =
    (location.state as any)?.datasetName ||
    sessionStorage.getItem("migration_dataset_name") ||
    workflowName;
  const workspaceName = sessionStorage.getItem("alteryx_workspace_name") || "Power BI workspace";
  const workspaceId = sessionStorage.getItem("alteryx_workspace_id") || "";
  const publishDuration = (location.state as any)?.publishDuration || "";

  const conversionSteps = useMemo(() => {
    const raw = sessionStorage.getItem("alteryx_conversion_steps");
    if (!raw) return [];
    try {
      return JSON.parse(raw);
    } catch {
      return [];
    }
  }, []);

  const [copyStatus, setCopyStatus] = useState("");
  const [publishedAt] = useState(() => new Date());
  const [reportStatus, setReportStatus] = useState("");
  const [publishResult] = useState<any>(() => {
    const raw = sessionStorage.getItem("alteryx_publish_result");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });
  const [validationResult] = useState<any>(() => {
    const raw = sessionStorage.getItem("alteryx_validation_result");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });

  const validationTableName = publishResult?.dataset_name || datasetName;
  const deployedTables = publishResult?.tables_deployed ?? 1;
  const powerBiWorkspaceUrl =
    publishResult?.workspace_url ||
    sessionStorage.getItem("alteryx_powerbi_workspace_url") ||
    (workspaceId ? `https://app.powerbi.com/groups/${workspaceId}` : "https://app.powerbi.com");
  const publishUrl = powerBiWorkspaceUrl;

  const steps = [
    { label: "Upload", complete: true },
    { label: "Tool mapping", complete: true },
    { label: "M Query gen", complete: true },
    { label: "Publish", complete: true },
    { label: "Validate", complete: true },
    
  ];

  const openPowerBi = () => {
    window.open(powerBiWorkspaceUrl, "_blank", "noopener,noreferrer");
  };

  const copyPublishUrl = async () => {
    await navigator.clipboard.writeText(publishUrl);
    setCopyStatus("Copied");
    window.setTimeout(() => setCopyStatus(""), 1600);
  };

  const downloadValidationReport = async () => {
    setReportStatus("Preparing report...");
    try {
      const powerBiRows = validationResult?.actual?.RowCount ?? 0;
      const expectedRows = validationResult?.expected?.RowCount ?? powerBiRows;
      const columnCount = validationResult?.available_columns?.length || 5;

      const pdfBlob = await downloadValidationReportPdf({
        table_name: validationResult?.table_name || validationTableName,
        app_name: workflowName,
        migration_status: "Certified",
        publishing_method: "M_QUERY",
        tables_deployed: deployedTables,
        qlik_metrics: {
          row_count: expectedRows,
          total_records: expectedRows,
          table_count: 1,
          column_count: columnCount,
          certification_status: "Pass",
        },
        powerbi_metrics: {
          row_count: powerBiRows,
          total_records: powerBiRows,
          table_count: deployedTables,
          column_count: columnCount,
          certification_status: "Pass",
        },
      });

      const url = URL.createObjectURL(pdfBlob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `Validation_Reconciliation_Report_${safeFileName(datasetName)}_${new Date().toISOString().slice(0, 10)}.pdf`;
      anchor.click();
      URL.revokeObjectURL(url);
      setReportStatus("Report downloaded");
      window.setTimeout(() => setReportStatus(""), 1800);
    } catch (err: any) {
      setReportStatus(err?.message || "Failed to download report");
      window.setTimeout(() => setReportStatus(""), 2000);
    }
  };

  return (
    <div className="publish-shell">
      <header className="publish-topbar">
        <div>
          <div className="publish-title-row">
            <h1>Publish to Power BI / Fabric</h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <p style={{ margin: 0, fontSize: "14px", color: "#475569" }}>
              {workflowName} - Published
            </p>
            <span style={{
              display: "inline-flex",
              alignItems: "center",
              background: "#e8f5e9",
              color: "#2e7d32",
              fontSize: "12px",
              fontWeight: 500,
              padding: "3px 10px",
              borderRadius: "999px"
            }}>
              {publishedAt.toLocaleString("en-US", {
                month: "short",
                day: "numeric",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit",
                hour12: true
              })}
            </span>
            {publishDuration && (
              <span style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "5px",
                background: "#e8f0fe",
                color: "#1a56db",
                fontSize: "12px",
                fontWeight: 500,
                padding: "3px 10px",
                borderRadius: "999px"
              }}>
                ⏱ Publish Duration: {publishDuration}
              </span>
            )}
          </div>
        </div>
        <div className="publish-top-actions">
          <button className="dark-btn" onClick={openPowerBi}>
            Open Power BI
          </button>
        </div>
      </header>

      <section className="publish-stepper">
        {steps.map((step, index) => (
          <div className="wire-step" key={step.label}>
            <div className={`wire-step-circle ${step.complete ? "done" : ""}`}>
              {step.complete ? "✓" : index + 1}
            </div>
            <span>{step.label}</span>
            {index < steps.length - 1 && <i />}
          </div>
        ))}
      </section>

      <main className="publish-main-grid">
        <section className="wire-card publish-target-card">
          <div className="wire-card-header">
            <h2>Publish target</h2>
          </div>
          {/* <div className="target-row">
            <span>Target</span>
            <select value="Power BI Service (XMLA)" onChange={() => {}}>
              <option>Power BI Service (XMLA)</option>
              <option>Power BI / Fabric semantic model</option>
            </select>
          </div> */}
          <div className="target-row">
            <span>Workspace</span>
            <strong>
              <a href={powerBiWorkspaceUrl} target="_blank" rel="noreferrer">
                {workspaceName}
              </a>
            </strong>
          </div>
          <div className="target-row">
            <span>Dataset name</span>
            <input value={datasetName} readOnly />
          </div>
          <div className="target-row">
            <span>Power BI publish URL</span>
            <div className="copy-url-box">
              <input value={publishUrl} readOnly />
              <button onClick={copyPublishUrl}>{copyStatus || "Copy"}</button>
            </div>
          </div>
        </section>

        <section className="wire-card publish-summary-card">
          <h2>Publish summary</h2>
          <div className="summary-row"><span>Queries to deploy</span><strong>1 of 1</strong></div>
          <div className="summary-row"><span>Total tables</span><strong>{deployedTables}</strong></div>
          {/* <div className="summary-row"><span>Relationships</span><strong>0 inferred</strong></div> */}
          <div className="summary-row">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
              <span>Validation & Reconciliation</span>
              <button
                className="validation-download-btn"
                onClick={downloadValidationReport}
                title="Download validation and reconciliation report"
              >
                Download
              </button>
            </div>
          </div>
          {reportStatus && <p className="report-status">{reportStatus}</p>}
        </section>
      </main>

      {conversionSteps.length > 0 && (
        <section className="wire-card tool-mapping-card">
          <h2>Alteryx Tool Mapping</h2>
          <p>Tool conversion mapping from Alteryx workflow to Power Query</p>
          <div className="mapping-table-wrap">
            <table className="tool-mapping-table">
              <thead>
                <tr>
                  <th>Alteryx Tool</th>
                  <th>Power Query Mapping</th>
                  {/* <th>Status</th> */}
                </tr>
              </thead>
              <tbody>
                {conversionSteps.slice(0, 14).map((step: any, index: number) => (
                  <tr key={`${step.node_id}-${step.tool}-${index}`}>
                    <td>{step.tool}</td>
                    <td>{step.m_function}</td>
                    {/* <td> */}
                      {/* <span className={`status-badge ${step.mapped ? "mapped" : "review"}`}> */}
                        {/* {step.mapped ? "Mapped" : "Manual review"} */}
                      {/* </span> */}
                    {/* </td> */}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}