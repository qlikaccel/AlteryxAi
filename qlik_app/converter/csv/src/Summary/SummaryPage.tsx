import "./SummaryPage.css";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
import {
  fetchAlteryxBrdHtml,
  fetchAlteryxWorkflowAnalysis,
} from "../api/alteryxApi";
import type { AlteryxWorkflow } from "../api/alteryxApi";
import { useWizard } from "../context/WizardContext";

const DEFAULT_SHAREPOINT_URL = "https://sorimtechnologies.sharepoint.com/Shared%20Documents/Forms/AllItems.aspx";
const DEFAULT_FILE_NAME = "sales_data_1M.csv";

type SummaryTab = "sourceTypes" | "summary" | "brd" | "diagram";

const TABS: Array<{ id: SummaryTab; label: string; icon: string }> = [
  { id: "sourceTypes", label: "Source Types", icon: "folder" },
  { id: "summary", label: "Summary", icon: "chart" },
  { id: "brd", label: "App BRD", icon: "doc" },
  { id: "diagram", label: "ER Diagram", icon: "flow" },
];

function readStoredWorkflow(): AlteryxWorkflow | null {
  const raw = sessionStorage.getItem("alteryx_selected_workflow");
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AlteryxWorkflow;
  } catch {
    return null;
  }
}

function safePercent(value: number, total: number) {
  return total > 0 ? Math.round((value / total) * 100) : 0;
}

function buildPieSlices(workflow: AlteryxWorkflow | null) {
  const counts = new Map<string, number>();
  (workflow?.toolTypes || []).forEach((tool) => {
    const shortName = tool.split(".").filter(Boolean).slice(-1)[0] || tool;
    counts.set(shortName, (counts.get(shortName) || 0) + 1);
  });
  const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 7);
  const unsupported = workflow?.unsupportedToolCount || 0;
  if (unsupported) entries.push(["Needs remediation", unsupported]);
  if (!entries.length) return [["Workflow", 1] as [string, number]];
  return entries;
}

function PieChart({ slices }: { slices: Array<[string, number]> }) {
  const total = slices.reduce((sum, [, value]) => sum + value, 0) || 1;
  let cumulative = 0;
  const colors = ["#ff4d4f", "#fb923c", "#facc15", "#14b8a6", "#6d5dfc", "#db3ea2", "#0ea5e9", "#22c55e"];
  const gradient = slices
    .map(([_, value], index) => {
      const start = (cumulative / total) * 100;
      cumulative += value;
      const end = (cumulative / total) * 100;
      return `${colors[index % colors.length]} ${start}% ${end}%`;
    })
    .join(", ");

  return (
    <div className="alteryx-pie-wrap">
      <div className="alteryx-pie" style={{ background: `conic-gradient(${gradient})` }}>
        <span>{safePercent(slices[0]?.[1] || 0, total)}%</span>
      </div>
      <div className="alteryx-pie-legend">
        {slices.map(([name, value], index) => (
          <div key={name}>
            <i style={{ backgroundColor: colors[index % colors.length] }} />
            <span>{name}: {safePercent(value, total)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function SummaryPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { stopTimer } = useWizard();

  const workflowId = (location.state as any)?.workflowId || sessionStorage.getItem("alteryx_workflow_id") || "";
  const batchId = sessionStorage.getItem("alteryx_batch_id") || "";

  const [workflow, setWorkflow] = useState<AlteryxWorkflow | null>(readStoredWorkflow());
  const [analysis, setAnalysis] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<SummaryTab>("sourceTypes");
  const [sharePointUrl, setSharePointUrl] = useState(DEFAULT_SHAREPOINT_URL);
  const [fileName, setFileName] = useState(DEFAULT_FILE_NAME);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [brdLoading, setBrdLoading] = useState(false);

  useEffect(() => {
    if (!workflowId || !batchId) {
      navigate("/apps");
      return;
    }

    setLoading(true);
    fetchAlteryxWorkflowAnalysis(batchId, workflowId, sharePointUrl, fileName)
      .then((data) => {
        setAnalysis(data);
        setWorkflow(data.workflow);
        sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(data.workflow));
        sessionStorage.setItem("alteryx_sharepoint_url", sharePointUrl);
        sessionStorage.setItem("alteryx_file_name", fileName);
        sessionStorage.setItem("migration_mquery", data.mquery?.combined_mquery || "");
        sessionStorage.setItem("migration_dataset_name", data.mquery?.dataset_name || data.workflow?.name || "AlteryxDataset");
        setError("");
      })
      .catch((err: any) => setError(err?.message || "Failed to load workflow analysis"))
      .finally(() => {
        const elapsed = stopTimer?.("/summary");
        setPageLoadTime(elapsed ?? null);
        setLoading(false);
      });
  }, [batchId, fileName, navigate, sharePointUrl, stopTimer, workflowId]);

  const assessment = useMemo(() => {
    const totalTools = workflow?.toolCount ?? 0;
    const unsupportedTools = workflow?.unsupportedToolCount ?? 0;
    const supportedTools = workflow?.supportedToolCount ?? Math.max(totalTools - unsupportedTools, 0);
    const automationScore = safePercent(supportedTools, totalTools);
    return { totalTools, supportedTools, unsupportedTools, automationScore };
  }, [workflow]);

  const pieSlices = useMemo(() => buildPieSlices(workflow), [workflow]);
  const conversionSteps = analysis?.mquery?.conversion_steps || [];

  const downloadBrd = async () => {
    if (!batchId || !workflowId) return;
    setBrdLoading(true);
    try {
      const html = await fetchAlteryxBrdHtml(batchId, workflowId, sharePointUrl, fileName);
      const blob = new Blob([html], { type: "text/html;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${(workflow?.name || "alteryx_workflow").replace(/[^a-z0-9]+/gi, "_")}_BRD.html`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err?.message || "Failed to generate BRD");
    } finally {
      setBrdLoading(false);
    }
  };

  const continueToExport = () => {
    sessionStorage.setItem("summaryComplete", "true");
    sessionStorage.setItem("summaryActiveTab", "mquery");
    navigate("/export");
  };

  if (loading) {
    return <LoadingOverlay isVisible={loading} message="Generating Alteryx executive summary and migration analysis..." />;
  }

  if (error) {
    return (
      <div className="summary-wrapper">
        <button className="back-btn" onClick={() => navigate("/apps")}>Back to workflows</button>
        <div className="error-card">{error}</div>
      </div>
    );
  }

  if (!workflow) {
    return (
      <div className="summary-wrapper">
        <button className="back-btn" onClick={() => navigate("/apps")}>Back to workflows</button>
        <div className="error-card">No Alteryx workflow is selected.</div>
      </div>
    );
  }

  return (
    <div className="summary-wrapper alteryx-summary-page">
      <div className="alteryx-summary-top">
        <h1>{workflow.name}</h1>
        <div className="summary-tab-bar alteryx-tab-bar">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <span>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>
        <div className="timer-badge">Analysis Time: {pageLoadTime || "00m : 00s : 00ms"}</div>
      </div>

      <div className="source-config alteryx-source-config">
        <label>
          Data Source Path
          <input value={sharePointUrl} onChange={(event) => setSharePointUrl(event.target.value)} />
        </label>
        <label>
          File name
          <input value={fileName} onChange={(event) => setFileName(event.target.value)} />
        </label>
      </div>

      {activeTab === "sourceTypes" && (
        <section className="source-type-grid">
          <article className="source-type-card muted">
            <div className="source-card-head">
              <span className="source-radio" />
              <span className="source-icon">db</span>
              <div><h3>Database</h3><p>Direct ODBC/JDBC connection</p></div>
              <em>Coming Soon</em>
            </div>
            <p>Connect directly to SQL Server, Oracle, Snowflake, or other governed databases. Schema is inferred automatically when credentials are supplied.</p>
            <div className="source-tags"><span>ODBC / JDBC</span><span>Live schema</span><span>SQL sources</span></div>
          </article>

          <article className="source-type-card active-card">
            <div className="source-card-head">
              <span className="source-radio" />
              <span className="source-icon warm">script</span>
              <div><h3>Parse & Convert</h3><p>Alteryx workflow XML to Power Query M</p></div>
            </div>
            <p>Maps 20+ Alteryx tools to M equivalents, including Filter, Join, Summarize, Formula, Select, Union, Sort, Unique, Record ID, APIs, CSV, Excel, and database source patterns.</p>
            <div className="source-tags"><span>M-QUERY</span><span>TOOL MAP</span><span>WORKFLOW GRAPH</span></div>
            <button onClick={() => navigate("/export")}>Go to Scripts</button>
          </article>

          <article className="source-type-card active-card">
            <div className="source-card-head">
              <span className="source-radio" />
              <span className="source-icon tan">csv</span>
              <div><h3>Publish CSV</h3><p>SharePoint CSV to Power BI</p></div>
            </div>
            <p>Uses the supplied SharePoint path for {fileName}, publishes the generated model, and exposes the backend API endpoint used by the migration.</p>
            <div className="source-tags blue"><span>ANY LICENSE</span><span>REST API</span><span>FAST DEPLOY</span></div>
            <button onClick={continueToExport}>Go to Export</button>
          </article>
        </section>
      )}

      {activeTab === "summary" && (
        <section className="summary-report alteryx-executive-grid">
          <PieChart slices={pieSlices} />
          <div className="alteryx-exec-copy">
            <h2>Executive Summary</h2>
            <ul>
              {(analysis?.summary?.bullets || []).map((item: string) => <li key={item}>{item}</li>)}
            </ul>
          </div>
          <div className="metric-grid alteryx-metrics">
            <div className="metric-card"><span>Total Tools</span><strong>{assessment.totalTools}</strong></div>
            <div className="metric-card"><span>Supported Tools</span><strong>{assessment.supportedTools}</strong></div>
            <div className="metric-card"><span>Needs Review</span><strong>{assessment.unsupportedTools}</strong></div>
            <div className="metric-card"><span>Automation Fit</span><strong>{assessment.automationScore}%</strong></div>
          </div>
        </section>
      )}

      {activeTab === "brd" && (
        <section className="assessment-panel alteryx-brd-panel">
          <h2>Workflow BRD</h2>
          <p>
            The BRD is generated for this selected Alteryx workflow, not the legacy Qlik application. It includes
            source inventory, conversion scope, tool mapping, workflow diagram, generated M Query, acceptance
            criteria, and validation/reconciliation requirements.
          </p>
          <div className="mapping-table-wrap">
            <table>
              <thead><tr><th>Alteryx Tool</th><th>Power Query Mapping</th><th>Status</th></tr></thead>
              <tbody>
                {conversionSteps.slice(0, 14).map((step: any) => (
                  <tr key={`${step.node_id}-${step.tool}`}>
                    <td>{step.tool}</td>
                    <td>{step.m_function}</td>
                    <td>{step.mapped ? "Mapped" : "Manual review"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button className="primary-summary-action" onClick={downloadBrd} disabled={brdLoading}>
            {brdLoading ? "Generating BRD..." : "Download Workflow BRD"}
          </button>
        </section>
      )}

      {activeTab === "diagram" && (
        <section className="assessment-panel alteryx-diagram-panel">
          <h2>Workflow Diagram</h2>
          <p>
            An ER diagram is only possible when the workflow contains multiple relational tables and join keys.
            For a local or SharePoint CSV workflow, the accelerator shows the Alteryx workflow graph so reviewers
            can validate transformation lineage before publishing.
          </p>
          <pre className="diagram-preview">{analysis?.diagram?.mermaid}</pre>
          <div className="pill-list">
            {(workflow.workflowEdges || []).slice(0, 12).map((edge: any, index: number) => (
              <span key={`${edge.from}-${edge.to}-${index}`}>Tool {edge.from} to Tool {edge.to}</span>
            ))}
          </div>
        </section>
      )}

      <div className="summary-actions">
        <button onClick={() => navigate("/apps")}>Back to workflows</button>
        <button onClick={downloadBrd} disabled={brdLoading}>{brdLoading ? "Generating BRD..." : "Download BRD"}</button>
        <button onClick={continueToExport}>Continue to Power BI Conversion</button>
      </div>
    </div>
  );
}
