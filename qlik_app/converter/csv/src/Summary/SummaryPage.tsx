// import "./SummaryPage.css";
// import { useEffect, useMemo, useRef, useState } from "react";
// import { useLocation, useNavigate } from "react-router-dom";
// import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
// // import {
// //   fetchAlteryxBrdHtml,
// //   fetchAlteryxWorkflowAnalysis,
// // } from "../api/alteryxApi";
// import {
//   fetchAlteryxBrdHtml,
//   fetchAlteryxWorkflowAnalysis,
//   publishAlteryxMQuery,
// } from "../api/alteryxApi";
// import type { AlteryxWorkflow } from "../api/alteryxApi";
// import { useWizard } from "../context/WizardContext";

// // ─── Constants ───────────────────────────────────────────────────────────────

// const DEFAULT_SHAREPOINT_URL = sessionStorage.getItem("alteryx_sharepoint_url") || "";
// const DEFAULT_FILE_NAME = sessionStorage.getItem("alteryx_file_name") || "";

// // ─── Types ────────────────────────────────────────────────────────────────────

// type SummaryTab = "sourceTypes" | "summary" | "brd" | "diagram";
// type SourceType = "database" | "scripts" | "csv";

// // ─── Tab Config ───────────────────────────────────────────────────────────────

// const TABS: Array<{ id: SummaryTab; label: string; icon: string }> = [
//   { id: "sourceTypes", label: "Source Types", icon: "" },
//   { id: "summary", label: "Summary", icon: "" },
//   { id: "brd", label: "App BRD", icon: "" },
//   { id: "diagram", label: "Workflow Diagram", icon: "" },
// ];

// // ─── Helpers ──────────────────────────────────────────────────────────────────

// function readStoredWorkflow(): AlteryxWorkflow | null {
//   const raw = sessionStorage.getItem("alteryx_selected_workflow");
//   if (!raw) return null;
//   try {
//     return JSON.parse(raw) as AlteryxWorkflow;
//   } catch {
//     return null;
//   }
// }

// function safePercent(value: number, total: number) {
//   return total > 0 ? Math.round((value / total) * 100) : 0;
// }

// function buildPieSlices(workflow: AlteryxWorkflow | null) {
//   const counts = new Map<string, number>();
//   (workflow?.toolTypes || []).forEach((tool) => {
//     const shortName = tool.split(".").filter(Boolean).slice(-1)[0] || tool;
//     counts.set(shortName, (counts.get(shortName) || 0) + 1);
//   });
//   const entries = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 7);
//   const unsupported = workflow?.unsupportedToolCount || 0;
//   if (unsupported) entries.push(["Needs remediation", unsupported]);
//   if (!entries.length) return [["Workflow", 1] as [string, number]];
//   return entries;
// }

// function workflowSourceName(workflow: AlteryxWorkflow | null, fallback = "") {
//   const source = (workflow?.dataSources || []).find((item: any) => item?.name || item?.fileName || item?.path);
//   if (!source) return fallback;
//   return source.fileName || source.name || String(source.path || "").split(/[\\/]/).pop() || fallback;
// }

// function workflowSourcePath(workflow: AlteryxWorkflow | null, fallback = "") {
//   const source = (workflow?.dataSources || []).find((item: any) => item?.path || item?.connection || item?.siteUrl);
//   if (!source) return fallback;
//   return source.path || source.connection || source.siteUrl || fallback;
// }

// // ─── Workflow Graph Helpers (dev12 backend) ───────────────────────────────────

// type WorkflowGraphNode = Record<string, any>;
// type WorkflowGraphEdge = Record<string, any>;

// function shortToolName(plugin: string) {
//   return (plugin || "Tool")
//     .split(/[./\\]/)
//     .filter(Boolean)
//     .slice(-1)[0]
//     .replace(/Tool$/i, "")
//     .replace(/([a-z])([A-Z])/g, "$1 $2")
//     .trim() || "Tool";
// }

// function toolFamily(plugin: string) {
//   const lowered = (plugin || "").toLowerCase();
//   if (lowered.includes("input") || lowered.includes("download")) return "input";
//   if (lowered.includes("output")) return "output";
//   if (lowered.includes("union")) return "union";
//   if (lowered.includes("join") || lowered.includes("findreplace")) return "join";
//   if (lowered.includes("filter")) return "filter";
//   if (lowered.includes("select")) return "select";
//   if (lowered.includes("cleansing") || lowered.includes("cleanse")) return "cleanse";
//   if (lowered.includes("summarize") || lowered.includes("aggregate")) return "summarize";
//   if (lowered.includes("formula")) return "formula";
//   if (lowered.includes("sort") || lowered.includes("unique")) return "shape";
//   return "default";
// }

// function toolIcon(family: string) {
//   const icons: Record<string, string> = {
//     input: "in",
//     output: "out",
//     union: "U",
//     join: "J",
//     filter: "F",
//     select: "S",
//     cleanse: "#",
//     summarize: "sum",
//     formula: "fx",
//     shape: "sort",
//     default: "tool",
//   };
//   return icons[family] || icons.default;
// }

// function nodeSubtitle(node: WorkflowGraphNode, sourceDetails: Array<Record<string, any>>) {
//   const plugin = String(node.plugin || "");
//   const family = toolFamily(plugin);
//   const config = node.config || {};
//   const sourcesForTool = sourceDetails.filter((source) => String(source.tool || "") === plugin);

//   if (family === "input" && sourcesForTool.length > 0) {
//     return sourcesForTool[0].name || sourcesForTool[0].path || "Input source";
//   }
//   if (config.filterExpression) return config.filterExpression;
//   if (Array.isArray(config.formulas) && config.formulas.length > 0) {
//     return `${config.formulas.length} formula field${config.formulas.length === 1 ? "" : "s"}`;
//   }
//   if (Array.isArray(config.groupBy) && config.groupBy.length > 0) {
//     return `Group by ${config.groupBy.slice(0, 2).join(", ")}`;
//   }
//   if (Array.isArray(config.selectedFields) && config.selectedFields.length > 0) {
//     return `${config.selectedFields.length} selected field${config.selectedFields.length === 1 ? "" : "s"}`;
//   }
//   return shortToolName(plugin);
// }

// function buildWorkflowLayout(nodes: WorkflowGraphNode[], edges: WorkflowGraphEdge[]) {
//   const nodeById = new Map(nodes.map((node, index) => [String(node.id || index), node]));
//   const levels = new Map<string, number>();
//   nodes.forEach((node, index) => levels.set(String(node.id || index), 0));

//   for (let pass = 0; pass < nodes.length; pass += 1) {
//     let changed = false;
//     edges.forEach((edge) => {
//       const from = String(edge.from || "");
//       const to = String(edge.to || "");
//       if (!nodeById.has(from) || !nodeById.has(to)) return;
//       const nextLevel = (levels.get(from) || 0) + 1;
//       if (nextLevel > (levels.get(to) || 0)) {
//         levels.set(to, nextLevel);
//         changed = true;
//       }
//     });
//     if (!changed) break;
//   }

//   const grouped = new Map<number, WorkflowGraphNode[]>();
//   nodes.forEach((node, index) => {
//     const id = String(node.id || index);
//     const level = levels.get(id) || 0;
//     if (!grouped.has(level)) grouped.set(level, []);
//     grouped.get(level)?.push(node);
//   });

//   const nodeWidth = 156;
//   const nodeHeight = 78;
//   const columnGap = 230;
//   const rowGap = 116;
//   const xOffset = 28;
//   const yOffset = 34;
//   const maxLevel = Math.max(0, ...Array.from(grouped.keys()));
//   const maxRows = Math.max(1, ...Array.from(grouped.values()).map((group) => group.length));
//   const positions = new Map<string, { x: number; y: number; width: number; height: number }>();

//   Array.from(grouped.keys())
//     .sort((a, b) => a - b)
//     .forEach((level) => {
//       const group = grouped.get(level) || [];
//       const columnHeight = (group.length - 1) * rowGap;
//       const yBase = yOffset + Math.max(0, (maxRows - 1) * rowGap - columnHeight) / 2;
//       group.forEach((node, row) => {
//         positions.set(String(node.id || ""), {
//           x: xOffset + level * columnGap,
//           y: yBase + row * rowGap,
//           width: nodeWidth,
//           height: nodeHeight,
//         });
//       });
//     });

//   return {
//     positions,
//     canvasWidth: xOffset * 2 + maxLevel * columnGap + nodeWidth + 48,
//     canvasHeight: yOffset * 2 + Math.max(1, maxRows) * rowGap + 32,
//   };
// }

// // ─── WorkflowGraph Component (dev12 backend) ─────────────────────────────────

// function WorkflowGraph({
//   workflow,
//   sourceDetails,
// }: {
//   workflow: AlteryxWorkflow;
//   sourceDetails: Array<Record<string, any>>;
// }) {
//   const nodes = workflow.workflowNodes || [];
//   const edges = workflow.workflowEdges || [];
//   const { positions, canvasWidth, canvasHeight } = useMemo(
//     () => buildWorkflowLayout(nodes, edges),
//     [nodes, edges]
//   );
//   const visibleEdges = edges.filter(
//     (edge) =>
//       positions.has(String(edge.from || "")) &&
//       positions.has(String(edge.to || ""))
//   );

//   if (!nodes.length) {
//     return (
//       <div className="workflow-empty-state">
//         <strong>Workflow graph is not available yet.</strong>
//         <span>
//           Upload the exported .yxmd/.yxzp package so the accelerator can parse
//           tool nodes and draw the lineage diagram.
//         </span>
//       </div>
//     );
//   }

//   return (
//     <div className="workflow-canvas-shell">
//       <div className="workflow-canvas" style={{ width: canvasWidth, height: canvasHeight }}>
//         <svg
//           className="workflow-edge-layer"
//           width={canvasWidth}
//           height={canvasHeight}
//           aria-hidden="true"
//         >
//           <defs>
//             <marker
//               id="workflow-arrow"
//               markerWidth="10"
//               markerHeight="8"
//               refX="9"
//               refY="4"
//               orient="auto"
//             >
//               <path d="M0,0 L10,4 L0,8 Z" />
//             </marker>
//           </defs>
//           {visibleEdges.map((edge, index) => {
//             const from = positions.get(String(edge.from || ""))!;
//             const to = positions.get(String(edge.to || ""))!;
//             const startX = from.x + from.width;
//             const startY = from.y + from.height / 2;
//             const endX = to.x;
//             const endY = to.y + to.height / 2;
//             const curve = Math.max(58, Math.min(120, (endX - startX) / 2));
//             return (
//               <path
//                 key={`${edge.from}-${edge.to}-${index}`}
//                 className="workflow-edge"
//                 d={`M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`}
//               />
//             );
//           })}
//         </svg>

//         {nodes.map((node, index) => {
//           const id = String(node.id || index);
//           const position = positions.get(id) || { x: 24, y: 24 + index * 92, width: 156, height: 78 };
//           const family = toolFamily(String(node.plugin || ""));
//           return (
//             <div
//               key={id}
//               className={`workflow-node workflow-node-${family} ${node.supported === false ? "needs-review" : ""}`}
//               style={{
//                 left: position.x,
//                 top: position.y,
//                 width: position.width,
//                 height: position.height,
//               }}
//               title={node.configurationText || nodeSubtitle(node, sourceDetails)}
//             >
//               <span className="node-icon">{toolIcon(family)}</span>
//               <span className="node-title">{shortToolName(String(node.plugin || ""))}</span>
//               <span className="node-id">#{id}</span>
//               <span className="node-subtitle">{nodeSubtitle(node, sourceDetails)}</span>
//               {node.supported === false && <span className="node-warning">review</span>}
//             </div>
//           );
//         })}
//       </div>
//     </div>
//   );
// }

// // ─── PieChart Component ───────────────────────────────────────────────────────

// function PieChart({ slices }: { slices: Array<[string, number]> }) {
//   const total = slices.reduce((sum, [, value]) => sum + value, 0) || 1;
//   let cumulative = 0;
//   const colors = ["#ff4d4f", "#fb923c", "#facc15", "#14b8a6", "#6d5dfc", "#db3ea2", "#0ea5e9", "#22c55e"];
//   const gradient = slices
//     .map(([_, value], index) => {
//       const start = (cumulative / total) * 100;
//       cumulative += value;
//       const end = (cumulative / total) * 100;
//       return `${colors[index % colors.length]} ${start}% ${end}%`;
//     })
//     .join(", ");

//   return (
//     <div className="alteryx-pie-wrap">
//       <div className="alteryx-pie" style={{ background: `conic-gradient(${gradient})` }}>
//         <span>{safePercent(slices[0]?.[1] || 0, total)}%</span>
//       </div>
//       <div className="alteryx-pie-legend">
//         {slices.map(([name, value], index) => (
//           <div key={name}>
//             <i style={{ backgroundColor: colors[index % colors.length] }} />
//             <span>
//               {name}: {safePercent(value, total)}%
//             </span>
//           </div>
//         ))}
//       </div>
//     </div>
//   );
// }

// // ─── Main SummaryPage Component ───────────────────────────────────────────────

// export default function SummaryPage() {
//   const location = useLocation();
//   const navigate = useNavigate();
//   const { stopTimer } = useWizard();

//   const workflowId = (location.state as any)?.workflowId || sessionStorage.getItem("alteryx_workflow_id") || "";
//   const batchId = sessionStorage.getItem("alteryx_batch_id") || "";
//   const platform = sessionStorage.getItem("platform") || "alteryx_upload";
//   const isCloudApiWorkflow = platform !== "alteryx_upload" && !batchId;

//   const [workflow, setWorkflow] = useState<AlteryxWorkflow | null>(readStoredWorkflow());
//   const [analysis, setAnalysis] = useState<any>(null);
//   const [activeTab, setActiveTab] = useState<SummaryTab>("sourceTypes");
//   // const [selectedSource, setSelectedSource] = useState<SourceType>("csv"); // ✅ dev11 UI state
//   const [selectedSource, setSelectedSource] = useState<SourceType>("scripts");
//   const [showSourceMQuery, setShowSourceMQuery] = useState(false);
//   const [sharePointUrl, setSharePointUrl] = useState(DEFAULT_SHAREPOINT_URL);
//   const [fileName, setFileName] = useState(DEFAULT_FILE_NAME);
//   const [loading, setLoading] = useState(true);
//   const [error, setError] = useState("");
//   const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
//   const [brdLoading, setBrdLoading] = useState(false);
//   const sourceMQueryPanelRef = useRef<HTMLElement | null>(null);

//   // ─── Data fetch (dev12 backend logic) ──────────────────────────────────────

//   useEffect(() => {
//     if (!workflowId) {
//       navigate("/apps");
//       return;
//     }

//     if (isCloudApiWorkflow) {
//       const storedWorkflow = readStoredWorkflow();
//       const materializeError = (location.state as any)?.cloudMaterializeError;
//       if (!storedWorkflow) {
//         navigate("/apps");
//         return;
//       }

//       setWorkflow(storedWorkflow);
//       setAnalysis({
//         summary: {
//           bullets: [
//             "Workflow metadata was retrieved from Alteryx Cloud successfully.",
//             materializeError
//               ? `The app attempted to download the full workflow package/XML but Alteryx Cloud did not return a parseable artifact: ${materializeError}`
//               : "Full tool-level parsing requires the workflow XML/package content, which the current Cloud API response does not include.",
//             "Use Bulk Upload with the .yxmd/.yxzp export to convert and publish this workflow until a full workflow download endpoint is wired.",
//           ],
//         },
//         mquery: null,
//         diagram: {
//           mermaid:
//             "graph LR\n  Cloud[Alteryx Cloud workflow metadata] --> Export[Export .yxmd/.yxzp]\n  Export --> Convert[Parse and convert]\n  Convert --> PowerBI[Publish to Power BI]",
//         },
//       });
//       sessionStorage.removeItem("migration_mquery");
//       const elapsed = stopTimer?.("/summary");
//       setPageLoadTime(elapsed ?? null);
//       setLoading(false);
//       setError("");
//       return;
//     }

//     if (!batchId) {
//       navigate("/apps");
//       return;
//     }

//     setLoading(true);
//     fetchAlteryxWorkflowAnalysis(batchId, workflowId, sharePointUrl, fileName)
//       .then((data) => {
//         setAnalysis(data);
//         setWorkflow(data.workflow);
//         sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(data.workflow));

//         // ✅ dev12: smart path/name resolution
//         const resolvedSourcePath = workflowSourcePath(
//           data.workflow,
//           data.mquery?.data_source_path || sharePointUrl
//         );
//         const resolvedFileName = workflowSourceName(
//           data.workflow,
//           data.mquery?.source?.name || fileName
//         );

//         if (sharePointUrl) sessionStorage.setItem("alteryx_sharepoint_url", sharePointUrl);
//         else sessionStorage.removeItem("alteryx_sharepoint_url");

//         sessionStorage.setItem("alteryx_file_name", resolvedFileName || "");
//         sessionStorage.setItem("migration_data_source_path", resolvedSourcePath || data.mquery?.data_source_path || "");
//         sessionStorage.setItem("migration_mquery", data.mquery?.combined_mquery || "");
//         sessionStorage.setItem("migration_dataset_name", data.mquery?.dataset_name || data.workflow?.name || "AlteryxDataset");
//         sessionStorage.setItem("migration_generation_method", data.mquery?.generation_method || "rule_based");
//         sessionStorage.setItem("migration_generation_label", data.mquery?.generation_label || "Rule-based mapping");
//         sessionStorage.setItem("migration_generation_reason", data.mquery?.routing_reason || "");
//         sessionStorage.setItem("migration_llm_status", data.mquery?.llm_status || "not_required");
//         sessionStorage.setItem("alteryx_conversion_steps", JSON.stringify(data.mquery?.conversion_steps || []));
//         setError("");
//       })
//       .catch((err: any) => setError(err?.message || "Failed to load workflow analysis"))
//       .finally(() => {
//         const elapsed = stopTimer?.("/summary");
//         setPageLoadTime(elapsed ?? null);
//         setLoading(false);
//       });
//   }, [batchId, fileName, isCloudApiWorkflow, navigate, sharePointUrl, stopTimer, workflowId]);

//   useEffect(() => {
//     if (!showSourceMQuery) return;
//     sourceMQueryPanelRef.current?.scrollIntoView({
//       behavior: "smooth",
//       block: "start",
//     });
//   }, [showSourceMQuery]);

//   // ─── Derived values ─────────────────────────────────────────────────────────

//   const assessment = useMemo(() => {
//     const totalTools = workflow?.toolCount ?? 0;
//     const unsupportedTools = workflow?.unsupportedToolCount ?? 0;
//     const supportedTools = workflow?.supportedToolCount ?? Math.max(totalTools - unsupportedTools, 0);
//     const automationScore = safePercent(supportedTools, totalTools);
//     return { totalTools, supportedTools, unsupportedTools, automationScore };
//   }, [workflow]);

//   const pieSlices = useMemo(() => buildPieSlices(workflow), [workflow]);
//   const conversionSteps = analysis?.mquery?.conversion_steps || [];
//   const generation = analysis?.mquery || {};
//   const generationMethod = generation.generation_method || "rule_based";
//   const generationLabel = generation.generation_label || "Rule-based mapping";
//   const generationReason = generation.routing_reason || "Low-complexity workflow with supported deterministic tool mappings.";
//   const generationIndicators = generation.complexity_indicators || [];
//   const generationStatus = generation.llm_status || "not_required";
//   const canConvertAndPublish = Boolean(batchId && analysis?.mquery?.combined_mquery);
//   const mqueryPreview = analysis?.mquery?.combined_mquery || sessionStorage.getItem("migration_mquery") || "";
//   const datasetName = analysis?.mquery?.dataset_name || workflow?.name || "AlteryxDataset";
//   const publishDuration = (location.state as any)?.publishDuration || "";
//   const sourceDetails = workflow?.dataSources || [];

//   // ─── Actions ────────────────────────────────────────────────────────────────

//   const downloadBrd = async () => {
//     if (!batchId || !workflowId) return;
//     setBrdLoading(true);
//     try {
//       const html = await fetchAlteryxBrdHtml(batchId, workflowId, sharePointUrl, fileName);
//       const blob = new Blob([html], { type: "text/html;charset=utf-8" });
//       const url = URL.createObjectURL(blob);
//       const anchor = document.createElement("a");
//       anchor.href = url;
//       anchor.download = `${(workflow?.name || "alteryx_workflow").replace(/[^a-z0-9]+/gi, "_")}_BRD.html`;
//       anchor.click();
//       URL.revokeObjectURL(url);
//     } catch (err: any) {
//       setError(err?.message || "Failed to generate BRD");
//     } finally {
//       setBrdLoading(false);
//     }
//   };

//   const continueToExport = () => {
//     if (!canConvertAndPublish) {
//       setError(
//         "This Cloud API workflow contains metadata only. Please use Bulk Upload with the exported .yxmd/.yxzp file to parse, convert, and publish it."
//       );
//       return;
//     }
//     sessionStorage.setItem("summaryComplete", "true");
//     sessionStorage.setItem("summaryActiveTab", "mquery");
//     navigate("/export");
//   };

//   const openSourceMQuery = () => {
//     setActiveTab("sourceTypes");
//     setSelectedSource("scripts");
//     setShowSourceMQuery(true);
//   };

//   // const publishSourceMQuery = () => {
//   //   if (!mqueryPreview) {
//   //     setError("No generated M Query is available for this workflow.");
//   //     return;
//   //   }

//   //   sessionStorage.setItem("summaryComplete", "true");
//   //   sessionStorage.setItem("exportComplete", "true");
//   //   sessionStorage.setItem("publishMethod", "M_QUERY");
//   //   navigate("/publish", {
//   //     state: {
//   //       workflowName: workflow?.name || "Alteryx workflow",
//   //       mquery: mqueryPreview,
//   //       datasetName,
//   //     },
//   //   });
//   // };

//   const [publishing, setPublishing] = useState(false);

//   // const publishSourceMQuery = async () => {
//   const publishSourceMQuery = async () => {
//     const publishStart = Date.now();
//     if (!mqueryPreview) {
//       setError("No generated M Query is available for this workflow.");
//       return;
//     }

//     setPublishing(true);
//     setError("");

//     try {
//       const result = await publishAlteryxMQuery({
//         dataset_name: datasetName,
//         combined_mquery: mqueryPreview,
//         sharepoint_url: sharePointUrl,
//         data_source_path: sessionStorage.getItem("migration_data_source_path") || sharePointUrl,
//         access_token: sessionStorage.getItem("powerbi_access_token") || "",
//         // FIX: forward source_fields_map so the backend can inject real column
//         // definitions for _raw CSV tables that have no field schema in the
//         // Alteryx workflow JSON.  Falls back to {} when not present (safe).
//         alteryx_source_fields: analysis?.mquery?.source_fields_map || {},
//       });

//       // Save result to sessionStorage for PublishPage to read
//       sessionStorage.setItem("alteryx_publish_result", JSON.stringify(result));
//       sessionStorage.setItem("summaryComplete", "true");
//       sessionStorage.setItem("exportComplete", "true");
//       sessionStorage.setItem("publishMethod", "M_QUERY");

//       // navigate("/publish", {
//       //   state: {
//       //     workflowName: workflow?.name || "Alteryx workflow",
//       //     mquery: mqueryPreview,
//       //     datasetName,
//       //   },
//       // });
//       const publishDurationMs = Date.now() - publishStart;
// const publishMins = Math.floor(publishDurationMs / 60000);
// const publishSecs = Math.floor((publishDurationMs % 60000) / 1000);
// const publishDuration = publishMins > 0
//   ? `${publishMins}m ${publishSecs}s`
//   : `${publishSecs}s`;

// navigate("/publish", {
//   state: {
//     workflowName: workflow?.name || "Alteryx workflow",
//     mquery: mqueryPreview,
//     datasetName,
//     publishDuration,
//   },
// });

//     } catch (err: any) {
//       setError(err?.message || "Publish to Power BI failed. Please try again.");
//     } finally {
//       setPublishing(false);
//     }
//   };

//   const downloadSourceMQuery = () => {
//     if (!mqueryPreview) {
//       setError("No generated M Query is available for this workflow.");
//       return;
//     }

//     const safeName = (datasetName || workflow?.name || "alteryx_mquery")
//       .replace(/[^a-z0-9]+/gi, "_")
//       .replace(/^_+|_+$/g, "")
//       || "alteryx_mquery";
//     const blob = new Blob([mqueryPreview], { type: "text/plain;charset=utf-8" });
//     const url = URL.createObjectURL(blob);
//     const anchor = document.createElement("a");
//     anchor.href = url;
//     anchor.download = `${safeName}.pq`;
//     anchor.click();
//     URL.revokeObjectURL(url);
//   };

//   // ─── Early returns ──────────────────────────────────────────────────────────

//   if (loading) {
//     return (
//       <LoadingOverlay
//         isVisible={loading}
//         message="Generating Alteryx executive summary and migration analysis..."
//       />
//     );
//   }
  
//   if (publishing) {
//   return (
//     <LoadingOverlay
//       isVisible={publishing}
//       message="Publishing to Power BI..."
//     />
//   );
// }

//   if (error) {
//     return (
//       <div className="summary-wrapper">
//         <button className="back-btn" onClick={() => navigate("/apps")}>
//           Back to workflows
//         </button>
//         <div className="error-card">{error}</div>
//       </div>
//     );
//   }

//   if (!workflow) {
//     return (
//       <div className="summary-wrapper">
//         <button className="back-btn" onClick={() => navigate("/apps")}>
//           Back to workflows
//         </button>
//         <div className="error-card">No Alteryx workflow is selected.</div>
//       </div>
//     );
//   }

//   // ─── Render ──────────────────────────────────────────────────────────────────

//   return (
//     <div className="summary-wrapper alteryx-summary-page">

//       {/* ── Header ── */}
//       <div className="alteryx-summary-top">
//         <h1>{workflow.name}</h1>
//         <div className="summary-tab-bar alteryx-tab-bar">
//           {TABS.map((tab) => (
//             <button
//               key={tab.id}
//               className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
//               onClick={() => setActiveTab(tab.id)}
//             >
//               <span>{tab.icon}</span>
//               {tab.label}
//             </button>
//           ))}
//         </div>
//         <div className="timer-badge">
//           Analysis Time: {pageLoadTime || "00m : 00s : 00ms"}
//         </div>
//       </div>

//       {/* ── Source config (dev12: only shown when NOT on sourceTypes tab) ──
//       {!isCloudApiWorkflow && activeTab !== "sourceTypes" && (
//         <div className="source-config alteryx-source-config">
//           <label>
//             Data Source Path
//             <input
//               value={sharePointUrl}
//               onChange={(event) => setSharePointUrl(event.target.value)}
//             />
//           </label>
//           <label>
//             File name
//             <input
//               value={fileName}
//               onChange={(event) => setFileName(event.target.value)}
//             />
//           </label>
//         </div>
//       )} */}

//       {/* ══════════════════════════════════════════════════════
//           TAB: Source Types
//       ══════════════════════════════════════════════════════ */}
//       {activeTab === "sourceTypes" && (
//         isCloudApiWorkflow ? (
//           <section className="assessment-panel cloud-workflow-panel">
//             <h2>Cloud Workflow Metadata</h2>
//             <p>
//               The Alteryx Cloud API returned the workflow list record, so discovery
//               is connected. This response does not include the workflow XML/package
//               content required to parse tools, generate M Query, build the BRD, or
//               publish to Power BI.
//             </p>
//             <div className="cloud-workflow-facts">
//               <div><span>Workflow ID</span>        <strong>{workflow.id}</strong></div>
//               <div><span>Workflow Name</span>      <strong>{workflow.name}</strong></div>
//               <div><span>Last Modified</span>      <strong>{workflow.lastModifiedDate || "Not returned by Cloud API"}</strong></div>
//               <div><span>Run Count</span>          <strong>{workflow.runCount ?? "Not returned by Cloud API"}</strong></div>
//               <div><span>Credential Type</span>    <strong>{workflow.credentialType || "Not returned by Cloud API"}</strong></div>
//               <div><span>Worker Tag</span>         <strong>{workflow.workerTag || "Not returned by Cloud API"}</strong></div>
//             </div>
//             <div className="cloud-next-step">
//               <strong>Migration-ready next step</strong>
//               <span>
//                 Export this workflow from Alteryx as .yxmd/.yxzp and use Bulk Upload.
//                 That path provides the XML needed for Scripts, Summary, BRD, validation,
//                 and Power BI publishing.
//               </span>
//             </div>
//           </section>
//         ) : (
//           <>
//             {/* dev11 UI: interactive source-type cards */}
//             {!showSourceMQuery && (
//               <section className="source-type-grid">

//                 {/* Database Card */}
//                 <article
//                   className={`source-type-card database ${selectedSource === "database" ? "selected" : "muted"}`}
//                   onClick={() => {
//                     setSelectedSource("database");
//                     setShowSourceMQuery(false);
//                   }}
//                   // style={{ cursor: "pointer" }}
//                   style={{ cursor: "not-allowed", opacity: 0.45, pointerEvents: "none" }}
//                 >
//                   <div className="source-card-head">
//                     <span className={`source-radio ${selectedSource === "database" ? "selected" : ""}`} />
//                     <span className="source-icon database-icon">🗄️</span>
//                     <div>
//                       <h3>Database</h3>
//                       <p>Direct ODBC / JDBC connection</p>
//                     </div>
//                   </div>
//                   <p className="source-card-desc">
//                     Connect directly to the source database via ODBC. Schema is inferred
//                     automatically. Best for live systems where data resides in SQL Server,
//                     Oracle, or Snowflake.
//                   </p>
//                   <div className="source-tags default">
//                     <span>ODBC / JDBC</span>
//                     <span>LIVE SCHEMA</span>
//                     <span>SQL SERVER · ORACLE</span>
//                   </div>
//                 </article>

//                 {/* Scripts Card */}
//                 <article
//                   className={`source-type-card scripts ${selectedSource === "scripts" ? "selected" : ""}`}
//                   onClick={() => setSelectedSource("scripts")}
//                   style={{ cursor: "pointer" }}
//                 >
//                   <div className="source-card-head">
//                     <span className={`source-radio ${selectedSource === "scripts" ? "selected" : ""}`} />
//                     <span className="source-icon script-icon">📜</span>
//                     <div>
//                       <h3>Scripts</h3>
//                       <p>Alteryx Worklflow → M-Query</p>
//                     </div>
//                   </div>
//                   <p className="source-card-desc">
//                     Parse the .YXMD/ .JSON/ .YXZP scripts from your workflow. Transforms complex Alteryx tools into Power Query M-code. Full schema and
//                     relationship preservation.
//                   </p>
//                   <div className="source-tags recommended">
//                     <span>M-QUERY</span>
//                     <span>XMLA</span>
//                     <span>RELATIONSHIPS</span>
//                   </div>
//                   {selectedSource === "scripts" && (
//                     <button
//                       onClick={(e) => {
//                         e.stopPropagation();
//                         openSourceMQuery();
//                       }}
//                     >
//                       Open M Query
//                     </button>
//                   )}
//                 </article>

//                 {/* Export CSV Card */}
//                 <article
//                   className={`source-type-card csv ${selectedSource === "csv" ? "selected" : ""}`}
//                   onClick={() => {
//                     setSelectedSource("csv");
//                     setShowSourceMQuery(false);
//                   }}
//                   // style={{ cursor: "pointer" }}
//                   style={{ cursor: "not-allowed", opacity: 0.45, pointerEvents: "none" }}
//                 >
//                   <div className="source-card-head">
//                     <span className={`source-radio ${selectedSource === "csv" ? "selected" : ""}`} />
//                     <span className="source-icon csv-icon">📦</span>
//                     <div>
//                       <h3>Export CSV</h3>
//                       <p>Data export via REST API</p>
//                     </div>
//                   </div>
//                   <p className="source-card-desc">
//                     Export all table data as CSV and push to Power BI as a push dataset
//                     via REST API. Works on any Power BI license. Ideal for flat tables
//                     without complex transformations.
//                   </p>
//                   <div className="source-tags csv-tags">
//                     <span>ANY LICENSE</span>
//                     <span>REST API</span>
//                     <span>FAST DEPLOY</span>
//                   </div>
//                   {selectedSource === "csv" && (
//                     <button
//                       onClick={(e) => {
//                         e.stopPropagation();
//                         continueToExport();
//                       }}
//                     >
//                       Go to Export
//                     </button>
//                   )}
//                 </article>

//               </section>
//             )}
//             {showSourceMQuery && (
//               <section className="source-mquery-panel" ref={sourceMQueryPanelRef} tabIndex={-1}>
//                 <div className="source-mquery-header">
//                   <div>
//                     {/* <h2>{workflow.name}</h2> */}
//                     {/* <p>
//                     Generated Power Query uses the configured data source <strong>{fileName}</strong>.
//                     The same mapper can emit connector stubs for CSV, Excel, database, and API inputs detected in Alteryx.
//                   </p> */}
//                     <div className={`source-generation-badge ${generationMethod === "llm" ? "llm" : "rules"}`}>
//                       <span>LLM-ASSISTED MAPPING</span>
//                       <strong>Medium workflow complexity.</strong>
//                       <em>LLM status: expression_fallback_failed_fallback</em>
//                     </div>
//                   </div>
//                 </div>

//                 <pre className="source-mquery-preview">
//                   {mqueryPreview || "No generated M Query is available for this workflow."}
//                 </pre>

//                 <div className="source-mquery-actions">
//                   <button
//                     className="source-mquery-download"
//                     onClick={downloadSourceMQuery}
//                     disabled={!mqueryPreview}
//                   >
//                     Download M Query
//                   </button>
//                   {/* <button onClick={publishSourceMQuery} disabled={!mqueryPreview}>
//                     Publish to Power BI
//                   </button> */}
//                   <button onClick={publishSourceMQuery} disabled={!mqueryPreview || publishing}>
//                     {publishing ? "Publishing..." : "Publish to Power BI"}
//                   </button>
//                 </div>
//               </section>
//             )}
//           </>
//         )
//       )}

//       {/* ══════════════════════════════════════════════════════
//           TAB: Summary
//       ══════════════════════════════════════════════════════ */}
//       {activeTab === "summary" && (
//         <section className="summary-report alteryx-executive-grid">
//           <PieChart slices={pieSlices} />
//           <div className="alteryx-exec-copy">
//             <h2>Executive Summary</h2>
//             <ul>
//               {(
//                 analysis?.summary?.bullets || [
//                   "Workflow metadata loaded. Upload the exported workflow package to generate a full executive summary.",
//                 ]
//               ).map((item: string) => (
//                 <li key={item}>{item}</li>
//               ))}
//             </ul>
//           </div>
//           {/* <div className="metric-grid alteryx-metrics">
//             <div className="metric-card"><span>Total Tools</span>    <strong>{assessment.totalTools}</strong></div>
//             <div className="metric-card"><span>Supported Tools</span><strong>{assessment.supportedTools}</strong></div>
//             <div className="metric-card"><span>Needs Review</span>   <strong>{assessment.unsupportedTools}</strong></div>
//             <div className="metric-card"><span>Automation Fit</span> <strong>{assessment.automationScore}%</strong></div>
//           </div> */}
//           <div className={`hybrid-route-panel ${generationMethod === "llm" ? "llm" : "rules"}`}>
//             {/* <span>Query Generation Path</span>
//             <strong>{generationLabel}</strong>
//             <p>{generationReason}</p>
//             <small>
//               {generationMethod === "llm"
//                 ? `LLM status: ${generationStatus}`
//                 : "Rule engine used for M Query generation"}
//             </small>
//             {generationIndicators.length > 0 && (
//               <div className="hybrid-route-tags">
//                 {generationIndicators.slice(0, 4).map((item: string) => (
//                   <em key={item}>{item}</em>
//                 ))}
//               </div>
//             )} */}
//           </div>
//         </section>
//       )}

//       {/* ══════════════════════════════════════════════════════
//           TAB: BRD
//       ══════════════════════════════════════════════════════ */}
//       {activeTab === "brd" && (
//         <section className="assessment-panel alteryx-brd-panel">
//           <h2>Workflow BRD</h2>
//           {isCloudApiWorkflow ? (
//             <>
//               <p>
//                 BRD generation needs the workflow package XML. The Cloud workflow list
//                 API returned only metadata for this workflow, so the accelerator cannot
//                 produce tool mapping, M Query, or validation criteria yet.
//               </p>
//               <div className="cloud-next-step">
//                 <strong>Use Bulk Upload for BRD</strong>
//                 <span>
//                   Upload the exported .yxmd/.yxzp file for this workflow, then this tab
//                   will generate the full workflow-specific BRD.
//                 </span>
//               </div>
//             </>
//           ) : (
//             <>
//               <p>
//                 The BRD is generated for this selected Alteryx workflow. It includes source inventory, conversion scope, tool
//                 mapping, workflow diagram, generated M Query, acceptance criteria, and
//                 validation/reconciliation requirements.
//               </p>
//               <button
//                 className="primary-summary-action"
//                 onClick={downloadBrd}
//                 disabled={brdLoading}
//               >
//                 {brdLoading ? "Generating BRD..." : "Download Workflow BRD"}
//               </button>
//             </>
//           )}
//         </section>
//       )}

//       {/* ══════════════════════════════════════════════════════
//           TAB: ER Diagram  ← dev12 WorkflowGraph integrated
//       ══════════════════════════════════════════════════════ */}
//       {activeTab === "diagram" && (
//         <section className="assessment-panel alteryx-diagram-panel">
//           <h2>Workflow Diagram</h2>
//           <p>
//             Accelerator shows the Alteryx workflow graph containing multiple relational tables and join keys, so reviewers can validate transformation lineage before publishing.
//           </p>

//           {/* ✅ dev12 WorkflowGraph replaces dev11's <pre> tag */}
//           <WorkflowGraph workflow={workflow} sourceDetails={sourceDetails} />

//           {/* ✅ dev12 legend */}
//           <div className="workflow-legend">
//             <span><i className="legend-input" /> Source</span>
//             <span><i className="legend-transform" /> Transform</span>
//             <span><i className="legend-join" /> Join / Union</span>
//             <span><i className="legend-output" /> Output</span>
//           </div>

//           {/* ✅ dev12 pill class */}
//           {/* <div className="pill-list workflow-detail-pills">
//             {sourceDetails.length > 0 &&
//               sourceDetails.slice(0, 12).map((source: any, index: number) => (
//                 <span key={`source-${index}`}>
//                   {source.fileName || source.path || source.connection || source.type || "Data source"}
//                 </span>
//               ))}
//             {sourceDetails.length === 0 &&
//               (workflow.workflowEdges || []).slice(0, 12).map((edge: any, index: number) => (
//                 <span key={`${edge.from}-${edge.to}-${index}`}>
//                   Tool {edge.from} to Tool {edge.to}
//                 </span>
//               ))}
//           </div> */}
//         </section>
//       )}

//       {/* ── Footer actions (dev12 backend) ── */}
//       <div className="summary-actions">
//         {/* <button onClick={() => navigate("/apps")}>Back to workflows</button> */}
//         {!isCloudApiWorkflow && (
//           <>
//             {/* <button onClick={downloadBrd} disabled={brdLoading}>
//               {brdLoading ? "Generating BRD..." : "Download BRD"}
//             </button> */}
//             {/* <button onClick={continueToExport} disabled={!canConvertAndPublish}>
//               Continue to Power BI Conversion
//             </button> */}
//           </>
//         )}
//       </div>

//     </div>
//   );
// }







import "./SummaryPage.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import LoadingOverlay from "../components/LoadingOverlay/LoadingOverlay";
// import {
//   fetchAlteryxBrdHtml,
//   fetchAlteryxWorkflowAnalysis,
// } from "../api/alteryxApi";
import {
  downloadAlteryxDataformProject,
  downloadAlteryxDbtProject,
  downloadAlteryxPythonProject,
  fetchAlteryxBrdHtml,
  fetchAlteryxWorkflowAnalysis,
  publishAlteryxDataformToBigQuery,
  publishAlteryxDataformToRepository,
  publishAlteryxDbtToBigQuery,
  publishAlteryxMQuery,
} from "../api/alteryxApi";
import type { AlteryxWorkflow } from "../api/alteryxApi";
import { useWizard } from "../context/WizardContext";

// ─── Constants ───────────────────────────────────────────────────────────────

const DEFAULT_SHAREPOINT_URL = sessionStorage.getItem("alteryx_sharepoint_url") || "";
const DEFAULT_FILE_NAME = sessionStorage.getItem("alteryx_file_name") || "";

// ─── Types ────────────────────────────────────────────────────────────────────

type SummaryTab = "sourceTypes" | "summary" | "brd" | "diagram";
type SourceType = "database" | "scripts";

// ─── Tab Config ───────────────────────────────────────────────────────────────

const TABS: Array<{ id: SummaryTab; label: string; icon: string }> = [
  { id: "sourceTypes", label: "Source Types", icon: "" },
  { id: "summary", label: "Summary", icon: "" },
  { id: "brd", label: "BRD", icon: "" },
  { id: "diagram", label: "Workflow Diagram", icon: "" },
];

// ─── Helpers ──────────────────────────────────────────────────────────────────

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

function workflowSourceName(workflow: AlteryxWorkflow | null, fallback = "") {
  const source = (workflow?.dataSources || []).find((item: any) => item?.name || item?.fileName || item?.path);
  if (!source) return fallback;
  return source.fileName || source.name || String(source.path || "").split(/[\\/]/).pop() || fallback;
}

function workflowSourcePath(workflow: AlteryxWorkflow | null, fallback = "") {
  const source = (workflow?.dataSources || []).find((item: any) => item?.path || item?.connection || item?.siteUrl);
  if (!source) return fallback;
  return source.path || source.connection || source.siteUrl || fallback;
}

// ─── Workflow Graph Helpers (dev12 backend) ───────────────────────────────────

type WorkflowGraphNode = Record<string, any>;
type WorkflowGraphEdge = Record<string, any>;

function shortToolName(plugin: string) {
  return (plugin || "Tool")
    .split(/[./\\]/)
    .filter(Boolean)
    .slice(-1)[0]
    .replace(/Tool$/i, "")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim() || "Tool";
}

function toolFamily(plugin: string) {
  const lowered = (plugin || "").toLowerCase();
  if (lowered.includes("input") || lowered.includes("download")) return "input";
  if (lowered.includes("output")) return "output";
  if (lowered.includes("union")) return "union";
  if (lowered.includes("join") || lowered.includes("findreplace")) return "join";
  if (lowered.includes("filter")) return "filter";
  if (lowered.includes("select")) return "select";
  if (lowered.includes("cleansing") || lowered.includes("cleanse")) return "cleanse";
  if (lowered.includes("summarize") || lowered.includes("aggregate")) return "summarize";
  if (lowered.includes("formula")) return "formula";
  if (lowered.includes("sort") || lowered.includes("unique")) return "shape";
  return "default";
}

function toolIcon(family: string) {
  const icons: Record<string, string> = {
    input: "in",
    output: "out",
    union: "U",
    join: "J",
    filter: "F",
    select: "S",
    cleanse: "#",
    summarize: "sum",
    formula: "fx",
    shape: "sort",
    default: "tool",
  };
  return icons[family] || icons.default;
}

function nodeSubtitle(node: WorkflowGraphNode, sourceDetails: Array<Record<string, any>>) {
  const plugin = String(node.plugin || "");
  const family = toolFamily(plugin);
  const config = node.config || {};
  const sourcesForTool = sourceDetails.filter((source) => String(source.tool || "") === plugin);

  if (family === "input" && sourcesForTool.length > 0) {
    return sourcesForTool[0].name || sourcesForTool[0].path || "Input source";
  }
  if (config.filterExpression) return config.filterExpression;
  if (Array.isArray(config.formulas) && config.formulas.length > 0) {
    return `${config.formulas.length} formula field${config.formulas.length === 1 ? "" : "s"}`;
  }
  if (Array.isArray(config.groupBy) && config.groupBy.length > 0) {
    return `Group by ${config.groupBy.slice(0, 2).join(", ")}`;
  }
  if (Array.isArray(config.selectedFields) && config.selectedFields.length > 0) {
    return `${config.selectedFields.length} selected field${config.selectedFields.length === 1 ? "" : "s"}`;
  }
  return shortToolName(plugin);
}

function buildWorkflowLayout(nodes: WorkflowGraphNode[], edges: WorkflowGraphEdge[]) {
  const nodeById = new Map(nodes.map((node, index) => [String(node.id || index), node]));
  const levels = new Map<string, number>();
  nodes.forEach((node, index) => levels.set(String(node.id || index), 0));

  for (let pass = 0; pass < nodes.length; pass += 1) {
    let changed = false;
    edges.forEach((edge) => {
      const from = String(edge.from || "");
      const to = String(edge.to || "");
      if (!nodeById.has(from) || !nodeById.has(to)) return;
      const nextLevel = (levels.get(from) || 0) + 1;
      if (nextLevel > (levels.get(to) || 0)) {
        levels.set(to, nextLevel);
        changed = true;
      }
    });
    if (!changed) break;
  }

  const grouped = new Map<number, WorkflowGraphNode[]>();
  nodes.forEach((node, index) => {
    const id = String(node.id || index);
    const level = levels.get(id) || 0;
    if (!grouped.has(level)) grouped.set(level, []);
    grouped.get(level)?.push(node);
  });

  const nodeWidth = 156;
  const nodeHeight = 78;
  const columnGap = 230;
  const rowGap = 116;
  const xOffset = 28;
  const yOffset = 34;
  const maxLevel = Math.max(0, ...Array.from(grouped.keys()));
  const maxRows = Math.max(1, ...Array.from(grouped.values()).map((group) => group.length));
  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();

  Array.from(grouped.keys())
    .sort((a, b) => a - b)
    .forEach((level) => {
      const group = grouped.get(level) || [];
      const columnHeight = (group.length - 1) * rowGap;
      const yBase = yOffset + Math.max(0, (maxRows - 1) * rowGap - columnHeight) / 2;
      group.forEach((node, row) => {
        positions.set(String(node.id || ""), {
          x: xOffset + level * columnGap,
          y: yBase + row * rowGap,
          width: nodeWidth,
          height: nodeHeight,
        });
      });
    });

  return {
    positions,
    canvasWidth: xOffset * 2 + maxLevel * columnGap + nodeWidth + 48,
    canvasHeight: yOffset * 2 + Math.max(1, maxRows) * rowGap + 32,
  };
}

// ─── WorkflowGraph Component (dev12 backend) ─────────────────────────────────

function WorkflowGraph({
  workflow,
  sourceDetails,
}: {
  workflow: AlteryxWorkflow;
  sourceDetails: Array<Record<string, any>>;
}) {
  const nodes = workflow.workflowNodes || [];
  const edges = workflow.workflowEdges || [];
  const { positions, canvasWidth, canvasHeight } = useMemo(
    () => buildWorkflowLayout(nodes, edges),
    [nodes, edges]
  );
  const visibleEdges = edges.filter(
    (edge) =>
      positions.has(String(edge.from || "")) &&
      positions.has(String(edge.to || ""))
  );

  if (!nodes.length) {
    return (
      <div className="workflow-empty-state">
        <strong>Workflow graph is not available yet.</strong>
        <span>
          Upload the .yxmd/.yxzp package so the accelerator can parse
          tool nodes and draw the lineage diagram.
        </span>
      </div>
    );
  }

  return (
    <div className="workflow-canvas-shell">
      <div className="workflow-canvas" style={{ width: canvasWidth, height: canvasHeight }}>
        <svg
          className="workflow-edge-layer"
          width={canvasWidth}
          height={canvasHeight}
          aria-hidden="true"
        >
          <defs>
            <marker
              id="workflow-arrow"
              markerWidth="10"
              markerHeight="8"
              refX="9"
              refY="4"
              orient="auto"
            >
              <path d="M0,0 L10,4 L0,8 Z" />
            </marker>
          </defs>
          {visibleEdges.map((edge, index) => {
            const from = positions.get(String(edge.from || ""))!;
            const to = positions.get(String(edge.to || ""))!;
            const startX = from.x + from.width;
            const startY = from.y + from.height / 2;
            const endX = to.x;
            const endY = to.y + to.height / 2;
            const curve = Math.max(58, Math.min(120, (endX - startX) / 2));
            return (
              <path
                key={`${edge.from}-${edge.to}-${index}`}
                className="workflow-edge"
                d={`M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`}
              />
            );
          })}
        </svg>

        {nodes.map((node, index) => {
          const id = String(node.id || index);
          const position = positions.get(id) || { x: 24, y: 24 + index * 92, width: 156, height: 78 };
          const family = toolFamily(String(node.plugin || ""));
          return (
            <div
              key={id}
              className={`workflow-node workflow-node-${family} ${node.supported === false ? "needs-review" : ""}`}
              style={{
                left: position.x,
                top: position.y,
                width: position.width,
                height: position.height,
              }}
              title={node.configurationText || nodeSubtitle(node, sourceDetails)}
            >
              <span className="node-icon">{toolIcon(family)}</span>
              <span className="node-title">{shortToolName(String(node.plugin || ""))}</span>
              <span className="node-id">#{id}</span>
              <span className="node-subtitle">{nodeSubtitle(node, sourceDetails)}</span>
              {node.supported === false && <span className="node-warning">review</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── PieChart Component ───────────────────────────────────────────────────────

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
            <span>
              {name}: {safePercent(value, total)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main SummaryPage Component ───────────────────────────────────────────────

export default function SummaryPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { stopTimer } = useWizard();

  const workflowId = (location.state as any)?.workflowId || sessionStorage.getItem("alteryx_workflow_id") || "";
  const batchId = sessionStorage.getItem("alteryx_batch_id") || "";
  const platform = sessionStorage.getItem("platform") || "alteryx_upload";
  const isCloudApiWorkflow = platform !== "alteryx_upload" && !batchId;

  const [workflow, setWorkflow] = useState<AlteryxWorkflow | null>(readStoredWorkflow());
  const [analysis, setAnalysis] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<SummaryTab>("sourceTypes");
  // const [selectedSource, setSelectedSource] = useState<SourceType>("csv"); // ✅ dev11 UI state
  const [selectedSource, setSelectedSource] = useState<SourceType>("scripts");
  const [showSourceMQuery, setShowSourceMQuery] = useState(false);
  const [sharePointUrl] = useState(DEFAULT_SHAREPOINT_URL);
  const [fileName] = useState(DEFAULT_FILE_NAME);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pageLoadTime, setPageLoadTime] = useState<string | null>(null);
  const [brdLoading, setBrdLoading] = useState(false);
  const [dbtPublishing, setDbtPublishing] = useState(false);
  const [dataformPublishing, setDataformPublishing] = useState(false);
  const [dataformRepoPublishing, setDataformRepoPublishing] = useState(false);
  const [dbtPublishResult, setDbtPublishResult] = useState<any>(() => {
    const raw = sessionStorage.getItem("alteryx_dbt_bigquery_publish_result");
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  });
  const sourceMQueryPanelRef = useRef<HTMLElement | null>(null);

  // ─── Data fetch (dev12 backend logic) ──────────────────────────────────────

  useEffect(() => {
    if (!workflowId) {
      navigate("/apps");
      return;
    }

    if (isCloudApiWorkflow) {
      const storedWorkflow = readStoredWorkflow();
      const materializeError = (location.state as any)?.cloudMaterializeError;
      if (!storedWorkflow) {
        navigate("/apps");
        return;
      }

      setWorkflow(storedWorkflow);
      setAnalysis({
        summary: {
          bullets: [
            "Workflow metadata was retrieved from Alteryx Cloud successfully.",
            materializeError
              ? `The app attempted to download the full workflow package/XML but Alteryx Cloud did not return a parseable artifact: ${materializeError}`
              : "Full tool-level parsing requires the workflow XML/package content, which the current Cloud API response does not include.",
            "Use Bulk Upload with the .yxmd/.yxzp package to convert and publish this workflow until a full workflow download endpoint is wired.",
          ],
        },
        mquery: null,
        diagram: {
          mermaid:
            "graph LR\n  Cloud[Alteryx Cloud workflow metadata] --> Package[.yxmd/.yxzp package]\n  Package --> Convert[Parse and convert]\n  Convert --> PowerBI[Publish to Power BI]",
        },
      });
      sessionStorage.removeItem("migration_mquery");
      const elapsed = stopTimer?.("/summary");
      setPageLoadTime(elapsed ?? null);
      setLoading(false);
      setError("");
      return;
    }

    if (!batchId) {
      navigate("/apps");
      return;
    }

    setLoading(true);
    fetchAlteryxWorkflowAnalysis(batchId, workflowId, sharePointUrl, fileName)
      .then((data) => {
        setAnalysis(data);
        setWorkflow(data.workflow);
        sessionStorage.removeItem("alteryx_validation_result");
        sessionStorage.removeItem("migration_row_count");
        sessionStorage.removeItem("migration_columns");
        sessionStorage.setItem("alteryx_selected_workflow", JSON.stringify(data.workflow));

        // ✅ dev12: smart path/name resolution
        const resolvedSourcePath = workflowSourcePath(
          data.workflow,
          data.mquery?.data_source_path || sharePointUrl
        );
        const resolvedFileName = workflowSourceName(
          data.workflow,
          data.mquery?.source?.name || fileName
        );

        if (sharePointUrl) sessionStorage.setItem("alteryx_sharepoint_url", sharePointUrl);
        else sessionStorage.removeItem("alteryx_sharepoint_url");

        sessionStorage.setItem("alteryx_file_name", resolvedFileName || "");
        sessionStorage.setItem("migration_data_source_path", resolvedSourcePath || data.mquery?.data_source_path || "");
        sessionStorage.setItem("migration_mquery", data.mquery?.combined_mquery || "");
        sessionStorage.setItem("migration_dataset_name", data.mquery?.dataset_name || data.workflow?.name || "AlteryxDataset");
        sessionStorage.setItem("migration_generation_method", data.mquery?.generation_method || "rule_based");
        sessionStorage.setItem("migration_generation_label", data.mquery?.generation_label || "Rule-based mapping");
        sessionStorage.setItem("migration_generation_reason", data.mquery?.routing_reason || "");
        sessionStorage.setItem("migration_llm_status", data.mquery?.llm_status || "not_required");
        sessionStorage.setItem("alteryx_conversion_steps", JSON.stringify(data.mquery?.conversion_steps || []));
        setError("");
      })
      .catch((err: any) => setError(err?.message || "Failed to load workflow analysis"))
      .finally(() => {
        const elapsed = stopTimer?.("/summary");
        setPageLoadTime(elapsed ?? null);
        setLoading(false);
      });
  }, [batchId, fileName, isCloudApiWorkflow, navigate, sharePointUrl, stopTimer, workflowId]);

  useEffect(() => {
    if (!showSourceMQuery) return;
    sourceMQueryPanelRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  }, [showSourceMQuery]);

  // ─── Derived values ─────────────────────────────────────────────────────────

  // const assessment = useMemo(() => {
  //   const totalTools = workflow?.toolCount ?? 0;
  //   const unsupportedTools = workflow?.unsupportedToolCount ?? 0;
  //   const supportedTools = workflow?.supportedToolCount ?? Math.max(totalTools - unsupportedTools, 0);
  //   const automationScore = safePercent(supportedTools, totalTools);
  //   return { totalTools, supportedTools, unsupportedTools, automationScore };
  // }, [workflow]);

  const pieSlices = useMemo(() => buildPieSlices(workflow), [workflow]);
  // const conversionSteps = analysis?.mquery?.conversion_steps || [];
  const generation = analysis?.mquery || {};
  const generationMethod = generation.generation_method || "rule_based";
  // const generationLabel = generation.generation_label || "Rule-based mapping";
  // const generationReason = generation.routing_reason || "Low-complexity workflow with supported deterministic tool mappings.";
  // const generationIndicators = generation.complexity_indicators || [];
  // const generationStatus = generation.llm_status || "not_required";
  const mqueryPreview = analysis?.mquery?.combined_mquery || sessionStorage.getItem("migration_mquery") || "";
  const datasetName = analysis?.mquery?.dataset_name || workflow?.name || "AlteryxDataset";
  const sourceDetails = workflow?.dataSources || [];
  const outputTargets = workflow?.outputTargets || [];
  const macroDependencies = workflow?.macroDependencies || [];
  const macroValidation = workflow?.macroValidation || {};
  const hasMacroDependencies = macroDependencies.length > 0;
  const macroReady = macroValidation.status === "ready";
  const macroTypes = Array.from(
    new Set(macroDependencies.map((item: any) => item.macroType).filter(Boolean))
  );
  const macroComplexity = dbtPublishResult?.macro_complexity || {};
  const batchMacro = macroDependencies.find((item: any) => String(item.macroType || "").toLowerCase() === "batch");
  const iterativeMacro = macroDependencies.find((item: any) => String(item.macroType || "").toLowerCase() === "iterative");
  const batchComplexity = macroComplexity.batch || null;
  const iterativeComplexity = macroComplexity.iterative || null;

  // ─── Actions ────────────────────────────────────────────────────────────────

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

  const openSourceMQuery = () => {
    setActiveTab("sourceTypes");
    setSelectedSource("scripts");
    setShowSourceMQuery(true);
  };

  // const publishSourceMQuery = () => {
  //   if (!mqueryPreview) {
  //     setError("No generated M Query is available for this workflow.");
  //     return;
  //   }

  //   sessionStorage.setItem("summaryComplete", "true");
  //   sessionStorage.setItem("exportComplete", "true");
  //   sessionStorage.setItem("publishMethod", "M_QUERY");
  //   navigate("/publish", {
  //     state: {
  //       workflowName: workflow?.name || "Alteryx workflow",
  //       mquery: mqueryPreview,
  //       datasetName,
  //     },
  //   });
  // };

  const [publishing, setPublishing] = useState(false);

  // const publishSourceMQuery = async () => {
  const publishSourceMQuery = async () => {
    const publishStart = Date.now();
    if (!mqueryPreview) {
      setError("No generated M Query is available for this workflow.");
      return;
    }

    setPublishing(true);
    setError("");

    try {
      sessionStorage.removeItem("alteryx_validation_result");
      sessionStorage.removeItem("migration_row_count");
      sessionStorage.removeItem("migration_columns");
      const result = await publishAlteryxMQuery({
        dataset_name: datasetName,
        combined_mquery: mqueryPreview,
        sharepoint_url: sharePointUrl,
        data_source_path: sessionStorage.getItem("migration_data_source_path") || sharePointUrl,
        access_token: sessionStorage.getItem("powerbi_access_token") || "",
        // FIX: forward source_fields_map so the backend can inject real column
        // definitions for _raw CSV tables that have no field schema in the
        // Alteryx workflow JSON.  Falls back to {} when not present (safe).
        alteryx_source_fields: analysis?.mquery?.source_fields_map || {},
      });

      // Save result to sessionStorage for PublishPage to read
      sessionStorage.setItem("alteryx_publish_result", JSON.stringify(result));
      sessionStorage.setItem("summaryComplete", "true");
      sessionStorage.setItem("exportComplete", "true");
      sessionStorage.setItem("publishMethod", "M_QUERY");

      // navigate("/publish", {
      //   state: {
      //     workflowName: workflow?.name || "Alteryx workflow",
      //     mquery: mqueryPreview,
      //     datasetName,
      //   },
      // });
      const publishDurationMs = Date.now() - publishStart;
const publishMins = Math.floor(publishDurationMs / 60000);
const publishSecs = Math.floor((publishDurationMs % 60000) / 1000);
const publishDuration = publishMins > 0
  ? `${publishMins}m ${publishSecs}s`
  : `${publishSecs}s`;

navigate("/publish", {
  state: {
    workflowName: workflow?.name || "Alteryx workflow",
    mquery: mqueryPreview,
    datasetName,
    publishDuration,
  },
});

    } catch (err: any) {
      setError(err?.message || "Publish to Power BI failed. Please try again.");
    } finally {
      setPublishing(false);
    }
  };

  const downloadSourceMQuery = () => {
    if (!mqueryPreview) {
      setError("No generated M Query is available for this workflow.");
      return;
    }

    const safeName = (datasetName || workflow?.name || "alteryx_mquery")
      .replace(/[^a-z0-9]+/gi, "_")
      .replace(/^_+|_+$/g, "")
      || "alteryx_mquery";
    const blob = new Blob([mqueryPreview], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${safeName}.pq`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const downloadDbtProject = async () => {
    if (!batchId || !workflowId) {
      setError("No uploaded Alteryx workflow batch is available for dbt export.");
      return;
    }

    try {
      const blob = await downloadAlteryxDbtProject(batchId, workflowId, sharePointUrl, fileName);
      const url = URL.createObjectURL(blob);
      const safeName = (datasetName || workflow?.name || "alteryx_dbt_project")
        .replace(/[^a-z0-9]+/gi, "_")
        .replace(/^_+|_+$/g, "")
        || "alteryx_dbt_project";
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${safeName}_dbt_project.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err?.message || "Failed to download dbt project.");
    }
  };

  const downloadProjectArtifact = async (artifact: "dataform" | "python") => {
    if (!batchId || !workflowId) {
      setError(`No uploaded Alteryx workflow batch is available for ${artifact} export.`);
      return;
    }

    try {
      const blob = artifact === "dataform"
        ? await downloadAlteryxDataformProject(batchId, workflowId, sharePointUrl, fileName)
        : await downloadAlteryxPythonProject(batchId, workflowId, sharePointUrl, fileName);
      const url = URL.createObjectURL(blob);
      const safeName = (datasetName || workflow?.name || `alteryx_${artifact}_project`)
        .replace(/[^a-z0-9]+/gi, "_")
        .replace(/^_+|_+$/g, "")
        || `alteryx_${artifact}_project`;
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${safeName}_${artifact}_project.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      setError(err?.message || `Failed to download ${artifact} project.`);
    }
  };

  const publishDbtToBigQuery = async () => {
    const publishStart = Date.now();
    if (!batchId || !workflowId) {
      setError("No uploaded Alteryx workflow batch is available for BigQuery publish.");
      return;
    }

    setDbtPublishing(true);
    setDbtPublishResult(null);
    setError("");
    try {
      const result = await publishAlteryxDbtToBigQuery(batchId, workflowId, sharePointUrl, fileName);
      setDbtPublishResult(result);
      sessionStorage.setItem("alteryx_dbt_bigquery_publish_result", JSON.stringify(result));
      sessionStorage.setItem("publishMethod", "DBT_BIGQUERY");
      sessionStorage.setItem("summaryComplete", "true");
      sessionStorage.setItem("exportComplete", "true");

      if (result?.success) {
        const publishDurationMs = Date.now() - publishStart;
        const publishMins = Math.floor(publishDurationMs / 60000);
        const publishSecs = Math.floor((publishDurationMs % 60000) / 1000);
        const publishDuration = publishMins > 0
          ? `${publishMins}m ${publishSecs}s`
          : `${publishSecs}s`;

        navigate("/publish", {
          state: {
            workflowName: workflow?.name || "Alteryx workflow",
            datasetName: result.final_model || result.project_name || datasetName,
            publishDuration,
            publishMode: "DBT_BIGQUERY",
          },
        });
      }
    } catch (err: any) {
      setError(err?.message || "Failed to publish dbt project to BigQuery.");
    } finally {
      setDbtPublishing(false);
    }
  };

  const publishDataformToBigQuery = async () => {
    const publishStart = Date.now();
    if (!batchId || !workflowId) {
      setError("No uploaded Alteryx workflow batch is available for Dataform publish.");
      return;
    }

    setDataformPublishing(true);
    setDbtPublishResult(null);
    setError("");
    try {
      const result = await publishAlteryxDataformToBigQuery(batchId, workflowId, sharePointUrl, fileName);
      setDbtPublishResult(result);
      sessionStorage.setItem("alteryx_dataform_bigquery_publish_result", JSON.stringify(result));
      sessionStorage.setItem("publishMethod", "DATAFORM_BIGQUERY");
      sessionStorage.setItem("summaryComplete", "true");
      sessionStorage.setItem("exportComplete", "true");

      if (result?.success) {
        const publishDurationMs = Date.now() - publishStart;
        const publishMins = Math.floor(publishDurationMs / 60000);
        const publishSecs = Math.floor((publishDurationMs % 60000) / 1000);
        const publishDuration = publishMins > 0
          ? `${publishMins}m ${publishSecs}s`
          : `${publishSecs}s`;

        navigate("/publish", {
          state: {
            workflowName: workflow?.name || "Alteryx workflow",
            datasetName: result.project_name || datasetName,
            publishDuration,
            publishMode: "DATAFORM_BIGQUERY",
          },
        });
      }
    } catch (err: any) {
      setError(err?.message || "Failed to publish Dataform project to BigQuery.");
    } finally {
      setDataformPublishing(false);
    }
  };

  const publishDataformToRepository = async () => {
    const publishStart = Date.now();
    if (!batchId || !workflowId) {
      setError("No uploaded Alteryx workflow batch is available for Dataform repository publish.");
      return;
    }

    setDataformRepoPublishing(true);
    setDbtPublishResult(null);
    setError("");
    try {
      const result = await publishAlteryxDataformToRepository(batchId, workflowId, sharePointUrl, fileName);
      setDbtPublishResult(result);
      sessionStorage.setItem("alteryx_dataform_repo_publish_result", JSON.stringify(result));
      sessionStorage.setItem("publishMethod", "DATAFORM_REPO");
      sessionStorage.setItem("summaryComplete", "true");
      sessionStorage.setItem("exportComplete", "true");

      if (result?.success) {
        const publishDurationMs = Date.now() - publishStart;
        const publishMins = Math.floor(publishDurationMs / 60000);
        const publishSecs = Math.floor((publishDurationMs % 60000) / 1000);
        const publishDuration = publishMins > 0
          ? `${publishMins}m ${publishSecs}s`
          : `${publishSecs}s`;

        navigate("/publish", {
          state: {
            workflowName: workflow?.name || "Alteryx workflow",
            datasetName: result.final_table_name || result.project_name || datasetName,
            publishDuration,
            publishMode: "DATAFORM_REPO",
          },
        });
      }
    } catch (err: any) {
      setError(err?.message || "Failed to publish Dataform project to GCP Dataform repository.");
    } finally {
      setDataformRepoPublishing(false);
    }
  };

  const downloadWorkflowDiagram = () => {
    const nodes = workflow?.workflowNodes || [];
    const edges = workflow?.workflowEdges || [];
    if (!nodes.length) {
      setError("Workflow diagram is not available to download.");
      return;
    }

    const { positions, canvasWidth, canvasHeight } = buildWorkflowLayout(nodes, edges);
    const width = Math.ceil(canvasWidth);
    const height = Math.ceil(canvasHeight);
    const canvas = document.createElement("canvas");
    const scale = Math.min(window.devicePixelRatio || 2, 3);
    canvas.width = width * scale;
    canvas.height = height * scale;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext("2d");

    if (!ctx) {
      setError("Failed to get canvas context.");
      return;
    }

    ctx.scale(scale, scale);

    const drawRoundRect = (
      x: number,
      y: number,
      rectWidth: number,
      rectHeight: number,
      radius: number
    ) => {
      const r = Math.min(radius, rectWidth / 2, rectHeight / 2);
      ctx.beginPath();
      ctx.moveTo(x + r, y);
      ctx.lineTo(x + rectWidth - r, y);
      ctx.quadraticCurveTo(x + rectWidth, y, x + rectWidth, y + r);
      ctx.lineTo(x + rectWidth, y + rectHeight - r);
      ctx.quadraticCurveTo(x + rectWidth, y + rectHeight, x + rectWidth - r, y + rectHeight);
      ctx.lineTo(x + r, y + rectHeight);
      ctx.quadraticCurveTo(x, y + rectHeight, x, y + rectHeight - r);
      ctx.lineTo(x, y + r);
      ctx.quadraticCurveTo(x, y, x + r, y);
      ctx.closePath();
    };

    const drawEllipsizedText = (text: string, x: number, y: number, maxWidth: number) => {
      let rendered = text;
      while (rendered.length > 1 && ctx.measureText(rendered).width > maxWidth) {
        rendered = rendered.slice(0, -2);
      }
      if (rendered !== text) rendered = `${rendered}...`;
      ctx.fillText(rendered, x, y);
    };

    const iconColors = (family: string) => {
      if (family === "input") return { background: "#d1fae5", color: "#047857" };
      if (family === "output") return { background: "#fee2e2", color: "#b91c1c" };
      if (family === "union" || family === "join") return { background: "#ede9fe", color: "#6d28d9" };
      if (["filter", "select", "cleanse", "summarize", "formula", "shape"].includes(family)) {
        return { background: "#dbeafe", color: "#0369a1" };
      }
      return { background: "#e8f2ff", color: "#075985" };
    };

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, width, height);

    const background = ctx.createLinearGradient(0, 0, width, height);
    background.addColorStop(0, "#f7fbff");
    background.addColorStop(1, "#eef5fb");
    ctx.fillStyle = background;
    drawRoundRect(0.5, 0.5, width - 1, height - 1, 22);
    ctx.fill();

    ctx.save();
    drawRoundRect(0.5, 0.5, width - 1, height - 1, 22);
    ctx.clip();
    ctx.fillStyle = "rgba(100, 116, 139, 0.16)";
    for (let x = 18; x < width; x += 28) {
      for (let y = 18; y < height; y += 28) {
        ctx.beginPath();
        ctx.arc(x, y, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();

    ctx.strokeStyle = "#d7e3f2";
    ctx.lineWidth = 1;
    drawRoundRect(0.5, 0.5, width - 1, height - 1, 22);
    ctx.stroke();

    edges
      .filter((edge) => positions.has(String(edge.from || "")) && positions.has(String(edge.to || "")))
      .forEach((edge) => {
        const from = positions.get(String(edge.from || ""))!;
        const to = positions.get(String(edge.to || ""))!;
        const startX = from.x + from.width;
        const startY = from.y + from.height / 2;
        const endX = to.x;
        const endY = to.y + to.height / 2;
        const curve = Math.max(58, Math.min(120, (endX - startX) / 2));

        ctx.strokeStyle = "#71819a";
        ctx.lineWidth = 2.1;
        ctx.beginPath();
        ctx.moveTo(startX, startY);
        ctx.bezierCurveTo(startX + curve, startY, endX - curve, endY, endX, endY);
        ctx.stroke();

        const angle = Math.atan2(endY - startY, endX - startX);
        ctx.fillStyle = "#71819a";
        ctx.beginPath();
        ctx.moveTo(endX, endY);
        ctx.lineTo(endX - 10 * Math.cos(angle - Math.PI / 7), endY - 10 * Math.sin(angle - Math.PI / 7));
        ctx.lineTo(endX - 10 * Math.cos(angle + Math.PI / 7), endY - 10 * Math.sin(angle + Math.PI / 7));
        ctx.closePath();
        ctx.fill();
      });

    nodes.forEach((node, index) => {
      const id = String(node.id || index);
      const position = positions.get(id) || { x: 24, y: 24 + index * 92, width: 156, height: 78 };
      const family = toolFamily(String(node.plugin || ""));
      const colors = iconColors(family);

      ctx.save();
      ctx.shadowColor = "rgba(15, 23, 42, 0.12)";
      ctx.shadowBlur = 28;
      ctx.shadowOffsetY = 15;
      ctx.fillStyle = node.supported === false ? "#fffafb" : "rgba(255, 255, 255, 0.95)";
      drawRoundRect(position.x, position.y, position.width, position.height, 18);
      ctx.fill();
      ctx.restore();

      ctx.strokeStyle = node.supported === false ? "#fecdd3" : "rgba(15, 23, 42, 0.12)";
      ctx.lineWidth = 1;
      drawRoundRect(position.x, position.y, position.width, position.height, 18);
      ctx.stroke();

      ctx.fillStyle = "#f8fbff";
      ctx.strokeStyle = "#9fb0c6";
      ctx.lineWidth = 2;
      [position.x - 1, position.x + position.width + 1].forEach((connectorX) => {
        ctx.beginPath();
        ctx.arc(connectorX, position.y + position.height / 2, 6, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      });

      const iconX = position.x + 16;
      const iconY = position.y + (position.height - 42) / 2;
      ctx.fillStyle = colors.background;
      drawRoundRect(iconX, iconY, 42, 42, 13);
      ctx.fill();

      ctx.fillStyle = colors.color;
      ctx.font = "900 10px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(toolIcon(family), iconX + 21, iconY + 21);

      const textX = position.x + 68;
      const textMaxWidth = position.width - 82;
      ctx.textAlign = "left";
      ctx.textBaseline = "alphabetic";
      ctx.fillStyle = "#0f172a";
      ctx.font = "600 14px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      drawEllipsizedText(shortToolName(String(node.plugin || "")), textX, position.y + 31, textMaxWidth);

      ctx.fillStyle = "#64748b";
      ctx.font = "800 11px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      drawEllipsizedText(`#${id}`, textX, position.y + 51, textMaxWidth);

      ctx.fillStyle = "#475569";
      ctx.font = "12px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
      drawEllipsizedText(nodeSubtitle(node, sourceDetails), textX, position.y + 70, textMaxWidth);

      if (node.supported === false) {
        ctx.fillStyle = "#fff1f2";
        const badgeWidth = 48;
        drawRoundRect(position.x + position.width - badgeWidth - 10, position.y + position.height - 10, badgeWidth, 20, 10);
        ctx.fill();
        ctx.fillStyle = "#be123c";
        ctx.font = "900 10px system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("REVIEW", position.x + position.width - badgeWidth / 2 - 10, position.y + position.height);
      }
    });

    canvas.toBlob((blob) => {
      if (!blob) {
        setError("Failed to create image blob.");
        return;
      }

      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      const safeName = (workflow?.name || "workflow_diagram")
        .replace(/[^a-z0-9]+/gi, "_")
        .replace(/^_+|_+$/g, "")
        || "workflow_diagram";
      anchor.href = url;
      anchor.download = `${safeName}_diagram.png`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    }, "image/png");
  };

  // ─── Early returns ──────────────────────────────────────────────────────────

  if (loading) {
    return (
      <LoadingOverlay
        isVisible={loading}
        message="Generating Alteryx executive summary and migration analysis..."
      />
    );
  }
  
  if (publishing) {
  return (
    <LoadingOverlay
      isVisible={publishing}
      message="Publishing to Power BI..."
    />
  );
}

  if (dbtPublishing || dataformPublishing || dataformRepoPublishing) {
    return (
      <LoadingOverlay
        isVisible={dbtPublishing || dataformPublishing || dataformRepoPublishing}
        message={
          dataformRepoPublishing
            ? "Publishing Dataform project to GCP repository..."
            : dataformPublishing
              ? "Publishing Dataform project to BigQuery..."
              : "Publishing dbt models to BigQuery..."
        }
      />
    );
  }

  if (error) {
    return (
      <div className="summary-wrapper">
        <button className="back-btn" onClick={() => navigate("/apps")}>
          Back to workflows
        </button>
        <div className="error-card">{error}</div>
      </div>
    );
  }

  if (!workflow) {
    return (
      <div className="summary-wrapper">
        <button className="back-btn" onClick={() => navigate("/apps")}>
          Back to workflows
        </button>
        <div className="error-card">No Alteryx workflow is selected.</div>
      </div>
    );
  }

  // ─── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="summary-wrapper alteryx-summary-page">

      {/* ── Header ── */}
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
        <div className="timer-badge">
          Analysis Time: {pageLoadTime || "00m : 00s : 00ms"}
        </div>
      </div>

      {/* ── Source config (dev12: only shown when NOT on sourceTypes tab) ──
      {!isCloudApiWorkflow && activeTab !== "sourceTypes" && (
        <div className="source-config alteryx-source-config">
          <label>
            Data Source Path
            <input
              value={sharePointUrl}
              onChange={(event) => setSharePointUrl(event.target.value)}
            />
          </label>
          <label>
            File name
            <input
              value={fileName}
              onChange={(event) => setFileName(event.target.value)}
            />
          </label>
        </div>
      )} */}

      {/* ══════════════════════════════════════════════════════
          TAB: Source Types
      ══════════════════════════════════════════════════════ */}
      {activeTab === "sourceTypes" && (
        isCloudApiWorkflow ? (
          <section className="assessment-panel cloud-workflow-panel">
            <h2>Cloud Workflow Metadata</h2>
            <p>
              The Alteryx Cloud API returned the workflow list record, so discovery
              is connected. This response does not include the workflow XML/package
              content required to parse tools, generate M Query, build the BRD, or
              publish to Power BI.
            </p>
            <div className="cloud-workflow-facts">
              <div><span>Workflow ID</span>        <strong>{workflow.id}</strong></div>
              <div><span>Workflow Name</span>      <strong>{workflow.name}</strong></div>
              <div><span>Last Modified</span>      <strong>{workflow.lastModifiedDate || "Not returned by Cloud API"}</strong></div>
              <div><span>Run Count</span>          <strong>{workflow.runCount ?? "Not returned by Cloud API"}</strong></div>
              <div><span>Credential Type</span>    <strong>{workflow.credentialType || "Not returned by Cloud API"}</strong></div>
              <div><span>Worker Tag</span>         <strong>{workflow.workerTag || "Not returned by Cloud API"}</strong></div>
            </div>
            <div className="cloud-next-step">
              <strong>Migration-ready next step</strong>
              <span>
                Download this workflow from Alteryx as .yxmd/.yxzp and use Bulk Upload.
                That path provides the XML needed for Scripts, Summary, BRD, validation,
                and Power BI publishing.
              </span>
            </div>
          </section>
        ) : (
          <>
            {/* dev11 UI: interactive source-type cards */}
            {!showSourceMQuery && (
              <section className="source-type-grid">

                {/* Database Card */}
                <article
                  className={`source-type-card database ${selectedSource === "database" ? "selected" : "muted"}`}
                  onClick={() => {
                    setSelectedSource("database");
                    setShowSourceMQuery(false);
                  }}
                  // style={{ cursor: "pointer" }}
                  style={{ cursor: "not-allowed", opacity: 0.45, pointerEvents: "none" }}
                >
                  <div className="source-card-head">
                    <span className={`source-radio ${selectedSource === "database" ? "selected" : ""}`} />
                    <span className="source-icon database-icon">🗄️</span>
                    <div>
                      <h3>Database</h3>
                      <p>Direct ODBC / JDBC connection</p>
                    </div>
                  </div>
                  <p className="source-card-desc">
                    Connect directly to the source database via ODBC. Schema is inferred
                    automatically. Best for live systems where data resides in SQL Server,
                    Oracle, or Snowflake.
                  </p>
                  <div className="source-tags default">
                    <span>ODBC / JDBC</span>
                    <span>LIVE SCHEMA</span>
                    <span>SQL SERVER · ORACLE</span>
                  </div>
                </article>

                {/* Scripts Card */}
                <article
                  className={`source-type-card scripts ${selectedSource === "scripts" ? "selected" : ""}`}
                  onClick={() => setSelectedSource("scripts")}
                  style={{ cursor: "pointer" }}
                >
                  <div className="source-card-head">
                    <span className={`source-radio ${selectedSource === "scripts" ? "selected" : ""}`} />
                    <span className="source-icon script-icon">📜</span>
                    <div>
                      <h3>Scripts</h3>
                      <p>Alteryx Worklflow → M-Query</p>
                    </div>
                  </div>
                  <p className="source-card-desc">
                    Parse the .YXMD/ .JSON/ .YXZP scripts from your workflow. Transforms complex Alteryx tools into Power Query M-code. Full schema and
                    relationship preservation.
                  </p>
                  <div className="source-tags recommended">
                    <span>M-QUERY</span>
                    <span>XMLA</span>
                    <span>RELATIONSHIPS</span>
                  </div>
                  {selectedSource === "scripts" && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        openSourceMQuery();
                      }}
                    >
                      Open M Query
                    </button>
                  )}
                </article>

              </section>
            )}
            {showSourceMQuery && (
              <section className="source-mquery-panel" ref={sourceMQueryPanelRef} tabIndex={-1}>
                <div className="source-mquery-header">
                  <div>
                    {/* <h2>{workflow.name}</h2> */}
                    {/* <p>
                    Generated Power Query uses the configured data source <strong>{fileName}</strong>.
                    The same mapper can emit connector stubs for CSV, Excel, database, and API inputs detected in Alteryx.
                  </p> */}
                    <div className={`source-generation-badge ${generationMethod === "llm" ? "llm" : "rules"}`}>
                      <span>LLM-ASSISTED MAPPING</span>
                      <strong>Medium workflow complexity.</strong>
                      <em>LLM status: expression_fallback_failed_fallback</em>
                    </div>
                  </div>
                </div>

                <pre className="source-mquery-preview">
                  {mqueryPreview || "No generated M Query is available for this workflow."}
                </pre>

                <div className="source-mquery-actions">
                  <button
                    className="source-mquery-download"
                    onClick={downloadSourceMQuery}
                    disabled={!mqueryPreview}
                  >
                    Download M Query
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={downloadDbtProject}
                    disabled={!batchId || !workflowId}
                  >
                    Download dbt Project
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={() => downloadProjectArtifact("dataform")}
                    disabled={!batchId || !workflowId}
                  >
                    Download Dataform Project
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={() => downloadProjectArtifact("python")}
                    disabled={!batchId || !workflowId}
                  >
                    Download Python Scripts
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={publishDbtToBigQuery}
                    disabled={!batchId || !workflowId || dbtPublishing || dataformPublishing || dataformRepoPublishing}
                  >
                    {dbtPublishing ? "Publishing dbt..." : "Publish dbt to BigQuery"}
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={publishDataformToBigQuery}
                    disabled={!batchId || !workflowId || dbtPublishing || dataformPublishing || dataformRepoPublishing}
                  >
                    {dataformPublishing ? "Publishing Dataform..." : "Publish Dataform to BigQuery"}
                  </button>
                  <button
                    className="source-mquery-download"
                    onClick={publishDataformToRepository}
                    disabled={!batchId || !workflowId || dbtPublishing || dataformPublishing || dataformRepoPublishing}
                  >
                    {dataformRepoPublishing ? "Publishing to Dataform Repo..." : "Publish Dataform to GCP Repo"}
                  </button>
                  {/* <button onClick={publishSourceMQuery} disabled={!mqueryPreview}>
                    Publish to Power BI
                  </button> */}
                  <button onClick={publishSourceMQuery} disabled={!mqueryPreview || publishing || dbtPublishing || dataformPublishing || dataformRepoPublishing}>
                    {publishing ? "Publishing..." : "Publish to Power BI"}
                  </button>
                </div>
                {dbtPublishResult && (
                  <div className={`dbt-publish-result ${dbtPublishResult.success ? "success" : "failed"}`}>
                    <strong>{dbtPublishResult.success ? "BigQuery publish complete" : "BigQuery publish failed"}</strong>
                    <span>{dbtPublishResult.final_model || dbtPublishResult.message}</span>
                    {!dbtPublishResult.success && dbtPublishResult.commands?.length > 0 && (
                      <details>
                        <summary>View dbt error log</summary>
                        <pre>
                          {[
                            dbtPublishResult.commands[dbtPublishResult.commands.length - 1]?.stdout,
                            dbtPublishResult.commands[dbtPublishResult.commands.length - 1]?.stderr,
                          ]
                            .filter(Boolean)
                            .join("\n")}
                        </pre>
                      </details>
                    )}
                  </div>
                )}
              </section>
            )}
          </>
        )
      )}

      {/* ══════════════════════════════════════════════════════
          TAB: Summary
      ══════════════════════════════════════════════════════ */}
      {activeTab === "summary" && (
        <section className="summary-report alteryx-executive-grid">
          <PieChart slices={pieSlices} />
          <div className="alteryx-exec-copy">
            <h2>Executive Summary</h2>
            <ul>
              {(
                analysis?.summary?.bullets || [
                  "Workflow metadata loaded. Upload the exported workflow package to generate a full executive summary.",
                ]
              ).map((item: string) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <div className={`macro-validation-panel ${hasMacroDependencies ? (macroReady ? "ready" : "missing") : "empty"}`}>
            <div className="macro-validation-header">
              <span>Macro Dependency Check</span>
              <strong>
                {hasMacroDependencies
                  ? `${macroValidation.uploaded || 0}/${macroValidation.referenced || macroDependencies.length} uploaded`
                  : "No macros"}
              </strong>
            </div>
            <p>
              {hasMacroDependencies
                ? macroValidation.message || "Macro references were detected in this workflow."
                : "This workflow does not reference external .yxmc macro files."}
            </p>
            {hasMacroDependencies && (
              <>
                <div className="macro-type-tags">
                  {macroTypes.map((type) => (
                    <em key={type}>{type}</em>
                  ))}
                </div>
                <table className="macro-validation-table">
                  <thead>
                    <tr>
                      <th>Macro</th>
                      <th>Type</th>
                      <th>Control</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {macroDependencies.map((macro: any) => (
                      <tr key={`${macro.toolId}-${macro.path}`}>
                        <td>{macro.name || macro.path}</td>
                        <td>{macro.macroType || "Macro"}</td>
                        <td>{macro.controlParameter || macro.iterationLimit || "-"}</td>
                        <td>
                          <span className={`macro-status ${macro.uploaded ? "ready" : "missing"}`}>
                            {macro.uploaded ? "Uploaded" : "Missing"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
          {hasMacroDependencies && (
            <div className="macro-complexity-panel">
              <div className="macro-validation-header">
                <span>Macro Complexity</span>
                <strong>{macroTypes.join(" + ") || "Macro"}</strong>
              </div>
              <div className="macro-complexity-grid">
                {batchMacro && (
                  <div className="macro-complexity-card">
                    <span>Batch Macro Complexity</span>
                    <strong>
                      {batchComplexity?.expected_batches != null
                        ? `${Number(batchComplexity.expected_batches).toLocaleString()} batch run${Number(batchComplexity.expected_batches) === 1 ? "" : "s"}`
                        : "Control table rows"}
                    </strong>
                    <p>
                      Expected executions are driven by
                      {" "}
                      <b>{batchComplexity?.control_parameter || batchMacro.controlParameter || "the control parameter"}</b>
                      {batchComplexity?.control_source ? ` from ${batchComplexity.control_source}.` : "."}
                      {" "}Each control row represents one parameter set for the batch macro.
                    </p>
                  </div>
                )}
                {iterativeMacro && (
                  <div className="macro-complexity-card">
                    <span>Iterative Macro Complexity</span>
                    <strong>{iterativeComplexity?.iteration_limit || iterativeMacro.iterationLimit || "100"} max</strong>
                    <p>
                      Stop condition:
                      {" "}
                      <b>{iterativeComplexity?.stop_condition || iterativeMacro.stopCondition || "No new records"}</b>.
                      Actual depth is available from <b>max(IterationDepth)</b> in the published model.
                    </p>
                  </div>
                )}
                {dbtPublishResult?.final_model && (
                  <div className="macro-complexity-card published">
                    <span>Published Model</span>
                    <strong>{dbtPublishResult.success ? "Complete" : "Failed"}</strong>
                    <p>{dbtPublishResult.final_model || dbtPublishResult.message}</p>
                  </div>
                )}
              </div>
            </div>
          )}
          {outputTargets.length > 0 && (
            <div className="macro-complexity-panel">
              <div className="macro-validation-header">
                <span>Detected Alteryx Outputs</span>
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
            </div>
          )}
          {/* <div className="metric-grid alteryx-metrics">
            <div className="metric-card"><span>Total Tools</span>    <strong>{assessment.totalTools}</strong></div>
            <div className="metric-card"><span>Supported Tools</span><strong>{assessment.supportedTools}</strong></div>
            <div className="metric-card"><span>Needs Review</span>   <strong>{assessment.unsupportedTools}</strong></div>
            <div className="metric-card"><span>Automation Fit</span> <strong>{assessment.automationScore}%</strong></div>
          </div>
          <div className={`hybrid-route-panel ${generationMethod === "llm" ? "llm" : "rules"}`}>
            <span>Query Generation Path</span>
            <strong>{generationLabel}</strong>
            <p>{generationReason}</p>
            <small>
              {generationMethod === "llm"
                ? `LLM status: ${generationStatus}`
                : "Rule engine used for M Query generation"}
            </small>
            {generationIndicators.length > 0 && (
              <div className="hybrid-route-tags">
                {generationIndicators.slice(0, 4).map((item: string) => (
                  <em key={item}>{item}</em>
                ))}
              </div>
            )}
          </div>
          {conversionSteps.length > 0 && (
            <div className="conversion-steps-panel">
              <h3>Conversion Steps</h3>
              <table className="conversion-steps-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Alteryx Tool</th>
                    <th>M Query Step</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {conversionSteps.map((step: any, index: number) => (
                    <tr key={index}>
                      <td>{index + 1}</td>
                      <td>{step.alteryx_tool || step.tool || step.source || "—"}</td>
                      <td>{step.mquery_step || step.step || step.description || "—"}</td>
                      <td>
                        <span className={`step-status ${step.status === "needs_review" || step.supported === false ? "review" : "supported"}`}>
                          {step.status || (step.supported === false ? "Needs Review" : "Converted")}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )} */}
        </section>
      )}

      {/* ══════════════════════════════════════════════════════
          TAB: BRD
      ══════════════════════════════════════════════════════ */}
      {activeTab === "brd" && (
        <section className="assessment-panel alteryx-brd-panel">
          <h2>BRD</h2>
          {isCloudApiWorkflow ? (
            <>
              <p>
                BRD generation needs the workflow package XML. The Cloud workflow list
                API returned only metadata for this workflow, so the accelerator cannot
                produce tool mapping, M Query, or validation criteria yet.
              </p>
              <div className="cloud-next-step">
                <strong>Use Bulk Upload for BRD</strong>
                <span>
                  Upload the .yxmd/.yxzp file for this workflow, then this tab
                  will generate the full workflow-specific BRD.
                </span>
              </div>
            </>
          ) : (
            <>
              <p>
                The BRD is generated for this selected Alteryx workflow. It includes source inventory, conversion scope, tool
                mapping, workflow diagram, generated M Query, acceptance criteria, and
                validation/reconciliation requirements.
              </p>
              <button
                className="primary-summary-action"
                onClick={downloadBrd}
                disabled={brdLoading}
              >
                {brdLoading ? "Generating BRD..." : "Download BRD"}
              </button>
            </>
          )}
        </section>
      )}

      {/* ══════════════════════════════════════════════════════
          TAB: ER Diagram  ← dev12 WorkflowGraph integrated
      ══════════════════════════════════════════════════════ */}
      {activeTab === "diagram" && (
        <section className="assessment-panel alteryx-diagram-panel">
          <div className="diagram-section-header">
            <div>
              <h2>Workflow Diagram</h2>
              <p>
                Accelerator shows the Alteryx workflow graph containing multiple relational tables and join keys, so reviewers can validate transformation lineage before publishing.
              </p>
            </div>
            <button
              className="diagram-download-btn"
              onClick={downloadWorkflowDiagram}
              title="Download workflow diagram as PNG"
            >
              Download as PNG
            </button>
          </div>

          {/* ✅ dev12 WorkflowGraph replaces dev11's <pre> tag */}
          <WorkflowGraph workflow={workflow} sourceDetails={sourceDetails} />

          {/* ✅ dev12 legend */}
          <div className="workflow-legend">
            <span><i className="legend-input" /> Source</span>
            <span><i className="legend-transform" /> Transform</span>
            <span><i className="legend-join" /> Join / Union</span>
            <span><i className="legend-output" /> Output</span>
          </div>

          {/* ✅ dev12 pill class */}
          {/* <div className="pill-list workflow-detail-pills">
            {sourceDetails.length > 0 &&
              sourceDetails.slice(0, 12).map((source: any, index: number) => (
                <span key={`source-${index}`}>
                  {source.fileName || source.path || source.connection || source.type || "Data source"}
                </span>
              ))}
            {sourceDetails.length === 0 &&
              (workflow.workflowEdges || []).slice(0, 12).map((edge: any, index: number) => (
                <span key={`${edge.from}-${edge.to}-${index}`}>
                  Tool {edge.from} to Tool {edge.to}
                </span>
              ))}
          </div> */}
        </section>
      )}

      {/* ── Footer actions (dev12 backend) ── */}
      <div className="summary-actions">
        {/* <button onClick={() => navigate("/apps")}>Back to workflows</button> */}
        {!isCloudApiWorkflow && (
          <>
            {/* <button onClick={downloadBrd} disabled={brdLoading}>
              {brdLoading ? "Generating BRD..." : "Download BRD"}
            </button> */}
            {/* <button onClick={continueToExport} disabled={!canConvertAndPublish}>
              Continue to Power BI Conversion
            </button> */}
          </>
        )}
      </div>

    </div>
  );
}
