// import "./MultiMigrationPage.css";
// import { useLocation, useNavigate } from "react-router-dom";
// import { useState } from "react";
// import { useWizard } from "../context/WizardContext";

// interface SelectedTableData {
//   name: string;
//   rows: any[];
//   summary: any;
// }

// interface SelectedTable {
//   name: string;
//   data: SelectedTableData;
// }

// export default function MultiMigrationPage() {
//   const { state } = useLocation() as any;
//   const navigate = useNavigate();
//   const { stopTimer, startTimer } = useWizard();

//   const appId = state?.appId || sessionStorage.getItem("appSelected");
//   const appName = state?.appName || sessionStorage.getItem("appName") || appId;
//   const selectedTables: SelectedTable[] = state?.selectedTables || [];

//   const [options, setOptions] = useState<{
//     combined: boolean;
//     separateDatasets: boolean;
//   }>({
//     combined: true,
//     separateDatasets: false,
//   });

//   const [isProcessing, setIsProcessing] = useState(false);
//   const [showError, setShowError] = useState(false);
//   const [errorMessage, setErrorMessage] = useState("");

//   // Check if we have valid data
//   if (!selectedTables || selectedTables.length === 0) {
//     return (
//       <div className="multi-migrate-wrap">
//         <div className="error-container">
//           <h2>🚫 No Tables Selected</h2>
//           <p>Please go back and select at least one table to migrate.</p>
//           <button onClick={() => navigate("/summary")}>← Back to Summary</button>
//         </div>
//       </div>
//     );
//   }

//   // Helper: Save data to sessionStorage (for Continue button)
//   const saveDataToSessionStorage = () => {
//     try {
//       // Save lightweight metadata only (do NOT serialize full row arrays)
//       const meta = selectedTables.map((t) => ({ name: t.name, rowCount: t.data?.rows?.length || 0 }));
//       sessionStorage.setItem("migration_selected_tables_meta", JSON.stringify(meta));
//       sessionStorage.setItem("migration_appName", appName);
//       sessionStorage.setItem("migration_table_count", String(selectedTables.length));

//       // Save CSV data if combined export is selected (try/catch to avoid quota exceptions)
//       if (options.combined) {
//         selectedTables.forEach((table, idx) => {
//           const rows = table.data?.rows || [];
//           if (rows.length > 0) {
//             const headers = Object.keys(rows[0]);
//             const csv = [
//               headers.join(","),
//               ...rows.map((r: any) => headers.map((h) => `"${r[h] ?? ""}"`).join(",")),
//             ].join("\n");

//             try {
//               sessionStorage.setItem(`migration_csv_${idx}`, csv);
//             } catch (e) {
//               console.warn(`sessionStorage quota exceeded while saving migration_csv_${idx}; skipping persistent save.`, e);
//               sessionStorage.removeItem(`migration_csv_${idx}`);
//             }
//           }
//         });

//         sessionStorage.setItem("migration_has_csv", "true");
//       }

//       sessionStorage.setItem("exportComplete", "true");
//     } catch (e) {
//       console.error("Error saving to sessionStorage:", e);
//     }
//   };

//   // Helper: Create DAX content for each table
//   const generateDAXContent = (tableName: string, rows: any[]) => {
//     if (!rows.length) return "";

//     const cols = Object.keys(rows[0]);
//     const daxLines = [] as string[];
//     daxLines.push(`-- DAX export skeleton for table: ${tableName}`);
//     daxLines.push(`-- Columns:`);
//     cols.forEach((c) => daxLines.push(`-- ${c}`));
//     daxLines.push(`\n-- Sample measure`);
//     daxLines.push(`[${tableName} Count] = COUNTROWS('${tableName}')`);

//     return daxLines.join("\n");
//   };

//   // Helper: Migrate tables to Power BI
//   const handleMigrate = async () => {
//     if (selectedTables.length === 0) {
//       setErrorMessage("No tables selected for migration");
//       setShowError(true);
//       return;
//     }

//     setIsProcessing(true);
//     setShowError(false);

//     try {
//       // Process each table
//       for (let idx = 0; idx < selectedTables.length; idx++) {
//         const tableData = selectedTables[idx].data;
//         const tableName = tableData.name;
//         const rows = tableData.rows || [];

//         if (rows.length === 0) {
//           console.warn(`Skipping ${tableName} - no data`);
//           continue;
//         }

//         // Create FormData for this table
//         const formData = new FormData();

//         // CSV file
//         const headers = Object.keys(rows[0]);
//         const csvContent = [
//           headers.join(","),
//           ...rows.map((r: any) =>
//             headers.map((h) => `"${r[h] ?? ""}"`).join(",")
//           ),
//         ].join("\n");

//         const csvBlob = new Blob([csvContent], {
//           type: "text/csv;charset=utf-8;",
//         });
//         formData.append("csv_file", csvBlob, `${tableName}.csv`);

//         // DAX file (if combined option selected)
//         if (options.combined) {
//           const daxContent = generateDAXContent(tableName, rows);
//           const daxBlob = new Blob([daxContent], { type: "text/plain" });
//           formData.append("dax_file", daxBlob, `${tableName}.dax`);
//         }

//         // Metadata
//         formData.append("meta_app_name", appName);
//         formData.append("meta_table", tableName);
//         formData.append("has_csv", "true");
//         formData.append("has_dax", options.combined ? "true" : "false");

//         // Send to backend
//         const response = await fetch("http://127.0.0.1:8000/powerbi/process", {
        
//         // const response = await fetch("https://qlikaiv2.onrender.com/powerbi/process", {
//           method: "POST",
//           body: formData,
//           credentials: "include",
//         });

//         if (!response.ok) {
//           const errorData = await response.json();
//           throw new Error(errorData.detail || `Failed to migrate ${tableName}`);
//         }

//         const result = await response.json();
//         console.log(`✅ Table ${tableName} migrated:`, result);
//       }

//       // All tables migrated successfully
//       saveDataToSessionStorage();
//       stopTimer?.("/multi-migrate");
//       startTimer?.("/publish");

//       // Navigate to publish/success page
//       navigate("/publish", {
//         state: {
//           success: true,
//           appId,
//           appName,
//           migratedTables: selectedTables.map((t) => t.name),
//           count: selectedTables.length,
//         },
//       });
//     } catch (error) {
//       const errorMsg =
//         error instanceof Error ? error.message : "Failed to migrate tables";
//       setErrorMessage(errorMsg);
//       setShowError(true);
//       console.error("Migration error:", error);
//     } finally {
//       setIsProcessing(false);
//     }
//   };

//   return (
//     <div className="multi-migrate-wrap">
//       <div className="multi-migrate-container">
//         <h2>📦 Prepare Tables for Migration</h2>

//         {/* Summary */}
//         <div className="summary-section">
//           <div className="summary-item">
//             <span className="label">App:</span>
//             <span className="value">{appName}</span>
//           </div>
//           <div className="summary-item">
//             <span className="label">Tables Selected:</span>
//             <span className="value badge">{selectedTables.length}</span>
//           </div>
//           <div className="summary-item">
//             <span className="label">Total Records:</span>
//             <span className="value">
//               {selectedTables.reduce((sum, t) => sum + (t.data?.rows?.length || 0), 0)}
//             </span>
//           </div>
//         </div>

//         {/* Error Alert */}
//         {showError && (
//           <div className="error-alert">
//             <div className="error-title">❌ Migration Error</div>
//             <div className="error-message">{errorMessage}</div>
//           </div>
//         )}

//         {/* Export Options */}
//         <div className="options-section">
//           <h3>Export Format</h3>
//           <div className="option-group">
//             <label className="checkbox-label">
//               <input
//                 type="checkbox"
//                 checked={options.combined}
//                 onChange={(e) =>
//                   setOptions((prev) => ({
//                     ...prev,
//                     combined: e.target.checked,
//                   }))
//                 }
//               />
//               <span>📊 Combined Export (CSV + DAX)</span>
//             </label>
//             <p className="option-description">
//               Export tables with DAX measures for richer visualizations
//             </p>
//           </div>

//           <div className="option-group">
//             <label className="checkbox-label">
//               <input
//                 type="checkbox"
//                 checked={options.separateDatasets}
//                 onChange={(e) =>
//                   setOptions((prev) => ({
//                     ...prev,
//                     separateDatasets: e.target.checked,
//                   }))
//                 }
//               />
//               <span>🗂️ Separate Datasets</span>
//             </label>
//             <p className="option-description">
//               Create separate Power BI datasets for each table (default: single dataset)
//             </p>
//           </div>
//         </div>

//         {/* Tables List */}
//         <div className="tables-list-section">
//           <h3>Selected Tables ({selectedTables.length})</h3>
//           <div className="tables-grid">
//             {selectedTables.map((table, idx) => (
//               <div key={idx} className="table-card">
//                 <div className="table-header">
//                   <h4>{table.name}</h4>
//                   <span className="row-count">
//                     {table.data?.rows?.length || 0} rows
//                   </span>
//                 </div>
//                 <div className="table-details">
//                   <div className="detail-item">
//                     <span className="detail-label">Columns:</span>
//                     <span className="detail-value">
//                       {table.data?.rows?.length > 0
//                         ? Object.keys(table.data.rows[0]).length
//                         : 0}
//                     </span>
//                   </div>
//                 </div>
//               </div>
//             ))}
//           </div>
//         </div>

//         {/* Action Buttons */}
//         <div className="action-buttons">
//           <button
//             className="btn btn-secondary"
//             onClick={() => {
//               stopTimer?.("/multi-migrate");
//               navigate("/summary");
//             }}
//             disabled={isProcessing}
//           >
//             ← Back to Summary
//           </button>
//           <button
//             className="btn btn-primary"
//             onClick={handleMigrate}
//             disabled={isProcessing}
//           >
//             {isProcessing ? (
//               <>
//                 <span className="spinner"></span> Migrating...
//               </>
//             ) : (
//               <>📤 Migrate to Power BI</>
//             )}
//           </button>
//         </div>

//         {/* Status */}
//         {isProcessing && (
//           <div className="processing-status">
//             <p>⏳ Processing {selectedTables.length} tables...</p>
//             <p className="note">
//               Please wait, this may take a minute depending on data size
//             </p>
//           </div>
//         )}
//       </div>
//     </div>
//   );
// }



import "./MultiMigrationPage.css";
import { useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { useWizard } from "../context/WizardContext";

interface SelectedTableData {
  name: string;
  rows: any[];
  summary: any;
}

interface SelectedTable {
  name: string;
  data: SelectedTableData;
}

export default function MultiMigrationPage() {
  const { state } = useLocation() as any;
  const navigate = useNavigate();
  const { stopTimer, startTimer } = useWizard();

  const appId = state?.appId || sessionStorage.getItem("appSelected");
  const appName = state?.appName || sessionStorage.getItem("appName") || appId;
  const selectedTables: SelectedTable[] = state?.selectedTables || [];

  const [options, setOptions] = useState<{
    combined: boolean;
    separateDatasets: boolean;
  }>({
    combined: true,
    separateDatasets: false,
  });

  const [isProcessing, setIsProcessing] = useState(false);
  const [showError, setShowError] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  // Check if we have valid data
  if (!selectedTables || selectedTables.length === 0) {
    return (
      <div className="multi-migrate-wrap">
        <div className="error-container">
          <h2>🚫 No Tables Selected</h2>
          <p>Please go back and select at least one table to migrate.</p>
          <button onClick={() => navigate("/summary")}>← Back to Summary</button>
        </div>
      </div>
    );
  }

  // Helper: Save data to sessionStorage (for Continue button)
  const saveDataToSessionStorage = () => {
    try {
      // Save lightweight metadata only (do NOT serialize full row arrays)
      const meta = selectedTables.map((t) => ({ name: t.name, rowCount: t.data?.rows?.length || 0 }));
      sessionStorage.setItem("migration_selected_tables_meta", JSON.stringify(meta));
      sessionStorage.setItem("migration_appName", appName);
      sessionStorage.setItem("migration_table_count", String(selectedTables.length));

      // Save CSV data if combined export is selected (try/catch to avoid quota exceptions)
      if (options.combined) {
        selectedTables.forEach((table, idx) => {
          const rows = table.data?.rows || [];
          if (rows.length > 0) {
            const headers = Object.keys(rows[0]);
            const csv = [
              headers.join(","),
              ...rows.map((r: any) => headers.map((h) => `"${r[h] ?? ""}"`).join(",")),
            ].join("\n");

            try {
              sessionStorage.setItem(`migration_csv_${idx}`, csv);
            } catch (e) {
              console.warn(`sessionStorage quota exceeded while saving migration_csv_${idx}; skipping persistent save.`, e);
              sessionStorage.removeItem(`migration_csv_${idx}`);
            }
          }
        });

        sessionStorage.setItem("migration_has_csv", "true");
      }

      sessionStorage.setItem("exportComplete", "true");
    } catch (e) {
      console.error("Error saving to sessionStorage:", e);
    }
  };

  // Helper: Create DAX content for each table
  const generateDAXContent = (tableName: string, rows: any[]) => {
    if (!rows.length) return "";

    const cols = Object.keys(rows[0]);
    const daxLines = [] as string[];
    daxLines.push(`-- DAX export skeleton for table: ${tableName}`);
    daxLines.push(`-- Columns:`);
    cols.forEach((c) => daxLines.push(`-- ${c}`));
    daxLines.push(`\n-- Sample measure`);
    daxLines.push(`[${tableName} Count] = COUNTROWS('${tableName}')`);

    return daxLines.join("\n");
  };

  // Prevent unused warning (may be used in future)
  void generateDAXContent;

  // Helper: Migrate tables to Power BI
  const handleMigrate = async () => {
    if (selectedTables.length === 0) {
      setErrorMessage("No tables selected for migration");
      setShowError(true);
      return;
    }

    setIsProcessing(true);
    setShowError(false);

    try {
      // ── Build the batch payload in-memory ──────────────────────────────
      // Do NOT call /powerbi/process per-table — that creates separate datasets
      // and requires its own auth session on each call.
      // Instead, carry the full table data to PublishPage which calls
      // /powerbi/process-batch → single dataset, single authenticated call.
      const batchTables: { name: string; rows: any[] }[] = [];

      for (const tableEntry of selectedTables) {
        const tableData = tableEntry.data;
        const tableName = tableData.name;
        const rows = tableData.rows || [];

        if (rows.length === 0) {
          console.warn(`Skipping ${tableName} - no data`);
          continue;
        }

        batchTables.push({ name: tableName, rows });
        console.log(`📦 Queued '${tableName}': ${rows.length} rows`);
      }

      if (batchTables.length === 0) {
        throw new Error("No tables with data to migrate");
      }

      // Save lightweight metadata to sessionStorage (fallback for PublishPage)
      saveDataToSessionStorage();
      stopTimer?.("/multi-migrate");
      startTimer?.("/publish");

      // Navigate to PublishPage with the full in-memory table data.
      // PublishPage reads state.batchTables and calls /powerbi/process-batch.
      navigate("/publish", {
        state: {
          success: true,
          appId,
          appName,
          migratedTables: selectedTables.map((t) => t.name),
          count: selectedTables.length,
          tableCount: batchTables.length,
          // Full rows in-memory — passed directly to process-batch
          batchTables,
          exportOptions: {
            combined: options.combined,
            separate: options.separateDatasets,
            separateDatasets: options.separateDatasets,
          },
        },
      });
    } catch (error) {
      const errorMsg =
        error instanceof Error ? error.message : "Failed to prepare tables";
      setErrorMessage(errorMsg);
      setShowError(true);
      console.error("Migration error:", error);
    } finally {
      setIsProcessing(false);
    }
  };

  return (
    <div className="multi-migrate-wrap">
      <div className="multi-migrate-container">
        <h2>📦 Prepare Tables for Migration</h2>

        {/* Summary */}
        <div className="summary-section">
          <div className="summary-item">
            <span className="label">App:</span>
            <span className="value">{appName}</span>
          </div>
          <div className="summary-item">
            <span className="label">Tables Selected:</span>
            <span className="value badge">{selectedTables.length}</span>
          </div>
          <div className="summary-item">
            <span className="label">Total Records:</span>
            <span className="value">
              {selectedTables.reduce((sum, t) => sum + (t.data?.rows?.length || 0), 0)}
            </span>
          </div>
        </div>

        {/* Error Alert */}
        {showError && (
          <div className="error-alert">
            <div className="error-title">❌ Migration Error</div>
            <div className="error-message">{errorMessage}</div>
          </div>
        )}

        {/* Export Options */}
        <div className="options-section">
          <h3>Export Format</h3>
          <div className="option-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={options.combined}
                onChange={(e) =>
                  setOptions((prev) => ({
                    ...prev,
                    combined: e.target.checked,
                  }))
                }
              />
              <span>📊 Combined Export (CSV + DAX)</span>
            </label>
            <p className="option-description">
              Export tables with DAX measures for richer visualizations
            </p>
          </div>

          <div className="option-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={options.separateDatasets}
                onChange={(e) =>
                  setOptions((prev) => ({
                    ...prev,
                    separateDatasets: e.target.checked,
                  }))
                }
              />
              <span>🗂️ Separate Datasets</span>
            </label>
            <p className="option-description">
              Create separate Power BI datasets for each table (default: single dataset)
            </p>
          </div>
        </div>

        {/* Tables List */}
        <div className="tables-list-section">
          <h3>Selected Tables ({selectedTables.length})</h3>
          <div className="tables-grid">
            {selectedTables.map((table, idx) => (
              <div key={idx} className="table-card">
                <div className="table-header">
                  <h4>{table.name}</h4>
                  <span className="row-count">
                    {table.data?.rows?.length || 0} rows
                  </span>
                </div>
                <div className="table-details">
                  <div className="detail-item">
                    <span className="detail-label">Columns:</span>
                    <span className="detail-value">
                      {table.data?.rows?.length > 0
                        ? Object.keys(table.data.rows[0]).length
                        : 0}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="action-buttons">
          <button
            className="btn btn-secondary"
            onClick={() => {
              stopTimer?.("/multi-migrate");
              navigate("/summary");
            }}
            disabled={isProcessing}
          >
            ← Back to Summary
          </button>
          <button
            className="btn btn-primary"
            onClick={handleMigrate}
            disabled={isProcessing}
          >
            {isProcessing ? (
              <>
                <span className="spinner"></span> Migrating...
              </>
            ) : (
              <>📤 Migrate to Power BI</>
            )}
          </button>
        </div>

        {/* Status */}
        {isProcessing && (
          <div className="processing-status">
            <p>⏳ Processing {selectedTables.length} tables...</p>
            <p className="note">
              Please wait, this may take a minute depending on data size
            </p>
          </div>
        )}
      </div>
    </div>
  );
}