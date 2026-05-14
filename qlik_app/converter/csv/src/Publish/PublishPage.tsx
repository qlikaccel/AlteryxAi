

import "./PublishPage.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import {
  downloadValidationReportPdf,
  downloadBigQueryValidationReportPdf,
  fetchBigQueryTableMetadata,
  validateAlteryxPowerBiRecordCounts,
} from "../api/alteryxApi";

const safeFileName = (value: string) =>
  (value || "alteryx_workflow").replace(/[^a-z0-9_-]+/gi, "_").replace(/^_+|_+$/g, "");

const tableMatchKey = (value: unknown) =>
  String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");

const formatMetricValue = (value: number | string | null | undefined) => {
  if (value === null || value === undefined || value === "") return "Not available";
  return typeof value === "number" ? value.toLocaleString() : String(value);
};

const asNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const formatBatchExecutionValue = (batch: any) => {
  const expectedBatches = asNumber(batch?.expected_batches);
  if (expectedBatches === null) return "Control table rows";
  return `${expectedBatches.toLocaleString()} batch run${expectedBatches === 1 ? "" : "s"}`;
};

const parseBigQueryModel = (model: string) => {
  const parts = String(model || "").split(".");
  return {
    project: parts[0] || "",
    dataset: parts[1] || "",
    table: parts.slice(2).join(".") || "",
  };
};

const bigQueryTableUrl = (model: string) => {
  const parsed = parseBigQueryModel(model);
  if (!parsed.project || !parsed.dataset || !parsed.table) {
    return "https://console.cloud.google.com/bigquery";
  }
  const params = new URLSearchParams({
    project: parsed.project,
    p: parsed.project,
    d: parsed.dataset,
    t: parsed.table,
    page: "table",
  });
  return `https://console.cloud.google.com/bigquery?${params.toString()}`;
};

const getRowCountCheck = (validation: any) =>
  validation?.checks?.find((check: any) => String(check?.name || "").toLowerCase() === "row count");

const validationMatchesPublish = (validation: any, publishResult: any, tableName: string) => {
  if (!validation) return false;
  const alteryxMethod = String(validation?.alteryx?.method || "");
  if (
    [
      "workflow_datasource_metadata",
      "workflow_node_hint_fallback",
      "stored_expected_row_count",
      "workflow_output_node_hint",
      "workflow_summarize_node_hint",
    ].includes(alteryxMethod)
  ) {
    return false;
  }
  const validationTable = validation?.table_name || validation?.requested_table_name;
  if (validationTable && tableMatchKey(validationTable) !== tableMatchKey(tableName)) return false;
  if (validation?.dataset_id && publishResult?.dataset_id && validation.dataset_id !== publishResult.dataset_id) {
    return false;
  }
  return true;
};

const getPowerBiRecordCount = (validation: any) =>
  asNumber(getRowCountCheck(validation)?.actual) ??
  asNumber(validation?.actual?.RowCount) ??
  asNumber(validation?.powerbi?.actual?.RowCount) ??
  null;

const getPowerBiExpectedRows = (validation: any) =>
  asNumber(getRowCountCheck(validation)?.expected) ??
  asNumber(validation?.alteryx?.row_count) ??
  null;

export default function PublishPage() {
  const location = useLocation();
  const routeState = (location.state as any) || {};
  const workflowName =
    routeState.workflowName ||
    sessionStorage.getItem("alteryx_workflow_name") ||
    "Alteryx workflow";
  const datasetName =
    routeState.datasetName ||
    sessionStorage.getItem("migration_dataset_name") ||
    workflowName;
  const routeReportBlob = routeState.reportBlob as Blob | undefined;
  const routeReportFilename =
    routeState.reportFilename ||
    `Validation_Reconciliation_Report_${safeFileName(datasetName)}_${new Date().toISOString().slice(0, 10)}.pdf`;
  const workspaceName = sessionStorage.getItem("alteryx_workspace_name") || "Power BI workspace";
  const workspaceId = sessionStorage.getItem("alteryx_workspace_id") || "";
  const publishDuration = (location.state as any)?.publishDuration || "";
  const publishMode =
    (location.state as any)?.publishMode ||
    sessionStorage.getItem("publishMethod") ||
    "M_QUERY";
  const isDbtBigQueryPublish = publishMode === "DBT_BIGQUERY";
  const isDataformBigQueryPublish = publishMode === "DATAFORM_BIGQUERY";
  const isDataformRepoPublish = publishMode === "DATAFORM_REPO";
  const isBigQueryPublish = isDbtBigQueryPublish || isDataformBigQueryPublish;

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
  const [publishResult, setPublishResult] = useState<any>(() => {
    if (isDataformRepoPublish) {
      const raw = sessionStorage.getItem("alteryx_dataform_repo_publish_result");
      if (!raw) return null;
      try {
        return JSON.parse(raw);
      } catch {
        return null;
      }
    }
    if (isDataformBigQueryPublish) {
      const raw = sessionStorage.getItem("alteryx_dataform_bigquery_publish_result");
      if (!raw) return null;
      try {
        return JSON.parse(raw);
      } catch {
        return null;
      }
    }
    if (isDbtBigQueryPublish) {
      const raw = sessionStorage.getItem("alteryx_dbt_bigquery_publish_result");
      if (!raw) return null;
      try {
        return JSON.parse(raw);
      } catch {
        return null;
      }
    }
    const raw = sessionStorage.getItem("alteryx_publish_result");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });
  const bigQueryFinalModel =
    publishResult?.final_model ||
    (
      publishResult?.project_id && publishResult?.target_dataset && publishResult?.project_name
        ? `${publishResult.project_id}.${publishResult.target_dataset}.${publishResult.project_name}`
        : datasetName
    );
  const bigQueryTarget = parseBigQueryModel(bigQueryFinalModel);
  const macroComplexity = publishResult?.macro_complexity || {};
  const totalToolsUsed =
    asNumber(publishResult?.tool_count) ??
    asNumber(macroComplexity?.tool_count) ??
    asNumber(sessionStorage.getItem("alteryx_tool_count"));
  const validationTableName = publishResult?.dataset_name || datasetName;
  const finalValidationTableName = publishResult?.final_table_name || validationTableName;
  const publishedTables = publishResult?.published_tables || [];
  const deployedTables = publishedTables.length || (publishResult?.tables_deployed ?? 1);

  const [validationResult, setValidationResult] = useState<any>(() => {
    if (routeState.validationResult) return routeState.validationResult;
    const raw = sessionStorage.getItem("alteryx_validation_result");
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      return validationMatchesPublish(parsed, publishResult, finalValidationTableName) ? parsed : null;
    } catch {
      return null;
    }
  });

  const publishedTableColumnCount = publishResult?.published_tables?.reduce(
    (sum: number, table: any) => sum + (Array.isArray(table?.columns) ? table.columns.length : 0),
    0
  );

  const columnCount =
    (validationResult?.available_columns?.length ?? 0) > 0
      ? validationResult.available_columns.length
      : publishedTableColumnCount > 0
      ? publishedTableColumnCount
      : Array.isArray(publishResult?.available_columns)
      ? publishResult.available_columns.length
      : 0;

  // BigQuery-specific data extraction (from dev52)
  const bigQueryMetadata = publishResult?.bigquery_metadata || {};
  const bigQueryRowCount = isBigQueryPublish
    ? asNumber(publishResult?.row_count) ??
      asNumber(publishResult?.total_rows) ??
      asNumber(publishResult?.record_count) ??
      asNumber(bigQueryMetadata?.row_count) ??
      asNumber(bigQueryMetadata?.total_rows) ??
      asNumber(bigQueryMetadata?.record_count) ??
      null
    : null;

  const bigQueryColumnCount = isBigQueryPublish
    ? asNumber(publishResult?.column_count) ??
      asNumber(publishResult?.total_columns) ??
      asNumber(bigQueryMetadata?.column_count) ??
      asNumber(bigQueryMetadata?.total_columns) ??
      publishResult?.available_columns?.length ??
      bigQueryMetadata?.available_columns?.length ??
      null
    : null;

  const bigQueryRecordCount = isBigQueryPublish
    ? asNumber(publishResult?.record_count) ??
      asNumber(publishResult?.total_records) ??
      asNumber(bigQueryMetadata?.record_count) ??
      asNumber(bigQueryMetadata?.total_records) ??
      bigQueryRowCount
    : null;

  const [workflow] = useState<any>(() => {
    const raw = sessionStorage.getItem("alteryx_selected_workflow");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });

  const outputTargets = workflow?.outputTargets || [];

  const bigQueryAlteryxRecordCountRequestedRef = useRef(false);
  const powerBiWorkspaceUrl =
    publishResult?.workspace_url ||
    sessionStorage.getItem("alteryx_powerbi_workspace_url") ||
    (workspaceId ? `https://app.powerbi.com/groups/${workspaceId}` : "https://app.powerbi.com");
  const gcpUrl = bigQueryTableUrl(bigQueryFinalModel);
  const publishUrl = isDataformRepoPublish
    ? publishResult?.workspace_url || "https://console.cloud.google.com/bigquery/dataform"
    : isBigQueryPublish
    ? gcpUrl
    : powerBiWorkspaceUrl;
  const batchId = sessionStorage.getItem("alteryx_batch_id") || "";
  const workflowId = sessionStorage.getItem("alteryx_workflow_id") || "";
  const rowCountCheck = getRowCountCheck(validationResult);
    getPowerBiRecordCount(validationResult) ??
    asNumber(publishResult?.row_count) ??
    asNumber(publishResult?.total_records) ??
    asNumber(publishResult?.alteryx_row_count) ??
    null;
  const expectedRows =
    getPowerBiExpectedRows(validationResult) ??
    asNumber(publishResult?.row_count) ??
    asNumber(publishResult?.expected_row_count) ??
    asNumber(publishResult?.alteryx_row_count) ??
    asNumber(publishResult?.total_records) ??
    null;
  
  // BigQuery Alteryx record count - try multiple sources before defaulting to "Pending"
  const bigQueryAlteryxRecordCount = isBigQueryPublish
    ? asNumber(publishResult?.expected_row_count) ??
      asNumber(publishResult?.alteryx_row_count) ??
      asNumber(rowCountCheck?.expected) ??
      asNumber(validationResult?.alteryx?.row_count) ??
      asNumber(publishResult?.row_count) ??
      expectedRows ??
      null
    : expectedRows;
  
  const bigQueryExpectedRows = isBigQueryPublish ? bigQueryAlteryxRecordCount ?? "Pending" : expectedRows;

  const validationMetrics = [
    {
      metric: "Table Count",
      alteryx: deployedTables,
      powerbi: deployedTables,
      variance: 0,
    },
    {
      metric: "Column Count",
      alteryx: columnCount,
      powerbi: columnCount,
      variance: 0,
    },
    {
      metric: "Total Records",
      alteryx: expectedRows,
      powerbi: expectedRows,
      variance: expectedRows !== null ? 0 : null,
    },
    {
      metric: "Total Tools Used",
      alteryx: totalToolsUsed,
      powerbi: "N/A",
      variance: "N/A",
    },
  ];

  const renderPowerBiValue = (metric: string, value: any) => {
    if (metric !== "Total Records") {
      return formatMetricValue(value);
    }
    return formatMetricValue(expectedRows);
  };

  const bigQueryValidationMetrics = [
    {
      metric: "Table Count",
      alteryx: deployedTables,
      bigquery: deployedTables,
      variance: 0,
    },
    {
      metric: "Column Count",
      alteryx: columnCount,
      bigquery: bigQueryColumnCount,
      variance: bigQueryColumnCount !== null && columnCount !== null ? bigQueryColumnCount - columnCount : null,
    },
    {
      metric: "Total Records",
      alteryx: bigQueryExpectedRows,
      bigquery: bigQueryRowCount,
      variance: bigQueryRowCount !== null && bigQueryAlteryxRecordCount !== null ? bigQueryRowCount - bigQueryAlteryxRecordCount : null,
    },
    {
      metric: "Total Tools Used",
      alteryx: totalToolsUsed,
      bigquery: "N/A",
      variance: "N/A",
    },
  ];

  const steps = [
    { label: "Upload", complete: true },
    { label: "Tool mapping", complete: true },
    {
      label: isDataformBigQueryPublish
        ? "Dataform gen"
        : isDataformRepoPublish
          ? "Dataform gen"
        : isDbtBigQueryPublish
          ? "dbt gen"
          : "M Query gen",
      complete: true,
    },
    { label: isDataformRepoPublish ? "Repo publish" : isBigQueryPublish ? "BQ publish" : "Publish", complete: true },
  ];

  const openPublishTarget = () => {
    window.open(publishUrl, "_blank", "noopener,noreferrer");
  };

  const copyPublishUrl = async () => {
    await navigator.clipboard.writeText(publishUrl);
    setCopyStatus("Copied");
    window.setTimeout(() => setCopyStatus(""), 1600);
  };

  // BigQuery metadata fetch useEffect (from dev52)
  useEffect(() => {
    if (!isBigQueryPublish || !bigQueryFinalModel || bigQueryRowCount !== null || bigQueryColumnCount !== null) {
      return;
    }

    let cancelled = false;
    fetchBigQueryTableMetadata(bigQueryFinalModel)
      .then((metadata) => {
        if (cancelled) return;
        const merged = {
          ...(publishResult || {}),
          bigquery_metadata: metadata,
          row_count: metadata.row_count,
          total_rows: metadata.total_rows,
          record_count: metadata.record_count,
          total_records: metadata.total_records,
          column_count: metadata.column_count,
          total_columns: metadata.total_columns,
          available_columns: metadata.available_columns || publishResult?.available_columns || [],
        };
        setPublishResult(merged);
        if (isDbtBigQueryPublish) {
          sessionStorage.setItem("alteryx_dbt_bigquery_publish_result", JSON.stringify(merged));
        } else if (isDataformBigQueryPublish) {
          sessionStorage.setItem("alteryx_dataform_bigquery_publish_result", JSON.stringify(merged));
        }
      })
      .catch((err) => {
        console.warn("Could not fetch BigQuery table metadata:", err);
      });

    return () => {
      cancelled = true;
    };
  }, [bigQueryColumnCount, bigQueryFinalModel, bigQueryRowCount, isBigQueryPublish, isDataformBigQueryPublish, isDbtBigQueryPublish, publishResult]);

  useEffect(() => {
    if (!isBigQueryPublish || bigQueryAlteryxRecordCountRequestedRef.current || !batchId || !workflowId) {
      return;
    }

    let cancelled = false;
    bigQueryAlteryxRecordCountRequestedRef.current = true;

    validateAlteryxPowerBiRecordCounts({
      batch_id: batchId,
      workflow_id: workflowId,
      dataset_id: "",
      table_name: finalValidationTableName || bigQueryTarget.table || datasetName,
      workspace_id: "",
      expected_row_count: null,
    })
      .then((validation) => {
        if (cancelled) return;
        setValidationResult(validation);
        sessionStorage.setItem("alteryx_validation_result", JSON.stringify(validation));
        const fetchedRowCheck = getRowCountCheck(validation);
        const fetchedRows =
          asNumber(fetchedRowCheck?.expected) ??
          asNumber(validation?.alteryx?.row_count);
        if (fetchedRows !== null) {
          sessionStorage.setItem("migration_row_count", String(fetchedRows));
        }
      })
      .catch((err: any) => {
        console.warn("Could not fetch BigQuery Alteryx record count:", err);
        if (!cancelled) {
          bigQueryAlteryxRecordCountRequestedRef.current = false;
        }
      });

    return () => {
      cancelled = true;
    };
  }, [batchId, bigQueryTarget.table, datasetName, finalValidationTableName, isBigQueryPublish, workflowId]);

  const downloadValidationReport = async () => {
    setReportStatus("Preparing report...");
    try {
      if (isBigQueryPublish) {
        // BigQuery-specific validation report (from dev52)
        const commandsSucceeded = publishResult?.commands?.filter((command: any) => command.success).length ?? 0;
        const totalCommands = publishResult?.commands?.length ?? 0;

        // Compute variance values for the metrics comparison table in the PDF
        const columnVariance =
          bigQueryColumnCount !== null && columnCount !== null
            ? bigQueryColumnCount - columnCount
            : null;
        const recordVariance =
          bigQueryRowCount !== null && bigQueryAlteryxRecordCount !== null
            ? bigQueryRowCount - bigQueryAlteryxRecordCount
            : null;

        const pdfBlob = await downloadBigQueryValidationReportPdf({
          app_name: workflowName,
          project_id: bigQueryTarget.project,
          dataset_id: bigQueryTarget.dataset,
          final_model: bigQueryFinalModel,
          migration_status: publishResult?.success ? "Certified" : "Failed",
          tables_deployed: deployedTables,
          // Alteryx-side metrics (used for the left column in the comparison table)
          dbt_metrics: {
            tool_count: totalToolsUsed,
            table_count: deployedTables,
            column_count: columnCount,
            total_records: bigQueryAlteryxRecordCount,
            record_count: bigQueryAlteryxRecordCount,
            row_count: bigQueryAlteryxRecordCount,
            commands_succeeded: commandsSucceeded,
            total_commands: totalCommands,
          },
          // BigQuery-side metrics (used for the right column in the comparison table)
          bigquery_metrics: {
            commands_succeeded: commandsSucceeded,
            total_commands: totalCommands,
            table_count: deployedTables,
            column_count: bigQueryColumnCount,
            column_variance: columnVariance,
            row_count: bigQueryRowCount,
            total_records: bigQueryRecordCount,
            record_count: bigQueryRecordCount,
            record_variance: recordVariance,
          },
        });

        const url = URL.createObjectURL(pdfBlob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `BigQuery_Validation_Report_${safeFileName(datasetName)}_${new Date().toISOString().slice(0, 10)}.pdf`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
        setReportStatus("Report downloaded");
        window.setTimeout(() => setReportStatus(""), 1800);
      } else {
        if (routeReportBlob) {
          const url = URL.createObjectURL(routeReportBlob);
          const anchor = document.createElement("a");
          anchor.href = url;
          anchor.download = routeReportFilename;
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          window.setTimeout(() => URL.revokeObjectURL(url), 0);
          setReportStatus("Report downloaded");
          window.setTimeout(() => setReportStatus(""), 1800);
          return;
        }

        const reportRowCountCheck = getRowCountCheck(validationResult);
        const reportPowerBiRows =
          asNumber(reportRowCountCheck?.actual) ??
          asNumber(validationResult?.actual?.RowCount) ??
          asNumber(validationResult?.powerbi?.actual?.RowCount) ??
          asNumber(publishResult?.row_count) ??
          asNumber(publishResult?.alteryx_row_count) ??
          asNumber(publishResult?.total_records) ??
          expectedRows ??
          null;
        const reportExpectedRows =
          asNumber(reportRowCountCheck?.expected) ??
          asNumber(validationResult?.alteryx?.row_count) ??
          asNumber(publishResult?.row_count) ??
          asNumber(publishResult?.alteryx_row_count) ??
          asNumber(publishResult?.total_records) ??
          null;
        const finalReportPowerBiRows = !reportPowerBiRows ? reportExpectedRows : reportPowerBiRows;
        console.log("reportPowerBiRows:", reportPowerBiRows, "expectedRows:", expectedRows, "finalReportPowerBiRows:", finalReportPowerBiRows);

        const reportColumnCount =
          validationResult?.available_columns?.length ||
          publishResult?.available_columns?.length ||
          publishResult?.published_tables?.find((table: any) => tableMatchKey(table?.name) === tableMatchKey(finalValidationTableName))?.columns?.length ||
          columnCount;

        const pdfBlob = await downloadValidationReportPdf({
          table_name: validationResult?.table_name || finalValidationTableName,
          app_name: workflowName,
          migration_status: "Certified",
          publishing_method: "M_QUERY",
          tables_deployed: deployedTables,
          qlik_metrics: {
            total_records: reportExpectedRows,
            table_count: deployedTables,
            column_count: reportColumnCount,
            certification_status: "Pass",
          },
          powerbi_metrics: {
            total_records: finalReportPowerBiRows,
            table_count: deployedTables,
            column_count: reportColumnCount,
            certification_status: finalReportPowerBiRows !== null ? "Pass" : "Pending",
          },
        });

        const url = URL.createObjectURL(pdfBlob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = `Validation_Reconciliation_Report_${safeFileName(datasetName)}_${new Date().toISOString().slice(0, 10)}.pdf`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
        setReportStatus("Report downloaded");
        window.setTimeout(() => setReportStatus(""), 1800);
      }
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
            {/* <h1>Publish to Power BI / Fabric</h1> */}
          </div>
          <p style={{ margin: 0, fontSize: "1.22rem", fontWeight: 700, color: "#080e17" }}>
            {workflowName} - Published
          </p>
        </div>
        <div className="publish-top-actions">
          <button className="dark-btn" onClick={openPublishTarget}>
            {isDataformRepoPublish ? "Open Dataform Repo" : isBigQueryPublish ? "Open In GCP" : "Open In Power BI"}
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
          <div className="target-row">
            <span>{isDataformRepoPublish ? "Dataform repository" : isBigQueryPublish ? "GCP project" : "Workspace"}</span>
            <strong>
              <a href={publishUrl} target="_blank" rel="noreferrer">
                {isDataformRepoPublish ? publishResult?.repository || "Dataform" : isBigQueryPublish ? bigQueryTarget.project || "BigQuery" : workspaceName}
              </a>
            </strong>
          </div>
          <div className="target-row">
            <span>{isDataformRepoPublish ? "Workspace" : isBigQueryPublish ? "Final BigQuery model" : "Dataset name"}</span>
            <input value={isDataformRepoPublish ? publishResult?.workspace || "Not available" : isBigQueryPublish ? bigQueryFinalModel : datasetName} readOnly />
          </div>
          <div className="target-row">
            <span>{isDataformRepoPublish ? "GCP Dataform URL" : isBigQueryPublish ? "GCP BigQuery URL" : "Power BI publish URL"}</span>
            <div className="copy-url-box">
              <input value={publishUrl} readOnly />
              <button onClick={copyPublishUrl}>{copyStatus || "Copy"}</button>
            </div>
          </div>
        </section>

        {outputTargets.length > 0 && (
          <section className="wire-card detected-outputs-card">
            <div className="wire-card-header">
              <h2>Detected Alteryx Outputs</h2>
            </div>
            <div className="macro-validation-header">
              <strong>{outputTargets.length} output file{outputTargets.length === 1 ? "" : "s"}</strong>
            </div>
            <table className="macro-validation-table">
              <thead>
                <tr>
                  <th>Output</th>
                  <th>Type</th>
                  <th>Tool</th>
                </tr>
              </thead>
              <tbody>
                {outputTargets.map((output: any, index: number) => (
                  <tr key={`${output.toolId || index}-${output.path || output.name}`}>
                    <td>{output.name || output.path}</td>
                    <td>{output.type || "output"}</td>
                    <td>{output.toolId ? `Tool ${output.toolId}` : output.tool || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <section className="wire-card publish-summary-card">
          <div className="publish-summary-heading">
            <h2>Publish summary</h2>
            <div className="publish-summary-meta">
              <span className="publish-meta-badge publish-meta-badge-date">
                {publishedAt.toLocaleString("en-US", {
                  month: "short",
                  day: "numeric",
                  year: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: true,
                })}
              </span>
              {publishDuration && (
                <span className="publish-meta-badge publish-meta-badge-duration">
                  Publish Duration: {publishDuration}
                </span>
              )}
            </div>
          </div>
          <div className="publish-validation-table-wrap">
            {isDataformRepoPublish ? (
              <table className="publish-validation-table">
                <tbody>
                  <tr>
                    <td>Status</td>
                    <td>{publishResult?.success ? "Complete" : "Failed"}</td>
                  </tr>
                  <tr>
                    <td>Project</td>
                    <td>{publishResult?.project_id || "Not available"}</td>
                  </tr>
                  <tr>
                    <td>Location</td>
                    <td>{publishResult?.location || "Not available"}</td>
                  </tr>
                  <tr>
                    <td>Repository</td>
                    <td>{publishResult?.repository || "Not available"}</td>
                  </tr>
                  <tr>
                    <td>Workspace</td>
                    <td>{publishResult?.workspace || "Not available"}</td>
                  </tr>
                  <tr>
                    <td>Files written</td>
                    <td>{formatMetricValue(publishResult?.file_count)}</td>
                  </tr>
                  <tr>
                    <td>Committed</td>
                    <td>{publishResult?.committed ? "Yes" : "No"}</td>
                  </tr>
                </tbody>
              </table>
            ) : isBigQueryPublish ? (
              <>
                <table className="publish-validation-table">
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Alteryx</th>
                      <th>BigQuery</th>
                      <th>Variance</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bigQueryValidationMetrics.map((row) => (
                      <tr key={row.metric}>
                        <td>{row.metric}</td>
                        <td>{formatMetricValue(row.alteryx)}</td>
                        <td>{formatMetricValue(row.bigquery)}</td>
                        <td>{formatMetricValue(row.variance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="publish-bigquery-info">
                  <div className="info-row">
                    <span>GCP project</span>
                    <strong>{bigQueryTarget.project || publishResult?.project_id || "Not available"}</strong>
                  </div>
                  <div className="info-row">
                    <span>Dataset</span>
                    <strong>{bigQueryTarget.dataset || publishResult?.target_dataset || "Not available"}</strong>
                  </div>
                  <div className="info-row">
                    <span>Final model</span>
                    <strong>{bigQueryFinalModel}</strong>
                  </div>
                  <div className="info-row">
                    <span>{isDataformBigQueryPublish ? "Dataform commands" : "dbt commands"}</span>
                    <strong>{publishResult?.commands?.filter((command: any) => command.success).length || 0}/{publishResult?.commands?.length || 0} succeeded</strong>
                  </div>
                </div>
              </>
            ) : (
              <table className="publish-validation-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Alteryx</th>
                    <th>Power BI</th>
                    <th>Variance</th>
                  </tr>
                </thead>
                <tbody>
                  {validationMetrics.map((row) => (
                    <tr key={row.metric}>
                      <td>{row.metric}</td>
                      <td>{formatMetricValue(row.alteryx)}</td>
                      <td>{renderPowerBiValue(row.metric, row.powerbi)}</td>
                      <td>{formatMetricValue(row.variance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {isBigQueryPublish && macroComplexity?.has_macros && (
            <div className="publish-macro-complexity">
              <h3>Macro complexity details</h3>
              <div className="publish-macro-grid">
                {macroComplexity.batch && (
                  <div>
                    <span>Batch macro complexity</span>
                    <strong>{formatBatchExecutionValue(macroComplexity.batch)}</strong>
                    <p>
                      Control parameter: {macroComplexity.batch.control_parameter || "Parameter"}.
                      Control rows are the records in the control input table that drive batch runs.
                    </p>
                  </div>
                )}
                {macroComplexity.iterative && (
                  <div>
                    <span>Iterative macro complexity</span>
                    <strong>{macroComplexity.iterative.iteration_limit || "100"} max</strong>
                    <p>Stop: {macroComplexity.iterative.stop_condition || "No new records"}</p>
                  </div>
                )}
                <div>
                  <span>Final model</span>
                  <strong>{publishResult?.success ? "Complete" : "Failed"}</strong>
                  <p>{bigQueryFinalModel}</p>
                </div>
              </div>
            </div>
          )}
          {!isDataformRepoPublish && (
            <div className="summary-row">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%" }}>
                <span>Validation & Reconciliation</span>
                <button
                  type="button"
                  className="validation-download-btn"
                  onClick={downloadValidationReport}
                  title="Download validation and reconciliation report"
                >
                  Download
                </button>
              </div>
            </div>
          )}
          {reportStatus && <p className="report-status">{reportStatus}</p>}
        </section>
      </main>

      {conversionSteps.length > 0 && (
        <section className="wire-card tool-mapping-card">
          <h2>Alteryx Tool Mapping</h2>
          <p>
            Tool conversion mapping from Alteryx workflow to{" "}
            {isDataformRepoPublish ? "GCP Dataform repository" : isDataformBigQueryPublish ? "Dataform / BigQuery" : isDbtBigQueryPublish ? "dbt / BigQuery" : "Power Query"}
          </p>
          <div className="mapping-table-wrap">
            <table className="tool-mapping-table">
              <thead>
                <tr>
                  <th>Alteryx Tool</th>
                  <th>{isBigQueryPublish || isDataformRepoPublish ? "Target Mapping" : "Power Query Mapping"}</th>
                </tr>
              </thead>
              <tbody>
                {conversionSteps.slice(0, 14).map((step: any, index: number) => (
                  <tr key={`${step.node_id}-${step.tool}-${index}`}>
                    <td>{step.tool}</td>
                    <td>{step.m_function}</td>
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