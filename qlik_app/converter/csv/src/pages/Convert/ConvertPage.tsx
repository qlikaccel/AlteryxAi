import { type ChangeEvent, type MouseEvent, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  type Edge,
  type Node,
} from "reactflow";
import { convertAlteryxWorkflow, uploadCsvPreview, publishMQuery } from "../../api/qlikApi";
import type { AlteryxConvertApproach } from "../../api/qlikApi";
import "reactflow/dist/style.css";
import "./ConvertPage.css";

type MQueryStep = {
  name: string;
  type: string;
  expression: string;
};

type WorkflowGraph = {
  nodes: Node[];
  edges: Edge[];
};

const workflowStepIcons: Record<string, string> = {
  Input: "📥",
  Select: "📊",
  Filter: "🔍",
  Aggregation: "🧮",
  Sort: "🔃",
  Transformation: "⚙️",
  Step: "🔧",
};

const approachLabels: Record<AlteryxConvertApproach, string> = {
  "rule-based": "Rule-Based",
  llm: "LLM-Driven",
};

const approachHints: Record<AlteryxConvertApproach, string> = {
  "rule-based": "Fast deterministic conversion from parsed workflow structure.",
  llm: "AI-assisted conversion for complex workflows.",
};

const classifyStepType = (expression: string): string => {
  const normalized = expression.toLowerCase();
  if (normalized.includes("csv.document")) return "Input";
  if (normalized.includes("table.selectcolumns")) return "Select";
  if (normalized.includes("table.selectrows")) return "Filter";
  if (normalized.includes("table.group")) return "Aggregation";
  if (normalized.includes("table.sort")) return "Sort";
  if (normalized.includes("table.addcolumn")) return "Transformation";
  return "Step";
};

const parseMQuerySteps = (mQuery: string): MQueryStep[] => {
  const normalized = mQuery.replace(/\r\n/g, "\n").replace(/\t/g, " ").trim();
  const letMatch = normalized.match(/^let\s*(.*)\s*in\s*/s);
  if (!letMatch) {
    return [];
  }

  const body = letMatch[1];
  const stepLines = body
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/,$/, ""));

  return stepLines
    .map((line) => {
      const equalsIndex = line.indexOf("=");
      if (equalsIndex < 0) return null;
      const name = line.slice(0, equalsIndex).trim();
      const expression = line.slice(equalsIndex + 1).trim();
      if (!name) return null;
      return {
        name,
        expression,
        type: classifyStepType(expression),
      };
    })
    .filter((step): step is MQueryStep => Boolean(step));
};

const parseMQueryToWorkflow = (mQuery: string): WorkflowGraph => {
  const steps = parseMQuerySteps(mQuery);

  const nodes: Node[] = steps.map((step, index) => {
    const icon = workflowStepIcons[step.type] ?? workflowStepIcons.Step;
    const label = (
      <div className="workflow-node-card">
        <div className="workflow-node-header">
          <span className="workflow-node-icon">{icon}</span>
          <div>
            <div className="workflow-node-title">{step.name}</div>
            <div className="workflow-node-subtitle">{step.type}</div>
          </div>
        </div>
        <div className="workflow-node-tooltip">{step.expression}</div>
      </div>
    );

    return {
      id: step.name,
      data: { label, type: step.type, expression: step.expression },
      position: { x: index * 280, y: 0 },
      style: {
        width: 260,
        borderRadius: 18,
        border: "1px solid rgba(59, 130, 246, 0.18)",
        background: "#ffffff",
        boxShadow: "0 20px 40px rgba(15, 23, 42, 0.08)",
      },
    };
  });

  const edges: Edge[] = steps.map((step, index) => {
    if (index === 0) return null;
    const source = steps[index - 1].name;
    const target = step.name;
    return {
      id: `edge-${source}-${target}`,
      source,
      target,
      animated: true,
      markerEnd: {
        type: MarkerType.ArrowClosed,
      },
      style: {
        stroke: "#4f46e5",
        strokeWidth: 2,
      },
    } as Edge;
  }).filter((edge): edge is Edge => Boolean(edge));

  return { nodes, edges };
};

export default function ConvertPage() {
  const workspaceName = useMemo(
    () => sessionStorage.getItem("alteryx_workspace_name") || "",
    []
  );
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedCsv, setSelectedCsv] = useState<File | null>(null);
  const [approach, setApproach] = useState<AlteryxConvertApproach>("llm");
  const [loading, setLoading] = useState(false);
  const [csvLoading, setCsvLoading] = useState(false);
  const [error, setError] = useState("");
  const [csvError, setCsvError] = useState("");
  const [result, setResult] = useState<any>(null);
  const [csvPreview, setCsvPreview] = useState<Array<Record<string, unknown>>>([]);
  const [csvColumns, setCsvColumns] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const [showDiagram, setShowDiagram] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [publishStatus, setPublishStatus] = useState<"idle" | "success" | "error">("idle");
  const [publishMessage, setPublishMessage] = useState("");
  const [publishResult, setPublishResult] = useState<any>(null);
  const [activeConvertTab, setActiveConvertTab] = useState<"script" | "mquery">("script");

  const displayedMQuery = useMemo(
    () => result?.rule_based || result?.llm_driven || result?.m_query || "",
    [result]
  );

  const workflowGraph = useMemo(() => parseMQueryToWorkflow(displayedMQuery), [displayedMQuery]);
  const [activeNode, setActiveNode] = useState<string | null>(null);

  const fileLabel = selectedFile ? selectedFile.name : "Upload a .yxmd workflow file";
  const csvLabel = selectedCsv ? selectedCsv.name : "Upload a CSV file";

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    setCopied(false);
    setError("");
    setResult(null);
    setActiveConvertTab("script");
    const file = event.target.files?.[0] || null;
    setSelectedFile(file);
  };

  const handleCsvChange = (event: ChangeEvent<HTMLInputElement>) => {
    setCsvError("");
    setCsvPreview([]);
    const file = event.target.files?.[0] || null;
    setSelectedCsv(file);
  };

  const handleUploadCsvPreview = async () => {
    if (!selectedFile || !selectedCsv) {
      setCsvError("Please select both the .yxmd file and the corresponding CSV file.");
      return;
    }

    if (!selectedFile.name.toLowerCase().endsWith(".yxmd")) {
      setCsvError("Please select a valid .yxmd workflow file.");
      return;
    }

    if (!selectedCsv.name.toLowerCase().endsWith(".csv")) {
      setCsvError("Please select a valid CSV file.");
      return;
    }

    setCsvError("");
    setCsvLoading(true);
    setCsvPreview([]);

    try {
      const response = await uploadCsvPreview(selectedFile, selectedCsv);
      setCsvColumns(response.columns || []);
      setCsvPreview(response.data || []);
    } catch (err: any) {
      const message = err?.response?.data?.detail || err?.message || "CSV upload failed. Please try again.";
      setCsvError(message);
    } finally {
      setCsvLoading(false);
    }
  };

  const handleConvert = async () => {
    if (!selectedFile) {
      setError("Please upload a valid .yxmd file before converting.");
      return;
    }

    if (!selectedFile.name.toLowerCase().endsWith(".yxmd")) {
      setError("Only .yxmd files are supported. Please select a valid Alteryx workflow file.");
      return;
    }

    setError("");
    setCopied(false);
    setLoading(true);
    setResult(null);

    try {
      const data = await convertAlteryxWorkflow(selectedFile, approach);
      setResult(data);
      setActiveConvertTab("mquery");
    } catch (err: any) {
      const message = err?.response?.data?.detail || err?.message || "Conversion failed. Please try again.";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const handlePublishToPowerBI = async () => {
    const mquery = displayedMQuery?.trim();
    if (!mquery) {
      setPublishStatus("error");
      setPublishMessage("No M Query available to publish. Convert a workflow first.");
      return;
    }

    setPublishing(true);
    setPublishStatus("idle");
    setPublishMessage("");
    setPublishResult(null);

    try {
      const datasetName = selectedFile?.name.replace(/\.yxmd$/i, "") || "Qlik_Migrated_Dataset";
      const data = await publishMQuery(mquery, datasetName);
      if (data?.auth_required) {
        setPublishStatus("error");
        setPublishResult(data);
        setPublishMessage(
          `Power BI sign-in required. Open ${data.device_code_url} and enter the code ${data.user_code}. Then retry publish.`
        );
        return;
      }
      if (!data?.success) {
        throw new Error(data?.message || data?.error || "Publish failed");
      }
      setPublishResult(data);
      setPublishStatus("success");
      setPublishMessage(data.message || `Published ${data.dataset_name || datasetName} to Power BI.`);
    } catch (err: any) {
      const message = err?.response?.data?.detail || err?.message || "Publish failed. Please try again.";
      setPublishStatus("error");
      setPublishMessage(message);
    } finally {
      setPublishing(false);
    }
  };

  const downloadText = (content: string, suffix: string) => {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedFile?.name.replace(/\.yxmd$/i, "") || "workflow"}-${suffix}.m`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Unable to copy to clipboard. Please use the download button.");
    }
  };

  const renderResultBlock = (title: string, code: string, suffix: string) => (
    <div className="result-block">
      <div className="result-header">
        <div>
          <h3>{title}</h3>
          <p>{code ? `Length: ${code.length} characters` : "No M Query generated."}</p>
        </div>
        {code && (
          <div className="result-actions">
            <button onClick={() => downloadText(code, suffix)} type="button">
              Download .m
            </button>
            <button onClick={() => copyToClipboard(code)} type="button">
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        )}
      </div>
      <pre className="result-code">{code || "No output available."}</pre>
    </div>
  );

  return (
    <div className="convert-wrapper">
      <div className="convert-card">
        <div className="card-header">
          <div>
            <h1>Convert Alteryx Workflow to M Query</h1>
            <p>
              Upload a sample Alteryx <strong>.yxmd</strong> workflow file, then choose a conversion mode.
            </p>
            {workspaceName && (
              <p className="workspace-tag">Connected workspace: <strong>{workspaceName}</strong></p>
            )}
          </div>
        </div>

        <div className="convert-tab-bar">
          <button
            type="button"
            className={`convert-tab-button ${activeConvertTab === "script" ? "active" : ""}`}
            onClick={() => setActiveConvertTab("script")}
          >
            📜 Alteryx Script
          </button>
          <button
            type="button"
            className={`convert-tab-button ${activeConvertTab === "mquery" ? "active" : ""}`}
            onClick={() => setActiveConvertTab("mquery")}
          >
            🧩 Generated MQuery
          </button>
        </div>

        {activeConvertTab === "script" && (
          <>
            <div className="upload-section">
              <label htmlFor="yxmd-upload">Workflow file</label>
              <div className="file-upload-field">
                <input
                  id="yxmd-upload"
                  type="file"
                  accept=".yxmd"
                  onChange={handleFileChange}
                  disabled={loading}
                />
              </div>
              <p className="field-hint">{fileLabel}</p>
              <p className="field-hint">Select an Alteryx workflow file with the .yxmd extension.</p>
            </div>

            <div className="upload-section">
              <label htmlFor="csv-upload">Upload CSV File</label>
              <div className="file-upload-field">
                <input
                  id="csv-upload"
                  type="file"
                  accept=".csv"
                  onChange={handleCsvChange}
                  disabled={loading}
                />
              </div>
              <p className="field-hint">{csvLabel}</p>
              <p className="field-hint">Upload the CSV file that matches the Alteryx workflow source data.</p>
              <button
                type="button"
                className="csv-preview-button"
                onClick={handleUploadCsvPreview}
                disabled={csvLoading || loading || !selectedCsv}
              >
                {csvLoading ? "Loading CSV preview..." : "Preview CSV Data"}
              </button>
              {csvError && <div className="error-banner" style={{ marginTop: 8 }}>{csvError}</div>}
            </div>

            <div className="mode-section">
          <label>Conversion mode</label>
          <div className="mode-buttons">
            {(["rule-based", "llm"] as AlteryxConvertApproach[]).map((option) => (
              <button
                key={option}
                type="button"
                className={option === approach ? "mode-button active" : "mode-button"}
                onClick={() => {
                  setApproach(option);
                  setError("");
                  setResult(null);
                  setActiveConvertTab("script");
                }}
                disabled={loading}
              >
                <span>{approachLabels[option]}</span>
                <small>{approachHints[option]}</small>
              </button>
            ))}
          </div>
          {approach === "llm" && (
            <p className="field-hint" style={{ marginTop: 14 }}>
              LLM conversion can take longer than the rule-based path. Please allow up to 2 minutes for the backend to finish.
            </p>
          )}
        </div>
      </>)}

        {error && <div className="error-banner">{error}</div>}

        {loading && (
          <div className="loading-banner">
            <div className="loading-dot" />
            <div>
              <strong>Conversion in progress</strong>
              <p>Processing your workflow file. This can take up to 2 minutes for LLM conversion.</p>
            </div>
          </div>
        )}

        <div className="actions">
          <button className="convert-btn" onClick={handleConvert} disabled={loading || !selectedFile}>
            {loading ? "Converting..." : "Convert to M Query"}
          </button>
        </div>
        {activeConvertTab === "mquery" && !result && !loading && !error && (
          <div className="empty-display" style={{ marginTop: 24 }}>
            Convert to M Query to view the generated M Query here.
          </div>
        )}
        {activeConvertTab === "mquery" && result && (
          <div className="results-section">
            <div className="summary-card">
              <div>
                <strong>File:</strong> {result.filename || selectedFile?.name}
              </div>
              {result.node_count !== undefined && (
                <div>
                  <strong>Workflow nodes:</strong> {result.node_count}
                </div>
              )}
              <div>
                <strong>Approach:</strong> {result.approach || approach}
              </div>
            </div>

            {result.llm_error && (
              <div className="error-banner" style={{ marginTop: 16 }}>
                <strong>LLM fallback:</strong> {result.llm_error}
              </div>
            )}

            {result.rule_based && renderResultBlock("Rule-Based M Query", result.rule_based, "rule-based")}
            {result.llm_driven && renderResultBlock("LLM-Driven M Query", result.llm_driven, "llm")}
            {!result.rule_based && !result.llm_driven && result.m_query && renderResultBlock("M Query", result.m_query, approach)}

            {displayedMQuery && (
              <div className="publish-section">
                <button
                  type="button"
                  className="publish-pbi-btn"
                  onClick={handlePublishToPowerBI}
                  disabled={publishing}
                >
                  {publishing ? "Publishing to Power BI..." : "Publish M Query to Power BI"}
                </button>
                {(publishStatus !== "idle" || publishResult) && (
                  <div className={`publish-status-msg ${publishStatus === "success" ? "publish-success" : "publish-error"}`}>
                    {publishMessage || publishResult?.message || "Publish completed."}
                  </div>
                )}
                {publishResult?.auth_required && (
                  <div className="publish-auth-instructions">
                    <div>
                      <strong>Power BI login required</strong>
                    </div>
                    <div>
                      <a href={publishResult.device_code_url} target="_blank" rel="noreferrer">
                        Open Power BI sign-in page
                      </a>
                    </div>
                    <div>Enter code: <strong>{publishResult.user_code}</strong></div>
                  </div>
                )}
                {publishResult?.workspace_url && (
                  <div className="publish-result-link">
                    <a href={publishResult.workspace_url} target="_blank" rel="noreferrer">
                      Open Power BI Workspace
                    </a>
                  </div>
                )}
              </div>
            )}
            {displayedMQuery && (
              <div className="graph-toggle-bar">
                <button
                  type="button"
                  className="graph-toggle"
                  onClick={() => setShowDiagram((prev) => !prev)}
                >
                  {showDiagram ? "Hide Workflow View" : "Show Workflow View"}
                </button>
                <p className="field-hint">Toggle a visual workflow canvas for the generated M Query.</p>
              </div>
            )}

            {showDiagram && workflowGraph.nodes.length > 0 && (
              <div className="graph-section">
                <div className="graph-header">
                  <h3>Workflow Canvas</h3>
                  <p className="field-hint">Drag nodes, zoom, and explore the M Query pipeline visually.</p>
                </div>
                <div className="reactflow-wrapper">
                  <ReactFlow
                    nodes={workflowGraph.nodes.map((node) => ({
                      ...node,
                      className:
                        node.id === activeNode ? "workflow-node workflow-node--active" : "workflow-node",
                    }))}
                    edges={workflowGraph.edges}
                    fitView
                    nodesDraggable
                    nodesConnectable={false}
                    onNodeMouseEnter={(_: MouseEvent, node: Node) => setActiveNode(node.id)}
                    onNodeMouseLeave={() => setActiveNode(null)}
                    attributionPosition="bottom-left"
                  >
                    <Background gap={16} color="#e2e8f0" />
                    <MiniMap
                      nodeColor={(node: Node) => (node.id === activeNode ? "#4f46e5" : "#c7d2fe")}
                      nodeStrokeWidth={2}
                      maskColor="rgba(79, 70, 229, 0.08)"
                    />
                    <Controls />
                  </ReactFlow>
                </div>
              </div>
            )}

            {csvPreview.length > 0 && (
              <div className="data-preview-section">
                <div className="graph-header">
                  <h3>Data Preview</h3>
                  <p className="field-hint">Total Records: {csvPreview.length}</p>
                </div>
                <div className="data-preview-table-wrapper">
                  <table className="data-preview-table">
                    <thead>
                      <tr>
                        {csvColumns.map((column) => (
                          <th key={column}>{column}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {csvPreview.map((row, rowIndex) => (
                        <tr key={rowIndex}>
                          {csvColumns.map((column) => (
                            <td key={`${rowIndex}-${column}`}>{String(row[column] ?? "")}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
