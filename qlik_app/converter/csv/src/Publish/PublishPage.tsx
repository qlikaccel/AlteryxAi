import "./PublishPage.css";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { publishAlteryxMQuery } from "../api/alteryxApi";

type PublishTab = "progress" | "details" | "validation";

const PUBLISH_TABS: Array<{ id: PublishTab; label: string }> = [
  { id: "progress", label: "Migration Progress" },
  { id: "details", label: "Publish Details" },
  { id: "validation", label: "Validation" },
];

export default function PublishPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const workflowName = (location.state as any)?.workflowName || sessionStorage.getItem("alteryx_workflow_name") || "Alteryx workflow";
  const datasetName = (location.state as any)?.datasetName || sessionStorage.getItem("migration_dataset_name") || workflowName;
  const mquery = (location.state as any)?.mquery || sessionStorage.getItem("migration_mquery") || "";
  const sharePointUrl = sessionStorage.getItem("alteryx_sharepoint_url") || "";
  const fileName = sessionStorage.getItem("alteryx_file_name") || "sales_data_1M.csv";
  const [activeTab, setActiveTab] = useState<PublishTab>("progress");
  const [publishing, setPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState<any>(() => {
    const raw = sessionStorage.getItem("alteryx_publish_result");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });
  const [publishError, setPublishError] = useState("");

  const publishSummary = useMemo(() => {
    const lineCount = mquery ? mquery.split(/\r?\n/).length : 0;
    return {
      workflowName,
      lineCount,
      status: publishResult?.success ? "Published to Power BI" : mquery ? "Ready to publish" : "Conversion artifact missing",
      percent: publishResult?.success ? 100 : mquery ? 68 : 24,
    };
  }, [mquery, publishResult, workflowName]);

  const progressSteps = useMemo(() => [
    { name: "Validate source", detail: `${fileName} from configured source path`, status: sharePointUrl ? "complete" : "warning" },
    { name: "Parse Alteryx workflow", detail: "Workflow tools and connections loaded", status: "complete" },
    { name: "Generate M Query", detail: `${publishSummary.lineCount} generated line(s)`, status: mquery ? "complete" : "pending" },
    { name: "Publish semantic model", detail: publishResult?.dataset_id || "Awaiting Power BI publish", status: publishResult?.success ? "complete" : publishing ? "running" : "pending" },
    { name: "Reconcile migration", detail: publishResult?.success ? "Dataset id and endpoint returned" : "Runs after publish", status: publishResult?.success ? "complete" : "pending" },
  ], [fileName, mquery, publishResult, publishSummary.lineCount, publishing, sharePointUrl]);

  const validationChecks = useMemo(() => [
    { name: "Power Query artifact generated", status: mquery ? "PASS" : "PENDING", detail: mquery ? `${publishSummary.lineCount} lines ready for publish.` : "Return to Export and generate the artifact." },
    { name: "Source path configured", status: sharePointUrl ? "PASS" : "WARNING", detail: sharePointUrl || "No source path is saved for this workflow." },
    { name: "Power BI publish completed", status: publishResult?.success ? "PASS" : "PENDING", detail: publishResult?.message || "Publish has not completed in this session." },
    { name: "Dataset identifier returned", status: publishResult?.dataset_id ? "PASS" : "PENDING", detail: publishResult?.dataset_id || "Power BI dataset id pending." },
    { name: "API endpoint available", status: publishResult?.api_endpoint ? "PASS" : "PENDING", detail: publishResult?.api_endpoint || "Endpoint will display after publish." },
  ], [mquery, publishResult, publishSummary.lineCount, sharePointUrl]);

  const publishNow = async () => {
    setPublishing(true);
    setPublishError("");
    setActiveTab("progress");
    try {
      const result = await publishAlteryxMQuery({
        dataset_name: datasetName,
        combined_mquery: mquery,
        sharepoint_url: sharePointUrl,
        data_source_path: sharePointUrl,
      });
      setPublishResult(result);
      sessionStorage.setItem("alteryx_publish_result", JSON.stringify(result));
      setActiveTab("details");
    } catch (err: any) {
      setPublishError(err?.message || "Power BI publish failed");
    } finally {
      setPublishing(false);
    }
  };

  const downloadPlan = () => {
    const blob = new Blob([mquery || "No conversion plan generated."], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${workflowName.replace(/[^a-z0-9]+/gi, "_").replace(/^_+|_+$/g, "") || "alteryx_workflow"}_power_query_plan.m`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="publish-wrap alteryx-publish-page">
      <div className="publish-hero">
        <div>
          <p className="eyebrow">Power BI Publish</p>
          <h1>{publishSummary.workflowName}</h1>
          <p>
            Publish the generated Alteryx-to-Power Query artifact to Power BI and review the migration progress,
            endpoint, dataset identifiers, and validation/reconciliation status in one place.
          </p>
        </div>
        <div className="publish-status-card">
          <span>Status</span>
          <strong>{publishSummary.status}</strong>
          <small>{publishSummary.percent}% migration progress</small>
        </div>
      </div>

      <div className="publish-actions">
        <button onClick={() => navigate("/export")}>Back to conversion</button>
        <button onClick={downloadPlan} disabled={!mquery}>Download M Query</button>
        <button onClick={publishNow} disabled={!mquery || publishing}>{publishing ? "Publishing..." : "Publish to Power BI"}</button>
      </div>

      {publishError && <div className="error-card">{publishError}</div>}

      <div className="publish-tab-bar">
        {PUBLISH_TABS.map((tab) => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? "active" : ""}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "progress" && (
        <section className="migration-dashboard">
          <div className="progress-meter">
            <span style={{ width: `${publishSummary.percent}%` }} />
          </div>
          <div className="migration-step-grid">
            {progressSteps.map((step, index) => (
              <article key={step.name} className={`migration-step ${step.status}`}>
                <b>{index + 1}</b>
                <h3>{step.name}</h3>
                <p>{step.detail}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      {activeTab === "details" && (
        <section>
          <div className="publish-result-grid">
            <div><span>API Endpoint</span><strong>{publishResult?.api_endpoint || "Publishes through /api/migration/publish-mquery"}</strong></div>
            <div><span>Dataset ID</span><strong>{publishResult?.dataset_id || "Unavailable until publish succeeds"}</strong></div>
            <div><span>Workspace URL</span><strong>{publishResult?.workspace_url || "Unavailable until publish succeeds"}</strong></div>
            <div><span>Tables Deployed</span><strong>{publishResult?.tables_deployed ?? 0}</strong></div>
            <div><span>Dataset Name</span><strong>{datasetName}</strong></div>
            <div><span>Source File</span><strong>{fileName}</strong></div>
          </div>
          <pre className="publish-preview">{mquery || "No Power Query conversion plan was found. Return to Export and generate the plan again."}</pre>
        </section>
      )}

      {activeTab === "validation" && (
        <section className="publish-validation validation-dashboard">
          <h2>Validation & Reconciliation</h2>
          <div className="validation-grid">
            {validationChecks.map((check) => (
              <article key={check.name} className={`validation-card ${check.status.toLowerCase()}`}>
                <strong>{check.status}</strong>
                <h3>{check.name}</h3>
                <p>{check.detail}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
