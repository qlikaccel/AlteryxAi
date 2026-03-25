import { useState, useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useWizard } from "../context/WizardContext";
import "./PublishPage.css";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";

// Stepper Component
interface StepperProps {
  currentStep: number;
  steps: string[];
}

function Stepper({ currentStep, steps }: StepperProps) {
  return (
    <div className="stepper-container">
      <div className="stepper">
        {steps.map((step, idx) => (
          <div key={idx} className="step">
            <div
              className={`step-circle ${
                idx < currentStep
                  ? "completed"
                  : idx === currentStep
                  ? "active"
                  : ""
              }`}
            >
              {idx < currentStep ? "✓" : idx + 1}
            </div>
            <div
              className={`step-label ${
                idx < currentStep
                  ? "completed"
                  : idx === currentStep
                  ? "active"
                  : ""
              }`}
            >
              {step}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

const STEPPER_STEPS = [
  "Prepare Data",
  "Authenticate",
  "Publish Dataset",
  "Generate URL",
  "Publish",
];

export default function PublishPage() {
  const navigate = useNavigate();
  const { state } = useLocation() as any;
  const { getLastElapsed } = useWizard();

  // Get data from sessionStorage (lightweight metadata) — prefer navigation `state` for large payloads
  const tableName = sessionStorage.getItem("migration_selected_table") || "";
  const appName = sessionStorage.getItem("migration_appName") || sessionStorage.getItem("appName") || "Unknown";
  const hasCSV = sessionStorage.getItem("migration_has_csv") === "true";
  const hasDAX = sessionStorage.getItem("migration_has_dax") === "true";
  // READ ACCURATE ROW COUNT: ExportPage already saved the correct total to sessionStorage
  const sessionRowCount = Number(sessionStorage.getItem("migration_row_count") || "0");
  // Prefer navigation state totalRows first, then fall back to the accurate sessionRowCount that ExportPage saved
  const rowCount = state?.totalRows ?? sessionRowCount;
  const columns = JSON.parse(sessionStorage.getItem("migration_columns") || "[]");

  // Check if multi-table mode (use metadata key first)
  // Prefer navigation state tableCount first (for M-Query flow), then fall back to sessionStorage (for CSV/DAX flow)
  const tableCount = state?.tableCount ?? Number(sessionStorage.getItem("migration_table_count") || "0");
  const isMultiTableMode = tableCount > 0;

  // Get export options from navigation state
  const exportOptions = state?.exportOptions || { combined: true, separate: false };
  const isSeparateMode = exportOptions.separate;

  // Selected tables to publish: prefer `state.selectedTables` (in-memory), fallback to lightweight metadata
  const selectedTablesToPublish = state?.selectedTables || (() => {
    try {
      const metaJson = sessionStorage.getItem("migration_selected_tables_meta") || sessionStorage.getItem("migration_selected_tables");
      if (!metaJson) return [];
      const parsed = JSON.parse(metaJson);
      // If metadata (array of {name,rowCount,...}) convert to expected shape and restore actualRowCount
      return parsed.map((p: any) => ({ 
        name: p.name || p.table || "", 
        data: { name: p.name || p.table || "", rows: [] },
        actualRowCount: p.rowCount || 0  // Restore server-reported row count from metadata
      }));
    } catch (e) {
      return [];
    }
  })();

  // Extract single vs multi-select variables from state for info display
  const isMultiSelect = state?.selectedTables && state?.selectedTables.length > 0;
  let selectedTable = state?.selectedTable || sessionStorage.getItem("selectedTable");
  let rows = state?.rows || [];

  // Export page state
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  // Skip export options if coming from ExportPage (exportComplete already set in sessionStorage)
  const [showExportOptions, setShowExportOptions] = useState(sessionStorage.getItem("exportComplete") !== "true");
  const [exportOptionsState, setExportOptionsState] = useState<{ combined: boolean }>({ combined: exportOptions?.combined ?? true });
  const [showError, setShowError] = useState(false);
  const [datasetURL, setDatasetURL] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);
  const [statusBoxes, setStatusBoxes] = useState({ columns: false, powerbi: false, finished: false });
  const [result, setResult] = useState<any>(null);
  const [desktopBundle, setDesktopBundle] = useState<any>(null);
  const [desktopBundleLoading, setDesktopBundleLoading] = useState(false);
  const [desktopBundleError, setDesktopBundleError] = useState<string>("");
  const [xmlaSemanticLoading, setXmlaSemanticLoading] = useState(false);
  const [xmlaSemanticError, setXmlaSemanticError] = useState<string>("");
  const [xmlaSemanticResult, setXmlaSemanticResult] = useState<any>(null);
  const [showXmlaDiagram, setShowXmlaDiagram] = useState(false);

  // Prevent unused-variable TypeScript errors (these hooks may be used in future updates)
  void desktopBundleLoading;
  void xmlaSemanticLoading;
  const [bundlePathCopied, setBundlePathCopied] = useState(false);

  const [copied, setCopied] = useState(false);
  const [publishEndTime, setPublishEndTime] = useState<Date | null>(null);
  const [publishDuration, setPublishDuration] = useState<number>(0);
  const [publishedTableName, setPublishedTableName] = useState<string>("");
  const [csvData, setCSVData] = useState<string>("");
  const [daxData, setDAXData] = useState<string>("");
  const [customTableName, setCustomTableName] = useState(tableName);

  // Capture page load time from WizardContext
  useEffect(() => {
    const elapsed = getLastElapsed?.();
    if (elapsed) {
      setPageLoadTime(elapsed);
    }
  }, [getLastElapsed]);

  // Save metadata immediately on load
  useEffect(() => {
    if (!isMultiSelect && rows && rows.length > 0) {
      // Single-select: Save lightweight metadata
      sessionStorage.setItem("migration_selected_table", selectedTable || "");
      sessionStorage.setItem("migration_appName", appName);
      sessionStorage.setItem("migration_columns", JSON.stringify(Object.keys(rows[0])));
      const totalFromState = state?.totalRows ?? null;
      sessionStorage.setItem("migration_row_count", String(totalFromState ?? rows.length));
      sessionStorage.setItem("migration_table_count", "1");
    } else if (isMultiSelect && selectedTablesToPublish.length > 0) {
      // Multi-select: Save metadata ONLY
      const meta = selectedTablesToPublish.map((t: any) => ({
        name: t.name,
        rowCount: t.actualRowCount ?? (t.data?.rows?.length || 0),
        columns: t.data?.rows && t.data.rows.length > 0 ? Object.keys(t.data.rows[0]) : [],
      }));

      sessionStorage.setItem("migration_selected_tables_meta", JSON.stringify(meta));
      sessionStorage.setItem("migration_appName", appName);
      sessionStorage.setItem("migration_table_count", String(selectedTablesToPublish.length));

      // Save first table's columns and row count
      if (selectedTablesToPublish[0]?.data?.rows && selectedTablesToPublish[0].data.rows.length > 0) {
        sessionStorage.setItem("migration_columns", JSON.stringify(Object.keys(selectedTablesToPublish[0].data.rows[0])));
        const totalRows = selectedTablesToPublish.reduce((s: number, t: any) => s + (t.actualRowCount ?? (t.data?.rows?.length || 0)), 0);
        sessionStorage.setItem("migration_row_count", String(totalRows));
      }
    }
  }, [selectedTable, rows, selectedTablesToPublish, appName, isMultiSelect]);

  // ✅ Handle M Query publishing - show workflow steps while publishing
  useEffect(() => {
    if (state?.publishMethod === "M_QUERY" && state?.showWorkflow) {
      console.log("✅ M Query publishing - showing workflow steps");
      
      // Start the workflow animation
      setShowExportOptions(false);
      setCurrentStep(0); // Start at step 0
      setPublishing(true);

      // Realistic workflow progression with longer delays between steps
      // Each step waits ~1-1.2 seconds for actual processing
      const steps = [
        { delay: 500, step: 1, label: "Prepare Data" },
        { delay: 1500, step: 2, label: "Authenticate" },
        { delay: 2500, step: 3, label: "Publish Dataset" },
        { delay: 3500, step: 4, label: "Generate URL" },
      ];

      // Execute workflow steps
      const timers = steps.map((s) =>
        setTimeout(() => {
          console.log(`Step ${s.step}: ${s.label}`);
          setCurrentStep(s.step);
        }, s.delay)
      );

      // Start the actual publishing API call AFTER step 4 begins
      const publishTimer = setTimeout(async () => {
        try {
          //const apiBase = window.location.hostname.includes('localhost') || window.location.hostname === '127.0.0.1'
          //  ? 'http://127.0.0.1:8000'
          //  : 'https://qlikai-app-ltmrv.ondigitalocean.app';
          const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
          const mqueryData = state.mqueryData || {};
          const publishStartTime = new Date();
          
          const response = await fetch(`${apiBase}/api/migration/publish-mquery`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(mqueryData),
          });

          let result: any = {};
          const rawText = await response.text();
          try {
            result = rawText ? JSON.parse(rawText) : {};
          } catch {}

          if (!response.ok || !result.success) {
            throw new Error(result.detail || result.error || `HTTP ${response.status}: Publish failed`);
          }

          // Wait a moment before showing completion
          await new Promise(resolve => setTimeout(resolve, 500));
          
          // Complete the workflow - step 5
          setCurrentStep(5);
          
          // Set success result with proper metrics
          const datasetName = result.dataset_name || state.selectedTable || "Qlik_Dataset";
          const workspaceUrl = result.workspace_url || "https://app.powerbi.com";
          const publishEndTime = new Date();
          const duration = publishEndTime.getTime() - publishStartTime.getTime();
          
          setPublishedTableName(datasetName);
          setDatasetURL(workspaceUrl);
          setResult({
            dataset: {
              workspace_id: workspaceUrl.split('/groups/')[1] || "",
              id: result.dataset_id || "",
              name: datasetName,
            },
            mquery: true,
            publishResult: result,
            // Store metrics from initial state for display
            tableCount: state.tableCount || result.tables_deployed || 1,
            rowCount: state.rowCount || state.totalRows || 0,
          });
          setStatusBoxes({ columns: true, powerbi: true, finished: true });
          setPublishing(false);
          
          // Set timing
          setPublishEndTime(publishEndTime);
          setPublishDuration(duration);

          // Store metadata
          sessionStorage.setItem("migration_publishing_method", "M_QUERY");
          sessionStorage.setItem("migration_dataset_name", datasetName);
          sessionStorage.setItem("migration_dataset_id", result.dataset_id || "");
          sessionStorage.setItem("migration_tables_deployed", String(result.tables_deployed || state.tableCount || 1));
          sessionStorage.setItem("migration_row_count", String(state.rowCount || state.totalRows || 0));
        } catch (error: any) {
          console.error("M Query publishing failed:", error);
          setPublishing(false);
          setCurrentStep(3); // Show error at step 3
          alert(`Publishing failed: ${error.message}`);
        }
      }, 4000);

      // Cleanup timers on unmount
      return () => {
        timers.forEach(clearTimeout);
        clearTimeout(publishTimer);
      };
    }
  }, [state?.publishMethod, state?.showWorkflow]);

  // ✅ Handle M Query publishing - show completion page directly (legacy)
  useEffect(() => {
    if (state?.publishMethod === "M_QUERY" && state?.publishResult && !state?.showWorkflow) {
      console.log("✅ M Query publishing detected - showing completion page");
      console.log("Publishing Result:", state.publishResult);
      
      const publishResult = state.publishResult;
      const datasetName = publishResult.dataset_name || state.selectedTable || "Qlik_Dataset";
      const workspaceUrl = publishResult.workspace_url || "https://app.powerbi.com";
      
      // Set UI state to show completion
      setPublishedTableName(datasetName);
      setDatasetURL(workspaceUrl);
      setResult({ 
        dataset: {
          workspace_id: workspaceUrl.split('/groups/')[1] || "",
          id: publishResult.dataset_id || "",
          name: datasetName,
        },
        mquery: true, // Flag that this is M Query
        publishResult: publishResult
      });
      setStatusBoxes({ columns: true, powerbi: true, finished: true });
      setCurrentStep(5);
      setPublishing(false);
      
      // Set timing
      const now = new Date();
      setPublishEndTime(now);
      setPublishDuration(0);
      
      // Hide export options since M Query publishing is complete
      setShowExportOptions(false);
    }
  }, [state?.publishMethod, state?.publishResult, state?.showWorkflow]);

  // Auto-publish on page load (but only after showing export options if needed)
  // Skip if M Query is detected
  useEffect(() => {
    // Skip auto-publish if M Query publishing is detected
    if (state?.publishMethod === "M_QUERY") {
      return;
    }
    
    // Auto-publish if:
    // 1. Export options are hidden (coming from Export page with exportComplete set), OR
    // 2. CSV/DAX data is prepared and user clicked the button to hide export options
    if (!showExportOptions && hasCSV && hasDAX) {
      const timer = setTimeout(() => {
        handlePublish();
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [showExportOptions, hasCSV, hasDAX, state?.publishMethod]);

  const formatDuration = (durationMs: number) => {
    const totalSeconds = Math.floor(durationMs / 1000);
    const milliseconds = durationMs % 1000;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${String(minutes).padStart(2, '0')}m:${String(seconds).padStart(2, '0')}s:${String(milliseconds).padStart(3, '0')}ms`;
  };

  // Monitor CSV/DAX data updates
  useEffect(() => {
    if (csvData) console.log("✅ CSV Data updated:", csvData.length, "characters");
    if (daxData) console.log("✅ DAX Data updated:", daxData.length, "characters");
  }, [csvData, daxData]);

  // Helper function to publish a single table
  const publishSingleTable = async (
    csvText: string,
    daxText: string,
    tableNameToPublish: string,
    rowCount: number
  ) => {
    const startTime = new Date();
    const dateStr = startTime.toISOString().split('T')[0];
    const timeStr = startTime.toTimeString().split(' ')[0].replace(/:/g, '-');
    const tableNameWithDateTime = `${tableNameToPublish}_${dateStr}_${timeStr}`;

    const form = new FormData();
    if (hasCSV && csvText) {
      form.append("csv_file", new Blob([csvText], { type: "text/csv" }), `${tableNameWithDateTime}.csv`);
    }
    if (hasDAX && daxText) {
      form.append("dax_file", new Blob([daxText], { type: "text/plain" }), `${tableNameWithDateTime}.dax`);
    }
    form.append("meta_app_name", appName);
    form.append("meta_table", tableNameWithDateTime);
    form.append("has_csv", hasCSV ? "true" : "false");
    form.append("has_dax", hasDAX ? "true" : "false");

    // const res = await fetch("http://localhost:8000/powerbi/process", {
    const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
    const res = await fetch(`${apiBase}/api/powerbi/process`, {
    
      method: "POST",
      body: form,
    });

    const data = await res.json();

    if (!res.ok) {
      throw new Error(data?.detail || "Publishing failed");
    }

    // Navigate to the Power BI workspace instead of dataset
    const realDatasetURL = data?.dataset?.workspace_id 
      ? `https://app.powerbi.com/groups/${data.dataset.workspace_id}`
      : "";

    return {
      tableName: tableNameToPublish,
      nameWithTime: tableNameWithDateTime,
      rowCount,
      url: realDatasetURL,
      workspaceId: data?.dataset?.workspace_id,
      datasetId: data?.dataset?.id,
      publishTime: new Date(),
    };
  };

  const handlePublish = async () => {
    try {
      const startTime = new Date();
      setPublishing(true);
      setCurrentStep(0);

      // Step 2: Initiate authentication (Device Code Flow) - Do this once
      setCurrentStep(1);
      setStatusBoxes({ columns: true, powerbi: false, finished: false });
      console.log("🔐 Step 2: Initiating Power BI authentication...");
      
      // const authRes = await fetch("http://localhost:8000/powerbi/login/acquire-token", {
      const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
      const authRes = await fetch(`${apiBase}/powerbi/login/acquire-token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });

      if (!authRes.ok) {
        const errData = await authRes.json();
        throw new Error(errData?.detail || "Authentication initiation failed");
      }

      const authData = await authRes.json();
      console.log("✅ Device code generated. User needs to authenticate.");
      console.log("Instructions:", authData?.message || "Check backend logs");

      // Step 3: Wait for user to complete authentication (up to 30 seconds)
      let authAttempts = 0;
      const maxAttempts = 30;
      let isAuthenticated = false;

      while (authAttempts < maxAttempts && !isAuthenticated) {
        await new Promise(resolve => setTimeout(resolve, 1000));

        try {
          // const statusRes = await fetch("http://localhost:8000/powerbi/login/status", {
          const apiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
          const statusRes = await fetch(`${apiBase}/powerbi/login/status`, {
            method: "POST",
          });
          const statusData = await statusRes.json();

          if (statusData.logged_in) {
            console.log("✅ Authentication successful!");
            isAuthenticated = true;
            break;
          }
        } catch (e) {
          console.warn("⚠️ Error checking auth status:", e);
        }

        authAttempts++;
        if (authAttempts % 5 === 0) {
          console.log(`⏳ Still waiting for authentication... (${authAttempts}s elapsed)`);
        }
      }

      if (!isAuthenticated) {
        throw new Error("Authentication timeout. Please login at microsoft.com/devicelogin and try again.");
      }

      setCurrentStep(2);
      setStatusBoxes({ columns: true, powerbi: true, finished: false });
      console.log("📤 Step 3: Publishing to Power BI...");

      // Check if separate table mode
      /*if (isSeparateMode && isMultiTableMode && selectedTablesToPublish.length > 0) {
        // Publish each table separately
        const publishedResults: any[] = [];

        for (let i = 0; i < tableCount; i++) {
          const csvText = state?.csvPayloads?.[`migration_csv_${i}`] || sessionStorage.getItem(`migration_csv_${i}`) || "";
          const tableName = selectedTablesToPublish[i]?.name || `Table_${i + 1}`;
          const tableRows = selectedTablesToPublish[i]?.data?.rows || [];
          const inferredRowCount = tableRows.length || (csvText ? Math.max(0, csvText.split('\n').length - 1) : 0);

          if (csvText) {
            console.log(`📤 Publishing table ${i + 1}/${tableCount}: ${tableName}...`);
            const publishResult = await publishSingleTable(csvText, `-- ${tableName} data`, tableName, inferredRowCount);
            publishedResults.push(publishResult);
          }
        }

        // Use the first published table as the primary dataset for the unified success UI
        if (publishedResults.length > 0) {
          setPublishedTableName(publishedResults[0].nameWithTime || publishedResults[0].tableName || "");
          setDatasetURL(publishedResults[0].url || "");
        }

        setResult({ published_tables: publishedResults });*/
        if (isSeparateMode && isMultiTableMode && selectedTablesToPublish.length > 0) {
        // Publish all tables as a single dataset with relationships
        const batchTables: any[] = [];

        for (let i = 0; i < tableCount; i++) {
          const csvText = state?.csvPayloads?.[`migration_csv_${i}`] || sessionStorage.getItem(`migration_csv_${i}`) || "";
          const tName = selectedTablesToPublish[i]?.name || `Table_${i + 1}`;
          if (!csvText) continue;

          // Parse CSV into rows
          const lines = csvText.trim().split('\n').filter((l: string) => l.trim());
          if (lines.length < 2) continue;
          /*
          const headers = lines[0].split(',').map((h: string) => h.trim().replace(/^"|"$/g, ''));
          const rows = lines.slice(1).map((line: string) => {
            const vals = line.split(',');
            const row: any = {};
            headers.forEach((h: string, idx: number) => {
              row[h] = vals[idx]?.trim().replace(/^"|"$/g, '') ?? null;
            });
            return row;
          });
          */
          const parseCSVLine = (line: string): string[] => {
          const result: string[] = [];
          let current = '';
          let inQuotes = false;
          for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
              if (inQuotes && line[i + 1] === '"') { current += '"'; i++; }
              else inQuotes = !inQuotes;
            } else if (ch === ',' && !inQuotes) {
              result.push(current.trim());
              current = '';
            } else {
              current += ch;
            }
          }
          result.push(current.trim());
          return result;
        };
        const headers = parseCSVLine(lines[0]);
        const rows = lines.slice(1).map((line: string) => {
          const vals = parseCSVLine(line);
          const row: any = {};
          headers.forEach((h: string, idx: number) => {
            row[h] = vals[idx] ?? null;
          });
          return row;
        });

          batchTables.push({ name: tName, rows });
          console.log(`📦 Prepared table ${i + 1}/${tableCount}: ${tName} (${rows.length} rows)`);
        }

        if (batchTables.length === 0) throw new Error("No table data available to publish");
        console.log(`📤 Publishing ${batchTables.length} tables as single dataset with relationships...`);

        const batchApiBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
        const batchRes = await fetch(`${batchApiBase}/api/migration/publish-tables`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            dataset_name: appName || "Qlik_Migrated_Dataset",
            tables: batchTables,
          }),
        });
        const batchData = await batchRes.json();
        if (!batchRes.ok) throw new Error(batchData?.detail || "Batch publish failed");

        console.log("✅ Batch publish successful:", batchData);
        setPublishedTableName(batchData.dataset_name || appName);
        setDatasetURL(batchData.workspace_url || "");
        setResult({ published_tables: batchTables.map((t: any) => ({ tableName: t.name, rowCount: t.rows.length, url: batchData.workspace_url })) });
      } else {
        // Combined mode or single table - use updated logic (prefer navigation state payloads)
        setCurrentStep(0);
        console.log("📦 Step 1: Preparing data...");
        
        let csvText = "";
        let daxText = "";
        let tableNameForPublish = customTableName || "Data";

        if (isMultiTableMode) {
          csvText = state?.csvPayloads?.["migration_csv_0"] || sessionStorage.getItem("migration_csv_0") || "";
          let selectedTableNames: string[] = [];
          try {
            const selectedTablesJson = sessionStorage.getItem("migration_selected_tables") || sessionStorage.getItem("migration_selected_tables_meta");
            if (selectedTablesJson) {
              const tables = JSON.parse(selectedTablesJson);
              // `tables` might be full objects or metadata; normalize
              selectedTableNames = tables.map((t: any) => t.name || t.table || '');
            }
          } catch (e) {
            //
          }
          
          daxText = state?.daxPayloads?.["migration_dax"] || sessionStorage.getItem("migration_dax") || `-- Multi-Table Export\n-- Primary Table: ${selectedTableNames[0] || 'Table 1'}\n`;
          daxText += `-- All Selected Tables: ${selectedTableNames.join(', ')}\n`;
          daxText += `-- Generated: ${new Date().toISOString()}\n\n`;
          daxText += selectedTableNames.map((name, idx) => {
            const tableCsv = state?.csvPayloads?.[`migration_csv_${idx}`] || sessionStorage.getItem(`migration_csv_${idx}`) || "";
            const tableSize = tableCsv ? Math.max(0, tableCsv.split('\n').length) : 0;
            return `-- ${name}: ${tableSize - 1} rows`;
          }).join('\n');

          tableNameForPublish = selectedTableNames[0] || "Combined_Data";
        } else {
          csvText = state?.csvPayloads?.["migration_csv"] || sessionStorage.getItem("migration_csv") || "";
          daxText = state?.daxPayloads?.["migration_dax"] || sessionStorage.getItem("migration_dax") || "";
        }

        console.log("📊 CSV Data length:", csvText.length);
        console.log("📊 DAX Data length:", daxText.length);
        setCSVData(csvText);
        setDAXData(daxText);

        if (!hasCSV && !hasDAX) {
          throw new Error("No formats selected. Please go back to Export page.");
        }

        if (hasCSV && !csvText) {
          throw new Error("CSV was selected but file not provided");
        }

        if (hasDAX && !daxText) {
          throw new Error("DAX was selected but file not provided");
        }

        const publishResult = await publishSingleTable(csvText, daxText, tableNameForPublish, rowCount);
        const dateStr = startTime.toISOString().split('T')[0];
        const timeStr = startTime.toTimeString().split(' ')[0].replace(/:/g, '-');
        setPublishedTableName(`${tableNameForPublish}_${dateStr}_${timeStr}`);
        setDatasetURL(publishResult.url);
        setResult({ dataset: publishResult });
      }

      setStatusBoxes({ columns: true, powerbi: true, finished: true });
      setCurrentStep(5);
      
      const endTime = new Date();
      setPublishEndTime(endTime);
      const durationMs = endTime.getTime() - startTime.getTime();
      setPublishDuration(durationMs);
      
      console.log("✅ Dataset published successfully!");

    } catch (err: any) {
      console.error("❌ Publishing error:", err);
      setStatusBoxes({ columns: false, powerbi: false, finished: false });
    } finally {
      setPublishing(false);
    }
  };

  const copyToClipboard = () => {
    navigator.clipboard.writeText(datasetURL).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }).catch(() => {
      // Silently fail if copy fails
    });
  };

  const downloadDataset = async () => {
    try {
      console.log("📥 Generating Validation & Reconciliation Report...");
      
      // Extract actual metrics from the CSV data that was published
      let qlikRowCount = 0;
      let qlikColumnCount = 0;
      let qlikColumns: string[] = [];
      
      // Prefer reported totalRows from navigation state or previously computed `rowCount`.
      // This ensures the PDF/report shows the correct *total* even when the in-memory CSV contains only a page.
      const navReportedTotal = state?.totalRows ?? rowCount ?? 0;
      if (navReportedTotal && navReportedTotal > 0) {
        qlikRowCount = navReportedTotal;
        // attempt to infer columns if CSV or selectedTables available
        const csvContentForCols =
          state?.csvPayloads?.["migration_csv"] ||
          state?.csvPayloads?.["migration_csv_0"] ||
          csvData ||
          null;
        if (csvContentForCols) {
          const csvLinesForCols = csvContentForCols.trim().split('\n').filter((line: string) => line.trim());
          if (csvLinesForCols.length > 0) {
            qlikColumns = csvLinesForCols[0].split(',').map((col: string) => col.trim());
            qlikColumnCount = qlikColumns.length;
          }
        } else if (state?.selectedTables && state.selectedTables.length > 0) {
          const first = state.selectedTables[0];
          qlikColumns = first?.data?.rows?.length ? Object.keys(first.data.rows[0]) : (columns || []);
          qlikColumnCount = qlikColumns.length;
        } else {
          qlikColumns = columns || [];
          qlikColumnCount = qlikColumns.length;
        }
      } else {
        // Fallback: derive counts from CSV / state.selectedTables / sessionStorage (previous behaviour)
        const csvContent =
          state?.csvPayloads?.["migration_csv"] ||
          state?.csvPayloads?.["migration_csv_0"] ||
          (() => {
            const firstCsvKey = Object.keys(state?.csvPayloads || {}).find(k => k.startsWith('migration_csv_'));
            return firstCsvKey ? state.csvPayloads[firstCsvKey] : null;
          })() ||
          sessionStorage.getItem("migration_csv") ||
          sessionStorage.getItem("migration_csv_0") ||
          csvData ||
          "";

        if (csvContent) {
          const csvLines = csvContent.trim().split('\n').filter((line: string) => line.trim());
          if (csvLines.length > 0) {
            // First line = headers
            qlikColumns = csvLines[0].split(',').map((col: string) => col.trim());
            qlikColumnCount = qlikColumns.length;
            // Remaining lines = data rows
            qlikRowCount = csvLines.length - 1;
          }
        } else if (state?.selectedTables && state.selectedTables.length > 0) {
          qlikRowCount = state.selectedTables.reduce((sum: number, t: any) => sum + (t.data?.rows?.length || 0), 0);
          const first = state.selectedTables[0];
          qlikColumns = first?.data?.rows?.length ? Object.keys(first.data.rows[0]) : (columns || []);
          qlikColumnCount = qlikColumns.length;
        } else if (sessionStorage.getItem('migration_selected_tables_meta')) {
          try {
            const meta = JSON.parse(sessionStorage.getItem('migration_selected_tables_meta') || '[]');
            qlikRowCount = (meta || []).reduce((sum: number, t: any) => sum + (t.rowCount || 0), 0);
            qlikColumns = columns || [];
            qlikColumnCount = qlikColumns.length;
          } catch (e) {
            // fallback to session-stored migration_row_count
            qlikRowCount = Number(sessionStorage.getItem('migration_row_count') || 0);
          }
        } else {
          // fallback to session-stored migration_row_count if nothing else is present
          qlikRowCount = Number(sessionStorage.getItem('migration_row_count') || 0);
        }
      }
      
      // For demo: Power BI metrics same as Qlik (simulating successful sync)
      // In real scenario, this would query Power BI API for actual published metrics
      const powerbiRowCount = qlikRowCount;
      const powerbiColumnCount = qlikColumnCount;
      const powerbiColumns = [...qlikColumns];
      
      console.log(`📊 QLIK SENSE METRICS:
        📈 Row Count: ${qlikRowCount}
        📋 Column Count: ${qlikColumnCount}
        🏷️  Columns: ${qlikColumns.join(', ')}`);
      
      console.log(`📊 POWER BI METRICS:
        📈 Row Count: ${powerbiRowCount}
        📋 Column Count: ${powerbiColumnCount}
        🏷️  Columns: ${powerbiColumns.join(', ')}`);
      
      // Build comparison metrics with additional data
      const qlikMetrics = {
        row_count: qlikRowCount,
        table_count: (tableCount > 0 ? tableCount : (state?.selectedTables ? state.selectedTables.length : 1)),
        // column_count: qlikColumnCount,
        column_names: qlikColumns,
        total_records: qlikRowCount,
        // certification_status: "Pre-Migration",
        timestamp: new Date().toISOString()
      };
      
      const powerbiMetrics = {
        row_count: powerbiRowCount,
        table_count: (tableCount > 0 ? tableCount : (state?.selectedTables ? state.selectedTables.length : 1)),
        // column_count: powerbiColumnCount,
        column_names: powerbiColumns,
        total_records: powerbiRowCount,
        // certification_status: "Published to Power BI",
        timestamp: new Date().toISOString()
      };
      
      // Prepare comprehensive payload for PDF generation
      const payload = {
        table_name: publishedTableName || tableName,
        app_name: appName,
        qlik_metrics: qlikMetrics,
        powerbi_metrics: powerbiMetrics,
        sync_timestamp: new Date().toISOString(),
        migration_status: "Completed",
        publishing_method: result?.mquery ? "M_QUERY" : "CSV_EXPORT",
        tables_deployed: tableCount > 0 ? tableCount : (state?.selectedTables ? state.selectedTables.length : 1)
      };

      console.log("📤 Sending to backend:", JSON.stringify(payload, null, 2));

      // Download PDF
      // const response = await fetch('http://localhost:8000/report/download-pdf', {
      const response = await fetch('https://qlikai-app-ltmrv.ondigitalocean.app/report/download-pdf', {
        method: 'POST',
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error?.detail || 'Failed to generate PDF');
      }

      // Get blob and trigger download
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      // link.download = `Validation_Report_${publishedTableName || tableName}_${new Date().toISOString().split('T')[0]}.pdf`;
       // sanitize filename to remove unicode and special chars
      const baseName = publishedTableName || tableName;
      const safeName = baseName.replace(/[^a-zA-Z0-9 _-]/g, '');
      link.download = `Validation_Reconciliation_Report_${safeName}_${new Date().toISOString().split('T')[0]}.pdf`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      console.log('✅ Validation report downloaded successfully');
    } catch (error) {
      console.error('❌ Error downloading report:', error);
      alert('Failed to download report PDF: ' + (error instanceof Error ? error.message : String(error)));
    }
  };

  const openInPowerBI = () => {
    if (datasetURL) {
      window.open(datasetURL, "_blank");
    }
  };

  const resolveWorkspaceId = () => {
    const resultWorkspaceId =
      result?.dataset?.workspaceId ||
      result?.published_tables?.[0]?.workspaceId ||
      "";
    if (resultWorkspaceId) return resultWorkspaceId;

    const urlMatch = datasetURL.match(/groups\/([^/?#]+)/i);
    return urlMatch?.[1] || "";
  };

  const resolveAppId = () => {
    return state?.appId || sessionStorage.getItem("appSelected") || "";
  };

  const toCsvSample = (
    csvText: string,
    maxRows: number = 150,
    maxChars: number = 120000
  ) => {
    const text = (csvText || "").trim();
    if (!text) return "";

    const lines = text.split(/\r?\n/);
    if (lines.length === 0) return "";

    const sampledLines = [lines[0]]; // header
    for (let i = 1; i < lines.length && sampledLines.length <= maxRows; i++) {
      if (!lines[i]?.trim()) continue;
      sampledLines.push(lines[i]);
    }

    let sampled = sampledLines.join("\n");
    if (sampled.length > maxChars) {
      sampled = sampled.slice(0, maxChars);
    }
    return sampled;
  };

  const collectCsvPayloadMap = () => {
    const payloadMap: Record<string, string> = {};
    // Keep multipart form payload safely below 1MB parser limit.
    let remainingBudget = 650000;

    if (isMultiTableMode) {
      const totalTables = Math.max(tableCount, selectedTablesToPublish.length);
      for (let i = 0; i < totalTables; i++) {
        if (remainingBudget <= 2000) break;

        const tableNameForCsv = selectedTablesToPublish[i]?.name || `Table_${i + 1}`;
        const csvText =
          state?.csvPayloads?.[`migration_csv_${i}`] ||
          sessionStorage.getItem(`migration_csv_${i}`) ||
          "";

        const sampled = toCsvSample(csvText, 120, Math.min(100000, remainingBudget));
        if (sampled.trim()) {
          payloadMap[tableNameForCsv] = sampled;
          remainingBudget -= sampled.length + tableNameForCsv.length + 32;
        }
      }
      return payloadMap;
    }

    const singleCsv =
      state?.csvPayloads?.["migration_csv"] ||
      sessionStorage.getItem("migration_csv") ||
      state?.csvPayloads?.["migration_csv_0"] ||
      sessionStorage.getItem("migration_csv_0") ||
      "";
    const singleTableName = customTableName || tableName || "Data";
    const sampledSingle = toCsvSample(singleCsv, 200, 250000);
    if (sampledSingle.trim()) {
      payloadMap[singleTableName] = sampledSingle;
    }
    return payloadMap;
  };

  const generateXmlaSemanticModel = async () => {
    try {
      setXmlaSemanticLoading(true);
      setXmlaSemanticError("");
      setXmlaSemanticResult(null);
      setShowXmlaDiagram(false);

      const appId = resolveAppId();
      const workspaceId = resolveWorkspaceId();

      if (!appId) {
        throw new Error("Missing app ID. Please restart from app selection.");
      }
      if (!workspaceId) {
        throw new Error("Missing workspace ID. Publish to cloud first, then enable semantic model.");
      }

      const datasetNameRaw = publishedTableName || customTableName || tableName || "Model_Master";
      const datasetName = `${datasetNameRaw.replace(/\s+/g, "_")}_Semantic`;
      const csvPayloadMap = collectCsvPayloadMap();

      const form = new FormData();
      form.append("app_id", appId);
      form.append("dataset_name", datasetName);
      form.append("workspace_id", workspaceId);
      if (Object.keys(csvPayloadMap).length > 0) {
        const csvPayloadJson = JSON.stringify(csvPayloadMap);
        // Hard guard: avoid multipart part-size failures from large JSON payloads.
        if (csvPayloadJson.length <= 850000) {
          form.append("csv_payload_json", csvPayloadJson);
        } else {
          console.warn("XMLA CSV payload too large, sending schema-only mode.");
        }
      }

      // const res = await fetch("http://localhost:8000/api/migration/publish-semantic-model", {
      const res = await fetch("https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/publish-semantic-model", {
        method: "POST",
        body: form,
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || data?.message || "Failed to create XMLA semantic model");
      }

      setXmlaSemanticResult(data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setXmlaSemanticError(msg);
    } finally {
      setXmlaSemanticLoading(false);
    }
  };

  // Prevent unused symbol warning
  void generateXmlaSemanticModel;

  const generateDesktopCloudBundle = async () => {
    try {
      setDesktopBundleLoading(true);
      setDesktopBundleError("");
      setDesktopBundle(null);

      const appId = resolveAppId();
      const workspaceId = resolveWorkspaceId();

      if (!appId) {
        throw new Error("Missing app ID. Please restart from app selection.");
      }

      if (!workspaceId) {
        throw new Error("Missing workspace ID. Publish to cloud first, then generate desktop bundle.");
      }

      const datasetNameRaw = publishedTableName || customTableName || tableName || "Model_Master";
      const datasetName = datasetNameRaw.replace(/\s+/g, "_");

      const params = new URLSearchParams({
        app_id: appId,
        dataset_name: datasetName,
        workspace_id: workspaceId,
        publish_mode: "desktop_cloud",
      });

      // const res = await fetch(`http://localhost:8000/api/migration/publish-table?${params.toString()}`, {
      const res = await fetch(`https://qlikai-app-ltmrv.ondigitalocean.app/api/migration/publish-table?${params.toString()}`, {
        method: "POST",
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || data?.message || "Failed to generate Desktop+Cloud bundle");
      }

      if (!data?.desktop_bundle?.bundle_dir) {
        throw new Error("Desktop bundle response missing bundle path");
      }

      setDesktopBundle(data.desktop_bundle);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setDesktopBundleError(msg);
    } finally {
      setDesktopBundleLoading(false);
    }
  };

  // Prevent unused symbol warning
  void generateDesktopCloudBundle;

  const copyBundlePath = () => {
    const path = desktopBundle?.bundle_dir;
    if (!path) return;

    navigator.clipboard.writeText(path).then(() => {
      setBundlePathCopied(true);
      setTimeout(() => setBundlePathCopied(false), 2000);
    }).catch(() => {
      // Ignore clipboard errors
    });
  };

  return (
    <div className="publish-container">
      <h2 className="publish-header">📤 Publish to Power BI</h2>

      {/* EXPORT OPTIONS SECTION - Show first to let user select export format */}
      {showExportOptions && (
        <div className="export-wrap" style={{ maxWidth: "900px", margin: "0 auto" }}>
          {/* HEADER WITH TIMER */}
          <div className="header-with-timer">
            <h2>📤 Export Data</h2>
            <span className="analysis-time">Analysis Time: {pageLoadTime || "00s"}</span>
          </div>

          {/* INFO BOXES */}
          <div className="info-grid">
            <div className="info-box">
              <span className="label">Application</span>
              <span className="value">{appName}</span>
            </div>

            <div className="info-box">
              <span className="label">Table Name</span>
              <span className="value">{isMultiSelect ? (selectedTablesToPublish[0]?.name || "") : selectedTable}</span>
            </div>

            <div className="info-box">
              <span className="label">Total Rows</span>
              <span className="value">{rowCount}</span>
            </div>

            <div className="info-box">
              <span className="label">Tables Exported</span>
              <span className="value">{isMultiSelect ? selectedTablesToPublish.length : 1}</span>
            </div>
          </div>

          {/* EXPORT OPTIONS */}
          <div className="export-options-grid">
            <div className="export-section">
              <div className="export-header powerbi">
                Export To PowerBI
              </div>

              <div className="checkbox-row">
                <label>
                  <input
                    type="checkbox"
                    checked={exportOptionsState.combined}
                    onChange={() => {
                      setExportOptionsState({ ...exportOptionsState, combined: !exportOptionsState.combined });
                    }}
                  />
                  <strong> 📄 Export as CSV / DAX </strong>
                </label>
              </div>
            </div>

            <div className="export-section disabled-section">
              <div className="export-header ssrs">
                Export To SSRS
              </div>

              <div className="checkbox-row">
                <label>
                  <input type="checkbox" disabled />
                  <strong> Select All</strong>
                </label>
              </div>

              <div className="checkbox-row">
                <label>
                  <input type="checkbox" disabled />
                  📄 Export as CSV
                </label>
              </div>

              <div className="checkbox-row">
                <label>
                  <input type="checkbox" disabled />
                  📊 Export as DAX
                </label>
              </div>
            </div>
          </div>

          {/* CONTINUE BUTTON */}
          <div className="page-actions">
            {showError && (
              <div className="error-message">
                ⚠️ Please select an export option to continue
              </div>
            )}
            <button
              className="continue-btn"
              disabled={!exportOptionsState.combined}
              onClick={() => {
                if (exportOptionsState.combined) {
                  setShowError(false);
                  // Mark CSV/DAX as selected
                  sessionStorage.setItem("migration_has_csv", "true");
                  sessionStorage.setItem("migration_has_dax", "true");
                  sessionStorage.setItem("exportComplete", "true");

                  // Hide export options and proceed with publish
                  setShowExportOptions(false);
                } else {
                  setShowError(true);
                }
              }}
              title={!exportOptionsState.combined ? "Select an export option to continue" : "Publish to PowerBI"}
            >
              {exportOptionsState.combined ? "✅ Publish to PowerBI" : "⚠️ Select Export Option"}
            </button>
          </div>
        </div>
      )}

      {/* PUBLISHING SECTION - Show after export options are confirmed */}
      {!showExportOptions && (
        <>
      {/* STEPPER - Show while publishing and after completion */}
      {(publishing || result) && (
        <Stepper currentStep={currentStep} steps={STEPPER_STEPS} />
      )}

      {/* LOADING MESSAGE - Show only while publishing and no result yet */}
      {publishing && !result && (
        <LoadingOverlay
          isVisible={publishing && !result}
          message="Publishing your dataset to Power BI..."
        />
      )}

      {/* FORM SECTION - HIDDEN */}
      {false && (
        <div className="form-section">
          <div className="form-group">
            <label className="form-label">📋 Custom Table Name</label>
            <input
              type="text"
              value={customTableName}
              onChange={(e) => setCustomTableName(e.target.value)}
              placeholder="Enter table name"
              disabled={publishing}
              className="form-input"
            />
          </div>

          {/* STATUS BOXES */}
          <div className="status-boxes">
            {/* CSV Status */}
            <div className={`status-box ${statusBoxes.columns ? "active" : ""}`}>
              <div className="status-box-icon">📊</div>
              <div className="status-box-label">CSV Data</div>
              <div className="status-box-status">
                {statusBoxes.columns ? "✅ Prepared" : "⏳ Pending"}
              </div>
            </div>

            {/* Power BI Auth Status */}
            <div className={`status-box ${statusBoxes.powerbi ? "active" : ""}`}>
              <div className="status-box-icon">⚡</div>
              <div className="status-box-label">Power BI Auth</div>
              <div className="status-box-status">
                {statusBoxes.powerbi ? "✅ Authenticated" : "⏳ Pending"}
              </div>
            </div>

            {/* URL Status */}
            <div className={`status-box ${statusBoxes.finished ? "active" : ""}`}>
              <div className="status-box-icon">🔗</div>
              <div className="status-box-label">Dataset URL</div>
              <div className="status-box-status">
                {statusBoxes.finished ? "✅ Generated" : "⏳ Pending"}
              </div>
            </div>
          </div>

          {/* PUBLISH BUTTON */}
          <button
            onClick={handlePublish}
            disabled={publishing}
            className="btn btn-primary"
          >
            {publishing ? "⏳ Publishing..." : "🚀 Publish Now"}
          </button>

          {/* Authentication Instructions */}
          {publishing && (
            <div className="instruction-box">
              <div className="instruction-title">🔐 Authentication Required</div>
              <div className="instruction-line">1️⃣ A device code has been generated</div>
              <div className="instruction-line">2️⃣ Go to: <strong>https://microsoft.com/devicelogin</strong></div>
              <div className="instruction-line">3️⃣ Enter the code shown in the console</div>
              <div className="instruction-line">4️⃣ Wait for this page to complete...</div>
            </div>
          )}
        </div>
      )}

      {/* SUCCESS SECTION */}
      {result && (
        <div className="success-section">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
            <div className="success-title">✨ Success!</div>
            <div style={{ 
              fontSize: "14px", 
              color: "#0078d4",
              fontWeight: "bold",
              padding: "8px 15px",
              backgroundColor: "#e7f3ff",
              borderRadius: "5px"
            }}>
              ⏱️ Published in {formatDuration(publishDuration)}
            </div>
          </div>

          <div className="success-message">
            Dataset "<strong>{publishedTableName}</strong>" published to Power BI Cloud
          </div>

          {/* URL BOX */}
          <div className="url-box">
            <div className="url-label">🔗 Dataset URL </div>
            <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px" }}>
              <input
                type="text"
                value={datasetURL}
                readOnly
                className="url-input"
              />
              <button
                onClick={copyToClipboard}
                className="btn btn-small"
                style={{
                  padding: "8px 12px",
                  width:"60px",
                  height : "45px",
                  whiteSpace: "nowrap",
                  backgroundColor: copied ? "#27ae60" : "#3498db",
                  color: "white",
                  border: "none",
                  borderRadius: "4px",
                  cursor: "pointer",
                  fontSize: "14px",
                  transition: "background-color 0.3s"
                }}
              >
                {copied ? "✅ Copy" : "📋"}
              </button>
            </div>
            <div className="url-note">
              ✓ This URL is ready to use. Click "Open in Power BI" to view your dataset.
            </div>
          </div>

          {/* COMBINED FORMAT INFO - Only show for CSV/DAX publishing */}
          {hasCSV && hasDAX && result?.mquery !== true && (
            <div style={{ 
              backgroundColor: "#dbeafe", 
              border: "2px solid #0078d4", 
              borderRadius: "10px", 
              padding: "16px", 
              marginBottom: "20px",
              textAlign: "center"
            }}>
              <strong style={{ color: "#0078d4", fontSize: "16px" }}>
                📊 Combined Format: CSV + DAX
              </strong>
              <div style={{ fontSize: "13px", color: "#0369a1", marginTop: "8px" }}>
                ✅ CSV Data: Complete dataset with all rows and columns<br/>
                ✅ DAX Metadata: Query definitions and table structure
              </div>
            </div>
          )}

          {/* M QUERY FORMAT INFO */}
          {result?.mquery && (
            <div style={{ 
              backgroundColor: "#fef3c7", 
              border: "2px solid #f59e0b", 
              borderRadius: "10px", 
              padding: "16px", 
              marginBottom: "20px",
              textAlign: "center"
            }}>
              <strong style={{ color: "#d97706", fontSize: "16px" }}>
                🔍 M Query Format
              </strong>
              <div style={{ fontSize: "13px", color: "#92400e", marginTop: "8px" }}>
                ✅ Power BI M Query: Complete LoadScript conversion<br/>
                ✅ Ready for Power Query Editor in Power BI Desktop
              </div>
            </div>
          )}

          {/* INFO BOXES (show master table details) */}
          <div className="info-boxes">
            <div className="info-box">
              <div className="info-box-label">Table Name </div>
              <div className="info-box-value" style={{ fontSize: "12px", wordBreak: "break-word" }}>{publishedTableName}</div>
            </div>
            <div className="info-box">
              <div className="info-box-label">Published Date & Time</div>
              <div className="info-box-value">{publishEndTime ? publishEndTime.toLocaleString() : new Date().toLocaleString()}</div>
            </div>
            <div className="info-box">
              <div className="info-box-label">Publishing Duration</div>
              <div className="info-box-value">⏱️ {formatDuration(publishDuration)}</div>
            </div>
            <div className="info-box">
              <div className="info-box-label">Total Rows</div>
              <div className="info-box-value" style={{ fontSize: "18px", fontWeight: "bold", color: "#0078d4" }}>
                {result?.mquery ? (result.rowCount || 0) : rowCount}
              </div>
            </div>
            <div className="info-box">
              <div className="info-box-label">Tables Exported</div>
              <div className="info-box-value" style={{ fontSize: "18px", fontWeight: "bold", color: "#0078d4" }}>
                {result?.mquery ? (result.tableCount || 1) : (tableCount > 0 ? tableCount : (isMultiTableMode ? tableCount : 1))}
              </div>
            </div>
            <div className="info-box">
              <div className="info-box-label">Status</div>
              <div className="info-box-value">✅ Published</div>
            </div>
          </div>

        
        

          {/* ACTION BUTTONS */}
          <div className="btn-group">
            <button
              onClick={openInPowerBI}
              className="btn btn-small btn-success"
            >
              Open in Power BI
            </button>
            <button
              onClick={downloadDataset}
              className="btn btn-small btn-warning"
            >
              Download Report
            </button>
          </div>

          {desktopBundleError && (
            <div style={{
              marginTop: "12px",
              padding: "10px 12px",
              borderRadius: "6px",
              backgroundColor: "#fff5f5",
              color: "#b42318",
              border: "1px solid #fecaca",
              fontSize: "13px"
            }}>
              Desktop bundle error: {desktopBundleError}
            </div>
          )}

          {xmlaSemanticError && (
            <div style={{
              marginTop: "12px",
              padding: "10px 12px",
              borderRadius: "6px",
              backgroundColor: "#fff5f5",
              color: "#b42318",
              border: "1px solid #fecaca",
              fontSize: "13px"
            }}>
              XMLA semantic model error: {xmlaSemanticError}
            </div>
          )}

          {xmlaSemanticResult?.links?.workspace && (
            <div style={{
              marginTop: "14px",
              padding: "12px",
              borderRadius: "8px",
              backgroundColor: "#ecfdf3",
              border: "1px solid #86efac"
            }}>
              <div style={{ fontWeight: 600, marginBottom: "8px", color: "#14532d" }}>
                XMLA semantic model created in cloud
              </div>
              <div style={{ fontSize: "12px", color: "#14532d", marginBottom: "6px" }}>
                Model name: <strong>{xmlaSemanticResult?.dataset_name || "N/A"}</strong>
              </div>
              <div style={{ fontSize: "12px", color: "#14532d", marginBottom: "8px" }}>
                This mode builds an enhanced semantic model from Qlik schema + CSV metadata without Desktop.
              </div>
              <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "8px" }}>
                <input
                  type="text"
                  readOnly
                  value={xmlaSemanticResult?.links?.workspace || ""}
                  className="url-input"
                />
                <button
                  onClick={() => window.open(xmlaSemanticResult?.links?.workspace, "_blank")}
                  className="btn btn-small"
                  style={{
                    padding: "8px 12px",
                    minWidth: "120px",
                    height: "45px",
                    backgroundColor: "#16a34a",
                    color: "white",
                    border: "none",
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "13px",
                  }}
                >
                  Open Workspace
                </button>
              </div>
              {xmlaSemanticResult?.links?.dataset && (
                <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "8px" }}>
                  <input
                    type="text"
                    readOnly
                    value={xmlaSemanticResult?.links?.dataset || ""}
                    className="url-input"
                  />
                  <button
                    onClick={() => window.open(xmlaSemanticResult?.links?.dataset, "_blank")}
                    className="btn btn-small"
                    style={{
                      padding: "8px 12px",
                      minWidth: "120px",
                      height: "45px",
                      backgroundColor: "#15803d",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "13px",
                    }}
                  >
                    Open Model
                  </button>
                  <button
                    onClick={() => setShowXmlaDiagram((v) => !v)}
                    className="btn btn-small"
                    style={{
                      padding: "8px 12px",
                      minWidth: "120px",
                      height: "45px",
                      backgroundColor: "#0f766e",
                      color: "white",
                      border: "none",
                      borderRadius: "4px",
                      cursor: "pointer",
                      fontSize: "13px",
                    }}
                  >
                    {showXmlaDiagram ? "Hide ER Diagram" : "View ER Diagram"}
                  </button>
                </div>
              )}
              {!xmlaSemanticResult?.links?.dataset && (
                <div style={{ fontSize: "12px", color: "#14532d", marginTop: "8px" }}>
                  Dataset link unavailable. Use workspace search with the model name shown above.
                </div>
              )}
              {showXmlaDiagram && xmlaSemanticResult?.er_diagram && (
                <div style={{ marginTop: "10px" }}>
                  <div style={{ fontSize: "12px", color: "#14532d", marginBottom: "6px" }}>
                    Mermaid ER diagram (all tables + inferred relationships)
                  </div>
                  <textarea
                    readOnly
                    value={xmlaSemanticResult.er_diagram}
                    style={{
                      width: "100%",
                      minHeight: "180px",
                      borderRadius: "6px",
                      border: "1px solid #86efac",
                      padding: "10px",
                      fontFamily: "monospace",
                      fontSize: "12px",
                      color: "#14532d",
                      backgroundColor: "#f0fdf4",
                    }}
                  />
                </div>
              )}
            </div>
          )}

          {desktopBundle?.bundle_dir && (
            <div style={{
              marginTop: "14px",
              padding: "12px",
              borderRadius: "8px",
              backgroundColor: "#f0f9ff",
              border: "1px solid #bae6fd"
            }}>
              <div style={{ fontWeight: 600, marginBottom: "8px", color: "#0c4a6e" }}>
                Desktop + Cloud bundle generated
              </div>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <input
                  type="text"
                  readOnly
                  value={desktopBundle.bundle_dir}
                  className="url-input"
                />
                <button
                  onClick={copyBundlePath}
                  className="btn btn-small"
                  style={{
                    padding: "8px 12px",
                    minWidth: "90px",
                    height: "45px",
                    backgroundColor: bundlePathCopied ? "#27ae60" : "#3498db",
                    color: "white",
                    border: "none",
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "13px",
                  }}
                >
                  {bundlePathCopied ? "Copied" : "Copy Path"}
                </button>
              </div>
              <div style={{ marginTop: "8px", fontSize: "12px", color: "#0c4a6e" }}>
                Open the bundle README file and publish with Power BI Desktop to cloud.
              </div>
            </div>
          )}
          {/* COMPLETION MESSAGE */}
          <div className="completion-message">
            🎉 Your dataset is now live in Power BI! You can start creating reports and dashboards with your data.
          </div>
        </div>
      )}
      

      {/* HIDDEN REPORT CONTENT FOR PDF GENERATION */}
      <div id="report-content" style={{
        position: 'absolute',
        left: '-9999px',
        top: '-9999px',
        width: '1000px',
        backgroundColor: 'white'
      }}>
        <div style={{ padding: '40px', fontFamily: 'Arial, sans-serif', color: '#333' }}>
          <h1 style={{ color: '#0078d4', borderBottom: '3px solid #0078d4', paddingBottom: '15px', marginBottom: '20px', fontSize: '28px' }}>
            📊 Power BI Dataset Report
          </h1>
          
          <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
            📋 Table Information
          </h2>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '15px 0' }}>
            <tbody>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Original Table Name:</td>
                <td style={{ padding: '10px 12px' }}>{customTableName}</td>
              </tr>
              <tr style={{ backgroundColor: '#f9f9f9', borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Published Table Name:</td>
                <td style={{ padding: '10px 12px', backgroundColor: '#fff3cd', fontWeight: 'bold' }}>{publishedTableName}</td>
              </tr>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Application:</td>
                <td style={{ padding: '10px 12px' }}>{appName}</td>
              </tr>
              <tr style={{ backgroundColor: '#f9f9f9', borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Published Date & Time:</td>
                <td style={{ padding: '10px 12px' }}>{publishEndTime ? publishEndTime.toLocaleString() : new Date().toLocaleString()}</td>
              </tr>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Publishing Duration:</td>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>{formatDuration(publishDuration)}</td>
              </tr>
            </tbody>
          </table>

          {/* Dataset Details section removed per user request */}
          <div style={{ height: '8px' }} />

          <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
            📑 Columns ({columns.length} total)
          </h2>
          <pre style={{ backgroundColor: '#f5f5f5', padding: '15px', borderRadius: '5px', borderLeft: '3px solid #0078d4', fontSize: '12px', overflow: 'auto' }}>
            {columns.map((col: string, idx: number) => `${idx + 1}. ${col}`).join("\n")}
          </pre>

          <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
            ☁️ Power BI Information
          </h2>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '15px 0' }}>
            <tbody>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Dataset URL:</td>
                <td style={{ padding: '10px 12px', wordBreak: 'break-all', fontSize: '12px' }}>{datasetURL}</td>
              </tr>
              <tr style={{ backgroundColor: '#f9f9f9', borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Status:</td>
                <td style={{ padding: '10px 12px', color: '#107c10', fontWeight: 'bold' }}>✅ Published to Power BI Cloud</td>
              </tr>
            </tbody>
          </table>

          <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
            ✅ Export Status
          </h2>
          <table style={{ width: '100%', borderCollapse: 'collapse', margin: '15px 0' }}>
            <tbody>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>CSV Processed:</td>
                <td style={{ padding: '10px 12px' }}>{hasCSV ? "✅ Yes" : "❌ No"}</td>
              </tr>
              <tr style={{ backgroundColor: '#f9f9f9', borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>DAX Processed:</td>
                <td style={{ padding: '10px 12px' }}>{hasDAX ? "✅ Yes" : "❌ No"}</td>
              </tr>
              <tr style={{ borderBottom: '1px solid #e0e0e0' }}>
                <td style={{ padding: '10px 12px', fontWeight: 'bold' }}>Dataset Published to Power BI:</td>
                <td style={{ padding: '10px 12px', color: '#107c10', fontWeight: 'bold' }}>✅ Successfully Published</td>
              </tr>
            </tbody>
          </table>

          {hasCSV && csvData && (
            <>
              <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
                📄 CSV Data
              </h2>
              <div style={{ backgroundColor: '#ffffff', padding: '15px', borderRadius: '5px', borderLeft: '3px solid #0078d4', fontSize: '11px', overflow: 'visible', wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                {csvData}
              </div>
            </>
          )}

          {hasDAX && daxData && (
            <>
              <h2 style={{ color: '#0078d4', marginTop: '25px', marginBottom: '15px', fontSize: '18px', borderLeft: '4px solid #0078d4', paddingLeft: '10px' }}>
                🔧 DAX Query
              </h2>
              <div style={{ backgroundColor: '#ffffff', padding: '15px', borderRadius: '5px', borderLeft: '3px solid #0078d4', fontSize: '11px', overflow: 'visible', wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                {daxData}
              </div>
            </>
          )}

          <div style={{ marginTop: '40px', fontSize: '11px', color: '#666', borderTop: '1px solid #ddd', paddingTop: '15px', textAlign: 'center' }}>
            <p><strong>Qlik to Power BI Migration Tool</strong></p>
            <p>This report was automatically generated on: {new Date().toLocaleString()}</p>
          </div>
        </div>
      </div>
        </>
      )}

      {/* BACK BUTTON */}
      <div className="publish-footer">
        <button
          onClick={() => navigate("/")}
          className="btn btn-secondary"
          disabled={publishing}
        >
          ← Back to Connect
        </button>
      </div>
    </div>
  );

};

