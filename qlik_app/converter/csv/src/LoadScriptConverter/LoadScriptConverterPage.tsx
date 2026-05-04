// import { useState, useEffect, useRef } from 'react';
// import './LoadScriptConverterPage.css';

// interface LogEntry {
//   timestamp: string;
//   level: string;
//   message: string;
//   phase: number;
//   data: Record<string, any>;
// }

// interface ConversionStatus {
//   session_id: string;
//   status: string;
//   progress: number;
//   current_phase: number;
//   duration_seconds: number;
//   error_message?: string;
//   data_summary: {
//     tables_count: number;
//     fields_count: number;
//     loadscript_length: number;
//     m_query_length: number;
//   };
// }

// interface Table {
//   name: string;
//   selected: boolean;
// }

// export default function LoadScriptConverterPage() {
//   const logsEndRef = useRef<HTMLDivElement>(null);

//   // Step 1: Get App ID
//   const [appId, setAppId] = useState('');
  
//   // Step 2: Select tables
//   const [tables, setTables] = useState<Table[]>([]);
//   const [loadingTables, setLoadingTables] = useState(false);
//   const [hasCheckedTables, setHasCheckedTables] = useState(false);
  
//   // Step 3: Conversion
//   const [sessionId, setSessionId] = useState('');
//   const [status, setStatus] = useState<'idle' | 'selecting' | 'running' | 'completed' | 'error'>('idle');
//   const [logs, setLogs] = useState<LogEntry[]>([]);
//   const [progress, setProgress] = useState(0);
//   const [conversionStatus, setConversionStatus] = useState<ConversionStatus | null>(null);
//   const [error, setError] = useState('');

//   // Auto-scroll logs to bottom
//   useEffect(() => {
//     logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
//   }, [logs]);

//   // Fetch available tables from app
//   const fetchTablesList = async () => {
//     if (!appId.trim()) {
//       setError('Please enter an App ID');
//       return;
//     }

//     setLoadingTables(true);
//     setError('');

//     try {
//       const response = await fetch(
//         `http://localhost:8000/applications/${appId}/tables`,
//         { method: 'GET' }
//       );

//       if (!response.ok) {
//         throw new Error(`Failed to fetch tables: ${response.status}`);
//       }

//       const data = await response.json();
//       const tableList = (data.tables || []).map((t: any) => ({
//         name: typeof t === 'string' ? t : t.name,
//         selected: false
//       }));

//       setTables(tableList);
//       setStatus('selecting');
//       setHasCheckedTables(true);
//     } catch (err: any) {
//       setError(`Error fetching tables: ${err.message}`);
//     } finally {
//       setLoadingTables(false);
//     }
//   };

//   const toggleTableSelection = (tableName: string) => {
//     setTables(tables.map(t => 
//       t.name === tableName ? { ...t, selected: !t.selected } : t
//     ));
//   };

//   const selectAllTables = () => {
//     setTables(tables.map(t => ({ ...t, selected: true })));
//   };

//   const deselectAllTables = () => {
//     setTables(tables.map(t => ({ ...t, selected: false })));
//   };

//   const startSession = async () => {
//     try {
//       const response = await fetch('http://localhost:8000/api/migration/conversion/start-session', {
//         method: 'POST'
//       });
      
//       if (!response.ok) {
//         throw new Error(`Backend returned ${response.status}`);
//       }
      
//       const data = await response.json();
//       if (data.session_id) {
//         setSessionId(data.session_id);
//         return data.session_id;
//       }
//       throw new Error('No session_id in response');
//     } catch (err: any) {
//       setError(`Failed to start session: ${err.message}`);
//     }
//     return null;
//   };

//   const pollLogs = async (sessionId: string) => {
//     try {
//       const response = await fetch(
//         `http://localhost:8000/api/migration/conversion/logs?session_id=${sessionId}&limit=100`
//       );
//       const data = await response.json();
//       if (data.logs) {
//         setLogs(data.logs);
//       }
//     } catch (err) {
//       console.error('Failed to fetch logs:', err);
//     }
//   };

//   const pollStatus = async (sessionId: string) => {
//     try {
//       const response = await fetch(
//         `http://localhost:8000/api/migration/conversion/status?session_id=${sessionId}`
//       );
//       const data = await response.json();
//       if (data.session_id) {
//         setConversionStatus(data);
//         setProgress(data.progress);
//       }
//     } catch (err) {
//       console.error('Failed to fetch status:', err);
//     }
//   };

//   const startConversion = async () => {
//     const selectedTables = tables.filter(t => t.selected).map(t => t.name);
    
//     if (selectedTables.length === 0) {
//       setError('Please select at least one table');
//       return;
//     }

//     setError('');
//     setLogs([]);
//     setProgress(0);
//     setStatus('running');

//     try {
//       const newSessionId = await startSession();
//       if (!newSessionId) {
//         throw new Error('Failed to create conversion session');
//       }

//       setSessionId(newSessionId);
//       setLogs([
//         {
//           timestamp: new Date().toISOString(),
//           level: 'INFO',
//           message: `Starting conversion for app: ${appId} | Tables: ${selectedTables.join(', ')}`,
//           phase: 0,
//           data: {}
//         }
//       ]);

//       // Start polling
//       const pollInterval = setInterval(async () => {
//         await pollLogs(newSessionId);
//         await pollStatus(newSessionId);
//       }, 500);

//       // Execute the pipeline
//       const response = await fetch(
//         `http://localhost:8000/api/migration/full-pipeline-tracked?app_id=${appId}&session_id=${newSessionId}`,
//         { method: 'POST' }
//       );

//       const result = await response.json();

//       clearInterval(pollInterval);

//       if (response.ok) {
//         setStatus('completed');
//         setProgress(100);

//         // Final log update
//         await pollLogs(newSessionId);
//         await pollStatus(newSessionId);
//       } else {
//         setStatus('error');
//         setError(result.detail || 'Conversion failed');
//       }
//     } catch (err: any) {
//       setStatus('error');
//       setError(err.message || 'An error occurred during conversion');
//       console.error('Conversion error:', err);
//     }
//   };

//   const downloadFile = async (format: string, tableName?: string) => {
//     if (!sessionId) return;

//     try {
//       let url = `http://localhost:8000/api/migration/download-file?session_id=${sessionId}&format=${format}`;
//       if (tableName && tableName !== 'combined') {
//         url += `&table=${encodeURIComponent(tableName)}`;
//       }

//       const response = await fetch(url, { method: 'POST' });

//       if (!response.ok) throw new Error('Download failed');

//       const blob = await response.blob();
//       const filename = `mquery_${tableName || 'combined'}_${new Date().toISOString().split('T')[0]}.${format === 'm' ? 'm' : format}`;
//       const downloadUrl = window.URL.createObjectURL(blob);
//       const a = document.createElement('a');
//       a.href = downloadUrl;
//       a.download = filename;
//       document.body.appendChild(a);
//       a.click();
//       window.URL.revokeObjectURL(downloadUrl);
//       document.body.removeChild(a);
//     } catch (err: any) {
//       setError(`Download failed: ${err.message}`);
//     }
//   };

//   const downloadDualZip = async () => {
//     if (!sessionId) return;

//     try {
//       const response = await fetch(
//         `http://localhost:8000/api/migration/download-dual-zip?session_id=${sessionId}`,
//         { method: 'POST' }
//       );

//       if (!response.ok) throw new Error('Download failed');

//       const blob = await response.blob();
//       const url = window.URL.createObjectURL(blob);
//       const a = document.createElement('a');
//       a.href = url;
//       a.download = `mquery_combined_${new Date().toISOString().split('T')[0]}.zip`;
//       document.body.appendChild(a);
//       a.click();
//       window.URL.revokeObjectURL(url);
//       document.body.removeChild(a);
//     } catch (err) {
//       setError(`Failed to download ZIP file: ${err}`);
//     }
//   };

//   const getPhaseIcon = (level: string) => {
//     switch (level) {
//       case 'SUCCESS':
//         return '✅';
//       case 'ERROR':
//         return '❌';
//       case 'WARNING':
//         return '⚠️';
//       default:
//         return '📍';
//     }
//   };

//   return (
//     <div className="loadscript-converter-page">
//       <div className="converter-container">
//         {/* Header */}
//         <div className="converter-header">
//           <h1>🔄 Qlik LoadScript to Power BI M Query</h1>
//           <p>Convert Qlik Cloud LoadScript to PowerBI M Query with real-time progress tracking</p>
//         </div>

//         {/* Step 1: Enter App ID */}
//         {status === 'idle' && (
//           <div className="converter-section input-section">
//             <h2>📋 Step 1: Enter Qlik App ID</h2>
//             <div className="input-group">
//               <input
//                 type="text"
//                 value={appId}
//                 onChange={(e) => setAppId(e.target.value)}
//                 placeholder="Enter your Qlik App ID (e.g., abcd1234-ef56-7890-ab12-cdef34567890)"
//                 disabled={loadingTables}
//                 className="app-id-input"
//               />
//               <button
//                 onClick={fetchTablesList}
//                 disabled={loadingTables || !appId.trim()}
//                 className="start-btn"
//               >
//                 {loadingTables ? (
//                   <>
//                     <span className="spinner"></span> Loading Tables...
//                   </>
//                 ) : (
//                   '► Load Tables'
//                 )}
//               </button>
//             </div>
//           </div>
//         )}

//         {/* Step 2: Select Tables */}
//         {hasCheckedTables && status === 'selecting' && tables.length > 0 && (
//           <div className="converter-section tables-section">
//             <h2>📊 Step 2: Select Tables ({tables.filter(t => t.selected).length}/{tables.length})</h2>
//             <div className="table-controls">
//               <button onClick={selectAllTables} className="select-btn">✓ Select All</button>
//               <button onClick={deselectAllTables} className="select-btn">✗ Deselect All</button>
//             </div>
//             <div className="tables-list">
//               {tables.map((table, idx) => (
//                 <div key={idx} className="table-checkbox-item">
//                   <input
//                     type="checkbox"
//                     id={`table-${idx}`}
//                     checked={table.selected}
//                     onChange={() => toggleTableSelection(table.name)}
//                   />
//                   <label htmlFor={`table-${idx}`}>{table.name}</label>
//                 </div>
//               ))}
//             </div>
//             <button
//               onClick={startConversion}
//               disabled={tables.filter(t => t.selected).length === 0}
//               className="start-btn"
//               style={{ marginTop: '20px', width: '100%' }}
//             >
//               ► Start Conversion
//             </button>
//           </div>
//         )}

//         {/* Error handling for table loading */}
//         {hasCheckedTables && tables.length === 0 && !loadingTables && (
//           <div className="converter-section error-section">
//             <div className="error-box">
//               <span className="error-icon">⚠️</span>
//               <span className="error-message">No tables found in this app. Try another App ID.</span>
//               <button onClick={() => { setStatus('idle'); setHasCheckedTables(false); }} className="retry-btn">
//                 Try Again
//               </button>
//             </div>
//           </div>
//         )}

//         {/* Progress Bar */}
//         {status === 'running' && (
//           <div className="converter-section progress-section">
//             <h2>⏱️ Progress</h2>
//             <div className="progress-bar-container">
//               <div className="progress-bar">
//                 <div
//                   className={`progress-fill ${status}`}
//                   style={{ width: `${progress}%` }}
//                 ></div>
//               </div>
//               <div className="progress-text">
//                 {progress}% Complete
//                 {conversionStatus && (
//                   <span className="current-phase">
//                     {' '}
//                     (Phase {conversionStatus.current_phase}) ⏱️ {conversionStatus.duration_seconds.toFixed(1)}s
//                   </span>
//                 )}
//               </div>
//             </div>
//           </div>
//         )}

//         {/* Status Summary */}
//         {conversionStatus && (
//           <div className="converter-section status-summary">
//             <h3>📊 Conversion Summary</h3>
//             <div className="summary-grid">
//               <div className="summary-item">
//                 <span className="label">Tables</span>
//                 <span className="value">{conversionStatus.data_summary.tables_count}</span>
//               </div>
//               <div className="summary-item">
//                 <span className="label">Fields</span>
//                 <span className="value">{conversionStatus.data_summary.fields_count}</span>
//               </div>
//               <div className="summary-item">
//                 <span className="label">LoadScript Size</span>
//                 <span className="value">{(conversionStatus.data_summary.loadscript_length / 1024).toFixed(2)} KB</span>
//               </div>
//               <div className="summary-item">
//                 <span className="label">M Query Size</span>
//                 <span className="value">{(conversionStatus.data_summary.m_query_length / 1024).toFixed(2)} KB</span>
//               </div>
//             </div>
//           </div>
//         )}

//         {/* Logs Section */}
//         <div className="converter-section logs-section">
//           <h2>📝 Live Conversion Logs</h2>
//           <div className="logs-container">
//             <div className="logs-content">
//               {logs.length === 0 ? (
//                 <div className="empty-logs">No logs yet. Start a conversion to see progress...</div>
//               ) : (
//                 logs.map((log, idx) => (
//                   <div key={idx} className={`log-entry log-${log.level.toLowerCase()}`}>
//                     <span className="log-icon">{getPhaseIcon(log.level)}</span>
//                     <span className="log-timestamp">{new Date(log.timestamp).toLocaleTimeString()}</span>
//                     <span className="log-message">{log.message}</span>
//                   </div>
//                 ))
//               )}
//               <div ref={logsEndRef} />
//             </div>
//           </div>
//         </div>

//         {/* Error Display */}
//         {error && (
//           <div className="converter-section error-section">
//             <div className="error-box">
//               <span className="error-icon">❌</span>
//               <span className="error-message">{error}</span>
//             </div>
//           </div>
//         )}

//         {/* Download Section */}
//         {status === 'completed' && (
//           <div className="converter-section download-section">
//             <h2>💾 Step 3: Download Results</h2>
//             <div className="download-info">
//               <p>Selected Tables: <strong>{tables.filter(t => t.selected).map(t => t.name).join(', ') || 'All'}</strong></p>
//             </div>
            
//             {/* Single Table Download */}
//             {tables.filter(t => t.selected).length === 1 && (
//               <div className="download-group">
//                 <h3>📋 {tables.find(t => t.selected)?.name} - Download Options</h3>
//                 <p className="download-hint">Download M Query for the selected table</p>
//                 <div className="download-buttons">
//                   <button onClick={() => downloadFile('m', tables.find(t => t.selected)?.name)} className="download-btn m-btn">
//                     📥 Download .m
//                   </button>
//                   <button onClick={() => downloadFile('pq', tables.find(t => t.selected)?.name)} className="download-btn pq-btn">
//                     📥 Download .pq
//                   </button>
//                   <button onClick={() => downloadFile('txt', tables.find(t => t.selected)?.name)} className="download-btn txt-btn">
//                     📥 Download .txt
//                   </button>
//                 </div>
//               </div>
//             )}

//             {/* Combined Download - Only show for multiple tables */}
//             {tables.filter(t => t.selected).length > 1 && (
//               <>
//                 <div className="download-group">
//                   <h3>📦 Combined M Query</h3>
//                   <p className="download-hint">Download M Query for all selected tables in one file</p>
//                   <div className="download-buttons">
//                     <button onClick={() => downloadFile('pq')} className="download-btn pq-btn">
//                       📥 Download Combined .pq
//                     </button>
//                     <button onClick={() => downloadFile('txt')} className="download-btn txt-btn">
//                       📥 Download Combined .txt
//                     </button>
//                     <button onClick={() => downloadFile('m')} className="download-btn m-btn">
//                       📥 Download Combined .m
//                     </button>
//                     <button onClick={downloadDualZip} className="download-btn zip-btn">
//                       📦 Download Combined .zip
//                     </button>
//                   </div>
//                 </div>

//                 {/* Per-Table Downloads */}
//                 <div className="download-group">
//                   <h3>🔍 Per-Table Downloads</h3>
//                   <p className="download-hint">Download individual M Query for each selected table</p>
//                   <div className="tables-download-list">
//                     {tables.filter(t => t.selected).map((table, idx) => (
//                       <div key={idx} className="table-download-item">
//                         <span className="table-name">📋 {table.name}</span>
//                         <div className="table-download-buttons">
//                           <button 
//                             onClick={() => downloadFile('m', table.name)} 
//                             className="download-btn sm-btn m-btn"
//                             title={`Download M Query for ${table.name}`}
//                           >
//                             .m
//                           </button>
//                           <button 
//                             onClick={() => downloadFile('pq', table.name)} 
//                             className="download-btn sm-btn pq-btn"
//                             title={`Download Power Query for ${table.name}`}
//                           >
//                             .pq
//                           </button>
//                           <button 
//                             onClick={() => downloadFile('txt', table.name)} 
//                             className="download-btn sm-btn txt-btn"
//                             title={`Download Documentation for ${table.name}`}
//                           >
//                             .txt
//                           </button>
//                         </div>
//                       </div>
//                     ))}
//                   </div>
//                 </div>
//               </>
//             )}
//           </div>
//         )}

//         {/* Usage Instructions */}
//         <div className="converter-section info-section">
//           <h2>📖 How to Use</h2>
//           <ol className="instructions-list">
//             <li>Enter your Qlik Cloud App ID</li>
//             <li>Click "Load Tables" to see available tables</li>
//             <li>Select the tables you want to convert</li>
//             <li>Click "Start Conversion" and watch the real-time progress</li>
//             <li>Once complete, download the M Query files (combined or individual tables)</li>
//             <li>Paste the M Query into Power BI Advanced Editor</li>
//           </ol>
//         </div>
//       </div>
//     </div>
//   );
// }
